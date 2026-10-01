"""Package A: seven defects where wrong or unsafe output could reach a customer.

Each class names the client issue it guards. All data is fictitious; nothing
here is specific to an operator, a notice or a customer.

  41/15  an analysis failure was told to the customer as "no supported grounds"
  20/21  PoFA was SATISFIED when the pass-to-driver wording was never seen
  25     an altered PCN or a misread VRM passed the integrity check
  28     an attempted payment was recorded as a payment made
  8      any written answer set the fact to True, including "No, ..."
  23/26  approved wording inferred the operator's record spanned an "interval"
  2      two different photos of the front counted as both sides
"""
from __future__ import annotations

import unittest

from pcn_appeal.disclosure import apply_disclosure
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.outcome import (OUTCOME_NO_SUPPORTED_GROUNDS, OUTCOME_PROCESSING_ERROR,
                                        analysis_failed, classify_hold)
from pcn_appeal.engines.questioning import QuestionEngine, answer_polarity
from pcn_appeal.engines.recovery import FactRecoveryEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact,
                               FactSource, FactStatus, SourceKind, ValidationResult)
from pcn_appeal.notice_completeness import assess_notice_sides, notice_pages_sufficient
from pcn_appeal.orchestrator import AppealPipeline

from support import ReferenceAnalysisLLM, analysis_llm
from test_case_intelligence_fixes import _pack

BAY_BREACH = "Parked in a Parent and Child bay without being accompanied by a child"
FIELDS = {
    k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
    for k, v in dict(operator_name="Example Parking Ltd", pcn_number="PCN000111",
                     vrm="AB12CDE", parking_location="Example Store car park",
                     site_postcode="M1 1AA", parking_event_date="01/06/2026",
                     notice_issue_date="03/06/2026", charge_amount="£100",
                     alleged_breach=BAY_BREACH, observation_time="12:23",
                     event_time="12:23", operator_ata="BPA").items()}


def _fact(name, value, status=FactStatus.DERIVED, kind=SourceKind.CALCULATION):
    return Fact(f"F-{name}", name, value, status, FactSource(kind, "t"))


# --------------------------------------------------------------------- 41/15
class AnalysisFailingLLM(ReferenceAnalysisLLM):
    """Every case_analysis call raises, as a provider outage or timeout would."""

    def complete_json(self, *, task, system, user, images=None):
        if task == "case_analysis":
            self.calls.append({"task": task, "user": user})
            raise TimeoutError("provider timed out")
        return super().complete_json(task=task, system=system, user=user, images=images)


