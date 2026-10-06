"""Phase 2D: the readiness of the customer's account gates the customer's account.

Two streams feed a case and neither may switch the other off:

  * the customer-account stream - what the customer's free text means. It is
    used for knowledge retrieval only while the reading is READY
    (understanding.is_ready): status UNDERSTOOD, ambiguity assessed by a model,
    no material ambiguity open.
  * the independent stream - the notice's own facts, document analysis, verified
    PoFA findings, timing. It never waits on the customer's free text.

Nothing here names an operator, a site, a retailer or a phrase. The model is
scripted; what is pinned is what code does with the verdict it gives.

Run:  PYTHONPATH=.:tests python -m unittest discover -s tests -p test_semantic_readiness_gate.py -v
"""
from __future__ import annotations

import itertools
import json
import unittest

from pcn_appeal.engines.knowledge_matcher import _semantic_candidate_signals
from pcn_appeal.engines.module_resolver import _semantic_material
from pcn_appeal.drafting.support_contract import _semantic_material_from_case
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem, FactStatus
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.semantics import understanding as U
from support import ReferenceAnalysisLLM

NOTICE = dict(
    operator_name="Northgate Parking Ltd", pcn_number="NG778899", vrm="KL55MNO",
    parking_location="Northgate Retail Park", site_postcode="LS2 7AA",
    parking_event_date="02/06/2026", notice_issue_date="08/06/2026",
    charge_amount="£100", alleged_breach="Overstayed maximum free period",
    operator_ata="BPA", entry_time="09:10", exit_time="12:37",
    jurisdiction="ENGLAND_WALES",
)
LATE_NTK = {"notice_issue_date": "26/06/2026"}          # a verified PoFA timing finding
ACCOUNT_TEXT = ("I was collecting a prescription for a relative, left the site "
                "straight afterwards and returned later that afternoon.")


def _c(concept, src=""):
    return {"concept": concept, "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": src, "confidence": 0.9}


def _ev(eid, etype, desc):
    return {"event_id": eid, "event_type": etype, "description": desc,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": desc, "confidence": 0.9}


def _at(aid, category, proposition):
    return {"atom_id": aid, "category": category, "proposition": proposition,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": proposition, "confidence": 0.9}


def _reading(status="UNDERSTOOD", clarification=None, **kw):
    """A model reply with the account's meaning in it. `status=None` is a model
    that did not say whether it understood: ambiguity was not assessed."""
    out = {"summary": "A visit, a departure and a return.",
           "concepts": [_c("COLLECTION", "collecting a prescription"),
                        _c("LEFT_SITE", "left the site"),
                        _c("RETURNED", "returned later"),
                        _c("MULTIPLE_VISITS", "returned later")],
           "events": [_ev("E1", "ACTIVITY", "Collected a prescription for a relative"),
                      _ev("E2", "DEPARTURE", "Left the site straight afterwards"),
                      _ev("E3", "RETURN", "Returned later that afternoon")],
           "narrative_atoms": [
               _at("A1", "visit_purpose", "The first attendance was to collect a prescription"),
               _at("A2", "return_event", "The later attendance was to take the relative home")],
           "relationships": [], "material_relevance": [], "uncertainties": [],
           "clarification": clarification}
    if status is not None:
        out["status"] = status
    out.update(kw)
    return out


def _ask():
    return _reading("NEEDS_CLARIFICATION", {
        "question": "Which of these did you mean: the first visit or the second?",
        "ambiguity": "'it' could be the first visit or the second"})


def _unresolved():
    return _reading("UNRESOLVED", None,
                    summary="Whether the second visit was a new stay is not clear.")


class _Injected:
    def __init__(self, reading, inner):
        self.reading, self.inner = reading, inner
        self.calls = []

    def complete_json(self, *, task, system, user, images=None):
        self.calls.append(task)
        if task == "semantic_extraction":
            if isinstance(self.reading, Exception):
                raise self.reading
            return self.reading
        return self.inner.complete_json(task=task, system=system, user=user, images=images)


def _run(reading, doc=None, narrative=ACCOUNT_TEXT):
    """One case through the real pipeline up to and including generate()."""
    fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
              for k, v in dict(NOTICE, **(doc or {})).items()}
    inner = ReferenceAnalysisLLM({"extraction": [
        {"fields": fields, "doc_types": {"E1": "PCN"}}]})
    llm = _Injected(reading, inner)
    case = CaseFile("p2d", evidence={
        "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice")})
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    confirmed = [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]
    questions = pipe.confirm(case, {}, confirmed, narrative)
    out = pipe.generate(case)
    return case, out, questions


