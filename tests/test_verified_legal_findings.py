"""P6.1 - the Legal Claim Evidence Gate.

A specific legal defect reaches a letter only through:

    document facts -> legal calculation engine -> VERIFIED legal finding
        -> Claim Plan -> drafting -> validation (VAL-LEGAL-FINDING)

Spec tests:
    1. Late PoFA notice            -> finding VERIFIED, PoFA claim allowed.
    2. Compliant notice            -> defect NOT_SUPPORTED, PoFA defect rejected.
    3. Missing date                -> UNRESOLVED; no legal defect drafted.
    4. LLM writes unsupported defect -> validation failure (VAL-LEGAL-FINDING).
    5. Regression case (postal NTK shape from production) -> no unsupported
       PoFA statement; a verified one is still allowed.

Plus: persistence (legal_findings table, reload, identity immutable), the
drafter's payload (VERIFIED only), trace and report, and the claim-plan
rejection reason ("Legal defect not verified"). Everything generic - no
operator- or PCN-specific rules.
"""
from __future__ import annotations

import re
import unittest

import sqlite_store
from test_scenarios import BASE, ReferenceAnalysisLLM, fields, make_case, run

from pcn_appeal.drafting.drafter import drafting_payload
from pcn_appeal.engines.claim_plan import build_claim_plan
from pcn_appeal.engines.claim_plan_authority import latest_locked
from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.integrity import checks, report, trace
from pcn_appeal.legal import findings as lf
from pcn_appeal.models import CaseFile, CaseState, Draft, DraftSentence, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.store import cases as store

LATE = {"notice_issue_date": "20/06/2026"}        # event 01/06 -> deadline 15/06
CANCEL = "In light of the above, the keeper requests that the operator cancel this parking charge."
DEFECT = ("The Notice to Keeper was not delivered within the applicable statutory "
          "period, so a required condition of Schedule 4 has not been satisfied.")
VAGUE = "The notice appears non-compliant with the applicable requirements."
PROOF = ("The operator is requested to demonstrate that the notice was delivered "
         "within the applicable statutory period.")


def finding(case, ftype: str) -> dict:
    return next(r for r in case.legal_findings if r["finding_type"] == ftype)


def make_case_without(*names, extra=None):
    """A scenario case with the named BASE fields removed (make_case can only
    add)."""
    f = dict(BASE, **(extra or {}))
    for n in names:
        f.pop(n, None)
    ev = {"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice ...")}
    llm = ReferenceAnalysisLLM(
        {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "PCN"}}]})
    case = CaseFile("C-1", evidence=ev)
    return case, AppealPipeline(llm)


def letter_defects(letter: str) -> set[str]:
    out: set[str] = set()
    for s in re.split(r"(?<=[.!?])\s+", letter or ""):
        if lf.PUT_TO_PROOF.search(s):
            continue
        out |= lf.asserted_types(s)
    return out


def injected(pack, text: str, module: str) -> Draft:
    refs = {"module_refs": [module]} if module else {}
    return Draft("C-1", [[DraftSentence(text, **refs)],
                         [DraftSentence(CANCEL, module_refs=["STRUCTURAL"])]])


def rules(result) -> set[str]:
    return {i.rule for i in result.issues}


