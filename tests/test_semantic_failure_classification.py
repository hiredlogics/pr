"""Phase 2F: an account that stayed ambiguous is not an account nobody read.

Two conditions used to end in the same place:

  SEMANTIC   a model read the account and a material ambiguity is still open
             (MATERIAL_AMBIGUITY, CLARIFICATION_EXHAUSTED)    -> ACCOUNT_UNRESOLVED
  TECHNICAL  no model reading was made: no provider, the call failed or timed
             out, the reply broke the contract                -> PROCESSING_ERROR

The second says nothing about the customer's account, so it is never described
as ambiguous, never rejected, and never "no supported grounds". Either way an
independent ground (a verified PoFA finding) proceeds. The kind is decided from
how the reading went (was it assessed, what stopped it), never from the wording
of the reason label.

Nothing here names an operator, a site, a retailer or a phrase.

Run:  PYTHONPATH=.:tests python -m unittest discover -s tests -p test_semantic_failure_classification.py -v
"""
from __future__ import annotations

import json
import unittest

from pcn_appeal.api import _outcome_fields
from pcn_appeal.engines import outcome as O
from pcn_appeal.models import CaseFile, CaseState
from pcn_appeal.semantics import understanding as U
from test_account_unresolved_outcome import _ask_again, _journey
from test_semantic_readiness_gate import (
    ACCOUNT_TEXT, LATE_NTK, _Injected, _ask, _customer_derived, _events, _ids, _packet,
    _pofa, _reading, _run, _seed, _unresolved,
)

NO_ACCOUNT_MEANING = "I do not agree with this charge."

# Everything that stops a reading being made, as the model client reports it.
TECHNICAL_FAULTS = {
    "provider failure": RuntimeError("provider unavailable"),
    "provider timeout": TimeoutError("semantic provider timeout"),
    "connection lost": ConnectionError("connection reset"),
    "reply with no status": _reading(None),
    "reply with a status outside the contract": _reading("MAYBE"),
    "empty reply": {},
}

HOLD_OUTCOMES = ("ACCOUNT_UNRESOLVED", "NO_SUPPORTED_GROUNDS")


class SemanticUnresolvedIsAccountUnresolved(unittest.TestCase):
    """A. A reading was made and a material ambiguity remains."""

    def test_exhausted_clarifications_with_no_independent_ground(self):
        case, out, _q = _journey([_ask(), _ask_again(1), _ask_again(2)],
                                 ["the second one", "in the afternoon"])
        self.assertEqual(U.customer_stream_blocked(case), "CLARIFICATION_EXHAUSTED")
        self.assertEqual(U.customer_stream_failure(case), U.SEMANTIC)
        self.assertIsNone(U.technical_cause(U.load_packet(case)))
        self.assertEqual(out.outcome, "ACCOUNT_UNRESOLVED")
        self.assertNotEqual(out.state, CaseState.NO_SUPPORTED_GROUNDS)
        self.assertEqual(_ids(out), [])

    def test_a_material_ambiguity_the_model_could_not_resolve(self):
        case, out, _q = _run(_unresolved())
        self.assertEqual(U.customer_stream_blocked(case), "MATERIAL_AMBIGUITY")
        self.assertEqual(U.customer_stream_failure(case), U.SEMANTIC)
        self.assertEqual(out.outcome, "ACCOUNT_UNRESOLVED")
        self.assertTrue(_events(case, "held_account_unresolved"))
        self.assertEqual(_events(case, "held_semantic_processing"), [])

    def test_the_customer_copy_says_an_important_part_is_unresolved(self):
        copy = O.CUSTOMER_COPY[O.OUTCOME_ACCOUNT_UNRESOLVED]
        self.assertIn("important part of your account", copy["title"])


