"""Phase 3B.2: every hard do_not_use_when condition can actually be settled in production.

Phase 3B.1 made an UNKNOWN hard blocker keep a module out of SUPPORTED. That is only
safe if the blocker can become known: a blocker on a fact nothing writes, derives or
asks would hold the module at UNRESOLVED for ever. These tests

  * inventory every hard blocker and where each fact it stands on comes from;
  * fail on a blocker whose fact has no production source, or that can never be
    FALSE (the module could never be SUPPORTED) or never TRUE (it could never fire);
  * show that a contextual caution lives in `advisory_when`, which no status reads;
  * drive the real pipeline (FactRecovery, reasoning, the matcher, Question
    Authority) to settle each kind of blocker, instead of calling the evaluator on a
    hand-made fact view.

Nothing here injects a fact a production path could not have written.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pcn_appeal.engines import module_eligibility as ME
from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
from pcn_appeal.engines.question_authority import QuestionAuthority
from pcn_appeal.eval.eligibility import blockers as B
from pcn_appeal.eval.eligibility import matrix
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (CaseFile, EvidenceItem, Fact, FactSource, FactStatus,
                               SourceKind)
from pcn_appeal.rules.dsl import referenced_facts

from test_private_parking_v2 import make_case, run_pipeline

KG = KnowledgeGraph()
ROOT = Path(__file__).resolve().parents[1] / "pcn_appeal"

AUDITED = ["KB-ACT-02", "KB-AUTH-01", "KB-AUTH-02", "KB-BREAK-01", "KB-CON-01", "KB-CON-02",
           "KB-EV-01", "KB-PAY-01", "KB-PAY-02", "KB-PAY-03", "KB-POFA-01", "KB-POFA-02",
           "KB-POFA-03", "KB-POFA-04", "KB-POFA-05", "KB-RES-02"]
# The conditions the audit found nothing in production could ever settle.
NO_PRODUCER = ("terms_rejected_left", "fault_pre_existing_not_preventing", "relevant_land")


def _answer(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "q")))


def _status(case, pipe, module_id):
    """The eligibility status of a module on the case as the matcher reads it
    (the matcher's RELEVANT and OPEN are the eligibility status UNRESOLVED)."""
    got = KnowledgeMatcher(pipe.kg).match(case).candidates[module_id].status
    return "UNRESOLVED" if got in ("RELEVANT", "OPEN") else got


# ------------------------------------------------------------------ inventory
class Inventory(unittest.TestCase):

    def test_the_sixteen_audited_modules_are_the_hard_blockers_plus_the_two_removed(self):
        hard = {r["module_id"] for r in B.inventory(KG)}
        self.assertEqual(hard | {"KB-PAY-01", "KB-BREAK-01"}, set(AUDITED))
        self.assertEqual(len(hard), 14)

    def test_every_hard_blocker_fact_has_a_production_source(self):
        for r in B.inventory(KG):
            with self.subTest(module=r["module_id"], fact=r["fact"]):
                self.assertNotEqual(r["source"], ["NONE"], r)
                self.assertTrue(set(r["source"]) <= set(B.SOURCE_CLASSES))

    def test_no_hard_blocker_is_unreachable_never_false_or_never_true(self):
        self.assertEqual(B.unreachable(KG), [])

    def test_every_source_names_a_path_that_exists(self):
        for fact, src in B.SOURCES.items():
            for file, needle in src["paths"]:
                with self.subTest(fact=fact, path=file):
                    self.assertTrue(B.path_exists(file, needle), f"{file} has no {needle!r}")

    def test_the_registry_holds_nothing_a_hard_blocker_does_not_use(self):
        used = {r["fact"] for r in B.inventory(KG)}
        self.assertEqual(set(B.SOURCES) - used, set(), "stale source entries")

    def test_every_hard_blocker_is_classified(self):
        for r in B.inventory(KG):
            self.assertEqual(r["classification"], "HARD_RESOLVABLE", r["module_id"])

    def test_a_blocker_on_a_fact_with_no_source_is_reported(self):
        """The check has teeth: a blocker on a fact nothing produces is caught."""
        import copy
        kg = KnowledgeGraph()
        m = kg.modules["KB-PAY-01"]
        m.do_not_use_when = {"is": "terms_rejected_left"}
        self.assertTrue(any(d["defect"] == "UNREACHABLE_BLOCKER" and d["fact"] == "terms_rejected_left"
                            for d in B.unreachable(kg)))
        m.do_not_use_when = {"all": [{"is": "payment_made"}, {"is": "payment_attempt_failed"}]}
        kg.modules["KB-BREAK-01"].do_not_use_when = {"eq": ["parking_validation_status", "UNKNOWN"]}
        self.assertTrue(any(d["defect"] == "NEVER_FALSE" and d["module_id"] == "KB-BREAK-01"
                            for d in B.unreachable(kg)))

    def test_the_facts_with_no_producer_are_in_no_gate(self):
        for m in KG.active_modules():
            gate = (referenced_facts(m.use_when) | referenced_facts(m.do_not_use_when)
                    | set(m.required_facts or []))
            for fact in NO_PRODUCER:
                with self.subTest(module=m.module_id, fact=fact):
                    self.assertNotIn(fact, gate)

    def test_nothing_in_production_writes_or_asks_them(self):
        """So they cannot be gates: no writer, no derivation, no question."""
        files = [p for p in ROOT.rglob("*") if p.suffix in (".py", ".yaml")
                 and "eval" not in p.relative_to(ROOT).parts and p.name != "kb_modules.yaml"]
        for fact in ("terms_rejected_left", "fault_pre_existing", "fault_pre_existing_not_preventing"):
            hits = [str(p.relative_to(ROOT)) for p in files if fact in p.read_text(encoding="utf-8")]
            self.assertEqual(hits, [], fact)
        for p in files:
            text = p.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"""Fact\(\s*['"]F-relevant_land['"]""", text), p.name)
            self.assertIsNone(re.search(r"""['"]relevant_land['"]\s*:\s*\{?\s*['"]value""", text), p.name)


