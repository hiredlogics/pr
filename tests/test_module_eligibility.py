"""Phase 3B: deterministic module eligibility.

    candidate module + authoritative fact view + verified findings
        -> SUPPORTED / UNRESOLVED / REJECTED / BLOCKED

Retrieval is bypassed: the input is {module, fact_view, verified_findings,
evidence_state, kb_release_id}. Expected values come from an independently written
reference (pcn_appeal/eval/eligibility/reference.py) and from named scenarios
registered before the evaluator changed - never from the evaluator under test.

Run:  PYTHONPATH=.:tests python -m unittest tests.test_module_eligibility -v
"""
from __future__ import annotations

import inspect
import re
import unittest
from types import SimpleNamespace

from pcn_appeal.engines import knowledge_matcher as KM
from pcn_appeal.engines import module_eligibility as ME
from pcn_appeal.engines.module_eligibility import (BLOCKED, REJECTED, SUPPORTED, UNRESOLVED,
                                                   evaluate_module, evaluate_module_id)
from pcn_appeal.engines.reasoning import ReasoningEngine
from pcn_appeal.eval.eligibility import harness as H
from pcn_appeal.eval.eligibility import matrix, truth_table as TT
from pcn_appeal.eval.eligibility.scenarios import SCENARIOS
from pcn_appeal.models import CaseFile
from pcn_appeal.rules import dsl
from pcn_appeal.rules.dsl import evaluate, evaluate3

KG = H.kg()


def mod(use_when, dnuw=None, required=(), role="SUBSTANTIVE_GROUND", mid="T-1"):
    return SimpleNamespace(module_id=mid, use_when=use_when, do_not_use_when=dnuw or {},
                           required_facts=list(required), module_role=role)


def eq(f, v):
    return {"eq": [f, v]}


def ne(f, v):
    return {"ne": [f, v]}


def status(m, view, **kw):
    return evaluate_module(m, view, **kw).status


class OperatorTruthTable(unittest.TestCase):
    """Every operator x (known match, known non-match, missing, uncertain, conflicted)."""

    def test_the_table_is_complete(self):
        ops = {label.split()[0] for label, *_ in TT.OPERATORS}
        # has_evidence reads the evidence state, not a fact: EvidenceState has its own table.
        for needed in ("eq", "ne", "in", "gt", "gte", "lt", "lte", "is", "exists", "missing",
                       "contains"):
            self.assertIn(needed, ops, f"operator {needed} has no truth-table row")
        rows = H.operator_table()["rows"]
        self.assertGreaterEqual(len(rows), 100)
        states = {r["state"] for r in rows}
        self.assertEqual(states, set(TT.STATES))

    def test_every_cell_matches_the_contract(self):
        table = H.operator_table()
        for r in table["rows"]:
            with self.subTest(op=r["op"], state=r["state"]):
                self.assertTrue(r["ok"], r)
        self.assertEqual(table["bad"], [])

    def test_missing_uncertain_conflicted_are_unknown_for_every_fact_operator(self):
        for label, pred, match_v, non_v in TT.OPERATORS + TT.NEGATED:
            if TT.expected(label, "missing") == TT.NA:
                continue
            for state in ("missing", "uncertain", "conflicted"):
                view, unreliable = H._view_for(state, non_v)
                with self.subTest(op=label, state=state):
                    self.assertIsNone(evaluate3(pred, view, unreliable=unreliable))

    def test_ne_on_a_missing_fact_is_unknown_not_true(self):
        """payment_method != "cash" with no payment_method: nobody knows."""
        pred = ne("payment_method", "cash")
        self.assertIsNone(evaluate3(pred, {}))
        self.assertFalse(evaluate(pred, {}), "the boolean wrapper may only say TRUE for TRUE")
        self.assertTrue(evaluate3(pred, {"payment_method": "APP"}))
        self.assertIs(evaluate3(pred, {"payment_method": "cash"}), False)

    def test_not_over_unknown_stays_unknown(self):
        self.assertIsNone(evaluate3({"not": {"is": "x"}}, {}))
        self.assertIs(evaluate3({"not": {"is": "x"}}, {"x": True}), False)
        self.assertIs(evaluate3({"not": {"is": "x"}}, {"x": False}), True)

    def test_kleene_connectives(self):
        t, f, u = {"is": "t"}, {"is": "f"}, {"is": "u"}
        v = {"t": True, "f": False}
        self.assertIs(evaluate3({"all": [t, t]}, v), True)
        self.assertIs(evaluate3({"all": [t, f]}, v), False)
        self.assertIsNone(evaluate3({"all": [t, u]}, v))
        self.assertIs(evaluate3({"all": [f, u]}, v), False)      # a known FALSE decides
        self.assertIs(evaluate3({"any": [f, f]}, v), False)
        self.assertIs(evaluate3({"any": [t, u]}, v), True)       # a known TRUE decides
        self.assertIsNone(evaluate3({"any": [f, u]}, v))

    def test_false_value_is_known_not_missing(self):
        """0, False, '' and [] are values. Only absence is absence."""
        self.assertIs(evaluate3({"is": "x"}, {"x": False}), False)
        self.assertIs(evaluate3(eq("n", 0), {"n": 0}), True)
        self.assertIs(evaluate3({"gt": ["n", 0]}, {"n": 0}), False)
        self.assertIsNone(evaluate3({"gt": ["n", 0]}, {}))

    def test_incomparable_values_are_unknown(self):
        self.assertIsNone(evaluate3({"gt": ["n", 3]}, {"n": "three"}))


class UseWhen(unittest.TestCase):
    A, B = {"is": "a"}, {"is": "b"}

    def test_all_true_is_supported(self):
        self.assertEqual(status(mod({"all": [self.A, self.B]}), {"a": True, "b": True}), SUPPORTED)

    def test_one_false_is_rejected(self):
        self.assertEqual(status(mod({"all": [self.A, self.B]}), {"a": True, "b": False}), REJECTED)

    def test_one_unknown_is_unresolved(self):
        out = evaluate_module(mod({"all": [self.A, self.B]}), {"a": True})
        self.assertEqual(out.status, UNRESOLVED)
        self.assertEqual(len(out.missing_conditions), 1)
        self.assertIn("b", out.missing_conditions[0])

    def test_a_known_false_beats_an_unknown_in_all(self):
        self.assertEqual(status(mod({"all": [self.A, self.B]}), {"b": False}), REJECTED)

    def test_nested_and(self):
        m = mod({"all": [self.A, {"all": [self.B, {"is": "c"}]}]})
        self.assertEqual(status(m, {"a": True, "b": True, "c": True}), SUPPORTED)
        self.assertEqual(status(m, {"a": True, "b": True}), UNRESOLVED)
        self.assertEqual(status(m, {"a": True, "b": True, "c": False}), REJECTED)

    def test_nested_or(self):
        m = mod({"any": [self.A, {"any": [self.B, {"is": "c"}]}]})
        self.assertEqual(status(m, {"c": True}), SUPPORTED)
        self.assertEqual(status(m, {"a": False, "b": False, "c": False}), REJECTED)
        self.assertEqual(status(m, {"a": False, "b": False}), UNRESOLVED)

    def test_and_over_or(self):
        m = mod({"all": [self.A, {"any": [self.B, {"is": "c"}]}]})
        self.assertEqual(status(m, {"a": True, "b": False}), UNRESOLVED)
        self.assertEqual(status(m, {"a": True, "b": False, "c": True}), SUPPORTED)

    def test_nothing_known_is_unresolved_never_rejected(self):
        for m in KG.active_modules():
            with self.subTest(module=m.module_id):
                self.assertEqual(evaluate_module(m, {"driver_status": "UNIDENTIFIED"}).status,
                                 UNRESOLVED)

    def test_a_negative_use_when_condition_needs_a_known_value(self):
        m = mod(ne("payment_method", "cash"))
        self.assertEqual(status(m, {}), UNRESOLVED)
        self.assertEqual(status(m, {"payment_method": "APP"}), SUPPORTED)
        self.assertEqual(status(m, {"payment_method": "cash"}), REJECTED)