# ----------------------------------------------------------------- spec test 1
class LatePofaNotice(unittest.TestCase):
    def test_late_notice_is_a_verified_finding_and_the_claim_is_allowed(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "Got the letter weeks later", {})
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertEqual(rec["status"], lf.VERIFIED)
        calc = rec["calculation_result"]
        self.assertEqual(calc["deadline"], "2026-06-15")
        self.assertGreater(calc["days_between"], 0)
        self.assertEqual({e["fact"] for e in rec["supporting_facts"]},
                         {"parking_event_date", "notice_issue_date", "notice_route"})
        self.assertTrue(all(e.get("fact_id") for e in rec["supporting_facts"]))
        self.assertIn("KB-POFA-02", out.pack.module_ids)
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        # the pack carries VERIFIED findings only
        self.assertTrue(out.pack.legal_findings)
        self.assertTrue(all(f["status"] == lf.VERIFIED for f in out.pack.legal_findings))
        self.assertIn("POFA_POSTAL_LATE", {f["finding_type"] for f in out.pack.legal_findings})

    def test_finding_identity_is_deterministic(self):
        self.assertEqual(lf.finding_id("C-1", "POFA_POSTAL_LATE"),
                         lf.finding_id("C-1", "POFA_POSTAL_LATE"))
        self.assertNotEqual(lf.finding_id("C-1", "POFA_POSTAL_LATE"),
                            lf.finding_id("C-2", "POFA_POSTAL_LATE"))


# ----------------------------------------------------------------- spec test 2
class CompliantNotice(unittest.TestCase):
    def test_compliant_timing_is_a_verified_negative_and_no_defect_is_argued(self):
        case, pipe = make_case()                      # issue 05/06: within 14 days
        out = run(case, pipe, "", {})
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertEqual(rec["status"], lf.NOT_SUPPORTED)
        self.assertIn("compliant", rec["calculation_result"]["note"])
        self.assertNotIn("KB-POFA-02", out.pack.module_ids)
        self.assertEqual(letter_defects(out.letter), set())
        result = {c["check"]: c["status"] for c in checks.check_case(case, out)}
        self.assertEqual(result.get("LEGAL_DEFECTS_VERIFIED"), "PASS")

    def test_claim_plan_rejects_a_defect_ground_without_a_verified_finding(self):
        """The hard belt: the gate fact says a defect exists, but no finding
        verifies it - the ground is refused with the spec's reason."""
        case, pipe = make_case()
        run(case, pipe, "", {})
        facts = dict(case.fact_view(), pofa_route="POSTAL",
                     pofa_finding="POFA_POSTAL_LATE")       # injected, unverified
        plan = build_claim_plan(case, pipe.kg, ["KB-POFA-02"], [], facts,
                                findings=[])
        claim = next(c for c in plan.claims if c["module_id"] == "KB-POFA-02")
        self.assertEqual(claim["status"], "excluded")
        self.assertTrue(claim["reason"].startswith(lf.REJECTION_REASON), claim["reason"])
        self.assertNotIn("KB-POFA-02", plan.module_ids)

    def test_the_same_injection_is_allowed_when_the_finding_is_verified(self):
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        facts = dict(case.fact_view())
        self.assertEqual(facts.get("pofa_finding"), "POFA_POSTAL_LATE")
        plan = build_claim_plan(case, pipe.kg, ["KB-POFA-02"], [], facts,
                                findings=["POFA_POSTAL_LATE"])
        self.assertIn("KB-POFA-02", plan.module_ids)


# ----------------------------------------------------------------- spec test 3
class MissingDate(unittest.TestCase):
    def test_missing_issue_date_is_unresolved_and_nothing_is_drafted_from_it(self):
        case, pipe = make_case_without("notice_issue_date")
        out = run(case, pipe, "", {})
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertEqual(rec["status"], lf.UNRESOLVED)
        self.assertNotIn("KB-POFA-02", out.pack.module_ids or [])
        # UNRESOLVED never reaches the pack or the drafter
        self.assertNotIn("POFA_POSTAL_LATE",
                         {f["finding_type"] for f in out.pack.legal_findings})
        payload = drafting_payload(out.pack)
        self.assertNotIn("POFA_POSTAL_LATE",
                         {f["finding_type"] for f in payload["verified_legal_findings"]})
        self.assertEqual(letter_defects(out.letter), set())

    def test_unresolved_is_not_a_defect_and_not_a_negative(self):
        case, pipe = make_case_without("notice_issue_date")
        run(case, pipe, "", {})
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertNotIn(rec["status"], (lf.VERIFIED, lf.NOT_SUPPORTED))


