"""P5 - Claim Plan Authority: one locked decision object per case version.

Spec tests 1-7, plus the lock in the database, client trust (what produced a
plan, why a claim is in or out, what changed between runs), the admin routes,
and "they may propose, only the plan decides".

Run:  python -m unittest tests.test_claim_plan_authority -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api, manifest, prompts, version
from pcn_appeal.engines.claim_plan_authority import (CARRIED_FORWARD, EVIDENCE_REQUIRED, LOCKED,
                                                     NOT_SELECTED,
                                                     REJECTED, SUPERSEDED, SUPPORTED, UNRESOLVED,
                                                     ClaimPlanIntegrityError,
                                                     ClaimPlanItem, ClaimPlanLockedError,
                                                     latest_locked)
from pcn_appeal.knowledge_ingestion.graph import knowledge_id
from pcn_appeal.models import (CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact,
                               FactSource, FactStatus, SourceKind)
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.store import cases as store
import sqlite_store
from support import ReferenceAnalysisLLM
from test_question_authority import FULL, fields

NARRATIVE = "My little one was strapped in the back seat the whole time we were there"


def scenario(breach, answers=None, extra=None, case_id="C-P5", llm_cls=ReferenceAnalysisLLM,
             narrative=NARRATIVE):
    f = dict(FULL, alleged_breach=breach, **(extra or {}))
    llm = llm_cls({"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]}, ask=[])
    case = CaseFile(case_id, evidence={"E1": EvidenceItem(
        "E1", "OTHER", "notice.pdf", text="Notice to Keeper\nOperator: Acme Parking Ltd")})
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    answer(case, answers)
    pipe.confirm(case, {}, [n for n, fa in case.facts.items()
                            if fa.status == FactStatus.EXTRACTED], narrative)
    return case, pipe


def answer(case, answers):
    for k, v in (answers or {}).items():
        case.put(Fact(f"F-{k}", k, v, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, f"answer:{k}")))


def bay(**kw):
    return scenario("Parked in a Parent and Child bay without a child",
                    answers={"children_present": True}, **kw)


def anpr(**kw):
    return scenario("Overstayed paid time", answers={"multiple_visits": True},
                    extra={"entry_time": "10:00", "exit_time": "13:00"}, **kw)


def photo_notice(**kw):
    return scenario("Parked without displaying a valid ticket", answers={"multiple_visits": True},
                    extra={"observation_time": "10:15"}, **kw)


def drafting_payloads(pipe) -> list[str]:
    return [c["user"] for c in pipe.extraction.llm.calls if c["task"] == "drafting"]


# ---------------------------------------------------------------- 1
class SupportedModuleCreatesPlanItem(unittest.TestCase):
    """Spec 1: "Multiple visit issue / KB-ANPR-01 / SUPPORTED / multiple_visits=true"."""

    def test_supported_module_is_a_supported_item_with_its_facts(self):
        case, pipe = anpr()
        out = pipe.generate(case)
        plan = latest_locked(case)
        item = plan.item("KB-ANPR-01")
        self.assertEqual(item.status, SUPPORTED)
        self.assertEqual(item.knowledge_id, knowledge_id("KB-ANPR-01"))
        self.assertEqual(item.claim_type, "ANPR")
        self.assertIn("multiple_visits=true", [f["condition"] for f in item.supporting_facts])
        self.assertTrue(item.reason)
        self.assertEqual(item.priority, 1)
        self.assertEqual(out.pack.module_ids, plan.supported_ids)
        self.assertEqual(out.state, CaseState.RELEASED)

    def test_every_item_says_why(self):
        case, pipe = photo_notice()
        pipe.generate(case)
        plan = latest_locked(case)
        self.assertTrue(plan.items)
        for i in plan.items:
            with self.subTest(module=i.module_id):
                self.assertIn(i.status, (SUPPORTED, REJECTED, UNRESOLVED))
                self.assertTrue(i.reason)
                if i.status == SUPPORTED:
                    self.assertTrue(i.supporting_facts)
                    self.assertIsNotNone(i.priority)
                else:
                    self.assertIsNone(i.priority)

    def test_evidence_required_but_missing_is_unresolved_not_argued(self):
        """A claim whose every approved paragraph needs an enclosure is not
        argued until that evidence is uploaded; then a new version supports it."""
        case, pipe = scenario("Failed to comply with the terms and conditions",
                              answers={"signage_issue_raised": True,
                                       "signage_issue_type": "TERM_PROMINENCE"})
        pipe.generate(case)
        plan = latest_locked(case)
        item = plan.item("KB-SIGN-02")
        self.assertIsNotNone(item, plan.trace())
        self.assertEqual((item.status, item.decision), (UNRESOLVED, EVIDENCE_REQUIRED))
        self.assertNotIn("KB-SIGN-02", plan.supported_ids)
        self.assertEqual(plan.required_evidence()[0]["module_id"], "KB-SIGN-02")
        case.evidence["E2"] = EvidenceItem("E2", "PHOTO", "sign.jpg", text="photo of the sign")
        pipe._reanalyse(case, NARRATIVE)
        pipe.generate(case)
        v2 = latest_locked(case)
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.item("KB-SIGN-02").status, SUPPORTED)


# ---------------------------------------------------------------- 2
class UnsupportedClaimFailsValidation(unittest.TestCase):
    """Spec 2 / section 6: every argument in the draft must exist in the plan."""

    def setUp(self):
        self.case, self.pipe = photo_notice()     # ANPR is blocked for this notice
        self.pipe.generate(self.case)
        self.plan = latest_locked(self.case)
        self.pack = self.pipe.reasoning.pack_for(self.case, self.plan)

    def _validate(self, *sentences):
        return self.pipe.validation.validate(
            Draft(self.case.case_id, [list(sentences)]), self.pack)

    def plan_issues(self, result):
        return [i for i in result.issues if i.rule == "VAL-PLAN"]

    def test_anpr_paragraph_not_in_plan_fails(self):
        self.assertNotIn("KB-ANPR-01", self.plan.supported_ids)
        res = self._validate(DraftSentence(
            "The operator's ANPR records may combine separate visits into one stay.",
            module_refs=["KB-ANPR-01"]))
        self.assertFalse(res.passed)
        self.assertIn("ANPR not approved in Claim Plan",
                      [i.message for i in self.plan_issues(res)])

    def test_approved_wording_of_an_unapproved_module_fails_whatever_it_cites(self):
        kg = self.pipe.kg
        block = next(kg.blocks[b] for b in kg.modules["KB-ANPR-01"].building_blocks
                     if b in kg.blocks and kg.blocks[b].status == "ACTIVE")
        sentence = max(block.letter_text.split(". "), key=len).strip()
        res = self._validate(DraftSentence(sentence, module_refs=["KB-POFA-01"]))
        self.assertIn("ANPR not approved in Claim Plan",
                      [i.message for i in self.plan_issues(res)])

    def test_pofa_paragraph_with_pofa_in_plan_passes_the_plan_check(self):
        self.assertIn("KB-POFA-01", self.plan.supported_ids)
        res = self._validate(DraftSentence(
            "Keeper liability is not automatic and the operator must show it has met "
            "the statutory conditions.", module_refs=["KB-POFA-01"]))
        self.assertEqual(self.plan_issues(res), [])

    def test_feedback_names_the_family_not_the_rejected_module(self):
        res = self._validate(DraftSentence("ANPR text", module_refs=["KB-ANPR-02"]))
        for i in self.plan_issues(res):
            self.assertNotIn("KB-", i.message)

    def test_a_pack_that_is_not_the_plan_fails(self):
        self.pack.module_ids = list(self.pack.module_ids) + ["KB-ANPR-01"]
        res = self._validate(DraftSentence("Text.", module_refs=["STRUCTURAL"]))
        self.assertIn("Drafting pack does not match the locked Claim Plan",
                      [i.message for i in self.plan_issues(res)])

    def test_a_drafter_that_adds_an_argument_cannot_ship_it(self):
        rogue = "The ANPR cameras may have merged two separate visits into a single stay."

        class Rogue(ReferenceAnalysisLLM):
            def _draft(self, payload):
                out = super()._draft(payload)
                out["paragraphs"].insert(1, [{"text": rogue, "fact_refs": [],
                                              "module_refs": ["KB-ANPR-01"],
                                              "evidence_refs": [], "quote_of": None}])
                return out

        case, pipe = bay(llm_cls=Rogue)
        out = pipe.generate(case)
        rules = [r for a in case.audit if a.get("event") == "validation" for r in a["issues"]]
        self.assertIn("VAL-PLAN", rules)
        self.assertNotIn(rogue, out.letter or "")


class SharedBoilerplateIsNotAnArgument(unittest.TestCase):
    """P5 review: the VAL-PLAN wording check must not mistake common request,
    closing or framing wording for an unapproved argument."""

    # Requests and closings a letter writes whatever it argues.
    GENERIC = (
        "Please provide copies of the evidence relied upon, including any photographs "
        "and the full record of the vehicle's entry and exit.",
        "The operator is put to strict proof of the alleged contravention and of its "
        "entitlement to issue a parking charge at this location.",
        "I would be grateful if the operator could review this appeal and cancel the "
        "parking charge notice without further delay.",
        "Please confirm in writing that the charge has been cancelled and that no further "
        "action will be taken in respect of this notice.",
    )
    PLANS = (["KB-POFA-01"], ["KB-BAY-02", "KB-POFA-01"], ["KB-PAY-01", "KB-POFA-01"],
             ["KB-ANPR-01", "KB-POFA-01"], ["KB-LAND-01"], ["KB-INFRA-01"])

    @classmethod
    def setUpClass(cls):
        cls.pipe = AppealPipeline(ReferenceAnalysisLLM({}))
        kg = cls.pipe.kg
        attached = {b for m in kg.modules.values() for b in m.building_blocks}
        cls.structural = [b for b in kg.blocks.values()
                          if b.status == "ACTIVE" and b.block_id not in attached]

    def pack(self, approved):
        from pcn_appeal.models import RetrievalPack
        return RetrievalPack(
            primary_route=None, secondary_routes=[], module_ids=list(approved),
            verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
            prohibited_claims=[], code_version=None, pofa_route="POSTAL", pofa_findings=[],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES", context_chunks=[],
            lease_clauses=[], claim_plan={"status": LOCKED, "approved": list(approved),
                                          "labels": {}})

    def plan_issues(self, text, approved, refs=("STRUCTURAL",)):
        res = self.pipe.validation.validate(
            Draft("C", [[DraftSentence(text, module_refs=list(refs))]]), self.pack(approved))
        return [i.message for i in res.issues if i.rule == "VAL-PLAN"]

    def test_generic_requests_pass_under_every_plan(self):
        for approved in self.PLANS:
            for text in self.GENERIC:
                with self.subTest(plan=approved, text=text[:40]):
                    self.assertEqual(self.plan_issues(text, approved), [])

    def test_structural_blocks_pass_under_every_plan(self):
        """Introduction and closing wording belongs to no module (PP-END-001 / 002
        are appended by the orchestrator to every AI letter)."""
        self.assertTrue(self.structural)
        for approved in self.PLANS:
            for blk in self.structural:
                for sentence in blk.letter_text.split(". "):
                    with self.subTest(plan=approved, block=blk.block_id):
                        self.assertEqual(self.plan_issues(sentence, approved), [])

    def test_wording_shared_with_an_approved_module_passes(self):
        """GRACE and INFRA share the ANPR exit-timestamp wording: with INFRA in
        the plan it is INFRA's own wording, not an unapproved GRACE argument."""
        text = ("An ANPR exit timestamp records passage at the camera and does not "
                "necessarily establish that the vehicle remained parked until then.")
        self.assertEqual(self.plan_issues(text, ["KB-INFRA-01"], refs=["KB-INFRA-01"]), [])

    def test_positive_control_the_same_shared_wording_fails_without_either_module(self):
        text = ("An ANPR exit timestamp records passage at the camera and does not "
                "necessarily establish that the vehicle remained parked until then.")
        issues = self.plan_issues(text, ["KB-POFA-01"], refs=["KB-POFA-01"])
        self.assertTrue(any(m.endswith("not approved in Claim Plan") for m in issues), issues)


