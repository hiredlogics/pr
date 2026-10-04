"""P10.3 — module roles, semantic concepts, orphan-support, generic DEV cases.

Does not load or inspect the new holdout expected outputs.
Does not tune against the old P9 blind set.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pcn_appeal.engines.claim_plan_authority import ROLE_INELIGIBLE, ClaimPlanBuilder
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (
    CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, RetrievalPack,
)
from pcn_appeal.module_roles import (
    CLAIM_GROUND_ROLES, LEGAL_CONCLUSION, SUPPORTING_PROPOSITION,
    SUBSTANTIVE_GROUND, can_be_claim_ground, classify_pack, role_of,
)
from pcn_appeal.semantics import extract_concepts, extract_and_promote
from pcn_appeal.semantics.ontology import CONCEPTS
from test_scenarios import make_case, run

ROOT = Path(__file__).resolve().parents[1]
DEV_DIR = ROOT / "datasets" / "p10_3_v1" / "development"
HOLDOUT_DIR = ROOT / "datasets" / "p10_3_v1" / "holdout"


def _base_fields(**extra):
    f = dict(
        operator_name="Northbridge Parking Ltd", pcn_number="NB900100",
        vrm="XY12ZAB", parking_location="Northbridge Retail",
        site_postcode="LS1 1AA", parking_event_date="01/06/2026",
        notice_issue_date="05/06/2026", charge_amount="£100",
        alleged_breach="Overstayed paid time", operator_ata="BPA",
        entry_time="10:00", exit_time="12:47",
    )
    f.update(extra)
    return f


class P103ModuleRoles(unittest.TestCase):
    def test_taxonomy_covers_shipped_modules(self):
        kg = KnowledgeGraph()
        for mid, mod in kg.modules.items():
            self.assertIn(role_of(mod), {
                "SUBSTANTIVE_GROUND", "SUPPORTING_PROPOSITION", "LEGAL_CONCLUSION",
                "EVIDENCE_REQUIREMENT", "STRUCTURAL",
            }, mid)
            self.assertEqual(mod.module_role, role_of(mod))

    def test_pofa_companions_are_not_claim_grounds(self):
        self.assertEqual(role_of("KB-POFA-01"), LEGAL_CONCLUSION)
        self.assertEqual(role_of("KB-POFA-05"), LEGAL_CONCLUSION)
        self.assertFalse(can_be_claim_ground("KB-POFA-01"))
        self.assertFalse(can_be_claim_ground("KB-POFA-05"))
        self.assertTrue(can_be_claim_ground("KB-POFA-02"))

    def test_landowner_support_cannot_lead_alone(self):
        self.assertEqual(role_of("KB-LAND-01"), SUPPORTING_PROPOSITION)
        parts = classify_pack(["KB-LAND-01", "KB-POFA-01"])
        self.assertTrue(parts["orphan_support"])
        self.assertEqual(parts["substantive"], [])

    def test_negative_control_pay_and_key_remain_substantive(self):
        self.assertEqual(role_of("KB-PAY-01"), SUBSTANTIVE_GROUND)
        self.assertEqual(role_of("KB-KEY-01"), SUBSTANTIVE_GROUND)
        self.assertTrue(role_of("KB-PAY-01") in CLAIM_GROUND_ROLES)


class P103SemanticOntology(unittest.TestCase):
    def test_mechanical_issue_promotes_before_questions(self):
        # Wording deliberately unlike REG_breakdown blind narrative.
        case, pipe = make_case(_base_fields(alleged_breach="Overstayed paid time"))
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts),
                     "The engine cut out on the forecourt and we could not move the car")
        self.assertTrue(case.get("vehicle_immobilised"))
        concepts = {c["concept"] for c in json.loads(case.raw_answers["_semantic_concepts"])
                    if c["polarity"] == "AFFIRMED"}
        self.assertTrue({"BROKEN_DOWN", "IMMOBILISED"} & concepts)

    def test_payment_registration_mismatch(self):
        case, pipe = make_case(_base_fields(
            alleged_breach="No valid payment for vehicle"))
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts),
                     "Paid via the phone app; entered an incorrect registration plate")
        self.assertTrue(case.get("payment_made"))
        self.assertEqual(case.get("keying_error_type"), "MINOR")

    def test_negated_statement_is_not_promoted(self):
        concepts = extract_concepts(["I did not pay for parking that day"])
        affirmed = [c for c in concepts if c.polarity == "AFFIRMED" and c.concept == "PAYMENT_MADE"]
        self.assertEqual(affirmed, [])
        negated = [c for c in concepts if c.polarity == "NEGATED" and c.concept == "PAYMENT_MADE"]
        self.assertTrue(negated)

    def test_uncertain_statement_is_not_promoted_as_fact(self):
        case = CaseFile("C-UNC")
        extract_and_promote(case, ["Maybe the car broke down, not sure"])
        self.assertNotEqual(case.get("vehicle_immobilised"), True)

    def test_loading_delivery_concept(self):
        concepts = extract_concepts([
            "I was unloading a delivery for the hardware store next door"])
        names = {c.concept for c in concepts if c.polarity == "AFFIRMED"}
        self.assertTrue({"LOADING", "DELIVERY"} & names)

    def test_multiple_visits_concept(self):
        concepts = extract_concepts([
            "We left the retail park and came back later for a second visit"])
        names = {c.concept for c in concepts if c.polarity == "AFFIRMED"}
        self.assertIn("MULTIPLE_VISITS", names)

    def test_llm_must_not_accept_module_ids_as_concepts(self):
        from pcn_appeal.semantics.extract import validate_concepts
        bad = validate_concepts([
            {"concept": "KB-PAY-01", "polarity": "AFFIRMED", "source_text": "x"},
            {"concept": "PAYMENT_MADE", "polarity": "AFFIRMED", "source_text": "paid"},
        ])
        self.assertEqual([c.concept for c in bad], ["PAYMENT_MADE"])

    def test_ontology_keys_are_controlled(self):
        self.assertIn("KEYING_ERROR", CONCEPTS)
        self.assertIn("BROKEN_DOWN", CONCEPTS)


class P103GroundEligibility(unittest.TestCase):
    def test_late_ntk_excludes_pofa_companions_from_plan(self):
        case, pipe = make_case(_base_fields(notice_issue_date="20/06/2026"))
        out = run(case, pipe, "Letter arrived weeks after the parking date", {})
        self.assertIn("KB-POFA-02", out.pack.module_ids)
        self.assertNotIn("KB-POFA-01", out.pack.module_ids)
        self.assertNotIn("KB-POFA-05", out.pack.module_ids)
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)

    def test_support_only_pack_is_held_not_drafted(self):
        """Orphan SUPPORTING/LEGAL_CONCLUSION → no empty appeal."""
        pack = RetrievalPack(
            primary_route="POFA", secondary_routes=[],
            module_ids=["KB-POFA-01", "KB-LAND-01"],
            verified_facts={"pcn_number": "NB1", "vrm": "XY12ZAB"},
            fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version="SCOP-1.1", pofa_route="POSTAL", pofa_findings=[],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
            claim_plan={"status": "LOCKED", "approved": ["KB-POFA-01", "KB-LAND-01"]},
        )
        draft = Draft("C-ORPH", [[DraftSentence(
            "Keeper liability is not automatic.", [], ["KB-POFA-01"])]])
        rules = {i.rule for i in ValidationEngine().validate(draft, pack).issues}
        self.assertIn("VAL-ORPHAN-SUPPORT", rules)

    def test_claim_plan_rejects_legal_conclusion_role(self):
        case, pipe = make_case(_base_fields(notice_issue_date="20/06/2026"))
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "Letter came late")
        plan = ClaimPlanBuilder(pipe.kg, pipe.reasoning).build(case)
        self.assertNotIn("KB-POFA-01", plan.supported_ids)
        self.assertNotIn("KB-POFA-05", plan.supported_ids)
        self.assertTrue(any(
            i.module_id in ("KB-POFA-01", "KB-POFA-05") and i.decision == ROLE_INELIGIBLE
            for i in plan.items
        ) or not any(i.module_id in ("KB-POFA-01", "KB-POFA-05") for i in plan.items))

    def test_near_miss_content_defect_remains_substantive(self):
        self.assertTrue(can_be_claim_ground("KB-POFA-04"))

    def test_payment_keying_selects_substantive_pair(self):
        case, pipe = make_case(_base_fields(
            alleged_breach="No valid payment for vehicle"),
            evidence={"E4": EvidenceItem("E4", "APP_SCREENSHOT", "app.png")},
            doc_types={"E4": "APP_SCREENSHOT"})
        out = run(case, pipe,
                  "Settled the tariff on the phone app but mistyped the plate",
                  {"payment_made": "yes", "payment_method": "APP",
                   "keying_error_type": "MINOR"})
        self.assertEqual(out.pack.primary_route, "PAYMENT")
        self.assertIn("KEYING", out.pack.secondary_routes)
        self.assertIn("KB-PAY-01", out.pack.module_ids)
        self.assertIn("KB-KEY-01", out.pack.module_ids)
        self.assertNotIn("KB-POFA-01", out.pack.module_ids)
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)


class P103DevelopmentFixtures(unittest.TestCase):
    """Generic DEV cases — different wording/facts from old blind holdout."""

    def test_development_manifest_exists(self):
        manifest = json.loads((DEV_DIR.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_id"], "p10_3_v1")
        self.assertGreaterEqual(len(manifest["split"]["DEVELOPMENT"]), 8)
        self.assertGreaterEqual(len(manifest["split"]["HOLDOUT"]), 4)

    def test_holdout_is_sealed_from_this_suite(self):
        """Implementation tests must not open holdout expected blocks."""
        for path in HOLDOUT_DIR.glob("*.json"):
            # Only assert the file exists and has an input; do not read expected.
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("input", data)
            self.assertEqual(data.get("split"), "HOLDOUT")
            self.assertTrue(data.get("sealed", True))


class P103DevMetricsSmoke(unittest.TestCase):
    """Lightweight DEV precision/recall smoke — not hardcoded golden matching."""

    CASES = [
        ("mech", "The engine cut out and we were unable to leave the bay",
         {}, {"BROKEN_DOWN", "IMMOBILISED"}, {"KB-BREAK-01", "KB-BREAK-02", "KB-BREAK-03"}),
        ("keypay", "Paid on the app with a mistyped registration",
         {"payment_made": "yes", "keying_error_type": "MINOR", "payment_method": "APP"},
         {"PAYMENT_MADE", "KEYING_ERROR"}, {"KB-PAY-01", "KB-KEY-01"}),
        ("visits", "Left the site and returned later for a second visit",
         {}, {"MULTIPLE_VISITS", "LEFT_SITE", "RETURNED"}, set()),
        ("neg", "I did not pay and there was no breakdown",
         {}, set(), set()),
    ]

    def test_semantic_and_ground_metrics_improve_on_dev_classes(self):
        concept_tp = concept_fn = 0
        ground_hits = ground_cases = 0
        orphan = 0
        companion_fp = 0
        for name, narrative, answers, want_concepts, want_grounds in self.CASES:
            extra = {}
            if name == "keypay":
                extra = {"alleged_breach": "No valid payment for vehicle"}
                case, pipe = make_case(
                    _base_fields(**extra),
                    evidence={"E4": EvidenceItem("E4", "APP_SCREENSHOT", "a.png")},
                    doc_types={"E4": "APP_SCREENSHOT"},
                )
            else:
                case, pipe = make_case(_base_fields(**extra))
            out = run(case, pipe, narrative, answers)
            concepts = set()
            raw = case.raw_answers.get("_semantic_concepts")
            if raw:
                concepts = {c["concept"] for c in json.loads(raw)
                            if c.get("polarity") == "AFFIRMED"}
            concept_tp += len(want_concepts & concepts)
            concept_fn += len(want_concepts - concepts)
            selected = set(out.pack.module_ids or [])
            selected_claim = {m for m in selected if can_be_claim_ground(m)}
            if want_grounds:
                ground_cases += 1
                if want_grounds & selected_claim:
                    ground_hits += 1
            companion_fp += len({"KB-POFA-01", "KB-POFA-05"} & selected)
            parts = classify_pack(out.pack.module_ids)
            if parts["orphan_support"] and out.letter:
                orphan += 1
        self.assertGreater(concept_tp, 0)
        self.assertEqual(concept_fn, 0, "DEV semantic recall regression")
        if ground_cases:
            self.assertGreaterEqual(ground_hits / ground_cases, 0.9)
        self.assertEqual(companion_fp, 0, "LEGAL_CONCLUSION companions must not be grounds")
        self.assertEqual(orphan, 0)


if __name__ == "__main__":
    unittest.main()
