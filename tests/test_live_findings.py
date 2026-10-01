"""Defects found by the live run over twelve customer uploads (2026-10-01).

All data is fictitious. Nothing here is specific to an operator or a case.

  * the front of one notice and the back of another were drafted as one notice
  * two identical photos were refused as "only front pages", with the wrong message
  * a PCN misread from a photo had nothing to disagree with
  * the customer was asked about an internally derived fact
  * letters printed ISO dates
  * a notice without the para 9(2)(f) keeper warning was never checked
"""
from __future__ import annotations

import unittest

from pcn_appeal.disclosure import apply_disclosure
from pcn_appeal.engines.analysis import is_internal_fact
from pcn_appeal.engines.recovery import FactRecoveryEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.legal.pofa import scan_keeper_warning
from pcn_appeal.models import CaseFile, EvidenceItem, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.notice_completeness import (DIFFERENT_NOTICES_MESSAGE, DUPLICATE_PAGES_MESSAGE,
                                            assess_notice_sides, notice_pages_sufficient,
                                            rejection_message)
from pcn_appeal.orchestrator import AppealPipeline, uk_dates


def _fact(name, value, status=FactStatus.DERIVED, kind=SourceKind.CALCULATION):
    return Fact(f"F-{name}", name, value, status, FactSource(kind, "t"))


def _two_page_case(refs1, refs2, sides=("FRONT", "REVERSE"), images=(b"ONE", b"TWO")):
    case = CaseFile("C-two", evidence={
        "E1": EvidenceItem("E1", "NTK", "p1.jpg", images=[images[0]]),
        "E2": EvidenceItem("E2", "NTK", "p2.jpg", images=[images[1]]),
    })
    case.document_classes.update({"E1": "NTK", "E2": "NTK"})
    case.classifications = {
        "E1": {"document_type": "PRIVATE_PARKING_NOTICE", "references": refs1,
               "pages": [{"page": 1, "side": sides[0]}]},
        "E2": {"document_type": "PRIVATE_PARKING_NOTICE", "references": refs2,
               "pages": [{"page": 1, "side": sides[1]}]},
    }
    return case


class PagesFromDifferentNotices(unittest.TestCase):

    def test_different_registrations_are_refused(self):
        case = _two_page_case({"vrm": "AB12CDE", "pcn_number": "1000200030"}, {"vrm": "XY65ZZZ"})
        self.assertEqual(notice_pages_sufficient(case), (False, "different_notices"))
        r = assess_notice_sides(case)
        self.assertFalse(r["complete"])
        self.assertFalse(r["same_notice"])
        self.assertEqual(rejection_message("different_notices"), DIFFERENT_NOTICES_MESSAGE)

    def test_different_charge_numbers_are_refused(self):
        case = _two_page_case({"pcn_number": "1000200030"}, {"pcn_number": "5550001119"})
        self.assertEqual(notice_pages_sufficient(case), (False, "different_notices"))

    def test_same_notice_passes_including_a_partial_misread(self):
        for refs2 in ({"vrm": "AB12CDE"}, {"vrm": "AB12CDF"}, {"vrm": "AB12 CDE"},
                      {}, {"pcn_number": "1000200080"},
                      {"vrm": "AB80ODE"}, {"vrm": "AB80OBE"},   # blurred: 3 and 4 of 7 misread
                      {"pcn_number": "1000277730"}):
            with self.subTest(refs2=refs2):
                case = _two_page_case({"vrm": "AB12CDE", "pcn_number": "1000200030"}, refs2)
                self.assertTrue(notice_pages_sufficient(case)[0])

    def test_a_form_code_read_as_the_charge_number_is_not_compared(self):
        """Live: a reverse's footer form code came back as its PCN."""
        for code in ("ABCDEFGH/P/0524", "FORM-REV-2", "315254106235660190004683"):
            with self.subTest(code=code):
                case = _two_page_case({"vrm": "AB12CDE", "pcn_number": "45000564251"},
                                      {"vrm": "AB12CDE", "pcn_number": code})
                self.assertTrue(notice_pages_sufficient(case)[0])
        case = _two_page_case({"pcn_number": "PC10002003"}, {"pcn_number": "PC55577711"})
        self.assertEqual(notice_pages_sufficient(case), (False, "different_notices"))

    def test_supporting_evidence_with_another_registration_is_not_a_second_notice(self):
        """A payment receipt showing a mistyped registration is a keying-error
        case, not a different notice."""
        case = _two_page_case({"vrm": "AB12CDE"}, {"vrm": "AB12CXX"})
        case.classifications["E2"]["document_type"] = "SUPPORTING_EVIDENCE"
        case.classifications["E2"]["references"] = {"vrm": "QQ99QQQ"}
        self.assertNotEqual(notice_pages_sufficient(case)[1], "different_notices")