# ---------------------------------------------------------------- 3
class LockedPlanCannotMutate(unittest.TestCase):
    """Spec 3: no adding or removing claims, no priority or reasoning changes."""

    def setUp(self):
        self.case, self.pipe = bay()
        self.pipe.generate(self.case)
        self.plan = latest_locked(self.case)

    def test_the_plan_refuses_every_change(self):
        p = self.plan
        self.assertEqual(p.status, LOCKED)
        item = p.items[0]
        with self.assertRaises(ClaimPlanLockedError):
            p.items = ()
        with self.assertRaises(ClaimPlanLockedError):
            p.add_item(ClaimPlanItem("x", "x", "KB-ANPR-01", "ANPR", SUPPORTED, "SELECTED", "r"))
        with self.assertRaises(ClaimPlanLockedError):
            p.inputs_digest = "0"
        with self.assertRaises(ClaimPlanLockedError):
            p.status = "DRAFT"
        with self.assertRaises(ClaimPlanLockedError):
            p.confirm()
        with self.assertRaises(Exception):
            item.priority = 9                        # frozen item
        with self.assertRaises(Exception):
            item.reason = "changed"
        with self.assertRaises(TypeError):
            item.supporting_facts[0]["condition"] = "changed"   # read-only mapping
        with self.assertRaises(ClaimPlanLockedError):
            p.trust = {}
        self.assertEqual(p.plan_digest, p.content_digest())

    def test_only_a_new_version_supersedes_it(self):
        before = self.plan.as_dict()
        answer(self.case, {"payment_made": True, "payment_method": "APP"})
        self.pipe._reanalyse(self.case, NARRATIVE)
        self.pipe.generate(self.case)
        self.assertEqual(self.plan.status, SUPERSEDED)
        after = self.plan.as_dict()
        for k in ("superseded_at", "superseded_by", "status"):
            before.pop(k), after.pop(k)
        self.assertEqual(after, before)              # V1 itself never changed
        with self.assertRaises(ClaimPlanLockedError):
            self.plan.status = LOCKED                # and cannot come back


