"""Regressions from the false Euro Car Parks pair mismatch, plus recovery."""
import io
import subprocess
import unittest
from unittest.mock import patch
from PIL import Image

from pcn_appeal import document_identity as identity, ocr
from pcn_appeal.document_retry import reopen
from pcn_appeal.engines.extraction import ExtractionEngine
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem, Fact, FactStatus, FactSource, SourceKind


def notice_case():
    case = CaseFile("readiness")
    for ev, side, vrm in (("E1", "REVERSE", "KS80OPW"), ("E2", "FRONT", "KS58OPW")):
        case.evidence[ev] = EvidenceItem(ev, "NTK", side + ".jpg")
        case.classifications[ev] = {"document_type": "PRIVATE_PARKING_NOTICE",
                                    "references": {"vrm": vrm},
                                    "pages": [{"page": 1, "side": side}]}
    for name, value in (("operator_name", "Euro Car Parks Limited"),
                        ("pcn_number", "12345678901"), ("vrm", "KS58OPW")):
        case.put(Fact("F-" + name, name, value, FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E2#p1"), confidence=.99))
    return case


class ReadingRecovery(unittest.TestCase):
    def test_close_model_misread_is_a_field_conflict_not_foreign_pages(self):
        case = notice_case()
        state = identity.establish_document_identity(case)
        self.assertFalse(state.document_pair_conflict)
        self.assertEqual(state.field_status["vrm"], identity.STATUS_CONFLICT)
        question = identity.identity_customer_questions(case)[0]
        self.assertEqual((question["fact"], question["type"]), ("vrm", "text"))
        self.assertNotEqual(identity.identity_blocks_claim_plan(case), "DOCUMENT_PAIR_CONFLICT")

    def test_explicit_same_value_confirmation_clears_the_misread_and_survives_resume(self):
        case = notice_case()
        identity.establish_document_identity(case)
        ExtractionEngine.confirm(case, {"vrm": "KS58OPW"}, [])
        for _ in range(2):
            state = identity.establish_document_identity(case)
            self.assertEqual(state.field_status["vrm"], identity.STATUS_VERIFIED)
            self.assertFalse(state.document_pair_conflict)
            self.assertEqual(case.get("vrm"), "KS58OPW")

    def test_blanket_confirmation_does_not_clear_conflicts(self):
        case = notice_case()
        identity.establish_document_identity(case)
        ExtractionEngine.confirm(case, {}, ["vrm"])
        self.assertEqual(case.facts["vrm"].status, FactStatus.UNCERTAIN)
        self.assertIsNotNone(identity.identity_blocks_claim_plan(case))

    def test_foreign_pages_remain_blocked_after_customer_field_correction(self):
        case = notice_case()
        case.classifications["E1"]["references"]["vrm"] = "ZZ99ZZZ"
        identity.establish_document_identity(case)
        ExtractionEngine.confirm(case, {"vrm": "KS58OPW"}, [])
        self.assertEqual(identity.identity_blocks_claim_plan(case), "DOCUMENT_PAIR_CONFLICT")
        self.assertEqual(identity.identity_customer_questions(case), [])

    def test_operator_suffix_and_case_are_equivalent_but_other_operators_are_not(self):
        def obs(raw):
            return identity._obs(raw=raw, name="operator_name", method="initial_extraction", confidence=.99)
        rec = identity.reconcile_field("operator_name", [obs("Euro Car Parks Limited"),
                                                        obs("EURO CAR PARKS LTD.")], revision=1)
        self.assertEqual(rec.status, identity.STATUS_VERIFIED)
        rec = identity.reconcile_field("operator_name", [obs("Euro Car Parks"),
                                                        obs("Other Parking Ltd")], revision=1)
        self.assertEqual(rec.status, identity.STATUS_CONFLICT)

    def test_phone_and_footer_digits_cannot_corroborate_a_wrong_charge_number(self):
        case = notice_case()
        case.evidence["E2"].text = "Payment telephone 02035534559\nCompany no 12345678901\nPCN Number: AB12345678"
        rows = identity._observations_from_text_scan(case)["pcn_number"]
        self.assertEqual([r.canonical_value for r in rows], ["AB12345678"])

    def test_invalid_date_cannot_become_verified(self):
        case = notice_case()
        identity.establish_document_identity(case)
        self.assertFalse(identity.confirm_identity_field(case, "notice_issue_date", "not a date"))

    def test_replacement_preserves_account_and_audit_but_retracts_old_notice_values(self):
        case = notice_case()
        case.state = CaseState.MANUAL_REVIEW
        case.raw_answers.update(narrative="The vehicle left and returned", _identity_verify_cache="stale")
        case.put(Fact("F-payment_made", "payment_made", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:payment_made")))
        reopened = reopen(case)
        self.assertEqual(reopened.case_id, case.case_id)
        self.assertEqual(reopened.state, CaseState.CREATED)
        self.assertTrue(reopened.get("payment_made"))
        self.assertIsNone(reopened.get("vrm"))
        self.assertFalse(reopened.evidence)
        self.assertNotIn("_identity_verify_cache", reopened.raw_answers)
        self.assertEqual(reopened.raw_answers["narrative"], "The vehicle left and returned")
        self.assertTrue(any(h["outcome"] == "RETRACTED" for h in reopened.fact_history))

    def test_replacement_survives_database_reload(self):
        from tests import sqlite_store
        from pcn_appeal.store import cases as store
        sqlite_store.install(self)
        case = notice_case()
        case.case_id = store.new_case().case_id
        case.state = CaseState.MANUAL_REVIEW
        case.raw_answers["narrative"] = "The vehicle left and returned"
        store.save(case)
        store.save(reopen(case))
        loaded = store.load(case.case_id)
        self.assertEqual(loaded.state, CaseState.CREATED)
        self.assertFalse(loaded.evidence)
        self.assertIsNone(loaded.get("vrm"))
        self.assertEqual(loaded.raw_answers["narrative"], "The vehicle left and returned")

    def test_replacement_endpoint_keeps_case_id_and_clears_pending_output(self):
        from fastapi.testclient import TestClient
        from pcn_appeal import api
        case = notice_case()
        case.state = CaseState.MANUAL_REVIEW
        rec = {"case": case, "pipe": None, "output": object(), "questions": [{"fact": "vrm"}], "flags": ["vrm"]}
        with patch.dict(api.CASES, {case.case_id: rec}), patch("pcn_appeal.api._persist"):
            response = TestClient(api.app).post(f"/cases/{case.case_id}/reopen-upload")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"case_id": case.case_id, "state": "CREATED"})
        self.assertIsNone(rec["output"])
        self.assertFalse(rec["questions"])
        self.assertTrue(api.is_customer_route("POST", f"/cases/{case.case_id}/reopen-upload"))

    def test_released_case_cannot_be_reopened(self):
        case = notice_case()
        case.state = CaseState.RELEASED
        with self.assertRaises(ValueError):
            reopen(case)


class IndependentOCR(unittest.TestCase):
    def test_missing_engine_retains_vision(self):
        with patch("pcn_appeal.ocr.available", return_value=False):
            self.assertEqual(ocr.transcribe_pages([b"photo"])[0], "")

    def test_transcription_has_page_provenance_and_is_not_confirmed(self):
        result = subprocess.CompletedProcess([], 0, b"PCN Number: AB12345678", b"")
        with patch("pcn_appeal.ocr.available", return_value=True), \
             patch("pcn_appeal.ocr._prepare", return_value=b"png"), \
             patch("pcn_appeal.ocr.shutil.which", return_value="tesseract"), \
             patch("pcn_appeal.ocr.subprocess.run", return_value=result) as run:
            text, note = ocr.transcribe_pages([b"first", b"second"])
            self.assertIn('page="2"', text)
            self.assertIn("not customer confirmation", text)
            self.assertIn("2 page(s)", note)
            self.assertEqual(run.call_args.kwargs["timeout"], 12)

    def test_timeout_does_not_lose_the_upload(self):
        with patch("pcn_appeal.ocr.available", return_value=True), \
             patch("pcn_appeal.ocr._prepare", return_value=b"png"), \
             patch("pcn_appeal.ocr.shutil.which", return_value="tesseract"), \
             patch("pcn_appeal.ocr.subprocess.run", side_effect=subprocess.TimeoutExpired("tesseract", 12)):
            text, note = ocr.transcribe_pages([b"photo"])
            self.assertFalse(text)
            self.assertIn("1 failed", note)

    def test_preprocessing_keeps_small_print_and_bounds_large_rasters(self):
        source = io.BytesIO()
        Image.new("RGB", (4000, 2000), "white").save(source, "JPEG")
        with Image.open(io.BytesIO(ocr._prepare(source.getvalue()))) as raster:
            self.assertEqual(raster.mode, "L")
            self.assertEqual(raster.size, (3200, 1600))


if __name__ == "__main__":
    unittest.main()
