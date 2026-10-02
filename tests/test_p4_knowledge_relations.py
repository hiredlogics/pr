"""P4: knowledge graph relationship engine.

Verified facts and evidence reach knowledge modules only through explicit,
typed relationships, and the system can say why a module applies, why another
does not, and which fact supports or blocks each.

Run:  python -m unittest tests.test_p4_knowledge_relations -v
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api
from pcn_appeal.engines.analysis import AnalysisEngine
from pcn_appeal.engines.knowledge_matcher import (BLOCKED, OFFERABLE, REJECTED, SUPPORTED,
                                                  KnowledgeMatcher)
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.kg.relations import (BLOCKS, CURATED, DEPENDS_ON, DERIVED, EVIDENCE_SUPPORTS,
                                     RELATIONSHIPS, REQUIRES, SUPPORTS, _leaves, build, edge)
from pcn_appeal.models import CaseFile, EvidenceItem, Fact, FactSource, FactStatus, SourceKind
from support import ReferenceAnalysisLLM
from test_question_authority import case_with

import sqlite_store

KG = KnowledgeGraph()
ANPR_MODULES = ("KB-ANPR-01", "KB-ANPR-02", "KB-ANPR-03", "KB-TIME-01")
PAYMENT_MODULES = ("KB-PAY-01", "KB-PAY-02", "KB-PAY-03", "KB-KEY-01", "KB-KEY-02",
                   "KB-GRACE-01", "KB-GRACE-02")
PARENT_CHILD = "Parked in a Parent and Child bay without a child"
NO_PARKING = "Parked in a No Parking Area"


def doc(name, value):
    return Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                FactSource(SourceKind.DOCUMENT, "E1#p1"))


def answered(name, value):
    return Fact(f"F-{name}", name, value, FactStatus.ANSWERED,
                FactSource(SourceKind.ANSWER, f"answer:{name}"))


def facts_case(documents=None, answers=None, evidence=None) -> CaseFile:
    """A case holding exactly these verified facts."""
    case = CaseFile("C-P4", evidence={"E1": EvidenceItem("E1", "OTHER", "notice.pdf", text="x")})
    for k, v in (documents or {}).items():
        case.put(doc(k, v))
    for k, v in (answers or {}).items():
        case.put(answered(k, v))
    for eid, kind in (evidence or {}).items():
        case.evidence[eid] = EvidenceItem(eid, kind, f"{eid}.jpg", text="evidence")
    return case


def match(case):
    return KnowledgeMatcher(KG).match(case)


def pipeline_case(breach, narrative="I want to appeal", answers=None, extra=None):
    """Ingest + confirm through the real pipeline (reference analysis model)."""
    case, pipe = case_with(extra={"alleged_breach": breach, **(extra or {})})
    pipe.ingest(case)
    for k, v in (answers or {}).items():
        case.put(answered(k, v))
    confirmable = [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]
    pipe.confirm(case, {}, confirmable, narrative)
    return case, pipe


def _without_ids(obj):
    if isinstance(obj, dict):
        return {k: _without_ids(v) for k, v in obj.items() if k != "fact_id"}
    if isinstance(obj, list):
        return [_without_ids(v) for v in obj]
    return obj


# =========================================================== spec tests 1-5
class SpecTests(unittest.TestCase):

    def test_1_parent_child_allegation_with_children_present_selects_contradiction_module(self):
        case, pipe = pipeline_case(PARENT_CHILD, answers={"children_present": True})
        c = KnowledgeMatcher(pipe.kg).match(case).candidates["KB-BAY-02"]
        self.assertEqual(c.status, SUPPORTED, c.reason)
        why = {s["fact"]: s for s in c.selected_because}
        self.assertIn("account_contradicts_allegation", why)
        self.assertIn("restricted_bay_alleged", why)
        # ... and the trace names the answer it rests on.
        basis = why["account_contradicts_allegation"].get("because_of") or []
        self.assertIn(("child_occupant_present", "answer:children_present"),
                      {(b["fact"], b["source"]) for b in basis})

    def test_1b_the_same_from_the_customers_account(self):
        case, pipe = pipeline_case(PARENT_CHILD, narrative="My kids were in the car with me.")
        self.assertEqual(KnowledgeMatcher(pipe.kg).match(case).candidates["KB-BAY-02"].status,
                         SUPPORTED)

    def test_1c_children_absent_does_not_select_it(self):
        case, pipe = pipeline_case(PARENT_CHILD, answers={"children_present": False})
        self.assertNotEqual(KnowledgeMatcher(pipe.kg).match(case).candidates["KB-BAY-02"].status,
                            SUPPORTED)

    def test_1d_children_present_on_a_non_bay_allegation_does_not_select_it(self):
        case, pipe = pipeline_case("Overstayed paid time", answers={"children_present": True})
        self.assertNotEqual(KnowledgeMatcher(pipe.kg).match(case).candidates["KB-BAY-02"].status,
                            SUPPORTED)

    def test_2_static_photo_evidence_blocks_anpr_modules(self):
        # Even with an ANPR-module fact asserted, a photo/attendant notice blocks them.
        case = facts_case({"observation_time": "10:15", "alleged_breach": "Parked without a ticket",
                           "entry_time": None},
                          {"multiple_visits": True, "anpr_sequence_incomplete": True})
        m = match(case)
        self.assertEqual(m.signals["evidence_method"]["value"], "ATTENDANT_PHOTO")
        for mid in ANPR_MODULES:
            with self.subTest(module=mid):
                c = m.candidates[mid]
                self.assertEqual(c.status, BLOCKED)
                self.assertIn("wrong evidence type", c.reason)
                self.assertEqual(c.blocked_by[0]["signal"], "evidence_method=ATTENDANT_PHOTO")

    def test_2b_windscreen_notice_is_a_photo_notice(self):
        m = match(facts_case({"notice_route": "WINDSCREEN"}, {"multiple_visits": True}))
        self.assertEqual(m.candidates["KB-ANPR-01"].status, BLOCKED)

    def test_2c_anpr_notice_is_not_blocked(self):
        m = match(facts_case({"entry_time": "10:00", "exit_time": "12:30"},
                             {"multiple_visits": True}))
        self.assertEqual(m.signals["evidence_method"]["value"], "ANPR")
        self.assertEqual(m.candidates["KB-ANPR-01"].status, SUPPORTED)

    def test_3_payment_completed_selects_payment_module(self):
        m = match(facts_case({"alleged_breach": "Failed to pay the tariff"},
                             {"payment_made": True}))
        c = m.candidates["KB-PAY-01"]
        self.assertEqual(c.status, SUPPORTED, c.reason)
        self.assertEqual([(s["fact"], s["source"]) for s in c.selected_because
                          if s.get("fact") == "payment_made"],
                         [("payment_made", "answer:payment_made")])

    def test_4_no_payment_allegation_payment_module_not_selected(self):
        # A prohibition: no payment can answer it, even if one was made.
        m = match(facts_case({"alleged_breach": NO_PARKING}, {"payment_made": True}))
        self.assertEqual(m.signals["allegation_class"]["value"], "PROHIBITION")
        for mid in PAYMENT_MODULES:
            with self.subTest(module=mid):
                self.assertEqual(m.candidates[mid].status, BLOCKED)
                self.assertIn("payment allegation absent", m.candidates[mid].reason)

    def test_4b_no_payment_fact_payment_module_not_selected(self):
        m = match(facts_case({"alleged_breach": "Overstayed paid time"}))
        self.assertNotEqual(m.candidates["KB-PAY-01"].status, SUPPORTED)

    def test_5_same_facts_same_candidates_every_run(self):
        def case():
            return facts_case({"alleged_breach": PARENT_CHILD, "restricted_bay_alleged": True,
                               "observation_time": "09:00"},
                              {"payment_made": True, "account_contradicts_allegation": True})

        same = case()
        first = KnowledgeMatcher(KG).match(same).trace()
        for _ in range(3):
            self.assertEqual(KnowledgeMatcher(KG).match(same).trace(), first)
        for _ in range(3):
            self.assertEqual(_without_ids(KnowledgeMatcher(KnowledgeGraph()).match(case()).trace()),
                             _without_ids(first))

    def test_5b_relationship_ids_are_stable_across_builds(self):
        a, b = build(KnowledgeGraph()), build(KnowledgeGraph())
        self.assertEqual([e.edge_id for e in a.edges], [e.edge_id for e in b.edges])
        self.assertEqual({k: n.knowledge_id for k, n in a.nodes.items()},
                         {k: n.knowledge_id for k, n in b.nodes.items()})


# =========================================================== nodes + relationships
class RelationGraphShape(unittest.TestCase):
    G = KG.relations

    def test_every_module_is_a_node_with_the_reasoning_fields(self):
        self.assertEqual(set(self.G.nodes), set(KG.modules))
        for n in self.G.nodes.values():
            for k in ("use_when", "required_facts", "supporting_evidence", "blocked_conditions",
                      "prohibited_claims", "drafting_guidance"):
                self.assertIn(k, n.metadata, f"{n.module_id} lacks {k}")

    def test_no_drafting_paragraph_is_copied_into_a_node(self):
        texts = [b.text for b in KG.blocks.values() if len(b.text or "") > 60]
        for n in self.G.nodes.values():
            blob = json.dumps(n.metadata)
            for t in texts:
                self.assertNotIn(t[:60], blob, f"{n.module_id} carries block text")

    def test_only_the_allowed_relationships(self):
        self.assertEqual(set(RELATIONSHIPS), {"SUPPORTS", "BLOCKS", "REQUIRES", "CONFLICTS_WITH",
                                              "DEPENDS_ON", "EVIDENCE_SUPPORTS"})
        self.assertTrue({e.relationship_type for e in self.G.edges} <= set(RELATIONSHIPS))
        with self.assertRaises(ValueError):
            edge("FACT", "x", "IMPLIES", "MODULE", "KB-PAY-01")

    def test_derived_edges_agree_with_the_gates(self):
        """Every leaf of every gate is an edge, and every derived gate edge is a
        leaf - they are the same predicates, so they cannot drift."""
        for mid, m in KG.modules.items():
            for fld, pred in (("use_when", m.use_when), ("do_not_use_when", m.do_not_use_when)):
                leaves = {json.dumps(l, sort_keys=True) for _, l in _leaves(pred)}
                edges = {json.dumps(e.metadata["condition"], sort_keys=True)
                         for e in self.G.edges_of(mid)
                         if e.origin == DERIVED and e.metadata.get("from_field") == fld}
                self.assertEqual(leaves, edges, f"{mid}.{fld}")
            req = {e.target_id for e in self.G.edges_of(mid, REQUIRES)}
            self.assertEqual(req, set(m.required_facts or []), mid)

    def test_spec_relationship_examples(self):
        def has(st, sid, rel, tid):
            return any(e.source_type == st and e.source_id == sid and e.relationship_type == rel
                       and e.target_id == tid for e in self.G.edges)
        self.assertTrue(has("FACT", "account_contradicts_allegation", SUPPORTS, "KB-BAY-02"))
        self.assertTrue(has("FACT", "account_contradicts_allegation", DEPENDS_ON,
                            "child_occupant_present"))
        self.assertTrue(has("SIGNAL", "evidence_method=ATTENDANT_PHOTO", BLOCKS, "KB-ANPR-01"))
        self.assertTrue(has("FACT", "payment_made", SUPPORTS, "KB-PAY-01"))
        self.assertTrue(any(e.relationship_type == EVIDENCE_SUPPORTS for e in self.G.edges))

    def test_curated_edges_are_generic(self):
        """No operator, site or PCN in the curated relationships."""
        for e in self.G.edges:
            if e.origin == CURATED:
                self.assertIn(e.source_type, ("SIGNAL", "FACT"))
                self.assertNotRegex(json.dumps(e.as_row()).lower(),
                                    r"pcn\d|ltd|limited|parking eye|euro car")


# =========================================================== trace
class MatchTrace(unittest.TestCase):

    def test_selected_modules_carry_facts_and_sources_rejected_carry_reasons(self):
        t = match(facts_case({"alleged_breach": NO_PARKING, "observation_time": "08:00"},
                             {"payment_made": True})).trace()
        self.assertTrue(t["rejected"])
        for row in t["rejected"]:
            self.assertTrue(row["reason"], row)
        for row in t["selected"]:
            self.assertTrue(row["selected_because"], row)
            for f in row["facts"]:
                if "fact" in f and "value" in f:
                    self.assertIn("source", f)
        self.assertIn("evidence_method", t["signals"])

    def test_every_module_is_accounted_for(self):
        m = match(facts_case({"alleged_breach": PARENT_CHILD}))
        self.assertEqual(set(m.candidates), {x.module_id for x in KG.active_modules()})

    def test_matcher_writes_nothing_to_the_case(self):
        case = facts_case({"alleged_breach": NO_PARKING}, {"payment_made": True})
        before = (dict(case.fact_view()), len(case.audit))
        match(case)
        self.assertEqual((dict(case.fact_view()), len(case.audit)), before)

    def test_evidence_upload_is_shown_as_evidence(self):
        g = KG.relations
        ev = [e for e in g.edges if e.relationship_type == EVIDENCE_SUPPORTS]
        e = ev[0]
        kind = e.source_id
        mod = KG.modules[e.target_id]
        case = facts_case(evidence={"R1": kind})
        c = match(case).candidates[mod.module_id]
        if c.status not in (BLOCKED, REJECTED):
            self.assertIn(kind, c.evidence)


# =========================================================== case intelligence input
class _Proposes(ReferenceAnalysisLLM):
    """A model that always proposes `extra` modules on top of the reference grounds."""

    def __init__(self, extra, **kw):
        super().__init__(**kw)
        self.extra = list(extra)

    def _analyse(self, payload):
        out = super()._analyse(payload)
        out["grounds"] += [{"module_id": m, "supported_by": [], "note": "model insists"}
                           for m in self.extra]
        return out


class CaseIntelligenceInput(unittest.TestCase):

    def _analyse(self, case, llm=None):
        llm = llm or ReferenceAnalysisLLM()
        result = AnalysisEngine(KG, llm).analyse(case)
        payloads = [json.loads(c["user"]) for c in llm.calls if c["task"] == "case_analysis"]
        return result, payloads[-1]

    def test_blocked_modules_never_reach_the_model(self):
        case = facts_case({"alleged_breach": NO_PARKING, "observation_time": "08:00"},
                          {"payment_made": True, "multiple_visits": True})
        result, payload = self._analyse(case)
        offered = {c["module_id"] for c in payload["candidates"]}
        self.assertFalse(offered & set(ANPR_MODULES + PAYMENT_MODULES), offered)
        self.assertEqual(set(result.candidate_ids), offered)

    def test_the_model_never_receives_the_whole_kb(self):
        case = facts_case({"alleged_breach": PARENT_CHILD})
        result, payload = self._analyse(case)
        self.assertLess(len(payload["candidates"]), len(list(KG.active_modules())))
        statuses = {result.knowledge.candidates[c["module_id"]].status
                    for c in payload["candidates"]}
        self.assertTrue(statuses <= set(OFFERABLE))
        for c in payload["candidates"]:
            self.assertIn("relation", c)
        self.assertIn("case_signals", payload)

    def test_supported_modules_are_offered_first(self):
        case = facts_case({"alleged_breach": "Failed to pay the tariff"}, {"payment_made": True})
        result, payload = self._analyse(case)
        first = payload["candidates"][0]
        self.assertEqual(first["relation"]["status"], SUPPORTED)

    def test_a_blocked_proposal_is_suppressed_before_the_claim_plan(self):
        case = facts_case({"alleged_breach": NO_PARKING}, {"payment_made": True})
        result, _ = self._analyse(case, _Proposes(["KB-PAY-01"]))
        self.assertNotIn("KB-PAY-01", result.module_ids)
        why = [s["why"] for s in result.suppressed if s["module_id"] == "KB-PAY-01"]
        self.assertTrue(why and why[0].startswith("blocked by relation"), result.suppressed)

    def test_the_match_is_in_the_audit(self):
        case = facts_case({"alleged_breach": NO_PARKING})
        self._analyse(case)
        ev = [a for a in case.audit if a.get("event") == "knowledge_match"]
        self.assertTrue(ev)
        self.assertIn("rejected", ev[-1])


# =========================================================== admin knowledge store
class KnowledgeStore(unittest.TestCase):

    def setUp(self):
        self.db = sqlite_store.install(self)
        from pcn_appeal.store import knowledge
        self.k = knowledge

    def seed(self):
        return self.k.seed(KG)

    def test_seed_loads_every_node_and_edge_and_is_idempotent(self):
        first = self.seed()
        self.assertEqual(first["nodes"], len(KG.modules))
        self.assertEqual(first["edges"], len(KG.relations.edges))
        self.assertEqual(self.seed(), {"nodes": 0, "edges": 0,
                                       "relations_version": first["relations_version"]})
        self.assertEqual(sqlite_store.count(self.db, "knowledge_nodes"), len(KG.modules))

    def test_every_change_needs_a_user_and_a_reason(self):
        self.seed()
        for who, why in (("", "r"), ("admin", ""), ("  ", "r")):
            with self.subTest(who=who, why=why), self.assertRaises(self.k.KnowledgeChangeError):
                self.k.disable_module("KB-PAY-01", changed_by=who, reason=why)

    def test_update_bumps_version_stages_and_logs(self):
        self.seed()
        out = self.k.update_module("KB-PAY-01", {"strength": 70}, changed_by="legal.a",
                                   reason="raise weight after review")
        self.assertEqual(out["node"]["version"], "1.1")
        self.assertEqual(out["node"]["status"], "REVIEW")
        ch = self.k.list_changes("KB-PAY-01")[0]
        self.assertEqual((ch["action"], ch["changed_by"], ch["reason"], ch["version"]),
                         ("UPDATE", "legal.a", "raise weight after review", "1.1"))
        self.assertTrue(ch["changed_at"])
        self.assertEqual(ch["before"]["metadata"]["strength"], KG.modules["KB-PAY-01"].strength)
        self.assertEqual(ch["after"]["metadata"]["strength"], 70)

    def test_gate_change_recomputes_derived_edges(self):
        self.seed()
        out = self.k.update_module("KB-PAY-01", {"use_when": {"all": [
            {"is": "payment_made"}, {"exists": "payment_method"}]}},
            changed_by="legal.a", reason="require the method")
        self.assertEqual(out["derived_added"], 1)
        edges = self.k.list_edges("KB-PAY-01")
        self.assertTrue(any(e["source_id"] == "payment_method" and e["status"] == "REVIEW"
                            for e in edges))

    def test_bad_predicate_is_refused(self):
        self.seed()
        for bad in ({"implies": "x"}, {"all": []}, {"eq": "x"}, {"is": 3}):
            with self.subTest(bad=bad), self.assertRaises(self.k.KnowledgeChangeError):
                self.k.update_module("KB-PAY-01", {"use_when": bad}, changed_by="a", reason="r")

    def test_create_and_disable_module(self):
        self.seed()
        out = self.k.create_module({"module_id": "KB-NEW-01", "name": "New ground",
                                    "use_when": {"is": "some_fact"}},
                                   changed_by="legal.a", reason="new ground drafted")
        self.assertEqual(out["node"]["status"], "REVIEW")
        with self.assertRaises(self.k.KnowledgeChangeError):
            self.k.create_module({"module_id": "KB-NEW-01", "name": "x", "use_when": {"is": "y"}},
                                 changed_by="a", reason="r")
        out = self.k.disable_module("KB-NEW-01", changed_by="legal.b", reason="withdrawn")
        self.assertEqual((out["node"]["status"], out["node"]["version"]), ("DISABLED", "1.1"))
        self.assertEqual([c["action"] for c in self.k.list_changes("KB-NEW-01")],
                         ["DISABLE", "CREATE"])

    def test_create_and_remove_a_curated_relationship(self):
        self.seed()
        out = self.k.create_edge("SIGNAL", "allegation_class=OVERSTAY", "SUPPORTS", "MODULE",
                                 "KB-BAY-01", changed_by="legal.a", reason="test relevance",
                                 weight=0.5)
        eid = out["edge"]["edge_id"]
        self.assertEqual((out["edge"]["origin"], out["edge"]["status"]), (CURATED, "REVIEW"))
        with self.assertRaises(self.k.KnowledgeChangeError):
            self.k.create_edge("SIGNAL", "allegation_class=OVERSTAY", "SUPPORTS", "MODULE",
                               "KB-BAY-01", changed_by="legal.a", reason="again")
        out = self.k.remove_edge(eid, changed_by="legal.b", reason="not needed")
        self.assertEqual((out["edge"]["status"], out["edge"]["version"]), ("REMOVED", 2))
        self.assertNotIn(eid, {e["edge_id"] for e in self.k.list_edges()})
        self.assertIn(eid, {e["edge_id"] for e in self.k.list_edges(include_removed=True)})
        self.assertEqual([c["action"] for c in self.k.list_changes(eid)], ["REMOVE", "CREATE"])

    def test_unknown_relationship_or_module_is_refused(self):
        self.seed()
        with self.assertRaises(self.k.KnowledgeChangeError):
            self.k.create_edge("FACT", "x", "IMPLIES", "MODULE", "KB-PAY-01",
                               changed_by="a", reason="r")
        with self.assertRaises(self.k.KnowledgeChangeError):
            self.k.create_edge("FACT", "x", "SUPPORTS", "MODULE", "KB-NOPE-99",
                               changed_by="a", reason="r")

    def test_a_derived_relationship_cannot_be_removed(self):
        self.seed()
        derived = next(e for e in KG.relations.edges if e.origin == DERIVED)
        with self.assertRaises(self.k.KnowledgeChangeError) as cm:
            self.k.remove_edge(derived.edge_id, changed_by="a", reason="r")
        self.assertIn("gate", str(cm.exception))

    def test_admin_changes_do_not_reach_live_reasoning(self):
        """Staged until a release is published: the live matcher is unchanged."""
        self.seed()
        self.k.disable_module("KB-PAY-01", changed_by="legal.a", reason="staged")
        m = match(facts_case({"alleged_breach": "Failed to pay the tariff"},
                             {"payment_made": True}))
        self.assertEqual(m.candidates["KB-PAY-01"].status, SUPPORTED)


# =========================================================== admin API
class KnowledgeAdminApi(unittest.TestCase):

    def setUp(self):
        sqlite_store.install(self)
        from pcn_appeal.store import knowledge
        knowledge.seed(KG)
        self.client = TestClient(api.app)
        p = mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k"})
        p.start()
        self.addCleanup(p.stop)
        self.h = {"X-Admin-Token": "t0k"}

    def test_admin_only(self):
        for method, url, body in (
                ("get", "/admin/knowledge/nodes", None),
                ("get", "/admin/knowledge/edges", None),
                ("get", "/admin/knowledge/changes", None),
                ("post", "/admin/knowledge/modules/KB-PAY-01/disable",
                 {"changed_by": "a", "reason": "r"})):
            with self.subTest(url=url):
                r = getattr(self.client, method)(url, **({"json": body} if body else {}))
                self.assertEqual(r.status_code, 401)

    def test_update_through_the_api_is_logged(self):
        r = self.client.patch("/admin/knowledge/modules/KB-PAY-01", headers=self.h,
                              json={"changed_by": "legal.a", "reason": "weight",
                                    "changes": {"strength": 61}})
        self.assertEqual(r.status_code, 200, r.text)
        ch = self.client.get("/admin/knowledge/changes?entity_id=KB-PAY-01",
                             headers=self.h).json()["changes"]
        self.assertEqual((ch[0]["changed_by"], ch[0]["reason"], ch[0]["version"]),
                         ("legal.a", "weight", "1.1"))

    def test_reason_is_required(self):
        r = self.client.post("/admin/knowledge/modules/KB-PAY-01/disable", headers=self.h,
                             json={"changed_by": "legal.a", "reason": ""})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/admin/knowledge/modules/KB-PAY-01/disable", headers=self.h,
                             json={"changed_by": "legal.a"})
        self.assertEqual(r.status_code, 422)

    def test_edges_round_trip(self):
        r = self.client.post("/admin/knowledge/edges", headers=self.h, json={
            "changed_by": "legal.a", "reason": "relevance", "source_type": "SIGNAL",
            "source_id": "allegation_class=PERMIT", "relationship_type": "SUPPORTS",
            "target_type": "MODULE", "target_id": "KB-BAY-01"})
        self.assertEqual(r.status_code, 200, r.text)
        eid = r.json()["edge"]["edge_id"]
        r = self.client.post(f"/admin/knowledge/edges/{eid}/remove", headers=self.h,
                             json={"changed_by": "legal.a", "reason": "undo"})
        self.assertEqual(r.json()["edge"]["status"], "REMOVED")

    def test_case_trace_includes_the_knowledge_match(self):
        case, pipe = pipeline_case(NO_PARKING, answers={"payment_made": True})
        t = api._knowledge_trace({"case": case, "pipe": pipe})
        rejected = {r["module"]: r for r in t["rejected"]}
        self.assertIn("payment allegation absent", rejected["KB-PAY-01"]["reason"])
        self.assertEqual(set(t), {"relations_version", "signals", "selected", "relevant",
                                  "rejected"})


class KnowledgeAdminWithoutDatabase(unittest.TestCase):
    def test_store_endpoints_say_the_database_is_needed(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "", "ADMIN_TOKEN": ""}):
            r = TestClient(api.app).get("/admin/knowledge/nodes")
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
