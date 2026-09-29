"""System-wide free-text handling: extract → normalize → professional draft use."""
import unittest

from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.validation import ValidationEngine, _copy_fingerprint
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (
    CaseFile, Draft, DraftSentence, Fact, FactSource, FactStatus, RetrievalPack, SourceKind,
)
from pcn_appeal.engines.recovery import FactRecoveryEngine
from pcn_appeal.engines.extraction import known_operator_ata

KG = KnowledgeGraph()


def _case(breach: str, narrative: str) -> CaseFile:
    case = CaseFile("C-FT")
    case.put(Fact(
        "F-br", "alleged_breach", breach,
        FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1"),
    ))
    case.raw_answers["narrative"] = narrative
    return case


class FreeTextExtractionScenarios(unittest.TestCase):
    def test_kids_and_seeking_space(self):
        case = _case(
            "parked in a Parent and Child bay without being accompanied by a child",
            "Kids were in the car and I was trying to find somewhere to park.",
        )
        dig = assess_material_account(case)
        self.assertTrue(case.get("child_occupant_present"))
        self.assertTrue(case.get("seeking_parking_space"))
        self.assertTrue(dig["contradicts"])
        props = " ".join(dig["propositions"]).lower()
        self.assertIn("presence of children", props)
        self.assertIn("locating a suitable parking space", props)
        # No customer wording in propositions.
        self.assertNotIn("kids were", props)
        self.assertNotIn("trying to find", props)
        # Provenance chain present.
        self.assertTrue(case.free_text_provenance)
        row = case.free_text_provenance[0]
        self.assertEqual(row["source"], "CUSTOMER_FREE_TEXT")
        self.assertIn("original", row)
        self.assertIn("fact_name", row)
        self.assertIn("drafting_proposition", row)

    def test_breakdown(self):
        case = _case("Overstayed paid time", "The car broke down and we couldn't leave.")
        dig = assess_material_account(case)
        self.assertTrue(case.get("vehicle_immobilised"))
        self.assertTrue(case.get("immobilisation_prevented_departure"))
        self.assertTrue(any("immobilised" in p.lower() for p in dig["propositions"]))

    def test_payment_failure(self):
        case = _case(
            "Parked without payment",
            "The machine would not take my card and the payment failed.",
        )
        dig = assess_material_account(case)
        self.assertTrue(case.get("payment_attempt_failed"))
        self.assertTrue(any("payment facility" in p.lower() for p in dig["propositions"]))

    def test_residential(self):
        case = _case(
            "Parked without a permit",
            "I live in the block and this is our allocated bay.",
        )
        dig = assess_material_account(case)
        self.assertTrue(case.get("resident_connection_stated"))
        self.assertTrue(any("residential" in p.lower() for p in dig["propositions"]))

    def test_multiple_visits(self):
        case = _case(
            "Overstayed",
            "We left and came back later — two separate visits that day.",
        )
        dig = assess_material_account(case)
        self.assertTrue(case.get("multiple_visits"))
        self.assertTrue(any("more than once" in p.lower() for p in dig["propositions"]))

    def test_disability_blue_badge(self):
        case = _case(
            "Vehicle parked in a disabled bay",
            "A blue badge was displayed and extra time was needed for disability reasons.",
        )
        dig = assess_material_account(case)
        self.assertTrue(case.get("blue_badge_displayed"))
        self.assertTrue(case.get("disability_extra_time"))
        self.assertTrue(dig["contradicts"])

    def test_does_not_invent_from_unrelated_text(self):
        case = _case(
            "Parent and Child bay without being accompanied by a child",
            "The letter arrived a few days later.",
        )
        dig = assess_material_account(case)
        self.assertFalse(dig["contradicts"])
        self.assertEqual(dig["propositions"], [])
        self.assertFalse(case.get("child_occupant_present"))

    def test_source_kind_is_customer_free_text(self):
        case = _case(
            "Parent and Child bay without child",
            "Left the kids in the car while shopping.",
        )
        assess_material_account(case)
        fact = case.facts["child_occupant_present"]
        self.assertEqual(fact.source.kind, SourceKind.CUSTOMER_FREE_TEXT)


class NoVerbatimPaste(unittest.TestCase):
    def test_validator_blocks_pasted_free_text(self):
        raw = "Kids were in the car and I was trying to find somewhere to park."
        pack = RetrievalPack(
            primary_route="BAY", secondary_routes=[], module_ids=["KB-BAY-01"],
            verified_facts={
                "alleged_breach": "Parent and Child bay",
                "operator_name": "Euro Car Parks",
                "pcn_number": "45000564251",
                "vrm": "RX75VPP",
            },
            fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="UNRESOLVED", pofa_findings=[],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
            case_context={
                "customer_source_texts": [raw],
                "alleged_breach": "Parent and Child bay",
                "operator_name": "Euro Car Parks",
            },
        )
        draft = Draft("C1", [[
            DraftSentence(f"The keeper says: {raw}", [], ["KB-BAY-01"], []),
            DraftSentence("The operator is requested to cancel the charge.", [], ["KB-BAY-01"], []),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertFalse(result.passed)
        self.assertTrue(any(i.rule == "VAL-CUSTOMER-COPY" for i in result.issues))

    def test_professional_rewrite_is_not_blocked(self):
        raw = "Kids were in the car and I was trying to find somewhere to park."
        pack = RetrievalPack(
            primary_route="BAY", secondary_routes=[], module_ids=["KB-BAY-01"],
            verified_facts={
                "alleged_breach": "Parent and Child bay without child",
                "operator_name": "Euro Car Parks",
                "pcn_number": "45000564251",
                "vrm": "RX75VPP",
            },
            fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="UNRESOLVED", pofa_findings=[],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
            case_context={
                "customer_source_texts": [raw],
                "alleged_breach": "Parent and Child bay without child",
                "operator_name": "Euro Car Parks",
            },
        )
        draft = Draft("C1", [[
            DraftSentence(
                "The vehicle was being used in connection with the presence of children, "
                "and time was spent on arrival locating a suitable parking space.",
                [], ["KB-BAY-01"], []),
            DraftSentence(
                "That is inconsistent with the factual premise of the Parent and Child bay allegation.",
                [], ["KB-BAY-01"], []),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertFalse(any(i.rule == "VAL-CUSTOMER-COPY" for i in result.issues))

    def test_fingerprint_ignores_short_tokens(self):
        self.assertEqual(_copy_fingerprint("OK"), "")


class KnownOperatorAta(unittest.TestCase):
    def test_euro_car_parks_resolves_without_asking(self):
        self.assertEqual(known_operator_ata("Euro Car Parks"), "BPA")
        case = CaseFile("C-ATA")
        case.put(Fact("F-op", "operator_name", "Euro Car Parks", FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.put(Fact("F-bay", "restricted_bay_alleged", True, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "t")))
        case.put(Fact("F-win", "observation_window_min", 0, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "t")))
        report = FactRecoveryEngine(KG).recover(case)
        self.assertEqual(case.get("operator_ata"), "BPA")
        self.assertIn("operator_ata", report.do_not_ask)


if __name__ == "__main__":
    unittest.main()
