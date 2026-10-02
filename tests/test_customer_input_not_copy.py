"""System-wide: customer responses are input, not letter copy.

Covers extraction → provenance → pack → validation across payment, breakdown,
residential, multiple visits, children, accessibility, negation, informal
wording, and adaptive-question prose answers.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.questioning import QuestionEngine, _is_prose_answer
from pcn_appeal.engines.validation import (
    ValidationEngine, _customer_prose_pasted, _copy_fingerprint,
)
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (
    CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact, FactSource,
    FactStatus, RetrievalPack, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline, render
from support import ReferenceAnalysisLLM


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


def _pack(module_ids, facts, ctx):
    return RetrievalPack(
        primary_route="BAY", secondary_routes=[], module_ids=module_ids,
        verified_facts=facts, fact_refs={k: f"F-{k}" for k in facts},
        missing_facts=[], evidence_refs=[], prohibited_claims=[],
        code_version=None, pofa_route="UNRESOLVED", pofa_findings=[],
        driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[], lease_clauses=[], case_context=ctx,
    )


class ExtractionPreservesMeaning(unittest.TestCase):
    def _digest(self, breach: str, text: str):
        case = CaseFile("C")
        case.put(Fact("F-br", "alleged_breach", breach, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.raw_answers["narrative"] = text
        return case, assess_material_account(case)

    def test_payment_attempt_not_asserted_as_paid(self):
        case, dig = self._digest(
            "Parked without payment",
            "I tried paying on the app but it wouldn't go through.",
        )
        self.assertTrue(case.get("payment_attempt_failed"), dig)
        self.assertFalse(case.get("payment_made"))
        props = " ".join(dig["propositions"]).lower()
        self.assertIn("attempt", props)
        self.assertNotIn("wouldn't go through", props)
        self.assertNotIn("i tried", props)

    def test_payment_made_informal(self):
        case, dig = self._digest("No payment", "I paid via the app for the visit.")
        self.assertTrue(case.get("payment_made"))
        self.assertTrue(any("payment" in p.lower() for p in dig["propositions"]))

    def test_breakdown(self):
        case, dig = self._digest(
            "Overstay", "Car broke down flat battery — couldn't leave the car park.")
        self.assertTrue(case.get("vehicle_immobilised"))
        self.assertTrue(case.get("immobilisation_prevented_departure"))
        self.assertNotIn("couldn't leave", " ".join(dig["propositions"]).lower())

    def test_residential(self):
        case, dig = self._digest(
            "No permit", "I live heer in the blok this is our allocated bay.")
        self.assertTrue(case.get("resident_connection_stated"))

    def test_multiple_visits(self):
        case, dig = self._digest(
            "Overstay", "We left and came back later — two seperate visits.")
        # P2: a hypothesis to confirm, not a fact (test_p2_narrative_hypotheses).
        self.assertIsNone(case.get("multiple_visits"))
        self.assertEqual(case.fact_hypotheses[0]["possible_value"], True)

    def test_children_present(self):
        case, dig = self._digest(
            "Parent and Child bay without child",
            "Left kids in the car. Child remained in the vehicle.")
        self.assertTrue(case.get("child_occupant_present"))
        self.assertTrue(dig["contradicts"])
        self.assertNotIn("left kids", " ".join(dig["propositions"]).lower())

    def test_children_absent_negation(self):
        case, dig = self._digest(
            "Parent and Child bay without child",
            "There were no children in the vehicle.")
        self.assertFalse(case.get("child_occupant_present"))
        self.assertFalse(dig["contradicts"])

    def test_children_unknown(self):
        case, dig = self._digest(
            "Parent and Child bay without child",
            "Not sure if a child was with the vehicle.")
        self.assertFalse(case.get("child_occupant_present"))

    def test_accessibility_blue_badge(self):
        case, dig = self._digest(
            "Disabled bay",
            "A blue badge was displayed; extra time needed for disability reasons.")
        self.assertTrue(case.get("blue_badge_displayed"))
        self.assertTrue(case.get("disability_extra_time"))

    def test_multiple_facts_one_narrative(self):
        case, dig = self._digest(
            "Parent and Child bay without child",
            "Kids were in the car and I was trying to find somewhere to park.")
        self.assertTrue(case.get("child_occupant_present"))
        self.assertTrue(case.get("seeking_parking_space"))
        self.assertGreaterEqual(len(dig["propositions"]), 2)

    def test_provenance_retains_original(self):
        case, dig = self._digest(
            "Parent and Child bay", "Left the kids in the car while shopping.")
        self.assertTrue(case.free_text_provenance)
        self.assertIn("kids", case.free_text_provenance[0]["original"].lower())
        self.assertEqual(
            case.facts["child_occupant_present"].source.kind,
            SourceKind.CUSTOMER_FREE_TEXT,
        )


class AdaptiveAnswerProseNotLetterCopy(unittest.TestCase):
    def test_prose_answer_stored_as_input_flag(self):
        kg = KnowledgeGraph()
        case = CaseFile("C")
        case.pending_questions = [{
            "fact": "situation_detail", "type": "text",
            "text": "What happened?",
        }]
        QuestionEngine(kg).record_answer(
            case, "situation_detail",
            "I tried paying on the app but it wouldn't go through at all.",
        )
        self.assertEqual(
            case.raw_answers["situation_detail"],
            "I tried paying on the app but it wouldn't go through at all.",
        )
        self.assertTrue(case.get("situation_detail") is True)
        self.assertEqual(
            case.facts["situation_detail"].source.kind,
            SourceKind.CUSTOMER_FREE_TEXT,
        )
        assess_material_account(case)
        self.assertTrue(case.get("payment_attempt_failed"))

    def test_short_closed_answer_kept(self):
        self.assertFalse(_is_prose_answer("BPA"))
        self.assertTrue(_is_prose_answer(
            "I tried paying on the app but it wouldn't go through."))


class ValidatorPasteAndCoverage(unittest.TestCase):
    def test_blocks_pasted_phrase(self):
        raw = "I tried paying on the app but it wouldn't go through."
        pack = _pack(
            ["KB-PAY-01"],
            {"alleged_breach": "No payment", "operator_name": "Acme",
             "pcn_number": "P1", "vrm": "AB12CDE"},
            {"customer_source_texts": [raw], "alleged_breach": "No payment",
             "operator_name": "Acme"},
        )
        draft = Draft("C", [[
            DraftSentence(f"The keeper says: {raw}", [], ["KB-PAY-01"]),
            DraftSentence("The operator is requested to cancel.", [], ["KB-PAY-01"]),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertTrue(any(i.rule == "VAL-CUSTOMER-COPY" for i in result.issues), result.issues)

    def test_professional_rewrite_allowed(self):
        raw = "I tried paying on the app but it wouldn't go through."
        pack = _pack(
            ["KB-PAY-01"],
            {"alleged_breach": "No payment made", "operator_name": "Acme Parking",
             "pcn_number": "P1", "vrm": "AB12CDE", "payment_attempt_failed": True},
            {"customer_source_texts": [raw], "alleged_breach": "No payment made",
             "operator_name": "Acme Parking",
             "material_account_propositions": [
                 "an attempt to pay was unsuccessful because the payment facility "
                 "did not work as required",
             ]},
        )
        draft = Draft("C", [[
            DraftSentence(
                "An attempt was made to pay through the app, but the payment "
                "could not be completed.",
                ["F-payment_attempt_failed"], ["KB-PAY-01"]),
            DraftSentence(
                "The operator is requested to reconcile its transaction records "
                "for PCN P1 regarding vehicle AB12CDE.",
                ["F-pcn_number", "F-vrm"], ["KB-PAY-01"]),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertFalse(any(i.rule == "VAL-CUSTOMER-COPY" for i in result.issues), result.issues)

    def test_pcn_vrm_not_treated_as_paste(self):
        raw = "My PCN is 45000564251 and VRM is RX75VPP on the notice."
        letter = (
            "I write as the registered keeper of vehicle RX75VPP in respect of "
            "Parking Charge Notice 45000564251. The operator is requested to cancel."
        )
        # Shared identifiers alone must not trip paste detection.
        self.assertFalse(_customer_prose_pasted(
            "45000564251", letter,
            {"pcn_number": "45000564251", "vrm": "RX75VPP"}))

    def test_justified_quotation_allowed(self):
        quote = "the machine displayed error code E42"
        pack = _pack(
            ["KB-PAY-02"],
            {"alleged_breach": "No payment", "operator_name": "Acme",
             "pcn_number": "P1", "vrm": "AB12CDE"},
            {
                "customer_source_texts": [f"Honestly {quote} when I tried to pay"],
                "customer_quotations": [{
                    "text": quote,
                    "reason": "exact machine error code is material to the payment attempt",
                }],
                "alleged_breach": "No payment", "operator_name": "Acme",
            },
        )
        draft = Draft("C", [[
            DraftSentence(
                f'The payment facility displayed the message "{quote}".',
                [], ["KB-PAY-02"]),
            DraftSentence(
                "The operator is requested to produce fault records for PCN P1.",
                ["F-pcn_number"], ["KB-PAY-02"]),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertFalse(any(i.rule in ("VAL-CUSTOMER-COPY", "VAL-RES")
                             for i in result.issues), result.issues)

    def test_fingerprint_ignores_short_tokens(self):
        self.assertEqual(_copy_fingerprint("OK"), "")


class PackWithholdsProse(unittest.TestCase):
    def test_free_text_string_not_in_verified_facts(self):
        llm = ReferenceAnalysisLLM({
            "extraction": [{
                "fields": fields(
                    operator_name="Acme", pcn_number="P1", vrm="AB12CDE",
                    parking_location="Car Park", site_postcode="M1 1AA",
                    parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                    charge_amount="£100", alleged_breach="No payment made",
                    operator_ata="BPA",
                ),
                "doc_types": {"E1": "PCN"},
            }],
        })
        case = CaseFile("C-PAY", evidence={
            "E1": EvidenceItem("E1", "PCN", "p.pdf", text="No payment",
                               images=[b"a", b"b"])})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        raw = "I tried paying on the app but it wouldn't go through."
        pipe.confirm(case, {}, list(case.facts), raw)
        out = pipe.generate(case)
        # Raw wording must not appear as a verified string fact.
        for k, v in (out.pack.verified_facts or {}).items():
            if isinstance(v, str):
                self.assertNotIn("wouldn't go through", v.lower())
                self.assertNotIn("i tried paying", v.lower())
        self.assertTrue(out.pack.case_context.get("customer_source_texts"))
        self.assertIn("payment_attempt_failed",
                      out.pack.case_context.get("customer_reported_facts") or []
                      or list(out.pack.verified_facts))
        # Render uses validated draft text only — not raw answers appended.
        if out.letter:
            self.assertNotIn("wouldn't go through", out.letter.lower())
            self.assertEqual(out.letter, render(out.draft))


class DriverIdentityNotIntroduced(unittest.TestCase):
    def test_first_person_driving_blocked(self):
        pack = _pack(
            ["KB-BAY-02"],
            {"alleged_breach": "Parent and Child bay", "operator_name": "Acme",
             "pcn_number": "P1", "vrm": "AB12CDE"},
            {"alleged_breach": "Parent and Child bay", "operator_name": "Acme"},
        )
        draft = Draft("C", [[
            DraftSentence("I parked in the bay with my children.", [], ["KB-BAY-02"]),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertTrue(any(i.rule == "VAL-DRIVER" for i in result.issues))


if __name__ == "__main__":
    unittest.main()