class ATechnicalFaultIsARetryableProcessingHold(unittest.TestCase):
    """B and C. Nothing read the account, so nothing is said about it."""

    def test_every_fault_with_no_independent_ground(self):
        for name, fault in TECHNICAL_FAULTS.items():
            with self.subTest(name):
                case, out, _q = _run(fault)
                self.assertEqual(U.customer_stream_failure(case), U.TECHNICAL)
                self.assertEqual(out.outcome, "PROCESSING_ERROR")
                self.assertNotIn(out.outcome, HOLD_OUTCOMES)
                self.assertNotEqual(out.state, CaseState.NO_SUPPORTED_GROUNDS)
                self.assertTrue(out.can_continue)
                self.assertEqual(_ids(out), [])
                self.assertEqual(len(out.draft.paragraphs), 0)
                self.assertTrue(_events(case, "held_semantic_processing"))
                self.assertEqual(_events(case, "held_account_unresolved"), [])
                self.assertEqual(_events(case, "analysis_complete_no_supported_grounds"), [])

    def test_the_cause_is_recorded_internally(self):
        want = {"provider failure": "SEMANTIC_PROVIDER_FAILED",
                "provider timeout": "SEMANTIC_PROVIDER_FAILED",
                "reply with no status": "INVALID_MODEL_RESPONSE",
                "reply with a status outside the contract": "INVALID_MODEL_RESPONSE"}
        for name, cause in want.items():
            with self.subTest(name):
                case, _out, _q = _run(TECHNICAL_FAULTS[name])
                row = _events(case, "held_semantic_processing")[-1]
                self.assertEqual(row["cause"], cause)

    def test_an_invalid_response_is_labelled_invalid_not_ambiguous(self):
        case, _out, _q = _run(_reading(None))
        self.assertEqual(U.customer_stream_blocked(case), "INVALID_MODEL_RESPONSE")

    def test_the_account_is_kept(self):
        case, out, _q = _run(TimeoutError("semantic provider timeout"))
        self.assertEqual(out.outcome, "PROCESSING_ERROR")
        self.assertEqual(case.raw_answers.get("narrative"), ACCOUNT_TEXT)

    def test_what_the_customer_sees_does_not_describe_the_account(self):
        info = O.no_ground_outcome(_seed(CaseFile("c"), _packet(assessed=False)))
        self.assertEqual(info["outcome"], "PROCESSING_ERROR")
        shown = " ".join(str(info[k]) for k in
                         ("outcome_title", "outcome_message", "outcome_next", "cta_label")).lower()
        for word in ("ambigu", "unclear", "not clear", "supported ground", "did not find",
                     "reject", "no case", "resolve", "customer_semantics",
                     "invalid_model_response", "ambiguity_not_assessed"):
            self.assertNotIn(word, shown)
        self.assertIn("saved", shown)
        self.assertTrue(info["can_continue"])

    def test_the_payload_the_api_returns_carries_no_internal_reason(self):
        _case, out, _q = _run(RuntimeError("provider unavailable"))
        shown = json.dumps(_outcome_fields(out))
        for code in ("INVALID_MODEL_RESPONSE", "SEMANTIC_PROVIDER_FAILED",
                     "customer_semantics", "provider unavailable"):
            self.assertNotIn(code, shown)


class ARetryAfterATechnicalFault(unittest.TestCase):

    def test_continuing_the_case_once_the_provider_recovers_uses_the_account(self):
        from pcn_appeal.models import EvidenceItem, FactStatus
        from pcn_appeal.orchestrator import AppealPipeline
        from support import ReferenceAnalysisLLM
        from test_semantic_readiness_gate import NOTICE
        fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
                  for k, v in NOTICE.items()}
        inner = ReferenceAnalysisLLM({"extraction": [
            {"fields": fields, "doc_types": {"E1": "PCN"}}]})
        llm = _Injected(TimeoutError("semantic provider timeout"), inner)
        case = CaseFile("p2f", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED], ACCOUNT_TEXT)
        held = pipe.generate(case)
        self.assertEqual(held.outcome, "PROCESSING_ERROR")
        self.assertEqual(_ids(held), [])
        self.assertIsNotNone(U.customer_stream_blocked(case))

        llm.reading = _reading("UNDERSTOOD")
        again = pipe.generate(case)
        self.assertIsNone(U.customer_stream_blocked(case))
        self.assertTrue(_customer_derived(_ids(again)), _ids(again))
        self.assertEqual(case.raw_answers.get("narrative"), ACCOUNT_TEXT)


