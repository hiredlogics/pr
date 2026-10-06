"""P8 architecture hardening — additive authority, lineage, multi-finding gates.

Sainsbury's-shaped late NTK + narrative is a regression example only.
No operator- or PCN-specific rules.
"""
from __future__ import annotations

import unittest

from test_scenarios import make_case, run
from test_verified_legal_findings import LATE, make_case_without

import sqlite_store
from pcn_appeal.engines.claim_plan_authority import (
    CANDIDATE, CARRIED_FORWARD, ClaimPlanItem, FinalClaimPlan, ORIGIN_PRIORITY,
    SELECTED, SUPPORTED, VERIFIED_FINDING, latest_locked,
)
from pcn_appeal.store import cases as store
from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.fact_graph import FactManager, IGNORED, IGNORED_DUPLICATE
from pcn_appeal.legal import findings as lf
from pcn_appeal.models import (
    CaseFile, CaseState, Draft, DraftSentence, Fact, FactSource, FactStatus,
    RetrievalPack, SourceKind,
)
from pcn_appeal.rules.dsl import evaluate


class P81MultiFindingAuthority(unittest.TestCase):
    def test_gate_passes_when_required_code_is_not_findings_zero(self):
        """Scalar collapse must not hide a second calculated defect."""
        facts = {
            "pofa_finding": "POFA_NTK_INVITATION_DEFECT",
            "pofa_findings": ["POFA_NTK_INVITATION_DEFECT", "POFA_POSTAL_LATE"],
            "notice_route": "POSTAL",
            "jurisdiction": "ENGLAND_WALES",
        }
        pred = {"eq": ["pofa_finding", "POFA_POSTAL_LATE"]}
        self.assertTrue(evaluate(pred, facts))
        self.assertFalse(evaluate(pred, {"pofa_finding": "POFA_NTK_INVITATION_DEFECT"}))

    def test_gate_facts_exposes_full_set(self):
        g = lf.gate_facts({"notice_route": "POSTAL"}, ["POFA_POSTAL_LATE", "X"])
        self.assertEqual(g["pofa_findings"], ["POFA_POSTAL_LATE", "X"])
        self.assertEqual(g["pofa_finding"], "POFA_POSTAL_LATE")


