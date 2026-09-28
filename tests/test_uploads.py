"""Uploads: what a customer actually has -> something Engine 1 can read.

A PCN arrives on paper or as a PDF attachment, so the two paths that matter are
a PDF with a text layer (read as text) and one without (rasterised for the
vision model). Fixtures are generated here rather than committed as binaries,
so the assertions describe behaviour instead of pinning bytes.
"""
import io
import unittest

from fastapi.testclient import TestClient

from pcn_appeal.api import app
from support import demo_asking, patch_client
from pcn_appeal.ingest import MIN_TEXT_CHARS, UnsupportedUpload, read_upload

NOTICE_LINES = [
    "PARKING CHARGE NOTICE",
    "Operator Name: Acme Parking Ltd",
    "PCN Number: PCN778899",
    "Vehicle Registration: KX19 PLT",
    "Location: Riverside Retail Park",
    "Postcode: M1 4BT",
    "Date of Contravention: 12/06/2026",
    "Date of Issue: 02/07/2026",
    "Charge: 100",
    "Alleged Breach: Overstayed the maximum permitted period",
    "Trade Association: BPA",
]


def text_pdf(lines=NOTICE_LINES) -> bytes:
    """A born-digital PDF: the text is selectable, so no pixels are needed."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((60, 80 + i * 18), line, fontsize=11)
    out = doc.tobytes()
    doc.close()
    return out


def scanned_pdf() -> bytes:
    """A photographed notice: a page-sized image and no text layer at all."""
    import pymupdf
    src = pymupdf.open(stream=text_pdf(), filetype="pdf")
    pix = src[0].get_pixmap(dpi=80)
    src.close()
    doc = pymupdf.open()
    page = doc.new_page(width=pix.width, height=pix.height)
    page.insert_image(page.rect, pixmap=pix)
    out = doc.tobytes()
    doc.close()
    return out


def png_photo() -> bytes:
    import pymupdf
    src = pymupdf.open(stream=text_pdf(), filetype="pdf")
    pix = src[0].get_pixmap(dpi=80)
    src.close()
    return pix.tobytes("png")


# What the analysis model would ask on a payment-shaped case. Supplied as a
# fixture because the demo reader cannot judge materiality - the mechanism under
# test is the asking, answering and looping, not the choice of question.
PAYMENT_QUESTIONS = [
    {"fact": "payment_made", "text": "Was a parking payment made or attempted?", "type": "bool"},
    {"fact": "payment_method", "text": "How was the payment made?", "type": "choice",
     "options": ["APP", "MACHINE", "PHONE", "WEBSITE", "OTHER"]},
]
FOLLOW_UP = [{"fact": "payment_attempt_failed",
              "text": "Did the payment facility fail to take the payment?", "type": "bool"}]

class Ingest(unittest.TestCase):
    def test_text_pdf_is_read_as_text_not_pixels(self):
        got = read_upload("E1", "notice.pdf", "application/pdf", text_pdf())
        self.assertIn("PCN778899", got.text)
        self.assertEqual(got.images, [], "a text layer makes rasterising pointless")
        self.assertGreaterEqual(len(got.text.strip()), MIN_TEXT_CHARS)

    def test_scanned_pdf_falls_back_to_the_vision_model(self):
        got = read_upload("E1", "scan.pdf", "application/pdf", scanned_pdf())
        self.assertLess(len(got.text.strip()), MIN_TEXT_CHARS)
        self.assertEqual(len(got.images), 1)
        self.assertTrue(got.readable)

    def test_images_are_normalised_to_jpeg(self):
        """The LLM clients hardcode image/jpeg, so anything else must be
        converted rather than mislabelled."""
        got = read_upload("E1", "photo.png", "image/png", png_photo())
        self.assertEqual(len(got.images), 1)
        self.assertEqual(got.images[0][:2], b"\xff\xd8", "not a JPEG")

    def test_magic_bytes_beat_a_wrong_content_type(self):
        """Browsers and email clients mislabel attachments constantly."""
        got = read_upload("E1", "notice", "application/octet-stream", text_pdf())
        self.assertIn("PCN778899", got.text)

    def test_extension_is_used_when_the_type_is_missing(self):
        got = read_upload("E1", "notes.md", None, b"Operator Name: Acme Parking Ltd")
        self.assertIn("Acme", got.text)

    def test_unsupported_type_is_refused_with_a_reason(self):
        with self.assertRaises(UnsupportedUpload) as caught:
            read_upload("E1", "bundle.zip", "application/zip", b"PK\x03\x04junk")
        self.assertIn("zip", str(caught.exception).lower())

    def test_empty_file_is_refused(self):
        with self.assertRaises(UnsupportedUpload):
            read_upload("E1", "notice.pdf", "application/pdf", b"")

    def test_oversized_file_is_refused_before_decoding(self):
        with self.assertRaises(UnsupportedUpload) as caught:
            read_upload("E1", "huge.pdf", "application/pdf", b"%PDF-" + b"0" * (13 * 1024 * 1024))
        self.assertIn("MB", str(caught.exception))


class BoolAnswers(unittest.TestCase):
    """A sharp edge the UI has to respect: a bool answer that is not a
    recognised yes is recorded as no, silently. A checkbox or yes/no control
    must therefore send a real boolean, never free text."""

    def test_unrecognised_text_reads_as_no(self):
        from pcn_appeal.api import KG
        from pcn_appeal.engines.questioning import QuestionEngine
        from pcn_appeal.models import CaseFile

        case = CaseFile(case_id="C-bool")
        engine = QuestionEngine(KG, llm=None)
        engine.record_answer(case, "payment_made", "maybe")
        self.assertIs(case.facts["payment_made"].value, False)
        self.assertEqual(case.raw_answers["payment_made"], "maybe",
                         "the customer's own wording is kept for audit only")

    def test_yes_spellings_all_read_as_yes(self):
        from pcn_appeal.api import KG
        from pcn_appeal.engines.questioning import QuestionEngine
        from pcn_appeal.models import CaseFile

        for raw in (True, "yes", "Yes", "y", "true", "1"):
            case = CaseFile(case_id="C-bool")
            QuestionEngine(KG, llm=None).record_answer(case, "payment_made", raw)
            self.assertIs(case.facts["payment_made"].value, True, f"{raw!r} should mean yes")


class UploadEndpoint(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def post(self, files, narrative="the letter arrived weeks after the visit"):
        return self.c.post("/appeal/files", files=files, data={"narrative": narrative})

    def test_pdf_upload_runs_the_whole_journey(self):
        r = self.post([("files", ("notice.pdf", io.BytesIO(text_pdf()), "application/pdf"))])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["case_id"])
        self.assertEqual(body["rejected"], [])
        self.assertEqual(len(body["read_as"]), 1)
        self.assertGreater(body["read_as"][0]["chars"], 0)
        # a complete notice leaves nothing gating a ground, so it goes straight
        # to a letter - the one-click case the upload page exists to serve
        self.assertEqual(body["questions"], [])
        self.assertEqual(body["state"], "RELEASED")
        self.assertTrue(body["letter"].strip())

    def test_photo_upload_is_accepted_even_though_demo_reads_no_pixels(self):
        """Without a vision-capable key the demo extractor finds nothing in an
        image. The upload must still be accepted and reported honestly."""
        r = self.post([("files", ("photo.png", io.BytesIO(png_photo()), "image/png"))])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["read_as"][0]["images"], 1)
        self.assertEqual(body["read_as"][0]["chars"], 0)
        self.assertIn("missing:operator_name", body["flags"],
                      "an unread field must be reported, not silently dropped")

    def test_one_bad_file_does_not_lose_the_case(self):
        r = self.post([
            ("files", ("notice.pdf", io.BytesIO(text_pdf()), "application/pdf")),
            ("files", ("bundle.zip", io.BytesIO(b"PK\x03\x04junk"), "application/zip")),
        ])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["read_as"]), 1)
        self.assertEqual([x["filename"] for x in body["rejected"]], ["bundle.zip"])

    def test_nothing_readable_is_a_422_that_says_why(self):
        r = self.post([("files", ("bundle.zip", io.BytesIO(b"PK\x03\x04junk"), "application/zip"))])
        self.assertEqual(r.status_code, 422)
        detail = r.json()["detail"]
        self.assertEqual(len(detail["rejected"]), 1)
        self.assertIn("zip", detail["rejected"][0]["reason"].lower())

    def test_a_photo_alone_asks_before_drafting(self):
        """The demo extractor reads no pixels, so nothing is known from the image
        and the narrative is all there is - exactly when the UI must ask."""
        patch_client(self, demo_asking(PAYMENT_QUESTIONS))
        r = self.post([("files", ("photo.png", io.BytesIO(png_photo()), "image/png"))],
                      narrative="I was only 5 minutes over and the machine would not take my card")
        body = r.json()
        self.assertEqual(body["state"], "CONFIRMED")
        self.assertTrue(body["questions"], "a photo alone should pause, not draft")
        self.assertIsNone(body.get("letter"), "no letter while a question is outstanding")
        for q in body["questions"]:
            self.assertEqual(set(q) - {"fact", "text", "type", "options"}, set())

    def test_a_choice_answer_outside_its_options_is_refused(self):
        """The frontend must send one of `options`; anything else is a 422 whose
        detail names the fact and the permitted values."""
        case_id = self._photo_case()
        bad = self.c.post(f"/appeal/{case_id}", json={"answers": {"payment_method": "CARROT"}})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("payment_method", bad.json()["detail"])

    def test_a_choice_answer_is_case_insensitive(self):
        case_id = self._photo_case()
        ok = self.c.post(f"/appeal/{case_id}",
                         json={"answers": {"payment_made": True, "payment_method": "machine"}})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_answering_can_reveal_a_follow_up_round(self):
        """The UI cannot assume one question round; it must loop until empty."""
        patch_client(self, demo_asking(PAYMENT_QUESTIONS + FOLLOW_UP))
        case_id = self._photo_case()
        nxt = self.c.post(f"/appeal/{case_id}",
                          json={"answers": {"payment_made": True, "payment_method": "MACHINE"}}).json()
        # An answered fact must not come back round again (Q-07).
        self.assertNotIn("payment_made", [q["fact"] for q in nxt.get("questions", [])])

    def _photo_case(self) -> str:
        r = self.post([("files", ("photo.png", io.BytesIO(png_photo()), "image/png"))],
                      narrative="the machine would not take my card")
        return r.json()["case_id"]

    def test_evidence_ids_are_stable_and_in_upload_order(self):
        """Facts cite evidence by this id, so E1 must stay the first file."""
        r = self.post([
            ("files", ("first.pdf", io.BytesIO(text_pdf()), "application/pdf")),
            ("files", ("second.txt", io.BytesIO(b"Charge: 100"), "text/plain")),
        ])
        body = r.json()
        self.assertEqual([x["evidence_id"] for x in body["read_as"]], ["E1", "E2"])
        self.assertEqual([x["filename"] for x in body["read_as"]], ["first.pdf", "second.txt"])

    def test_uploaded_case_can_be_continued_and_skipped_to_a_letter(self):
        r = self.post([("files", ("notice.pdf", io.BytesIO(text_pdf()), "application/pdf"))])
        case_id = r.json()["case_id"]
        done = self.c.post(f"/appeal/{case_id}", json={"answers": {}, "skip": True})
        self.assertEqual(done.status_code, 200, done.text)
        body = done.json()
        self.assertEqual(body["questions"], [])
        self.assertEqual(body["state"], "RELEASED")
        self.assertTrue(body["letter"].strip())



class LetterPdf(unittest.TestCase):
    """The `letter` field is plain text for validation; `/cases/{id}/letter.pdf`
    is the actual document a customer can send. It must not exist before the
    case is genuinely RELEASED - laying out a MANUAL_REVIEW draft would hand
    a customer a letter nobody approved."""

    def setUp(self):
        self.c = TestClient(app)

    def test_pdf_is_offered_and_downloadable_once_released(self):
        r = self.c.post("/appeal/files",
                        files=[("files", ("pcn.pdf", io.BytesIO(text_pdf()), "application/pdf"))],
                        data={"narrative": ""})
        body = r.json()
        self.assertEqual(body["state"], "RELEASED")
        self.assertEqual(body["letter_pdf_url"], f"/cases/{body['case_id']}/letter.pdf")

        pdf = self.c.get(body["letter_pdf_url"])
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertEqual(pdf.content[:5], b"%PDF-")
        self.assertGreater(len(pdf.content), 1000)

    def test_pdf_404s_before_release(self):
        patch_client(self, demo_asking(PAYMENT_QUESTIONS))
        r = self.c.post("/appeal/files",
                        files=[("files", ("photo.png", io.BytesIO(png_photo()), "image/png"))],
                        data={"narrative": "the machine would not take my card"})
        body = r.json()
        self.assertEqual(body["state"], "CONFIRMED")
        self.assertNotIn("letter_pdf_url", body)
        self.assertEqual(self.c.get(f"/cases/{body['case_id']}/letter.pdf").status_code, 404)

    def test_pdf_404s_for_an_unknown_case(self):
        self.assertEqual(self.c.get("/cases/C-9999/letter.pdf").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class ExtractionResponseShape(unittest.TestCase):
    """A live model returned the fields at the top level rather than under
    "fields", and the engine silently produced an empty case. Both shapes are
    accepted now; these lock that in."""

    FIELDS = {
        "operator_name": {"value": "Acme Parking Ltd", "confidence": 1, "evidence_id": "E1", "page": 1},
        "pcn_number": {"value": "PCN778899", "confidence": 1, "evidence_id": "E1", "page": 1},
        "vrm": {"value": "KX19 PLT", "confidence": 1, "evidence_id": "E1", "page": 1},
        "parking_event_date": {"value": "12/06/2026", "confidence": 1, "evidence_id": "E1", "page": 1},
        "notice_issue_date": {"value": "02/07/2026", "confidence": 1, "evidence_id": "E1", "page": 1},
        "site_postcode": {"value": "M1 4BT", "confidence": 1, "evidence_id": "E1", "page": 1},
        "charge_amount": {"value": "100", "confidence": 1, "evidence_id": "E1", "page": 1},
        "alleged_breach": {"value": "Overstay", "confidence": 1, "evidence_id": "E1", "page": 1},
        "operator_ata": {"value": "BPA", "confidence": 1, "evidence_id": "E1", "page": 1},
    }

    def _run(self, response):
        from pcn_appeal.llm import FakeLLM
        from pcn_appeal.models import CaseFile, EvidenceItem
        from pcn_appeal.orchestrator import AppealPipeline

        case = CaseFile("C-SHAPE", evidence={"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="PCN")})
        pipe = AppealPipeline(FakeLLM({"extraction": [response]}))
        flags = pipe.ingest(case)
        return case, flags

    def test_nested_under_fields(self):
        case, flags = self._run({"fields": self.FIELDS, "doc_types": {"E1": "PCN"}})
        self.assertEqual(case.get("pcn_number"), "PCN778899")
        self.assertNotIn("missing:pcn_number", flags)

    def test_flat_at_the_top_level(self):
        case, flags = self._run({**self.FIELDS, "doc_types": {"E1": "PCN"}})
        self.assertEqual(case.get("pcn_number"), "PCN778899")
        self.assertEqual(case.get("vrm"), "KX19PLT")          # normalised, EX-08
        self.assertNotIn("missing:pcn_number", flags)

    def test_doc_types_is_not_mistaken_for_a_field(self):
        case, _ = self._run({**self.FIELDS, "doc_types": {"E1": "NTK"}})
        self.assertIsNone(case.get("doc_types"))
        self.assertEqual(case.evidence["E1"].kind, "NTK")