# ------------------------------------------------------------------ advisory
class AdvisoryMetadata(unittest.TestCase):

    def test_the_two_cautions_live_in_advisory_when(self):
        self.assertEqual(KG.modules["KB-PAY-01"].advisory_when, {"is": "terms_rejected_left"})
        self.assertEqual(KG.modules["KB-BREAK-01"].advisory_when, {"is": "fault_pre_existing"})
        self.assertEqual(KG.modules["KB-PAY-01"].do_not_use_when, {"always": False})
        self.assertEqual(KG.modules["KB-BREAK-01"].do_not_use_when, {"always": False})

    def test_an_advisory_fact_is_not_a_gate_or_a_requirement(self):
        for m in KG.active_modules():
            if not m.advisory_when:
                continue
            gate = (referenced_facts(m.use_when) | referenced_facts(m.do_not_use_when)
                    | set(m.required_facts or []))
            self.assertEqual(referenced_facts(m.advisory_when) & gate, set(), m.module_id)

    def test_no_status_depends_on_an_advisory_fact(self):
        """Every state of the advisory fact, on every kind of view: the status and the
        diagnostics are what they are without it."""
        from pcn_appeal.engines.module_eligibility import evaluate_module
        checked = 0
        for m in KG.active_modules():
            if not m.advisory_when:
                continue
            (fact,) = referenced_facts(m.advisory_when)
            for view, _ in matrix.cases_for(m, 200):
                base = evaluate_module(m, view)
                for state, kw in (("true", dict(value=True)), ("false", dict(value=False)),
                                  ("missing", dict()), ("uncertain", dict(unreliable={fact})),
                                  ("conflicted", dict(unreliable={fact}))):
                    v = dict(view)
                    if "value" in kw:
                        v[fact] = kw["value"]
                    elif state != "missing":
                        v[fact] = "stale"
                    out = evaluate_module(m, v, unreliable=kw.get("unreliable", ()))
                    checked += 1
                    with self.subTest(module=m.module_id, state=state):
                        self.assertEqual(out.status, base.status)
                        self.assertEqual(out.missing_conditions, base.missing_conditions)
                        self.assertEqual(out.unverified_blockers, base.unverified_blockers)
                        self.assertEqual(out.blocking_conditions, base.blocking_conditions)
                        self.assertNotIn(fact, out.fact_keys_used)
                        self.assertEqual(ME.gate_holds(m, v), base.status == ME.SUPPORTED)
        self.assertGreater(checked, 40)

    def test_the_matcher_and_the_gate_ignore_it_too(self):
        view = {"driver_status": "UNIDENTIFIED", "payment_made": True}
        for adv in ({}, {"terms_rejected_left": True}, {"terms_rejected_left": False}):
            c = KnowledgeMatcher(KG).match(CaseFile("adv"), {**view, **adv}).candidates["KB-PAY-01"]
            self.assertEqual(c.status, "SUPPORTED", adv)
            self.assertNotIn("terms_rejected_left", c.missing)
            self.assertEqual(c.unverified_blockers, [])

    def test_it_is_part_of_the_kb_fingerprint(self):
        from pcn_appeal.manifest import kb_digest
        kg = KnowledgeGraph()
        before = kb_digest(kg)
        kg._manifest_digest = None
        kg.modules["KB-PAY-01"].advisory_when = {"is": "something_else"}
        self.assertNotEqual(kb_digest(kg), before)

    def test_advisory_facts_are_phase_4_inputs_not_questions_today(self):
        """No question exists for them and Question Authority does not ask: a
        dependency recorded for Phase 4, not behaviour added here."""
        questions = (ROOT / "data" / "questions.yaml").read_text(encoding="utf-8")
        for fact in ("terms_rejected_left", "fault_pre_existing"):
            self.assertNotIn(f"{fact}:", questions)
        case, pipe = make_case({"entry_time": "10:00", "exit_time": "10:03",
                                "alleged_breach": "No ticket displayed"})
        run_pipeline(case, pipe, "I was there a few minutes", scenario="advisory")
        qa = QuestionAuthority(pipe.kg)
        for fact in ("terms_rejected_left", "fault_pre_existing"):
            rv = qa.review(case, [{"fact": fact, "target_fact": fact, "text": fact, "type": "bool"}])
            self.assertNotIn(fact, [s.get("target_fact") for s in rv.shown])


