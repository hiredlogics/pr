"""Uploaded documents must remain retrievable.

The live system records `storage_provider = memory` for all 108 of its documents
and puts the filename in the column meant for the storage key - so the original
notice behind every case is gone. These assertions exist so that cannot recur
here quietly.
"""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal.api import app
from pcn_appeal.models import EvidenceItem

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
\fNOTICE - REVERSE
How to appeal: write to the operator within 28 days of this notice.
Protection of Freedoms Act 2012, Schedule 4 applies to this charge.
"""
BLOB = "https://public.blob.vercel-storage.com/abc123/notice-xyz.pdf"


class FakeResponse:
    def __init__(self, data): self._d, self.headers = data, {"Content-Type": "text/plain"}
    def read(self, n=-1): return self._d
    def __enter__(self): return self
    def __exit__(self, *e): return False


class StorageKey(unittest.TestCase):
    def test_evidence_can_record_where_the_file_lives(self):
        ev = EvidenceItem("E1", "NTK", "notice.pdf", storage_url=BLOB)
        self.assertEqual(ev.storage_url, BLOB)
        self.assertEqual(ev.filename, "notice.pdf")

    def test_storage_url_defaults_to_none_not_the_filename(self):
        """Overloading the storage column with a filename is the live bug."""
        ev = EvidenceItem("E1", "NTK", "notice.pdf")
        self.assertIsNone(ev.storage_url)

    def test_a_blob_upload_records_its_url(self):
        c = TestClient(app)
        case = c.post("/cases").json()["case_id"]
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(NOTICE)):
            r = c.post(f"/cases/{case}/blobs",
                       json={"blobs": [{"url": BLOB, "filename": "notice.pdf"}]})
        self.assertEqual(r.status_code, 200, r.text)

        from pcn_appeal.api import CASES
        ev = CASES[case]["case"].evidence["E1"]
        self.assertEqual(ev.storage_url, BLOB, "the blob URL was not kept")
        self.assertEqual(ev.filename, "notice.pdf")

    def test_the_audit_trail_records_it_too(self):
        c = TestClient(app)
        case = c.post("/cases").json()["case_id"]
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(NOTICE)):
            c.post(f"/cases/{case}/blobs", json={"blobs": [{"url": BLOB, "filename": "n.pdf"}]})
        from pcn_appeal.api import CASES
        entry = next(e for e in CASES[case]["case"].audit if e["event"] == "upload_read")
        self.assertEqual(entry["storage_url"], BLOB)

    def test_the_schema_keeps_them_in_separate_columns(self):
        # Line-based: a column comment contains ");", so splitting on that
        # truncates the block before the columns under test.
        with open("infra/postgres_schema.sql") as fh:
            lines = fh.read().splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("CREATE TABLE evidence"))
        end = next(i for i, l in enumerate(lines[start:], start) if l.strip() == ");")
        block = "\n".join(lines[start:end])
        self.assertIn("filename", block)
        self.assertIn("s3_key", block)

    def test_persistence_writes_the_url_not_the_filename(self):
        import inspect
        from pcn_appeal.store import cases as store
        src = inspect.getsource(store.save)
        self.assertIn("ev.storage_url", src)
        self.assertNotIn("ev.kind, ev.filename, _sha", src)   # the old overload


if __name__ == "__main__":
    unittest.main()