class AnalysisFailureIsAProcessingError(unittest.TestCase):

    def _run(self, llm):
        pipe = AppealPipeline(llm)
        case = CaseFile("C-a1")
        case.evidence["E1"] = EvidenceItem("E1", "OTHER", "notice.txt",
                                           text="Notice to Keeper front\fReverse terms")
        pipe.ingest(case)
        pipe.confirm(case, {}, list(FIELDS), "I would like to appeal.")
        return case, pipe.generate(case)

    def test_failed_analysis_is_never_no_supported_grounds(self):
        llm = AnalysisFailingLLM({"extraction": [{"fields": FIELDS, "doc_types": {"E1": "NTK"}}]})
        case, out = self._run(llm)
        self.assertNotEqual(out.state, CaseState.RELEASED)
        self.assertEqual(out.outcome, OUTCOME_PROCESSING_ERROR)
        events = [a.get("event") for a in case.audit]
        self.assertNotIn("analysis_complete_no_supported_grounds", events)
        self.assertIn("case_analysis_error", events)

    def test_the_call_is_retried_once_before_failing(self):
        llm = AnalysisFailingLLM({"extraction": [{"fields": FIELDS, "doc_types": {"E1": "NTK"}}]})
        case, _ = self._run(llm)
        per_error = [a for a in case.audit if a.get("event") == "case_analysis_error"]
        calls = [c for c in llm.calls if c["task"] == "case_analysis"]
        self.assertEqual(len(calls), 2 * len(per_error))

    def test_a_transient_failure_recovers_on_retry(self):
        class FlakyOnce(ReferenceAnalysisLLM):
            failed = False

            def complete_json(self, *, task, system, user, images=None):
                if task == "case_analysis" and not self.failed:
                    self.failed = True
                    raise TimeoutError("once")
                return super().complete_json(task=task, system=system, user=user, images=images)

        llm = FlakyOnce({"extraction": [{"fields": FIELDS, "doc_types": {"E1": "NTK"}}]})
        case, out = self._run(llm)
        self.assertFalse(analysis_failed(case))
        self.assertNotEqual(out.outcome, OUTCOME_PROCESSING_ERROR)

    def test_classify_hold_reads_the_latest_attempt(self):
        def hold(events):
            case = CaseFile("C-h")
            case.state = CaseState.MANUAL_REVIEW
            case.audit = [{"event": e} for e in events]
            return classify_hold(case, _pack(module_ids=[]), ValidationResult(False, []))["outcome"]

        self.assertEqual(hold(["case_analysis_error", "analysis_complete_no_supported_grounds"]),
                         OUTCOME_PROCESSING_ERROR)
        self.assertEqual(hold(["case_analysis_error", "case_analysis_completed",
                               "analysis_complete_no_supported_grounds"]),
                         OUTCOME_NO_SUPPORTED_GROUNDS)
        self.assertEqual(hold(["case_analysis_completed", "analysis_complete_no_supported_grounds"]),
                         OUTCOME_NO_SUPPORTED_GROUNDS)


# --------------------------------------------------------------------- 20/21
NAME_ONLY_FACE = ("Parking Charge Notice. If you were not the driver, please tell us the "
                  "name and address of the driver.")


class PofaNeedsThePassOnWordingSeen(unittest.TestCase):

    def _case(self, text):
        case = CaseFile("C-pofa", evidence={"E1": EvidenceItem("E1", "NTK", "ntk.jpg", text=text)})
        case.document_classes["E1"] = "NTK"
        case.put(_fact("notice_route", "POSTAL"))
        case.put(_fact("jurisdiction", "ENGLAND_WALES"))
        apply_disclosure(case, False, source="t")
        return case

    def test_name_driver_flag_alone_is_not_satisfied(self):
        """A vision flag for the name-driver limb, no readable text: the pass-on
        limb was never seen, so nothing can be SATISFIED."""
        case = self._case("")
        case.put(_fact("ntk_invites_name_driver", True, FactStatus.EXTRACTED, SourceKind.DOCUMENT))
        FactRecoveryEngine(KnowledgeGraph()).recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "UNRESOLVED")

    def test_pass_on_flag_seen_is_satisfied(self):
        case = self._case("")
        case.put(_fact("ntk_invites_pass_to_driver", True, FactStatus.EXTRACTED, SourceKind.DOCUMENT))
        FactRecoveryEngine(KnowledgeGraph()).recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "SATISFIED")

    def test_no_text_no_flags_is_unresolved(self):
        case = self._case("")
        FactRecoveryEngine(KnowledgeGraph()).recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "UNRESOLVED")


