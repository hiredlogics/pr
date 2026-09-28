"""Uploads that arrive as a URL rather than a request body.

The browser sends big photos straight to blob storage and the API fetches them,
so the URL is attacker-controlled input. These tests are mostly about what the
fetcher refuses.
"""
import io
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal.api import app
from pcn_appeal.ingest import UnsupportedUpload, blob_hosts, fetch_upload

NOTICE = b"""PARKING CHARGE NOTICE
Operator Name: Acme Parking Ltd
PCN Number: PCN778899
Vehicle Registration: KX19 PLT
Postcode: M1 4BT
Date of Contravention: 12/06/2026
Date of Issue: 02/07/2026
Charge: 100
Alleged Breach: Overstay
Trade Association: BPA
"""

BLOB = "https://public.blob.vercel-storage.com/abc/notice.txt"


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, content_type: str = "text/plain"):
        super().__init__(data)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def patched_urlopen(data: bytes, content_type: str = "text/plain"):
    """Patch at the call site: fetch_upload imports urlopen inside the function."""
    return mock.patch("urllib.request.urlopen", return_value=FakeResponse(data, content_type))


class Fetching(unittest.TestCase):
    def test_reads_a_document_from_an_allowed_host(self):
        with patched_urlopen(NOTICE):
            doc = fetch_upload("E1", BLOB, "notice.txt")
        self.assertIn("PCN778899", doc.text)
        self.assertEqual(doc.filename, "notice.txt")

    def test_filename_falls_back_to_the_url_path(self):
        with patched_urlopen(NOTICE):
            doc = fetch_upload("E1", BLOB)
        self.assertEqual(doc.filename, "notice.txt")

    def test_http_is_refused(self):
        with self.assertRaises(UnsupportedUpload) as ctx:
            fetch_upload("E1", "http://public.blob.vercel-storage.com/x/notice.txt")
        self.assertIn("only https", str(ctx.exception))

    def test_a_host_off_the_allowlist_is_refused(self):
        """Without this the endpoint is an SSRF hole: 'fetch this URL for me'."""
        for url in (
            "https://169.254.169.254/latest/meta-data/",   # cloud metadata
            "https://localhost/admin",
            "https://evil.example.com/payload.pdf",
        ):
            with self.subTest(url=url), self.assertRaises(UnsupportedUpload) as ctx:
                fetch_upload("E1", url)
            self.assertIn("not an allowed upload host", str(ctx.exception))

    def test_a_lookalike_host_is_refused(self):
        with self.assertRaises(UnsupportedUpload):
            fetch_upload("E1", "https://public.blob.vercel-storage.com.evil.test/x")

    def test_subdomains_of_an_allowed_host_are_accepted(self):
        with patched_urlopen(NOTICE):
            doc = fetch_upload("E1", "https://eu.public.blob.vercel-storage.com/a/notice.txt")
        self.assertIn("PCN778899", doc.text)

    def test_allowlist_is_configurable(self):
        with patched_urlopen(NOTICE):
            doc = fetch_upload("E1", "https://my-bucket.s3.amazonaws.com/notice.txt",
                               allowed_hosts={"s3.amazonaws.com"})
        self.assertIn("PCN778899", doc.text)

    def test_oversized_download_is_refused_not_truncated(self):
        from pcn_appeal.ingest import MAX_BYTES
        with patched_urlopen(b"%PDF-" + b"x" * (MAX_BYTES + 1)):
            with self.assertRaises(UnsupportedUpload) as ctx:
                fetch_upload("E1", BLOB, "huge.pdf")
        self.assertIn("larger than", str(ctx.exception))

    def test_a_dead_url_is_a_reason_not_a_crash(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            with self.assertRaises(UnsupportedUpload) as ctx:
                fetch_upload("E1", BLOB, "notice.txt")
        self.assertIn("could not download", str(ctx.exception))

    def test_default_allowlist_is_vercel_blob(self):
        self.assertIn("public.blob.vercel-storage.com", blob_hosts())


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_blobs_run_extraction_and_reach_a_letter(self):
        case = self.c.post("/cases").json()["case_id"]
        with patched_urlopen(NOTICE):
            up = self.c.post(f"/cases/{case}/blobs",
                             json={"blobs": [{"url": BLOB, "filename": "notice.txt"}]})
        self.assertEqual(up.status_code, 200, up.text)
        self.assertEqual(up.json()["state"], "EXTRACTED")
        self.assertEqual(up.json()["rejected"], [])

        details = self.c.get(f"/cases/{case}/confirmation").json()["details"]
        by_name = {d["name"]: d["value"] for d in details}
        self.assertEqual(by_name["pcn_number"], "PCN778899")

        self.c.post(f"/cases/{case}/confirm",
                    json={"narrative": "the letter arrived weeks late", "confirmed": list(by_name)})
        final = self.c.post(f"/appeal/{case}", json={"skip": True}).json()
        self.assertEqual(final["state"], "RELEASED", final.get("blocking_issues"))

    def test_a_refused_url_is_reported_not_fatal(self):
        case = self.c.post("/cases").json()["case_id"]
        with patched_urlopen(NOTICE):
            r = self.c.post(f"/cases/{case}/blobs", json={"blobs": [
                {"url": BLOB, "filename": "notice.txt"},
                {"url": "https://evil.example.com/x.pdf", "filename": "x.pdf"},
            ]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([x["filename"] for x in r.json()["rejected"]], ["x.pdf"])
        self.assertEqual([x["filename"] for x in r.json()["read_as"]], ["notice.txt"])

    def test_nothing_usable_is_a_422_that_says_why(self):
        case = self.c.post("/cases").json()["case_id"]
        r = self.c.post(f"/cases/{case}/blobs",
                        json={"blobs": [{"url": "https://evil.example.com/x.pdf"}]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("nothing readable", r.json()["detail"]["message"])


if __name__ == "__main__":
    unittest.main()