class ReferencesAreReadPerDocument(unittest.TestCase):
    """Live, the joint classification call copied the front's registration onto
    the back of a different notice, so the different-notices gate passed."""

    def _classify(self, page_refs):
        from pcn_appeal.intake.classifier import classify
        from pcn_appeal.llm import FakeLLM
        leaked = {"pcn_number": "1000200030", "vrm": "AB12CDE"}
        joint = {"documents": [
            {"evidence_id": e, "document_type": "PRIVATE_PARKING_NOTICE",
             "service_family": "PRIVATE_PARKING", "stage": "INITIAL_NOTICE", "confidence": 0.9,
             "references": dict(leaked), "pages": [{"page": 1, "side": side}]}
            for e, side in (("E1", "FRONT"), ("E2", "REVERSE"))]}
        responses = {"classification": [joint]}
        if page_refs is not None:
            responses["page_references"] = page_refs
        case = CaseFile("C-refs", evidence={
            "E1": EvidenceItem("E1", "OTHER", "a.jpg", images=[b"ONE"]),
            "E2": EvidenceItem("E2", "OTHER", "b.jpg", images=[b"TWO"])})
        case.classifications = {k: v.to_dict() for k, v in classify(case, FakeLLM(responses)).items()}
        return case

    def test_per_document_read_overrides_a_leaked_reference(self):
        case = self._classify([{"pcn_number": "1000200030", "vrm": "AB12CDE"},
                               {"pcn_number": None, "vrm": "XY65ZZZ"}])
        self.assertEqual(case.classifications["E2"]["references"], {"vrm": "XY65ZZZ"})
        self.assertEqual(notice_pages_sufficient(case), (False, "different_notices"))

    def test_a_failed_read_keeps_the_joint_reading(self):
        case = self._classify(None)                      # FakeLLM raises for the task
        self.assertEqual(case.classifications["E2"]["references"]["vrm"], "AB12CDE")
        self.assertTrue(any("failed" in n for n in case.classifications["E2"]["notes"]))


class IdenticalPhotos(unittest.TestCase):

    def test_identical_images_are_duplicates_whatever_the_labels(self):
        for sides in (("FRONT", "FRONT"), ("REVERSE", "REVERSE"), ("UNKNOWN", "UNKNOWN")):
            with self.subTest(sides=sides):
                case = _two_page_case({}, {}, sides=sides, images=(b"SAME", b"SAME"))
                self.assertEqual(notice_pages_sufficient(case), (False, "duplicate_front_images"))
        self.assertEqual(rejection_message("duplicate_front_images"), DUPLICATE_PAGES_MESSAGE)