# ------------------------------------------------------------------------ 25
class IdentifierIntegrity(unittest.TestCase):
    FACTS = {"pcn_number": "4401928375", "vrm": "RX7V5PP", "operator_name": "Example Parking Ltd",
             "alleged_breach": "Overstayed", "evidence_kinds": ["PCN"]}

    def _issues(self, *sentences):
        pack = _pack(verified_facts=dict(self.FACTS), module_ids=["KB-REC-01"])
        draft = Draft("C-id", [[DraftSentence(s, ["F-pcn"], ["STRUCTURAL"])] for s in sentences])
        return [i.message for i in ValidationEngine().validate(draft, pack).issues
                if i.rule == "VAL-CONFLICT"]

    def test_correct_identifiers_pass(self):
        self.assertEqual(self._issues(
            "I write regarding PCN 4401928375 issued to vehicle RX7 V5PP."), [])

    def test_altered_pcn_is_blocked(self):
        for wrong in ("4401928376", "4401928", "44019283750"):
            with self.subTest(pcn=wrong):
                self.assertTrue(self._issues(f"I write regarding PCN {wrong} for vehicle RX7V5PP."))

    def test_pcn_altered_alongside_the_correct_one_is_blocked(self):
        self.assertTrue(self._issues("I write regarding PCN 4401928375.",
                                     "The reference 4401928357 appears on the reminder."))

    def test_misread_vrm_of_any_shape_is_blocked(self):
        for wrong in ("RX7V5FP", "RX7 V5FP", "RX7V5P", "RX1V5PP"):
            with self.subTest(vrm=wrong):
                issues = self._issues(f"I write regarding PCN 4401928375 for vehicle {wrong}.")
                self.assertTrue(any("VRM" in m for m in issues), issues)

    def test_ordinary_words_and_dates_are_not_vrm_variants(self):
        self.assertEqual(self._issues(
            "I write regarding PCN 4401928375 for vehicle RX7V5PP on 12 May 2026 at 10:15.",
            "Section 8.1.2 of the Code applies, as does Schedule 4 paragraph 9."), [])