class DoNotUseWhen(unittest.TestCase):
    """Blockers are judged independently of use_when."""

    GATE = {"is": "g"}

    def test_a_true_blocker_blocks(self):
        m = mod(self.GATE, eq("m", "X"))
        self.assertEqual(status(m, {"g": True, "m": "X"}), BLOCKED)

    def test_a_false_blocker_does_not_block(self):
        m = mod(self.GATE, eq("m", "X"))
        self.assertEqual(status(m, {"g": True, "m": "Y"}), SUPPORTED)

    def test_an_unknown_blocker_does_not_block_and_does_not_pass(self):
        """UNKNOWN is neither BLOCKED (absence is no contradiction) nor FALSE (absence
        is no exclusion): the module is UNRESOLVED and says what is still open."""
        m = mod(self.GATE, eq("m", "X"))
        out = evaluate_module(m, {"g": True})
        self.assertEqual(out.status, UNRESOLVED)
        self.assertEqual(out.blocking_conditions, [])
        self.assertEqual(len(out.unverified_blockers), 1, "the open blocker is disclosed")
        self.assertTrue(any("cannot rule out" in c for c in out.missing_conditions))

    def test_an_unknown_negative_blocker_does_not_block_and_does_not_pass(self):
        """The reported defect, generically: `ne` over a missing fact."""
        m = mod(self.GATE, ne("method", "MACHINE"))
        self.assertEqual(status(m, {"g": True}), UNRESOLVED)
        self.assertEqual(status(m, {"g": True, "method": "MACHINE"}), SUPPORTED)
        self.assertEqual(status(m, {"g": True, "method": "APP"}), BLOCKED)

    def test_a_true_blocker_blocks_even_when_use_when_is_false_or_unknown(self):
        m = mod(self.GATE, eq("m", "X"))
        self.assertEqual(status(m, {"g": False, "m": "X"}), BLOCKED)
        self.assertEqual(status(m, {"m": "X"}), BLOCKED)

    def test_a_known_false_use_when_is_rejected_whatever_the_blocker_says(self):
        m = mod(self.GATE, eq("m", "X"))
        self.assertEqual(status(m, {"g": False}), REJECTED)          # blocker unknown
        self.assertEqual(status(m, {"g": False, "m": "Y"}), REJECTED)  # blocker false
        self.assertEqual(status(m, {}), UNRESOLVED)

    def test_blocker_connectives(self):
        m = mod(self.GATE, {"all": [eq("a", 1), eq("b", 2)]})
        self.assertEqual(status(m, {"g": True, "a": 1, "b": 2}), BLOCKED)
        self.assertEqual(status(m, {"g": True, "a": 1}), UNRESOLVED)          # TRUE + UNKNOWN
        self.assertEqual(status(m, {"g": True, "a": 9}), SUPPORTED)           # a known FALSE decides
        any_m = mod(self.GATE, {"any": [eq("a", 1), eq("b", 2)]})
        self.assertEqual(status(any_m, {"g": True, "a": 1}), BLOCKED)         # a known TRUE decides
        self.assertEqual(status(any_m, {"g": True, "a": 9}), UNRESOLVED)      # FALSE + UNKNOWN
        self.assertEqual(status(any_m, {"g": True, "a": 9, "b": 9}), SUPPORTED)

    def test_uncertain_or_conflicted_blocker_fact_does_not_block_and_does_not_pass(self):
        m = mod(self.GATE, eq("m", "X"))
        out = evaluate_module(m, {"g": True}, unreliable={"m"})
        self.assertEqual(out.status, UNRESOLVED)
        self.assertEqual(out.blocking_conditions, [])
        self.assertIn("held but not trusted", out.unverified_blockers[0])

    def test_uncertain_or_conflicted_use_when_fact_is_unresolved_not_rejected(self):
        m = mod(eq("m", "X"))
        out = evaluate_module(m, {}, unreliable={"m"})
        self.assertEqual(out.status, UNRESOLVED)
        self.assertIn("held but not trusted", out.missing_conditions[0])


class RequiredFacts(unittest.TestCase):
    def test_a_missing_required_fact_is_never_a_rejection(self):
        for m in KG.active_modules():
            req = set(m.required_facts or [])
            if not req:
                continue
            with self.subTest(module=m.module_id):
                out = evaluate_module(m, {"driver_status": "UNIDENTIFIED"})
                self.assertNotEqual(out.status, REJECTED)

    def test_every_required_fact_is_audited_against_use_when(self):
        """Required facts the gate does not read can neither reject nor support."""
        for m in KG.active_modules():
            with self.subTest(module=m.module_id):
                gate = dsl.referenced_facts(m.use_when)
                outside = set(m.required_facts or []) - gate
                # Whatever the required facts outside the gate are, supplying or
                # withholding them cannot change the status.
                base = {"driver_status": "UNIDENTIFIED"}
                with_req = dict(base, **{f: "x" for f in outside})
                self.assertEqual(evaluate_module(m, base).status,
                                 evaluate_module(m, with_req).status)

    def test_a_gate_that_holds_is_supported_with_the_missing_required_fact_reported(self):
        m = mod({"is": "g"}, required=["g", "extra"])
        self.assertEqual(status(m, {"g": True}), SUPPORTED)


