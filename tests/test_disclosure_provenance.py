"""Regression: disclosure provenance, notice completeness, PoFA 9(2)(e) status."""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pcn_appeal.api import app
from pcn_appeal.disclosure import (
    DISCLOSURE_NO, DISCLOSURE_UNKNOWN, DISCLOSURE_YES,
    apply_disclosure, correct_disclosure, keeper_route_blocked, parse_disclosure,
)
from pcn_appeal.engines.recovery import FactRecoveryEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.legal.pofa import assess, scan_ntk_invitations
from pcn_appeal.models import (
    CaseFile, CaseState, DriverStatus, EvidenceItem, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.notice_completeness import assess_notice_sides, apply_notice_sides_fact


UKPPO_FACE = """
Parking Charge Notice
If you were not the driver, please supply the full name and current serviceable
postal address of the driver so that liability of the Parking Charge may be
transferred to the driver.
After 28 days, under Schedule 4 of the Protection of Freedoms Act 2012, we have
the right to recover the unpaid amount from the keeper of the vehicle.
"""


class ParseDisclosure(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(parse_disclosure(None), DISCLOSURE_UNKNOWN)
        self.assertEqual(parse_disclosure(""), DISCLOSURE_UNKNOWN)
        self.assertEqual(parse_disclosure("unknown"), DISCLOSURE_UNKNOWN)
        self.assertEqual(parse_disclosure(True), DISCLOSURE_YES)
        self.assertEqual(parse_disclosure("yes"), DISCLOSURE_YES)
        self.assertEqual(parse_disclosure("true"), DISCLOSURE_YES)
        self.assertEqual(parse_disclosure(False), DISCLOSURE_NO)
        self.assertEqual(parse_disclosure("false"), DISCLOSURE_NO)
        self.assertEqual(parse_disclosure("False"), DISCLOSURE_NO)
        self.assertEqual(parse_disclosure("NO"), DISCLOSURE_NO)
        # Non-empty junk must NOT become yes (generic truthiness defect).
        self.assertEqual(parse_disclosure("nothign"), DISCLOSURE_UNKNOWN)
        self.assertEqual(parse_disclosure("I parked"), DISCLOSURE_UNKNOWN)


class DisclosureVsNarrative(unittest.TestCase):
    def test_first_person_does_not_set_disclosure(self):
        case = CaseFile("C-narr")
        case.raw_answers["narrative"] = "I parked and left the children in the car"
        apply_disclosure(case, None, source="test")
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_UNKNOWN)
        self.assertEqual(case.driver_status, DriverStatus.UNIDENTIFIED)
        self.assertFalse(keeper_route_blocked(case))
        res = assess(
            jurisdiction="ENGLAND_WALES", relevant_land=True, notice_route="POSTAL",
            parking_event_date=None, notice_issue_date=None,
            driver_identified=keeper_route_blocked(case),
        )
        self.assertNotEqual(res.notes[:1], ["Driver formally identified - keeper route not used."])

    def test_unanswered_keeps_keeper_path(self):
        case = CaseFile("C-unans")
        apply_disclosure(case, None, source="test")
        self.assertFalse(keeper_route_blocked(case))
        self.assertEqual(case.driver_status, DriverStatus.UNIDENTIFIED)

    def test_explicit_yes_blocks_keeper(self):
        case = CaseFile("C-yes")
        apply_disclosure(case, True, source="test")
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_YES)
        self.assertEqual(case.driver_status, DriverStatus.FORMALLY_IDENTIFIED)
        self.assertTrue(keeper_route_blocked(case))
        events = [a for a in case.audit if a.get("event") == "driver_disclosure_set"]
        self.assertEqual(events[-1]["raw_submitted"], True)

    def test_explicit_no_and_string_false(self):
        for raw in (False, "false", "False", "no", "0"):
            case = CaseFile(f"C-no-{raw}")
            apply_disclosure(case, raw, source="test")
            self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_NO, raw)
            self.assertEqual(case.driver_status, DriverStatus.UNIDENTIFIED, raw)
            self.assertFalse(keeper_route_blocked(case), raw)

    def test_reanalysis_preserves_disclosure(self):
        case = CaseFile("C-re")
        apply_disclosure(case, False, source="confirm")
        # Simulate reanalysis not touching disclosure.
        case.put(Fact("F-x", "pcn_number", "1", FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:pcn_number")))
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_NO)
        self.assertEqual(case.driver_status, DriverStatus.UNIDENTIFIED)

    def test_auditable_correction_not_blanket(self):
        case = CaseFile("C-fix")
        apply_disclosure(case, True, source="confirm")
        self.assertTrue(keeper_route_blocked(case))
        correct_disclosure(case, DISCLOSURE_UNKNOWN, reason="customer confirms driver was not named")
        self.assertFalse(keeper_route_blocked(case))
        events = [a for a in case.audit if a.get("event") == "driver_disclosure_corrected"]
        self.assertEqual(events[-1]["reason"], "customer confirms driver was not named")
        self.assertEqual(events[-1]["previous_driver_status"], "FORMALLY_IDENTIFIED")


class IndependentCases(unittest.TestCase):
    def test_stale_state_not_shared(self):
        a = CaseFile("A")
        b = CaseFile("B")
        apply_disclosure(a, True, source="a")
        apply_disclosure(b, None, source="b")
        self.assertTrue(keeper_route_blocked(a))
        self.assertFalse(keeper_route_blocked(b))


class NoticeCompleteness(unittest.TestCase):
    def test_front_only_incomplete(self):
        case = CaseFile("C-front", evidence={
            "E1": EvidenceItem("E1", "NTK", "front.jpg", text="PCN front", images=[b"FRONTIMG"]),
        })
        case.document_classes["E1"] = "NTK"
        r = assess_notice_sides(case)
        self.assertTrue(r["applicable"])
        self.assertFalse(r["complete"])

    def test_duplicate_fronts_rejected(self):
        img = b"SAMEJPEGBYTES"
        case = CaseFile("C-dup", evidence={
            "E1": EvidenceItem("E1", "NTK", "front1.jpg", text="PCN", images=[img]),
            "E2": EvidenceItem("E2", "NTK", "front2.jpg", text="PCN", images=[img]),
        })
        case.document_classes["E1"] = "NTK"
        case.document_classes["E2"] = "NTK"
        r = assess_notice_sides(case)
        self.assertFalse(r["complete"])
        self.assertTrue(r["duplicate_pages"])
        self.assertEqual(r["reason"], "duplicate_front_images")

    def test_distinct_pages_complete(self):
        case = CaseFile("C-ok", evidence={
            "E1": EvidenceItem("E1", "NTK", "front.jpg", text="front", images=[b"FRONT"]),
            "E2": EvidenceItem("E2", "NTK", "back.jpg", text="reverse Schedule 4", images=[b"BACKXX"]),
        })
        case.document_classes["E1"] = "NTK"
        case.document_classes["E2"] = "NTK"
        apply_notice_sides_fact(case)
        self.assertTrue(case.get("notice_sides_complete"))

    def test_multipage_text_complete(self):
        case = CaseFile("C-pdf", evidence={
            "E1": EvidenceItem("E1", "NTK", "notice.pdf",
                               text="page1\fpage2 how to appeal Schedule 4"),
        })
        case.document_classes["E1"] = "NTK"
        apply_notice_sides_fact(case)
        self.assertTrue(case.get("notice_sides_complete"))

    def test_out_of_scope_not_forced(self):
        case = CaseFile("C-debt", evidence={
            "E1": EvidenceItem("E1", "DEBT_RECOVERY", "debt.pdf", text="debt recovery letter"),
        })
        case.document_classes["E1"] = "DEBT_RECOVERY"
        r = assess_notice_sides(case)
        self.assertFalse(r["applicable"])
        self.assertIsNone(r["complete"])


class PofaContentStatus(unittest.TestCase):
    def _case(self, text: str, disclosed: bool = False) -> CaseFile:
        case = CaseFile("C-pofa", evidence={
            "E1": EvidenceItem("E1", "NTK", "ntk.jpg", text=text),
        })
        case.document_classes["E1"] = "NTK"
        case.put(Fact("F-notice_route", "notice_route", "POSTAL", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "t")))
        case.put(Fact("F-jurisdiction", "jurisdiction", "ENGLAND_WALES", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "t")))
        apply_disclosure(case, True if disclosed else False, source="t")
        return case

    def test_reverse_with_pass_on_satisfied(self):
        text = UKPPO_FACE + " Please pass this notice on to the driver if you were not driving."
        case = self._case(text)
        eng = FactRecoveryEngine(KnowledgeGraph())
        report = eng.recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "SATISFIED")
        self.assertNotIn("POFA_NTK_INVITATION_DEFECT", report.calculated.get("pofa_findings") or [])

    def test_face_without_pass_on_unresolved_until_both_sides(self):
        case = self._case(UKPPO_FACE)
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete", False,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
        eng = FactRecoveryEngine(KnowledgeGraph())
        report = eng.recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "UNRESOLVED")
        self.assertNotIn("POFA_NTK_INVITATION_DEFECT", report.calculated.get("pofa_findings") or [])

    def test_both_sides_without_pass_on_is_defect(self):
        case = self._case(UKPPO_FACE)
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete", True,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
        eng = FactRecoveryEngine(KnowledgeGraph())
        report = eng.recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "DEFECT_IDENTIFIED")
        self.assertIn("POFA_NTK_INVITATION_DEFECT", report.calculated.get("pofa_findings") or [])

    def test_content_finding_survives_timing_assessment(self):
        from pcn_appeal.engines.reasoning import ReasoningEngine
        case = self._case(UKPPO_FACE)
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete", True,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
        eng = FactRecoveryEngine(KnowledgeGraph())
        eng.recover(case)
        self.assertEqual(case.get("pofa_finding"), "POFA_NTK_INVITATION_DEFECT")
        # Timing often UNRESOLVED without dates — must not wipe content finding.
        reason = ReasoningEngine(KnowledgeGraph())
        _, pofa_res = reason.applicability(case)
        self.assertIn("POFA_NTK_INVITATION_DEFECT", pofa_res.findings)
        self.assertEqual(case.get("pofa_finding"), "POFA_NTK_INVITATION_DEFECT")

    def test_first_person_does_not_disable_content(self):
        case = self._case(UKPPO_FACE)
        case.put(Fact("F-notice_sides_complete", "notice_sides_complete", True,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
        case.raw_answers["narrative"] = "I parked and I was driving"
        eng = FactRecoveryEngine(KnowledgeGraph())
        eng.recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "DEFECT_IDENTIFIED")
        self.assertEqual(case.driver_status.value, "UNIDENTIFIED")

    def test_insufficient_text_unresolved(self):
        case = self._case("PCN")
        eng = FactRecoveryEngine(KnowledgeGraph())
        eng.recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "UNRESOLVED")

    def test_disclosure_does_not_run_keeper_content(self):
        case = self._case(UKPPO_FACE, disclosed=True)
        eng = FactRecoveryEngine(KnowledgeGraph())
        report = eng.recover(case)
        self.assertEqual(case.get("pofa_9_2_e_status"), "NOT_APPLICABLE")
        self.assertEqual(report.calculated.get("pofa_route"), "NOT_APPLICABLE")
        self.assertIn("Driver formally identified", " ".join(report.calculated.get("pofa_notes") or []))