def _ids(out):
    return list(out.pack.module_ids or [])


def _pofa(ids):
    return [m for m in ids if m.startswith("KB-POFA-")]


def _customer_derived(ids):
    return [m for m in ids if not m.startswith("KB-POFA-") and not m.startswith("KB-LAND")]


def _events(case, name):
    return [a for a in case.audit if a.get("event") == name]


# ------------------------------------------------------------ the definition
class TheReadinessDefinition(unittest.TestCase):

    def _packet(self, status, assessed, open_):
        return {"status": status, "ambiguity_assessed": assessed,
                "open_material_ambiguities": open_}

    def test_ready_only_for_understood_and_assessed_and_nothing_open(self):
        open_one = [{"ambiguity": "x", "reason": "y", "code": "MATERIAL_AMBIGUITY"}]
        for status, assessed, open_ in itertools.product(
                (U.UNDERSTOOD, U.NEEDS_CLARIFICATION, U.UNRESOLVED, None, "nonsense"),
                (True, False, None, "yes"),
                ([], open_one)):
            want = status == U.UNDERSTOOD and assessed is True and not open_
            with self.subTest(status=status, assessed=assessed, open=bool(open_)):
                self.assertIs(U.is_ready(self._packet(status, assessed, open_)), want)

    def test_no_packet_is_not_ready_and_has_a_reason(self):
        self.assertFalse(U.is_ready({}))
        self.assertFalse(U.is_ready(None))
        self.assertEqual(U.not_ready_reason({}), "AMBIGUITY_NOT_ASSESSED")

    def test_every_not_ready_packet_names_exactly_one_diagnostic_reason(self):
        reasons = {
            "CLARIFICATION_REQUIRED": self._packet(U.NEEDS_CLARIFICATION, True, [
                {"ambiguity": "a", "reason": "r", "code": "CLARIFICATION_REQUIRED"}]),
            "CLARIFICATION_EXHAUSTED": self._packet(U.UNRESOLVED, True, [
                {"ambiguity": "a", "reason": "r", "code": "CLARIFICATION_EXHAUSTED"}]),
            "MATERIAL_AMBIGUITY": self._packet(U.UNRESOLVED, True, [
                {"ambiguity": "a", "reason": "r", "code": "MATERIAL_AMBIGUITY"}]),
            "AMBIGUITY_NOT_ASSESSED": dict(self._packet(U.UNDERSTOOD, False, []),
                                           semantic_mode="FALLBACK"),
            "INVALID_MODEL_RESPONSE": dict(self._packet(U.UNDERSTOOD, False, []),
                                           semantic_mode="LIVE"),
        }
        for want, packet in reasons.items():
            with self.subTest(want):
                self.assertFalse(U.is_ready(packet))
                self.assertEqual(U.not_ready_reason(packet), want)
        self.assertIsNone(U.not_ready_reason(self._packet(U.UNDERSTOOD, True, [])))

    def test_an_unresolved_account_is_not_relabelled_understood_to_pass(self):
        """The diagnostic reason is kept apart; the status is never faked."""
        packet = self._packet(U.UNRESOLVED, True, [
            {"ambiguity": "a", "reason": "r", "code": "MATERIAL_AMBIGUITY"}])
        self.assertEqual(packet["status"], U.UNRESOLVED)
        self.assertFalse(U.is_ready(packet))


# --------------------------------------------- the accessor every consumer uses
def _seed(case, packet, state=None):
    """What an earlier run left in the case: the live semantic keys and a packet."""
    state = state or {
        "concepts": [{"concept": "MULTIPLE_VISITS", "polarity": "AFFIRMED"}],
        "narrative_atoms": [{"atom_id": "A1", "category": "visit_purpose",
                             "proposition": "collect a prescription"}],
        "events": [{"event_id": "E1", "event_type": "ACTIVITY"}],
        "relationships": [{"from": "E1", "to": "E2", "type": "CAUSES"}],
    }
    case.raw_answers["_semantic_case_state"] = json.dumps(state)
    case.raw_answers["_semantic_narrative_atoms"] = json.dumps(state["narrative_atoms"])
    case.raw_answers["_semantic_concepts"] = json.dumps(state["concepts"])
    U._save(case, {"asked": [], "packet": packet})
    return case