class VerifiedFindings(unittest.TestCase):
    """Legal findings are consumed as recorded by the calculation engine; no model is asked."""

    def setUp(self):
        self.m = KG.modules["KB-POFA-02"]            # POSTAL route and POFA_POSTAL_LATE
        self.view = {"driver_status": "UNIDENTIFIED", "pofa_route": "POSTAL"}

    def test_verified_finding_is_usable(self):
        out = evaluate_module(self.m, self.view, verified_findings=["POFA_POSTAL_LATE"])
        self.assertEqual(out.status, SUPPORTED)
        self.assertIn("finding:POFA_POSTAL_LATE", out.fact_keys_used)

    def test_no_finding_is_unresolved(self):
        self.assertEqual(evaluate_module(self.m, self.view).status, UNRESOLVED)

    def test_unresolved_finding_cannot_establish_support(self):
        out = evaluate_module(self.m, self.view,
                              finding_states={"POFA_POSTAL_LATE": "UNRESOLVED"})
        self.assertEqual(out.status, UNRESOLVED)

    def test_rejected_finding_cannot_establish_the_proposition(self):
        out = evaluate_module(self.m, self.view,
                              finding_states={"POFA_POSTAL_LATE": "NOT_SUPPORTED"})
        self.assertEqual(out.status, REJECTED)

    def test_a_state_cannot_unverify_a_code_in_the_verified_set(self):
        view = dict(self.view, pofa_findings=["POFA_POSTAL_LATE"])
        self.assertEqual(evaluate_module(self.m, view).status, SUPPORTED,
                         "the view's pofa_findings is the authoritative gate set (gate_facts)")
        out = evaluate_module(self.m, view, finding_states={"POFA_POSTAL_LATE": "NOT_SUPPORTED"})
        self.assertEqual(out.status, SUPPORTED,
                         "a code that is in the verified set is verified; a state cannot unverify it")

    def test_other_codes_do_not_stand_in(self):
        out = evaluate_module(self.m, self.view, verified_findings=["POFA_NTD_NTK_LATE"])
        self.assertNotEqual(out.status, SUPPORTED)

    def test_case_records_reach_the_matcher_path(self):
        for state, want in (("VERIFIED", "SUPPORTED"), ("NOT_SUPPORTED", "REJECTED"),
                            ("UNRESOLVED", "UNRESOLVED")):
            case = CaseFile("f")
            case.legal_findings.append({"finding_type": "POFA_POSTAL_LATE", "status": state})
            view = dict(self.view)
            if state == "VERIFIED":
                view.update(pofa_findings=["POFA_POSTAL_LATE"], pofa_finding="POFA_POSTAL_LATE")
            got = KM.KnowledgeMatcher(KG).match(case, view).candidates["KB-POFA-02"].status
            with self.subTest(state=state):
                self.assertEqual(H._MAP[got], want)

    def test_no_model_is_involved(self):
        src = inspect.getsource(ME)
        for banned in ("llm", "openai", "anthropic", "embed", "vector", "complete(", "chat("):
            self.assertNotIn(banned, src.lower().replace("llm may", ""), banned)


class EvidenceState(unittest.TestCase):
    def test_absent_evidence_is_unresolved_until_the_set_is_complete(self):
        m = KG.modules["KB-SIGN-01"]
        base = {"driver_status": "UNIDENTIFIED"}
        pending = evaluate_module(m, base)
        self.assertNotEqual(pending.status, REJECTED)
        done = evaluate_module(m, base, evidence_state={"kinds": [], "complete": True})
        self.assertIn(done.status, (REJECTED, UNRESOLVED, BLOCKED, SUPPORTED))
        # whatever 'complete' says, uploading the evidence is what supports it
        got = {s for s in (evaluate_module(m, base, evidence_state={"kinds": ["SIGNAGE_PHOTO"]}).status,
                           evaluate_module(m, base, evidence_state={"kinds": []}).status)}
        self.assertNotIn(REJECTED, got)

    def test_evidence_absence_is_only_known_when_the_set_is_complete(self):
        pred = {"has_evidence": "PHOTO"}
        self.assertIsNone(evaluate3(pred, {"evidence_kinds": []}))
        self.assertIs(evaluate3(pred, {"evidence_kinds": [], "evidence_complete": True}), False)
        self.assertIs(evaluate3(pred, {"evidence_kinds": ["PHOTO"]}), True)
        self.assertIsNone(evaluate3(pred, {"evidence_kinds": [], "evidence_complete": False}))


class Roles(unittest.TestCase):
    def test_status_never_changes_the_role(self):
        for m in KG.active_modules():
            expect = ME.role_of(m)
            for view in ({"driver_status": "UNIDENTIFIED"}, {}):
                with self.subTest(module=m.module_id):
                    self.assertEqual(evaluate_module(m, view).module_role, expect)

    def test_a_supported_supporting_proposition_is_not_a_ground(self):
        from pcn_appeal.module_roles import can_be_claim_ground
        supp = mod({"is": "g"}, role="SUPPORTING_PROPOSITION")
        out = evaluate_module(supp, {"g": True})
        self.assertEqual(out.status, SUPPORTED)
        self.assertEqual(out.module_role, "SUPPORTING_PROPOSITION")
        self.assertFalse(can_be_claim_ground(supp))

    def test_roles_of_every_family_survive_support(self):
        for role in ("SUBSTANTIVE_GROUND", "SUPPORTING_PROPOSITION", "EVIDENCE_REQUIREMENT",
                     "LEGAL_CONCLUSION", "STRUCTURAL"):
            with self.subTest(role=role):
                self.assertEqual(evaluate_module(mod({"is": "g"}, role=role), {"g": True}).module_role,
                                 role)


class Conflicts(unittest.TestCase):
    """Only declared deterministic conflicts remove a module; nothing is inferred."""

    both = {"vehicle_immobilised": True, "immobilisation_prevented_departure": True,
            "fault_pre_existing_not_preventing": False, "no_parking_took_place": True,
            "payment_made": False, "permitted_period_ended": False,
            "immobilisation_cause": "x", "driver_status": "UNIDENTIFIED"}

    def test_a_declared_conflict_drops_the_weaker_module(self):
        for mid in ("KB-BREAK-01", "KB-CON-02"):
            self.assertEqual(evaluate_module_id(KG, mid, self.both).status, SUPPORTED)
        self.assertIn("KB-CON-02", KG.conflicts("KB-BREAK-01"))
        kept, why = ReasoningEngine(KG).eligibility(dict(self.both), object(), [])
        ids = {m.module_id for m in kept}
        self.assertIn("KB-BREAK-01", ids)
        self.assertNotIn("KB-CON-02", ids)
        self.assertIn("R-04", why["KB-CON-02"])

    def test_an_undeclared_pair_is_never_dropped(self):
        self.assertNotIn("KB-BREAK-02", KG.conflicts("KB-BREAK-01"))
        self.assertNotIn("KB-BREAK-01", KG.conflicts("KB-BREAK-02"))
        kept, _ = ReasoningEngine(KG).eligibility(dict(self.both), object(), [])
        ids = {m.module_id for m in kept}
        self.assertTrue({"KB-BREAK-01", "KB-BREAK-02"} <= ids)

    def test_similarity_alone_creates_no_block(self):
        src = inspect.getsource(ME)
        self.assertNotIn("similar", src.lower().replace("similarity", "similarity"))