class PcnReadTwice(unittest.TestCase):

    def test_classifier_and_extractor_disagreeing_is_a_conflict(self):
        from pcn_appeal.engines.extraction import ExtractionEngine
        from pcn_appeal.llm import FakeLLM
        fields = {"pcn_number": {"value": "88812545642", "confidence": 0.97, "evidence_id": "E1"},
                  "vrm": {"value": "AB12CDE", "confidence": 0.97, "evidence_id": "E1"}}
        case = CaseFile("C-pcn", evidence={"E1": EvidenceItem("E1", "NTK", "p.jpg", images=[b"x"])})
        case.classifications = {"E1": {"document_type": "PRIVATE_PARKING_NOTICE",
                                       "references": {"pcn_number": "88812545842"}}}
        ExtractionEngine(FakeLLM({"extraction": [{"fields": fields, "doc_types": {"E1": "NTK"}}]})).run(case)
        # Checked by the customer on the confirmation screen; not the
        # cross-document conflict gate (both readings could be wrong).
        self.assertEqual(case.facts["pcn_number"].status, FactStatus.UNCERTAIN)
        self.assertFalse(case.get("pcn_conflict"))
        self.assertTrue(any(a.get("event") == "pcn_read_disagreement" for a in case.audit))

    def test_agreeing_readings_are_no_conflict(self):
        from pcn_appeal.engines.extraction import ExtractionEngine
        from pcn_appeal.llm import FakeLLM
        fields = {"pcn_number": {"value": "88812545842", "confidence": 0.97, "evidence_id": "E1"}}
        case = CaseFile("C-pcn2", evidence={"E1": EvidenceItem("E1", "NTK", "p.jpg", images=[b"x"])})
        case.classifications = {"E1": {"document_type": "PRIVATE_PARKING_NOTICE",
                                       "references": {"pcn_number": "8881 2545 842"}}}
        ExtractionEngine(FakeLLM({"extraction": [{"fields": fields, "doc_types": {"E1": "NTK"}}]})).run(case)
        self.assertFalse(case.get("pcn_conflict"))
        self.assertNotEqual(case.facts["pcn_number"].status, FactStatus.UNCERTAIN)


class InternalFactsAreNotAsked(unittest.TestCase):

    def test_derived_facts_are_internal(self):
        for f in ("account_contradicts_allegation", "pofa_9_2_e_status", "ntk_invites_name_driver",
                  "notice_sides_complete", "observation_window_min", "_material_source_texts"):
            self.assertTrue(is_internal_fact(f), f)
        for f in ("payment_made", "genuine_customer", "site_postcode", "keying_error_type",
                  "child_occupant_present", "permit_held"):
            self.assertFalse(is_internal_fact(f), f)


class UkDates(unittest.TestCase):

    def test_iso_dates_are_written_in_uk_form(self):
        self.assertEqual(uk_dates("an event dated 2026-09-19 and 2026-07-01."),
                         "an event dated 19 September 2026 and 1 July 2026.")
        self.assertEqual(uk_dates("PCN 4401928375, 12:23, 2026-13-40"), "PCN 4401928375, 12:23, 2026-13-40")


class QuotingTheNotice(unittest.TestCase):

    def test_the_allegation_as_printed_may_be_quoted(self):
        from pcn_appeal.engines.validation import _quotes_a_verified_fact
        facts = {"alleged_breach": "Your vehicle has overstayed the maximum time period allowed"}
        self.assertTrue(_quotes_a_verified_fact("Your vehicle has overstayed the maximum time period allowed.", facts))
        self.assertTrue(_quotes_a_verified_fact("overstayed the maximum   time period", facts))
        self.assertFalse(_quotes_a_verified_fact("the driver admits overstaying the period", facts))
        self.assertFalse(_quotes_a_verified_fact("allowed", facts))


