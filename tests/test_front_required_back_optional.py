"""The notice upload rule: the front is required, the back is optional.

The reverse page used to be mandatory. It gated the upload endpoint, it held
the case at confirm behind a NEEDS_DOCUMENTS screen, and the auto-appeal route
turned it into a customer question with a free-text box. A customer whose
notice was printed on one side, or who simply did not photograph the back,
could not get an appeal at all.

What the reverse page tells us has not changed: `notice_sides_complete` still
records whether it was read, and the PoFA findings that depend on wording
printed there still gate on that fact. The difference is that its absence
leaves those findings unresolved instead of stopping the case.

The spec's regression matrix, one class each:

  A. front only                      → continues
  B. front + correct reverse         → continues, reverse read
  C. front + reverse of another notice → front stays valid, extra page set aside
  D. front, no reverse               → no question about the back page
  E. a finding needing reverse wording, no reverse → unresolved, not invented
  F. answers and case state survive

Run:  python -m unittest discover -s tests -p test_front_required_back_optional.py -v
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pcn_appeal import api
from pcn_appeal.intake import document_types as T
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile, CaseState, EvidenceItem, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.notice_completeness import (
    assess_notice_sides, front_page_present, rejectable_optional_page,
)
from support import ReferenceAnalysisLLM, patch_client

FRONT = ("Notice to Keeper\nPCN 00112233\nVRM AB12CDE\n"
         "Parking Charge Notice for Retail Park on 01/06/2026.\nCharge £100.")
BACK = ("Protection of Freedoms Act 2012, Schedule 4.\nHow to appeal.\n"
        "You may pass this notice to the driver.\nIndependent appeals: POPLA.")

NOTICE_FIELDS = {
    k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
    for k, v in dict(operator_name="Northgate Parking Ltd", pcn_number="00112233",
                     vrm="AB12CDE", parking_location="Retail Park",
                     site_postcode="M1 1AA", parking_event_date="01/06/2026",
                     notice_issue_date="05/06/2026", charge_amount="£100",
                     alleged_breach="Overstayed paid time",
                     operator_ata="BPA").items()}


def _doc(ev_id, document_type=T.PRIVATE_PARKING_NOTICE, **extra):
    return {"evidence_id": ev_id, "document_type": document_type,
            "service_family": T.FIXED_FAMILY.get(document_type, "UNKNOWN"),
            "stage": T.default_stage(document_type), "confidence": 0.95, **extra}


def _llm(*docs, refs=None):
    """A scripted classifier and extractor for the uploaded pages."""
    rows = []
    for i, d in enumerate(docs):
        row = dict(d)
        if refs and i < len(refs) and refs[i] is not None:
            row["references"] = refs[i]
        rows.append(row)
    return ReferenceAnalysisLLM({
        "classification": [{"documents": rows}],
        "extraction": [{"fields": NOTICE_FIELDS,
                        "doc_types": {d["evidence_id"]: "NTK" for d in docs}}]})


def _upload(client, files):
    case_id = client.post("/cases").json()["case_id"]
    res = client.post(f"/cases/{case_id}/files",
                      files=[("files", (name, text.encode(), "text/plain"))
                             for name, text in files])
    return case_id, res


def _fact(case, name, value, kind=SourceKind.DOCUMENT):
    case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                  FactSource(kind, "E1")))


# ----------------------------------------------------------------- A. front only
class AFrontOnlyUploadContinues(unittest.TestCase):

    def test_the_upload_is_accepted(self):
        patch_client(self, _llm(_doc("E1")))
        case_id, res = _upload(TestClient(api.app), [("front.txt", FRONT)])
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["route"], "PRIVATE_PARKING")
        self.assertEqual(body["state"], CaseState.EXTRACTED.value)

    def test_the_front_page_is_kept(self):
        patch_client(self, _llm(_doc("E1")))
        case_id, _res = _upload(TestClient(api.app), [("front.txt", FRONT)])
        case = api.CASES[case_id]["case"]
        self.assertEqual(len(case.evidence), 1)

    def test_the_gate_itself_passes_on_one_page(self):
        case = CaseFile("C-front")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg",
                                           images=[b"ONE_PAGE"])
        self.assertEqual(front_page_present(case), (True, "front_page_present"))

    def test_an_upload_with_no_page_is_still_refused(self):
        case = CaseFile("C-empty")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "nothing.jpg", text="   ")
        ok, reason = front_page_present(case)
        self.assertFalse(ok)
        self.assertEqual(reason, "no_page_uploaded")

    def test_confirm_continues_rather_than_asking_for_documents(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1")))
        case_id, _res = _upload(client, [("front.txt", FRONT)])
        body = client.post(f"/cases/{case_id}/confirm", json={
            "corrections": {}, "confirmed": ["pcn_number"],
            "narrative": "I was collecting a prescription",
        }).json()
        self.assertNotEqual(body.get("outcome"), "NEEDS_DOCUMENTS", body)
        self.assertNotIn("notice_sides_incomplete", body.get("flags") or [])


# ------------------------------------------------------- B. front + correct back
class BFrontAndReverseContinues(unittest.TestCase):

    def test_both_pages_are_read(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1"), _doc("E2")))
        case_id, res = _upload(client, [("front.txt", FRONT), ("back.txt", BACK)])
        self.assertEqual(res.status_code, 200, res.text)
        case = api.CASES[case_id]["case"]
        self.assertEqual(len(case.evidence), 2)

    def test_the_sides_assessment_records_it_as_complete(self):
        case = CaseFile("C-two")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg",
                                           images=[b"FRONT_PAGE_BYTES"])
        case.evidence["E2"] = EvidenceItem("E2", "NTK", "back.jpg",
                                           images=[b"BACK_PAGE_BYTES"])
        case.document_classes.update({"E1": "NTK", "E2": "NTK"})
        assessed = assess_notice_sides(case)
        self.assertTrue(assessed["applicable"])
        self.assertTrue(assessed["complete"], assessed)


# ------------------------------------------------- C. front + reverse of another
class CAForeignReverseIsSetAsideNotFatal(unittest.TestCase):
    """The front is what is being appealed, so it is never the page dropped."""

    REFS_FRONT = {"pcn_number": "00112233", "vrm": "AB12CDE"}
    REFS_OTHER = {"pcn_number": "99887766", "vrm": "ZZ99ZZZ"}

    def _case(self):
        case = CaseFile("C-foreign")
        for ev, refs in (("E1", self.REFS_FRONT), ("E2", self.REFS_OTHER)):
            case.evidence[ev] = EvidenceItem(ev, "NTK", f"{ev}.jpg",
                                             images=[ev.encode() * 4])
            case.classifications[ev] = {
                "document_type": "PRIVATE_PARKING_NOTICE", "references": refs}
        return case

    def test_the_later_page_is_the_one_identified(self):
        self.assertEqual(rejectable_optional_page(self._case()), "E2")

    def test_a_single_page_identifies_nothing(self):
        case = CaseFile("C-one")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg", images=[b"F"])
        case.classifications["E1"] = {"document_type": "PRIVATE_PARKING_NOTICE",
                                      "references": self.REFS_FRONT}
        self.assertIsNone(rejectable_optional_page(case))

    def test_matching_pages_identify_nothing(self):
        case = self._case()
        case.classifications["E2"]["references"] = dict(self.REFS_FRONT)
        self.assertIsNone(rejectable_optional_page(case))

    def test_the_api_keeps_the_front_and_drops_the_extra(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1"), _doc("E2"),
                                refs=[self.REFS_FRONT, self.REFS_OTHER]))
        case_id, res = _upload(client, [("front.txt", FRONT), ("other.txt", BACK)])
        self.assertEqual(res.status_code, 200, res.text)
        case = api.CASES[case_id]["case"]
        self.assertIn("E1", case.evidence, "the valid front was invalidated")
        self.assertNotIn("E2", case.evidence, "the foreign page was kept")
        self.assertEqual(case.state, CaseState.EXTRACTED.value
                         if isinstance(case.state, str) else case.state)

    def test_the_case_is_not_restarted(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1"), _doc("E2"),
                                refs=[self.REFS_FRONT, self.REFS_OTHER]))
        case_id, _res = _upload(client, [("front.txt", FRONT), ("other.txt", BACK)])
        case = api.CASES[case_id]["case"]
        self.assertNotEqual(case.state, CaseState.CREATED)
        self.assertEqual(
            [a for a in case.audit if a.get("event") == "optional_reverse_set_aside"
             and a.get("evidence_id") == "E2"].__len__(), 1, case.audit)


# ---------------------------------------------------------- D. no question asked
class DNoQuestionAsksForTheBackPage(unittest.TestCase):

    def test_the_auto_appeal_route_asks_nothing_about_the_reverse(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1")))
        case_id, _res = _upload(client, [("front.txt", FRONT)])
        body = client.post(f"/cases/{case_id}/auto-appeal", json={
            "narrative": "The machine would not take my payment",
        }).json()
        facts = [q.get("fact") for q in body.get("questions") or []]
        self.assertNotIn("notice_reverse_pages", facts, body)
        self.assertNotIn("notice_sides_incomplete", body.get("flags") or [])

    def test_no_question_anywhere_mentions_the_reverse_page(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1")))
        case_id, _res = _upload(client, [("front.txt", FRONT)])
        body = client.post(f"/cases/{case_id}/confirm", json={
            "corrections": {}, "confirmed": ["pcn_number"], "narrative": "",
        }).json()
        for q in body.get("questions") or []:
            self.assertNotIn("reverse", str(q.get("text", "")).lower(), q)
            self.assertNotIn("back of", str(q.get("text", "")).lower(), q)

    def test_the_question_fact_no_longer_exists_in_the_orchestrator(self):
        import pcn_appeal.orchestrator as orch
        from pathlib import Path
        source = Path(orch.__file__).read_text()
        self.assertNotIn("notice_reverse_pages", source)


# ------------------------------------------- E. reverse-dependent finding
class EAFindingNeedingReverseWordingStaysUnresolved(unittest.TestCase):
    """Absent wording is absent, not a defect. The KB gate already says so;
    this pins it, because removing the upload gate is what makes a front-only
    case reach the findings at all."""

    def _case(self, sides_complete: bool):
        case = CaseFile("C-findings")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg",
                                           images=[b"FRONT"])
        case.document_classes["E1"] = "NTK"
        # pofa_route is the derived form of notice_route (recovery calculates it);
        # POFA-04 lists it among its hard blockers, so it has to be known here for
        # these tests to be about the reverse page and not about an unknown blocker.
        for name, value in (("driver_status", "UNIDENTIFIED"),
                            ("notice_route", "POSTAL"),
                            ("pofa_route", "POSTAL"),
                            ("relevant_land", True),
                            ("ntk_defect_document_confirmed", True),
                            ("ntk_defect_keeper_warning", True)):
            _fact(case, name, value)
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete",
                      sides_complete, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_sides:test")))
        return case

    def _content_module_offered(self, case) -> bool:
        from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher, SUPPORTED
        from pcn_appeal.kg.graph import KnowledgeGraph
        match = KnowledgeMatcher(KnowledgeGraph()).match(case, case.fact_view())
        return "KB-POFA-04" in [c.module_id for c in match.by_status(SUPPORTED)]

    def test_without_the_reverse_the_content_defect_is_not_pleaded(self):
        case = self._case(sides_complete=False)
        self.assertFalse(self._content_module_offered(case),
                         "a defect was asserted from a page nobody read")

    def test_with_the_reverse_the_same_facts_do_plead_it(self):
        case = self._case(sides_complete=True)
        self.assertTrue(self._content_module_offered(case),
                        "the gate now blocks a defect the reverse does establish")

    def test_the_missing_reverse_is_recorded_rather_than_guessed(self):
        case = self._case(sides_complete=False)
        assessed = assess_notice_sides(case)
        self.assertFalse(assessed["complete"])
        self.assertEqual(assessed["reason"], "front_only_or_single_page")
        self.assertIsNot(assessed["complete"], None)


# -------------------------------------------------------- F. nothing else moves
class FExistingAnswersAndStateSurvive(unittest.TestCase):

    def test_the_narrative_and_answers_are_kept_across_confirm(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1")))
        case_id, _res = _upload(client, [("front.txt", FRONT)])
        client.post(f"/cases/{case_id}/confirm", json={
            "corrections": {}, "confirmed": ["pcn_number"],
            "narrative": "I left the site and came back later that day",
        })
        case = api.CASES[case_id]["case"]
        self.assertEqual(case.raw_answers.get("narrative"),
                         "I left the site and came back later that day")
        self.assertEqual(case.get("pcn_number"), "00112233")

    def test_a_two_page_upload_behaves_exactly_as_before(self):
        client = TestClient(api.app)
        patch_client(self, _llm(_doc("E1"), _doc("E2")))
        case_id, res = _upload(client, [("front.txt", FRONT), ("back.txt", BACK)])
        self.assertEqual(res.status_code, 200, res.text)
        case = api.CASES[case_id]["case"]
        self.assertEqual(case.get("pcn_number"), "00112233")
        self.assertEqual(len(case.evidence), 2)

    def test_the_other_routes_keep_their_own_upload_rules(self):
        from pcn_appeal.services import engine_for
        self.assertEqual(engine_for("PRIVATE_PARKING").completeness.name,
                         "FRONT_REQUIRED_BACK_OPTIONAL")
        self.assertEqual(engine_for("CHARGE_CERTIFICATE").completeness.name,
                         "FRONT_AND_BACK_OR_MULTIPAGE")

    def test_a_non_notice_route_is_not_asked_for_a_front_page(self):
        patch_client(self, FakeLLM({"classification": [
            {"documents": [_doc("E1", T.DEBT_RECOVERY)]}]}))
        _case_id, res = _upload(TestClient(api.app), [("letter.txt", "debt letter")])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotEqual(res.json().get("route"), "PRIVATE_PARKING")


class EAMissingReverseIsNotAnOutcome(unittest.TestCase):
    """A front-only case with no grounds reports the outcome it reached.

    It used to report "we need clearer documents — add the other side of the
    notice", which asks a customer whose notice is printed on one side for a
    page that does not exist.
    """

    def _held(self, sides_complete):
        from pcn_appeal.engines.outcome import classify_hold
        case = CaseFile("C-outcome")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg", images=[b"F"])
        case.document_classes["E1"] = "NTK"
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete",
                      sides_complete, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_sides:test")))
        case.audit.append({"event": "notice_sides_assessment", "complete": sides_complete,
                           "reason": "front_only_or_single_page"})
        case.audit.append({"event": "analysis_complete_no_supported_grounds",
                           "module_ids": []})
        return classify_hold(case, None, None)

    def test_a_front_only_case_is_not_told_to_add_documents(self):
        self.assertNotEqual(self._held(False).get("outcome"), "NEEDS_DOCUMENTS",
                            self._held(False))

    def test_the_outcome_does_not_ask_for_the_other_side(self):
        held = self._held(False)
        blob = " ".join(str(v) for v in held.values()).lower()
        self.assertNotIn("other side", blob)
        self.assertNotIn("missing pages", blob)

    def test_a_complete_notice_reaches_the_same_outcome(self):
        """The reverse made no difference to this path, which is the point."""
        self.assertEqual(self._held(False).get("outcome"),
                         self._held(True).get("outcome"))


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------- G. the back stays a bonus, never a gap
class GAMissingBackIsUnknownNeverAbsent(unittest.TestCase):
    """Found by the post-merge audit: OCR on every photo, filename-dependent
    set-aside, and reverse print codes each let a missing or optional back
    page cost the customer something. Each is pinned here."""

    OCR_REVERSE = ('<ocr_transcription page="1" engine="tesseract">\n'
                   "Machine OCR may misread characters. Check against the attached page; "
                   "this is not customer confirmation.\n" + BACK + "\n" + FRONT * 3
                   + "\n</ocr_transcription>")

    def _sides(self, value, reason):
        case = CaseFile("C-sides")
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete", value,
                      FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, f"notice_sides:{reason}")))
        return case

    def test_no_customer_message_says_both_sides_are_mandatory(self):
        from pcn_appeal.notice_completeness import (
            FRONT_REQUIRED_MESSAGE, rejection_message)
        for reason in ("no_page_uploaded", "no_readable_page", "different_notices",
                       "duplicate_front_images", "anything_else"):
            self.assertNotIn("mandatory", rejection_message(reason), reason)
        self.assertEqual(rejection_message("no_page_uploaded"), FRONT_REQUIRED_MESSAGE)

    def test_one_photo_with_ocr_text_is_still_front_only(self):
        case = CaseFile("C-ocr")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "IMG_1.jpg", images=[b"ONE"],
                                           text=self.OCR_REVERSE)
        case.document_classes["E1"] = "NTK"
        assessed = assess_notice_sides(case)
        self.assertTrue(assessed["applicable"], assessed)
        self.assertFalse(assessed["complete"], assessed)

    def test_set_aside_does_not_depend_on_filenames(self):
        for names in (("IMG_1.jpg", "notice_back.jpg"), ("pcn_front.jpg", "IMG_2.jpg")):
            case = CaseFile("C-names")
            for ev, name, refs in (("E1", names[0], CAForeignReverseIsSetAsideNotFatal.REFS_FRONT),
                                   ("E2", names[1], CAForeignReverseIsSetAsideNotFatal.REFS_OTHER)):
                case.evidence[ev] = EvidenceItem(ev, "OTHER", name, images=[ev.encode() * 4])
                case.classifications[ev] = {"document_type": "PRIVATE_PARKING_NOTICE",
                                            "references": refs}
            self.assertEqual(rejectable_optional_page(case), "E2", names)

    def test_a_back_uploaded_first_never_pushes_out_the_front(self):
        case = CaseFile("C-order")
        for ev, side, refs in (("E1", "REVERSE", CAForeignReverseIsSetAsideNotFatal.REFS_OTHER),
                               ("E2", "FRONT", CAForeignReverseIsSetAsideNotFatal.REFS_FRONT)):
            case.evidence[ev] = EvidenceItem(ev, "OTHER", f"{ev}.jpg", images=[ev.encode() * 4])
            case.classifications[ev] = {"document_type": "PRIVATE_PARKING_NOTICE",
                                        "references": refs,
                                        "pages": [{"page": 1, "side": side}]}
        self.assertEqual(rejectable_optional_page(case), "E1")

    def test_only_a_page_seen_as_the_back_counts_as_the_back_read(self):
        from pcn_appeal.notice_completeness import reverse_page_read
        self.assertFalse(reverse_page_read(self._sides(False, "front_only_or_single_page")))
        self.assertFalse(reverse_page_read(self._sides(True, "distinct_pages_or_multipage")),
                         "two photos are not proof one of them is the back")
        self.assertTrue(reverse_page_read(self._sides(True, "classifier_labelled_reverse_page")))

    def test_wording_not_on_the_front_is_not_recorded_as_absent(self):
        from pcn_appeal.engines.recovery import FactRecoveryEngine, RecoveryReport
        case = self._sides(False, "front_only_or_single_page")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg", images=[b"F"],
                                           text=FRONT * 5)
        _fact(case, "notice_route", "POSTAL")
        report = RecoveryReport()
        FactRecoveryEngine()._assess_ntk_schedule4_content(case, report)
        self.assertIsNone(case.get("ntk_invites_pass_to_driver"))
        self.assertIsNone(case.get("ntk_invites_name_driver"))
        self.assertEqual(case.get("pofa_9_2_e_status"), "UNRESOLVED")
        self.assertNotIn("request_document",
                         [g.get("action") for g in report.unknown_material],
                         "the optional back was put to the customer as a document to supply")

    def test_a_reverse_print_code_is_not_a_second_charge_number(self):
        from pcn_appeal.notice_completeness import classifier_charge_number
        back = {"references": {"pcn_number": "00998877"},
                "pages": [{"page": 1, "side": "REVERSE"}]}
        code = {"references": {"pcn_number": "PKN/P/0524"}}
        front = {"references": {"pcn_number": "00112233"},
                 "pages": [{"page": 1, "side": "FRONT"}]}
        self.assertEqual(classifier_charge_number(back), "")
        self.assertEqual(classifier_charge_number(code), "")
        self.assertEqual(classifier_charge_number(front), "00112233")
