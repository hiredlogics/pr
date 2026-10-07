"""P0 system integrity: customer output, the route registry, run isolation,
fact provenance and the execution manifest.

All data is fictitious. Nothing here is operator- or case-specific.
"""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import customer_safe
from pcn_appeal.engines.outcome import classify_hold
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (CaseFile, Fact, FactSource, FactStatus, RetrievalPack,
                               SourceKind, ValidationResult)
from pcn_appeal.orchestrator import AutoAppealResult
from pcn_appeal.routes import GROUND_ROUTES, Route, UnknownRouteError, validate_ground_routes
from test_no_weak_fallback import IN_TIME, LATE, case_with, outcome
from test_question_authority import run

ROOT = Path(__file__).resolve().parent.parent
# What the spec says a customer must never see, as substrings of the JSON.
FORBIDDEN = ("KB-", "VAL-", "RULE-", "MODULE-", "confidence", "reasoning", "material_because")


def assert_customer_clean(test: unittest.TestCase, payload) -> None:
    test.assertEqual(customer_safe.leaks(payload), [], payload)
    text = json.dumps(payload)
    for token in FORBIDDEN:
        test.assertNotIn(token, text, f"{token!r} reached the customer: {text[:400]}")


def internal_question():
    return {"fact": "site_postcode", "type": "text",
            "text": "Please confirm the site postcode.",
            "target_fact": "postcode",
            "material_because": "KB-POFA-02 requires jurisdiction",
            "why_asked": {"site_postcode": "VAL-POFA"}, "confidence": 0.71,
            "reasoning": "MODULE-POFA gate RULE-JURIS-1"}