class AnIndependentGroundWinsWhateverStoppedTheAccount(unittest.TestCase):
    """D and E, and every other fault."""

    def test_a_technical_fault_with_a_valid_pofa_ground(self):
        # The yardstick: the same notice with no account at all, so the customer
        # stream has nothing to say. A fault must leave the case exactly there.
        _c, alone, _q = _run(_reading("UNDERSTOOD"), LATE_NTK, narrative="")
        for name, fault in TECHNICAL_FAULTS.items():
            with self.subTest(name):
                case, out, _q = _run(fault, LATE_NTK)
                ids = _ids(out)
                self.assertTrue(_pofa(ids), ids)
                self.assertTrue(out.pack.pofa_findings)
                self.assertEqual(_customer_derived(ids), [], ids)
                self.assertEqual(_pofa(ids), _pofa(_ids(alone)))
                self.assertNotIn(out.outcome, HOLD_OUTCOMES)
                self.assertEqual(out.outcome, alone.outcome)
                self.assertEqual(_events(case, "held_semantic_processing"), [])

    def test_a_timeout_with_a_valid_pofa_ground(self):
        case, out, _q = _run(TimeoutError("semantic provider timeout"), LATE_NTK)
        self.assertTrue(_pofa(_ids(out)), _ids(out))
        self.assertIsNotNone(U.customer_stream_blocked(case))

    def test_a_material_ambiguity_with_a_valid_pofa_ground(self):
        for name, reading in (("unresolved", _unresolved()), ("asking", _ask())):
            with self.subTest(name):
                case, out, _q = _run(reading, LATE_NTK)
                ids = _ids(out)
                self.assertTrue(_pofa(ids), ids)
                self.assertEqual(_customer_derived(ids), [], ids)
                self.assertNotIn(out.outcome, HOLD_OUTCOMES)
                self.assertEqual(_events(case, "held_account_unresolved"), [])

    def test_the_notice_facts_and_findings_are_untouched_by_either(self):
        for fault in (RuntimeError("provider unavailable"), _unresolved()):
            case, out, _q = _run(fault, LATE_NTK)
            for name in ("vrm", "pcn_number", "operator_name", "notice_issue_date"):
                self.assertTrue(case.has(name), name)
            self.assertTrue(out.pack.pofa_findings)


class AUsableAccountWithNothingSupportedIsStillNoSupportedGrounds(unittest.TestCase):
    """F."""

    def test_it(self):
        empty = _reading("UNDERSTOOD", concepts=[], events=[], narrative_atoms=[])
        case, out, _q = _run(empty, narrative=NO_ACCOUNT_MEANING)
        self.assertIsNone(U.customer_stream_blocked(case))
        self.assertEqual(out.outcome, "NO_SUPPORTED_GROUNDS")
        self.assertEqual(out.state, CaseState.NO_SUPPORTED_GROUNDS)