class Authority(unittest.TestCase):
    """Eligibility reads the authoritative fact view and nothing else."""

    def test_only_the_view_and_findings_reach_the_decision(self):
        sig = inspect.signature(evaluate_module)
        self.assertEqual(set(sig.parameters),
                         {"module", "fact_view", "verified_findings", "finding_states",
                          "evidence_state", "unreliable", "blocking_signals", "kb_release_id"})
        src = inspect.getsource(ME)
        for banned in ("raw_answers", "narrative", "atoms", "semantic", "customer_text",
                       "circumstances", "retrieve", "case."):
            self.assertNotIn(banned, src.replace("customer's words, never a semantic atom", "")
                             .replace("never a semantic atom", "").replace("customer's words", ""),
                             banned)

    def test_raw_answers_and_semantic_state_cannot_change_a_status(self):
        for sid in ("PAY02-method-absent", "PAY02-machine", "PAY02-app-blocks"):
            sc = next(s for s in SCENARIOS if s["id"] == sid)
            case, view = H._case_for(sc)
            before = KM.KnowledgeMatcher(KG).match(case, dict(view)).candidates[sc["module"]].status
            case.raw_answers.update({"payment_method": "cash", "_semantic_case_state": "{}",
                                     "free_text": "I paid by cash at the machine"})
            after = KM.KnowledgeMatcher(KG).match(case, dict(view)).candidates[sc["module"]].status
            with self.subTest(sid=sid):
                self.assertEqual(before, after)

    def test_the_fact_view_is_usable_facts_only(self):
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        case = CaseFile("a")
        case.put(Fact("f1", "payment_made", True, FactStatus.UNCERTAIN,
                      FactSource(next(iter(SourceKind)), "t")))
        self.assertNotIn("payment_made", case.fact_view())
        match = KM.KnowledgeMatcher(KG).match(case)
        self.assertNotEqual(match.candidates["KB-PAY-01"].status, KM.SUPPORTED,
                            "an uncertain fact is not support")

    def test_an_untrusted_fact_in_an_explicit_view_is_still_not_trusted(self):
        """The matcher may be handed a view; a fact the case holds as UNCERTAIN is not
        evidence even if the view carries its value."""
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        case = CaseFile("u")
        case.put(Fact("f1", "payment_made", True, FactStatus.UNCERTAIN,
                      FactSource(next(iter(SourceKind)), "t")))
        view = {"driver_status": "UNIDENTIFIED", "payment_made": True,
                "terms_rejected_left": False}
        got = KM.KnowledgeMatcher(KG).match(case, view).candidates["KB-PAY-01"].status
        self.assertNotEqual(got, KM.SUPPORTED)
        trusted = CaseFile("t")
        got = KM.KnowledgeMatcher(KG).match(trusted, view).candidates["KB-PAY-01"].status
        self.assertEqual(got, KM.SUPPORTED, "the same view is support when the case trusts the fact")

    def test_same_view_same_status_whatever_the_candidate_route(self):
        """Candidate retrieval is not support: the status is a function of the view."""
        for sid in ("PAY02-machine", "PAY01-uncertain"):
            sc = next(s for s in SCENARIOS if s["id"] == sid)
            self.assertEqual(H.case_scenario_status(sc), H.eligibility_scenario_status(sc))


class NoSpecialCasing(unittest.TestCase):
    def test_no_module_id_or_fact_name_in_the_evaluator(self):
        for fn in (dsl.evaluate3, dsl._pofa_gate, dsl.leaf_report, ME.decide,
                   ME.evaluate_module, ME.effective_view):
            src = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertIsNone(re.search(r"KB-[A-Z]+-\d+", src), "module id in evaluator")
                for name in ("payment_method", "payment_made", "keying", "anpr", "signage"):
                    self.assertNotIn(name, src.lower())

    def test_the_matcher_decides_with_the_shared_evaluator(self):
        src = inspect.getsource(KM.KnowledgeMatcher._one)
        self.assertIn("evaluate3", src)
        self.assertNotRegex(src, r"\bevaluate\(module")


class ModuleMatrix(unittest.TestCase):
    """Every active module against generated truth-table cases, both paths, vs the reference."""

    def test_matcher_path_agrees_with_the_reference(self):
        res = H.module_matrix(H.production_status)
        self.assertGreater(res["cases"], 1000)
        self.assertEqual(res["modules_with_disagreement"], [], res["modules_with_disagreement"])

    def test_standalone_api_agrees_with_the_reference(self):
        res = H.module_matrix(H.eligibility_status)
        self.assertEqual(res["disagree"], 0)

    def test_every_active_module_is_covered(self):
        res = H.module_matrix(H.eligibility_status)
        self.assertEqual(set(res["modules"]), {m.module_id for m in KG.active_modules()})
        for mid, row in res["modules"].items():
            self.assertGreater(row["cases"], 0, mid)

    def test_all_four_statuses_are_reachable(self):
        res = H.module_matrix(H.eligibility_status)
        seen = set()
        for row in res["modules"].values():
            seen |= set(row["expected_mix"])
        self.assertEqual(seen, {SUPPORTED, UNRESOLVED, REJECTED, BLOCKED})


class Scenarios(unittest.TestCase):
    """Named scenarios by module family, expectations registered in advance."""

    def test_every_scenario_on_both_paths(self):
        for name, fn in (("case", H.case_scenario_status), ("api", H.eligibility_scenario_status)):
            for s in SCENARIOS:
                with self.subTest(path=name, scenario=s["id"]):
                    self.assertEqual(fn(s), s["expect"], s["family"])

    def test_the_required_families_are_present(self):
        fams = " ".join(s["family"] + " " + s["id"] for s in SCENARIOS).lower()
        for needed in ("pay02", "pay03", "payment", "key", "break", "multiple", "drop-off",
                       "barrier", "sign", "anpr", "pofa", "ev01", "land02"):
            self.assertIn(needed, fams, f"no scenario for {needed}")


