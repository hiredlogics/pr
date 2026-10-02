"""P7 B5 - KB release discipline.

A release is the whole of the law being served: it carries the curated
relations and a digest of every block text, its digest changes when any of
them change, a published release is immutable, publishing is gated on the
journey suite, and production never serves the authored YAML fallback.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api, manifest
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.store import kb_source
from pcn_appeal.store.kb_sync import DATA, _read, release_manifest

EMPTY_RELATIONS = {"version": "test", "edges": [], "signals": {}, "depends_on": {}}


def _release(src: dict, **overrides) -> dict:
    """A loaded-release dict (kb_source.load_release shape) built from the
    authored YAML, with fields overridden to simulate drift."""
    m = release_manifest(src, embedder_id="test")
    blocks = {bid: {"text": b["text"],
                    "requires_evidence_any": b.get("requires_evidence_any", []) or [],
                    "requires_facts": b.get("requires_facts", []) or [],
                    "version": str(b.get("version", "1.0"))}
              for bid, b in src["building_blocks"]["blocks"].items()}
    from pcn_appeal import prompts
    release = {
        "kb_modules": src["kb_modules"],
        "building_blocks": {"blocks": blocks},
        "routes": src["routes"], "questions": src["questions"],
        "prompts": {t: {"body": p["body"], "version": int(p["version"])}
                    for t, p in prompts.load().items()},
        "relations": m["relations"], "block_texts": m["block_texts"],
        "release_id": "kb-test",
    }
    release.update(overrides)
    return release


class TheDigestCoversTheWholeKb(unittest.TestCase):
    def test_relations_change_the_digest(self):
        a, b = KnowledgeGraph(), KnowledgeGraph()
        from pcn_appeal.kg.relations import build
        b._relations = build(b, curated=EMPTY_RELATIONS)
        self.assertNotEqual(manifest.kb_digest(a), manifest.kb_digest(b))

    def test_identical_kbs_share_a_digest(self):
        self.assertEqual(manifest.kb_digest(KnowledgeGraph()),
                         manifest.kb_digest(KnowledgeGraph()))

    def test_the_manifest_pins_relations_and_block_texts(self):
        m = release_manifest(_read(DATA), embedder_id="test")
        self.assertTrue(m["relations"], "the curated relations ship inside the release")
        self.assertEqual(set(m["block_texts"]), set(m["blocks"]),
                         "every block's text is digested")

    def test_a_release_serves_its_own_relations(self):
        src = _read(DATA)
        kg = KnowledgeGraph.from_release(_release(src, relations=EMPTY_RELATIONS))
        self.assertEqual(kg.relations.version, "test")
        self.assertEqual([e for e in kg.relations.edges if e.origin == "CURATED"], [])


class DriftIsNamedNotServed(unittest.TestCase):
    def setUp(self):
        self.src = _read(DATA)

    def test_a_faithful_release_reports_no_drift(self):
        self.assertEqual(kb_source.release_differs_from_yaml(_release(self.src)), [])

    def test_a_changed_block_text_is_drift(self):
        release = _release(self.src)
        bid = sorted(release["block_texts"])[0]
        release["block_texts"][bid] = "0" * 64
        drift = kb_source.release_differs_from_yaml(release)
        self.assertTrue(any(bid in d and "text differs" in d for d in drift), drift)

    def test_changed_relations_are_drift(self):
        drift = kb_source.release_differs_from_yaml(
            _release(self.src, relations=EMPTY_RELATIONS))
        self.assertTrue(any("relations" in d for d in drift), drift)

    def test_a_release_predating_the_pins_says_so(self):
        drift = kb_source.release_differs_from_yaml(
            _release(self.src, relations=None, block_texts=None))
        self.assertTrue(any("block drift cannot be checked" in d for d in drift), drift)
        self.assertTrue(any("relation drift cannot be checked" in d for d in drift), drift)


class ProductionNeverServesYaml(unittest.TestCase):
    def test_no_database_refuses_to_start(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production", "DATABASE_URL": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                api._load_kg()
        self.assertIn("never served in production", str(ctx.exception))

    def test_development_still_serves_yaml(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "development", "DATABASE_URL": ""}):
            self.assertIsInstance(api._load_kg(), KnowledgeGraph)


class PublishingIsGated(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k", "DATABASE_URL": ""})
        p.start(); self.addCleanup(p.stop)
        self.client = TestClient(api.app)
        self.h = {"X-Admin-Token": "t0k"}

    def test_admin_only(self):
        r = self.client.post("/admin/kb/releases", json={})
        self.assertEqual(r.status_code, 401)

    def test_no_database_is_a_503(self):
        r = self.client.post("/admin/kb/releases", json={}, headers=self.h)
        self.assertEqual(r.status_code, 503)

    def test_a_red_suite_refuses_to_publish(self):
        red = {"journeys": 5, "passed": 4, "failed": ["REG_X"],
               "results": [{"name": "REG_X", "failures": ["letter changed"]}]}
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://unit.test/db"}), \
                mock.patch.object(api.db, "enabled", return_value=True), \
                mock.patch.object(api, "_release_gate", return_value=red), \
                mock.patch.object(api, "_load_kg") as load, \
                mock.patch("pcn_appeal.store.kb_sync.sync") as sync:
            r = self.client.post("/admin/kb/releases", json={}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertIn("not green", str(r.json()))
        sync.assert_not_called()
        load.assert_not_called()

    def test_a_green_suite_publishes_and_reloads(self):
        green = {"journeys": 5, "passed": 5, "failed": [], "results": []}
        published = {"release_id": "kb-test", "modules": 1, "blocks": 1,
                     "embeddings": 2, "prompts": 1, "embedder": "t", "dim": 8}
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://unit.test/db"}), \
                mock.patch.object(api.db, "enabled", return_value=True), \
                mock.patch.object(api, "_release_gate", return_value=green), \
                mock.patch.object(api, "_load_kg", return_value=api.KG), \
                mock.patch("pcn_appeal.store.kb_sync.sync", return_value=published) as sync:
            r = self.client.post("/admin/kb/releases",
                                 json={"published_by": "qa"}, headers=self.h)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["release"]["release_id"], "kb-test")
        self.assertEqual(sync.call_args.kwargs["published_by"], "qa")
        self.assertTrue(sync.call_args.kwargs["publish"])

    def test_a_held_release_id_is_a_conflict_not_an_overwrite(self):
        green = {"journeys": 5, "passed": 5, "failed": [], "results": []}
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://unit.test/db"}), \
                mock.patch.object(api.db, "enabled", return_value=True), \
                mock.patch.object(api, "_release_gate", return_value=green), \
                mock.patch("pcn_appeal.store.kb_sync.sync",
                           side_effect=ValueError("KB release kb-x is already published")):
            r = self.client.post("/admin/kb/releases", json={}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertIn("already published", str(r.json()))


class GateCasesAreEphemeral(unittest.TestCase):
    DOC = {"evidence_id": "E1", "filename": "n.txt", "kind": "NTK",
           "text": "Parking Charge Notice\nPCN Number: PCN1\n"}

    def test_the_header_keeps_the_case_out_of_the_store(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k", "DATABASE_URL": ""}):
            client = TestClient(api.app)
            r = client.post("/appeal", json={"documents": [self.DOC], "narrative": ""},
                            headers={"X-Ephemeral-Case": "1", "X-Admin-Token": "t0k"})
            self.assertLess(r.status_code, 500)
            case_id = r.json()["case_id"]
            self.assertTrue(case_id.startswith("C-GATE-"))
            self.assertTrue(getattr(api.CASES[case_id]["case"], "_ephemeral", False))

    def test_a_customer_cannot_claim_it(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k", "DATABASE_URL": ""}):
            client = TestClient(api.app)
            r = client.post("/appeal", json={"documents": [self.DOC], "narrative": ""},
                            headers={"X-Ephemeral-Case": "1"})
            self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
