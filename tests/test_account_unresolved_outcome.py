"""Phase 2E: what a case with nothing to argue is called.

NO_SUPPORTED_GROUNDS says the information available was understood and weighed.
It must never say "we could not safely understand the account". So a case whose
customer account is not usable and which has no independent ground is either
waiting on a clarification that can still be asked (NEEDS_FACTS) or
ACCOUNT_UNRESOLVED; only a usable account with nothing supported is
NO_SUPPORTED_GROUNDS.

Nothing here names an operator, a site, a retailer or a phrase.

Run:  PYTHONPATH=.:tests python -m unittest discover -s tests -p test_account_unresolved_outcome.py -v
"""
from __future__ import annotations

import json
import unittest

from pcn_appeal.api import _outcome_fields
from pcn_appeal.engines import outcome as O
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem, FactStatus
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.semantics import understanding as U
from support import ReferenceAnalysisLLM
from test_semantic_readiness_gate import (
    ACCOUNT_TEXT, LATE_NTK, NOTICE, _ask, _customer_derived, _events, _ids, _packet,
    _pofa, _reading, _run, _seed, _unresolved,
)

NO_ACCOUNT_MEANING = "I do not agree with this charge."


def _ask_again(n):
    return _reading("NEEDS_CLARIFICATION", {
        "question": f"Setting aside the earlier point, what was the reason for item {n} "
                    f"in your account, and when did it happen?",
        "ambiguity": f"the reason for item {n} is not stated"})


class _Sequence:
    """A model that gives its readings in order, the last one repeating."""

    def __init__(self, readings, inner):
        self.readings, self.inner, self.calls = list(readings), inner, []

    def complete_json(self, *, task, system, user, images=None):
        self.calls.append(task)
        if task == "semantic_extraction":
            return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        return self.inner.complete_json(task=task, system=system, user=user, images=images)


def _journey(readings, answers, doc=None, narrative=ACCOUNT_TEXT):
    """confirm, then answer each clarification put, then generate."""
    fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
              for k, v in dict(NOTICE, **(doc or {})).items()}
    inner = ReferenceAnalysisLLM({"extraction": [
        {"fields": fields, "doc_types": {"E1": "PCN"}}]})
    llm = _Sequence(readings, inner)
    case = CaseFile("p2e", evidence={
        "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice")})
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    confirmed = [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]
    questions = pipe.confirm(case, {}, confirmed, narrative)
    for answer in answers:
        put = [q for q in questions if str(q.get("fact", "")).startswith(U.FACT_PREFIX)]
        if not put:
            break
        questions = pipe.answer(case, {put[0]["fact"]: answer})
    return case, pipe.generate(case), questions


def _outcome_of(out):
    return out.outcome


class AnUnresolvedAccountWithAValidPoFA(unittest.TestCase):
    """A. Rule 1: the independent ground proceeds."""

    def test_the_pofa_ground_proceeds_and_the_case_is_not_held_as_unresolved(self):
        case, out, _q = _run(_unresolved(), LATE_NTK)
        self.assertTrue(_pofa(_ids(out)), _ids(out))
        self.assertEqual(_customer_derived(_ids(out)), [])
        self.assertNotIn(_outcome_of(out), ("ACCOUNT_UNRESOLVED", "NO_SUPPORTED_GROUNDS"))
        self.assertEqual(_events(case, "held_account_unresolved"), [])

    def test_the_same_holds_while_a_clarification_is_open_and_when_unassessed(self):
        for name, reading in (("asking", _ask()), ("unassessed", _reading(None))):
            with self.subTest(name):
                case, out, _q = _run(reading, LATE_NTK)
                self.assertTrue(_pofa(_ids(out)), _ids(out))
                self.assertNotIn(_outcome_of(out), ("ACCOUNT_UNRESOLVED", "NO_SUPPORTED_GROUNDS"))