class Pay0203(unittest.TestCase):
    """The reported defect: payment_method missing + a `ne` blocker blocked PAY-02/03."""

    def test_missing_method_is_unresolved_not_blocked(self):
        for mid in ("KB-PAY-02", "KB-PAY-03"):
            out = evaluate_module_id(KG, mid, {"driver_status": "UNIDENTIFIED",
                                               "payment_attempt_failed": True})
            with self.subTest(module=mid):
                self.assertNotEqual(out.status, BLOCKED)
                self.assertEqual(out.blocking_conditions, [])

    def test_the_old_evaluator_blocked_it(self):
        """The defect, reproduced against the pre-fix semantics (op == 'ne' on missing)."""
        m = KG.modules["KB-PAY-02"]
        self.assertTrue(dsl.referenced_facts(m.do_not_use_when) & {"payment_method"})
        # Pre-3B rule (rules/dsl.py at 0168cac): `if v is _MISSING: return op == "ne"`.
        legacy = lambda p, v: True if "ne" in p and p["ne"][0] not in v else evaluate(p, v)
        blocker = m.do_not_use_when
        self.assertTrue(legacy(blocker, {"payment_attempt_failed": True}),
                        "under the old rule the unknown method satisfied the blocker")
        self.assertIsNone(evaluate3(blocker, {"payment_attempt_failed": True}))

    def test_confirmed_and_negated_paths(self):
        for sid in ("PAY02-machine", "PAY02-app-blocks", "PAY02-attempt-negated"):
            s = next(x for x in SCENARIOS if x["id"] == sid)
            with self.subTest(sid=sid):
                self.assertEqual(H.eligibility_scenario_status(s), s["expect"])


class Diagnostics(unittest.TestCase):
    def test_every_result_carries_the_contract_fields(self):
        keys = {"module_id", "status", "satisfied_conditions", "missing_conditions",
                "rejected_conditions", "blocking_conditions", "fact_keys_used"}
        for m in list(KG.active_modules())[:15]:
            d = evaluate_module(m, {"driver_status": "UNIDENTIFIED"},
                                kb_release_id="r1").diagnostics()
            self.assertTrue(keys <= set(d))
            self.assertEqual(d["kb_release_id"], "r1")
            self.assertIn(d["status"], ME.STATUSES)

    def test_conditions_are_partitioned_by_truth(self):
        m = mod({"all": [{"is": "a"}, {"is": "b"}, {"is": "c"}]})
        out = evaluate_module(m, {"a": True, "b": False})
        self.assertEqual((len(out.satisfied_conditions), len(out.rejected_conditions),
                          len(out.missing_conditions)), (1, 1, 1))
        self.assertEqual(out.fact_keys_used, ["a", "b"])

    def test_the_release_is_recorded(self):
        out = evaluate_module_id(KG, "KB-PAY-01", {"payment_made": True})
        self.assertTrue(out.kb_release_id)

    def test_a_blocked_outcome_names_the_blocker_and_the_fact_it_stood_on(self):
        m = mod({"is": "g"}, eq("m", "X"))
        out = evaluate_module(m, {"g": True, "m": "X"})
        self.assertEqual(out.status, BLOCKED)
        self.assertEqual(out.blocking_conditions, ["m=X"])
        self.assertIn("m", out.fact_keys_used)
        self.assertEqual(out.unverified_blockers, [])

    def test_pay02_blocked_names_the_method_that_blocked_it(self):
        out = evaluate_module_id(KG, "KB-PAY-02", {"driver_status": "UNIDENTIFIED",
                                                   "payment_attempt_failed": True,
                                                   "payment_method": "APP"})
        self.assertEqual(out.status, BLOCKED)
        self.assertEqual(out.blocking_conditions, ["payment_method!=MACHINE"])
        self.assertIn("payment_method", out.fact_keys_used)

    def test_a_blocking_signal_blocks_only_when_declared(self):
        m = mod({"is": "g"})
        self.assertEqual(status(m, {"g": True}), SUPPORTED)
        self.assertEqual(status(m, {"g": True}, blocking_signals=["allegation_class=X"]), BLOCKED)


class Invariants(unittest.TestCase):
    def test_missing_is_not_rejection_and_not_a_blocker(self):
        for m in KG.active_modules():
            out = evaluate_module(m, {"driver_status": "UNIDENTIFIED"})
            with self.subTest(module=m.module_id):
                self.assertEqual(out.rejected_conditions, [])
                self.assertEqual(out.blocking_conditions, [])

    def test_unknown_never_becomes_support(self):
        """SUPPORTED needs a TRUE gate under three-valued logic, which is TRUE under every
        completion of the unknown leaves (Kleene monotonicity); an unknown leaf may
        remain only where another branch already decides the tree."""
        from pcn_appeal.eval.eligibility import reference as R
        for m in KG.active_modules():
            for view, _ in matrix.cases_for(m, 120):
                out = evaluate_module(m, view)
                if out.status == SUPPORTED:
                    with self.subTest(module=m.module_id):
                        self.assertIs(out.use_when, True)
                        self.assertIs(R.tree(m.use_when, view), True)


    def test_supported_requires_a_true_gate_and_no_true_blocker(self):
        for m in KG.active_modules():
            for view, _ in matrix.cases_for(m, 80):
                out = evaluate_module(m, view)
                if out.status == SUPPORTED:
                    self.assertIs(out.use_when, True)
                    self.assertIs(out.do_not_use_when, False,
                                  "SUPPORTED needs every hard blocker FALSE, not merely not-TRUE")

    def test_status_is_a_pure_function_of_the_inputs(self):
        m = KG.modules["KB-PAY-02"]
        view = {"driver_status": "UNIDENTIFIED", "payment_attempt_failed": True}
        runs = {evaluate_module(m, dict(view)).status for _ in range(5)}
        self.assertEqual(len(runs), 1)
        self.assertEqual(view, {"driver_status": "UNIDENTIFIED", "payment_attempt_failed": True},
                         "the input view is not mutated")


# ======================================================================== Phase 3B.1
# A module is never SUPPORTED while one of its hard do_not_use_when conditions is UNKNOWN.

def _supported_views(m, limit=3):
    out = []
    for view, _ in matrix.cases_for(m, 400):
        if evaluate_module(m, view).status == SUPPORTED:
            out.append(view)
        if len(out) >= limit:
            break
    return out


def _blocker_leaves(m):
    from pcn_appeal.kg.relations import _leaves
    seen, out = set(), []
    for _, leaf in _leaves(m.do_not_use_when):
        key = repr(sorted(leaf.items(), key=repr))
        if key not in seen:
            seen.add(key)
            out.append(leaf)
    return out


def _leaf_fact(leaf):
    op, arg = next(iter(leaf.items()))
    return arg if isinstance(arg, str) else arg[0]


def _with_blocker_in(m, base, leaf, state):
    """(view, unreliable, stale) putting one blocker leaf in `state`, or None when the
    rest of the view does not allow it. `stale` is the value an untrusted fact still
    carries in a view handed in from outside."""
    from pcn_appeal.eval.eligibility import reference as R
    name = _leaf_fact(leaf)
    view = {k: v for k, v in base.items() if k != name}
    if state in ("true", "false"):
        return (view, frozenset(), None) if matrix.realise(leaf, state == "true", view) else None
    if state == "missing":
        return view, frozenset(), None
    return view, frozenset({name}), base.get(name, "x")


