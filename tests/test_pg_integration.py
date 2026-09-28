"""Live Postgres + pgvector tests.

Skipped unless DATABASE_URL points at a reachable pgvector-enabled server:

    docker compose -f infra/docker-compose.yml up -d db
    export DATABASE_URL=postgresql://postgres:dev@localhost:5432/postgres
    python -m unittest tests.test_pg_integration -v

These run against a REAL database and write to it, so point DATABASE_URL at a
throwaway one - `init_schema()` applies the full schema and `sync()` upserts
every module, block and embedding.
"""
import os
import unittest

REASON = "set DATABASE_URL to a pgvector-enabled Postgres to run these"


def _available() -> bool:
    if not os.getenv("DATABASE_URL"):
        return False
    try:
        from pcn_appeal.store import db
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@unittest.skipUnless(_available(), REASON)
class PostgresKB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pcn_appeal.store import db, kb_sync
        db.init_schema()
        cls.result = kb_sync.sync(published_by="test")

    def test_sync_wrote_every_module_block_and_embedding(self):
        from pcn_appeal.store import db
        with db.connect() as conn:
            modules = conn.execute("SELECT count(*) FROM kb_modules").fetchone()[0]
            blocks = conn.execute("SELECT count(*) FROM kb_blocks").fetchone()[0]
            embeddings = conn.execute("SELECT count(*) FROM kb_embeddings").fetchone()[0]
        self.assertGreaterEqual(modules, self.result["modules"])
        self.assertGreaterEqual(blocks, self.result["blocks"])
        self.assertEqual(embeddings, self.result["modules"] + self.result["blocks"])

    def test_predicates_survive_the_round_trip_as_jsonb(self):
        """The predicates ARE the decision surface; jsonb must not reshape them."""
        from pcn_appeal.kg.graph import KnowledgeGraph
        from pcn_appeal.store import kb_source
        yaml_kg = KnowledgeGraph()
        pg_kg = KnowledgeGraph.from_release(kb_source.load_release())
        self.assertEqual(sorted(yaml_kg.modules), sorted(pg_kg.modules))
        for mid, m in yaml_kg.modules.items():
            self.assertEqual(m.use_when, pg_kg.modules[mid].use_when, mid)
            self.assertEqual(m.do_not_use_when, pg_kg.modules[mid].do_not_use_when, mid)
            self.assertEqual(yaml_kg.gating_facts(mid), pg_kg.gating_facts(mid), mid)

    def test_vector_search_returns_the_relevant_module(self):
        from pcn_appeal.rag.embedder import HashingEmbedder
        from pcn_appeal.store import db
        q = HashingEmbedder(dim=db.EMBED_DIM)(["notice to keeper delivered late by post"])[0]
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT item_id FROM kb_embeddings WHERE kind = 'module' "
                "ORDER BY embedding <=> %s::vector LIMIT 5", (list(map(float, q)),)).fetchall()
        self.assertTrue(any(r[0].startswith("KB-POFA") for r in rows), rows)

    def test_release_is_an_immutable_pinned_snapshot(self):
        from pcn_appeal.store import db, kb_source
        first = kb_source.load_release()["release_id"]
        with db.connect() as conn:
            manifest = conn.execute("SELECT manifest FROM kb_releases WHERE kb_release_id = %s",
                                    (first,)).fetchone()[0]
        self.assertIn("modules", manifest)
        self.assertIn("embedder", manifest)
        # every pinned module/version must actually exist
        with db.connect() as conn:
            for module_id, version in manifest["modules"].items():
                row = conn.execute("SELECT 1 FROM kb_modules WHERE module_id = %s AND version = %s",
                                   (module_id, version)).fetchone()
                self.assertIsNotNone(row, f"{module_id}@{version} missing")


@unittest.skipUnless(_available(), REASON)
class PostgresCases(unittest.TestCase):
    def test_case_survives_a_save_and_load(self):
        from pcn_appeal.llm import FakeLLM
        from pcn_appeal.models import EvidenceItem, FactStatus
        from pcn_appeal.orchestrator import AppealPipeline
        from pcn_appeal.store import cases as case_store

        base = dict(operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12 CDE",
                    parking_location="Retail Park", site_postcode="M1 1AA",
                    parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                    charge_amount="£100", alleged_breach="Overstayed", operator_ata="BPA")
        f = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
             for k, v in base.items()}
        llm = FakeLLM({"extraction": [{"fields": f, "doc_types": {"E1": "PCN"}}]})

        case = case_store.new_case()
        case.evidence["E1"] = EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice")
        AppealPipeline(llm).auto_appeal(case, "nothing relevant")
        case_store.save(case)

        reloaded = case_store.load(case.case_id)
        self.assertEqual(reloaded.state, case.state)
        self.assertEqual(reloaded.get("vrm"), "AB12CDE")
        self.assertIn("E1", reloaded.evidence)                 # label round-trips, not a uuid
        self.assertEqual(reloaded.evidence["E1"].kind, "PCN")

    def test_corrected_fact_supersedes_rather_than_overwrites(self):
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        from pcn_appeal.store import cases as case_store, db

        case = case_store.new_case()
        case.put(Fact("F-vrm", "vrm", "AB12CDE", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case_store.save(case)
        case.put(Fact("F-vrm", "vrm", "XY99ZZZ", FactStatus.CORRECTED,
                      FactSource(SourceKind.ANSWER, "confirm:vrm")))
        case_store.save(case)

        with db.connect() as conn:
            rows = conn.execute("SELECT value, status, superseded FROM facts "
                                "WHERE case_id = %s AND name = 'vrm' ORDER BY created_at",
                                (case.case_id,)).fetchall()
        self.assertEqual(len(rows), 2, rows)                   # history kept, nothing overwritten
        self.assertTrue(rows[0][2])                            # old row superseded
        self.assertFalse(rows[1][2])
        self.assertEqual(case_store.load(case.case_id).get("vrm"), "XY99ZZZ")


if __name__ == "__main__":
    unittest.main()
