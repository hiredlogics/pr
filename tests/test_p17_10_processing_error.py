"""P17.10 — the generic "preparing your appeal" failure and its diagnostics.

Three defects are covered, each generic rather than case-specific:

1. `ClaimPlanAuthority.build` referenced an undefined `bundle_dict`, so every
   case that reached claim-plan building raised NameError and surfaced as
   PROCESSING_ERROR.
2. `attach_semantic_state` sliced the serialized state at a byte budget. A
   rich case produced invalid JSON, and every reader's `json.loads` sits in a
   `try/except` returning None - so the whole semantic state (the material
   meaning with it) was discarded silently.
3. A crash in `generate` escaped as a bare 500, recording no layer, class or
   revision, leaving the next failure undiagnosable.
"""
from __future__ import annotations

import json
import unittest

from pcn_appeal.models import CaseFile
from pcn_appeal.semantics.state import (
    ATOMS_BUDGET, STATE_BUDGET, SemanticCaseState, attach_semantic_state,
)


def _atom(i: int) -> dict:
    return {"atom_id": f"A{i}", "category": "unmapped_reason",
            "proposition": "The customer describes a material circumstance " * 4,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": "x" * 300, "source_excerpt": "y" * 300,
            "confidence": 0.8, "mapped_to_ontology": False,
            "material_to": ["substantive_rebuttal", "timeline"]}


def _event(i: int) -> dict:
    return {"event_id": f"E{i}", "event_type": "OTHER", "kind": "other",
            "description": "d" * 400, "proposition": "p" * 400,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": "s" * 300, "confidence": 0.7,
            "mapped_to_ontology": False}


def _state(n: int) -> SemanticCaseState:
    return SemanticCaseState(
        narrative_atoms=[_atom(i) for i in range(n)],
        events=[_event(i) for i in range(n)],
        timeline=[_event(i) for i in range(n)],
        provenance=[{"note": "v" * 300} for _ in range(n)],
        revision=1, ontology_version="1.0",
    )


class SemanticStateIsAlwaysValidJson(unittest.TestCase):
    """A byte-sliced blob is unparseable, so the state vanishes silently."""

    def test_every_size_round_trips(self):
        for n in (0, 1, 3, 10, 20, 40, 80):
            case = CaseFile(f"T{n}")
            attach_semantic_state(case, _state(n))
            blob = case.raw_answers["_semantic_case_state"]
            atoms = case.raw_answers["_semantic_narrative_atoms"]
            self.assertLessEqual(len(blob), STATE_BUDGET, n)
            self.assertLessEqual(len(atoms), ATOMS_BUDGET, n)
            # The real assertion: both parse. Slicing JSON fails here.
            self.assertIsInstance(json.loads(blob), dict, n)
            self.assertIsInstance(json.loads(atoms), list, n)

    def test_material_meaning_survives_a_large_case(self):
        case = CaseFile("big")
        attach_semantic_state(case, _state(40))
        state = json.loads(case.raw_answers["_semantic_case_state"])
        # Neither material channel may be emptied while context channels remain.
        self.assertTrue(state["narrative_atoms"], state.keys())
        self.assertTrue(state["events"], state.keys())

    def test_shedding_is_recorded_not_silent(self):
        case = CaseFile("shed")
        attach_semantic_state(case, _state(40))
        entry = case.audit[-1]
        self.assertEqual(entry["event"], "semantic_case_state")
        self.assertTrue(entry.get("truncated"), entry)

    def test_a_small_case_is_not_truncated_at_all(self):
        case = CaseFile("small")
        attach_semantic_state(case, _state(2))
        self.assertNotIn("truncated", case.audit[-1])
        state = json.loads(case.raw_answers["_semantic_case_state"])
        self.assertEqual(len(state["narrative_atoms"]), 2)
        self.assertEqual(len(state["events"]), 2)


class ClaimPlanBuildsItsSupportBundle(unittest.TestCase):
    """A case that reaches claim-plan building must not crash.

    Driven through the real pipeline: the NameError fired for every case that
    selected a module, whatever the semantic payload, so any released case
    proves it is gone.
    """

    def test_a_case_reaches_a_decision_without_crashing(self):
        import sys
        sys.path.insert(0, "tests")
        from test_private_parking_v2 import make_case, run_pipeline

        case, pipe = make_case()
        report = run_pipeline(
            case, pipe,
            "I left the car park and came back later the same day.",
            scenario="p17.10-no-nameerror")
        # Whatever the merits, the run must reach a state rather than raise.
        self.assertIsNotNone(report.state)
        self.assertTrue(report.analysis_modules, report.dump())

    def test_every_plan_item_carries_a_support_bundle(self):
        import sys
        sys.path.insert(0, "tests")
        from test_private_parking_v2 import make_case, run_pipeline

        case, pipe = make_case()
        run_pipeline(case, pipe, "I left and returned later that day.",
                     scenario="p17.10-bundle")
        self.assertTrue(case.claim_plans, "a claim plan must be recorded")
        plan = case.claim_plans[-1]
        items = plan["items"] if isinstance(plan, dict) else plan.items
        self.assertTrue(items, "the plan must carry items")
        for item in items:
            bundle = (item.get("support_bundle") if isinstance(item, dict)
                      else item.support_bundle)
            module = (item.get("module_id") if isinstance(item, dict)
                      else item.module_id)
            self.assertIsNotNone(bundle, module)


class GenerateFailureIsRecorded(unittest.TestCase):
    """A crash keeps its generic customer message but leaves a trail."""

    def _fail_with(self, exc):
        from pcn_appeal.api import _generate

        class Pipe:
            def generate(self, case):
                raise exc

        case = CaseFile("diag")
        with self.assertRaises(type(exc)):
            _generate({"pipe": Pipe()}, case)
        return case.audit[-1]

    def test_the_exception_is_not_swallowed(self):
        entry = self._fail_with(NameError("name 'bundle_dict' is not defined"))
        self.assertEqual(entry["event"], "pipeline_error")
        self.assertEqual(entry["exception_class"], "NameError")
        self.assertEqual(entry["error_code"], "PROCESSING_ERROR")

    def test_the_diagnostic_fields_are_present(self):
        entry = self._fail_with(ValueError("boom"))
        for key in ("error_layer", "error_code", "exception_class", "message",
                    "run_id", "case_revision", "semantic_version",
                    "claim_plan_id", "draft_plan_id"):
            self.assertIn(key, entry)

    def test_the_message_is_capped(self):
        entry = self._fail_with(ValueError("x" * 4000))
        self.assertLessEqual(len(entry["message"]), 500)


if __name__ == "__main__":
    unittest.main()
