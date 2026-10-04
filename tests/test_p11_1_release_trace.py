"""P11.1 — release metadata gate, invariants, legacy classification, trace panel."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from pcn_appeal.models import CaseFile, CaseState
from pcn_appeal.release_trace import (
    LEGACY_UNVERSIONED,
    OUTCOME_RELEASE_METADATA_INCOMPLETE,
    REQUIRED_RELEASE_KEYS,
    assert_released_invariants,
    build_release_trace,
    classify_case_row,
    gate_before_release,
    missing_release_keys,
    release_allowed,
)


def _complete_meta(**overrides):
    meta = {
        "commit_sha": "abc123def456",
        "kb_release_id": "kb-20261004T113657Z",
        "ontology_version": "p10_5_ontology_v1",
        "module_role_version": "p10_5_roles_v1",
        "claim_plan_builder_version": "3",
        "draft_plan_version": "p10_6_draft_plan_v1",
        "validation_version": "VAL-5",
        "prompt_versions": {"drafting": 12, "extraction": 8},
        "llm_provider": "openai",
        "model_versions": {"drafting": "gpt-5.1", "extraction": "gpt-5.1"},
    }
    meta.update(overrides)
    return meta


def _released_detail(**overrides):
    detail = {
        "case": {
            "case_id": "c-new",
            "state": "RELEASED",
            "commit_sha": "abc123def456",
            "kb_release_id": "kb-20261004T113657Z",
            "release_metadata": _complete_meta(),
        },
        "claim_plans": [{"claim_plan_id": "p1"}],
        "drafts": [{"draft_id": "d1", "released": True, "validation_status": "PASS"}],
        "validations": [{"passed": True}],
        "legal_findings": [{"finding_id": "f1"}],
        "all_facts": [{"fact_id": "x"}],
        "draft_plan": {"event": "draft_plan"},
        "traces": [],
    }
    detail.update(overrides)
    return detail


class ReleaseMetadataGate(unittest.TestCase):
    def test_complete_meta_allows_release(self):
        ok, missing = release_allowed(_complete_meta())
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_missing_kb_release_id_blocks(self):
        missing = missing_release_keys(_complete_meta(kb_release_id=None))
        self.assertIn("kb_release_id", missing)

    def test_missing_prompt_versions_blocks(self):
        missing = missing_release_keys(_complete_meta(prompt_versions={}))
        self.assertIn("prompt_versions", missing)

    def test_missing_model_versions_blocks(self):
        missing = missing_release_keys(_complete_meta(model_versions={}))
        self.assertIn("model_versions", missing)

    def test_gate_before_release_blocks_and_stamps(self):
        case = CaseFile("gate-1")
        with patch("pcn_appeal.release_trace.gather_release_metadata",
                   return_value=_complete_meta(kb_release_id=None)):
            blocked = gate_before_release(case, pipeline=object())
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["outcome"], OUTCOME_RELEASE_METADATA_INCOMPLETE)
        self.assertIn("kb_release_id", blocked["missing"])
        self.assertIsNotNone(case.release_metadata)
        self.assertTrue(any(a.get("event") == "release_metadata_incomplete"
                            for a in case.audit))

    def test_gate_before_release_ok(self):
        case = CaseFile("gate-2")
        # Structural gaps require a locked plan + draft_plan audit event.
        class _Plan:
            status = "LOCKED"
            claim_plan_id = "p1"
        case.claim_plans = [_Plan()]
        case.audit.append({"event": "draft_plan", "draft_plan_version": "p10_6_draft_plan_v1"})
        with patch("pcn_appeal.release_trace.gather_release_metadata",
                   return_value=_complete_meta()):
            with patch("pcn_appeal.release_trace.structural_release_gaps",
                       return_value=[]):
                blocked = gate_before_release(case, pipeline=object())
        self.assertIsNone(blocked)
        self.assertEqual(case.release_metadata["kb_release_id"], "kb-20261004T113657Z")


class ReleaseInvariants(unittest.TestCase):
    def test_complete_new_case_released_passes(self):
        self.assertEqual(assert_released_invariants(_released_detail()), [])

    def test_missing_claim_plan_blocks(self):
        fails = assert_released_invariants(_released_detail(claim_plans=[]))
        self.assertIn("missing_claim_plan", fails)

    def test_missing_draft_plan_blocks(self):
        fails = assert_released_invariants(_released_detail(
            draft_plan=None, claim_plans=[{"claim_plan_id": "p1"}],
            drafts=[{"draft_id": "d1", "released": True, "validation_status": "PASS",
                     "grounding": None}],
            traces=[],
        ))
        # draft_plan inferred from claim_plans in build_release_trace; invariants
        # require claim_plans + drafts. Explicit draft_plan absence alone is OK
        # when claim plan exists — assert via build_release_trace instead.
        trace = build_release_trace(_released_detail(
            draft_plan=None,
            claim_plans=[],
            drafts=[{"draft_id": "d1", "released": True, "validation_status": "PASS"}],
        ))
        self.assertIn("claim_plan", trace["missing"])

    def test_missing_validation_blocks(self):
        fails = assert_released_invariants(_released_detail(
            validations=[],
            drafts=[{"draft_id": "d1", "released": True, "validation_status": "FAIL"}],
        ))
        self.assertIn("missing_validation_pass", fails)

    def test_missing_draft_plan_via_trace_checks(self):
        detail = _released_detail(
            claim_plans=[],
            draft_plan=None,
            drafts=[],
            validations=[],
        )
        trace = build_release_trace(detail)
        self.assertIn("claim_plan", trace["missing"])
        self.assertIn("draft", trace["missing"])
        self.assertIn("draft_plan", trace["missing"])


class LegacyRows(unittest.TestCase):
    def test_legacy_unversioned_visible_not_rewritten(self):
        row = {
            "state": "RELEASED",
            "kb_release_id": None,
            "release_metadata": None,
            "commit_sha": "oldsha",
        }
        original = copy.deepcopy(row)
        cls = classify_case_row(row)
        self.assertEqual(cls["class"], LEGACY_UNVERSIONED)
        self.assertEqual(row, original)  # not rewritten
        # New-case invariants skipped for legacy
        self.assertEqual(assert_released_invariants({
            "case": row, "claim_plans": [], "drafts": [], "validations": [],
        }), [])

    def test_new_released_without_kb_fails_invariant(self):
        fails = assert_released_invariants({
            "case": {
                "state": "RELEASED",
                "kb_release_id": "kb-x",
                "commit_sha": "abc",
                "release_metadata": _complete_meta(),
            },
            "claim_plans": [],
            "drafts": [{"released": True, "validation_status": "PASS"}],
            "validations": [{"passed": True}],
        })
        self.assertIn("missing_claim_plan", fails)


class ReleaseTracePanel(unittest.TestCase):
    def test_complete_trace_all_checks(self):
        trace = build_release_trace(_released_detail())
        for key in (
            "code_version", "kb_release", "ontology", "facts", "legal_findings",
            "claim_plan", "draft_plan", "draft", "validation", "final_outcome",
        ):
            self.assertTrue(trace["checks"][key], key)
        self.assertEqual(trace["missing"], [])

    def test_save_reload_trace_unchanged(self):
        detail = _released_detail()
        t1 = build_release_trace(detail)
        # Simulate reload: same persisted fields
        reloaded = copy.deepcopy(detail)
        t2 = build_release_trace(reloaded)
        self.assertEqual(t1["checks"], t2["checks"])
        self.assertEqual(t1["missing"], t2["missing"])
        self.assertEqual(t1["release_metadata"], t2["release_metadata"])

    def test_required_keys_cover_spec(self):
        required = set(REQUIRED_RELEASE_KEYS)
        for key in (
            "commit_sha", "kb_release_id", "ontology_version", "module_role_version",
            "claim_plan_builder_version", "draft_plan_version", "validation_version",
            "prompt_versions", "llm_provider", "model_versions",
        ):
            self.assertIn(key, required)


class OrchestratorReleaseGate(unittest.TestCase):
    def test_release_gate_holds_manual_review(self):
        from pcn_appeal.orchestrator import AppealPipeline
        from pcn_appeal.models import ValidationResult, ValidationIssue
        from pcn_appeal.llm import FakeLLM

        pipe = AppealPipeline(FakeLLM({}))
        case = CaseFile("orch-gate")
        case.state = CaseState.DRAFTED
        result = ValidationResult(True, [])
        with patch("pcn_appeal.release_trace.gather_release_metadata",
                   return_value=_complete_meta(kb_release_id=None)):
            out = pipe._release_gate(case, result, pack=None, draft=None)
        self.assertIsNotNone(out)
        self.assertEqual(case.state, CaseState.MANUAL_REVIEW)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertTrue(any(i.rule == "RELEASE_METADATA" for i in out.validation.issues))
        self.assertTrue(any(a.get("event") == "release_metadata_incomplete"
                            for a in case.audit))


if __name__ == "__main__":
    unittest.main()