class ApiDisclosureAndCompleteness(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_string_false_form_does_not_identify(self):
        # Empty files rejected; exercise parse via JSON confirm path instead.
        created = self.client.post("/cases").json()
        cid = created["case_id"]
        self.client.post(f"/cases/{cid}/documents", json={
            "documents": [{
                "evidence_id": "E1", "filename": "ntk.txt", "kind": "NTK",
                "text": "Notice to Keeper\nPCN 1\nVRM AB12CDE\n" + UKPPO_FACE,
            }],
        })
        res = self.client.post(f"/cases/{cid}/confirm", json={
            "corrections": {}, "confirmed": ["pcn_number"],
            "narrative": "I parked near the entrance",
            "driver_already_named_to_operator": "false",
        })
        # May be NEEDS_DOCUMENTS (front-only) or questions — either way not FORMALLY via string false.
        body = res.json()
        from pcn_appeal.api import CASES
        case = CASES[cid]["case"]
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_NO)
        self.assertEqual(case.driver_status, DriverStatus.UNIDENTIFIED)
        self.assertNotIn("Driver formally identified", str(body))

    def test_omitted_field_is_unknown(self):
        created = self.client.post("/cases").json()
        cid = created["case_id"]
        self.client.post(f"/cases/{cid}/documents", json={
            "documents": [{
                "evidence_id": "E1", "filename": "ntk.txt", "kind": "NTK",
                "text": "Notice\n" + UKPPO_FACE,
            }],
        })
        self.client.post(f"/cases/{cid}/confirm", json={
            "corrections": {}, "confirmed": [],
            "narrative": "I left the children in the car",
        })
        from pcn_appeal.api import CASES
        case = CASES[cid]["case"]
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_UNKNOWN)
        self.assertEqual(case.driver_status, DriverStatus.UNIDENTIFIED)

    def test_files_upload_accepts_the_front_alone(self):
        """Server gate used by /cases/.../files before ingest advances the case.

        The back page is optional, so a front-only upload is not refused and
        the evidence is left in place.
        """
        from pcn_appeal.api import _require_front_page

        case = CaseFile("C-one")
        case.evidence["E1"] = EvidenceItem(
            "E1", "NTK", "front.jpg", images=[b"ONLY_ONE_PAGE_JPEG_BYTES"],
        )
        _require_front_page(case)          # must not raise
        self.assertEqual(len(case.evidence), 1)

    def test_files_upload_rejects_an_upload_with_no_page_at_all(self):
        from fastapi import HTTPException
        from pcn_appeal.api import _require_front_page

        case = CaseFile("C-none")
        case.evidence["E1"] = EvidenceItem("E1", "NTK", "empty.jpg", text="  ")
        with self.assertRaises(HTTPException) as ctx:
            _require_front_page(case)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail.get("code"), "NOTICE_SIDES_REQUIRED")
        self.assertEqual(len(case.evidence), 0)
        self.assertEqual(case.state, CaseState.CREATED)

    def test_files_upload_accepts_distinct_front_and_back(self):
        from pcn_appeal.notice_completeness import upload_pages_sufficient
        items = [
            EvidenceItem("E1", "NTK", "front.jpg", images=[b"FRONT_PAGE_BYTES_AAA"]),
            EvidenceItem("E2", "NTK", "back.jpg", images=[b"BACK_PAGE_BYTES_BBB"]),
        ]
        ok, reason = upload_pages_sufficient(items)
        self.assertTrue(ok, reason)

    def test_duplicate_front_images_rejected_by_gate(self):
        from pcn_appeal.notice_completeness import upload_pages_sufficient
        same = b"SAME_PAGE_BYTES_XXXX"
        items = [
            EvidenceItem("E1", "NTK", "front1.jpg", images=[same]),
            EvidenceItem("E2", "NTK", "front2.jpg", images=[same]),
        ]
        ok, reason = upload_pages_sufficient(items)
        self.assertFalse(ok)
        self.assertEqual(reason, "duplicate_front_images")

    def test_confirm_continues_on_a_front_only_notice(self):
        """The reverse is optional: confirm proceeds instead of holding the case
        on a NEEDS_DOCUMENTS screen, and the narrative is still kept."""
        created = self.client.post("/cases").json()
        cid = created["case_id"]
        self.client.post(f"/cases/{cid}/documents", json={
            "documents": [{
                "evidence_id": "E1", "filename": "front.jpg", "kind": "NTK",
                "text": "Notice to Keeper\nPCN 99\nFront face only.",
            }],
        })
        from pcn_appeal.api import CASES
        case = CASES[cid]["case"]
        self.assertEqual(case.evidence["E1"].kind, "NTK")
        res = self.client.post(f"/cases/{cid}/confirm", json={
            "corrections": {}, "confirmed": [],
            "narrative": "nothing",
        })
        body = res.json()
        self.assertNotEqual(body.get("outcome"), "NEEDS_DOCUMENTS", body)
        self.assertNotIn("notice_sides_incomplete", body.get("flags") or [])
        # No question asks for the back page, and none offers a free-text box
        # to describe it.
        self.assertNotIn("notice_reverse_pages",
                         [q.get("fact") for q in body.get("questions") or []])
        case = CASES[cid]["case"]
        self.assertEqual(case.raw_answers.get("narrative"), "nothing")

    def test_explicit_yes_sets_formally_identified(self):
        created = self.client.post("/cases").json()
        cid = created["case_id"]
        self.client.post(f"/cases/{cid}/documents", json={
            "documents": [{
                "evidence_id": "E1", "filename": "ntk.pdf", "kind": "NTK",
                "text": (
                    "Notice to Keeper page1 front\f"
                    "page2 reverse how to appeal Schedule 4 "
                    "pass this notice to the driver\n" + UKPPO_FACE
                ),
            }],
        })
        self.client.post(f"/cases/{cid}/confirm", json={
            "corrections": {}, "confirmed": [],
            "narrative": "payment was made",
            "driver_already_named_to_operator": True,
        })
        from pcn_appeal.api import CASES
        case = CASES[cid]["case"]
        self.assertEqual(case.driver_status, DriverStatus.FORMALLY_IDENTIFIED)
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_YES)