# ---------------------------------------------------------------- 4
class NewCustomerFactCreatesVersion2(unittest.TestCase):
    """Spec 4: V1 = POFA; the customer adds payment information -> V2."""

    def test_new_fact_new_version_old_one_untouched(self):
        case, pipe = scenario("Overstayed paid time")
        pipe.generate(case)
        v1 = latest_locked(case)
        self.assertEqual((v1.version, v1.supported_ids), (1, ["KB-POFA-01"]))
        v1_items = [i.as_dict() for i in v1.items]

        answer(case, {"payment_made": True, "payment_method": "APP"})
        pipe._reanalyse(case, NARRATIVE)
        out = pipe.generate(case)
        v2 = latest_locked(case)
        self.assertEqual(v2.version, 2)
        self.assertIn("KB-PAY-01", v2.supported_ids)
        self.assertEqual(out.pack.module_ids, v2.supported_ids)
        self.assertEqual((v1.status, v1.superseded_by), (SUPERSEDED, v2.claim_plan_id))
        self.assertEqual([i.as_dict() for i in v1.items], v1_items)
        self.assertEqual([p.version for p in case.claim_plans], [1, 2])

        # "What changed between runs?"
        changes = next(a for a in case.audit if a.get("event") == "claim_plan_locked"
                       and a["version"] == 2)["changes"]
        self.assertIn("payment_made", changes["facts"]["added"])
        self.assertIn("KB-PAY-01", [c["module_id"] for c in changes["changed"]])
        self.assertEqual(changes["approved_before"], ["KB-POFA-01"])


