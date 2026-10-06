"""Admin PostgreSQL + pgvector live data explorer — auth, read-only, pgvector absent."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient


class AdminDbAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import after env is clean enough for FastAPI app
        from pcn_appeal import api as api_mod
        cls.api = api_mod
        cls.app = api_mod.app

    def test_customer_proxy_paths_not_on_customer_allowlist(self):
        from pcn_appeal.api import is_customer_route
        self.assertFalse(is_customer_route("GET", "/admin/api/db/status"))
        self.assertFalse(is_customer_route("GET", "/admin/api/db/tables"))
        self.assertFalse(is_customer_route("POST", "/admin/api/db/vector/search"))

    def test_status_requires_admin_when_token_set(self):
        client = TestClient(self.app)
        with mock.patch.dict(os.environ, {"ADMIN_TRACE_TOKEN": "secret-token", "APP_ENV": "development"}):
            r = client.get("/admin/api/db/status")
            self.assertEqual(r.status_code, 401)
            r2 = client.get(
                "/admin/api/db/status",
                headers={"X-Admin-Token": "secret-token"},
            )
            # May be 200 or 503 depending on DATABASE_URL; never 401 once authed
            self.assertIn(r2.status_code, (200, 503))

    def test_production_closed_without_token(self):
        client = TestClient(self.app)
        with mock.patch.dict(os.environ, {"ADMIN_TRACE_TOKEN": "", "ADMIN_TOKEN": "", "APP_ENV": "production"}, clear=False):
            with mock.patch("pcn_appeal.admin_db.auth.runtime.is_production", return_value=True):
                r = client.get("/admin/api/db/status")
                self.assertEqual(r.status_code, 403)

    def test_sql_rejects_non_select(self):
        from pcn_appeal.admin_db import sql_safe
        with self.assertRaises(ValueError):
            sql_safe.run_select_only("DELETE FROM cases")
        with self.assertRaises(ValueError):
            sql_safe.run_select_only("SELECT 1; DROP TABLE cases")

    def test_pii_masking(self):
        from pcn_appeal.admin_db.masking import mask_row
        row = mask_row(
            ["vrm", "email", "pcn_number"],
            ("AB12CDE", "a@b.co", "123"),
            show_sensitive=False,
        )
        self.assertNotEqual(row["vrm"], "AB12CDE")
        self.assertIn("*", row["vrm"])
        self.assertEqual(row["pcn_number"], "123")

    def test_pgvector_absent_message(self):
        from pcn_appeal.admin_db import vectors
        with mock.patch("pcn_appeal.admin_db.vectors.vector_extension", return_value={
            "installed": False, "message": "PGVECTOR NOT CONFIGURED",
            "vector_tables": [], "embedding_rows": 0,
        }):
            st = vectors.status()
            self.assertFalse(st["installed"])
            self.assertEqual(st["message"], "PGVECTOR NOT CONFIGURED")
            self.assertEqual(st["embedding_rows"], 0)

    def test_similarity_does_not_claim_mutation(self):
        from pcn_appeal.admin_db import vectors
        with mock.patch("pcn_appeal.admin_db.vectors.status", return_value={
            "installed": False, "message": "PGVECTOR NOT CONFIGURED",
        }):
            out = vectors.similarity_search("late notice")
            self.assertFalse(out.get("installed"))
            self.assertEqual(out.get("results"), [])


def _ensure_database_url() -> bool:
    # Live tests are opt-in; never load private dotenv files or mutate the
    # environment of the remaining offline suite.
    return os.getenv("PCN_RUN_LIVE_DB_TESTS") == "1" and bool(os.getenv("DATABASE_URL"))


class AdminDbLiveOptional(unittest.TestCase):
    """Runs against real DATABASE_URL when present; skips otherwise."""

    def test_live_status_and_tables(self):
        if not _ensure_database_url():
            self.skipTest("DATABASE_URL not set")
        from pcn_appeal.admin_db import catalog
        st = catalog.status()
        self.assertTrue(st.get("connected"))
        self.assertNotIn("password", str(st).lower())
        self.assertIsNone(st.get("username"))
        tables = catalog.list_tables(include_counts=False)
        self.assertGreater(tables["n"], 0)
        names = {t["table"] for t in tables["tables"]}
        # Discover — do not require every table
        self.assertTrue(names)

    def test_live_pagination_limit(self):
        if not _ensure_database_url():
            self.skipTest("DATABASE_URL not set")
        from pcn_appeal.admin_db import browse
        # Pick any existing table
        from pcn_appeal.admin_db import catalog
        tables = catalog.list_tables(include_counts=False)["tables"]
        if not tables:
            self.skipTest("no tables")
        t = tables[0]
        out = browse.browse_table(t["schema"], t["table"], page=1, page_size=5)
        self.assertLessEqual(len(out["rows"]), 5)
        self.assertEqual(out["page_size"], 5)


if __name__ == "__main__":
    unittest.main()
