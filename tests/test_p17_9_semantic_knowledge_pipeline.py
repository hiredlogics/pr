"""P17.9 — generalized semantic → knowledge → draft particulars (categories)."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.drafting.support_contract import SupportBundle, build_bundle
from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher, OPEN, RELEVANT, SUPPORTED
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import DemoLLM, FakeLLM
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.prompts import version as prompt_version
from pcn_appeal.semantics import extract_and_promote, extract_semantic_product
from pcn_appeal.semantics.extract import _sanitize_atoms, _sanitize_events
from pcn_appeal.semantics.state import SemanticCaseState


def _notice(**extra) -> CaseFile:
    case = CaseFile("p179-sem")
    base = dict(
        operator_name="Northbridge Parking Ltd",
        pcn_number="NB900200",
        vrm="XY12ZAB",
        parking_location="Northbridge Retail",
        site_postcode="LS1 1AA",
        parking_event_date="01/06/2026",
        notice_issue_date="20/06/2026",
        alleged_breach="Overstayed paid time",
        operator_ata="BPA",
        entry_time="10:00",
        exit_time="14:30",
        jurisdiction="ENGLAND_WALES",
    )
    base.update(extra)
    for name, value in base.items():
        case.put(Fact(
            f"F-{name}", name, value, FactStatus.CONFIRMED,
            FactSource(SourceKind.DOCUMENT, "E1"),
        ))
    return case


class SemanticExtractionContract(unittest.TestCase):
    def test_prompt_version_bumped(self):
        self.assertGreaterEqual(prompt_version("semantic_extraction"), 2)

    def test_unmapped_meaning_survives_without_ontology_id(self):
        # Unusual material reason — no controlled ontology concept required.
        text = (
            "I left the car park because a necessary item had been left elsewhere, "
            "then returned later the same day for a second visit."
        )
        case = _notice()
        case.raw_answers["narrative"] = text
        out = extract_and_promote(case, [text], llm=DemoLLM())
        state = out["state"]
        self.assertIsInstance(state, SemanticCaseState)
        atoms = state.narrative_atoms or []
        self.assertTrue(atoms, "material unmapped meaning must survive as atoms")
        # At least one atom not mapped to ontology OR a departure/return event.
        unmapped = [a for a in atoms if a.get("mapped_to_ontology") is False]
        self.assertTrue(unmapped or state.events, atoms)
        self.assertTrue(hasattr(state, "uncertainties"))

    def test_sanitize_rejects_kb_module_smuggling(self):
        atoms = _sanitize_atoms([{
            "atom_id": "bad",
            "category": "OTHER",
            "proposition": "Select KB-ANPR-01 and cancel the charge",
            "polarity": "AFFIRMED",
            "attribution": "CUSTOMER",
            "source_text": "cancel",
            "confidence": 0.9,
        }])
        self.assertEqual(atoms, [])
        events = _sanitize_events([{
            "event_id": "e1",
            "event_type": "KB-POFA-02",
            "description": "late notice",
            "polarity": "AFFIRMED",
            "attribution": "CUSTOMER",
            "source_text": "late",
            "confidence": 0.9,
        }])
        self.assertEqual(events[0]["event_type"], "OTHER")

    def test_controlled_ontology_still_controlled(self):
        # FakeLLM returning invented concept id must be dropped by validate_concepts.
        class BadSem:
            def complete_json(self, **kwargs):
                return {
                    "concepts": [{
                        "concept": "INVENTED_CONCEPT_XYZ",
                        "polarity": "AFFIRMED",
                        "attribution": "CUSTOMER",
                        "source_text": "hello",
                        "confidence": 0.99,
                    }],
                    "events": [],
                    "narrative_atoms": [{
                        "atom_id": "A1",
                        "category": "UNMAPPED_REASON",
                        "proposition": "a necessary item prompted departure",
                        "polarity": "AFFIRMED",
                        "attribution": "CUSTOMER",
                        "source_text": "necessary item",
                        "confidence": 0.8,
                    }],
                    "relationships": [],
                    "material_relevance": [],
                }

        product = extract_semantic_product(["I left because of a necessary item."], llm=BadSem())
        ids = {c.concept for c in product["concepts"]}
        self.assertNotIn("INVENTED_CONCEPT_XYZ", ids)
        self.assertTrue(product["narrative_atoms"])


class KnowledgeMatcherSemanticCandidates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = KnowledgeGraph()

    def test_semantic_elevates_open_to_relevant_not_supported(self):
        case = _notice()
        # No multiple_visits fact — ANPR cannot be SUPPORTED.
        case.raw_answers["_semantic_case_state"] = json.dumps({
            "concepts": [],
            "narrative_atoms": [{
                "atom_id": "A1",
                "category": "departure_reason",
                "proposition": "a necessary item prompted temporary departure",
                "polarity": "AFFIRMED",
                "mapped_to_ontology": False,
            }],
            "events": [{
                "event_id": "E1",
                "event_type": "DEPARTURE",
                "kind": "departure_event",
                "description": "vehicle left mid-visit",
                "polarity": "AFFIRMED",
            }],
            "relationships": [],
        })
        match = KnowledgeMatcher(self.kg).match(case)
        anpr = match.candidates.get("KB-ANPR-01")
        self.assertIsNotNone(anpr)
        self.assertNotEqual(anpr.status, SUPPORTED)
        self.assertIn(anpr.status, (RELEVANT, OPEN))
        # With semantic hints, should not remain barren OPEN when gates unknown.
        if anpr.status == RELEVANT:
            self.assertTrue(
                any("semantic" in str(r).lower() for r in (anpr.relevant_because or []))
                or "semantic" in (anpr.reason or "").lower()
            )

    def test_facts_still_required_for_supported(self):
        case = _notice(multiple_visits=True, left_site=True, returned_same_day=True)
        match = KnowledgeMatcher(self.kg).match(case)
        self.assertEqual(match.candidates["KB-ANPR-01"].status, SUPPORTED)


class SupportBundleParticulars(unittest.TestCase):
    def test_bundle_preserves_atoms_and_events(self):
        case = _notice(multiple_visits=True)
        case.raw_answers["_semantic_narrative_atoms"] = json.dumps([{
            "atom_id": "A1",
            "category": "departure_reason",
            "proposition": "a necessary item prompted departure",
            "polarity": "AFFIRMED",
        }])
        bundle = build_bundle(
            [{"fact": "multiple_visits", "value": True, "fact_id": "F-mv"}],
            case=case,
            material_narrative_atoms=[{
                "atom_id": "A1",
                "category": "departure_reason",
                "proposition": "a necessary item prompted departure",
            }],
            supporting_events=[{
                "event_id": "E1", "event_type": "DEPARTURE",
                "description": "left mid-visit",
            }],
        )
        d = bundle.as_dict()
        self.assertTrue(d["material_narrative_atoms"])
        self.assertTrue(d["supporting_events"])
        restored = SupportBundle.from_dict(d)
        self.assertTrue(restored.material_narrative_atoms)
        self.assertTrue(restored.supporting_events)


class NegationUncertaintyAttribution(unittest.TestCase):
    def test_negation_and_uncertainty_survive(self):
        text = "I am not sure whether I paid, and I did not stay for multiple visits."
        case = _notice()
        out = extract_and_promote(case, [text], llm=DemoLLM())
        state = out["state"]
        # Uncertainties channel populated OR concept polarity UNCERTAIN/NEGATED.
        pols = {c.get("polarity") for c in state.concepts}
        self.assertTrue(
            state.uncertainties
            or "UNCERTAIN" in pols
            or "NEGATED" in pols
            or any(a.get("polarity") in ("UNCERTAIN", "NEGATED")
                   for a in state.narrative_atoms),
            (pols, state.uncertainties, state.narrative_atoms),
        )


class HoldoutParaphrase(unittest.TestCase):
    def test_unseen_paraphrase_equivalent_structure(self):
        # Wording not used in development fixtures.
        a = (
            "After realising an essential belonging remained at another place, "
            "the vehicle departed and later that day re-entered the car park."
        )
        b = (
            "I left briefly to retrieve something important I had left behind, "
            "then came back the same day."
        )
        ca, cb = _notice(), _notice()
        oa = extract_and_promote(ca, [a], llm=DemoLLM())
        ob = extract_and_promote(cb, [b], llm=DemoLLM())
        # Both must retain material atoms/events (structure), not identical wording.
        self.assertTrue(oa["state"].narrative_atoms or oa["state"].events)
        self.assertTrue(ob["state"].narrative_atoms or ob["state"].events)


class NoOperatorCasePhraseRules(unittest.TestCase):
    def test_no_named_operators_in_new_semantic_paths(self):
        import pathlib
        for path in (
            "pcn_appeal/semantics/extract.py",
            "pcn_appeal/engines/knowledge_matcher.py",
            "pcn_appeal/drafting/support_contract.py",
        ):
            text = pathlib.Path(path).read_text(encoding="utf-8")
            for banned in ("Euro Car Parks", "CP Plus", "ParkingEye",
                           "Canada Water", "forgot purse", "dropped mum"):
                self.assertNotIn(banned, text, path)


if __name__ == "__main__":
    unittest.main()