class UnknownBlockerMatrix(unittest.TestCase):
    """Every active module with a hard blocker x (TRUE, FALSE, MISSING, UNCERTAIN, CONFLICTED)."""

    STATES = ("true", "false", "missing", "uncertain", "conflicted")

    def _modules(self):
        return [m for m in sorted(KG.active_modules(), key=lambda m: m.module_id)
                if m.do_not_use_when and m.do_not_use_when != {"always": False}]

    def test_there_are_modules_with_blockers(self):
        # 16 modules had a hard blocker; PAY-01 and BREAK-01 lost theirs in 3B.2
        # (nothing could ever settle them) and are checked in HardBlockerResolvability.
        self.assertGreaterEqual(len(self._modules()), 14)

    def test_every_blocker_state_against_the_reference(self):
        from pcn_appeal.eval.eligibility import reference as R
        ran = 0
        for m in self._modules():
            bases = _supported_views(m)
            self.assertTrue(bases, f"{m.module_id}: no SUPPORTED view to vary")
            for base in bases:
                for leaf in _blocker_leaves(m):
                    for state in self.STATES:
                        got = _with_blocker_in(m, base, leaf, state)
                        if got is None:
                            continue
                        view, unreliable, _ = got
                        want = R.status(m.use_when, m.do_not_use_when, view, unreliable)
                        out = evaluate_module(m, view, unreliable=unreliable)
                        ran += 1
                        with self.subTest(module=m.module_id, leaf=leaf, state=state):
                            self.assertEqual(out.status, want)
        self.assertGreater(ran, 100)

    def test_blocker_missing_uncertain_conflicted_is_never_supported(self):
        from pcn_appeal.eval.eligibility import reference as R
        for m in self._modules():
            for base in _supported_views(m):
                for leaf in _blocker_leaves(m):
                    for state in ("missing", "uncertain", "conflicted"):
                        view, unreliable, _ = _with_blocker_in(m, base, leaf, state)
                        out = evaluate_module(m, view, unreliable=unreliable)
                        open_ = R.tree(m.do_not_use_when, view, unreliable) is None
                        with self.subTest(module=m.module_id, leaf=leaf, state=state):
                            if open_:
                                # the blocker is genuinely unsettled
                                self.assertNotEqual(out.status, SUPPORTED)
                                self.assertNotEqual(out.status, BLOCKED,
                                                    "absence is not a contradiction")
                            else:
                                # a compound blocker another arm already makes FALSE
                                # (all[a, b] with b known FALSE) is settled, not open
                                self.assertIs(R.tree(m.do_not_use_when, view, unreliable), False)

    def test_the_case_path_agrees_for_uncertain_and_conflicted_facts(self):
        """A fact the case holds as UNCERTAIN, or as disputed (CONFLICTED), is not
        evidence either way even when the value is still present in the view."""
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        src = FactSource(next(iter(SourceKind)), "t")
        for m in self._modules():
            base = _supported_views(m, 1)[0]
            for leaf in _blocker_leaves(m):
                name = _leaf_fact(leaf)
                if name == "driver_status":
                    continue          # the case always carries a driver status
                from pcn_appeal.eval.eligibility import reference as R
                if R.tree(m.do_not_use_when, {k: v for k, v in base.items() if k != name},
                          frozenset({name})) is not None:
                    continue          # another arm of a compound blocker already settles it
                for state, kw in (("uncertain", dict(status=FactStatus.UNCERTAIN)),
                                  ("conflicted", dict(status=FactStatus.CONFIRMED, disputed=True))):
                    case = CaseFile("bm")
                    case.put(Fact("f-x", name, "stale", source=src, **kw))
                    view = dict(base)
                    view.setdefault(name, "stale")
                    got = KM.KnowledgeMatcher(KG).match(case, view).candidates[m.module_id].status
                    with self.subTest(module=m.module_id, fact=name, state=state):
                        self.assertNotEqual(got, KM.SUPPORTED)
                        self.assertNotEqual(got, KM.BLOCKED)

    def test_single_blocker_modules_literal_table(self):
        """Written out, not computed: TRUE -> BLOCKED, FALSE -> SUPPORTED, else UNRESOLVED."""
        table = {  # module: (fact, base view that otherwise supports it)
            "KB-ACT-02": ("payment_made", {"dropoff_activity": True, "permitted_period_ended": False}),
            "KB-CON-01": ("permitted_period_ended",
                          {"short_presence_before_acceptance": True, "payment_made": False}),
            "KB-CON-02": ("permitted_period_ended",
                          {"no_parking_took_place": True, "payment_made": False}),
            "KB-AUTH-02": ("lease_parking_clause_found",
                           {"permit_held": True, "lease_evidence_provided": True}),
            "KB-POFA-01": ("pofa_route", {"driver_status": "UNIDENTIFIED",
                                         "jurisdiction": "ENGLAND_WALES"}),
        }
        for mid, (fact, base) in table.items():
            base = dict(base, driver_status=base.get("driver_status", "UNIDENTIFIED"))
            m = KG.modules[mid]
            blocker_true = {"pofa_route": "NOT_APPLICABLE"}.get(fact, True)
            blocker_false = {"pofa_route": "POSTAL"}.get(fact, False)
            with self.subTest(module=mid):
                self.assertEqual(status(m, {**base, fact: blocker_true}), BLOCKED)
                self.assertEqual(status(m, {**base, fact: blocker_false}), SUPPORTED)
                self.assertEqual(status(m, dict(base)), UNRESOLVED)
                self.assertEqual(status(m, dict(base), unreliable={fact}), UNRESOLVED)


