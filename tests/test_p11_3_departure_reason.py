"""P11.3 — generic departure_reason narrative particular propagation."""
from __future__ import annotations

import unittest

from pcn_appeal.engines.narrative import (
    departure_reason_proposition,
    extract_departure_reason,
    read,
    understand,
)
from pcn_appeal.models import CaseFile, CaseState, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.drafting.support_contract import enrich_support_rows, build_bundle
from pcn_appeal.drafting.plan import build_draft_plan, particular_expressed, LETTER_PARTICULARS
from pcn_appeal.models import RetrievalPack


NARRATIVES = (
    "I attended for shopping. I realised I had forgotten my purse, left the site, "
    "and returned later the same day.",
    "I forgot my wallet and returned later the same day.",
    "I left to collect my payment card and returned later.",
    "I realised a necessary item was at home, left and returned the same day.",
)


class DepartureReasonExtraction(unittest.TestCase):
    def test_cp_plus_purse(self):
        atom = extract_departure_reason(NARRATIVES[0])
        self.assertIsNotNone(atom)
        self.assertEqual(atom["name"], "departure_reason")
        self.assertEqual(atom["polarity"], "AFFIRMED")
        self.assertEqual(atom["attribution"], "CUSTOMER")
        self.assertTrue(
            "forgotten" in atom["proposition"]
            or "left elsewhere" in atom["proposition"]
            or "collect" in atom["proposition"]
        )
        self.assertTrue(atom["source_text"])

    def test_generic_equivalents(self):
        for text in NARRATIVES:
            with self.subTest(text=text[:40]):
                atom = extract_departure_reason(text)
                self.assertIsNotNone(atom, text)
                self.assertTrue(atom["proposition"])
                r = read(text)
                self.assertIn("departure_reason", r.facts)

    def test_no_false_positive_on_plain_multi_visit(self):
        atom = extract_departure_reason(
            "The vehicle left mid-morning and returned later the same day.")
        self.assertIsNone(atom)


class Propagation(unittest.TestCase):
    def test_understand_writes_atom_and_provenance(self):
        case = CaseFile("p113-1")
        out = understand(case, [NARRATIVES[0]])
        self.assertTrue(case.get("departure_reason"))
        self.assertTrue(out.get("narrative_atoms"))
        self.assertTrue(any(
            p.get("fact_name") == "departure_reason"
            for p in case.free_text_provenance
        ))
        self.assertTrue(any(a.get("event") == "narrative_atom" for a in case.audit))

    def test_support_bundle_carries_departure_reason(self):
        case = CaseFile("p113-2")
        understand(case, [NARRATIVES[0]])
        case.put(Fact(
            "F-mv", "multiple_visits", True, FactStatus.ANSWERED,
            FactSource(SourceKind.ANSWER, "answer:multiple_visits"),
        ))
        rows = enrich_support_rows(
            [{"fact": "multiple_visits", "value": True, "condition": "multiple_visits=true"}],
            case=case,
        )
        mv = next(r for r in rows if r.get("fact") == "multiple_visits")
        because = mv.get("because_of") or []
        names = {b.get("fact") for b in because if isinstance(b, dict)}
        self.assertIn("departure_reason", names)
        self.assertIn("left_site", names)
        bundle = build_bundle(rows, case=case)
        self.assertIn("departure_reason", bundle.source_fact_names)
        self.assertIn("departure_reason", bundle.values)
        self.assertIn("departure_reason", LETTER_PARTICULARS)

    def test_draft_plan_anpr_requires_departure_reason(self):
        case = CaseFile("p113-3")
        understand(case, [NARRATIVES[0]])
        dep = case.get("departure_reason")
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[], module_ids=["KB-ANPR-01"],
            verified_facts={
                "multiple_visits": True, "left_site": True,
                "returned_same_day": True, "purpose_of_visit": "shopping",
                "departure_reason": dep,
            },
            fact_refs={}, missing_facts=[], evidence_refs=[],
            prohibited_claims=[], code_version=None, pofa_route="UNRESOLVED",
            pofa_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            case_context={
                "claim_plan": {
                    "status": "LOCKED",
                    "approved": ["KB-ANPR-01"],
                    "module_ids": ["KB-ANPR-01"],
                    "support_bundles": {
                        "KB-ANPR-01": {
                            "source_fact_ids": ["a", "b", "c", "d", "e"],
                            "source_fact_names": [
                                "left_site", "returned_same_day",
                                "purpose_of_visit", "departure_reason",
                                "multiple_visits",
                            ],
                            "derived_fact_ids": ["m"],
                            "derived_fact_names": ["multiple_visits"],
                            "values": {
                                "left_site": True,
                                "returned_same_day": True,
                                "purpose_of_visit": "shopping",
                                "departure_reason": dep,
                                "multiple_visits": True,
                            },
                            "evidence_ids": [],
                            "legal_finding_ids": [],
                            "relationship_ids": [],
                        }
                    },
                    "draft_requirements": {
                        "KB-ANPR-01": {
                            "required_particulars": [
                                "left_site", "returned_same_day",
                                "purpose_of_visit", "departure_reason",
                                "multiple_visits",
                            ],
                            "prohibited_content": [],
                            "explanation_goal": [],
                        }
                    },
                    "labels": {"KB-ANPR-01": "ANPR"},
                },
                "narrative_atoms": [{
                    "atom_id": "NA-departure_reason",
                    "name": "departure_reason",
                    "proposition": dep,
                    "source_text": "forgotten my purse",
                    "attribution": "CUSTOMER",
                    "polarity": "AFFIRMED",
                    "confidence": 0.85,
                }],
            },
        )
        plan = build_draft_plan(pack, case_id=case.case_id)
        sec = plan.sections[0]
        self.assertEqual(sec.ground_id, "KB-ANPR-01")
        self.assertIn("departure_reason", sec.required_particulars)
        self.assertEqual(sec.particular_values.get("departure_reason"), dep)
        self.assertTrue(sec.narrative_atoms)
        self.assertIn("WHY", sec.purpose)

    def test_particular_expressed_departure_reason(self):
        text = (
            "The keeper's account is that after attending for shopping, "
            "a necessary item had been forgotten, prompting the departure; "
            "the vehicle left the site and later returned the same day."
        )
        self.assertTrue(particular_expressed(text, "departure_reason",
                                             "a necessary item had been forgotten"))
        self.assertTrue(particular_expressed(text, "purpose_of_visit", "shopping"))
        self.assertTrue(particular_expressed(text, "left_site", True))
        self.assertTrue(particular_expressed(text, "returned_same_day", True))


if __name__ == "__main__":
    unittest.main()
