"""Generic semantic propagation matrix (categories, not named cases)."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
from pcn_appeal.engines.question_materiality import annotate, filter_material
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.semantics import extract_and_promote, extract_concepts
from pcn_appeal.semantics.atoms import collect_narrative_atoms
from test_scenarios import make_case


def _base(**extra):
    f = dict(
        operator_name="Northbridge Parking Ltd", pcn_number="NB900200",
        vrm="XY12ZAB", parking_location="Northbridge Retail",
        site_postcode="LS1 1AA", parking_event_date="01/06/2026",
        notice_issue_date="05/06/2026", charge_amount="£100",
        alleged_breach="Overstayed paid time", operator_ata="BPA",
        entry_time="10:00", exit_time="14:30",
    )
    f.update(extra)
    return f


class UnmappedMeaningSurvives(unittest.TestCase):
    def test_unmapped_reason_atom_not_dropped(self):
        # Causal clause preserved even if no ontology concept matches the reason.
        text = (
            "I left the site because of an unexpected access problem at the barrier, "
            "then returned later the same day."
        )
        case = CaseFile("gen-unmapped")
        case.raw_answers["narrative"] = text
        assess_material_account(case)
        st = json.loads(case.raw_answers["_semantic_case_state"])
        cats = {a.get("category") for a in st.get("narrative_atoms") or []}
        self.assertTrue(
            {"unmapped_reason", "departure_event", "return_event"} & cats,
            cats,
        )
        self.assertTrue(st.get("material_relevance"))
        self.assertTrue(st.get("customer_reported_events"))
        # Operator span kept separate when present on case.
        case2 = CaseFile("gen-op")
        case2.put(Fact("F-e", "entry_time", "10:00", FactStatus.EXTRACTED,
                       FactSource(SourceKind.DOCUMENT, "doc")))
        case2.put(Fact("F-x", "exit_time", "14:00", FactStatus.EXTRACTED,
                       FactSource(SourceKind.DOCUMENT, "doc")))
        case2.raw_answers["narrative"] = text
        assess_material_account(case2)
        st2 = json.loads(case2.raw_answers["_semantic_case_state"])
        self.assertTrue(st2.get("operator_observed_events"))
        self.assertTrue(st2.get("customer_reported_events"))


class CategoryMatrix(unittest.TestCase):
    """Semantic categories A–O (inputs vary; no implementation tied to wording)."""

    def _fm(self, text: str) -> CaseFile:
        case = CaseFile("gen-m")
        case.raw_answers["narrative"] = text
        assess_material_account(case)
        return case

    def test_A_multiple_visits(self):
        c = self._fm("Left the car park and returned later; two separate visits.")
        self.assertTrue(c.get("multiple_visits"))
        self.assertTrue(c.get("left_site"))
        self.assertTrue(c.get("returned_same_day"))

    def test_B_dropoff_collection(self):
        c = self._fm(
            "Dropped a passenger off, left the car park, returned later to collect them."
        )
        self.assertTrue(c.get("dropoff_activity"))
        self.assertTrue(c.get("pickup_activity"))
        self.assertTrue(c.get("multiple_visits"))

    def test_C_shopping_departure_return(self):
        c = self._fm(
            "Attended for shopping, left the site, returned later the same day."
        )
        self.assertEqual(c.get("purpose_of_visit"), "shopping")
        self.assertTrue(c.get("multiple_visits"))

    def test_D_payment(self):
        c = self._fm("I paid for parking via the machine.")
        self.assertTrue(c.get("payment_made"))

    def test_E_keying(self):
        c = self._fm("I mistyped the registration when paying.")
        self.assertEqual(c.get("keying_error_type"), "MINOR")

    def test_F_immobilisation(self):
        c = self._fm("The vehicle broke down and could not be moved.")
        self.assertTrue(c.get("vehicle_immobilised"))

    def test_G_access_barrier_unmapped(self):
        c = self._fm(
            "I could not exit because the barrier failed, so I waited and then left."
        )
        st = json.loads(c.raw_answers["_semantic_case_state"])
        # Unmapped material circumstance retained as atom / event, not dropped.
        self.assertTrue(st.get("narrative_atoms") or st.get("events"))

    def test_H_uncertain(self):
        concepts = extract_concepts(["I think I may have left and come back."])
        left = next(c for c in concepts if c.concept == "LEFT_SITE")
        self.assertEqual(left.polarity, "UNCERTAIN")
        c = self._fm("I think I may have left and come back.")
        self.assertNotEqual(c.get("left_site"), True)

    def test_I_negation(self):
        c = self._fm("I did not leave the site.")
        self.assertNotEqual(c.get("left_site"), True)
        st = json.loads(c.raw_answers["_semantic_case_state"])
        neg = [a for a in st.get("narrative_atoms") or []
               if a.get("polarity") == "NEGATED"]
        self.assertTrue(neg or any(
            x.get("polarity") == "NEGATED"
            for x in st.get("concepts") or []))

    def test_J_contradiction(self):
        c = self._fm(
            "Left the site and returned later; these were two separate visits."
        )
        c.put(Fact("F-mv", "multiple_visits", False, FactStatus.ANSWERED,
                   FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        from pcn_appeal.semantics.state import record_material_conflicts, open_material_fact_conflicts
        record_material_conflicts(c)
        self.assertTrue(open_material_fact_conflicts(c))

    def test_K_unmapped_material_reason(self):
        atoms = collect_narrative_atoms([
            "I left because of a temporary access issue, then came back later."
        ])
        cats = {a.get("category") for a in atoms}
        self.assertIn("unmapped_reason", cats)

    def test_L_document_plus_customer(self):
        case, pipe = make_case(_base(notice_issue_date="20/06/2026"))
        pipe.ingest(case)
        pipe.confirm(
            case, {}, list(case.facts),
            "Left the car park and returned later for a second visit.",
        )
        self.assertTrue(case.get("multiple_visits"))
        match = KnowledgeMatcher(pipe.kg).match(case)
        anpr = match.candidates.get("KB-ANPR-01")
        self.assertIsNotNone(anpr)
        self.assertEqual(anpr.status, "SUPPORTED")

    def test_M_irrelevant_detail_not_forced_to_ground(self):
        c = self._fm("The weather was nice and the radio was playing.")
        # No visit / payment / immobilisation invented from colour.
        self.assertNotEqual(c.get("multiple_visits"), True)
        self.assertNotEqual(c.get("payment_made"), True)
        self.assertNotEqual(c.get("vehicle_immobilised"), True)

    def test_N_material_question_kept(self):
        kg = KnowledgeGraph()
        case = CaseFile("gen-q")
        # Unknown fact required by some module path — annotate structure.
        rec = annotate(case, kg, {
            "fact": "multiple_visits",
            "text": "Did the vehicle visit more than once?",
            "type": "bool",
        })
        for key in (
            "question_id", "target_fact", "requesting_module_ids",
            "current_module_status", "why_fact_is_missing",
            "why_answer_can_change_outcome", "existing_sources_checked",
            "case_revision", "ask",
        ):
            self.assertIn(key, rec)

    def test_O_derivable_postcode_suppressed(self):
        kg = KnowledgeGraph()
        case = CaseFile("gen-pc")
        case.put(Fact(
            "F-jur", "jurisdiction", "ENGLAND_WALES", FactStatus.DERIVED,
            FactSource(SourceKind.CALCULATION, "postcode_jurisdiction"),
        ))
        keep, suppressed = filter_material(case, kg, [{
            "fact": "site_postcode",
            "text": "What is the postcode of the car park itself?",
            "type": "text",
            "unlocks": ["KB-POFA-02"],
        }])
        self.assertEqual(keep, [])
        self.assertTrue(suppressed)
        self.assertEqual(
            suppressed[0]["materiality"]["suppress_reason"],
            "already_resolved_from_existing_evidence",
        )


class NoPhraseSpecificBypass(unittest.TestCase):
    def test_ontology_extra_is_generic_activity_mapping(self):
        from pcn_appeal.semantics.ontology import CONCEPT_TO_FACTS, CONCEPT_EXTRA_FACTS
        self.assertEqual(CONCEPT_TO_FACTS["DROP_OFF"][0], "dropoff_activity")
        self.assertEqual(CONCEPT_TO_FACTS["PICK_UP"][0], "pickup_activity")
        # Extras are purpose labels only — not operator/location/person keys.
        for concept, extras in CONCEPT_EXTRA_FACTS.items():
            for name, _val in extras:
                self.assertNotIn("operator", name)
                self.assertNotIn("postcode", name)
                self.assertNotIn("harringay", str(_val).lower())


if __name__ == "__main__":
    unittest.main()