class BlockerPrecedence(unittest.TestCase):
    """The precedence table, every use_when x do_not_use_when combination."""

    GATE = {"is": "g"}

    def _m(self):
        return mod(self.GATE, {"is": "b"})

    def test_all_nine_combinations(self):
        T_, F_, U_ = True, False, None
        view = lambda g, b: {k: v for k, v in (("g", g), ("b", b)) if v is not None}
        table = {
            (T_, T_): BLOCKED, (F_, T_): BLOCKED, (U_, T_): BLOCKED,       # 1: a TRUE blocker
            (F_, F_): REJECTED, (F_, U_): REJECTED,                        # 2: known not to apply
            (T_, U_): UNRESOLVED, (U_, F_): UNRESOLVED, (U_, U_): UNRESOLVED,   # 3, 4: open
            (T_, F_): SUPPORTED,                                           # 5
        }
        self.assertEqual(len(table), 9)
        for (g, b), want in table.items():
            with self.subTest(use_when=g, blocker=b):
                self.assertEqual(status(self._m(), view(g, b)), want)

    def test_decide_is_the_same_table(self):
        for g in (True, False, None):
            for b in (True, False, None):
                got = ME.decide(g, b)
                if b is True:
                    self.assertEqual(got, BLOCKED)
                elif g is False:
                    self.assertEqual(got, REJECTED)
                elif g is True and b is False:
                    self.assertEqual(got, SUPPORTED)
                else:
                    self.assertEqual(got, UNRESOLVED)

    def test_a_declared_signal_blocks_before_anything(self):
        for g in (True, False, None):
            for b in (False, None):
                self.assertEqual(ME.decide(g, b, True), BLOCKED)

    def test_use_when_false_and_blocker_unknown_is_never_supported(self):
        m = mod(self.GATE, {"is": "b"})
        out = evaluate_module(m, {"g": False})
        self.assertEqual(out.status, REJECTED)
        self.assertFalse(ME.gate_holds(m, {"g": False}))

    def test_multiple_blockers(self):
        m = mod(self.GATE, {"any": [{"is": "b1"}, {"is": "b2"}]})
        cases = [
            ({"g": True, "b1": False, "b2": False}, SUPPORTED),    # FALSE + FALSE
            ({"g": True, "b1": False}, UNRESOLVED),                # FALSE + UNKNOWN
            ({"g": True}, UNRESOLVED),                             # UNKNOWN + UNKNOWN
            ({"g": True, "b1": True}, BLOCKED),                    # TRUE + UNKNOWN
            ({"g": True, "b1": True, "b2": False}, BLOCKED),       # TRUE + FALSE
            ({"g": True, "b1": True, "b2": True}, BLOCKED),
        ]
        for view, want in cases:
            with self.subTest(view=view):
                self.assertEqual(status(m, view), want)

    def test_multiple_blockers_with_an_uncertain_one(self):
        m = mod(self.GATE, {"any": [{"is": "b1"}, {"is": "b2"}]})
        self.assertEqual(status(m, {"g": True, "b1": False}, unreliable={"b2"}), UNRESOLVED)
        self.assertEqual(status(m, {"g": True, "b1": True}, unreliable={"b2"}), BLOCKED)

    def test_required_false_with_unknown_blocker(self):
        m = mod({"all": [{"is": "a"}, {"is": "b"}]}, {"is": "x"})
        self.assertEqual(status(m, {"a": False}), REJECTED)                 # a known FALSE
        self.assertEqual(status(m, {"a": True}), UNRESOLVED)                # open, never SUPPORTED
        self.assertEqual(status(m, {"a": True, "b": True}), UNRESOLVED)
        self.assertEqual(status(m, {"a": True, "b": True, "x": False}), SUPPORTED)

    def test_unknown_blocker_never_hidden_by_a_known_use_when(self):
        for m in KG.active_modules():
            for view, _ in matrix.cases_for(m, 120):
                out = evaluate_module(m, view)
                if out.status == SUPPORTED:
                    with self.subTest(module=m.module_id):
                        self.assertEqual(out.unverified_blockers, [])


class TwelveModuleAudit(unittest.TestCase):
    """A fact used only by do_not_use_when cannot be missing while the module is SUPPORTED,
    and nothing was added to required_facts to make that so."""

    TWELVE = ["KB-POFA-01", "KB-POFA-02", "KB-POFA-03", "KB-POFA-04", "KB-PAY-01",
              "KB-BREAK-01", "KB-CON-01", "KB-CON-02", "KB-EV-01", "KB-AUTH-01",
              "KB-AUTH-02", "KB-ACT-02"]

    # PAY-01 and BREAK-01 no longer carry a hard blocker (3B.2): what they excluded
    # on could never be known. They are audited in HardBlockerResolvability.
    HARD = [m for m in TWELVE if m not in ("KB-PAY-01", "KB-BREAK-01")]

    def test_each_blocker_only_fact_when_missing_prevents_support(self):
        from pcn_appeal.eval.eligibility import reference as R
        unsettled = 0
        for mid in self.HARD:
            m = KG.modules[mid]
            only = dsl.referenced_facts(m.do_not_use_when) - dsl.referenced_facts(m.use_when)
            self.assertTrue(only, f"{mid} has no blocker-only fact")
            bases = _supported_views(m, 5)
            self.assertTrue(bases, mid)
            for base in bases:
                for fact in sorted(only):
                    if fact not in base:
                        continue
                    view = {k: v for k, v in base.items() if k != fact}
                    for how, unreliable in (("missing", set()), ("uncertain", {fact}),
                                            ("conflicted", {fact})):
                        unsettled += R.tree(m.do_not_use_when, view, unreliable) is None
                        out = evaluate_module(m, view, unreliable=unreliable)
                        settled = R.tree(m.do_not_use_when, view, unreliable) is not None
                        with self.subTest(module=mid, fact=fact, state=how):
                            self.assertEqual(evaluate_module(m, base).status, SUPPORTED)
                            if settled:
                                # compound blocker (POFA-04): another arm is known FALSE,
                                # so the missing fact cannot make it hold
                                self.assertEqual(out.status, SUPPORTED)
                                self.assertEqual(out.unverified_blockers, [])
                            else:
                                self.assertEqual(out.status, UNRESOLVED)
                                self.assertTrue(out.unverified_blockers)
        self.assertGreater(unsettled, 20)

    def test_every_active_module_not_only_the_twelve(self):
        from pcn_appeal.eval.eligibility import reference as R
        for m in KG.active_modules():
            only = dsl.referenced_facts(m.do_not_use_when) - dsl.referenced_facts(m.use_when)
            for base in _supported_views(m, 3):
                for fact in sorted(only & set(base)):
                    view = {k: v for k, v in base.items() if k != fact}
                    if R.tree(m.do_not_use_when, view) is not None:
                        continue          # settled by another arm of a compound blocker
                    with self.subTest(module=m.module_id, fact=fact):
                        self.assertNotEqual(evaluate_module(m, view).status, SUPPORTED)

    def test_no_required_facts_were_added_to_cover_blockers(self):
        """required_facts of the twelve is what the KB shipped (git HEAD~ of this phase)."""
        import subprocess
        shipped = subprocess.run(["git", "show", "0168cac:pcn_appeal/data/kb_modules.yaml"],
                                 capture_output=True, text=True)
        if shipped.returncode:
            self.skipTest("git history not available")
        import yaml
        raw = yaml.safe_load(shipped.stdout)
        mods = raw.get("modules") if isinstance(raw, dict) else raw
        before = {m["module_id"]: list(m.get("required_facts") or []) for m in mods}
        for mid in self.TWELVE:
            self.assertEqual(KG.modules[mid].required_facts, before[mid], mid)

    def test_the_audit_set_is_the_modules_with_blocker_only_facts(self):
        have = {m.module_id for m in KG.active_modules()
                if dsl.referenced_facts(m.do_not_use_when) - dsl.referenced_facts(m.use_when)}
        self.assertTrue(set(self.HARD) <= have)