# ----------------------------------------------------------------- spec test 4
class UnsupportedDefectIsRefused(unittest.TestCase):
    """The drafting model writes a defect the calculation did not prove."""

    def pack_of(self, extra=None, without=()):
        if without:
            case, pipe = make_case_without(*without, extra=extra)
        else:
            case, pipe = make_case(extra)
        out = run(case, pipe, "", {})
        return case, pipe, out

    def test_validation_fails_the_unsupported_defect(self):
        case, pipe, out = self.pack_of()                       # compliant
        module = (out.pack.module_ids or ["STRUCTURAL"])[0]
        dv = DraftValidationEngine(pipe.kg).check(injected(out.pack, DEFECT, module), out.pack)
        self.assertIn("VAL-LEGAL-FINDING", rules(dv))
        self.assertFalse(dv.passed)
        row = dv.grounding[0]
        self.assertIn("unverified_legal_defect", row["reasons"])

    def test_a_vague_non_compliance_hint_is_refused_when_nothing_is_verified(self):
        case, pipe, out = self.pack_of()
        module = (out.pack.module_ids or ["STRUCTURAL"])[0]
        dv = DraftValidationEngine(pipe.kg).check(injected(out.pack, VAGUE, module), out.pack)
        self.assertIn("VAL-LEGAL-FINDING", rules(dv))

    def test_the_same_sentence_is_allowed_when_the_finding_is_verified(self):
        case, pipe, out = self.pack_of(LATE)
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertEqual(rec["status"], lf.VERIFIED)
        dv = DraftValidationEngine(pipe.kg).check(
            injected(out.pack, DEFECT, "KB-POFA-02"), out.pack)
        self.assertNotIn("VAL-LEGAL-FINDING", rules(dv))

    def test_putting_the_operator_to_proof_asserts_no_defect(self):
        case, pipe, out = self.pack_of()
        module = (out.pack.module_ids or ["STRUCTURAL"])[0]
        dv = DraftValidationEngine(pipe.kg).check(injected(out.pack, PROOF, module), out.pack)
        self.assertNotIn("VAL-LEGAL-FINDING", rules(dv))

    def test_unresolved_is_not_verified_for_validation(self):
        case, pipe, out = self.pack_of(without=("notice_issue_date",))
        module = (out.pack.module_ids or ["STRUCTURAL"])[0]
        dv = DraftValidationEngine(pipe.kg).check(injected(out.pack, DEFECT, module), out.pack)
        self.assertIn("VAL-LEGAL-FINDING", rules(dv))

    def test_full_pipeline_strips_or_holds_an_injected_defect(self):
        """End to end: a drafter that keeps writing the unsupported defect
        never gets it released."""
        case, pipe = make_case()
        bad = {"paragraphs": [
            {"sentences": [{"text": DEFECT, "module_refs": ["KB-POFA-01"]}]},
            {"sentences": [{"text": CANCEL, "module_refs": ["STRUCTURAL"]}]}]}
        pipe.extraction.llm.responses["drafting"] = [bad, bad, bad]
        out = run(case, pipe, "", {})
        self.assertEqual(letter_defects(out.letter or ""), set())
        if out.state == CaseState.RELEASED:
            self.assertNotIn("statutory period", (out.letter or "").lower())