class TheKindComesFromHowTheReadingWentNotFromTheLabel(unittest.TestCase):

    def _product(self, **kw):
        base = {"status": "UNDERSTOOD", "concepts": [], "events": [], "narrative_atoms": [],
                "relationships": [], "uncertainties": [], "clarification": None,
                "summary": "", "semantic_mode": "FALLBACK"}
        base.update(kw)
        return base

    def test_causes_of_a_reading_that_was_not_made(self):
        cases = {
            "SEMANTIC_PROVIDER_FAILED": self._product(
                fallback_reason="semantic_provider_call_failed", exception_class="TimeoutError"),
            "NO_SEMANTIC_PROVIDER": self._product(fallback_reason="no_semantic_provider_configured"),
            "SEMANTIC_RESULT_NOT_PRODUCED": self._product(fallback_reason="unknown"),
            "INVALID_MODEL_RESPONSE": self._product(semantic_mode="LIVE", status="MAYBE"),
        }
        for want, product in cases.items():
            with self.subTest(want):
                packet = U.build_packet(product, [])
                self.assertEqual(U.not_ready_kind(packet), U.TECHNICAL)
                self.assertEqual(U.technical_cause(packet), want)

    def test_an_assessed_reading_with_an_open_ambiguity_is_semantic(self):
        packet = U.build_packet(self._product(semantic_mode="LIVE", status="UNRESOLVED",
                                              summary="which visit is unclear"), [])
        self.assertEqual(U.not_ready_kind(packet), U.SEMANTIC)
        self.assertIsNone(U.technical_cause(packet))

    def test_a_ready_reading_is_neither(self):
        packet = U.build_packet(self._product(semantic_mode="LIVE"), [])
        self.assertIsNone(U.not_ready_kind(packet))

    def test_a_forged_label_cannot_change_the_kind(self):
        technical = dict(_packet(assessed=False), not_ready_reason="MATERIAL_AMBIGUITY")
        semantic = dict(_packet(U.UNRESOLVED, True, [
            {"ambiguity": "a", "reason": "r", "code": "MATERIAL_AMBIGUITY"}]),
            not_ready_reason="AMBIGUITY_NOT_ASSESSED")
        self.assertEqual(U.not_ready_kind(technical), U.TECHNICAL)
        self.assertEqual(U.not_ready_kind(semantic), U.SEMANTIC)
        self.assertEqual(O.no_ground_outcome(_seed(CaseFile("c"), technical))["outcome"],
                         "PROCESSING_ERROR")
        self.assertEqual(O.no_ground_outcome(_seed(CaseFile("c"), semantic))["outcome"],
                         "ACCOUNT_UNRESOLVED")

    def test_a_packet_that_records_no_assessment_is_technical(self):
        self.assertEqual(U.not_ready_kind({}), U.TECHNICAL)
        self.assertEqual(U.not_ready_kind({"status": U.UNDERSTOOD}), U.TECHNICAL)


class TheClassifierNeverCallsATechnicalFaultAnythingElse(unittest.TestCase):
    """Every road classify_hold has to the three hold outcomes."""

    def _paths(self, case):
        class Pack:
            def __init__(self, ids):
                self.module_ids = ids
        by_event = CaseFile("x")
        by_event.raw_answers = dict(case.raw_answers)
        by_event.audit.append({"event": "analysis_complete_no_supported_grounds"})
        yield O.classify_hold(by_event, Pack([]), None)
        yield O.classify_hold(case, Pack([]), None)
        yield O.classify_hold(case, Pack(["KB-LAND-01"]), None)

    def test_unassessed_packets_always_come_out_technical(self):
        for packet in (_packet(assessed=False),
                       dict(_packet(assessed=False), semantic_mode="FALLBACK",
                            processing={"fallback_reason": "semantic_provider_call_failed"})):
            for info in self._paths(_seed(CaseFile("c"), packet)):
                self.assertEqual(info["outcome"], "PROCESSING_ERROR")
                self.assertTrue(info["can_continue"])

    def test_assessed_unresolved_packets_never_come_out_technical(self):
        packet = _packet(U.UNRESOLVED, True, [
            {"ambiguity": "a", "reason": "r", "code": "CLARIFICATION_EXHAUSTED"}])
        for info in self._paths(_seed(CaseFile("c"), packet)):
            self.assertEqual(info["outcome"], "ACCOUNT_UNRESOLVED")

    def test_no_blocked_account_yields_no_supported_grounds(self):
        packets = (_packet(assessed=False),
                   _packet(U.UNRESOLVED, True, [
                       {"ambiguity": "a", "reason": "r", "code": "MATERIAL_AMBIGUITY"}]))
        for packet in packets:
            for info in self._paths(_seed(CaseFile("c"), packet)):
                self.assertNotEqual(info["outcome"], "NO_SUPPORTED_GROUNDS")


if __name__ == "__main__":
    unittest.main()
