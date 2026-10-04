"""P10.6 — DraftPlan ownership, merge, structured assemble, validators."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.drafting.context import DraftContext
from pcn_appeal.drafting.drafter import assemble_structured_draft
from pcn_appeal.drafting.plan import (
    DRAFT_PLAN_VERSION, build_draft_plan, particular_expressed,
    section_expresses_ground,
)
from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.llm import DemoLLM
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack


def _pack(module_ids, facts=None, chunks=None, findings=None):
    plan = {
        "status": "LOCKED",
        "claim_plan_id": "CP-TEST",
        "approved": list(module_ids),
        "module_ids": list(module_ids),
        "support_bundles": {},
        "draft_requirements": {},
        "labels": {m: m for m in module_ids},
    }
    for mid in module_ids:
        plan["support_bundles"][mid] = {
            "source_fact_ids": ["F1"],
            "source_fact_names": list((facts or {}).keys())[:1] or ["payment_made"],
            "derived_fact_ids": [],
            "evidence_ids": ["E1"],
            "legal_finding_ids": [],
            "relationship_ids": [],
            "derived_fact_names": [],
            "values": dict(facts or {"payment_made": True}),
        }
        plan["draft_requirements"][mid] = {
            "required_particulars": list((facts or {"payment_made": True}).keys()),
            "prohibited_content": [],
            "explanation_goal": [],
        }
    return RetrievalPack(
        primary_route="PAYMENT",
        secondary_routes=[],
        module_ids=list(module_ids),
        verified_facts=dict(facts or {"payment_made": True, "vrm": "AB12CDE"}),
        fact_refs={"payment_made": "F1", "vrm": "F2"},
        missing_facts=[],
        evidence_refs=["E1"],
        prohibited_claims=[],
        code_version="BPA_v9",
        pofa_route="POSTAL",
        pofa_findings=[],
        driver_status="UNIDENTIFIED",
        jurisdiction="ENG_WAL",
        context_chunks=list(chunks or []),
        lease_clauses=[],
        case_context={"claim_plan": plan, "operator_name": "Test Ops"},
        claim_plan=plan,
        legal_findings=list(findings or []),
        evidence_index={"E1": "APP_SCREENSHOT"},
    )


class TestDraftPlan(unittest.TestCase):
    def test_one_section_per_ground(self):
        pack = _pack(["KB-PAY-01", "KB-BREAK-01"], facts={
            "payment_made": True, "vehicle_immobilised": True,
        })
        plan = build_draft_plan(pack, "C1")
        self.assertEqual(plan.version, DRAFT_PLAN_VERSION)
        self.assertEqual(len(plan.sections), 2)
        owned = plan.owned_grounds()
        self.assertIn("KB-PAY-01", owned)
        self.assertIn("KB-BREAK-01", owned)

    def test_merges_payment_and_keying(self):
        pack = _pack(["KB-PAY-01", "KB-KEY-01"])
        plan = build_draft_plan(pack, "C2")
        self.assertEqual(len(plan.sections), 1)
        sec = plan.sections[0]
        self.assertTrue(sec.merged)
        self.assertEqual(set(sec.ground_ids), {"KB-PAY-01", "KB-KEY-01"})

    def test_support_only_does_not_own_section(self):
        pack = _pack(["KB-LAND-01", "KB-SIGN-01"])
        plan = build_draft_plan(pack, "C3")
        self.assertEqual(plan.sections, [])
        self.assertIn("KB-LAND-01", plan.support_only_ids)
        self.assertIn("KB-SIGN-01", plan.support_only_ids)

    def test_assemble_structured_sections(self):
        out = {
            "opening": "I am appealing as the registered keeper.",
            "sections": [{
                "section_id": "S01",
                "ground_ids": ["KB-PAY-01"],
                "text": "A payment was made for the parking session via the app.",
                "fact_ids_used": ["F1"],
                "finding_ids_used": [],
            }],
            "closing": "I invite the operator to cancel the Parking Charge Notice.",
            "no_ground_reason": None,
        }
        draft = assemble_structured_draft("C4", out, 1, "DemoLLM", 17, {
            "sections": [{"section_id": "S01", "ground_ids": ["KB-PAY-01"],
                          "supporting_fact_ids": ["F1"], "evidence_ids": []}],
        })
        self.assertTrue(draft.section_ownership)
        self.assertIn("S01", draft.section_ownership)
        self.assertTrue(any("payment" in s.text.lower() for s in draft.sentences()))
        self.assertEqual(draft.sentences()[0].module_refs, ["STRUCTURAL"])

    def test_semantic_equivalence_particulars(self):
        text = "The vehicle left the site and later returned the same day."
        self.assertTrue(particular_expressed(text, "left_site", True))
        self.assertTrue(particular_expressed(text, "returned_same_day", True))

        class S:
            ground_ids = ["KB-ANPR-01"]
            particular_values = {"left_site": True}
            required_particulars = ["left_site"]

        self.assertTrue(section_expresses_ground(text, S()))

    def test_val_ground_coverage_blocks_missing_section(self):
        pack = _pack(["KB-PAY-01", "KB-BREAK-01"], facts={
            "payment_made": True, "vehicle_immobilised": True,
        })
        draft = Draft("C5", [
            [DraftSentence("I appeal as keeper.", [], ["STRUCTURAL"], [])],
            [DraftSentence("A payment was made for the session.", ["F1"], ["KB-PAY-01"], [])],
            [DraftSentence("Please cancel the Parking Charge Notice.", [], ["STRUCTURAL"], [])],
        ])
        result = DraftValidationEngine().check(draft, pack, {"F1"})
        rules = {i.rule for i in result.issues}
        self.assertTrue("VAL-GROUND-COVERAGE" in rules or "VAL-COVERAGE" in rules)

    def test_demo_draft_respects_draft_plan(self):
        pack = _pack(["KB-PAY-01", "KB-KEY-01"])
        payload = DraftContext.from_pack(pack).to_payload()
        self.assertTrue(payload.get("draft_plan"))
        self.assertEqual(len(payload["draft_plan"]["sections"]), 1)
        out = DemoLLM().complete_json(task="drafting", system="", user=json.dumps(payload))
        self.assertTrue(out.get("sections"))
        self.assertEqual(out["sections"][0]["ground_ids"], ["KB-PAY-01", "KB-KEY-01"])
        text = out["sections"][0]["text"].lower()
        self.assertTrue("pay" in text or "payment" in text)


if __name__ == "__main__":
    unittest.main()
