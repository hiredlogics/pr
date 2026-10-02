"""P5.5 - AI System Integrity Audit Layer.

Fact audit, question audit, claim protection, draft safety, reload, versions,
AI call log, state history, leakage detection, the database checks and the
journey harness - each as the spec lists them. Nothing here adds
intelligence; these tests prove the inspection layer sees what the system did.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api, integrity
from pcn_appeal.engines.claim_plan_authority import ClaimPlanLockedError, latest_locked
from pcn_appeal.integrity import ai_log, checks, trace
from pcn_appeal.integrity.journeys import JourneyRunner, load_journey, run_directory
from pcn_appeal.models import CaseFile, CaseState, Draft, DraftSentence, Fact, FactSource, \
    FactStatus, SourceKind
from pcn_appeal.store import cases as store
import sqlite_store
from test_claim_plan_authority import NARRATIVE, anpr, answer, bay, scenario
from test_question_authority import case_with, run

JOURNEYS = Path(__file__).resolve().parent.parent / "journeys"


class FactAudit(unittest.TestCase):
    """Who created or changed each fact, and from what."""

    def test_every_fact_has_a_source_and_a_write_record(self):
        case, pipe = bay()
        pipe.generate(case)
        result = {c["check"]: c for c in checks.check_case(case, None, pipe.kg)}
        self.assertEqual(result["FACTS_HAVE_SOURCES"]["status"], "PASS",
                         result["FACTS_HAVE_SOURCES"])
        names = {h["fact"] for h in case.fact_history}
        self.assertTrue(set(case.facts) <= names, set(case.facts) - names)

    def test_a_changed_fact_keeps_old_and_new_with_the_writer(self):
        case, pipe = anpr()
        pipe.generate(case)
        answer(case, {"payment_made": True})
        rows = [h for h in case.fact_history if h["fact"] == "payment_made"]
        self.assertTrue(rows)
        self.assertEqual(str(rows[-1]["new"]), "True")
        self.assertTrue(rows[-1].get("source_ref"))
        self.assertIsNone(rows[-1].get("previous"))
        self.assertTrue(rows[-1].get("at"))
        self.assertTrue(rows[-1].get("changed_by"))

    def test_the_report_lists_facts_and_redacts_personal_ones(self):
        case, pipe = bay()
        out = pipe.generate(case)
        report = out.integrity["report"]
        self.assertIn("## Facts", report)
        self.assertIn("| alleged_breach |", report)
        for fact in checks_report_redacted():
            if fact in case.facts:
                self.assertNotIn(str(case.facts[fact].value), report)


def checks_report_redacted():
    from pcn_appeal.integrity.report import REDACTED
    return REDACTED


class QuestionAudit(unittest.TestCase):
    """Every approved and rejected question carries its reason."""

    def test_approved_and_rejected_questions_have_reasons(self):
        case, pipe = case_with(ask=[
            {"fact": "payment_made", "text": "Was a parking payment made for this visit?",
             "type": "bool", "material_because": "permission defeats the alleged breach"},
            {"fact": "site_postcode", "text": "What is the postcode?", "type": "text"}])
        run(case, pipe, "I want to challenge this charge")
        rows = [a for a in case.audit if a.get("event") == "question_review"]
        self.assertTrue(rows)
        decisions = {a["decision"] for a in rows}
        self.assertTrue(decisions <= {"APPROVED", "REJECTED"}, decisions)
        for a in rows:
            self.assertTrue(str(a.get("reason") or "").strip(), a)
        result = {c["check"]: c for c in checks.check_case(case, None, pipe.kg)}
        self.assertEqual(result["QUESTIONS_HAVE_REASONS"]["status"], "PASS")
        self.assertIn("## Questions", integrity.case_report(case, None, pipe.kg))

    def test_a_question_without_a_reason_fails_the_check(self):
        case, pipe = bay()
        case.audit.append({"event": "question_review", "fact": "x", "decision": "APPROVED",
                           "reason": "", "shown": True})
        result = {c["check"]: c for c in checks.check_case(case, None, pipe.kg)}
        self.assertEqual(result["QUESTIONS_HAVE_REASONS"]["status"], "FAIL")


class ClaimProtection(unittest.TestCase):

    def test_a_locked_plan_cannot_change(self):
        case, pipe = bay()
        pipe.generate(case)
        plan = latest_locked(case)
        with self.assertRaises(ClaimPlanLockedError):
            plan.add_item(plan.items[0])
        with self.assertRaises(ClaimPlanLockedError):
            plan.items = ()
        with self.assertRaises(Exception):
            plan.items[0].priority = 99
        with self.assertRaises(ClaimPlanLockedError):
            plan.status = "DRAFT"


class DraftSafety(unittest.TestCase):

    def test_an_argument_outside_the_plan_is_caught(self):
        case, pipe = anpr()
        out = pipe.generate(case)
        plan = latest_locked(case)
        rejected = next(i.module_id for i in plan.items if i.status != "SUPPORTED")
        out.draft = Draft(case.case_id, [[DraftSentence("An argument nobody approved.", [], [rejected], [])]])
        result = {c["check"]: c for c in checks.check_case(case, out, pipe.kg)}
        self.assertEqual(result["NO_CLAIM_OUTSIDE_PLAN"]["status"], "FAIL")
        self.assertIn(rejected, str(result["NO_CLAIM_OUTSIDE_PLAN"]["detail"]))

    def test_a_draft_without_a_locked_plan_is_caught(self):
        case, pipe = anpr()
        out = pipe.generate(case)
        case.claim_plans.clear()
        result = {c["check"]: c for c in checks.check_case(case, out, pipe.kg)}
        self.assertEqual(result["DRAFT_REQUIRES_LOCKED_PLAN"]["status"], "FAIL")

    def test_a_clean_run_passes_every_check(self):
        case, pipe = bay()
        out = pipe.generate(case)
        failed = [c for c in out.integrity["checks"] if c["status"] != "PASS"]
        self.assertEqual(failed, [])
        self.assertTrue(out.integrity["passed"])
        self.assertIn("integrity_check", [a.get("event") for a in case.audit])


class LeakageDetection(unittest.TestCase):
    """Positive controls: each kind of leak is caught."""

    def _check(self, letter: str) -> dict:
        case, pipe = bay()
        out = pipe.generate(case)
        out.letter = letter
        return {c["check"]: c for c in checks.check_case(case, out, pipe.kg)}

    def test_internal_id_in_the_letter(self):
        r = self._check("I rely on KB-BAY-02 and VAL-PLAN.")
        self.assertEqual(r["NO_CUSTOMER_LEAKAGE"]["status"], "FAIL")

    def test_placeholder_and_trace_vocabulary(self):
        self.assertEqual(self._check("Dear {{operator}},")["NO_CUSTOMER_LEAKAGE"]["status"], "FAIL")
        self.assertEqual(self._check("see the RetrievalPack")["NO_CUSTOMER_LEAKAGE"]["status"],
                         "FAIL")

    def test_prompt_text(self):
        fragment = checks._prompt_fragments()[0]
        self.assertEqual(self._check(f"...{fragment}...")["NO_CUSTOMER_LEAKAGE"]["status"], "FAIL")

    def test_driver_identification(self):
        r = self._check("I parked there at noon.")
        self.assertEqual(r["DRIVER_NOT_IDENTIFIED"]["status"], "FAIL")


class AICallLog(unittest.TestCase):

    def test_every_model_call_is_logged_with_hashes_not_text(self):
        case, pipe = bay()
        pipe.generate(case)
        calls = case.ai_calls
        self.assertTrue(calls)
        tasks = {c["task"] for c in calls}
        self.assertTrue({"extraction", "case_analysis", "drafting"} <= tasks, tasks)
        for c in calls:
            self.assertEqual(c["case_id"], case.case_id)
            self.assertEqual(c["run_id"], case.run_id)
            self.assertEqual(c["status"], "SUCCESS")
            self.assertRegex(c["input_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(c["output_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(c["model"])
            self.assertIsNotNone(c["prompt_version"])
            self.assertTrue(c["at"])
            # references, not content
            self.assertNotIn("user", c)
            self.assertNotIn("system", c)
            self.assertNotIn("output", c)
            for v in c.values():
                self.assertNotIn(NARRATIVE, str(v))
        self.assertEqual(len([a for a in case.audit if a.get("event") == "ai_call"]), len(calls))

    def test_a_failing_call_is_logged_as_an_error(self):
        class Boom:
            def complete_json(self, **kw):
                raise RuntimeError("provider down")
        case = CaseFile("C-ERR")
        case.ensure_run("test")
        with self.assertRaises(RuntimeError):
            ai_log.audited(Boom()).complete_json(task="drafting", system="s", user="{}")
        self.assertEqual(case.ai_calls[-1]["status"], "ERROR")
        self.assertIn("provider down", case.ai_calls[-1]["error"])


class StateHistory(unittest.TestCase):

    def test_transitions_are_recorded_with_reasons_and_end_at_the_final_state(self):
        case, pipe = bay()
        pipe.generate(case)
        history = trace.state_history(case)
        self.assertEqual(history[0]["from"], "CREATED")
        self.assertEqual(history[-1]["to"], case.state.value)
        for i in range(len(history) - 1):
            self.assertEqual(history[i]["to"], history[i + 1]["from"])
        self.assertTrue(all(t["reason"] for t in history), history)
        self.assertIn("claim_plan_locked", " ".join(t["reason"] for t in history))

    def test_a_broken_chain_fails_the_check(self):
        case, pipe = bay()
        pipe.generate(case)
        case.state_history.append({"from": "DRAFTED", "to": "CREATED", "run_id": 1, "at": "x"})
        result = {c["check"]: c for c in checks.check_case(case, None, pipe.kg)}
        self.assertEqual(result["STATE_MACHINE_CONSISTENT"]["status"], "FAIL")


class Versions(unittest.TestCase):

    def test_model_prompt_and_kb_versions_are_in_the_trace(self):
        case, pipe = bay()
        out = pipe.generate(case)
        v = out.integrity["trace"]["versions"]
        self.assertTrue(v["kb"])
        self.assertTrue(v["prompts"])
        self.assertTrue(v["models"])
        self.assertEqual(v["claim_plan_version"], 1)
        self.assertIn("## Versions", out.integrity["report"])

    def test_execution_trace_shape(self):
        case, pipe = bay()
        out = pipe.generate(case)
        t = out.integrity["trace"]
        self.assertEqual([s["stage"] for s in t["steps"]], list(trace.STAGES))
        by = {s["stage"]: s for s in t["steps"]}
        for stage in ("FACT_EXTRACTION", "KNOWLEDGE_MATCH", "CASE_ANALYSIS", "CLAIM_PLAN",
                      "RETRIEVAL", "DRAFTING", "VALIDATION", "OUTCOME"):
            self.assertEqual(by[stage]["status"], "SUCCESS", (stage, by[stage]))
        self.assertGreaterEqual(by["FACT_EXTRACTION"]["facts_created"], 1)
        self.assertEqual(by["CLAIM_PLAN"]["decision"], "claim_plan_locked")
        self.assertTrue(t["execution_id"])
        self.assertEqual(t["final_state"], case.state.value)


class Reload(unittest.TestCase):

    def setUp(self):
        self.db = sqlite_store.install(self)
        row = store.new_case()
        self.case, self.pipe = bay(case_id=row.case_id)

    def test_the_same_case_gives_the_same_trace_after_reload(self):
        out = self.pipe.generate(self.case)
        store.save(self.case)
        store.save_output(self.case, out)
        store.save_execution_trace(self.case, out)
        store.save(self.case)                                       # idempotent
        loaded = store.load(self.case.case_id)
        self.assertEqual(sqlite_store.count(self.db, "case_state_history"),
                         len(self.case.state_history))
        self.assertEqual(sqlite_store.count(self.db, "ai_execution_logs"), len(self.case.ai_calls))
        self.assertEqual(sqlite_store.count(self.db, "case_execution_trace"), 1)

        before = trace.execution_trace(self.case)
        after = trace.execution_trace(loaded)
        for key in ("execution_id", "final_state", "versions", "claim_plan"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual([(s["stage"], s["status"]) for s in before["steps"]],
                         [(s["stage"], s["status"]) for s in after["steps"]])
        self.assertEqual([(t["from"], t["to"], t["reason"]) for t in before["state_history"]],
                         [(t["from"], t["to"], t["reason"]) for t in after["state_history"]])
        self.assertEqual([c["input_sha256"] for c in before["ai_calls"]],
                         [c["input_sha256"] for c in after["ai_calls"]])
        stored = store.load_execution_trace(self.case.case_id)
        self.assertTrue(stored["passed"])
        self.assertEqual(stored["execution_id"], before["execution_id"])
        self.assertIn("# Case", stored["report"])

    def test_database_checks_pass_on_a_clean_case_and_catch_a_bad_row(self):
        out = self.pipe.generate(self.case)
        store.save(self.case)
        store.save_output(self.case, out)
        results = {r["check"]: r for r in checks.check_store(store.connect)}
        self.assertEqual([k for k, r in results.items() if r["status"] != "PASS"], [])
        # the database itself refuses to unlock the plan ...
        with self.assertRaises(Exception):
            self.db.execute("UPDATE claim_plans SET status = 'DRAFT'")
        # ... and a fact whose write record has gone is caught
        self.db.execute("DELETE FROM fact_history WHERE fact = 'alleged_breach'")
        self.db.commit()
        results = {r["check"]: r for r in checks.check_store(store.connect, self.case.case_id)}
        self.assertEqual(results["FACTS_HAVE_WRITE_RECORDS"]["status"], "FAIL")
        self.assertIn("alleged_breach", str(results["FACTS_HAVE_WRITE_RECORDS"]["detail"]))


class JourneyHarness(unittest.TestCase):
    """Input-only journey files, driven through the real customer routes."""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k", "DATABASE_URL": ""})
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(api.app)
        self.runner = JourneyRunner(self.client, "t0k")

    def test_every_shipped_journey_passes(self):
        results = run_directory(self.runner, JOURNEYS)
        self.assertGreaterEqual(len(results), 5)
        for r in results:
            self.assertTrue(r.passed, (r.name, r.failures))
            self.assertIn("# Case", r.report)
        released = [r for r in results if r.state == "RELEASED"]
        self.assertTrue(released, [(r.name, r.state) for r in results])

    def test_an_unmet_expectation_fails_the_journey(self):
        j = load_journey(JOURNEYS / "overstay_skip_all.yaml")
        j["repeat"] = 1
        j["expect"] = {"state": "MANUAL_REVIEW", "letter_contains": ["unicorn"]}
        r = self.runner.run(j)
        self.assertFalse(r.passed)
        self.assertEqual(len(r.failures), 2, r.failures)

    def test_admin_audit_routes(self):
        j = load_journey(JOURNEYS / "overstay_skip_all.yaml")
        final = self.runner.drive(j)
        cid = final["case_id"]
        self.assertIn(self.client.get(f"/admin/cases/{cid}/audit").status_code, (401, 403))
        audit = self.client.get(f"/admin/cases/{cid}/audit", headers=self.runner.headers).json()
        self.assertTrue(audit["integrity"]["passed"], audit["integrity"])
        self.assertEqual(audit["trace"]["final_state"], final["state"])
        md = self.client.get(f"/admin/cases/{cid}/audit/report.md", headers=self.runner.headers)
        self.assertEqual(md.status_code, 200)
        self.assertIn("## Timeline", md.text)
        et = self.client.get(f"/admin/cases/{cid}/execution-trace",
                             headers=self.runner.headers).json()
        self.assertEqual(et["execution_id"], audit["trace"]["execution_id"])
        internal = self.client.get(f"/cases/{cid}/trace", headers=self.runner.headers).json()
        self.assertIn("execution_trace", internal)
        self.assertIn("integrity", internal)


if __name__ == "__main__":
    unittest.main()
