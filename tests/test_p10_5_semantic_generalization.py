"""P10.5 — LLM-primary semantics, meaning bridge, role lead governance."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.module_roles import can_lead_letter, governance_basis, role_of
from pcn_appeal.semantics import CONCEPT_DEFINITIONS, extract_and_promote, extract_concepts
from pcn_appeal.semantics.ontology import ONTOLOGY_VERSION
from support import ReferenceAnalysisLLM
from test_scenarios import make_case


class P105ConceptDefinitions(unittest.TestCase):
    def test_every_concept_has_meaning_definition(self):
        from pcn_appeal.semantics.ontology import CONCEPTS
        for cid in CONCEPTS:
            self.assertIn(cid, CONCEPT_DEFINITIONS)
            self.assertGreater(len(CONCEPT_DEFINITIONS[cid]), 20)
        self.assertEqual(ONTOLOGY_VERSION, "p10_5_ontology_v1")


class P105MeaningGeneralization(unittest.TestCase):
    def setUp(self):
        self.llm = ReferenceAnalysisLLM({})

    def test_restart_failure_is_broken_down(self):
        cs = extract_concepts([
            "After a short halt the engine refused to fire again; assistance was arranged."
        ], llm=self.llm)
        aff = {c.concept for c in cs if c.polarity == "AFFIRMED"}
        self.assertTrue({"BROKEN_DOWN", "IMMOBILISED"} & aff)

    def test_payment_and_keying_multi_concept(self):
        cs = extract_concepts([
            "Tariff paid through the parking app; the entered VRM was off by one letter."
        ], llm=self.llm)
        aff = {c.concept for c in cs if c.polarity == "AFFIRMED"}
        self.assertIn("PAYMENT_MADE", aff)
        self.assertIn("KEYING_ERROR", aff)

    def test_departed_location_left_site(self):
        cs = extract_concepts([
            "I departed the location for a while then came back."
        ], llm=self.llm)
        aff = {c.concept: c.polarity for c in cs}
        self.assertEqual(aff.get("LEFT_SITE"), "AFFIRMED")
        self.assertEqual(aff.get("RETURNED"), "AFFIRMED")

    def test_negation_not_promoted(self):
        case, _ = make_case()
        extract_and_promote(
            case, ["I did not leave the site at any point."], llm=self.llm)
        self.assertNotEqual(case.get("left_site"), True)
        concepts = json.loads(case.raw_answers["_semantic_concepts"])
        left = [c for c in concepts if c["concept"] == "LEFT_SITE"]
        self.assertTrue(left)
        self.assertEqual(left[0]["polarity"], "NEGATED")

    def test_uncertainty_not_promoted(self):
        case, _ = make_case()
        extract_and_promote(
            case, ["Possibly the vehicle lost power; I'm not sure."], llm=self.llm)
        self.assertNotEqual(case.get("vehicle_immobilised"), True)

    def test_llm_path_invoked_via_reference_double(self):
        cs = extract_concepts(["Mistyped the vehicle details on the app."], llm=self.llm)
        self.assertTrue(self.llm.calls)
        self.assertEqual(self.llm.calls[-1]["task"], "semantic_extraction")
        self.assertTrue(any(c.concept == "KEYING_ERROR" for c in cs))


class P105RoleGovernance(unittest.TestCase):
    def test_evidence_cannot_lead_by_default(self):
        for mid in ("KB-ANPR-02", "KB-ANPR-03", "KB-EV-01", "KB-TIME-01"):
            self.assertEqual(role_of(mid), "EVIDENCE_REQUIREMENT")
            self.assertFalse(can_lead_letter(mid), mid)

    def test_pofa06_review_cannot_lead(self):
        self.assertFalse(can_lead_letter("KB-POFA-06"))
        g = governance_basis("KB-POFA-06")
        self.assertIn("REVIEW", g["governance_basis"])

    def test_substantive_still_leads(self):
        self.assertTrue(can_lead_letter("KB-PAY-01"))
        self.assertTrue(can_lead_letter("KB-BREAK-01"))


if __name__ == "__main__":
    unittest.main()