def _packet(status=U.UNDERSTOOD, assessed=True, open_=None, **kw):
    out = {"status": status, "ambiguity_assessed": assessed,
           "open_material_ambiguities": open_ or [], "semantic_mode": "LIVE"}
    out.update(kw)
    return out


NOT_READY = {
    "ambiguity_assessed_false": _packet(assessed=False),
    "open_material_ambiguity": _packet(U.UNRESOLVED, True, [
        {"ambiguity": "which visit", "reason": "x", "code": "MATERIAL_AMBIGUITY"}]),
    "understood_with_open_ambiguity": _packet(U.UNDERSTOOD, True, [
        {"ambiguity": "which visit", "reason": "x", "code": "MATERIAL_AMBIGUITY"}]),
    "needs_clarification": _packet(U.NEEDS_CLARIFICATION, True, [
        {"ambiguity": "which visit", "reason": "x", "code": "CLARIFICATION_REQUIRED"}]),
    "unresolved": _packet(U.UNRESOLVED, True, [
        {"ambiguity": "which visit", "reason": "x", "code": "CLARIFICATION_EXHAUSTED"}]),
}


def _retrieval_inputs(case):
    """Everything the customer-account stream hands to knowledge retrieval."""
    signals = _semantic_candidate_signals(case)
    material = _semantic_material(case)
    bundle_material = _semantic_material_from_case(case)
    from pcn_appeal.engines.reasoning import _semantic_material_relevance
    return signals, material, bundle_material, _semantic_material_relevance(case)