class LettersEndWithARequest(unittest.TestCase):
    """Live, an AI letter ended on its last ground and asked for nothing."""

    def _pipe_and_pack(self):
        from types import SimpleNamespace
        from pcn_appeal.llm import FakeLLM
        kg = KnowledgeGraph()
        pack = SimpleNamespace(verified_facts={"pcn_number": "1000200030"},
                               fact_refs={"pcn_number": "F-pcn_number"})
        return AppealPipeline(FakeLLM(), FakeLLM(), kg=kg), pack, kg

    def test_the_approved_closing_is_added_when_missing(self):
        from pcn_appeal.models import Draft, DraftSentence
        pipe, pack, kg = self._pipe_and_pack()
        draft = Draft("C", [[DraftSentence("The operator is put to proof of the breach.", [], ["KB-BAY-01"], [])]], 1)
        self.assertTrue(pipe._with_closing(draft, pack))
        closing = " ".join(s.text for s in draft.paragraphs[-1])
        self.assertIn("requested to cancel Parking Charge Notice 1000200030", closing)
        self.assertEqual({m for s in draft.paragraphs[-1] for m in s.module_refs}, {"STRUCTURAL"})
        self.assertEqual(kg.blocks["PP-END-001"].status, "ACTIVE")

    def test_a_letter_that_already_asks_is_left_alone(self):
        from pcn_appeal.models import Draft, DraftSentence
        pipe, pack, _ = self._pipe_and_pack()
        for text in ("I request that the operator cancels this charge.",
                     "The charge should therefore be cancelled."):
            draft = Draft("C", [[DraftSentence(text, [], ["KB-LAND-01"], [])]], 1)
            self.assertFalse(pipe._with_closing(draft, pack))
            self.assertEqual(len(draft.paragraphs), 1)
        self.assertFalse(pipe._with_closing(Draft("C", [], 1), pack))


WITH_WARNING = (
    "You are advised that if, after the period of 28 days beginning with the day after that on "
    "which the notice is given, the parking charge has not been paid in full and we do not know "
    "both the name and current address of the driver, we have the right to recover any unpaid "
    "part of the parking charge from you. ") * 2
WITHOUT_WARNING = (
    "If after 28 days beginning with the day after that on which this notice is given, the "
    "Parking Charge has not been paid in full, we may pursue you for any Parking Charge amount "
    "that remains unpaid. If you were not the driver please provide the name and address of "
    "the driver and pass the notice on to the driver. ") * 2


class KeeperLiabilityWarning(unittest.TestCase):

    def test_text_scan(self):
        self.assertIs(scan_keeper_warning(WITH_WARNING), True)
        self.assertIs(scan_keeper_warning(WITHOUT_WARNING), False)
        self.assertIsNone(scan_keeper_warning("PCN"))

    def _case(self, text="", flag=None, sides=True):
        case = CaseFile("C-kw", evidence={"E1": EvidenceItem("E1", "NTK", "n.pdf", text=text)})
        case.document_classes["E1"] = "NTK"
        case.put(_fact("notice_route", "POSTAL"))
        case.put(_fact("jurisdiction", "ENGLAND_WALES"))
        case.put(_fact("notice_sides_complete", sides))
        if flag is not None:
            case.put(_fact("ntk_keeper_liability_warning", flag, FactStatus.EXTRACTED,
                           SourceKind.DOCUMENT))
        apply_disclosure(case, False, source="t")
        FactRecoveryEngine(KnowledgeGraph()).recover(case)
        return case

    def test_absent_warning_in_the_notice_text_is_a_defect(self):
        case = self._case(text=WITHOUT_WARNING)
        self.assertIs(case.get("ntk_defect_keeper_warning"), True)
        self.assertIs(case.get("ntk_defect_document_confirmed"), True)

    def test_present_unknown_or_one_side_is_not_a_defect(self):
        for case in (self._case(text=WITH_WARNING), self._case(),
                     self._case(text=WITHOUT_WARNING, sides=False)):
            self.assertNotIn("ntk_defect_keeper_warning", case.facts)

    def test_a_model_reading_of_a_photo_is_never_pleaded(self):
        """Live, the model returned statutory wording a blurred photo does not
        print. Its yes/no is recorded, not relied on, whichever way it goes."""
        for flag in (False, True):
            with self.subTest(flag=flag):
                self.assertNotIn("ntk_defect_keeper_warning", self._case(flag=flag).facts)
        # The text decides even when the model disagrees with it.
        self.assertIs(self._case(text=WITHOUT_WARNING, flag=True).get("ntk_defect_keeper_warning"), True)

    def test_kb_wording_for_the_defect_is_approved(self):
        kg = KnowledgeGraph()
        self.assertEqual(kg.modules["KB-POFA-05"].status, "ACTIVE")
        self.assertEqual(kg.blocks["PP-POFA-005B"].status, "ACTIVE")


if __name__ == "__main__":
    unittest.main()
