"""Outcome-boundary regressions: MANUAL_REVIEW must not always mean no merit.

Maps the live PCN 88812207965 failure mode (ground_recovery wipe → empty pack →
VAL-SUBSTANCE) to PROCESSING_ERROR, and proves genuine empty analysis maps to
NO_SUPPORTED_GROUNDS. Does not invent grounds for that PCN.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pcn_appeal.engines.outcome import (
    OUTCOME_NEEDS_FACTS,
    OUTCOME_NO_SUPPORTED_GROUNDS,
    OUTCOME_PROCESSING_ERROR,
    classify_hold,
)
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile,
    CaseState,
    Draft,
    Fact,
    FactSource,
    FactStatus,
    RetrievalPack,
    SourceKind,
    ValidationIssue,
    ValidationResult,
)
from pcn_appeal.orchestrator import AppealOutput, AppealPipeline, _with_outcome


def _empty_pack(**kwargs) -> RetrievalPack:
    base = dict(
        primary_route=None, secondary_routes=[], module_ids=[],
        verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
        prohibited_claims=[], code_version=None, pofa_route="NOT_APPLICABLE",
        pofa_findings=[], driver_status="UNKNOWN", jurisdiction="UNKNOWN",
        context_chunks=[], lease_clauses=[], trace=[],
    )
    base.update(kwargs)
    return RetrievalPack(**base)


class ClassifyHoldBoundary(unittest.TestCase):
    def test_ground_recovery_wipe_is_processing_error_not_merits(self):
        """Live 3cb620c9 path: was=[KB-LAND-01] now=[] → not 'nothing stands up'."""
        case = CaseFile(case_id="t-wipe")
        case.state = CaseState.MANUAL_REVIEW
        case.audit = [{
            "event": "ground_recovery",
            "was": ["KB-LAND-01"],
            "now": [],
            "round": 2,
        }]
        pack = _empty_pack()
        val = ValidationResult(False, [
            ValidationIssue("VAL-SUBSTANCE", "BLOCK", "no substantive grounds"),
        ])
        out = classify_hold(case, pack, val)
        self.assertEqual(out["outcome"], OUTCOME_PROCESSING_ERROR)
        self.assertNotIn("stand up", (out["outcome_message"] or "").lower())
        self.assertTrue(out["can_continue"])

    def test_empty_pack_no_ground_is_processing_error(self):
        case = CaseFile(case_id="t-empty")
        case.state = CaseState.MANUAL_REVIEW
        case.audit = [
            {"event": "no_ground", "reason": "no modules", "attempt": 1},
            {"event": "no_leading_ground_drafting_simple", "module_ids": []},
        ]
        out = classify_hold(case, _empty_pack(), ValidationResult(False, [
            ValidationIssue("VAL-SUBSTANCE", "BLOCK", "shell"),
        ]))
        self.assertEqual(out["outcome"], OUTCOME_PROCESSING_ERROR)

    def test_completed_analysis_no_grounds_is_truthful(self):
        case = CaseFile(case_id="t-none")
        case.state = CaseState.MANUAL_REVIEW
        case.audit = [{
            "event": "analysis_complete_no_supported_grounds",
            "module_ids": [],
        }]
        out = classify_hold(case, _empty_pack(), ValidationResult(False, []))
        self.assertEqual(out["outcome"], OUTCOME_NO_SUPPORTED_GROUNDS)

    def test_draft_error_is_processing_error(self):
        case = CaseFile(case_id="t-draft")
        case.state = CaseState.MANUAL_REVIEW
        case.audit = [{"event": "draft_error", "error": "timeout"}]
        out = classify_hold(
            case,
            _empty_pack(module_ids=["KB-LAND-01"]),
            ValidationResult(False, [
                ValidationIssue("VAL-DRAFT", "BLOCK", "AI drafting failed"),
            ]),
        )
        self.assertEqual(out["outcome"], OUTCOME_PROCESSING_ERROR)

    def test_pcn_conflict_maps_to_needs_facts(self):
        case = CaseFile(case_id="t-pcn")
        case.state = CaseState.MANUAL_REVIEW
        case.put(Fact(
            "F-pcn", "pcn_conflict", True, FactStatus.DERIVED,
            FactSource(SourceKind.CALCULATION, "test"),
        ))
        out = classify_hold(case, _empty_pack(), ValidationResult(False, [
            ValidationIssue("VAL-CONFLICT", "BLOCK", "conflicting PCN"),
        ]))
        self.assertEqual(out["outcome"], OUTCOME_NEEDS_FACTS)


class GroundRecoveryPreserve(unittest.TestCase):
    def test_generate_does_not_wipe_selection_to_empty(self):
        """Re-analysis that returns [] must not clear a prior finalized selection."""
        kg = KnowledgeGraph()
        case = CaseFile(case_id="t-preserve")
        case.state = CaseState.ANALYSED
        case.analysis_module_ids = ["KB-LAND-01"]
        case.raw_answers["narrative"] = "nothing"

        pipe = AppealPipeline(kg, FakeLLM(), FakeLLM())
        empty = _empty_pack()

        # Force the recovery loop: first pack has no leading grounds.
        pipe.reasoning.leading_grounds = lambda ids: False  # type: ignore[method-assign]
        pipe.reasoning.analyse = lambda *a, **k: empty  # type: ignore[method-assign]

        def wipe(_c, _narrative):
            _c.analysis_module_ids = []
            return []

        with patch.object(pipe, "_reanalyse", side_effect=wipe):
            pipe._analyse_until_a_ground_can_lead(case)

        self.assertEqual(case.analysis_module_ids, ["KB-LAND-01"])
        events = [a.get("event") for a in case.audit]
        self.assertIn("ground_recovery_preserved", events)


class ApiOutcomePayload(unittest.TestCase):
    def test_held_payload_exposes_outcome_not_grounds(self):
        from pcn_appeal.api import _outcome_fields

        case = CaseFile(case_id="api-hold")
        case.state = CaseState.MANUAL_REVIEW
        case.audit = [{
            "event": "ground_recovery",
            "was": ["KB-LAND-01"],
            "now": [],
        }]
        pack = _empty_pack()
        val = ValidationResult(False, [
            ValidationIssue("VAL-SUBSTANCE", "BLOCK", "shell"),
        ])
        out = _with_outcome(
            AppealOutput(case.state, None, pack, Draft(case.case_id, []), val, []),
            case,
        )
        fields = _outcome_fields(out)
        self.assertEqual(fields.get("outcome"), OUTCOME_PROCESSING_ERROR)
        self.assertNotIn("grounds", fields)
        self.assertTrue(fields.get("can_continue"))
        self.assertIn("processing", (fields.get("outcome_message") or "").lower())
        # Customer outcome event recorded for audit/admin trace.
        self.assertTrue(any(a.get("event") == "customer_outcome" for a in case.audit))


class GenerateShortCircuit(unittest.TestCase):
    def test_empty_selection_short_circuits_to_no_supported_grounds(self):
        kg = KnowledgeGraph()
        case = CaseFile(case_id="t-short")
        case.state = CaseState.ANALYSED
        case.analysis_module_ids = []
        case.document_classes = {"E1": "NTK"}
        # A completed analysis that proposes nothing. This test used to pass
        # the KnowledgeGraph as the LLM, so analysis *raised* and the failure
        # was reported as "no supported grounds" (client issue 41).
        llm = FakeLLM({"case_analysis": [{"grounds": [], "questions": []}] * 8})
        pipe = AppealPipeline(llm, FakeLLM(), kg=kg)
        empty = _empty_pack()
        pipe.reasoning.analyse = lambda *a, **k: empty  # type: ignore[method-assign]
        pipe.reasoning.leading_grounds = lambda ids: False  # type: ignore[method-assign]

        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.NO_SUPPORTED_GROUNDS)
        self.assertEqual(out.outcome, OUTCOME_NO_SUPPORTED_GROUNDS)
        self.assertEqual(case.state, CaseState.NO_SUPPORTED_GROUNDS)
        events = [a.get("event") for a in case.audit]
        self.assertIn("analysis_complete_no_supported_grounds", events)
        self.assertNotIn("no_ground", events)

    def test_failed_analysis_short_circuits_to_processing_error(self):
        kg = KnowledgeGraph()
        case = CaseFile(case_id="t-short-fail")
        case.state = CaseState.ANALYSED
        case.analysis_module_ids = []
        case.document_classes = {"E1": "NTK"}
        pipe = AppealPipeline(FakeLLM(), FakeLLM(), kg=kg)    # case_analysis raises
        empty = _empty_pack()
        pipe.reasoning.analyse = lambda *a, **k: empty  # type: ignore[method-assign]
        pipe.reasoning.leading_grounds = lambda ids: False  # type: ignore[method-assign]

        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertEqual(out.outcome, OUTCOME_PROCESSING_ERROR)
        events = [a.get("event") for a in case.audit]
        self.assertNotIn("analysis_complete_no_supported_grounds", events)


if __name__ == "__main__":
    unittest.main()