class P82VerifiedGroundAuthority(unittest.TestCase):
    def test_verified_finding_survives_empty_ci_selection(self):
        """TEST 1: verified finding exists; CI selects nothing; plan still has it."""
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        item = plan.item("KB-POFA-02")
        self.assertEqual(item.status, SUPPORTED)
        self.assertEqual(item.decision, VERIFIED_FINDING)
        self.assertTrue(any("overrides omission" in line for line in plan.source_trace()),
                        plan.source_trace())

    def test_sainsburys_shaped_narrative_keeps_pofa_and_adds_anpr(self):
        """TEST 2 / regression: late NTK + narrative — verified ground remains, new added."""
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27",
                                    parking_location="Sainsburys - Cromwell Road"))
        run(case, pipe, "", {})
        self.assertIn("KB-POFA-02", latest_locked(case).supported_ids)
        pipe.answer(case, {"multiple_visits": True})
        out = pipe.generate(case)
        plan = latest_locked(case)
        self.assertIn("KB-POFA-02", plan.supported_ids, plan.trace())
        self.assertIn("KB-ANPR-01", plan.supported_ids, plan.trace())
        pofa = plan.item("KB-POFA-02")
        self.assertEqual(pofa.status, SUPPORTED)
        self.assertIn(pofa.decision, (VERIFIED_FINDING, CARRIED_FORWARD, SELECTED))
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)

    def test_ci_reject_cannot_block_verified_rescue(self):
        """TEST 3: CI proposes invalidation; verified finding wins."""
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        proposals = pipe.claim_authority.proposals(case)
        proposals["selected"] = []
        proposals["add_ground_candidates"] = []
        proposals["support_existing_ground"] = []
        proposals["proposed_invalidations"] = [{
            "ground_id": "KB-POFA-02", "reason": "Case Intelligence did not select it",
        }]
        plan = pipe.claim_authority.build(case, version=11, proposals=proposals)
        self.assertEqual(plan.item("KB-POFA-02").status, SUPPORTED)
        self.assertEqual(plan.item("KB-POFA-02").decision, VERIFIED_FINDING)
        refused = dict(plan.trust).get("refused_invalidations") or []
        self.assertTrue(any(r.get("ground_id") == "KB-POFA-02" for r in refused), refused)

    def test_ground_removal_requires_invalidation_record(self):
        """TEST 4: omission is not removal; a fact change is, and records it."""
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27"))
        run(case, pipe, "", {"multiple_visits": True})
        self.assertIn("KB-ANPR-01", latest_locked(case).supported_ids)
        case.analysis_module_ids = []
        kept = pipe.claim_authority.build(case, version=9)
        self.assertIn("KB-POFA-02", kept.supported_ids)
        self.assertIn("KB-ANPR-01", kept.supported_ids)
        self.assertFalse(any(i.get("ground_id") == "KB-POFA-02"
                             for i in dict(kept.trust).get("invalidations") or []))
        case.put(Fact("F-mv", "multiple_visits", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        dropped = pipe.claim_authority.build(case, version=10)
        item = dropped.item("KB-ANPR-01")
        self.assertEqual(item.status, "REJECTED")
        inv = [i for i in dict(dropped.trust).get("invalidations") or []
               if i.get("ground_id") == "KB-ANPR-01"]
        self.assertTrue(inv, dict(dropped.trust).get("invalidations"))
        self.assertTrue(inv[0]["reason"])
        self.assertEqual(inv[0]["previous_plan_version"], latest_locked(case).version)

    def test_reload_reuses_the_same_claim_plan(self):
        """TEST 5: persist and reload — same supported grounds and digest."""
        db = sqlite_store.install(self)
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        stored = store.new_case()
        case.case_id = stored.case_id
        for plan in case.claim_plans:
            object.__setattr__(plan, "case_id", stored.case_id)
        locked = latest_locked(case)
        store.save(case)
        loaded = store.load(case.case_id)
        again = latest_locked(loaded)
        self.assertEqual(again.supported_ids, locked.supported_ids)
        self.assertEqual(again.plan_digest, locked.plan_digest)
        self.assertEqual(again.inputs_digest, locked.inputs_digest)
        self.assertGreaterEqual(sqlite_store.count(db, "claim_plans"), 1)

    def test_priority_order_is_verified_then_carried_then_selected_then_candidate(self):
        """TEST 6: authority ranking."""
        self.assertEqual(ORIGIN_PRIORITY,
                         (VERIFIED_FINDING, CARRIED_FORWARD, SELECTED, CANDIDATE))
        b = type("B", (), {})()
        from pcn_appeal.engines.claim_plan_authority import ClaimPlanBuilder
        rank = ClaimPlanBuilder._origin_rank
        self.assertLess(rank(None, VERIFIED_FINDING), rank(None, CARRIED_FORWARD))
        self.assertLess(rank(None, CARRIED_FORWARD), rank(None, SELECTED))
        self.assertLess(rank(None, SELECTED), rank(None, CANDIDATE))
        self.assertLess(rank(None, SELECTED), rank(None, "NOT_SELECTED"))


class P83DocumentBelt(unittest.TestCase):
    def test_verified_finding_reaches_plan_when_ci_selects_nothing(self):
        """TEST 1: deterministic finding exists; CI returns no matching ground."""
        from pcn_appeal.document_baseline import latest_baseline
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        self.assertEqual(plan.item("KB-POFA-02").status, SUPPORTED)
        base = latest_baseline(case)
        self.assertIsNotNone(base)
        self.assertIn("KB-POFA-02", base.document_grounds)
        self.assertIn("POFA_POSTAL_LATE", base.document_finding_types)

    def test_baseline_survives_customer_narrative(self):
        """TEST 2: notice baseline remains; customer grounds are added."""
        from pcn_appeal.document_baseline import latest_analysis_state, latest_baseline
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27",
                                    parking_location="Sainsburys - Cromwell Road"))
        run(case, pipe, "", {})
        before = latest_baseline(case)
        self.assertIn("KB-POFA-02", before.document_grounds)
        pipe.answer(case, {"multiple_visits": True})
        pipe.generate(case)
        after = latest_baseline(case)
        self.assertEqual(after.digest, before.digest)
        self.assertEqual(after.document_grounds, before.document_grounds)
        plan = latest_locked(case)
        self.assertIn("KB-POFA-02", plan.supported_ids)
        self.assertIn("KB-ANPR-01", plan.supported_ids)
        delta = latest_analysis_state(case)
        self.assertIn("KB-ANPR-01", delta.add_ground_candidates)

    def test_same_notice_twice_same_baseline_digest(self):
        """TEST 3: the same notice produces the same document baseline digest."""
        from pcn_appeal.document_baseline import establish_document_baseline, latest_baseline
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        first = latest_baseline(case)
        reused = establish_document_baseline(pipe, case)
        self.assertEqual(reused.digest, first.digest)
        self.assertEqual(reused.version, first.version)
        self.assertEqual(len(case.document_baselines), 1)
        other, other_pipe = make_case(LATE)
        run(other, other_pipe, "", {})
        self.assertEqual(latest_baseline(other).digest, first.digest)
        sqlite_store.install(self)
        stored = store.new_case()
        case.case_id = stored.case_id
        for plan in case.claim_plans:
            object.__setattr__(plan, "case_id", stored.case_id)
        store.save(case)
        loaded = store.load(case.case_id)
        self.assertEqual(latest_baseline(loaded).digest, first.digest)
        self.assertEqual(latest_baseline(loaded).document_grounds, first.document_grounds)
        establish_document_baseline(pipe, loaded)
        self.assertEqual(latest_baseline(loaded).digest, first.digest)

    def test_customer_narrative_cannot_remove_document_finding(self):
        """TEST 4: narrative / empty CI does not invalidate a document finding."""
        from pcn_appeal.document_baseline import latest_baseline
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27"))
        run(case, pipe, "", {})
        pipe.answer(case, {"multiple_visits": True})
        pipe.generate(case)
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=20)
        self.assertIn("KB-POFA-02", plan.supported_ids)
        inv = [i for i in dict(plan.trust).get("invalidations") or []
               if i.get("ground_id") == "KB-POFA-02"]
        self.assertEqual(inv, [])
        self.assertIn("KB-POFA-02", latest_baseline(case).document_grounds)

    def test_missing_document_data_creates_no_finding(self):
        """TEST 5: no dates → no verified defect, no hallucinated ground."""
        from pcn_appeal.document_baseline import latest_baseline
        case, pipe = make_case_without("notice_issue_date")
        run(case, pipe, "", {})
        rec = next((r for r in case.legal_findings
                    if r.get("finding_type") == "POFA_POSTAL_LATE"), None)
        if rec is not None:
            self.assertNotEqual(rec["status"], "VERIFIED")
        base = latest_baseline(case)
        self.assertIsNotNone(base)
        self.assertNotIn("POFA_POSTAL_LATE", base.document_finding_types)
        plan = latest_locked(case)
        if plan is not None and plan.item("KB-POFA-02") is not None:
            self.assertNotEqual(plan.item("KB-POFA-02").status, SUPPORTED)