class ANeedsClarificationAccountWithNoIndependentGround(unittest.TestCase):
    """B. Rule 2: ask, do not conclude."""

    @classmethod
    def setUpClass(cls):
        cls.case, cls.out, cls.questions = _run(_ask())

    def test_it_is_a_request_for_detail_not_a_conclusion(self):
        self.assertEqual(self.out.outcome, "NEEDS_FACTS")
        self.assertNotEqual(self.out.state, CaseState.NO_SUPPORTED_GROUNDS)
        self.assertTrue(self.out.can_continue)
        self.assertEqual(_ids(self.out), [])

    def test_the_clarification_is_the_question_the_customer_is_given(self):
        facts = [q.get("fact") for q in self.case.pending_questions]
        self.assertEqual(len([f for f in facts if str(f).startswith(U.FACT_PREFIX)]), 1, facts)

    def test_the_reason_is_internal_only(self):
        row = _events(self.case, "held_customer_clarification")[-1]
        self.assertEqual(row["customer_semantics_not_ready"], "CLARIFICATION_REQUIRED")
        shown = json.dumps(_outcome_fields(self.out))
        self.assertNotIn("CLARIFICATION_REQUIRED", shown)
        self.assertNotIn("customer_semantics", shown)


class AnUnresolvedAccountAfterTheClarificationCap(unittest.TestCase):
    """C. Rule 3."""

    @classmethod
    def setUpClass(cls):
        cls.case, cls.out, cls.questions = _journey(
            [_ask(), _ask_again(1), _ask_again(2)],
            ["the second one", "in the afternoon"])

    def test_the_account_is_unresolved_by_the_cap(self):
        packet = U.load_packet(self.case)
        self.assertEqual(packet["status"], U.UNRESOLVED)
        self.assertEqual(U.customer_stream_blocked(self.case), "CLARIFICATION_EXHAUSTED")

    def test_it_is_account_unresolved_and_not_no_supported_grounds(self):
        self.assertEqual(self.out.outcome, "ACCOUNT_UNRESOLVED")
        self.assertNotEqual(self.out.state, CaseState.NO_SUPPORTED_GROUNDS)
        self.assertEqual(_ids(self.out), [])
        self.assertEqual(len(self.out.draft.paragraphs), 0)
        self.assertTrue(self.out.can_continue)

    def test_no_more_questions_are_put(self):
        self.assertFalse([q for q in self.case.pending_questions
                          if str(q.get("fact", "")).startswith(U.FACT_PREFIX)])

    def test_the_account_and_the_answers_are_kept(self):
        self.assertEqual(self.case.raw_answers.get("narrative"), ACCOUNT_TEXT)
        self.assertEqual(len(U.answered(U.clarification_history(self.case))), 2)


class AnUnassessedAccountWithNoIndependentGround(unittest.TestCase):
    """D. Never a verdict. (Phase 2F: it is a retryable processing hold, because
    nothing read the account; ACCOUNT_UNRESOLVED is for a reading that did.)"""

    def test_unassessed_is_not_a_verdict_about_the_case(self):
        for name, reading in (("no status", _reading(None)),
                              ("provider down", RuntimeError("provider down"))):
            with self.subTest(name):
                case, out, _q = _run(reading)
                self.assertEqual(out.outcome, "PROCESSING_ERROR")
                self.assertNotEqual(out.state, CaseState.NO_SUPPORTED_GROUNDS)
                self.assertEqual(_ids(out), [])
                self.assertIn(U.customer_stream_blocked(case),
                              ("INVALID_MODEL_RESPONSE", "AMBIGUITY_NOT_ASSESSED"))


class AUsableAccountWithNothingSupported(unittest.TestCase):
    """E. Rule 4: the only way to NO_SUPPORTED_GROUNDS."""

    def test_a_clean_assessed_account_with_no_supported_module(self):
        empty = _reading("UNDERSTOOD", concepts=[], events=[], narrative_atoms=[])
        case, out, _q = _run(empty, narrative=NO_ACCOUNT_MEANING)
        self.assertIsNone(U.customer_stream_blocked(case))
        # P8 (client instruction 2026-10-07): an understood account with no
        # supported ground now gets the default keeper appeal, not no letter.
        self.assertEqual(_ids(out), ["KB-KEEPER-01"])
        self.assertNotEqual(out.state, CaseState.NO_SUPPORTED_GROUNDS)

    def test_no_account_at_all_is_not_an_unresolved_account(self):
        case, out, _q = _run(_reading("UNDERSTOOD"), narrative="")
        self.assertIsNone(U.customer_stream_blocked(case))
        self.assertNotEqual(out.outcome, "ACCOUNT_UNRESOLVED")