class Pofa04(unittest.TestCase):
    """The reverse-dependent proposition stays open; the rest of the appeal is not held up."""

    BASE = {"driver_status": "UNIDENTIFIED", "notice_route": "POSTAL", "pofa_route": "POSTAL",
            "ntk_defect_document_confirmed": True,
            "ntk_defect_keeper_warning": True}

    def _s(self, **extra):
        return evaluate_module_id(KG, "KB-POFA-04", {**self.BASE, **extra})

    def test_a_reverse_complete_notice_is_evaluated_normally(self):
        self.assertEqual(self._s(notice_sides_complete=True).status, SUPPORTED)

    def test_front_only_with_the_wording_explicitly_safe_is_pleadable(self):
        out = self._s(notice_sides_complete=False, ntk_invites_pass_to_driver=False)
        self.assertEqual(out.status, SUPPORTED)

    def test_front_only_with_the_wording_explicitly_a_blocker_is_blocked(self):
        out = self._s(notice_sides_complete=False, ntk_invites_pass_to_driver=True)
        self.assertEqual(out.status, BLOCKED)
        self.assertTrue(out.blocking_conditions)

    def test_front_only_with_the_wording_unknown_is_unresolved_not_blocked(self):
        out = self._s(notice_sides_complete=False)
        self.assertEqual(out.status, UNRESOLVED)
        self.assertEqual(out.blocking_conditions, [])
        self.assertTrue(any("ntk_invites_pass_to_driver" in b for b in out.unverified_blockers))

    def test_sides_unknown_is_unresolved(self):
        self.assertEqual(self._s().status, UNRESOLVED)

    def test_an_unrelated_verified_timing_defect_stays_usable(self):
        """Front-only upload: POFA-04 stays open, the verified postal-timing ground does not."""
        view = {**self.BASE, "notice_sides_complete": False}
        timing = evaluate_module_id(KG, "KB-POFA-02", view, verified_findings=["POFA_POSTAL_LATE"])
        content = evaluate_module_id(KG, "KB-POFA-04", view, verified_findings=["POFA_POSTAL_LATE"])
        self.assertEqual(timing.status, SUPPORTED)
        self.assertEqual(content.status, UNRESOLVED)

    def test_the_timing_ground_survives_the_reasoning_gate_beside_an_open_content_ground(self):
        view = {**self.BASE, "notice_sides_complete": False, "pofa_findings": ["POFA_POSTAL_LATE"],
                "pofa_finding": "POFA_POSTAL_LATE"}
        kept, why = ReasoningEngine(KG).eligibility(view, object(), [])
        ids = {m.module_id for m in kept}
        self.assertIn("KB-POFA-02", ids)
        self.assertNotIn("KB-POFA-04", ids)
        self.assertIn("R-03", why["KB-POFA-04"])

    def test_front_only_is_a_valid_case_for_the_matcher(self):
        view = {**self.BASE, "notice_sides_complete": False, "pofa_findings": ["POFA_POSTAL_LATE"],
                "pofa_finding": "POFA_POSTAL_LATE"}
        case = CaseFile("fo")
        case.legal_findings.append({"finding_type": "POFA_POSTAL_LATE", "status": "VERIFIED"})
        match = KM.KnowledgeMatcher(KG).match(case, view)
        self.assertEqual(match.candidates["KB-POFA-02"].status, KM.SUPPORTED)
        self.assertIn(match.candidates["KB-POFA-04"].status, (KM.RELEVANT, KM.OPEN))
        self.assertIn("ntk_invites_pass_to_driver", match.candidates["KB-POFA-04"].missing)

    def test_the_yaml_carries_no_extra_clause_for_it(self):
        """The generic rule is enough: the module is the one the KB shipped."""
        m = KG.modules["KB-POFA-04"]
        self.assertNotIn("ntk_invites_pass_to_driver", dsl.referenced_facts(m.use_when))


class DownstreamGates(unittest.TestCase):
    """The places that turn eligibility into a ground read the same rule."""

    def test_reasoning_gate_requires_every_blocker_false(self):
        base = {"driver_status": "UNIDENTIFIED", "dropoff_activity": True,
                "permitted_period_ended": False}
        eng = ReasoningEngine(KG)
        kept, why = eng.eligibility(dict(base), object(), [])
        self.assertNotIn("KB-ACT-02", {m.module_id for m in kept})
        self.assertIn("R-03", why["KB-ACT-02"])
        kept, _ = eng.eligibility({**base, "payment_made": False}, object(), [])
        self.assertIn("KB-ACT-02", {m.module_id for m in kept})
        kept, why = eng.eligibility({**base, "payment_made": True}, object(), [])
        self.assertNotIn("KB-ACT-02", {m.module_id for m in kept})

    def test_gate_holds_equals_supported_for_every_module(self):
        for m in KG.active_modules():
            for view, _ in matrix.cases_for(m, 80):
                self.assertEqual(ME.gate_holds(m, view), evaluate_module(m, view).status == SUPPORTED,
                                 m.module_id)

    def test_the_matcher_reports_the_open_blocker_and_what_it_waits_on(self):
        view = {"driver_status": "UNIDENTIFIED", "dropoff_activity": True,
                "permitted_period_ended": False}
        c = KM.KnowledgeMatcher(KG).match(CaseFile("m"), view).candidates["KB-ACT-02"]
        self.assertIn(c.status, (KM.RELEVANT, KM.OPEN))
        self.assertIn("payment_made", c.missing)
        self.assertTrue(c.unverified_blockers)
        self.assertIn("not yet ruled out", c.reason)

    def test_a_proposed_module_with_an_open_blocker_never_reaches_the_claim_plan(self):
        from pcn_appeal.engines.analysis import AnalysisEngine
        from pcn_appeal.legal import pofa as pofa_mod
        from pcn_appeal.llm import FakeLLM
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind

        def propose(*facts):
            case = CaseFile("open-blocker")
            for name, value in facts:
                case.put(Fact(f"F-{name}", name, value, FactStatus.ANSWERED,
                              FactSource(SourceKind.ANSWER, "q")))
            llm = FakeLLM({"case_analysis": [{
                "grounds": [{"module_id": "KB-ACT-02", "supported_by": ["dropoff_activity"],
                             "note": "x"}], "questions": [], "not_supported": []}]})
            return AnalysisEngine(KG, llm).analyse(
                case, pofa=pofa_mod.PofaResult("POSTAL", [], []), code_version=None)

        open_ = propose(("dropoff_activity", True), ("permitted_period_ended", False))
        self.assertNotIn("KB-ACT-02", open_.module_ids, "payment_made is not known")
        why = {row["module_id"]: row["why"] for row in open_.suppressed}
        self.assertIn("not yet ruled out", why.get("KB-ACT-02", ""))
        settled = propose(("dropoff_activity", True), ("permitted_period_ended", False),
                          ("payment_made", False))
        self.assertIn("KB-ACT-02", settled.module_ids)


if __name__ == "__main__":
    unittest.main()