# ------------------------------------------------------------------ the two removed blockers
class PaymentAndBreakdown(unittest.TestCase):
    """What used to wait on a flag nothing produced."""

    def test_a_payment_alone_now_supports_the_payment_ground(self):
        view = {"driver_status": "UNIDENTIFIED", "payment_made": True}
        self.assertEqual(ME.evaluate_module_id(KG, "KB-PAY-01", view).status, "SUPPORTED")

    def test_through_the_pipeline_a_confirmed_payment_selects_it(self):
        case, pipe = make_case({"alleged_breach": "No valid payment for vehicle"})
        r = run_pipeline(case, pipe, "I paid at the machine", answers={"payment_made": True},
                         scenario="3b2-pay01")
        self.assertEqual(_status(case, pipe, "KB-PAY-01"), "SUPPORTED", r.dump())
        self.assertIn("KB-PAY-01", r.retrieved_modules, r.dump())

    def test_breakdown_that_prevented_departure_is_supported_without_the_old_flag(self):
        view = {"driver_status": "UNIDENTIFIED", "vehicle_immobilised": True,
                "immobilisation_prevented_departure": True}
        self.assertEqual(ME.evaluate_module_id(KG, "KB-BREAK-01", view).status, "SUPPORTED")

    def test_breakdown_that_did_not_prevent_departure_is_still_rejected(self):
        """The 'not preventing' half of the old flag was already use_when's negation."""
        view = {"driver_status": "UNIDENTIFIED", "vehicle_immobilised": True,
                "immobilisation_prevented_departure": False}
        self.assertEqual(ME.evaluate_module_id(KG, "KB-BREAK-01", view).status, "REJECTED")

    def test_a_pre_existing_fault_is_an_advisory_not_a_block(self):
        view = {"driver_status": "UNIDENTIFIED", "vehicle_immobilised": True,
                "immobilisation_prevented_departure": True, "fault_pre_existing": True}
        self.assertEqual(ME.evaluate_module_id(KG, "KB-BREAK-01", view).status, "SUPPORTED")