class P84FactLifecycle(unittest.TestCase):
    def test_narrative_facts_persist_with_provenance(self):
        """TEST 1: customer narrative creates facts with provenance."""
        from pcn_appeal.engines.account import assess_material_account
        from pcn_appeal.fact_lifecycle import EXTRACTOR_VERSION
        case = CaseFile(case_id="C-P84-1")
        case.put(Fact("F-br", "alleged_breach", "Overstayed", FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.raw_answers["narrative"] = "We left and came back later the same day."
        assess_material_account(case)
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))
        self.assertTrue(case.free_text_provenance or case.get("left_site"))
        if case.free_text_provenance:
            row = case.free_text_provenance[0]
            self.assertIn(row.get("extractor_version", EXTRACTOR_VERSION), (EXTRACTOR_VERSION, None))
        self.assertTrue(any(v.get("name") == "left_site" for v in case.fact_versions))

    def test_reassessment_keeps_unchanged_facts(self):
        """TEST 2: reassessment does not drop unchanged material facts."""
        from pcn_appeal.engines.account import assess_material_account
        case = CaseFile(case_id="C-P84-2")
        case.put(Fact("F-br", "alleged_breach", "Overstayed", FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.raw_answers["narrative"] = "We left and came back later the same day."
        assess_material_account(case)
        node = case.facts.node_id("left_site")
        status = case.facts["left_site"].status
        assess_material_account(case)
        self.assertTrue(case.get("left_site"))
        self.assertEqual(case.facts.node_id("left_site"), node)
        self.assertEqual(case.facts["left_site"].status, status)

    def test_derived_fact_lineage_is_complete(self):
        """TEST 3: multiple_visits names the facts it was derived from."""
        from pcn_appeal import case_state
        case = CaseFile(case_id="C-P84-3")
        case.put(Fact("F-a", "left_site", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:narrative:left_site")))
        case.put(Fact("F-b", "returned_same_day", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:narrative:returned_same_day")))
        with case_state.derives(case, "left_site", "returned_same_day",
                                rule="narrative.multiple_visits"):
            case.put(Fact("F-c", "multiple_visits", True, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "narrative.multiple_visits")))
        row = case.master.derived_facts["multiple_visits"]
        self.assertEqual(row["source_facts"], ["left_site", "returned_same_day"])
        self.assertEqual(row["derived_from"],
                         [case.facts.node_id("left_site"), case.facts.node_id("returned_same_day")])
        self.assertEqual(row.get("lineage_status"), "COMPLETE")
        self.assertEqual(case.master.lineage_gaps(), [])

    def test_same_value_does_not_rewrite_source_or_status(self):
        """TEST 4: equal value never demotes a confirmed fact."""
        case = CaseFile(case_id="C-P84-4")
        case.put(Fact("F1", "payment_made", True, FactStatus.CONFIRMED,
                      FactSource(SourceKind.ANSWER, "answer:payment_made")))
        held_status = case.facts["payment_made"].status
        held_kind = case.facts["payment_made"].source.kind
        r = FactManager.update_fact(case, Fact(
            "F2", "payment_made", True, FactStatus.ANSWERED,
            FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:payment_made")))
        self.assertEqual(r.outcome, IGNORED_DUPLICATE)
        self.assertEqual(case.facts["payment_made"].status, held_status)
        self.assertEqual(case.facts["payment_made"].source.kind, held_kind)

    def test_correction_preserves_the_old_generation(self):
        """TEST 5: left_site true → false keeps the old record and reasons it."""
        from pcn_appeal.fact_lifecycle import SUPERSEDED, generations
        case = CaseFile(case_id="C-P84-5")
        case.put(Fact("F1", "left_site", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:narrative:left_site",
                                 excerpt="I left the site")))
        case.put(Fact("F2", "left_site", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:left_site",
                                 excerpt="Actually I never left")),
                 reason="customer correction")
        self.assertEqual(case.get("left_site"), False)
        gens = generations(case, "left_site")
        self.assertGreaterEqual(len(gens), 2)
        self.assertTrue(any(g.get("value") is True for g in gens))
        self.assertEqual(gens[-1]["value"], False)
        self.assertTrue(any(g.get("supersedes") or g.get("lifecycle") == SUPERSEDED
                            for g in gens))

    def test_reload_keeps_active_facts_provenance_and_digest(self):
        """TEST 6: save/reload — same active facts, provenance, digest."""
        from pcn_appeal.engines.account import assess_material_account
        db = sqlite_store.install(self)
        case = CaseFile(case_id="C-P84-6")
        case.put(Fact("F-br", "alleged_breach", "Overstayed", FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.raw_answers["narrative"] = "We left and came back later the same day."
        assess_material_account(case)
        stored = store.new_case()
        case.case_id = stored.case_id
        before = case.master.digest()
        active = {n: case.get(n) for n in ("left_site", "returned_same_day") if case.has(n)}
        store.save(case)
        loaded = store.load(case.case_id)
        self.assertEqual({n: loaded.get(n) for n in active}, active)
        self.assertTrue(loaded.free_text_provenance or loaded.fact_versions)
        self.assertEqual(loaded.master.digest(), before)
        self.assertGreaterEqual(sqlite_store.count(db, "raw_answers"), 1)


class P87FactStability(unittest.TestCase):
    """P8.7: FactManager same-value, authority, conflict, derived stability."""

    def test_same_fact_written_twice_keeps_one_active_and_two_provenance(self):
        """TEST 1: same fact twice — one active, two provenance, no downgrade."""
        case = CaseFile(case_id="C-P87-1")
        case.put(Fact("F1", "payment_made", True, FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1"), 0.95))
        held = case.facts["payment_made"]
        r = FactManager.update_fact(case, Fact(
            "F2", "payment_made", True, FactStatus.ANSWERED,
            FactSource(SourceKind.CUSTOMER_FREE_TEXT, "account:payment_made"), 0.4))
        self.assertEqual(r.outcome, IGNORED_DUPLICATE)
        self.assertEqual(r.reason, "Existing fact has higher authority.")
        active = case.facts["payment_made"]
        self.assertEqual(active.value, True)
        self.assertEqual(active.source.kind, SourceKind.DOCUMENT)
        self.assertEqual(active.status, FactStatus.EXTRACTED)
        self.assertGreaterEqual(active.confidence, held.confidence)
        self.assertEqual(len([s for s in case.fact_sources if s["fact"] == "payment_made"]), 2)
        self.assertEqual(len([v for v in case.fact_versions if v["name"] == "payment_made"
                              and v["lifecycle"] == "ACTIVE"]), 1)

    def test_higher_authority_survives_lower_write(self):
        """TEST 2: higher authority followed by lower — higher remains."""
        from pcn_appeal.fact_graph import DOCUMENT_CONFIRMED, fact_authority
        case = CaseFile(case_id="C-P87-2")
        case.put(Fact("F1", "payment_made", True, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        self.assertEqual(fact_authority(case.facts["payment_made"]), DOCUMENT_CONFIRMED)
        r = FactManager.update_fact(case, Fact(
            "F2", "payment_made", True, FactStatus.ANSWERED,
            FactSource(SourceKind.ANSWER, "analysis:payment_made")))
        self.assertEqual(r.outcome, IGNORED_DUPLICATE)
        self.assertEqual(case.facts["payment_made"].source.kind, SourceKind.DOCUMENT)
        self.assertEqual(case.facts["payment_made"].status, FactStatus.CONFIRMED)

    def test_conflicting_values_create_a_conflict_not_an_overwrite(self):
        """TEST 3: conflicting values — conflict record, no silent overwrite."""
        case = CaseFile(case_id="C-P87-3")
        case.put(Fact("F1", "left_site", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:left_site")))
        applied = case.put(Fact("F2", "left_site", False, FactStatus.ANSWERED,
                                FactSource(SourceKind.CUSTOMER_FREE_TEXT, "account:left_site")))
        self.assertFalse(applied)
        self.assertIs(case.get("left_site"), True)
        self.assertTrue(case.fact_conflicts)
        c = case.fact_conflicts[-1]
        self.assertEqual(c["previous_value"], True)
        self.assertEqual(c["new_value"], False)
        self.assertEqual(c["resolution_status"], "KEPT_EXISTING")
        self.assertTrue(c.get("sources"))

    def test_reload_restores_identical_fact_state(self):
        """TEST 4: reload — identical active value, authority, provenance, conflicts."""
        db = sqlite_store.install(self)
        case = CaseFile(case_id="C-P87-4")
        case.put(Fact("F1", "payment_made", True, FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        FactManager.update_fact(case, Fact(
            "F2", "payment_made", True, FactStatus.ANSWERED,
            FactSource(SourceKind.ANSWER, "a")))
        case.put(Fact("F3", "left_site", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:left_site")))
        case.put(Fact("F4", "left_site", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "account:left_site")))
        stored = store.new_case()
        case.case_id = stored.case_id
        before = {
            "payment": (case.get("payment_made"),
                        case.facts["payment_made"].source.kind.value,
                        case.facts["payment_made"].status.value),
            "left": case.get("left_site"),
            "conflicts": len(case.fact_conflicts),
            "sources": len(case.fact_sources),
            "digest": case.master.digest(),
        }
        store.save(case)
        loaded = store.load(case.case_id)
        self.assertEqual(loaded.get("payment_made"), before["payment"][0])
        self.assertEqual(loaded.facts["payment_made"].source.kind.value, before["payment"][1])
        self.assertEqual(loaded.facts["payment_made"].status.value, before["payment"][2])
        self.assertEqual(loaded.get("left_site"), before["left"])
        self.assertEqual(len(loaded.fact_conflicts), before["conflicts"])
        self.assertEqual(loaded.master.digest(), before["digest"])
        self.assertTrue(loaded.master.active_facts.get("payment_made"))
        self.assertGreaterEqual(sqlite_store.count(db, "facts"), 1)

    def test_derived_fact_is_not_regenerated_when_sources_hold(self):
        """TEST 5: derived regeneration with unchanged sources — no new version."""
        from pcn_appeal import case_state
        from pcn_appeal.fact_lifecycle import ACTIVE, generations
        case = CaseFile(case_id="C-P87-5")
        case.put(Fact("F-a", "left_site", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:left_site")))
        case.put(Fact("F-b", "returned_same_day", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:returned")))
        with case_state.derives(case, "left_site", "returned_same_day",
                                rule="narrative.multiple_visits"):
            case.put(Fact("F-c", "multiple_visits", True, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "narrative.multiple_visits")))
            r = FactManager.update_fact(case, Fact(
                "F-c2", "multiple_visits", True, FactStatus.DERIVED,
                FactSource(SourceKind.CALCULATION, "narrative.multiple_visits")))
        self.assertEqual(r.outcome, IGNORED_DUPLICATE)
        active = [g for g in generations(case, "multiple_visits") if g.get("lifecycle") == ACTIVE]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(case.master.derived_facts["multiple_visits"]["source_facts"]), 2)

    def test_full_pipeline_repeat_is_stable(self):
        """TEST 6: same inputs five times — same facts, findings, plan, digest."""
        snaps = []
        for _ in range(5):
            case, pipe = make_case(LATE)
            run(case, pipe, "", {})
            plan = latest_locked(case)
            snaps.append({
                "facts": {n: (plain_val(case.get(n)),
                              case.facts[n].status.value,
                              case.facts[n].source.kind.value)
                          for n in sorted(case.facts) if case.facts[n].usable},
                "findings": sorted(
                    (f.get("finding_type") or f.get("family"), f.get("status"))
                    for f in (case.legal_findings or [])),
                "plan": list(plan.supported_ids) if plan else [],
                "digest": plan.plan_digest if plan else None,
            })
        first = snaps[0]
        for i, snap in enumerate(snaps[1:], start=2):
            self.assertEqual(snap["facts"], first["facts"], f"run {i} facts drifted")
            self.assertEqual(snap["findings"], first["findings"], f"run {i} findings drifted")
            self.assertEqual(snap["plan"], first["plan"], f"run {i} plan drifted")
            self.assertEqual(snap["digest"], first["digest"], f"run {i} plan digest drifted")


def plain_val(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class P85LineageValidation(unittest.TestCase):
    def test_narrative_support_without_derived_bridge_fails(self):
        engine = DraftValidationEngine()
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[],
            module_ids=["KB-ANPR-01"], verified_facts={}, fact_refs={},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=[],
            legal_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[],
            lease_clauses=[],
            claim_plan={
                "approved": ["KB-ANPR-01"],
                "labels": {"KB-ANPR-01": "Multiple visits"},
                "support_facts": {
                    "KB-ANPR-01": ["left_site", "returned_same_day", "multiple_visits"],
                },
            },
        )
        d = Draft("C", [[DraftSentence(
            "I write as the registered keeper. Please cancel the charge.",
            module_refs=["KB-ANPR-01"])]])
        r = engine.check(d, pack, set())
        self.assertIn("VAL-LINEAGE", {i.rule for i in r.issues})


class P85ParticularisationContract(unittest.TestCase):
    def _item(self, *, sources=True, derived=True):
        from pcn_appeal.drafting.support_contract import build_bundle, build_requirement
        rows = []
        if derived:
            row = {"fact": "multiple_visits", "value": True, "fact_id": "F-mv"}
            if sources:
                row["because_of"] = [
                    {"fact": "left_site", "value": True, "fact_id": "F-left"},
                    {"fact": "returned_same_day", "value": True, "fact_id": "F-ret"},
                ]
            rows.append(row)
        elif sources:
            rows.append({"fact": "left_site", "value": True, "fact_id": "F-left"})
        bundle = build_bundle(rows)
        req = build_requirement(bundle)
        return bundle, req

    def test_ground_with_source_facts_has_complete_bundle(self):
        """TEST 1: source + derived → SupportBundle complete."""
        bundle, req = self._item()
        self.assertTrue(bundle.complete())
        self.assertIn("left_site", bundle.source_fact_names)
        self.assertIn("returned_same_day", bundle.source_fact_names)
        self.assertIn("multiple_visits", bundle.derived_fact_names)
        self.assertTrue(req.required_particulars)

    def test_derived_fact_without_lineage_fails_validation(self):
        """TEST 2: derived fact with no sources → VAL-LINEAGE."""
        from pcn_appeal.drafting.support_contract import SupportBundle, DraftRequirement
        bundle = SupportBundle(derived_fact_ids=("F-mv",),
                               derived_fact_names=("multiple_visits",))
        req = DraftRequirement(required_particulars=("multiple_visits",),
                               explanation_goal=("state the source sequence",))
        self.assertFalse(bundle.complete())
        engine = DraftValidationEngine()
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[],
            module_ids=["KB-ANPR-01"], verified_facts={"multiple_visits": True},
            fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=[],
            legal_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            claim_plan={
                "approved": ["KB-ANPR-01"],
                "labels": {"KB-ANPR-01": "Multiple visits"},
                "support_facts": {"KB-ANPR-01": ["multiple_visits"]},
                "support_bundles": {"KB-ANPR-01": bundle.as_dict()},
                "draft_requirements": {"KB-ANPR-01": req.as_dict()},
            },
        )
        d = Draft("C", [[DraftSentence("Please cancel the charge.",
                                      module_refs=["KB-ANPR-01"])]])
        r = engine.check(d, pack, set())
        self.assertIn("VAL-LINEAGE", {i.rule for i in r.issues})

    def test_requirements_without_support_cannot_build_context(self):
        """TEST 3: DraftRequirement present, SupportBundle empty → no DraftContext."""
        from pcn_appeal.drafting.context import DraftContext, DraftContextError
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[],
            module_ids=["KB-ANPR-01"], verified_facts={}, fact_refs={},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=[],
            legal_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            claim_plan={
                "status": "LOCKED", "approved": ["KB-ANPR-01"],
                "module_ids": ["KB-ANPR-01"],
                "draft_requirements": {
                    "KB-ANPR-01": {"required_particulars": ["left_site"],
                                   "prohibited_content": [], "explanation_goal": []},
                },
                "support_bundles": {
                    "KB-ANPR-01": {"source_fact_ids": [], "derived_fact_ids": ["F-mv"],
                                   "source_fact_names": [], "derived_fact_names": ["multiple_visits"]},
                },
            },
        )
        pack.case_context = {"claim_plan": pack.claim_plan}
        with self.assertRaises(DraftContextError):
            DraftContext.from_pack(pack)

    def test_draft_omitting_material_facts_fails_particulars(self):
        """TEST 4: required left_site / returned_same_day, vague draft → FAIL."""
        bundle, req = self._item()
        engine = DraftValidationEngine()
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[],
            module_ids=["KB-ANPR-01"],
            verified_facts={"multiple_visits": True, "left_site": True,
                            "returned_same_day": True},
            fact_refs={"multiple_visits": "F-mv"},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=[],
            legal_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            claim_plan={
                "approved": ["KB-ANPR-01"],
                "labels": {"KB-ANPR-01": "Multiple visits"},
                "support_bundles": {"KB-ANPR-01": bundle.as_dict()},
                "draft_requirements": {"KB-ANPR-01": req.as_dict()},
            },
        )
        d = Draft("C", [[DraftSentence(
            "The vehicle attended more than once. Please cancel the charge.",
            module_refs=["KB-ANPR-01"], fact_refs=["F-mv"])]])
        r = engine.check(d, pack, set())
        self.assertIn("VAL-DRAFT-PARTICULARS", {i.rule for i in r.issues})

    def test_lifecycle_keeps_material_facts_in_context(self):
        """TEST 5: narrative → graph → plan → DraftContext keeps source facts."""
        from pcn_appeal import case_state
        from pcn_appeal.drafting.context import DraftContext
        from pcn_appeal.drafting.support_contract import build_bundle, build_requirement
        from pcn_appeal.engines.account import assess_material_account
        case = CaseFile(case_id="C-P85-5")
        case.put(Fact("F-br", "alleged_breach", "Overstayed", FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.raw_answers["narrative"] = "We left and came back later the same day."
        assess_material_account(case)
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))
        with case_state.derives(case, "left_site", "returned_same_day",
                                rule="narrative.multiple_visits"):
            case.put(Fact("F-c", "multiple_visits", True, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "narrative.multiple_visits")))
        bundle = build_bundle([{
            "fact": "multiple_visits", "value": True,
            "fact_id": case.facts.node_id("multiple_visits"),
            "because_of": [
                {"fact": "left_site", "value": True,
                 "fact_id": case.facts.node_id("left_site")},
                {"fact": "returned_same_day", "value": True,
                 "fact_id": case.facts.node_id("returned_same_day")},
            ],
        }], case=case)
        req = build_requirement(bundle)
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[],
            module_ids=["KB-ANPR-01"],
            verified_facts={"multiple_visits": True},
            fact_refs={"multiple_visits": case.facts.node_id("multiple_visits") or "F-c"},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=[],
            legal_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            claim_plan={
                "status": "LOCKED", "approved": ["KB-ANPR-01"],
                "module_ids": ["KB-ANPR-01"],
                "support_bundles": {"KB-ANPR-01": bundle.as_dict()},
                "draft_requirements": {"KB-ANPR-01": req.as_dict()},
            },
        )
        pack.case_context = {"claim_plan": pack.claim_plan}
        ctx = DraftContext.from_pack(pack)
        self.assertEqual(ctx.facts.get("left_site"), True)
        self.assertEqual(ctx.facts.get("returned_same_day"), True)
        self.assertEqual(ctx.facts.get("multiple_visits"), True)

    def test_pofa_timing_draft_must_state_the_calculation(self):
        """TEST 6: PoFA timing particulars — dates and days late — must appear."""
        from pcn_appeal.legal.findings import particularised_sentence
        finding = {
            "finding_id": "LF-1", "finding_type": "POFA_POSTAL_LATE",
            "status": "VERIFIED", "legal_module_id": "KB-POFA-01",
            "calculation_result": {
                "parking_event_date": "2026-07-17",
                "notice_issue_date": "2026-08-10",
                "deadline": "2026-08-01",
                "presumed_delivery": "2026-08-12",
                "days_between": 11,
            },
        }
        engine = DraftValidationEngine()
        req = {
            "required_particulars": [
                "parking_event_date", "notice_issue_date", "deadline",
                "presumed_delivery", "days_late"],
            "prohibited_content": [],
            "explanation_goal": ["state each calculated date and the day count"],
        }
        bundle = {
            "source_fact_ids": ["F-event"], "source_fact_names": ["parking_event_date"],
            "legal_finding_ids": ["LF-1"], "derived_fact_ids": [],
        }
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[],
            module_ids=["KB-POFA-01"],
            verified_facts={"parking_event_date": "2026-07-17",
                            "notice_issue_date": "2026-08-10"},
            fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=["POFA_POSTAL_LATE"],
            legal_findings=[finding], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            claim_plan={
                "approved": ["KB-POFA-01"],
                "labels": {"KB-POFA-01": "Notice timing"},
                "support_bundles": {"KB-POFA-01": bundle},
                "draft_requirements": {"KB-POFA-01": req},
            },
        )
        vague = Draft("C", [[DraftSentence(
            "The notice appears to have been served late. Please cancel the charge.",
            module_refs=["KB-POFA-01"])]])
        self.assertIn("VAL-DRAFT-PARTICULARS",
                      {i.rule for i in engine.check(vague, pack, set()).issues})
        full = Draft("C", [[DraftSentence(
            particularised_sentence(finding) + " Please cancel the charge.",
            module_refs=["KB-POFA-01"])]])
        self.assertNotIn("VAL-DRAFT-PARTICULARS",
                         {i.rule for i in engine.check(full, pack, set()).issues})


class P86JoinedIntegrity(unittest.TestCase):
    """P8.6: KM → CI → Claim Plan → Draft join. Diagnostics only."""

    def _history(self, payload):
        return [(d["module_id"], d["stage"], d["decision"], d["reason"])
                for d in payload.get("module_decisions") or []]

    def test_matched_module_becomes_selected_with_full_chain(self):
        """TEST 1: matched module selected — full trace chain exists."""
        from pcn_appeal.integrity import console as cons
        from pcn_appeal.integrity.module_decisions import (
            CASE_INTELLIGENCE, CLAIM_PLAN, DRAFT_AUTHORITY, KNOWLEDGE_MATCH,
            MATCHED, SELECTED, SUPPORTED as MD_SUPPORTED, USED,
        )
        case = CaseFile(case_id="C-P86-1")
        case.state = CaseState.DRAFTED
        case.run_id = 1
        case.facts = {
            "left_site": Fact("F-l", "left_site", True, FactStatus.ANSWERED,
                              FactSource(SourceKind.ANSWER, "a")),
            "returned_same_day": Fact("F-r", "returned_same_day", True, FactStatus.ANSWERED,
                                      FactSource(SourceKind.ANSWER, "a")),
            "pcn_number": Fact("F-p", "pcn_number", "1", FactStatus.CONFIRMED,
                               FactSource(SourceKind.DOCUMENT, "d")),
        }
        case.audit.append({
            "event": "knowledge_match", "at": "2026-10-04T10:00:00Z",
            "selected": [{
                "module": "KB-ANPR-01",
                "selected_because": ["left_site", "returned_same_day"],
                "facts": ["left_site", "returned_same_day"],
                "reason": "multiple visit relationship available",
            }],
            "relevant": [], "rejected": [],
        })
        case.audit.append({
            "event": "case_analysis", "at": "2026-10-04T10:00:01Z",
            "kept": ["KB-ANPR-01"], "proposed": ["KB-ANPR-01"],
            "not_supported": [],
        })
        plan = FinalClaimPlan(
            claim_plan_id="CP-P86-1", case_id="C-P86-1",
            analysis_run_id="run-1", run_number=1, version=1,
            inputs_digest="x", trust={},
        )
        plan.add_item(ClaimPlanItem(
            item_id="I-ANPR", knowledge_id="KB-ANPR-01", module_id="KB-ANPR-01",
            claim_type="ANPR", status=SUPPORTED, decision=SELECTED,
            reason="multiple visit relationship available",
            supporting_facts=({"fact": "left_site", "value": True},
                              {"fact": "returned_same_day", "value": True}),
            evidence_refs=(), relationships=(), priority=1, topic="anpr",
        ))
        plan.confirm().lock()
        case.claim_plans = [plan]
        draft = Draft("C-P86-1", [[DraftSentence(
            "The ANPR pair records two visits the same day.",
            module_refs=["KB-ANPR-01"])]])
        out = type("O", (), {"draft": draft, "validation": None, "outcome": None})()
        payload = cons.build_console(case, out)
        row = next(r for r in payload["module_journey"] if r["module_id"] == "KB-ANPR-01")
        self.assertEqual(row["knowledge"]["decision"], MATCHED)
        self.assertEqual(row["case_intelligence"]["decision"], SELECTED)
        self.assertEqual(row["claim_plan"]["decision"], MD_SUPPORTED)
        self.assertEqual(row["draft"]["decision"], USED)
        stages = {d["stage"] for d in row["history"]}
        self.assertTrue({KNOWLEDGE_MATCH, CASE_INTELLIGENCE, CLAIM_PLAN, DRAFT_AUTHORITY}
                        <= stages)
        self.assertEqual(row["integrity"], "PASS")
        self.assertFalse(payload["grounds"]["integrity_errors"])

    def test_matched_module_rejected_for_missing_facts(self):
        """TEST 2: rejected because facts missing — no integrity failure."""
        from pcn_appeal.engines.claim_plan_authority import MISSING_FACTS, REJECTED
        from pcn_appeal.integrity import console as cons
        case = CaseFile(case_id="C-P86-2")
        case.state = CaseState.ANALYSED
        case.run_id = 1
        case.facts = {"pcn_number": Fact("F-p", "pcn_number", "1", FactStatus.CONFIRMED,
                                         FactSource(SourceKind.DOCUMENT, "d"))}
        case.audit.append({
            "event": "knowledge_match",
            "selected": [],
            "relevant": [{"module": "KB-PAY-01", "missing": ["payment_made"],
                          "reason": "use_when not yet met"}],
            "rejected": [{"module": "KB-PAY-01", "status": "REJECTED",
                          "missing": ["payment_made"],
                          "reason": "use_when not yet met: no confirmed payment_made"}],
        })
        case.audit.append({
            "event": "case_analysis",
            "kept": [], "proposed": ["KB-PAY-01"],
            "not_supported": [{"module_id": "KB-PAY-01",
                               "reason": "payment_made missing",
                               "missing": ["payment_made"]}],
        })
        plan = FinalClaimPlan(
            claim_plan_id="CP-P86-2", case_id="C-P86-2",
            analysis_run_id="run-1", run_number=1, version=1,
            inputs_digest="x", trust={},
        )
        plan.add_item(ClaimPlanItem(
            item_id="I-PAY", knowledge_id="KB-PAY-01", module_id="KB-PAY-01",
            claim_type="PAYMENT", status=REJECTED, decision=MISSING_FACTS,
            reason="not established: payment_made",
            supporting_facts=(), evidence_refs=(), relationships=(),
            priority=None, topic="payment",
        ))
        plan.confirm().lock()
        case.claim_plans = [plan]
        payload = cons.build_console(case)
        self.assertFalse(payload["grounds"]["integrity_errors"])
        row = next(r for r in payload["module_journey"] if r["module_id"] == "KB-PAY-01")
        self.assertEqual(row["case_intelligence"]["decision"], "REJECTED")
        self.assertTrue(row["expected_rejection"])
        self.assertEqual(row["integrity"], "PASS")
        self.assertIn("payment_made", row["missing_facts"])
        checks = {c["rule"]: c["status"] for c in payload["module_trace_checks"]
                  if c["module_id"] == "KB-PAY-01"}
        self.assertEqual(checks.get("VAL-EXPECTED-REJECTION"), "PASS")

    def test_verified_finding_has_corresponding_ground(self):
        """TEST 3: verified finding exists → claim plan contains the ground."""
        from pcn_appeal.integrity import console as cons
        from pcn_appeal.integrity.checks import check_case
        case = CaseFile(case_id="C-P86-3")
        case.state = CaseState.ANALYSED
        case.run_id = 1
        case.facts = {"pcn_number": Fact("F-p", "pcn_number", "1", FactStatus.CONFIRMED,
                                         FactSource(SourceKind.DOCUMENT, "d"))}
        case.legal_findings = [{
            "finding_id": "LF-1", "finding_type": "POFA_POSTAL_LATE",
            "status": "VERIFIED", "legal_module_id": "KB-POFA-02",
        }]
        plan = FinalClaimPlan(
            claim_plan_id="CP-P86-3", case_id="C-P86-3",
            analysis_run_id="run-1", run_number=1, version=1,
            inputs_digest="x", trust={},
        )
        plan.add_item(ClaimPlanItem(
            item_id="I-POFA", knowledge_id="KB-POFA-02", module_id="KB-POFA-02",
            claim_type="POFA", status=SUPPORTED, decision=VERIFIED_FINDING,
            reason="verified postal timing defect",
            supporting_facts=({"fact": "parking_event_date", "value": "2026-07-17"},),
            evidence_refs=(), relationships=(), priority=1, topic="pofa",
        ))
        plan.confirm().lock()
        case.claim_plans = [plan]
        payload = cons.build_console(case)
        self.assertIn("KB-POFA-02", payload["claim_plan"]["approved"])
        row = next(r for r in payload["module_journey"] if r["module_id"] == "KB-POFA-02")
        self.assertEqual(row["claim_plan"]["decision"], "SUPPORTED")
        self.assertEqual(row["integrity"], "PASS")
        presence = [c for c in payload["module_trace_checks"]
                    if c["rule"] == "VAL-VERIFIED-GROUND-PRESENCE"]
        self.assertTrue(presence)
        self.assertTrue(all(c["status"] == "PASS" for c in presence))
        results = {r["check"]: r["status"] for r in check_case(case)}
        self.assertEqual(results.get("VAL-VERIFIED-GROUND-PRESENCE"), "PASS")

    def test_ground_removed_without_invalidation_fails(self):
        """TEST 4: ground removed with no invalidation → integrity failure."""
        from pcn_appeal.integrity import console as cons
        case = CaseFile(case_id="C-P86-4")
        case.state = CaseState.ANALYSED
        case.run_id = 2
        case.facts = {"pcn_number": Fact("F-p", "pcn_number", "1", FactStatus.CONFIRMED,
                                         FactSource(SourceKind.DOCUMENT, "d"))}
        case.legal_findings = [{
            "finding_id": "LF-1", "finding_type": "POFA_POSTAL_LATE",
            "status": "VERIFIED", "legal_module_id": "KB-POFA-02",
        }]
        case.claim_plans = [
            FinalClaimPlan(
                claim_plan_id="CP-P86-4a", case_id="C-P86-4",
                analysis_run_id="run-1", run_number=1, version=1,
                inputs_digest="a", trust={},
            ),
            FinalClaimPlan(
                claim_plan_id="CP-P86-4b", case_id="C-P86-4",
                analysis_run_id="run-2", run_number=2, version=2,
                inputs_digest="b", trust={},
            ),
        ]
        a, b = case.claim_plans
        a.add_item(ClaimPlanItem(
            item_id="I-POFA-1", knowledge_id="KB-POFA-02", module_id="KB-POFA-02",
            claim_type="POFA", status=SUPPORTED, decision=VERIFIED_FINDING,
            reason="verified postal timing defect",
            supporting_facts=({"fact": "parking_event_date", "value": "2026-07-17"},),
            evidence_refs=(), relationships=(), priority=1, topic="pofa",
        ))
        a.confirm().lock()
        b.add_item(ClaimPlanItem(
            item_id="I-ANPR-2", knowledge_id="KB-ANPR-01", module_id="KB-ANPR-01",
            claim_type="ANPR", status=SUPPORTED, decision=SELECTED,
            reason="multiple visits",
            supporting_facts=({"fact": "multiple_visits", "value": True},),
            evidence_refs=(), relationships=(), priority=1, topic="anpr",
        ))
        b.confirm().lock()
        payload = cons.build_console(case)
        errs = payload["grounds"]["integrity_errors"]
        self.assertTrue(errs)
        self.assertTrue(any(e.get("ground") == "KB-POFA-02" for e in errs))
        presence = [c for c in payload["module_trace_checks"]
                    if c["rule"] == "VAL-VERIFIED-GROUND-PRESENCE"]
        self.assertTrue(any(c["status"] == "FAIL" for c in presence), presence)
        diff = cons.compare_runs(case)
        self.assertTrue(diff["integrity_errors"])
        self.assertFalse(diff["explained"])

    def test_two_runs_comparison_explains_every_change(self):
        """TEST 5: two-run comparison — all changes explained."""
        from pcn_appeal.engines.claim_plan_authority import MISSING_FACTS, REJECTED
        from pcn_appeal.integrity import console as cons
        case = CaseFile(case_id="C-P86-5")
        case.state = CaseState.ANALYSED
        case.run_id = 2
        case.facts = {
            "pcn_number": Fact("F-p", "pcn_number", "1", FactStatus.CONFIRMED,
                               FactSource(SourceKind.DOCUMENT, "d")),
            "left_site": Fact("F-l", "left_site", True, FactStatus.ANSWERED,
                              FactSource(SourceKind.ANSWER, "a")),
        }
        a = FinalClaimPlan(
            claim_plan_id="CP-P86-5a", case_id="C-P86-5",
            analysis_run_id="run-1", run_number=1, version=1,
            inputs_digest="a",
            trust={"facts_used": {"payment_made": {"value": None}},
                   "verified_finding_types": ["POFA_POSTAL_LATE"]},
        )
        a.add_item(ClaimPlanItem(
            item_id="I-PAY-1", knowledge_id="KB-PAY-01", module_id="KB-PAY-01",
            claim_type="PAYMENT", status=SUPPORTED, decision=SELECTED,
            reason="payment recorded",
            supporting_facts=({"fact": "payment_made", "value": True},),
            evidence_refs=(), relationships=(), priority=1, topic="pay",
        ))
        a.add_item(ClaimPlanItem(
            item_id="I-POFA-1", knowledge_id="KB-POFA-02", module_id="KB-POFA-02",
            claim_type="POFA", status=SUPPORTED, decision=VERIFIED_FINDING,
            reason="verified postal timing defect",
            supporting_facts=({"fact": "parking_event_date", "value": "2026-07-17"},),
            evidence_refs=(), relationships=(), priority=2, topic="pofa",
        ))
        a.confirm().lock()
        b = FinalClaimPlan(
            claim_plan_id="CP-P86-5b", case_id="C-P86-5",
            analysis_run_id="run-2", run_number=2, version=2,
            inputs_digest="b",
            trust={"facts_used": {"left_site": {"value": True}},
                   "verified_finding_types": ["POFA_POSTAL_LATE"],
                   "invalidations": []},
        )
        b.add_item(ClaimPlanItem(
            item_id="I-PAY-2", knowledge_id="KB-PAY-01", module_id="KB-PAY-01",
            claim_type="PAYMENT", status=REJECTED, decision=MISSING_FACTS,
            reason="not established: payment_made",
            supporting_facts=(), evidence_refs=(), relationships=(),
            priority=None, topic="pay",
        ))
        b.add_item(ClaimPlanItem(
            item_id="I-POFA-2", knowledge_id="KB-POFA-02", module_id="KB-POFA-02",
            claim_type="POFA", status=SUPPORTED, decision=VERIFIED_FINDING,
            reason="verified postal timing defect",
            supporting_facts=({"fact": "parking_event_date", "value": "2026-07-17"},),
            evidence_refs=(), relationships=(), priority=1, topic="pofa",
        ))
        b.add_item(ClaimPlanItem(
            item_id="I-ANPR-2", knowledge_id="KB-ANPR-01", module_id="KB-ANPR-01",
            claim_type="ANPR", status=SUPPORTED, decision=SELECTED,
            reason="multiple visit relationship available",
            supporting_facts=({"fact": "left_site", "value": True},),
            evidence_refs=(), relationships=(), priority=2, topic="anpr",
        ))
        b.confirm().lock()
        case.claim_plans = [a, b]
        diff = cons.compare_runs(case)
        self.assertIn("KB-ANPR-01", diff["grounds"]["added"])
        self.assertIn("KB-PAY-01", diff["grounds"]["removed"])
        self.assertTrue(diff["explained"])
        self.assertFalse(diff["integrity_errors"])
        pay = next(r for r in diff["removals"] if r["module_id"] == "KB-PAY-01")
        self.assertEqual(pay["kind"], "requirements_unavailable")
        self.assertIn("left_site", [f["name"] for f in diff["facts"]["added"]])

    def test_reload_trace_keeps_decision_history(self):
        """TEST 6: reload trace — same decision history."""
        from pcn_appeal.integrity import console as cons
        db = sqlite_store.install(self)
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        stored = store.new_case()
        case.case_id = stored.case_id
        for plan in case.claim_plans:
            object.__setattr__(plan, "case_id", stored.case_id)
        first = cons.build_console(case)
        store.save(case)
        loaded = store.load(case.case_id)
        second = cons.build_console(loaded)
        self.assertEqual(self._history(first), self._history(second))
        self.assertEqual(
            [(r["module_id"], r["knowledge"]["decision"], r["claim_plan"]["decision"])
             for r in first["module_journey"]],
            [(r["module_id"], r["knowledge"]["decision"], r["claim_plan"]["decision"])
             for r in second["module_journey"]],
        )
        self.assertGreaterEqual(sqlite_store.count(db, "audit_log"), 1)

def setUpModule():
    from support import finished_reader
    finished_reader.start()


def tearDownModule():
    from support import finished_reader
    finished_reader.stop()


if __name__ == "__main__":
    unittest.main()
