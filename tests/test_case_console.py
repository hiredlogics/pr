"""P7.5 — Case Intelligence Trace console (admin aggregation, no re-reasoning)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api
from pcn_appeal.engines.claim_plan_authority import (
    SELECTED, SUPPORTED, VERIFIED_FINDING,
    ClaimPlanItem, FinalClaimPlan,
)
from pcn_appeal.integrity import console as cons
from pcn_appeal.models import (
    CaseFile, CaseState, Fact, FactSource, FactStatus, SourceKind,
)


def _fact(name, value, *, kind=SourceKind.DOCUMENT, status=FactStatus.CONFIRMED, fid=None):
    return Fact(
        fact_id=fid or f"F-{name}",
        name=name,
        value=value,
        status=status,
        source=FactSource(kind, "test"),
        confidence=0.99,
    )


def _plan(case_id, version, modules, *, decisions=None, run_number=1):
    decisions = decisions or {}
    plan = FinalClaimPlan(
        claim_plan_id=f"CP-{case_id}-v{version}",
        case_id=case_id,
        analysis_run_id=f"run-{run_number}",
        run_number=run_number,
        version=version,
        inputs_digest="x",
        trust={},
    )
    for i, m in enumerate(modules, start=1):
        sf = (
            ({"condition": "multiple_visits", "fact": "multiple_visits", "value": True,
              "because_of": [{"fact": "left_site"}, {"fact": "returned_same_day"}]},)
            if "ANPR" in m else
            ({"condition": "pofa_postal_late", "fact": "parking_event_date", "value": "2026-07-17"},)
        )
        plan.add_item(ClaimPlanItem(
            item_id=f"I-{m}-v{version}",
            knowledge_id=m,
            module_id=m,
            claim_type="POFA" if "POFA" in m else "ANPR",
            status=SUPPORTED,
            decision=decisions.get(m, SELECTED),
            reason="test support",
            supporting_facts=sf,
            evidence_refs=(),
            relationships=(),
            priority=i,
            topic="test",
        ))
    plan.confirm().lock()
    return plan


class ConsolePayload(unittest.TestCase):
    def test_build_console_includes_pipeline_and_pcn(self):
        case = CaseFile(case_id="C-TRACE-1")
        case.state = CaseState.MANUAL_REVIEW
        case.run_id = 2
        case.facts = {
            "pcn_number": _fact("pcn_number", "88812000363"),
            "vrm": _fact("vrm", "KS58OPW"),
            "parking_event_date": _fact("parking_event_date", "2026-07-17"),
            "notice_issue_date": _fact("notice_issue_date", "2026-07-30"),
            "operator_name": _fact("operator_name", "Euro Car Parks"),
            "alleged_breach": _fact("alleged_breach", "Maximum stay exceeded"),
            "left_site": _fact("left_site", True, kind=SourceKind.CUSTOMER_FREE_TEXT,
                               status=FactStatus.DERIVED),
            "returned_same_day": _fact("returned_same_day", True,
                                       kind=SourceKind.CUSTOMER_FREE_TEXT,
                                       status=FactStatus.DERIVED),
            "multiple_visits": _fact("multiple_visits", True, kind=SourceKind.ANSWER,
                                     status=FactStatus.ANSWERED),
            "keeper_name": _fact("keeper_name", "Secret Person"),
        }
        case.legal_findings = [{
            "family": "POFA_POSTAL_LATE", "status": "VERIFIED",
            "legal_module_id": "KB-POFA-02",
            "calculation_result": {
                "event_date": "2026-07-17", "issue_date": "2026-07-30",
                "deadline": "2026-07-31", "deemed_delivery": "2026-08-04",
                "days_outside": 4,
            },
        }]
        case.claim_plans = [
            _plan("C-TRACE-1", 1, ["KB-POFA-02"],
                  decisions={"KB-POFA-02": VERIFIED_FINDING}, run_number=1),
            _plan("C-TRACE-1", 2, ["KB-POFA-02", "KB-ANPR-01"],
                  decisions={"KB-POFA-02": VERIFIED_FINDING, "KB-ANPR-01": SELECTED},
                  run_number=2),
        ]
        case.audit.append({"event": "narrative_understanding", "at": "2026-07-17T18:03:05Z",
                           "run_id": 2})
        case.audit.append({"event": "claim_plan_locked", "at": "2026-07-17T18:03:08Z",
                           "run_id": 2, "version": 2})

        payload = cons.build_console(case)
        self.assertEqual(payload["schema"], "case_console.v1")
        self.assertEqual(payload["summary"]["case_id"], "C-TRACE-1")
        stages = [p["stage"] for p in payload["pipeline"]]
        self.assertIn("CLAIM_PLAN", stages)
        self.assertIn("GROUND_MERGE", stages)
        pcn = next(f for f in payload["extraction"] if f["name"] == "pcn_number")
        self.assertEqual(pcn["value"], "88812000363")
        kn = next(f for f in payload["facts"] if f["name"] == "keeper_name")
        self.assertEqual(kn["value"], "(redacted)")
        self.assertTrue(payload["narrative"]["derived"])
        self.assertEqual(payload["narrative"]["derived"]["name"], "multiple_visits")
        self.assertIn("left_site", payload["narrative"]["derived"]["derived_from"])
        self.assertIsNone(payload["narrative"]["raw_text"])
        finals = {g["module_id"] for g in payload["grounds"]["final_merged"]}
        self.assertEqual(finals, {"KB-POFA-02", "KB-ANPR-01"})
        self.assertTrue(payload["claim_plan"]["items"])
        self.assertIn("draft_requirement", payload["claim_plan"]["items"][0])
        self.assertIn("ground_sources", payload)
        self.assertTrue(payload["ground_sources"]["source_trace"][0].startswith("GROUND SOURCES"))
        self.assertIn("document_baseline", payload)
        self.assertTrue(payload["document_baseline"]["trace"][0].startswith("DOCUMENT BASELINE"))
        report = cons.copy_report(payload)
        self.assertIn("88812000363", report)
        self.assertIn("GROUND SOURCES", report)
        self.assertIn("MODULE JOURNEY", report)
        self.assertNotIn("Secret Person", report)
        self.assertTrue(payload["module_journey"])
        anpr = next(r for r in payload["module_journey"] if r["module_id"] == "KB-ANPR-01")
        self.assertEqual(anpr["knowledge"]["decision"], "MATCHED")
        self.assertEqual(anpr["case_intelligence"]["decision"], "SELECTED")
        self.assertEqual(anpr["claim_plan"]["decision"], "SUPPORTED")

    def test_enrich_used_by_tolerates_dict_because_of(self):
        case = CaseFile(case_id="C-TRACE-DICT")
        case.state = CaseState.ANALYSED
        case.run_id = 1
        case.facts = {
            "pcn_number": _fact("pcn_number", "1"),
            "left_site": _fact("left_site", True, kind=SourceKind.CUSTOMER_FREE_TEXT),
        }
        plan = FinalClaimPlan(
            claim_plan_id="CP-DICT-v1", case_id="C-TRACE-DICT",
            analysis_run_id="run-1", run_number=1, version=1,
            inputs_digest="x", trust={},
        )
        plan.add_item(ClaimPlanItem(
            item_id="I-ANPR", knowledge_id="KB-ANPR-01", module_id="KB-ANPR-01",
            claim_type="ANPR", status=SUPPORTED, decision=SELECTED,
            reason="test",
            supporting_facts=(
                {
                    "condition": "multiple_visits",
                    "fact": {"fact": "multiple_visits"},
                    "value": True,
                    "because_of": [
                        {"fact": {"name": "left_site"}},
                        {"fact": "returned_same_day"},
                    ],
                },
            ),
            evidence_refs=(), relationships=(), priority=1, topic="test",
        ))
        plan.confirm().lock()
        case.claim_plans = [plan]
        payload = cons.build_console(case)
        self.assertEqual(payload["schema"], "case_console.v1")
        left = next(f for f in payload["facts"] if f["name"] == "left_site")
        self.assertIn("KB-ANPR-01", left["used_by"])

    def test_relationships_tolerate_list_endpoints(self):
        """knowledge_match blocked_by / selected_because can be lists — must not 500."""
        case = CaseFile(case_id="C-TRACE-REL")
        case.state = CaseState.ANALYSED
        case.run_id = 1
        case.facts = {"pcn_number": _fact("pcn_number", "1")}
        case.evidence = {"E1": mock.Mock(evidence_id="E1"), "E2": mock.Mock(evidence_id="E2")}
        case.audit.append({
            "event": "knowledge_match",
            "selected": [
                {"module": "KB-ANPR-01", "selected_because": [["left_site", "returned"]]},
            ],
            "rejected": [
                {"module": "KB-POFA-01", "status": "BLOCKED",
                 "blocked_by": ["missing_date", "missing_issue"], "reason": "gap"},
            ],
        })
        payload = cons.build_console(case)
        rels = payload["relationships"]
        self.assertTrue(any(r.get("type") == "BLOCKS" for r in rels))
        for r in rels:
            self.assertNotIsInstance(r.get("from"), list)
            self.assertNotIsInstance(r.get("to"), list)
        self.assertEqual(payload["draft_context"]["evidence_refs"], ["E1", "E2"])

    def test_ground_integrity_failure_when_prior_ground_vanishes(self):
        case = CaseFile(case_id="C-TRACE-2")
        case.state = CaseState.ANALYSED
        case.run_id = 2
        case.facts = {"pcn_number": _fact("pcn_number", "1")}
        case.claim_plans = [
            _plan("C-TRACE-2", 1, ["KB-POFA-02"],
                  decisions={"KB-POFA-02": VERIFIED_FINDING}, run_number=1),
            _plan("C-TRACE-2", 2, ["KB-ANPR-01"], run_number=2),
        ]
        payload = cons.build_console(case)
        errs = payload["grounds"]["integrity_errors"]
        self.assertTrue(errs)
        self.assertEqual(errs[0]["code"], "GROUND_INTEGRITY_FAILURE")
        self.assertIn("KB-POFA-02", errs[0]["message"])

    def test_placeholder_identity_issue(self):
        case = CaseFile(case_id="C-TRACE-3")
        case.state = CaseState.DRAFTED
        case.facts = {}
        payload = cons.build_console(case)
        self.assertTrue(payload["identity_issues"])
        self.assertEqual(payload["identity_issues"][0]["code"], "UNRESOLVED_PLACEHOLDER")

    def test_compare_runs_flags_removed_ground(self):
        case = CaseFile(case_id="C-TRACE-4")
        case.claim_plans = [
            _plan("C-TRACE-4", 1, ["KB-POFA-02"],
                  decisions={"KB-POFA-02": VERIFIED_FINDING}, run_number=1),
            _plan("C-TRACE-4", 2, ["KB-ANPR-01"], run_number=2),
        ]
        diff = cons.compare_runs(case)
        self.assertIn("KB-POFA-02", diff["grounds"]["removed"])
        self.assertTrue(diff["integrity_errors"])

    def test_expected_rejection_is_not_integrity_failure(self):
        """Missing-fact reject is expected — not GROUND_INTEGRITY_FAILURE."""
        from pcn_appeal.engines.claim_plan_authority import MISSING_FACTS, REJECTED
        case = CaseFile(case_id="C-TRACE-PAY")
        case.state = CaseState.ANALYSED
        case.run_id = 1
        case.facts = {"pcn_number": _fact("pcn_number", "1")}
        case.audit.append({
            "event": "knowledge_match",
            "selected": [],
            "relevant": [{"module": "KB-PAY-01", "missing": ["payment_made"],
                          "reason": "use_when not yet met"}],
            "rejected": [{"module": "KB-PAY-01", "status": "REJECTED",
                          "missing": ["payment_made"],
                          "reason": "use_when not yet met: no confirmed payment_made"}],
        })
        case.audit.append({
            "event": "case_analysis",
            "kept": [], "proposed": ["KB-PAY-01"],
            "not_supported": [{"module_id": "KB-PAY-01", "reason": "payment_made missing",
                               "missing": ["payment_made"]}],
        })
        plan = FinalClaimPlan(
            claim_plan_id="CP-PAY-v1", case_id="C-TRACE-PAY",
            analysis_run_id="run-1", run_number=1, version=1,
            inputs_digest="x", trust={},
        )
        plan.add_item(ClaimPlanItem(
            item_id="I-PAY", knowledge_id="KB-PAY-01", module_id="KB-PAY-01",
            claim_type="PAYMENT", status=REJECTED, decision=MISSING_FACTS,
            reason="not established: payment_made",
            supporting_facts=(), evidence_refs=(), relationships=(),
            priority=None, topic="payment",
        ))
        plan.confirm().lock()
        case.claim_plans = [plan]
        payload = cons.build_console(case)
        self.assertFalse(payload["grounds"]["integrity_errors"])
        row = next(r for r in payload["module_journey"] if r["module_id"] == "KB-PAY-01")
        self.assertTrue(row["expected_rejection"])
        self.assertEqual(row["integrity"], "PASS")
        self.assertIn("payment_made", row["missing_facts"])
        val = {c["rule"]: c["status"] for c in payload["module_trace_checks"]}
        self.assertEqual(val.get("VAL-EXPECTED-REJECTION"), "PASS")


class ConsoleApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        self.case = CaseFile(case_id="C-API-TRACE")
        self.case.state = CaseState.ANALYSED
        self.case.facts = {"pcn_number": _fact("pcn_number", "88812000363")}
        api.CASES["C-API-TRACE"] = {"case": self.case, "output": None, "pipe": None}

    def tearDown(self):
        api.CASES.pop("C-API-TRACE", None)

    def test_console_requires_admin_when_token_configured(self):
        with mock.patch.dict(os.environ, {"ADMIN_TRACE_TOKEN": "secret"}):
            self.assertEqual(
                self.client.get("/admin/cases/C-API-TRACE/console").status_code, 401)
            ok = self.client.get(
                "/admin/cases/C-API-TRACE/console",
                headers={"X-Admin-Token": "secret"},
            )
            self.assertEqual(ok.status_code, 200)
            body = ok.json()
            self.assertEqual(body["summary"]["case_id"], "C-API-TRACE")
            self.assertTrue(body["pipeline"])

    def test_console_works_without_output(self):
        with mock.patch.dict(os.environ, {"ADMIN_TRACE_TOKEN": "", "ADMIN_TOKEN": "",
                                         "APP_ENV": "development"}):
            res = self.client.get("/admin/cases/C-API-TRACE/console")
            self.assertEqual(res.status_code, 200)

    def test_report_txt(self):
        with mock.patch.dict(os.environ, {"ADMIN_TRACE_TOKEN": "", "ADMIN_TOKEN": ""}):
            res = self.client.get("/admin/cases/C-API-TRACE/console/report.txt")
            self.assertEqual(res.status_code, 200)
            self.assertIn("C-API-TRACE", res.text)


if __name__ == "__main__":
    unittest.main()