# ---------------------------------------------------------------- 5
class SameFactsSamePlan(unittest.TestCase):
    """Spec 5: determinism."""

    def test_two_cases_on_the_same_facts_get_the_same_plan(self):
        a, pa = bay(case_id="C-A")
        b, pb = bay(case_id="C-B")
        pa.generate(a)
        pb.generate(b)
        x, y = latest_locked(a), latest_locked(b)
        self.assertEqual(x.plan_digest, y.plan_digest)
        self.assertEqual(x.supported_ids, y.supported_ids)
        self.assertEqual(x.trace(), y.trace())
        self.assertEqual(x.inputs_digest, y.inputs_digest)

    def test_regenerating_an_unchanged_case_reuses_its_locked_plan(self):
        case, pipe = bay()
        first = pipe.generate(case)
        second = pipe.generate(case)
        self.assertEqual(len(case.claim_plans), 1)
        self.assertEqual(first.pack.module_ids, second.pack.module_ids)
        self.assertEqual(first.letter, second.letter)
        self.assertTrue(any(a.get("event") == "claim_plan_reused" for a in case.audit))


# ---------------------------------------------------------------- 6
class RejectedModuleNeverReachesDrafting(unittest.TestCase):
    """Spec 6 / section 5: drafting sees the locked plan, verified facts and
    approved evidence only."""

    def test_drafter_payload_holds_only_the_plan(self):
        case, pipe = bay()
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.RELEASED)
        plan = latest_locked(case)
        rejected = [i.module_id for i in plan.items if i.status != SUPPORTED]
        self.assertTrue(rejected)
        payloads = drafting_payloads(pipe)
        self.assertTrue(payloads)
        for raw in payloads:
            data = json.loads(raw)
            self.assertEqual(data["module_ids"], plan.supported_ids)
            self.assertEqual({c["module_id"] for c in data["context_chunks"]} - set(
                plan.supported_ids), set())
            cp = data["case_context"]["claim_plan"]
            self.assertEqual([c["module_id"] for c in cp["claims"]], plan.supported_ids)
            self.assertNotIn("customer_source_texts", data["case_context"])
            self.assertNotIn(NARRATIVE, raw)          # no raw narrative
            for mid in rejected:
                with self.subTest(module=mid):
                    self.assertNotIn(mid, raw)

    def test_blocked_module_is_recorded_as_blocked_and_kept_out(self):
        case, pipe = scenario("Parked in a No Parking Area", answers={"payment_made": True})
        pipe.generate(case)
        plan = latest_locked(case)
        self.assertEqual(plan.item("KB-PAY-01").status, REJECTED)
        self.assertIn("Blocked KB-PAY-01, reason: payment allegation absent", "\n".join(
            plan.trace()))
        self.assertNotIn("KB-PAY-01", plan.supported_ids)


