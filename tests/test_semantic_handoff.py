"""Input → semantic → FactManager → knowledge handoff regressions."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
from pcn_appeal.engines.questioning import QuestionEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (
    CaseFile, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.semantics import extract_and_promote, extract_concepts
from pcn_appeal.semantics.state import (
    FACT_CONFLICT, SEMANTIC_OWNED_FACTS, handoff_ready, open_material_fact_conflicts,
)
from test_scenarios import make_case


SHOPPING_PURSE = (
    "I attended for shopping. I realised I had forgotten my purse at home, "
    "so the vehicle left the site. I returned later the same day. "
    "These were two separate visits."
)


def _base(**extra):
    f = dict(
        operator_name="Northbridge Parking Ltd", pcn_number="NB900100",
        vrm="XY12ZAB", parking_location="Northbridge Retail",
        site_postcode="LS1 1AA", parking_event_date="22/04/2026",
        notice_issue_date="25/04/2026", charge_amount="£100",
        alleged_breach="Overstayed paid time", operator_ata="BPA",
        entry_time="11:50:58", exit_time="18:15:21",
    )
    f.update(extra)
    return f


class SemanticHandoffMultipleVisits(unittest.TestCase):
    def test_shopping_purse_concepts_and_facts(self):
        case = CaseFile("C-HANDOFF-MV")
        case.raw_answers["narrative"] = SHOPPING_PURSE
        out = assess_material_account(case)
        concepts = {c["concept"]: c["polarity"]
                    for c in json.loads(case.raw_answers["_semantic_concepts"])}
        self.assertEqual(concepts.get("SHOPPING"), "AFFIRMED")
        self.assertEqual(concepts.get("LEFT_SITE"), "AFFIRMED")
        self.assertEqual(concepts.get("RETURNED"), "AFFIRMED")
        self.assertEqual(concepts.get("MULTIPLE_VISITS"), "AFFIRMED")
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))
        self.assertTrue(case.get("multiple_visits"))
        self.assertEqual(case.get("purpose_of_visit"), "shopping")
        self.assertTrue(case.get("departure_reason"))
        state = json.loads(case.raw_answers["_semantic_case_state"])
        cust = [t for t in state["timeline"] if t.get("attribution") == "CUSTOMER_ACCOUNT"]
        labels = [t["label"] for t in cust]
        self.assertEqual(labels[:4], ["visit", "departure", "return", "second_visit"])
        rel_preds = {r["predicate"] for r in state["relationships"]}
        self.assertIn("CAUSES", rel_preds)
        self.assertIn("PRECEDES", rel_preds)
        self.assertIn("SUPPORTS", rel_preds)
        ok, reasons = handoff_ready(case)
        self.assertTrue(ok, reasons)
        self.assertIsNotNone(out.get("semantic"))

    def test_kb_anpr_01_supported_from_authoritative_facts(self):
        case, pipe = make_case(_base())
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), SHOPPING_PURSE)
        self.assertTrue(case.get("multiple_visits"))
        match = KnowledgeMatcher(pipe.kg).match(case)
        cand = match.candidates.get("KB-ANPR-01")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.status, "SUPPORTED", cand.as_dict())

    def test_operator_span_not_collapsed_to_one_visit(self):
        case = CaseFile("C-SPAN")
        case.put(Fact("F-entry", "entry_time", "11:50:58", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "doc")))
        case.put(Fact("F-exit", "exit_time", "18:15:21", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "doc")))
        case.put(Fact("F-date", "parking_event_date", "22/04/2026", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "doc")))
        case.raw_answers["narrative"] = SHOPPING_PURSE
        assess_material_account(case)
        state = json.loads(case.raw_answers["_semantic_case_state"])
        kinds = {e["kind"] for e in state["events"]}
        self.assertIn("DEPARTURE", kinds)
        self.assertIn("RETURN", kinds)
        op = [t for t in state["timeline"] if t.get("kind") == "OPERATOR_OBSERVED_SPAN"]
        self.assertEqual(len(op), 1)
        self.assertEqual(op[0]["attribution"], "OPERATOR_ALLEGATION")
        self.assertTrue(case.get("multiple_visits"))


class SemanticHandoffAuthority(unittest.TestCase):
    def test_circumstance_rule_does_not_write_ontology_facts(self):
        case = CaseFile("C-AUTH")
        case.raw_answers["narrative"] = "I paid for parking via the app"
        assess_material_account(case)
        node = case.facts.get("payment_made")
        self.assertIsNotNone(node)
        self.assertTrue(str(node.source.ref).startswith("semantic:"), node.source.ref)

    def test_prose_answer_does_not_direct_put(self):
        kg = KnowledgeGraph()
        qe = QuestionEngine(kg)
        case = CaseFile("C-PROSE")
        case.pending_questions = [{"fact": "left_site", "type": "text"}]
        qe.record_answer(case, "left_site",
                         "Yes, the vehicle left the site and came back later")
        self.assertIsNone(case.get("left_site"))
        self.assertIn("left_site", case.raw_answers)
        assess_material_account(case)
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))

    def test_visit_conflict_not_last_write_wins(self):
        case = CaseFile("C-CONFLICT")
        case.raw_answers["narrative"] = SHOPPING_PURSE
        assess_material_account(case)
        case.put(Fact("F-mv", "multiple_visits", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        from pcn_appeal.semantics.state import record_material_conflicts
        record_material_conflicts(case)
        conflicts = open_material_fact_conflicts(case)
        self.assertTrue(conflicts)
        self.assertEqual(conflicts[0]["status"], FACT_CONFLICT)
        ok, reasons = handoff_ready(case)
        self.assertFalse(ok)
        self.assertIn("unresolved_material_fact_conflict", reasons)


class SemanticHandoffNarratives(unittest.TestCase):
    def test_payment_narrative(self):
        concepts = extract_concepts(["I paid at the machine for my parking"])
        names = {c.concept for c in concepts if c.polarity == "AFFIRMED"}
        self.assertIn("PAYMENT_MADE", names)
        case = CaseFile("C-PAY")
        extract_and_promote(case, ["I paid at the machine for my parking"])
        self.assertTrue(case.get("payment_made"))

    def test_breakdown_narrative(self):
        case = CaseFile("C-BD")
        extract_and_promote(case, ["The engine cut out and we could not move"])
        self.assertTrue(case.get("vehicle_immobilised"))

    def test_keying_error(self):
        case = CaseFile("C-KEY")
        extract_and_promote(case, ["I mistyped the registration plate when paying"])
        self.assertEqual(case.get("keying_error_type"), "MINOR")

    def test_negated_fact(self):
        concepts = extract_concepts(["I did not leave the site at any point"])
        left = [c for c in concepts if c.concept == "LEFT_SITE"]
        self.assertTrue(left)
        self.assertEqual(left[0].polarity, "NEGATED")
        case = CaseFile("C-NEG")
        extract_and_promote(case, ["I did not leave the site at any point"])
        self.assertNotEqual(case.get("left_site"), True)

    def test_uncertain_fact(self):
        case = CaseFile("C-UNC")
        extract_and_promote(case, ["Maybe I left the site, not sure"])
        self.assertNotEqual(case.get("left_site"), True)

    def test_contradictory_later_answer(self):
        case = CaseFile("C-LATER")
        case.raw_answers["narrative"] = SHOPPING_PURSE
        assess_material_account(case)
        self.assertTrue(case.get("multiple_visits"))
        case.put(Fact("F-mv", "multiple_visits", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        from pcn_appeal.semantics.state import record_material_conflicts
        record_material_conflicts(case)
        self.assertTrue(open_material_fact_conflicts(case))

    def test_semantic_owned_set_covers_ontology(self):
        self.assertIn("left_site", SEMANTIC_OWNED_FACTS)
        self.assertIn("multiple_visits", SEMANTIC_OWNED_FACTS)
        self.assertIn("payment_made", SEMANTIC_OWNED_FACTS)


if __name__ == "__main__":
    unittest.main()
