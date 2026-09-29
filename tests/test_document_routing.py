"""Engine 0: which documents may enter the private parking appeal pipeline.

Routing runs on the classifier's labels before case intelligence, fact recovery,
question generation or grounds analysis. These tests hold that boundary from both
sides: an out-of-scope document must never reach the appeal path, and a valid
notice must never be refused by it.

Nothing here asserts on a specific operator or a specific charge. Each case is
defined by its document class, which is the only thing routing is allowed to
decide on.

Run:  python -m unittest tests.test_document_routing -v
"""
from __future__ import annotations

import unittest

from pcn_appeal.models import CaseFile, CaseState, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.rules import scope
from support import ReferenceAnalysisLLM

NOTICE_FIELDS = dict(
    operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12CDE",
    parking_location="Retail Park", site_postcode="M1 1AA",
    parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
    charge_amount="£100", alleged_breach="Overstayed paid time",
    operator_ata="BPA",
)


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


def case_of(doc_class: str, *, extracted=None, text="", filename="upload.pdf"):
    """One document of `doc_class`, plus whatever the extractor read from it.

    `extracted` defaults to nothing: an out-of-scope document has no parking
    fields to read, and routing must not depend on any being present.
    """
    llm = ReferenceAnalysisLLM(
        {"extraction": [{"fields": fields(**(extracted or {})),
                         "doc_types": {"E1": doc_class}}]})
    case = CaseFile("C-ROUTE", evidence={"E1": EvidenceItem("E1", "OTHER", filename, text=text)})
    return case, AppealPipeline(llm)


class OutOfScopeDocumentsStop(unittest.TestCase):
    """Each of these closes the appeal route: no grounds, no questions, no letter."""

    def _assert_stopped(self, doc_class: str, expected_code: str):
        case, pipe = case_of(doc_class)
        result = pipe.auto_appeal(case, "I want to appeal this")

        self.assertEqual(result.state, CaseState.NO_APPEAL_RIGHT, doc_class)
        self.assertEqual(result.stop_code, expected_code, doc_class)
        self.assertIsNone(result.output, "an out-of-scope document must not draft")
        self.assertEqual(result.questions, [], "a stop must not ask anything")
        self.assertEqual(case.analysis_module_ids, [], "no grounds may be selected")
        self.assertEqual(case.pending_questions, [])
        self.assertTrue(result.stop_reason, "the customer needs an explanation")
        self.assertTrue(result.recommendation, "and somewhere to go instead")
        # Nothing downstream of routing may have run.
        events = {a.get("event") for a in case.audit}
        self.assertNotIn("case_analysis", events, doc_class)
        self.assertNotIn("analysis_round", events, doc_class)
        self.assertNotIn("retrieval_pack", events, doc_class)
        return result

    def test_debt_recovery_letter_stops(self):
        r = self._assert_stopped("DEBT_RECOVERY", "DEBT_RECOVERY")
        self.assertIn("debt recovery", r.stop_reason.lower())
        self.assertEqual(r.cta_action, "DEBT_RECOVERY_TEMPLATE")
        self.assertIn("template", (r.cta_label or "").lower())

    def test_council_pcn_stops_and_names_the_right_service(self):
        r = self._assert_stopped("COUNCIL_PCN", "COUNCIL_PCN")
        self.assertIn("council", r.stop_reason.lower())
        self.assertEqual(r.cta_action, "COUNCIL_PCN_SERVICE")
        # The stop that used to be shared with debt recovery must not say so.
        self.assertNotIn("debt recovery", r.stop_reason.lower())
        self.assertNotIn("debt recovery", r.recommendation.lower())

    def test_court_claim_stops(self):
        r = self._assert_stopped("COURT_CLAIM", "COURT_CLAIM")
        self.assertIn("court", r.stop_reason.lower())

    def test_out_of_stage_notice_stops(self):
        r = self._assert_stopped("OUT_OF_STAGE", "OUT_OF_STAGE")
        self.assertIn("appeal", r.stop_reason.lower())

    def test_unidentifiable_document_stops(self):
        """Nothing identifiable as a private parking notice: stop and say so
        rather than hunt for grounds in a document we could not read."""
        r = self._assert_stopped("OTHER", "UNSUPPORTED")
        self.assertIn("parking notice", r.stop_reason.lower())

    def test_a_stop_survives_the_customer_pressing_on(self):
        """Routing is re-checked on resume, so a second call cannot draft."""
        case, pipe = case_of("DEBT_RECOVERY")
        pipe.auto_appeal(case, "")
        again = pipe.auto_appeal(case, "please just try")
        self.assertEqual(again.state, CaseState.NO_APPEAL_RIGHT)
        self.assertIsNone(again.output)
        out = pipe.generate(case)
        self.assertIsNone(out.letter, "generate() must refuse a stopped case outright")