# ------------------------------------------------------------------ PoFA
class PofaBlockers(unittest.TestCase):

    def test_pofa_01_is_settled_by_the_derived_route(self):
        case, pipe = make_case({"alleged_breach": "No ticket displayed"})
        run_pipeline(case, pipe, "drove through then left", scenario="3b2-pofa01")
        self.assertEqual(case.get("pofa_route"), "POSTAL", "written by the recovery engine")
        self.assertIsNone(case.get("relevant_land"), "nothing writes relevant_land")
        self.assertEqual(_status(case, pipe, "KB-POFA-01"), "SUPPORTED")

    def test_pofa_01_without_a_derived_route_is_unresolved_not_supported(self):
        view = {"driver_status": "UNIDENTIFIED", "jurisdiction": "ENGLAND_WALES"}
        self.assertEqual(ME.evaluate_module_id(KG, "KB-POFA-01", view).status, "UNRESOLVED")

    def test_pofa_01_when_schedule_4_does_not_apply_is_blocked(self):
        view = {"driver_status": "UNIDENTIFIED", "jurisdiction": "ENGLAND_WALES",
                "pofa_route": "NOT_APPLICABLE"}
        out = ME.evaluate_module_id(KG, "KB-POFA-01", view)
        self.assertEqual(out.status, "BLOCKED")
        self.assertTrue(any("pofa_route" in c for c in out.blocking_conditions))

    def test_pofa_01_other_not_applicable_causes_are_already_in_use_when(self):
        """A non-England/Wales site or an identified driver is rejected by use_when,
        so the blocker adds nothing there and the relevant-land cause is the only
        one it carries."""
        for view in ({"driver_status": "UNIDENTIFIED", "jurisdiction": "SCOTLAND"},
                     {"driver_status": "FORMALLY_IDENTIFIED", "jurisdiction": "ENGLAND_WALES"}):
            self.assertEqual(ME.evaluate_module_id(KG, "KB-POFA-01", view).status, "REJECTED", view)

    def test_pofa_04_does_not_wait_on_relevant_land(self):
        m = KG.modules["KB-POFA-04"]
        self.assertNotIn("relevant_land", referenced_facts(m.do_not_use_when))
        base = {"driver_status": "UNIDENTIFIED", "notice_route": "POSTAL", "pofa_route": "POSTAL",
                "ntk_defect_document_confirmed": True, "ntk_defect_keeper_warning": True,
                "notice_sides_complete": True}
        self.assertEqual(ME.evaluate_module_id(KG, "KB-POFA-04", base).status, "SUPPORTED")

    def test_pofa_04_is_settled_by_what_the_notice_shows(self):
        """Front-only upload through the pipeline: notice_sides_complete is derived from
        the pages and the invitation wording comes from the extraction of the notice."""
        def run(flag, evidence=None):
            extra = {"alleged_breach": "Overstayed paid time", "ntk_defect_document_confirmed": True,
                     "ntk_defect_statutory_invitation": True}
            if flag is not None:
                extra["ntk_invites_pass_to_driver"] = flag
            case, pipe = make_case(extra, evidence={"E2": EvidenceItem(
                "E2", "NTK", "ntk.jpg", images=[b"ONE"], text="Notice to Keeper")},
                doc_types={"E2": "NTK"})
            run_pipeline(case, pipe, "x", scenario="3b2-pofa04")
            return case, pipe
        case, pipe = run(False)
        self.assertIs(case.get("notice_sides_complete"), False)
        self.assertIs(case.get("ntk_invites_pass_to_driver"), False)
        self.assertEqual(_status(case, pipe, "KB-POFA-04"), "SUPPORTED")
        case, pipe = run(True)
        self.assertEqual(_status(case, pipe, "KB-POFA-04"), "BLOCKED")
        case, pipe = run(None)            # front only, wording not seen: open, not blocked
        self.assertIsNone(case.get("ntk_invites_pass_to_driver"))
        self.assertEqual(_status(case, pipe, "KB-POFA-04"), "UNRESOLVED")