class AUsableAccountWithASupportedGround(unittest.TestCase):
    """F. Normal flow, untouched."""

    def test_the_customer_ground_is_used(self):
        case, out, _q = _run(_reading("UNDERSTOOD"))
        self.assertIsNone(U.customer_stream_blocked(case))
        self.assertTrue(_customer_derived(_ids(out)), _ids(out))
        self.assertNotIn(out.outcome, ("ACCOUNT_UNRESOLVED", "NO_SUPPORTED_GROUNDS"))


# ---------------------------------------------------------- the invariant
class _Pack:
    def __init__(self, module_ids):
        self.module_ids = module_ids


NOT_READY_PACKETS = {
    "unassessed": _packet(assessed=False),
    "material_ambiguity": _packet(U.UNRESOLVED, True, [
        {"ambiguity": "a", "reason": "r", "code": "MATERIAL_AMBIGUITY"}]),
    "exhausted": _packet(U.UNRESOLVED, True, [
        {"ambiguity": "a", "reason": "r", "code": "CLARIFICATION_EXHAUSTED"}]),
    "needs_clarification_but_nothing_to_ask": _packet(U.NEEDS_CLARIFICATION, True, [
        {"ambiguity": "a", "reason": "r", "code": "CLARIFICATION_REQUIRED"}]),
}


class NoSupportedGroundsCannotComeFromABlockedAccount(unittest.TestCase):
    """Every road to the code, not only the one the orchestrator takes."""

    def _paths(self, case):
        """classify_hold reaches NO_SUPPORTED_GROUNDS by three roads."""
        by_event = CaseFile("x")
        by_event.raw_answers = dict(case.raw_answers)
        by_event.audit.append({"event": "analysis_complete_no_supported_grounds"})
        yield "completed-analysis event", O.classify_hold(by_event, _Pack([]), None)
        yield "empty selection", O.classify_hold(case, _Pack([]), None)
        yield "support-only pack", O.classify_hold(case, _Pack(["KB-LAND-01"]), None)

    def test_a_blocked_account_never_yields_it_by_any_road(self):
        for name, packet in NOT_READY_PACKETS.items():
            case = _seed(CaseFile("c"), packet)
            for road, info in self._paths(case):
                with self.subTest(packet=name, road=road):
                    self.assertNotEqual(info["outcome"], "NO_SUPPORTED_GROUNDS")
                    self.assertIn(info["outcome"],
                                  ("ACCOUNT_UNRESOLVED", "NEEDS_FACTS", "PROCESSING_ERROR"))

    def test_a_usable_account_still_yields_it_by_each_road(self):
        for case in (_seed(CaseFile("c"), _packet()), CaseFile("c")):
            for road, info in self._paths(case):
                with self.subTest(road=road):
                    self.assertEqual(info["outcome"], "NO_SUPPORTED_GROUNDS")

    def test_a_stored_ready_flag_does_not_turn_a_blocked_account_into_a_verdict(self):
        forged = dict(NOT_READY_PACKETS["unassessed"], customer_semantics_ready=True,
                      ready_for_knowledge=True)
        case = _seed(CaseFile("c"), forged)
        for road, info in self._paths(case):
            with self.subTest(road=road):
                self.assertEqual(info["outcome"], "PROCESSING_ERROR")

    def test_the_detail_carries_the_internal_reason_and_the_copy_does_not(self):
        info = O.no_ground_outcome(_seed(CaseFile("c"), NOT_READY_PACKETS["exhausted"]))
        self.assertEqual(info["detail"]["customer_semantics_not_ready"],
                         "CLARIFICATION_EXHAUSTED")
        customer = " ".join(str(info[k]) for k in
                            ("outcome_title", "outcome_message", "outcome_next", "cta_label"))
        for code in ("CLARIFICATION", "MATERIAL_AMBIGUITY", "AMBIGUITY_NOT_ASSESSED",
                     "INVALID_MODEL_RESPONSE", "UNRESOLVED", "customer_semantics"):
            self.assertNotIn(code, customer)

    def test_the_two_outcomes_do_not_say_the_same_thing(self):
        a = O.CUSTOMER_COPY[O.OUTCOME_ACCOUNT_UNRESOLVED]
        n = O.CUSTOMER_COPY[O.OUTCOME_NO_SUPPORTED_GROUNDS]
        self.assertNotEqual(a["lede"], n["lede"])
        self.assertNotIn("did not find a supported ground", a["lede"])
        self.assertTrue(a["can_continue"])


if __name__ == "__main__":
    unittest.main()