class OutcomeBoundaryCopy(unittest.TestCase):
    def test_empty_grounds_processing_not_merits_copy(self):
        from pcn_appeal.engines.outcome import (
            OUTCOME_PROCESSING_ERROR, classify_hold, CUSTOMER_COPY,
        )
        from pcn_appeal.models import RetrievalPack, ValidationResult

        case = CaseFile("C-empty")
        case.audit.append({"event": "no_ground"})
        pack = RetrievalPack(
            primary_route=None, secondary_routes=[], module_ids=[],
            verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
            prohibited_claims=[], code_version=None, pofa_route="UNRESOLVED",
            pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[], trace=[],
        )
        out = classify_hold(case, pack, ValidationResult(False, []))
        self.assertEqual(out["outcome"], OUTCOME_PROCESSING_ERROR)
        lede = CUSTOMER_COPY[OUTCOME_PROCESSING_ERROR]["lede"].lower()
        self.assertNotIn("would stand", lede)
        self.assertIn("processing", lede)


class KeeperSafeRewriteUnchanged(unittest.TestCase):
    def test_rewrite_does_not_touch_disclosure(self):
        from pcn_appeal.engines.questioning import keeper_safe_text
        raw = "I parked and I left the children in the car"
        out = keeper_safe_text(raw)
        self.assertNotIn("I parked", out)
        self.assertIn("child", out.lower())
        case = CaseFile("C-ks")
        apply_disclosure(case, None, source="t")
        case.raw_answers["narrative"] = raw
        # Rewrite path must leave disclosure UNKNOWN.
        self.assertEqual(case.get("driver_disclosure_to_operator"), DISCLOSURE_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