def _empty(value):
    if isinstance(value, dict):
        return all(_empty(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return all(_empty(v) for v in value)
    return not value


class OnlyAReadyAccountReachesKnowledgeRetrieval(unittest.TestCase):

    def test_a_ready_account_is_handed_to_retrieval(self):
        case = _seed(CaseFile("c"), _packet())
        self.assertIsNone(U.customer_stream_blocked(case))
        signals = _semantic_candidate_signals(case)
        self.assertTrue(signals["concepts"] and signals["atoms"]
                        and signals["events"] and signals["relationships"])

    def test_nothing_not_ready_can_enable_customer_semantic_retrieval(self):
        for name, packet in NOT_READY.items():
            with self.subTest(name):
                case = _seed(CaseFile("c"), packet)
                self.assertIsNotNone(U.customer_stream_blocked(case))
                signals, material, bundle_material, relevance = _retrieval_inputs(case)
                self.assertTrue(_empty(signals), signals)
                self.assertTrue(_empty(material), material)
                self.assertTrue(_empty(bundle_material), bundle_material)
                self.assertTrue(_empty(relevance), relevance)
                for key in ("_semantic_case_state", "_semantic_narrative_atoms",
                            "_semantic_concepts"):
                    self.assertIsNone(U.customer_semantic_raw(case, key), key)

    def test_a_packet_that_was_never_written_is_not_ready_either(self):
        """Semantic keys present, no packet: nothing certified the reading."""
        case = CaseFile("c")
        _seed(case, _packet())
        del case.raw_answers[U.STATE_KEY]
        # No packet means no semantic reading was ever made through the gate;
        # there is nothing to certify and nothing it blocks.
        self.assertIsNone(U.load_packet(case))

    def test_stored_readiness_flags_are_ignored_and_recomputed(self):
        for name, packet in NOT_READY.items():
            with self.subTest(name):
                forged = dict(packet, customer_semantics_ready=True,
                              ready_for_knowledge=True, not_ready_reason=None)
                case = _seed(CaseFile("c"), forged)
                self.assertFalse(U.is_ready(forged))
                self.assertIsNotNone(U.customer_stream_blocked(case))
                self.assertTrue(_empty(_retrieval_inputs(case)[0]))
                self.assertFalse(U.stream_status(case)["customer_semantics_ready"])

    def test_a_ready_packet_wrongly_stored_as_not_ready_is_still_ready(self):
        forged = _packet(customer_semantics_ready=False, ready_for_knowledge=False,
                         not_ready_reason="MATERIAL_AMBIGUITY")
        case = _seed(CaseFile("c"), forged)
        self.assertIsNone(U.customer_stream_blocked(case))
        self.assertTrue(_semantic_candidate_signals(case)["concepts"])

    def test_the_gate_looks_at_the_packet_now_not_at_when_it_was_stored(self):
        case = _seed(CaseFile("c"), _packet())
        self.assertTrue(_semantic_candidate_signals(case)["concepts"])
        state = U._state(case)
        state["packet"]["open_material_ambiguities"] = [
            {"ambiguity": "which visit", "reason": "x", "code": "MATERIAL_AMBIGUITY"}]
        U._save(case, state)
        self.assertTrue(_empty(_semantic_candidate_signals(case)))


# ------------------------------------------------- the two streams, end to end
class AReadyAccountAndAnIndependentGroundProceedTogether(unittest.TestCase):
    """A and F."""

    @classmethod
    def setUpClass(cls):
        cls.case, cls.out, cls.questions = _run(_reading("UNDERSTOOD"), LATE_NTK)

    def test_the_independent_ground_and_the_account_ground_both_proceed(self):
        ids = _ids(self.out)
        self.assertTrue(_pofa(ids), ids)
        self.assertTrue(_customer_derived(ids), ids)
        self.assertTrue(self.out.pack.pofa_findings)

    def test_the_account_is_certified_and_retrievable(self):
        self.assertIsNone(U.customer_stream_blocked(self.case))
        self.assertTrue(U.stream_status(self.case)["customer_semantics_ready"])
        self.assertEqual(_events(self.case, "CUSTOMER_SEMANTICS_NOT_READY"), [])

    def test_a_clean_account_with_no_independent_ground_reaches_retrieval(self):
        case, out, _q = _run(_reading("UNDERSTOOD"))
        self.assertIsNone(U.customer_stream_blocked(case))
        self.assertTrue(_semantic_candidate_signals(case)["concepts"])
        self.assertTrue(_customer_derived(_ids(out)), _ids(out))


class AnUnresolvedAccountDoesNotErasePoFAOrCreateAGround(unittest.TestCase):
    """B."""

    @classmethod
    def setUpClass(cls):
        cls.case, cls.out, cls.questions = _run(_unresolved(), LATE_NTK)

    def test_the_verified_pofa_ground_still_proceeds(self):
        ids = _ids(self.out)
        self.assertTrue(_pofa(ids), ids)
        self.assertTrue(self.out.pack.pofa_findings)

    def test_the_unresolved_account_creates_no_ground(self):
        ids = _ids(self.out)
        self.assertEqual(_customer_derived(ids), [], ids)

    def test_the_notice_facts_are_untouched(self):
        for name in ("vrm", "pcn_number", "operator_name", "notice_issue_date"):
            self.assertTrue(self.case.has(name), name)

    def test_the_decision_is_recorded_with_its_reason_and_not_for_the_customer(self):
        rows = _events(self.case, "CUSTOMER_SEMANTICS_NOT_READY")
        self.assertTrue(rows)
        self.assertIn(rows[-1]["reason"], {"MATERIAL_AMBIGUITY", "CLARIFICATION_EXHAUSTED"})
        from pcn_appeal.engines.outcome import CUSTOMER_COPY
        self.assertNotIn("CUSTOMER_SEMANTICS_NOT_READY", json.dumps(CUSTOMER_COPY))

    def test_the_account_and_its_history_are_kept_not_deleted(self):
        self.assertEqual(self.case.raw_answers.get("narrative"), ACCOUNT_TEXT)
        packet = U.load_packet(self.case)
        self.assertEqual(packet["status"], U.UNRESOLVED)
        self.assertTrue(packet["open_material_ambiguities"])
        self.assertTrue(packet["uncertainties"])
        self.assertTrue(packet["concepts"], "the model's reading is kept, held")

    def test_the_held_reading_is_not_where_retrieval_looks(self):
        self.assertTrue(_empty(_retrieval_inputs(self.case)[0]))


class AnUnresolvedAccountWithNothingElseInventsNoAppeal(unittest.TestCase):
    """C."""

    @classmethod
    def setUpClass(cls):
        cls.case, cls.out, cls.questions = _run(_unresolved())

    def test_no_ground_and_no_letter(self):
        self.assertEqual(_ids(self.out), [])
        self.assertEqual(len(self.out.draft.paragraphs), 0)
        self.assertEqual(self.out.state, CaseState.NO_SUPPORTED_GROUNDS)

    def test_it_is_held_as_unusable_account_not_as_a_weighed_and_rejected_one(self):
        rows = [a for a in self.case.audit
                if a.get("event") == "analysis_complete_no_supported_grounds"]
        self.assertTrue(rows)
        self.assertIn(rows[-1].get("customer_semantics_not_ready"),
                      {"MATERIAL_AMBIGUITY", "CLARIFICATION_EXHAUSTED"})

    def test_the_customers_account_is_kept_for_the_next_round(self):
        self.assertEqual(self.case.raw_answers.get("narrative"), ACCOUNT_TEXT)
        self.assertEqual(U.load_packet(self.case)["status"], U.UNRESOLVED)


class AnUnassessedAccountIsNeverCertified(unittest.TestCase):
    """D and E."""

    def test_unassessed_with_a_valid_document_ground_blocks_only_the_account(self):
        case, out, _q = _run(_reading(None), LATE_NTK)
        ids = _ids(out)
        self.assertTrue(_pofa(ids), ids)
        self.assertEqual(_customer_derived(ids), [], ids)
        rows = _events(case, "CUSTOMER_SEMANTICS_NOT_READY")
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["reason"], "INVALID_MODEL_RESPONSE")
        self.assertFalse(rows[-1]["ambiguity_assessed"])

    def test_unassessed_with_no_other_ground_is_not_ready_for_account_reasoning(self):
        case, out, _q = _run(_reading(None))
        self.assertEqual(_ids(out), [])
        self.assertIsNotNone(U.customer_stream_blocked(case))
        self.assertTrue(_empty(_retrieval_inputs(case)[0]))

    def test_a_reading_that_never_reached_a_model_is_unassessed_too(self):
        case, out, _q = _run(RuntimeError("provider down"), LATE_NTK)
        ids = _ids(out)
        self.assertTrue(_pofa(ids), ids)
        self.assertEqual(_customer_derived(ids), [], ids)
        self.assertEqual(_events(case, "CUSTOMER_SEMANTICS_NOT_READY")[-1]["reason"],
                         "AMBIGUITY_NOT_ASSESSED")


class AnAccountThatNeedsAClarificationIsAskedNotUsed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.case, cls.out, cls.questions = _run(_ask(), LATE_NTK)

    def test_one_clarification_is_put_to_the_customer(self):
        asked = [q for q in self.questions
                 if str(q.get("fact", "")).startswith(U.FACT_PREFIX)]
        self.assertEqual(len(asked), 1, self.questions)

    def test_the_account_is_not_used_while_the_question_is_open(self):
        self.assertTrue(_empty(_retrieval_inputs(self.case)[0]))
        self.assertEqual(_customer_derived(_ids(self.out)), [], _ids(self.out))
        rows = _events(self.case, "CUSTOMER_SEMANTICS_NOT_READY")
        self.assertEqual(rows[-1]["reason"], "CLARIFICATION_REQUIRED")

    def test_the_independent_ground_is_not_erased_by_the_open_question(self):
        # The late notice is still on the case; nothing about it waits on the answer.
        self.assertTrue(self.case.has("notice_issue_date"))
        self.assertTrue(_pofa(_ids(self.out)), _ids(self.out))


class AnUnreadyAccountPromotesNoFactsFromTheCustomersWords(unittest.TestCase):

    def test_no_customer_fact_is_written_while_not_ready(self):
        for name, reading in (("unresolved", _unresolved()), ("unassessed", _reading(None)),
                              ("asking", _ask())):
            with self.subTest(name):
                case, _out, _q = _run(reading)
                customer = [n for n, f in case.facts.items()
                            if getattr(getattr(f, "source", None), "kind", None) is not None
                            and str(f.source.kind).endswith("CUSTOMER_FREE_TEXT")]
                self.assertEqual(customer, [])
                self.assertFalse(case.has("multiple_visits_stated")
                                 and case.get("multiple_visits_stated"))

    def test_a_ready_account_does_write_them(self):
        case, _out, _q = _run(_reading("UNDERSTOOD"))
        self.assertIsNone(U.customer_stream_blocked(case))


class ThereIsNoGlobalStopOnAnUnreadyAccount(unittest.TestCase):
    """The handoff that gates the Claim Plan is stream-specific."""

    def test_an_unready_account_does_not_hold_the_claim_plan(self):
        from pcn_appeal.semantics.state import handoff_blocks_claim_plan
        for name, reading in (("unresolved", _unresolved()), ("unassessed", _reading(None))):
            with self.subTest(name):
                case, out, _q = _run(reading, LATE_NTK)
                self.assertIsNone(handoff_blocks_claim_plan(case))
                self.assertEqual(_events(case, "held_semantic_handoff"), [])
                self.assertTrue(_pofa(_ids(out)))


if __name__ == "__main__":
    unittest.main()