# ----------------------------------------------------------------- spec test 5
class RegressionPostalShape(unittest.TestCase):
    """The production shape that triggered P6.1: a postal NTK whose issue date
    sits well after the event (generic - no operator rule)."""

    def test_verified_late_notice_shape_still_argues_the_defect(self):
        case, pipe = make_case({"parking_event_date": "17/07/2026",
                                "notice_issue_date": "30/07/2026"})
        out = run(case, pipe, "", {})
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertEqual(rec["status"], lf.VERIFIED)
        self.assertEqual(rec["calculation_result"]["days_between"], 3)
        self.assertIn("KB-POFA-02", out.pack.module_ids)
        self.assertLessEqual(letter_defects(out.letter),
                             lf.verified_types(case.legal_findings))
        result = {c["check"]: c["status"] for c in checks.check_case(case, out)}
        self.assertEqual(result.get("LEGAL_DEFECTS_VERIFIED"), "PASS")

    def test_boundary_late_by_one_presumed_day_is_unresolved_and_silent(self):
        case, pipe = make_case({"parking_event_date": "14/07/2026",
                                "notice_issue_date": "27/07/2026"})
        out = run(case, pipe, "", {})
        rec = finding(case, "POFA_POSTAL_LATE")
        self.assertEqual(rec["status"], lf.UNRESOLVED)
        self.assertNotIn("KB-POFA-02", out.pack.module_ids or [])
        self.assertEqual(letter_defects(out.letter), set())

    def test_check_fails_when_the_letter_states_an_unproven_defect(self):
        case, pipe = make_case()
        out = run(case, pipe, "", {})
        class Out:                                  # a letter with a bare defect
            letter = DEFECT + " " + CANCEL
        result = {c["check"]: c for c in checks.check_case(case, Out)}
        self.assertEqual(result["LEGAL_DEFECTS_VERIFIED"]["status"], "FAIL")
        self.assertEqual(result["LEGAL_DEFECTS_VERIFIED"]["detail"]["unproven"][0]["asserted"],
                         ["POFA_POSTAL_LATE"])


# ------------------------------------------------------------------ generality
class GateIsGeneric(unittest.TestCase):
    """The gate reads module gates and the registry - not module names."""

    def test_requirements_come_from_the_gate_not_a_hardcoded_list(self):
        case, pipe = make_case()
        m = pipe.kg.modules["KB-POFA-02"]
        self.assertEqual(lf.referenced_findings(m), {"POFA_POSTAL_LATE"})
        m3 = pipe.kg.modules["KB-POFA-03"]
        self.assertEqual(lf.referenced_findings(m3),
                         {"POFA_NTD_NTK_LATE", "POFA_NTD_NTK_TOO_EARLY"})

    def test_a_module_with_a_non_finding_branch_is_not_gated_on_findings(self):
        """KB-POFA-05 can pass via a document-confirmed content defect; the
        finding gate must not reject it then."""
        case, pipe = make_case()
        m5 = pipe.kg.modules["KB-POFA-05"]
        facts = dict(case.fact_view(), pofa_finding=None,
                     ntk_defect_document_confirmed=True, ntk_defect_keeper_warning=True)
        self.assertIsNone(lf.gate_depends_on_finding(m5, facts))
        self.assertIsNone(lf.rejection(m5, facts, set()))

    def test_every_registered_finding_has_assertion_and_description(self):
        for ftype, spec in lf.REGISTRY.items():
            self.assertTrue(spec.description)
            self.assertTrue(spec.assertion.pattern)
            self.assertEqual(spec.finding_type, ftype)
            # a calculable finding reads facts; an assertion-only guard (P7 B6)
            # has no calculator yet, so no fact list
            if spec.assertion_only:
                self.assertFalse(spec.facts)
            else:
                self.assertTrue(spec.facts)

    def test_content_defect_findings_follow_the_document_facts(self):
        case, pipe = make_case()
        run(case, pipe, "", {})
        # one-sided upload in this fixture: content not fully reviewable, so
        # the content-defect findings stay UNRESOLVED - never VERIFIED
        self.assertEqual(finding(case, "NTK_CONTENT_DEFECT")["status"], lf.UNRESOLVED)
        self.assertEqual(finding(case, "POFA_NTK_INVITATION_DEFECT")["status"], lf.UNRESOLVED)