# ------------------------------------------------------------------ EV-01
class Ev01(unittest.TestCase):

    def _run(self, evidence=None, **fields):
        case, pipe = make_case({"alleged_breach": "Overstayed paid time", **fields},
                               evidence=evidence, doc_types={k: v.kind for k, v in (evidence or {}).items()})
        run_pipeline(case, pipe, "the camera shows I left", scenario="3b2-ev01")
        _answer(case, "independent_evidence_contradicts", True)
        return case, pipe

    def test_without_a_receipt_the_exclusion_is_settled_false(self):
        case, pipe = self._run({"E2": EvidenceItem("E2", "DASHCAM", "cam.mp4", uploaded=True,
                                                   text="Left site at 10:05")})
        self.assertIs(case.get("shopping_purchase_confirmed"), False)
        self.assertIsNone(case.get("parking_validation_status"))
        self.assertEqual(_status(case, pipe, "KB-EV-01"), "SUPPORTED")

    def test_a_receipt_that_leaves_validation_unknown_blocks_it(self):
        case, pipe = self._run({"E2": EvidenceItem("E2", "RECEIPT", "r.pdf", uploaded=True,
                                                   text="Total £10")})
        self.assertIs(case.get("shopping_purchase_confirmed"), True)
        self.assertEqual(case.get("parking_validation_status"), "UNKNOWN")
        self.assertEqual(_status(case, pipe, "KB-EV-01"), "BLOCKED")

    def test_before_the_evidence_set_is_assessed_it_is_unresolved(self):
        """No fixture-made value: with nothing derived the exclusion is open."""
        view = {"independent_evidence_contradicts": True}
        out = ME.evaluate_module_id(KG, "KB-EV-01", view,
                                    evidence_state={"kinds": ["DASHCAM"]})
        self.assertEqual(out.status, "UNRESOLVED")
        self.assertTrue(out.unverified_blockers)

    def test_validation_itself_is_never_invented(self):
        """Only the UNKNOWN sentinel is ever written; no path writes a validated state."""
        text = (ROOT / "engines" / "recovery.py").read_text(encoding="utf-8")
        writes = re.findall(r'"parking_validation_status",\s*([^,]+),', text)
        self.assertEqual({w.strip() for w in writes}, {'"UNKNOWN"'})

    def test_a_receipt_that_is_removed_stops_blocking(self):
        case, pipe = self._run({"E2": EvidenceItem("E2", "RECEIPT", "r.pdf", uploaded=True,
                                                   text="Total £10")})
        self.assertIs(case.get("shopping_purchase_confirmed"), True)
        del case.evidence["E2"]
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        FactRecoveryEngine(pipe.kg).recover(case)
        self.assertIs(case.get("shopping_purchase_confirmed"), False)

    def test_a_value_the_customer_gave_is_not_overwritten(self):
        case, pipe = make_case({"alleged_breach": "Overstayed paid time"})
        _answer(case, "shopping_purchase_confirmed", True)
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        FactRecoveryEngine(pipe.kg).recover(case)
        self.assertIs(case.get("shopping_purchase_confirmed"), True)


# ------------------------------------------------------------------ lease
class LeaseBlockers(unittest.TestCase):
    LEASE = ("3.2 The Tenant shall have the right to park one private motor vehicle in the "
             "parking space numbered 14 shown on the plan.\n4. Rent is payable monthly.")

    def _facts(self, evidence):
        case, pipe = make_case({"alleged_breach": "No valid permit displayed"}, evidence=evidence,
                               doc_types={k: v.kind for k, v in evidence.items()})
        run_pipeline(case, pipe, "resident, my bay", scenario="3b2-lease")
        return case

    def test_a_lease_with_a_parking_clause_settles_the_flags_true_and_false(self):
        case = self._facts({"E3": EvidenceItem("E3", "LEASE", "lease.pdf", text=self.LEASE)})
        self.assertIs(case.get("lease_parking_clause_found"), True)
        self.assertIs(case.get("lease_has_regulations_clause"), False)

    def test_with_no_lease_the_exclusion_is_settled_without_claiming_anything_about_a_lease(self):
        """Phase 3C: lease_evidence_provided is False; the clause facts are unknown, not False."""
        case = self._facts({})
        self.assertIs(case.get("lease_evidence_provided"), False)
        self.assertNotIn("lease_parking_clause_found", case.facts)
        self.assertNotIn("lease_has_regulations_clause", case.facts)


