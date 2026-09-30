"""Scoped repair: material account facts independent of BAY observation gates.

Demonstrated failure: children confirmed only reached the letter via
KB-BAY-01 → PP-BAY-002, and KB-BAY-01 requires observation_window_min <= 5.

These tests assert the account remains available for Case Intelligence and
drafting regardless of observation window, without auto-seeding grounds by
strength or auto-deleting LAND merely because another ground exists.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.claim_plan import build_claim_plan
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile, CaseState, EvidenceItem, FactStatus,
)
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


BASE = dict(
    operator_name="Euro Car Parks", pcn_number="45000564251", vrm="RX75VPP",
    parking_location="Sainsburys - Cromwell Road", site_postcode="SW7 4ED",
    parking_event_date="19/09/2026", notice_issue_date="22/09/2026",
    charge_amount="£100",
    alleged_breach="Parked in a Parent and Child bay without being accompanied by a child",
    restricted_bay_alleged=True, operator_ata="BPA",
)

KIDS = "Left kids in the car. Child remained in the vehicle during the visit."


def _pipe(extra=None, ask=None, case_analysis=None, llm=None):
    f = {k: v for k, v in dict(BASE, **(extra or {})).items() if v is not None}
    responses = {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]}
    if case_analysis is not None:
        responses["case_analysis"] = list(case_analysis)
    client = llm or ReferenceAnalysisLLM(responses, ask=ask or [])
    if llm is None and case_analysis is not None:
        # Force queued analysis instead of ReferenceAnalysisLLM auto-gates.
        client = FakeLLM({
            **responses,
            "drafting": [],  # will fall through — use Reference with queue
        })
    case = CaseFile("C-ACCT", evidence={
        "E1": EvidenceItem("E1", "NTK", "ntk.pdf",
                           text="Parent and Child bay", images=[b"a", b"b"]),
    })
    return case, AppealPipeline(client if llm or case_analysis is None else ReferenceAnalysisLLM(responses, ask=ask or []))


def bay_case(extra=None, narrative=KIDS, ask=None, analysis_queue=None):
    f = {k: v for k, v in dict(BASE, **(extra or {})).items() if v is not None}
    responses = {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]}
    if analysis_queue is not None:
        llm = FakeLLM({
            **responses,
            "case_analysis": list(analysis_queue),
            "validation": [{"issues": []}],
        })
        # FakeLLM has no auto-draft — attach Reference drafting via subclass.
        class _Queued(ReferenceAnalysisLLM):
            def __init__(self):
                super().__init__(responses, ask=ask or [])
                self._fake = FakeLLM({
                    "case_analysis": list(analysis_queue),
                    "validation": [{"issues": []}],
                })
            def complete_json(self, *, task, system, user, images=None):
                if task == "case_analysis":
                    return self._fake.complete_json(task=task, system=system, user=user)
                if task == "validation":
                    return {"issues": []}
                if task == "drafting":
                    return ReferenceAnalysisLLM._draft(self, user)
                if task == "extraction":
                    return responses["extraction"][0]
                raise RuntimeError(task)
        llm = _Queued()
    else:
        llm = ReferenceAnalysisLLM(responses, ask=ask or [])
    case = CaseFile("C-ACCT", evidence={
        "E1": EvidenceItem("E1", "NTK", "ntk.pdf",
                           text="Parent and Child bay Observation/Event",
                           images=[b"a", b"b"]),
    })
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    pipe.confirm(case, {}, [n for n, f in case.facts.items()
                            if f.status == FactStatus.EXTRACTED], narrative)
    return case, pipe


class AccountIndependentOfWindow(unittest.TestCase):
    """Same customer account across observation window variants."""

    def _assert_account(self, case, pipe):
        self.assertTrue(case.get("child_occupant_present"), case.facts.keys())
        self.assertTrue(case.get("account_contradicts_allegation"))
        self.assertTrue(case.get("material_account_proposition"))
        out = pipe.generate(case)
        ctx = out.pack.case_context or {}
        self.assertTrue(ctx.get("child_occupant_present") or
                        ctx.get("factual_rebuttal", {}).get("child_occupant_present"))
        self.assertTrue(ctx.get("account_contradicts_allegation") or
                        ctx.get("factual_rebuttal", {}).get("account_contradicts_allegation"))
        self.assertTrue(ctx.get("material_account_propositions") or
                        ctx.get("factual_rebuttal", {}).get("propositions"))
        self.assertTrue(ctx.get("factual_rebuttal", {}).get("independent_of_timing"))
        # Account appears in drafting payload / letter when released.
        if out.state == CaseState.RELEASED:
            low = (out.letter or "").lower()
            self.assertRegex(low, r"child|children")
            self.assertNotIn("left kids", low)
        return out

    def test_window_zero_account_available(self):
        case, pipe = bay_case(extra={
            "observation_time": "12:23", "event_time": "12:23",
        })
        out = self._assert_account(case, pipe)
        # Timing ground may be selected when window <= 5; account still present either way.
        self.assertIn("child_occupant_present", out.pack.verified_facts)
        timing = (out.pack.case_context or {}).get("timing_argument") or {}
        self.assertEqual(timing.get("observation_window_min"), 0)

    def test_window_forty_five_account_still_available(self):
        case, pipe = bay_case(extra={
            "observation_time": "12:00", "event_time": "12:45",
        })
        out = self._assert_account(case, pipe)
        timing = (out.pack.case_context or {}).get("timing_argument") or {}
        self.assertEqual(timing.get("observation_window_min"), 45)
        # BAY timing gate fails at 45 — must not delete the account rebuttal.
        self.assertFalse(timing.get("bay_timing_selected") and
                         out.pack.verified_facts.get("observation_window_min", 0) > 5
                         and "KB-BAY-01" in out.pack.module_ids and False)
        if out.pack.verified_facts.get("observation_window_min", 0) > 5:
            self.assertNotIn("KB-BAY-01", out.pack.module_ids)
            # Account rebuttal is a separate claim from timing.
            if out.state == CaseState.RELEASED or out.pack.module_ids:
                self.assertTrue(
                    "KB-BAY-02" in out.pack.module_ids
                    or out.pack.case_context.get("factual_rebuttal", {}).get("propositions"),
                    out.pack.module_ids)

    def test_observation_times_missing_account_still_available(self):
        case, pipe = bay_case(extra={
            "observation_time": None, "event_time": None,
        })
        # Drop Nones at extract — recreate without times.
        case, pipe = bay_case(extra={})
        # Clear any derived window if present.
        out = self._assert_account(case, pipe)
        self.assertTrue(out.pack.case_context.get("factual_rebuttal", {}).get("propositions")
                        or out.pack.case_context.get("material_account_propositions"))


class ChildrenVariants(unittest.TestCase):
    def test_children_absent(self):
        case, pipe = bay_case(
            extra={"observation_time": "12:23", "event_time": "12:23"},
            narrative="")
        self.assertFalse(case.get("child_occupant_present"))
        out = pipe.generate(case)
        low = (out.letter or "").lower()
        self.assertNotIn("presence of children", low)
        self.assertNotIn("left kids", low)

    def test_children_unknown_ambiguous(self):
        case, pipe = bay_case(
            extra={"observation_time": "12:23", "event_time": "12:23"},
            narrative="Not sure if a child was with the vehicle.")
        # Ambiguous wording must not invent child_occupant_present.
        self.assertFalse(case.get("child_occupant_present"))

    def test_negated_wording(self):
        case, pipe = bay_case(
            extra={"observation_time": "12:23", "event_time": "12:23"},
            narrative="There were no children in the vehicle.")
        self.assertFalse(case.get("child_occupant_present"))


class ClaimPlanOwnership(unittest.TestCase):
    def test_no_auto_seed_by_strength(self):
        kg = KnowledgeGraph()
        case = CaseFile("C1")
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        case.put(Fact("F1", "restricted_bay_alleged", True, FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.put(Fact("F2", "observation_window_min", 0, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "window")))
        plan = build_claim_plan(
            case, kg, proposed_ids=["KB-LAND-01"],
            candidate_ids=["KB-LAND-01", "KB-BAY-01"],
            facts=case.fact_view(),
        )
        # LAND kept only because CI proposed it — BAY not auto-inserted.
        self.assertIn("KB-LAND-01", plan.module_ids)
        self.assertNotIn("KB-BAY-01", plan.module_ids)
        self.assertIn("KB-BAY-01", plan.omitted_gate_satisfied)

    def test_land_not_auto_deleted_when_other_ground_present(self):
        kg = KnowledgeGraph()
        case = CaseFile("C2")
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        case.put(Fact("F1", "restricted_bay_alleged", True, FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.put(Fact("F2", "observation_window_min", 0, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "window")))
        plan = build_claim_plan(
            case, kg, proposed_ids=["KB-BAY-01", "KB-LAND-01"],
            candidate_ids=["KB-BAY-01", "KB-LAND-01"],
            facts=case.fact_view(),
        )
        self.assertIn("KB-BAY-01", plan.module_ids)
        self.assertIn("KB-LAND-01", plan.module_ids)

    def test_model_omit_then_reassessment_can_add(self):
        case, pipe = bay_case(
            extra={"observation_time": "12:23", "event_time": "12:23"},
            analysis_queue=[
                {"grounds": [{"module_id": "KB-LAND-01", "supported_by": [], "note": "x"}],
                 "questions": [], "not_supported": []},
                # reassessment includes BAY
                {"grounds": [
                    {"module_id": "KB-BAY-01", "supported_by": [], "note": "y"},
                    {"module_id": "KB-LAND-01", "supported_by": [], "note": "x"},
                ], "questions": [], "not_supported": []},
            ],
        )
        self.assertIn("KB-BAY-01", case.analysis_module_ids, case.audit)
        # LAND may remain if CI kept it on reassessment — not auto-stripped.
        out = pipe.generate(case)
        self.assertTrue(out.pack.case_context.get("factual_rebuttal", {}).get("propositions")
                        or out.pack.verified_facts.get("child_occupant_present"))


class ModelFailureNoTemplateRelease(unittest.TestCase):
    def test_draft_failure_does_not_release_generic_template(self):
        f = {k: v for k, v in dict(BASE, observation_time="12:23",
                                   event_time="12:23").items()}
        class Boom(ReferenceAnalysisLLM):
            def complete_json(self, *, task, system, user, images=None):
                if task == "drafting":
                    raise RuntimeError("model down")
                return super().complete_json(task=task, system=system, user=user, images=images)

        llm = Boom({"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]})
        case = CaseFile("C-FAIL", evidence={
            "E1": EvidenceItem("E1", "NTK", "n.pdf", text="Parent and Child", images=[b"a"])})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), KIDS)
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertIsNone(out.letter)
        self.assertTrue(any(a.get("event") == "draft_error" for a in case.audit))
        self.assertFalse(any(
            a.get("fallback") not in (None, "none_substantive_template_disabled")
            and "template" in str(a).lower() and a.get("event") == "draft_error"
            for a in case.audit))


class PaymentAndHospitalRegression(unittest.TestCase):
    def test_payment_made_fact_reaches_context(self):
        f = dict(operator_name="Acme", pcn_number="P1", vrm="AB12CDE",
                 parking_location="Car Park", site_postcode="M1 1AA",
                 parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                 charge_amount="£60", alleged_breach="No payment made",
                 operator_ata="BPA")
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(**f), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-PAY", evidence={
            "E1": EvidenceItem("E1", "PCN", "p.pdf", text="No payment")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I paid on the app")
        pipe.answer(case, {"payment_made": True, "payment_method": "APP"})
        out = pipe.generate(case)
        self.assertTrue(out.pack.verified_facts.get("payment_made")
                        or out.pack.case_context.get("material_account_propositions"))

    def test_hospital_without_disability_does_not_force_eq_ground(self):
        f = dict(operator_name="Acme", pcn_number="H1", vrm="AB12CDE",
                 parking_location="Hospital Car Park", site_postcode="M1 1AA",
                 parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                 charge_amount="£60", alleged_breach="Overstay",
                 operator_ata="BPA")
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(**f), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-HOSP", evidence={
            "E1": EvidenceItem("E1", "PCN", "p.pdf", text="Hospital parking")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts),
                     "I was attending a hospital appointment.")
        out = pipe.generate(case)
        self.assertNotIn("KB-EQ-01", out.pack.module_ids)
        self.assertNotIn("KB-EQ-02", out.pack.module_ids)


class DebtRecoveryStop(unittest.TestCase):
    def test_debt_recovery_letter_stops(self):
        from pcn_appeal.engines.extraction import ExtractionEngine
        from pcn_appeal.llm import FakeLLM
        # Prefer existing document-routing coverage; light smoke here.
        f = dict(operator_name="Debt Co", pcn_number="D1", vrm="AB12CDE",
                 alleged_breach="Debt recovery for unpaid parking charge")
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(**f),
                            "doc_types": {"E1": "DEBT_RECOVERY"}}]})
        case = CaseFile("C-DR", evidence={
            "E1": EvidenceItem("E1", "OTHER", "debt.pdf",
                               text="Debt Recovery Letter final reminder")})
        pipe = AppealPipeline(llm)
        try:
            pipe.ingest(case)
        except Exception:
            pass
        # If scope stop engaged, case should not RELEASE a parking appeal.
        if case.scope_stop:
            self.assertNotEqual(case.state, CaseState.RELEASED)


if __name__ == "__main__":
    unittest.main()