# ------------------------------------------------------------------ P0.1
class CustomerOutputCarriesNoInternals(unittest.TestCase):

    def test_the_spec_example_becomes_customer_text(self):
        q = customer_safe.customer_question(internal_question())
        self.assertEqual(q, {"fact": "site_postcode", "type": "text",
                             "text": "Please confirm the site postcode."})

    def test_scrub_removes_internal_keys_and_ids_at_any_depth(self):
        payload = {"questions": [internal_question()],
                   "outcome_message": "Held under KB-POFA-02 by VAL-RES.",
                   "nested": {"module_ids": ["KB-LAND-01"], "ok": "PP-END-001 kept?"}}
        clean = customer_safe.scrub(payload, where="test")
        assert_customer_clean(self, clean)
        self.assertEqual(clean["questions"][0]["text"], "Please confirm the site postcode.")
        self.assertEqual(clean["outcome_message"], "Held under by.")

    def test_customer_reasons_and_values_are_not_touched(self):
        payload = {"rejected": [{"filename": "a.pdf", "reason": "no readable content"}],
                   "details": [{"name": "pcn_number", "value": "AB123456"}]}
        self.assertEqual(customer_safe.scrub(payload), payload)

    def test_an_internal_reason_generated_by_the_pipeline_never_reaches_the_customer(self):
        """The pipeline writes a question with its internal reason and a hold
        with module ids; the API response a customer receives is clean."""
        from pcn_appeal import api
        held = AutoAppealResult("C-T", api.CaseState.QUESTIONING, [internal_question()],
                                None, ["uncertain:vrm", "injection_suspected:E1"],
                                [internal_question()])
        client = TestClient(api.app)
        with mock.patch.object(api.AppealPipeline, "auto_appeal", return_value=held), \
                mock.patch.object(api, "_intake", return_value=None):
            r = client.post("/appeal", json={"documents": [
                {"evidence_id": "E1", "filename": "n.txt", "text": "Notice to Keeper"}]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        assert_customer_clean(self, body)
        self.assertEqual(body["questions"][0]["text"], "Please confirm the site postcode.")
        self.assertEqual(body["flags"], ["uncertain:vrm"])

    def test_the_real_postcode_question_carries_no_reason(self):
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        _, questions = run(case, pipe, "I would like to appeal this charge.")
        q = [q for q in questions if q["fact"] == "site_postcode"]
        self.assertEqual(len(q), 1)
        self.assertEqual(set(q[0]), {"fact", "type", "text"})
        # Why it was asked is kept - in the audit, for the admin trace.
        self.assertTrue(any(a.get("event") == "site_postcode_material" and a.get("unlocks")
                            for a in case.audit))

    def test_a_hold_payload_is_clean(self):
        from pcn_appeal.api import _held_questions, _outcome_fields
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        _, out = outcome(case, pipe)
        payload = {**_outcome_fields(out), **_held_questions(case, out)}
        self.assertEqual(payload["outcome"], "NEEDS_FACTS")
        assert_customer_clean(self, payload)

    def test_confirmation_screen_has_no_confidence_or_sources(self):
        from pcn_appeal import api
        client = TestClient(api.app)
        case_id = client.post("/cases").json()["case_id"]
        rec = api.CASES[case_id]
        rec["case"].put(Fact("F-vrm", "vrm", "AB12CDE", FactStatus.EXTRACTED,
                             FactSource(SourceKind.DOCUMENT, "E1#p1"), 0.93))
        rec["flags"] = ["uncertain:vrm", "injection_suspected:E1"]
        body = client.get(f"/cases/{case_id}/confirmation").json()
        self.assertNotIn("facts", body)
        self.assertEqual(body["flags"], ["uncertain:vrm"])
        assert_customer_clean(self, body)
        # The reviewer's view moved to an admin route.
        with mock.patch.dict(os.environ, {"ADMIN_TRACE_TOKEN": "t0k"}):
            self.assertEqual(client.get(f"/cases/{case_id}/facts").status_code, 401)
            full = client.get(f"/cases/{case_id}/facts", headers={"X-Admin-Token": "t0k"}).json()
        self.assertEqual(full["facts"][0]["confidence"], 0.93)

    def test_an_upload_refusal_names_no_routing_reason(self):
        from fastapi import HTTPException

        from pcn_appeal import api
        case = CaseFile("C-R")
        with self.assertRaises(HTTPException) as ctx:
            api._reject_incomplete(case, "only_front_pages", "FRONT_AND_BACK")
        self.assertEqual(set(ctx.exception.detail), {"message", "code"})
        self.assertTrue(any(a.get("reason") == "only_front_pages" for a in case.audit))

    def test_every_customer_route_is_scrubbed_by_the_middleware(self):
        from pcn_appeal import api
        for method, path in (("POST", "/appeal"), ("POST", "/appeal/files"),
                             ("POST", "/appeal/C-1"), ("POST", "/cases"),
                             ("POST", "/cases/C-1/files"), ("POST", "/cases/C-1/blobs"),
                             ("POST", "/cases/C-1/confirm"), ("GET", "/cases/C-1"),
                             ("GET", "/cases/C-1/confirmation"), ("GET", "/cases/C-1/letter.pdf")):
            self.assertTrue(api.is_customer_route(method, path), (method, path))
        for method, path in (("GET", "/cases/C-1/trace"), ("GET", "/cases/C-1/facts"),
                             ("GET", "/cases/C-1/appeal"), ("POST", "/cases/C-1/generate")):
            self.assertFalse(api.is_customer_route(method, path), (method, path))

    def test_the_middleware_catches_what_an_endpoint_lets_through(self):
        """Second layer: a customer route whose payload builder leaks is still
        cleaned on the way out, errors included."""
        from pcn_appeal import api
        client = TestClient(api.app)
        case_id = client.post("/cases").json()["case_id"]
        api.CASES[case_id]["case"].scope_stop = "ANY"
        leaky = {"stop_reason": "Stopped by KB-SCOPE-01 (VAL-STAGE).",
                 "module_ids": ["KB-LAND-01"], "confidence": 0.4}
        with mock.patch.object(api, "_stop_payload", return_value=leaky):
            body = client.get(f"/cases/{case_id}").json()
        assert_customer_clean(self, body)
        self.assertEqual(body["stop_reason"], "Stopped by.")
        missing = client.get("/cases/C-NOPE/confirmation")
        self.assertEqual(missing.status_code, 404)
        assert_customer_clean(self, missing.json())

    def test_validation_refuses_a_letter_carrying_an_internal_id(self):
        from pcn_appeal.engines.validation import ValidationEngine
        from pcn_appeal.models import Draft, DraftSentence
        pack = RetrievalPack(primary_route="BAY", secondary_routes=[], module_ids=["KB-BAY-01"],
                             verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
                             prohibited_claims=[], code_version=None, pofa_route="NOT_APPLICABLE",
                             pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="UNKNOWN",
                             context_chunks=[], lease_clauses=[], trace=[])
        for leak in ("RULE-JURIS-1", "MODULE-BAY", "SCOP-2024"):
            draft = Draft("C", [[DraftSentence(f"This is held under {leak}.",
                                               module_refs=["KB-BAY-01"])]])
            issues = ValidationEngine().validate(draft, pack).issues
            self.assertIn("VAL-LEAK", [i.rule for i in issues], leak)


# ------------------------------------------------------------------ P0.2
class RouteRegistry(unittest.TestCase):

    def test_the_landowner_kb_route_is_the_registered_route(self):
        kg = KnowledgeGraph()
        land = [m for m in kg.modules.values() if m.module_id.startswith("KB-LAND")]
        self.assertTrue(land)
        for m in land:
            self.assertEqual(m.route, Route.LANDOWNER)
        self.assertIn(Route.LANDOWNER, GROUND_ROUTES)

    def test_every_kb_route_is_registered(self):
        kg = KnowledgeGraph()
        known = {r.value for r in GROUND_ROUTES}
        self.assertLessEqual({m.route for m in kg.modules.values()}, known)
        self.assertLessEqual(set(kg.routes), known)

    def test_an_unregistered_kb_route_stops_the_load(self):
        with self.assertRaises(UnknownRouteError):
            validate_ground_routes([("KB-X-01", "LAND")], ["LANDOWNER"])
        with self.assertRaises(UnknownRouteError):
            validate_ground_routes([("KB-X-01", "LANDOWNER")], ["LANDOWNER", "NEW_ROUTE"])
        import yaml
        d = ROOT / "pcn_appeal" / "data"
        kb = yaml.safe_load((d / "kb_modules.yaml").read_text())
        kb["modules"][0]["route"] = "LAND"
        release = {"kb_modules": kb, "routes": yaml.safe_load((d / "routes.yaml").read_text()),
                   "building_blocks": yaml.safe_load((d / "building_blocks.yaml").read_text()),
                   "questions": yaml.safe_load((d / "questions.yaml").read_text())}
        with self.assertRaises(UnknownRouteError):
            KnowledgeGraph.from_release(release)

    def test_ccj_is_the_ccj_removal_service(self):
        self.assertIs(Route.CCJ, Route.CCJ_REMOVAL)
        from pcn_appeal.intake import router
        self.assertEqual(router.CCJ_REMOVAL, Route.CCJ.value)

    def test_no_route_is_compared_as_a_string_literal(self):
        names = "|".join(r.value for r in Route) + "|LAND"
        literal = re.compile(r"\broute\w*\s*(?:==|!=|\bin\b|\bnot in\b)\s*[\(\[{]?\s*[\"'](%s)[\"']"
                             % names)
        hits = []
        for path in (ROOT / "pcn_appeal").rglob("*.py"):
            if path.name == "routes.py":           # the registry's own docstring quotes the bug
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if literal.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(hits, [])


# ------------------------------------------------------------------ P0.3
def _hold(case):
    pack = RetrievalPack(primary_route=None, secondary_routes=[], module_ids=[], verified_facts={},
                         fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
                         code_version=None, pofa_route="UNRESOLVED", pofa_findings=[],
                         driver_status="UNIDENTIFIED", jurisdiction="UNKNOWN",
                         context_chunks=[], lease_clauses=[], trace=[])
    return classify_hold(case, pack, ValidationResult(False, []))["outcome"]


class FailingAnalysis:
    """The provider is down for case analysis; everything else still answers."""

    def __init__(self, llm):
        self._llm = llm

    def complete_json(self, *a, **kw):
        raise RuntimeError("provider unavailable")

    def __getattr__(self, name):
        return getattr(self._llm, name)


def _without_default_keeper(test, pipe):
    """These tests use a no-grounds case to check run bookkeeping. Since P8 a
    keeper case with no leading ground gets the default keeper appeal
    (KB-KEEPER-01); switching it off here keeps the NO_SUPPORTED_GROUNDS
    terminal (still reachable, e.g. once a driver is identified) under test."""
    m = pipe.kg.modules["KB-KEEPER-01"]
    old = m.status
    m.status = "DISABLED"
    test.addCleanup(setattr, m, "status", old)


class OnlyTheLatestRunDecides(unittest.TestCase):

    def test_no_grounds_then_an_api_failure_is_a_processing_error(self):
        case, pipe = case_with(extra=IN_TIME)
        _without_default_keeper(self, pipe)
        _, first = outcome(case, pipe)
        self.assertEqual(first.outcome, "NO_SUPPORTED_GROUNDS")
        run1 = case.run_id
        pipe.analysis.llm = FailingAnalysis(pipe.analysis.llm)
        pipe.answer(case, {"payment_made": True})
        second = pipe.generate(case)
        self.assertGreater(case.run_id, run1)
        self.assertEqual(second.outcome, "PROCESSING_ERROR")

    def test_an_old_failure_does_not_decide_a_new_completed_run(self):
        case = CaseFile("C-RUN")
        case.begin_run("confirm")
        case.audit.append({"event": "draft_error"})
        case.audit.append({"event": "ground_recovery", "was": ["KB-BAY-01"], "now": []})
        self.assertEqual(_hold(case), "PROCESSING_ERROR")
        case.complete_run("PROCESSING_ERROR")
        case.begin_run("answer")
        case.audit.append({"event": "case_analysis_completed"})
        case.audit.append({"event": "analysis_complete_no_supported_grounds"})
        self.assertEqual(_hold(case), "NO_SUPPORTED_GROUNDS")
        # Combining the runs - as classify_hold did before P0.3 - gives the old failure.
        legacy = CaseFile("C-LEGACY")
        legacy.audit = [{k: v for k, v in a.items() if k != "run_id"} for a in case.audit]
        self.assertEqual(_hold(legacy), "PROCESSING_ERROR")

    def test_every_audit_entry_belongs_to_a_run(self):
        case, pipe = case_with(extra=IN_TIME)
        _without_default_keeper(self, pipe)
        outcome(case, pipe)
        self.assertTrue(all(isinstance(a.get("run_id"), int) and a["run_id"] >= 1
                            for a in case.audit))
        self.assertEqual(case.run_status, "COMPLETED")
        done = [a for a in case.audit if a["event"] == "run_completed"]
        self.assertEqual(done[-1]["outcome"], "NO_SUPPORTED_GROUNDS")

    def test_a_paused_run_is_continued_not_restarted(self):
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        run(case, pipe, "I would like to appeal this charge.")
        paused = case.run_id
        self.assertEqual(case.run_status, "OPEN")
        pipe.answer(case, {"site_postcode": "M1 1AA"})
        out = pipe.generate(case)
        self.assertEqual(case.run_id, paused)
        self.assertIsNotNone(out.letter)


# ------------------------------------------------------------------ P0.4
def _fact(name, value, status, kind=SourceKind.DOCUMENT, ref="E1#p1"):
    return Fact(f"F-{name}", name, value, status, FactSource(kind, ref))


class FactProvenance(unittest.TestCase):

    def test_an_extraction_cannot_overwrite_a_customer_fact(self):
        case = CaseFile("C-F")
        case.begin_run("answer")
        case.put(_fact("children_present", True, FactStatus.ANSWERED, SourceKind.ANSWER, "q"))
        applied = case.put(_fact("children_present", False, FactStatus.EXTRACTED))
        self.assertFalse(applied)
        self.assertIs(case.facts["children_present"].value, True)
        self.assertEqual(len(case.fact_conflicts), 1)
        c = case.fact_conflicts[0]
        # P1: the customer's answer is kept, the reading recorded beside it.
        self.assertEqual((c["held_value"], c["proposed_value"], c["status"]),
                         (True, False, "KEPT_EXISTING"))
        self.assertTrue(any(a["event"] == "fact_conflict" for a in case.audit))

    def test_every_write_records_previous_new_source_time_and_reason(self):
        case = CaseFile("C-H")
        case.put(_fact("vrm", "AB12CDE", FactStatus.UNCERTAIN), reason="extraction")
        case.put(_fact("vrm", "AB12CDF", FactStatus.CORRECTED, SourceKind.ANSWER, "confirm:vrm"),
                 reason="customer correction")
        h = case.fact_history[-1]
        self.assertEqual((h["previous"], h["new"], h["source_kind"], h["source_ref"],
                          h["reason"], h["outcome"]),
                         ("AB12CDE", "AB12CDF", "ANSWER", "confirm:vrm",
                          "customer correction", "APPLIED"))
        self.assertRegex(h["at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_the_customer_can_correct_an_uncertain_reading(self):
        # P1 changed this: a correction of a CONFIDENT document reading is a
        # NEEDS_CONFIRMATION conflict (test_p1_fact_graph); an uncertain one,
        # which the system itself doubts, is corrected directly.
        case = CaseFile("C-C")
        case.put(_fact("pcn_number", "AB123456", FactStatus.UNCERTAIN))
        self.assertTrue(case.put(_fact("pcn_number", "AB123457", FactStatus.CORRECTED,
                                       SourceKind.ANSWER, "confirm:pcn_number")))
        self.assertEqual(case.facts["pcn_number"].value, "AB123457")

    def test_the_account_cannot_rewrite_what_the_notice_prints(self):
        case = CaseFile("C-D")
        case.put(_fact("charge_amount", "100", FactStatus.EXTRACTED))
        self.assertFalse(case.put(_fact("charge_amount", "60", FactStatus.ANSWERED,
                                        SourceKind.CUSTOMER_FREE_TEXT, "free_text:charge_amount")))
        self.assertEqual(case.fact_conflicts[0]["rule"], "document_owned")

    def test_a_reading_cannot_rewrite_what_the_customer_told_us(self):
        case = CaseFile("C-O")
        case.put(_fact("vehicle_immobilised", True, FactStatus.ANSWERED,
                       SourceKind.CUSTOMER_FREE_TEXT, "free_text:vehicle_immobilised"))
        self.assertFalse(case.put(_fact("vehicle_immobilised", False, FactStatus.EXTRACTED)))
        # Read from the account, not answered to a question, so it is ownership
        # (CUSTOMER) rather than a settled answer that keeps it.
        self.assertEqual(case.fact_conflicts[0]["rule"], "customer_owned")

    def test_a_placeholder_is_filled_without_conflict(self):
        case = CaseFile("C-P")
        case.put(_fact("jurisdiction", "UNKNOWN", FactStatus.CONFIRMED))
        self.assertTrue(case.put(_fact("jurisdiction", "ENGLAND_WALES", FactStatus.DERIVED,
                                       SourceKind.CALCULATION, "postcode")))
        self.assertEqual(case.fact_conflicts, [])

    def test_a_re_read_never_demotes_a_confirmation(self):
        case = CaseFile("C-S")
        case.put(_fact("vrm", "AB12CDE", FactStatus.CONFIRMED))
        case.put(_fact("vrm", "AB12 CDE", FactStatus.EXTRACTED))
        self.assertEqual(case.facts["vrm"].status, FactStatus.CONFIRMED)

    def test_conflicts_and_history_survive_a_reload(self):
        import sqlite_store
        from pcn_appeal.store import cases as store
        db = sqlite_store.install(self)
        case = store.new_case()
        case.begin_run("answer")
        case.put(_fact("children_present", True, FactStatus.ANSWERED, SourceKind.ANSWER, "q"))
        case.put(_fact("children_present", False, FactStatus.EXTRACTED))
        store.save(case)
        again = store.load(case.case_id)
        self.assertIs(again.facts["children_present"].value, True)
        self.assertEqual([(c["held_value"], c["proposed_value"]) for c in again.fact_conflicts],
                         [(True, False)])
        self.assertEqual([h["outcome"] for h in again.fact_history], ["APPLIED", "CONFLICT"])
        self.assertEqual((again.run_id, again.run_status), (1, "OPEN"))
        store.save(again)                       # nothing is written twice
        self.assertEqual(sqlite_store.count(db, "fact_history"), 2)


# ------------------------------------------------------------------ P0.5
MANIFEST_KEYS = {"case_id", "run_id", "commit", "build_id", "frontend_version", "kb", "prompts",
                 "provider", "models", "drafter", "validator", "inputs", "result", "created_at"}


class EveryLetterHasAManifest(unittest.TestCase):

    def released(self):
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        run(case, pipe, "I would like to appeal this charge.")
        pipe.answer(case, {"site_postcode": "M1 1AA"})
        out = pipe.generate(case)
        self.assertIsNotNone(out.letter)
        return case, pipe, out

    def test_a_generated_letter_has_a_manifest(self):
        from pcn_appeal import prompts
        from pcn_appeal.engines import validation
        from pcn_appeal.manifest import _sha
        case, pipe, out = self.released()
        m = out.manifest
        self.assertLessEqual(MANIFEST_KEYS, set(m))
        self.assertEqual((m["case_id"], m["run_id"]), (case.case_id, case.run_id))
        self.assertEqual(m["prompts"], prompts.versions())
        for task in ("extraction", "case_analysis", "drafting", "validation"):
            self.assertIn(task, m["prompts"])
        self.assertEqual(m["validator"]["version"], validation.VERSION)
        self.assertEqual(set(m["kb"]["modules"]), set(out.pack.module_ids))
        self.assertEqual(m["result"]["letter_sha256"], _sha(out.letter))
        self.assertEqual(m["drafter"]["model"], out.draft.model)
        self.assertTrue(any(a["event"] == "execution_manifest" for a in case.audit))

    def test_a_hold_has_a_manifest_too(self):
        case, pipe = case_with(extra=IN_TIME)
        _without_default_keeper(self, pipe)
        _, out = outcome(case, pipe)
        self.assertEqual(out.manifest["result"]["outcome"], "NO_SUPPORTED_GROUNDS")

    def test_the_manifest_is_stored_with_the_letter(self):
        import sqlite_store
        from pcn_appeal.store import cases as store
        sqlite_store.install(self)
        case, pipe, out = self.released()
        case.case_id = store.new_case().case_id
        store.save(case)
        store.save_output(case, out)
        again = store.load_output(store.load(case.case_id))
        self.assertEqual(again.manifest, json.loads(json.dumps(out.manifest, default=str)))

    def test_the_frontend_version_comes_from_the_proxy_header(self):
        from pcn_appeal import api
        client = TestClient(api.app)
        case_id = client.post("/cases", headers={"X-Frontend-Version": "abc123<script>"}).json()["case_id"]
        self.assertEqual(api.CASES[case_id]["case"].frontend_version, "abc123script")


if __name__ == "__main__":
    unittest.main()