class ValidNoticesProceed(unittest.TestCase):

    def test_each_private_notice_class_enters_the_pipeline(self):
        for doc_class in ("PCN", "NTK", "NTD"):
            with self.subTest(doc_class=doc_class):
                case, pipe = case_of(doc_class, extracted=NOTICE_FIELDS)
                pipe.auto_appeal(case, "the letter arrived weeks after the visit")
                self.assertNotEqual(case.state, CaseState.NO_APPEAL_RIGHT)
                self.assertIsNone(scope.decide(case))

    def test_supporting_evidence_beside_a_notice_does_not_stop_the_case(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**NOTICE_FIELDS),
                             "doc_types": {"E1": "NTK", "E2": "RECEIPT"}}]})
        case = CaseFile("C-MIX", evidence={
            "E1": EvidenceItem("E1", "OTHER", "notice.pdf", text="Notice to Keeper"),
            "E2": EvidenceItem("E2", "OTHER", "receipt.jpg", text="Receipt"),
        })
        pipe = AppealPipeline(llm)
        pipe.auto_appeal(case, "I have the shop receipt")
        self.assertIsNone(scope.decide(case))

    def test_a_mislabelled_notice_is_not_refused(self):
        """Routing may only ever open the appeal path on this evidence, never
        close it: a notice the classifier called OTHER still carries the charge's
        identifying fields, and stranding that customer would be our error."""
        case, pipe = case_of("OTHER", extracted=NOTICE_FIELDS)
        pipe.ingest(case)
        self.assertEqual(case.document_classes, {"E1": "OTHER"})
        self.assertIsNone(scope.decide(case))


class TheSafetyValveIsNotAWayRound(unittest.TestCase):
    """The valve above opens the appeal path for a document the classifier could
    not label but that carries a charge's fields. A debt-recovery letter carries
    those same fields - it quotes the PCN number and the amount - so the valve is
    conditioned on the backup debt check finding nothing."""

    def test_a_debt_letter_labelled_other_still_stops(self):
        case, pipe = case_of(
            "OTHER", extracted=NOTICE_FIELDS,
            text="Our client has passed to debt recovery. The right to appeal has now expired.")
        result = pipe.auto_appeal(case, "can I still appeal")
        self.assertEqual(result.stop_code, "DEBT_RECOVERY")
        self.assertEqual(result.state, CaseState.NO_APPEAL_RIGHT)
        self.assertIsNone(result.output)

    def test_the_valve_still_opens_when_there_are_no_debt_signals(self):
        case, pipe = case_of("OTHER", extracted=NOTICE_FIELDS,
                             text="Parking Charge Notice. You may appeal within 28 days.")
        pipe.ingest(case)
        self.assertFalse(scope.has_debt_signals(case))
        self.assertIsNone(scope.decide(case))