# ------------------------------------------------------------------ customer-question facts
class AskedBlockers(unittest.TestCase):
    """payment_made, payment_method, permitted_period_ended: asked, answered, settled."""

    def _case(self, narrative="I was there a few minutes", **fields):
        case, pipe = make_case({"entry_time": "10:00", "exit_time": "10:03",
                                "alleged_breach": "No ticket displayed", **fields})
        run_pipeline(case, pipe, narrative, scenario="3b2-asked")
        return case, pipe

    def _approved(self, case, pipe, fact):
        rv = QuestionAuthority(pipe.kg).review(case, [{
            "fact": fact, "target_fact": fact, "text": fact, "type": "bool",
            "options": ["APP", "MACHINE", "PHONE", "WEBSITE", "OTHER"]}])
        return [row.get("decision") for row in rv.rows]

    def test_question_authority_approves_each_of_them(self):
        case, pipe = self._case()
        for fact in ("payment_made", "permitted_period_ended"):
            with self.subTest(fact=fact):
                self.assertEqual(self._approved(case, pipe, fact), ["APPROVED"])
        _answer(case, "payment_attempt_failed", True)
        self.assertEqual(self._approved(case, pipe, "payment_method"), ["APPROVED"])

    def test_each_has_a_question_definition(self):
        questions = (ROOT / "data" / "questions.yaml").read_text(encoding="utf-8")
        for fact in ("payment_made", "payment_method", "permitted_period_ended"):
            self.assertRegex(questions, rf"(?m)^\s+{fact}:")

    def test_the_answer_settles_the_blocker_either_way(self):
        def run(**answers):
            case, pipe = make_case({"entry_time": "10:00", "exit_time": "10:03",
                                    "alleged_breach": "Overstayed paid time"})
            run_pipeline(case, pipe, "I was there", scenario="3b2-settle")
            _answer(case, "dropoff_activity", True)
            for k, v in answers.items():
                _answer(case, k, v)
            return case, pipe
        case, pipe = run(permitted_period_ended=False)
        self.assertEqual(_status(case, pipe, "KB-ACT-02"), "UNRESOLVED")      # payment_made not known
        case, pipe = run(permitted_period_ended=False, payment_made=False)
        self.assertEqual(_status(case, pipe, "KB-ACT-02"), "SUPPORTED")
        case, pipe = run(permitted_period_ended=False, payment_made=True)
        self.assertEqual(_status(case, pipe, "KB-ACT-02"), "BLOCKED")

    def test_the_payment_method_answer_settles_pay_02_and_03(self):
        def run(method):
            case, pipe = make_case({"alleged_breach": "No valid payment for vehicle"})
            run_pipeline(case, pipe, "the machine would not take my card", scenario="3b2-method")
            _answer(case, "payment_attempt_failed", True)
            if method:
                _answer(case, "payment_method", method)
            return case, pipe
        for method, p2, p3 in (("MACHINE", "SUPPORTED", "BLOCKED"), ("APP", "BLOCKED", "SUPPORTED"),
                               (None, "UNRESOLVED", "UNRESOLVED")):
            case, pipe = run(method)
            with self.subTest(method=method):
                self.assertEqual(_status(case, pipe, "KB-PAY-02"), p2)
                self.assertEqual(_status(case, pipe, "KB-PAY-03"), p3)


# ------------------------------------------------------------------ the evaluator was not weakened
class EvaluatorUntouched(unittest.TestCase):

    def test_the_safety_rule_still_holds_for_a_blocker_that_is_really_unknown(self):
        for mid, view in (("KB-ACT-02", {"dropoff_activity": True, "permitted_period_ended": False}),
                          ("KB-AUTH-02", {"permit_held": True})):
            out = ME.evaluate_module_id(KG, mid, {"driver_status": "UNIDENTIFIED", **view})
            with self.subTest(module=mid):
                self.assertEqual(out.status, "UNRESOLVED")
                self.assertTrue(out.unverified_blockers)

    def test_decide_is_the_frozen_table(self):
        T, F, U = True, False, None
        self.assertEqual(ME.decide(T, T), "BLOCKED")
        self.assertEqual(ME.decide(T, U), "UNRESOLVED")
        self.assertEqual(ME.decide(T, F), "SUPPORTED")
        self.assertEqual(ME.decide(F, U), "REJECTED")
        self.assertEqual(ME.decide(U, F), "UNRESOLVED")

    def test_no_module_or_fact_is_named_in_the_engine_or_evaluator(self):
        for rel in ("engines/module_eligibility.py", "rules/dsl.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            for token in ("KB-PAY", "KB-BREAK", "KB-POFA", "KB-EV", "terms_rejected", "relevant_land",
                          "shopping_purchase", "parking_validation", "advisory_when"):
                self.assertNotIn(token, text, f"{rel} mentions {token}")

    def test_the_derivation_is_generic(self):
        """The receipt derivation names facts, not the module that reads them."""
        text = (ROOT / "engines" / "recovery.py").read_text(encoding="utf-8")
        self.assertNotIn("KB-EV", text)

    def test_the_engines_do_not_read_advisory_when(self):
        for p in (ROOT / "engines").glob("*.py"):
            self.assertNotIn("advisory_when", p.read_text(encoding="utf-8"), p.name)
        self.assertNotIn("advisory_when", (ROOT / "rules" / "dsl.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
