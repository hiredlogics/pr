"""P1: the Fact Graph layer.

Spec tests 1-5 first, then the guarantees they rest on: writes only through
FactManager, conflicts asked about rather than passed silently, the internal
Fact API, and a reload that gives back the same graph.

All values are synthetic. No operator or charge is special-cased.
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from fastapi.testclient import TestClient

import sqlite_store
from pcn_appeal import disclosure, fact_graph
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.outcome import classify_hold
from pcn_appeal.fact_graph import (CONFLICT, IGNORED, KEPT_EXISTING, NEEDS_CONFIRMATION,
                                   RESOLVED, FactManager, GraphStatus, SourceType)
from pcn_appeal.models import (CaseFile, CaseState, Fact, FactGraph, FactSource, FactStatus,
                               FactWriteError, SourceKind)
from pcn_appeal.store import cases as store
from test_no_weak_fallback import LATE, case_with
from test_question_authority import run


def _fact(name, value, status=FactStatus.EXTRACTED, kind=SourceKind.DOCUMENT, ref="E1#p1",
          confidence=1.0):
    return Fact(f"F-{name}", name, value, status, FactSource(kind, ref), confidence)


def _customer(name, value, status=FactStatus.CORRECTED):
    return _fact(name, value, status, SourceKind.ANSWER, f"confirm:{name}")


# ------------------------------------------------------------------ spec tests
class SpecTests(unittest.TestCase):

    def test_1_document_vrm_against_customer_vrm_is_a_conflict_not_an_overwrite(self):
        case = CaseFile("C-1")
        case.put(_fact("vrm", "ABC123"), reason="extraction")
        applied = case.put(_customer("vrm", "XYZ999"), reason="customer correction")
        self.assertFalse(applied)
        self.assertEqual(case.facts["vrm"].value, "ABC123")          # not overwritten
        [c] = case.fact_conflicts
        self.assertEqual((c["held_value"], c["proposed_value"], c["status"], c["rule"]),
                         ("ABC123", "XYZ999", NEEDS_CONFIRMATION, "document_owned"))
        self.assertEqual((c["held_source_type"], c["proposed_source_type"]),
                         (SourceType.DOCUMENT.value, SourceType.CUSTOMER_ANSWER.value))
        # Disputed: not a verified fact until the customer confirms which.
        self.assertIsNone(case.get("vrm"))
        self.assertNotIn("vrm", case.fact_view())
        self.assertEqual(fact_graph.node(case, "vrm")["status"], GraphStatus.CONFLICT.value)
        self.assertEqual([x["fact"] for x in FactManager.needs_confirmation(case)], ["vrm"])

    def test_2_an_unknown_extraction_does_not_replace_the_customers_fact(self):
        case = CaseFile("C-2")
        case.put(_customer("children_present", True, FactStatus.ANSWERED))
        for placeholder in (None, "UNKNOWN", ""):
            with self.subTest(ai=placeholder):
                self.assertFalse(case.put(_fact("children_present", placeholder,
                                                FactStatus.DERIVED, SourceKind.CALCULATION,
                                                "ai_extraction")))
                self.assertIs(case.facts["children_present"].value, True)
        self.assertEqual(case.fact_history[-1]["outcome"], IGNORED)
        # The alias and the canonical name are one node.
        self.assertIs(case.get("child_occupant_present"), True)
        self.assertEqual(list(case.facts), ["child_occupant_present"])

    def test_2b_a_contrary_reading_is_kept_beside_the_customers_fact(self):
        case = CaseFile("C-2b")
        case.put(_customer("children_present", True, FactStatus.ANSWERED))
        self.assertFalse(case.put(_fact("children_present", False)))
        self.assertIs(case.get("children_present"), True)
        self.assertEqual(case.fact_conflicts[0]["status"], KEPT_EXISTING)
        self.assertEqual(FactManager.needs_confirmation(case), [])

    def test_3_a_narrative_describes_the_event_and_identifies_no_driver(self):
        case = CaseFile("C-3")
        case.raw_answers["narrative"] = "I parked and my children were with me"
        assess_material_account(case)
        self.assertIs(case.get("customer_described_event"), True)
        self.assertIs(case.get("children_present"), True)
        self.assertEqual(disclosure.parse_disclosure(case.get("driver_disclosure")),
                         disclosure.DISCLOSURE_UNKNOWN)
        self.assertNotIn(disclosure.DISCLOSURE_FACT, case.facts)   # never set from free text
        self.assertNotIn("driver_identified", case.facts)
        self.assertFalse(disclosure.keeper_route_blocked(case))
        self.assertEqual(case.driver_status.value, "UNIDENTIFIED")
        self.assertEqual(fact_graph.node(case, "customer_described_event")["source_type"],
                         SourceType.CUSTOMER_FREE_TEXT.value)

    def test_3b_the_described_event_is_not_letter_content(self):
        # Provenance only: it must not change what the drafter or analysis sees.
        from pcn_appeal.engines.analysis import is_internal_fact
        self.assertTrue(is_internal_fact("customer_described_event"))

    def test_3c_a_negated_account_describes_no_event(self):
        case = CaseFile("C-3c")
        case.raw_answers["narrative"] = "I never parked there, it was not me at all"
        assess_material_account(case)
        self.assertIsNone(case.get("customer_described_event"))

    def test_4_a_fact_update_creates_a_history_record(self):
        case = CaseFile("C-4")
        case.begin_run("test")
        case.put(_fact("charge_amount", "100", FactStatus.UNCERTAIN), reason="extraction")
        case.put(_customer("charge_amount", "60"), reason="customer correction")
        first, second = case.fact_history
        node_id = case.facts.node_id("charge_amount")
        self.assertEqual(first["fact_id"], node_id)
        self.assertEqual(second["fact_id"], node_id)                  # one node, two writes
        self.assertEqual((second["previous"], second["new"], second["source_type"],
                          second["changed_by"], second["reason"], second["outcome"]),
                         ("100", "60", "CUSTOMER_ANSWER", "customer",
                          "customer correction", "APPLIED"))
        self.assertRegex(second["at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(second["run_id"], 1)
        self.assertEqual(fact_graph.history_of(case, node_id), [first, second])

    def test_5_a_case_reloaded_from_the_database_has_identical_facts(self):
        sqlite_store.install(self)
        case = store.new_case()
        case.begin_run("test")
        case.put(_fact("vrm", "ABC123", confidence=0.93))
        case.put(_customer("vrm", "XYZ999"))                          # open conflict
        case.put(_fact("parking_event_date", date(2026, 6, 1)))
        case.put(_customer("children_present", True, FactStatus.ANSWERED))
        case.put(_fact("children_present", "UNKNOWN", FactStatus.DERIVED,
                       SourceKind.CALCULATION, "ai"))                  # ignored
        case.put(_fact("operator_name", "Example Parking Ltd"))
        case.retract("operator_name", "test: retracted")
        store.save(case)

        again = store.load(case.case_id)
        self.assertEqual(dict(again.facts), dict(case.facts))          # Fact objects, exactly
        self.assertEqual(fact_graph.nodes(again), fact_graph.nodes(case))
        self.assertEqual(again.facts.node_id("operator_name"),
                         case.facts.node_id("operator_name"))         # retracted id kept
        strip = lambda rows: [{k: v for k, v in r.items() if k != "_persisted"} for r in rows]
        self.assertEqual(strip(again.fact_history), strip(case.fact_history))
        self.assertEqual(strip(again.fact_sources), strip(case.fact_sources))
        self.assertEqual(again.fact_conflicts, case.fact_conflicts)
        self.assertIsInstance(again.facts["parking_event_date"].value, date)
        self.assertTrue(again.facts["vrm"].disputed)
        self.assertEqual(FactManager.confirmation_questions(again),
                         FactManager.confirmation_questions(case))

    def test_5b_saving_again_writes_nothing_twice(self):
        db = sqlite_store.install(self)
        case = store.new_case()
        case.put(_fact("vrm", "ABC123"))
        case.put(_customer("vrm", "XYZ999"))
        store.save(case)
        again = store.load(case.case_id)
        store.save(again)
        store.save(again)
        self.assertEqual(sqlite_store.count(db, "facts"), 1)
        self.assertEqual(sqlite_store.count(db, "fact_history"), 2)
        self.assertEqual(sqlite_store.count(db, "fact_sources"), 2)
        self.assertEqual(sqlite_store.count(db, "fact_conflicts"), 1)


# ------------------------------------------------------------------ write path
class OnlyFactManagerWrites(unittest.TestCase):

    def test_the_graph_refuses_direct_writes(self):
        case = CaseFile("C-W")
        case.put(_fact("vrm", "ABC123"))
        for attempt in (lambda: case.facts.__setitem__("vrm", _fact("vrm", "X")),
                        lambda: case.facts.pop("vrm"),
                        lambda: case.facts.update({}),
                        lambda: case.facts.clear()):
            with self.assertRaises(FactWriteError):
                attempt()
        with self.assertRaises(Exception):                           # frozen dataclass
            case.facts["vrm"].value = "X"
        self.assertEqual(case.get("vrm"), "ABC123")

    def test_assigning_a_dict_still_goes_through_the_graph(self):
        case = CaseFile("C-A")
        case.facts = {}
        self.assertIsInstance(case.facts, FactGraph)
        with self.assertRaises(FactWriteError):
            case.facts["vrm"] = _fact("vrm", "X")

    def test_a_node_id_is_stable_across_updates(self):
        case = CaseFile("C-N")
        case.put(_fact("pcn_number", "AB1", FactStatus.UNCERTAIN))
        first = case.facts.node_id("pcn_number")
        case.put(_customer("pcn_number", "AB2"))
        self.assertEqual(case.facts.node_id("pcn_number"), first)
        self.assertEqual(case.facts.name_of(first), "pcn_number")

    def test_an_uncertain_reading_is_the_customers_to_correct(self):
        case = CaseFile("C-U")
        case.put(_fact("vrm", "ABC123", FactStatus.UNCERTAIN))
        self.assertTrue(case.put(_customer("vrm", "XYZ999")))
        self.assertEqual(case.get("vrm"), "XYZ999")
        self.assertEqual(case.fact_conflicts, [])

    def test_free_text_cannot_rewrite_evidence(self):
        case = CaseFile("C-E")
        case.put(_fact("payment_recorded_in_document", True))
        self.assertFalse(case.put(_fact("payment_recorded_in_document", False,
                                        FactStatus.ANSWERED, SourceKind.CUSTOMER_FREE_TEXT,
                                        "free_text:payment")))
        self.assertEqual(case.fact_conflicts[0]["status"], KEPT_EXISTING)

    def test_correcting_twice_does_not_get_round_the_confirmation(self):
        case = CaseFile("C-T")
        case.put(_fact("vrm", "ABC123"))
        case.put(_customer("vrm", "XYZ999"))
        case.put(_customer("vrm", "QQQ111"))
        self.assertEqual(case.facts["vrm"].value, "ABC123")
        self.assertTrue(case.facts["vrm"].disputed)


# ------------------------------------------------------------------ resolution
class ConflictsAreAskedNotPassed(unittest.TestCase):

    def test_the_question_offers_only_the_two_values(self):
        case = CaseFile("C-Q")
        case.put(_fact("parking_event_date", date(2026, 6, 1)))
        case.put(_customer("parking_event_date", date(2026, 6, 2)))
        [q] = FactManager.confirmation_questions(case)
        self.assertEqual((q["fact"], q["type"]), ("parking_event_date", "choice"))
        self.assertEqual(q["options"], ["1 June 2026", "2 June 2026"])

    def test_choosing_a_value_resolves_the_conflict(self):
        for chosen, status in (("ABC123", FactStatus.CONFIRMED),
                               ("XYZ999", FactStatus.CORRECTED)):
            with self.subTest(chosen=chosen):
                case = CaseFile("C-R")
                case.put(_fact("vrm", "ABC123"))
                case.put(_customer("vrm", "XYZ999"))
                self.assertTrue(case.put(_customer("vrm", chosen, FactStatus.ANSWERED)))
                self.assertEqual((case.get("vrm"), case.facts["vrm"].status), (chosen, status))
                self.assertFalse(case.facts["vrm"].disputed)
                self.assertEqual(case.fact_conflicts[0]["status"], RESOLVED)
                self.assertEqual(FactManager.needs_confirmation(case), [])

    def test_a_shown_date_resolves_to_the_typed_date(self):
        case = CaseFile("C-D")
        case.put(_fact("parking_event_date", date(2026, 6, 1)))
        case.put(_customer("parking_event_date", date(2026, 6, 2)))
        case.put(_customer("parking_event_date", "2 June 2026", FactStatus.ANSWERED))
        self.assertEqual(case.get("parking_event_date"), date(2026, 6, 2))

    def test_the_pipeline_asks_and_does_not_continue_silently(self):
        case, pipe = case_with(extra=LATE)
        pipe.ingest(case)
        held = case.get("vrm")
        self.assertIsNotNone(held)
        typo = held[:-1] + ("X" if held[-1] != "X" else "Y")
        confirmable = [n for n, f in case.facts.items()
                       if f.status == FactStatus.EXTRACTED and n != "vrm"]
        questions = pipe.confirm(case, {"vrm": typo}, confirmable,
                                 "I would like to appeal this charge.")
        self.assertEqual(case.facts["vrm"].value, held)
        asked = next(q for q in questions if q["fact"] == "vrm")
        self.assertEqual(asked["options"], [held, typo])

        # Not answered: generate holds for the confirmation, never drafts.
        out = pipe.generate(case)
        self.assertIsNone(out.letter)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertEqual(out.outcome, "NEEDS_FACTS")
        self.assertIn("held_needs_fact_confirmation", [a["event"] for a in case.audit])
        self.assertEqual([q["fact"] for q in case.pending_questions], ["vrm"])

    def test_answering_the_confirmation_lets_the_case_proceed(self):
        case, pipe = case_with(extra=LATE)
        pipe.ingest(case)
        held = case.get("vrm")
        typo = held[:-1] + ("X" if held[-1] != "X" else "Y")
        confirmable = [n for n, f in case.facts.items()
                       if f.status == FactStatus.EXTRACTED and n != "vrm"]
        pipe.confirm(case, {"vrm": typo}, confirmable, "I would like to appeal this charge.")
        pipe.answer(case, {"vrm": held})
        self.assertEqual((case.get("vrm"), case.facts["vrm"].status),
                         (held, FactStatus.CONFIRMED))
        self.assertEqual(FactManager.needs_confirmation(case), [])
        pipe.generate(case)
        self.assertNotIn("held_needs_fact_confirmation",
                         [a["event"] for a in case.current_run_audit()])

    def test_the_hold_is_needs_facts_not_a_processing_error(self):
        case = CaseFile("C-H")
        case.begin_run("test")
        case.audit.append({"event": "held_needs_fact_confirmation", "facts": ["vrm"]})
        self.assertEqual(classify_hold(case, None, None)["outcome"], "NEEDS_FACTS")


# ------------------------------------------------------------------ Fact API
class FactApi(unittest.TestCase):

    def setUp(self):
        from pcn_appeal import api
        self.api = api
        self.client = TestClient(api.app)
        env = mock.patch.dict("os.environ", {"ADMIN_TRACE_TOKEN": "", "ADMIN_TOKEN": "",
                                             "APP_ENV": "development"})
        env.start()
        self.addCleanup(env.stop)
        self.case = CaseFile("C-API")
        self.case.put(_fact("vrm", "ABC123"))
        api.CASES[self.case.case_id] = {"case": self.case, "pipe": None, "flags": [],
                                       "questions": [], "output": None}
        self.addCleanup(api.CASES.pop, self.case.case_id, None)

    def test_get_returns_the_graph(self):
        body = self.client.get(f"/cases/{self.case.case_id}/facts").json()
        [n] = body["nodes"]
        self.assertEqual((n["fact_name"], n["fact_value"], n["source_type"], n["status"]),
                         ("vrm", "ABC123", "DOCUMENT", "EXTRACTED"))
        self.assertEqual(n["fact_id"], self.case.facts.node_id("vrm"))
        self.assertEqual(body["fact_conflicts"], [])

    def test_post_goes_through_fact_manager(self):
        r = self.client.post(f"/cases/{self.case.case_id}/facts",
                             json={"fact_name": "vrm", "value": "XYZ999", "reason": "operator"})
        self.assertEqual(r.json()["outcome"], CONFLICT)
        self.assertEqual(self.case.get("vrm"), None)                 # disputed, not replaced
        self.assertEqual(self.case.facts["vrm"].value, "ABC123")
        cid = r.json()["conflict"]["conflict_id"]
        self.assertNotIn("_held", r.json()["conflict"])
        body = self.client.get(f"/cases/{self.case.case_id}/facts").json()
        self.assertEqual(body["needs_confirmation"], ["vrm"])
        self.assertTrue(all(not k.startswith("_") for c in body["fact_conflicts"] for k in c))

        r = self.client.post(f"/cases/{self.case.case_id}/facts",
                             json={"conflict_id": cid, "value": "XYZ999",
                                   "reason": "checked the notice"})
        self.assertEqual(r.json()["outcome"], "APPLIED")
        self.assertEqual(self.case.get("vrm"), "XYZ999")
        self.assertEqual(self.case.fact_history[-1]["changed_by"], "admin")

    def test_a_write_needs_a_reason_and_a_target(self):
        url = f"/cases/{self.case.case_id}/facts"
        self.assertEqual(self.client.post(url, json={"fact_name": "vrm", "value": "X",
                                                     "reason": " "}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"value": "X",
                                                     "reason": "r"}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"conflict_id": "nope", "value": "X",
                                                     "reason": "r"}).status_code, 404)

    def test_history_by_fact_id(self):
        node_id = self.case.facts.node_id("vrm")
        self.client.post(f"/cases/{self.case.case_id}/facts",
                         json={"fact_name": "vrm", "value": "XYZ999", "reason": "operator"})
        body = self.client.get(f"/facts/{node_id}/history").json()
        self.assertEqual(body["fact_name"], "vrm")
        self.assertEqual([h["outcome"] for h in body["history"]], ["APPLIED", "CONFLICT"])
        self.assertEqual(self.client.get("/facts/unknown/history").status_code, 404)

    def test_the_fact_api_is_admin_only(self):
        with mock.patch.dict("os.environ", {"ADMIN_TRACE_TOKEN": "t"}):
            self.assertEqual(self.client.get(f"/cases/{self.case.case_id}/facts").status_code, 401)
            self.assertEqual(self.client.post(f"/cases/{self.case.case_id}/facts",
                                              json={"fact_name": "vrm", "value": "X",
                                                    "reason": "r"}).status_code, 401)
            node_id = self.case.facts.node_id("vrm")
            self.assertEqual(self.client.get(f"/facts/{node_id}/history").status_code, 401)
        self.assertFalse(self.api.is_customer_route("GET", f"/cases/{self.case.case_id}/facts"))
        self.assertFalse(self.api.is_customer_route("POST", f"/cases/{self.case.case_id}/facts"))


if __name__ == "__main__":
    unittest.main()