# ------------------------------------------------------------------------ 28
class PaymentAttemptIsNotPaymentMade(unittest.TestCase):

    def _facts(self, text):
        case = CaseFile("C-pay")
        case.put(Fact("F-alleged_breach", "alleged_breach", "No valid payment", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case.raw_answers["narrative"] = text
        result = assess_material_account(case)
        return case, result

    def test_attempts_do_not_set_payment_made(self):
        for text in ("I was paying at the machine when the warden came.",
                     "I tried to pay on the app but it failed.",
                     "I did not pay because the machine was broken.",
                     "I never paid as I could not find the machine."):
            with self.subTest(text=text):
                case, result = self._facts(text)
                self.assertNotIn("payment_made", case.facts)
                self.assertFalse(any("payment was made" in p for p in result["propositions"]))

    def test_completed_payments_still_set_payment_made(self):
        for text in ("I paid for parking on the app.", "I paid the parking at the machine."):
            with self.subTest(text=text):
                case, result = self._facts(text)
                self.assertIs(case.get("payment_made"), True)
                self.assertTrue(all("attempted" not in p for p in result["propositions"]))

    def test_question_vocabulary_no_longer_says_made_or_attempted(self):
        q = KnowledgeGraph().question_for("payment_made")
        self.assertNotIn("attempt", q["text"].lower())


# ------------------------------------------------------------------------- 8
class WrittenAnswersKeepTheirPolarity(unittest.TestCase):

    def _answer(self, text, qtype="text"):
        case = CaseFile("C-ans")
        case.pending_questions = [{"fact": "child_occupant_present", "type": qtype,
                                   "text": "Was a child travelling in the vehicle?"}]
        QuestionEngine(KnowledgeGraph()).record_answer(case, "child_occupant_present", text)
        return case

    def test_a_written_no_is_false(self):
        case = self._answer("No, the children were not in the car at that time.")
        self.assertIs(case.get("child_occupant_present"), False)

    def test_a_written_yes_is_true(self):
        case = self._answer("Yes, my two children were in the back of the car.")
        self.assertIs(case.get("child_occupant_present"), True)

    def test_uncertainty_sets_no_fact_but_keeps_the_answer(self):
        for qtype in ("text", "bool"):
            for text in ("Not sure, it was a long time ago and I cannot recall.", "not sure"):
                with self.subTest(qtype=qtype, text=text):
                    case = self._answer(text, qtype)
                    self.assertNotIn("child_occupant_present", case.facts)
                    self.assertEqual(case.raw_answers["child_occupant_present"], text)

    def test_a_bool_question_answered_yes_in_prose_is_true(self):
        self.assertIs(self._answer("Yes, they were", "bool").get("child_occupant_present"), True)
        self.assertIs(self._answer("No they weren't", "bool").get("child_occupant_present"), False)

    def test_polarity_reads_only_the_opening(self):
        self.assertIsNone(answer_polarity("I tried paying on the app but it wouldn't go through."))
        self.assertIsNone(answer_polarity("Not sure"))
        self.assertIs(answer_polarity("None of us were in the car"), False)


# --------------------------------------------------------------------- 23/26
class BayWordingMakesNoInference(unittest.TestCase):
    BANNED = ("spanning only", "only that interval", "that instant", "single instant",
              "does not of itself establish")

    def test_approved_block_and_proposition_are_neutral(self):
        kg = KnowledgeGraph()
        texts = [kg.blocks["PP-BAY-001"].text, kg.modules["KB-BAY-01"].core_proposition]
        for t in texts:
            for phrase in self.BANNED:
                self.assertNotIn(phrase, t.lower())
        self.assertIn("timestamps", texts[0])


# ------------------------------------------------------------------------- 2
class TwoFrontsAreNotBothSides(unittest.TestCase):

    def _case(self, sides):
        case = CaseFile("C-sides", evidence={
            "E1": EvidenceItem("E1", "NTK", "photo1.jpg", images=[b"IMAGE-ONE"]),
            "E2": EvidenceItem("E2", "NTK", "photo2.jpg", images=[b"IMAGE-TWO"]),
        })
        case.document_classes.update({"E1": "NTK", "E2": "NTK"})
        if sides is not None:
            case.classifications = {f"E{i}": {"pages": [{"page": 1, "side": s}]}
                                    for i, s in enumerate(sides, start=1)}
        return case

    def test_two_distinct_front_photos_are_incomplete(self):
        case = self._case(["FRONT", "FRONT"])
        self.assertFalse(assess_notice_sides(case)["complete"])
        self.assertEqual(notice_pages_sufficient(case), (False, "only_front_pages"))

    def test_front_and_reverse_are_complete(self):
        for back in ("REVERSE", "BLANK", "CONTINUATION"):
            with self.subTest(back=back):
                case = self._case(["FRONT", back])
                self.assertTrue(assess_notice_sides(case)["complete"])
                self.assertTrue(notice_pages_sufficient(case)[0])

    def test_without_labels_the_page_rule_is_unchanged(self):
        for sides in (None, ["UNKNOWN", "UNKNOWN"], ["FRONT", "UNKNOWN"]):
            with self.subTest(sides=sides):
                self.assertTrue(assess_notice_sides(self._case(sides))["complete"])

    def test_front_text_citing_pofa_is_not_a_reverse(self):
        case = CaseFile("C-text", evidence={"E1": EvidenceItem(
            "E1", "NTK", "notice.jpg", images=[b"ONE"],
            text=("Parking Charge Notice issued under the Protection of Freedoms Act 2012, "
                  "Schedule 4. " * 4))})
        case.document_classes["E1"] = "NTK"
        self.assertFalse(assess_notice_sides(case)["complete"])

    def test_classifier_page_sides_are_normalised(self):
        from pcn_appeal.intake.classifier import _normalise
        case = CaseFile("C-cls", evidence={"E1": EvidenceItem("E1", "NTK", "a.jpg",
                                                               images=[b"A", b"B"])})
        c = _normalise({"evidence_id": "E1", "document_type": "PRIVATE_PARKING_NOTICE",
                        "pages": [{"page": 1, "side": "front"}, {"page": 2, "side": "back"},
                                  {"page": 9, "side": "REVERSE"}, {"page": "x"}]}, case)
        self.assertEqual(c.pages, [{"page": 1, "side": "FRONT"}, {"page": 2, "side": "UNKNOWN"}])


if __name__ == "__main__":
    unittest.main()