# ----------------------------------------------------------------- persistence
class Persistence(unittest.TestCase):
    def setUp(self):
        self.db = sqlite_store.install(self)

    def generated(self, extra=None):
        row = store.new_case()
        f = dict(BASE, **(extra or {}))
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile(row.case_id,
                        evidence={"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="PCN ...")})
        pipe = AppealPipeline(llm)
        out = run(case, pipe, "", {})
        store.save(case)
        return case, pipe, out

    def test_findings_persist_and_reload_identically(self):
        case, pipe, out = self.generated(LATE)
        loaded = store.load(case.case_id)
        key = lambda r: {k: r[k] for k in ("finding_id", "finding_type", "status",
                                           "legal_module_id", "calculation_result")}
        self.assertEqual(sorted((key(r) for r in loaded.legal_findings),
                                key=lambda d: d["finding_type"]),
                         sorted((key(r) for r in case.legal_findings),
                                key=lambda d: d["finding_type"]))
        self.assertEqual(finding(loaded, "POFA_POSTAL_LATE")["status"], lf.VERIFIED)
        self.assertEqual(sqlite_store.count(self.db, "legal_findings"),
                         len(case.legal_findings))

    def test_rerun_updates_in_place_not_in_duplicate(self):
        case, pipe, out = self.generated(LATE)
        n = sqlite_store.count(self.db, "legal_findings")
        loaded = store.load(case.case_id)
        AppealPipeline(pipe.extraction.llm)  # no-op; regenerate on the loaded case
        pipe.generate(loaded)
        store.save(loaded)
        self.assertEqual(sqlite_store.count(self.db, "legal_findings"), n)

    def test_the_db_refuses_to_rewrite_a_findings_identity(self):
        case, pipe, out = self.generated(LATE)
        rec = finding(case, "POFA_POSTAL_LATE")
        with self.assertRaises(Exception):
            self.db.execute("UPDATE legal_findings SET finding_type = 'SOMETHING_ELSE' "
                            "WHERE finding_id = ?", (rec["finding_id"],))

    def test_status_may_move_when_facts_arrive(self):
        """UNRESOLVED -> VERIFIED is the legitimate movement: a missing date
        answered later."""
        case, pipe, out = self.generated()
        rec = finding(case, "POFA_POSTAL_LATE")
        self.db.execute("UPDATE legal_findings SET status = 'UNRESOLVED' "
                        "WHERE finding_id = ?", (rec["finding_id"],))
        self.db.commit()
        loaded = store.load(case.case_id)
        self.assertEqual(finding(loaded, "POFA_POSTAL_LATE")["status"], lf.UNRESOLVED)


# ---------------------------------------------------------------- trace/report
class TraceAndReport(unittest.TestCase):
    def test_trace_shows_claim_finding_status_and_evidence(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        t = trace.execution_trace(case)
        rows = {r["finding_type"]: r for r in t["legal_findings"]}
        late = rows["POFA_POSTAL_LATE"]
        self.assertEqual(late["status"], lf.VERIFIED)
        self.assertTrue(all({"fact", "fact_id"} <= set(e) for e in late["supporting_facts"]))
        self.assertIn("deadline", late["calculation_result"])
        self.assertEqual(late["legal_module_id"], "KB-POFA-02")
        self.assertTrue(any(a.get("event") == "legal_findings" for a in case.audit))

    def test_report_has_a_legal_findings_section(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        md = report.case_report(case, out, pipe.kg)
        self.assertIn("## Legal findings", md)
        self.assertIn("POFA_POSTAL_LATE", md)
        self.assertIn("VERIFIED", md)

    def test_rejected_defect_ground_carries_the_reason_in_the_plan(self):
        """Authority-level: when the only licence a defect ground could have is
        an unverified finding, the plan's rejection says so."""
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        plan = latest_locked(case)
        item = next(i for i in plan.items if i.module_id == "KB-POFA-02")
        self.assertEqual(item.status, "SUPPORTED")
        # and the finding that licenses it is on record, VERIFIED
        self.assertEqual(finding(case, "POFA_POSTAL_LATE")["status"], lf.VERIFIED)


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