class AFailedClassificationIsATechnicalError(unittest.TestCase):
    """No labels at all is our classifier failing, not a verdict on the document.
    It must not proceed: skipping this gate skips the only check that keeps a debt
    letter or a council PCN out of the appeal path. And it must not be reported as
    a refusal, because the customer did nothing wrong and can retry."""

    def test_no_labels_stops_with_its_own_code(self):
        case = CaseFile("C-NONE", evidence={"E1": EvidenceItem("E1", "OTHER", "n.pdf", text="x")})
        self.assertEqual(case.document_classes, {})
        stop = scope.decide(case)
        self.assertIsNotNone(stop, "an unclassified document must never proceed")
        self.assertEqual(stop.code, "CLASSIFICATION_FAILED")
        self.assertIn(stop.code, scope.TECHNICAL_STOPS)

    def test_it_is_a_distinct_state_from_having_no_appeal_right(self):
        llm = ReferenceAnalysisLLM({"extraction": [{"fields": fields(**NOTICE_FIELDS),
                                                    "doc_types": {}}]})
        case = CaseFile("C-FAIL", evidence={"E1": EvidenceItem("E1", "OTHER", "n.pdf", text="x")})
        result = AppealPipeline(llm).auto_appeal(case, "please appeal this")

        self.assertEqual(result.state, CaseState.CLASSIFICATION_FAILED)
        self.assertNotEqual(result.state, CaseState.NO_APPEAL_RIGHT)
        self.assertIsNone(result.output, "nothing may be drafted without a classification")
        self.assertEqual(result.questions, [])
        self.assertEqual(case.analysis_module_ids, [])
        self.assertTrue(result.stop_reason)
        self.assertTrue(result.recommendation)
        events = {a.get("event") for a in case.audit}
        self.assertIn("classification_failed", events)
        self.assertNotIn("no_appeal_right", events)
        self.assertNotIn("case_analysis", events)

    def test_the_customer_is_not_blamed_for_it(self):
        stop = scope.STOPS["CLASSIFICATION_FAILED"]
        self.assertNotIn("unsupported", stop.message.lower())
        self.assertNotIn("we couldn't read this as", stop.message.lower())
        self.assertTrue(stop.cta_action, "a technical stop must offer a retry")


class ClassificationPrecedence(unittest.TestCase):

    def test_the_more_advanced_document_decides(self):
        """A claim form alongside the original notice means the appeal stage has
        passed, however appealable the notice looks on its own."""
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**NOTICE_FIELDS),
                             "doc_types": {"E1": "NTK", "E2": "COURT_CLAIM"}}]})
        case = CaseFile("C-ADV", evidence={
            "E1": EvidenceItem("E1", "OTHER", "notice.pdf", text="Notice to Keeper"),
            "E2": EvidenceItem("E2", "OTHER", "claim.pdf", text="Claim Form"),
        })
        pipe = AppealPipeline(llm)
        result = pipe.auto_appeal(case, "")
        self.assertEqual(result.stop_code, "COURT_CLAIM")

    def test_every_stop_class_has_a_message_and_a_route_onward(self):
        for code in scope.STOP_ORDER + ("UNSUPPORTED",):
            with self.subTest(code=code):
                stop = scope.STOPS[code]
                self.assertTrue(stop.message.strip())
                self.assertTrue(stop.recommendation.strip())
                self.assertTrue(stop.cta_action, "a stop must offer somewhere to go")
                for text in (stop.message, stop.recommendation, stop.cta_label or ""):
                    self.assertNotRegex(text, r"KB-|SCOP-|NOT_APPLICABLE|_stage\b",
                                        "customer wording must carry no internal code")


class DebtRecoveryFromWordingAlone(unittest.TestCase):
    """The classifier is the authority, but a plain-text demand must still stop
    even where the label came back as something benign."""

    def test_recovery_wording_reclassifies_the_document(self):
        text = ("FINAL NOTICE\nYour account has been passed to debt recovery.\n"
                "The right to appeal has now expired.")
        case, pipe = case_of("OTHER", text=text)
        result = pipe.auto_appeal(case, "")
        self.assertEqual(result.stop_code, "DEBT_RECOVERY")
        self.assertEqual(case.document_classes["E1"], "DEBT_RECOVERY")

    def test_a_breakdown_report_is_not_a_debt_demand(self):
        """RECOVERY_REPORT is a tow/breakdown report and ordinary evidence."""
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**NOTICE_FIELDS),
                             "doc_types": {"E1": "NTK", "E2": "RECOVERY_REPORT"}}]})
        case = CaseFile("C-TOW", evidence={
            "E1": EvidenceItem("E1", "OTHER", "notice.pdf", text="Notice to Keeper"),
            "E2": EvidenceItem("E2", "OTHER", "rac.pdf",
                               text="Roadside assistance report: vehicle recovered"),
        })
        pipe = AppealPipeline(llm)
        pipe.auto_appeal(case, "the car broke down")
        self.assertIsNone(scope.decide(case))


if __name__ == "__main__":
    unittest.main()