# ---------------------------------------------------------------- 7
class PlanSurvivesDatabaseReload(unittest.TestCase):
    """Spec 7, and the lock enforced by the database itself."""

    def setUp(self):
        self.db = sqlite_store.install(self)
        row = store.new_case()
        self.case, self.pipe = bay(case_id=row.case_id)

    def test_round_trip(self):
        self.pipe.generate(self.case)
        store.save(self.case)
        loaded = store.load(self.case.case_id)
        self.assertEqual([p.as_dict() for p in loaded.claim_plans],
                         [p.as_dict() for p in self.case.claim_plans])
        self.assertTrue(latest_locked(loaded).is_locked)
        self.assertEqual(sqlite_store.count(self.db, "claim_plans"), 1)
        self.assertEqual(sqlite_store.count(self.db, "claim_plan_items"),
                         len(self.case.claim_plans[0].items))

    def test_versions_and_supersession_round_trip(self):
        self.pipe.generate(self.case)
        store.save(self.case)
        answer(self.case, {"payment_made": True, "payment_method": "APP"})
        self.pipe._reanalyse(self.case, NARRATIVE)
        self.pipe.generate(self.case)
        store.save(self.case)
        store.save(self.case)                         # idempotent
        loaded = store.load(self.case.case_id)
        self.assertEqual([(p.version, p.status) for p in loaded.claim_plans],
                         [(1, SUPERSEDED), (2, LOCKED)])
        self.assertEqual(loaded.claim_plans[0].superseded_by, loaded.claim_plans[1].claim_plan_id)
        # The reloaded case decides from its stored plan: unchanged, reused.
        self.assertEqual(latest_locked(loaded).inputs_digest,
                         latest_locked(self.case).inputs_digest)

    def test_two_versions_saved_together_for_the_first_time(self):
        """Neither version stored yet: v2 must exist before v1 names it, and v1
        must be superseded before v2 is locked (one LOCKED plan per case)."""
        self.pipe.generate(self.case)
        answer(self.case, {"payment_made": True, "payment_method": "APP"})
        self.pipe._reanalyse(self.case, NARRATIVE)
        self.pipe.generate(self.case)
        store.save(self.case)
        loaded = store.load(self.case.case_id)
        self.assertEqual([(p.version, p.status) for p in loaded.claim_plans],
                         [(1, SUPERSEDED), (2, LOCKED)])
        self.assertEqual(sqlite_store.count(self.db, "claim_plans", "status = 'LOCKED'"), 1)

    def test_database_refuses_changes_to_a_locked_plan(self):
        self.pipe.generate(self.case)
        store.save(self.case)
        plan = latest_locked(self.case)
        for sql in ("UPDATE claim_plan_items SET reason = 'changed'",
                    "UPDATE claim_plan_items SET priority = 9",
                    "UPDATE claim_plans SET plan_digest = 'x'",
                    "UPDATE claim_plans SET status = 'DRAFT'"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                self.db.execute(sql)
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.execute("INSERT INTO claim_plan_items (item_id, claim_plan_id, ordinal, "
                            "knowledge_id, module_id, status, decision, reason) "
                            "VALUES ('i', ?, 99, 'k', 'KB-ANPR-01', 'SUPPORTED', 'SELECTED', 'r')",
                            (plan.claim_plan_id,))

    def test_tampered_stored_plan_is_refused_on_load(self):
        self.pipe.generate(self.case)
        store.save(self.case)
        self.db.execute("DROP TRIGGER claim_plan_items_no_update")
        self.db.execute("UPDATE claim_plan_items SET status = 'SUPPORTED' WHERE status = 'REJECTED'")
        with self.assertRaises(ClaimPlanIntegrityError):
            store.load(self.case.case_id)


# ---------------------------------------------------------------- trust
class ClientTrust(unittest.TestCase):
    """Section 10: what produced the plan, and why each argument is in or out."""

    def setUp(self):
        self.case, self.pipe = anpr()
        self.pipe.generate(self.case)
        self.plan = latest_locked(self.case)

    def test_plan_records_what_produced_it(self):
        t = self.plan.trust
        self.assertEqual(t["code_version"], version.commit())
        self.assertEqual(t["kb_version"]["digest"], manifest.kb_digest(self.pipe.kg))
        self.assertEqual(t["kb_version"]["relations_version"], self.pipe.kg.relations.version)
        self.assertEqual(dict(t["prompt_versions"]), prompts.versions())
        self.assertIn("model_versions", t)
        self.assertEqual(t["facts_used"]["multiple_visits"]["source_kind"], "ANSWER")
        rels = [r for r in t["relationships_used"] if r["module_id"] == "KB-ANPR-01"]
        self.assertTrue(any(r["relationship"] == "SUPPORTS" and r["edge_id"] for r in rels))

    def test_why_included_and_why_excluded(self):
        self.assertIn("Selected KB-ANPR-01", self.plan.explain("KB-ANPR-01"))
        self.assertIn("multiple_visits=true", self.plan.explain("KB-ANPR-01"))
        rejected = next(i for i in self.plan.items if i.status == REJECTED)
        self.assertIn(rejected.reason, self.plan.explain(rejected.module_id))
        self.assertIn("does not consider it", self.plan.explain("KB-NOT-A-MODULE"))

    def test_blocked_trace_names_the_relationship(self):
        case, pipe = photo_notice()
        pipe.generate(case)
        trace = latest_locked(case).trace()
        self.assertTrue(any(t.startswith("Blocked KB-ANPR-01, reason: wrong evidence type")
                            for t in trace), trace)


# ---------------------------------------------------------------- authority
class OnlyThePlanDecides(unittest.TestCase):

    def test_the_plan_never_adds_an_unlicensed_ground(self):
        # No model selection, no verified legal finding, no previous locked
        # plan: nothing reaches SUPPORTED.
        case, pipe = anpr()
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        self.assertEqual(plan.supported_ids, [])
        self.assertEqual(plan.item("KB-ANPR-01").decision, NOT_SELECTED)

    def test_an_established_ground_survives_losing_the_selection(self):
        # P6.2: supported grounds are cumulative. A ground the locked plan
        # already argued does not disappear because a later analysis run
        # stopped proposing it, as long as its gate and facts still hold.
        case, pipe = anpr()
        pipe.generate(case)
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        self.assertIn("KB-ANPR-01", plan.supported_ids)
        item = plan.item("KB-ANPR-01")
        self.assertEqual(item.decision, CARRIED_FORWARD)
        self.assertIn("supported in plan v", item.reason)

    def test_drafting_refuses_a_plan_that_is_not_locked(self):
        case, pipe = anpr()
        draft = pipe.claim_authority.build(case)
        with self.assertRaises(ValueError):
            pipe.reasoning.pack_for(case, draft)

    def test_widening_keeps_the_plan_claims(self):
        case, pipe = anpr()
        pipe.generate(case)
        plan = latest_locked(case)
        self.assertEqual(pipe.reasoning.pack_for(case, plan, widen=True).module_ids,
                         plan.supported_ids)


# ---------------------------------------------------------------- admin API
class AdminRoutes(unittest.TestCase):

    def setUp(self):
        p = mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k", "DATABASE_URL": ""})
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(api.app)
        self.h = {"X-Admin-Token": "t0k"}
        self.case, self.pipe = scenario("Overstayed paid time", case_id="C-P5-API")
        out = self.pipe.generate(self.case)
        answer(self.case, {"payment_made": True, "payment_method": "APP"})
        self.pipe._reanalyse(self.case, NARRATIVE)
        out = self.pipe.generate(self.case)
        api.CASES[self.case.case_id] = {"case": self.case, "pipe": self.pipe, "flags": [],
                                        "questions": [], "output": out}
        self.addCleanup(api.CASES.pop, self.case.case_id, None)

    def test_admin_only(self):
        r = self.client.get(f"/admin/cases/{self.case.case_id}/claim-plans")
        self.assertIn(r.status_code, (401, 403))

    def test_plans_explain_and_compare(self):
        base = f"/admin/cases/{self.case.case_id}/claim-plans"
        plans = self.client.get(base, headers=self.h).json()["plans"]
        self.assertEqual([p["version"] for p in plans], [1, 2])
        self.assertTrue(plans[1]["trace"])
        why = self.client.get(f"{base}/explain", params={"module_id": "KB-PAY-01"},
                              headers=self.h).json()
        self.assertIn("Selected KB-PAY-01", why["explanation"])
        diff = self.client.get(f"{base}/compare", params={"a": 1, "b": 2}, headers=self.h).json()
        self.assertIn("payment_made", diff["facts"]["added"])
        trace = self.client.get(f"/cases/{self.case.case_id}/trace", headers=self.h).json()
        self.assertEqual(trace["claim_plan"]["version"], 2)


if __name__ == "__main__":
    unittest.main()
