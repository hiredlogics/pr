"""Phase 2: customer free text -> one understood semantic packet, or one question.

The model here is scripted. These tests pin what code owns: the packet's shape,
what the model is shown, which clarifications may be asked, that an answer is
read together with the account, that nothing is asked twice, and that a model's
reading is never overruled by a phrase pattern. They say nothing about how well
a real model understands a sentence; `python -m pcn_appeal.eval.understanding`
measures that, and only with a working provider.

Run:  PYTHONPATH=.:tests python -m unittest discover -s tests -p test_customer_understanding.py -v
"""
from __future__ import annotations

import json
import unittest

from pcn_appeal.models import (
    CaseFile, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.semantics import understanding as U
from pcn_appeal.semantics.resolver import SemanticCaseResolver
from support import ReferenceAnalysisLLM

NOTICE = {
    "operator_name": "Northgate Parking Ltd", "pcn_number": "00112233",
    "vrm": "AB12CDE", "parking_location": "Retail Park",
    "alleged_breach": "Overstayed paid time",
}
ACCOUNT = "I paid at the machine but my son was ill so we stayed longer."


def _case(narrative=ACCOUNT):
    case = CaseFile("C-und")
    for name, value in NOTICE.items():
        case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
    case.raw_answers["narrative"] = narrative
    return case


def _reply(status="UNDERSTOOD", clarification=None, **kw):
    out = {"status": status, "summary": "The customer paid and stayed longer.",
           "concepts": [], "events": [], "narrative_atoms": [], "relationships": [],
           "material_relevance": [], "uncertainties": [], "clarification": clarification}
    out.update(kw)
    return out


def _ask(q="Which of these did you mean: the ticket or the card payment?",
         amb="'it' could be the ticket or the payment"):
    return _reply("NEEDS_CLARIFICATION", {"question": q, "ambiguity": amb})


def _resolve(case, *replies, llm=None):
    llm = llm or ReferenceAnalysisLLM({"semantic_extraction": list(replies)})
    pipe = AppealPipeline(llm)
    case.ensure_run("test")
    SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
    return llm, pipe


def _sent(llm, n=-1):
    calls = [c for c in llm.calls if c["task"] == "semantic_extraction"]
    return json.loads(calls[n]["user"])


class PacketShape(unittest.TestCase):

    def test_an_understood_account_yields_a_ready_packet(self):
        case = _case()
        _resolve(case, _reply())
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["ready_for_knowledge"], p["clarification"]),
                         ("UNDERSTOOD", True, None))
        for channel in U.PACKET_CHANNELS:
            self.assertIn(channel, p)
        self.assertTrue(p["summary"])

    def test_the_model_sees_the_notice_context_and_no_knowledge_base_ids(self):
        case = _case()
        llm, _ = _resolve(case, _reply())
        sent = _sent(llm)
        self.assertEqual(sent["notice_allegation"], "Overstayed paid time")
        self.assertEqual(sent["confirmed_facts"]["pcn_number"], "00112233")
        self.assertEqual(sent["customer_texts"], [ACCOUNT])
        self.assertNotIn("KB-", json.dumps(sent))
        self.assertNotIn("module_id", json.dumps(sent).lower())

    def test_the_packet_carries_no_legal_decision(self):
        case = _case()
        _resolve(case, _reply())
        blob = json.dumps(U.load_packet(case))
        self.assertNotIn("KB-", blob)
        self.assertNotIn("claim_plan", blob)

    def test_without_a_model_the_account_stands_and_says_it_was_not_assessed(self):
        case = _case()
        pipe = AppealPipeline(None)
        case.ensure_run("test")
        SemanticCaseResolver.resolve(case, llm=None, narrative=ACCOUNT)
        p = U.load_packet(case)
        self.assertEqual(p["status"], "UNDERSTOOD")
        self.assertEqual(p["semantic_mode"], "FALLBACK")
        self.assertTrue(any("not assessed" in n for n in p["notes"]), p["notes"])


class OneClarificationOnlyWhenGenuinelyAmbiguous(unittest.TestCase):

    def test_one_question_is_asked_for_a_genuine_ambiguity(self):
        case = _case("My mum said she would sort it.")
        _resolve(case, _ask())
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["ready_for_knowledge"]), ("NEEDS_CLARIFICATION", False))
        self.assertEqual(set(p["clarification"]), {"question", "ambiguity", "fact"})
        q = U.pending_question(case)
        self.assertEqual(len(q), 1)
        self.assertEqual((q[0]["type"], q[0]["fact"]), ("text", "account_clarification_1"))

    def test_a_clarification_sent_with_understood_is_dropped(self):
        case = _case()
        _resolve(case, _reply("UNDERSTOOD", {"question": "More detail?", "ambiguity": "x"}))
        p = U.load_packet(case)
        self.assertIsNone(p["clarification"])
        self.assertEqual(U.pending_question(case), [])
        self.assertTrue(any("dropped" in n for n in p["notes"]))

    def test_needs_clarification_without_a_question_is_read_as_understood(self):
        case = _case()
        _resolve(case, _reply("NEEDS_CLARIFICATION", None))
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["ready_for_knowledge"]), ("UNDERSTOOD", True))
        self.assertTrue(any("not asked" in n for n in p["notes"]))

    def test_an_unaskable_ambiguity_is_kept_as_an_uncertainty(self):
        case = _case()
        _resolve(case, _ask("Who was driving the car?", "it is unclear who drove"))
        p = U.load_packet(case)
        self.assertEqual(p["status"], "UNDERSTOOD")
        self.assertIn("it is unclear who drove", [u["about"] for u in p["uncertainties"]])

    def test_a_question_about_the_driver_is_never_asked(self):
        for q in ("Who was driving?", "Were you driving at the time?",
                  "What is the name of the driver?"):
            self.assertIsNotNone(U._banned_reason(q), q)

    def test_a_legal_or_internal_question_is_never_asked(self):
        for q in ("Was the notice compliant with PoFA?",
                  "Does KB-POFA-02 apply to your case?",
                  "Was the charge legally enforceable?"):
            self.assertIsNotNone(U._banned_reason(q), q)

    def test_a_plain_contextual_question_is_allowed(self):
        self.assertIsNone(U._banned_reason("Did the payment you mention go through?"))


class AnswerIsReadWithTheAccount(unittest.TestCase):

    def _asked(self):
        case = _case("My mum said she would sort it.")
        llm = ReferenceAnalysisLLM({"semantic_extraction": [_ask(), _reply()]})
        pipe = AppealPipeline(llm)
        case.ensure_run("test")
        SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        return case, llm, pipe

    def test_the_answer_and_the_original_account_are_sent_together(self):
        case, llm, pipe = self._asked()
        pipe.questions.record_answer(case, "account_clarification_1", "The phone payment.")
        SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        sent = _sent(llm)
        self.assertEqual(sent["customer_texts"], ["My mum said she would sort it."])
        self.assertEqual(sent["clarification_history"][0]["answer"], "The phone payment.")
        self.assertIn("payment", sent["clarification_history"][0]["question"])

    def test_an_answered_clarification_resolves_and_is_not_pending(self):
        case, llm, pipe = self._asked()
        pipe.questions.record_answer(case, "account_clarification_1", "The phone payment.")
        SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["clarification_rounds"]), ("UNDERSTOOD", 1))
        self.assertEqual(U.pending_question(case), [])

    def test_the_answer_is_not_stored_as_a_fact(self):
        case, llm, pipe = self._asked()
        pipe.questions.record_answer(case, "account_clarification_1", "yes the phone one")
        self.assertFalse(case.has("account_clarification_1"))

    def test_a_not_sure_answer_still_closes_the_round(self):
        case, llm, pipe = self._asked()
        pipe.questions.record_answer(case, "account_clarification_1", "not sure")
        SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        self.assertEqual(_sent(llm)["clarification_history"][0]["answer"], "not sure")
        self.assertEqual(U.load_packet(case)["status"], "UNDERSTOOD")


class NothingIsAskedTwice(unittest.TestCase):

    def _answered(self, *more):
        case = _case("My mum said she would sort it.")
        llm = ReferenceAnalysisLLM({"semantic_extraction": [_ask(), *more]})
        pipe = AppealPipeline(llm)
        case.ensure_run("test")
        SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        pipe.questions.record_answer(case, "account_clarification_1", "The phone payment.")
        SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        return case

    def test_the_same_question_again_is_refused(self):
        case = self._answered(_ask())
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["clarification"]), ("UNDERSTOOD", None))
        self.assertTrue(any("already asked" in n for n in p["notes"]))

    def test_a_reworded_question_is_the_same_question(self):
        case = self._answered(_ask("Was it the ticket or the card payment you meant?",
                                   "which thing 'it' refers to"))
        self.assertEqual(U.load_packet(case)["status"], "UNDERSTOOD")

    def test_a_new_ambiguity_may_be_asked_once_more(self):
        case = self._answered(_ask("What time did the doctor's appointment finish?",
                                   "the end time of the appointment is not stated"))
        p = U.load_packet(case)
        self.assertEqual(p["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(p["clarification"]["fact"], "account_clarification_2")

    def test_a_third_question_is_never_asked(self):
        case = self._answered(_ask("What time did the doctor's appointment finish?",
                                   "the end time is not stated"), _reply(), _ask(
            "Where did the appointment take place exactly?", "the place is not stated"))
        self.assertEqual(U.load_packet(case)["status"], "NEEDS_CLARIFICATION")

    def test_the_cap_ends_clarification(self):
        case = _case("My mum said she would sort it.")
        pipe = AppealPipeline(ReferenceAnalysisLLM({"semantic_extraction": [
            _ask(), _ask("What time did the doctor's appointment finish?", "end time not stated"),
            _ask("Where did the appointment take place exactly?", "place not stated")]}))
        case.ensure_run("test")
        run = lambda: SemanticCaseResolver.resolve(
            case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
        run()
        pipe.questions.record_answer(case, "account_clarification_1", "The phone payment.")
        run()
        pipe.questions.record_answer(case, "account_clarification_2", "About noon.")
        run()
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["clarification"]), ("UNDERSTOOD", None))
        self.assertTrue(any("cap" in n for n in p["notes"]))

    def test_the_same_input_is_read_once_and_asks_the_same_question(self):
        case = _case("My mum said she would sort it.")
        llm = ReferenceAnalysisLLM({"semantic_extraction": [_ask(), _reply()]})
        pipe = AppealPipeline(llm)
        case.ensure_run("test")
        for _ in range(3):
            SemanticCaseResolver.resolve(case, llm=pipe.llm,
                                         narrative=case.raw_answers["narrative"])
        self.assertEqual(sum(c["task"] == "semantic_extraction" for c in llm.calls), 1)
        q = U.pending_question(case)
        self.assertEqual([x["fact"] for x in q], ["account_clarification_1"])


class SpecificMeaningSurvives(unittest.TestCase):

    def test_a_specific_event_and_a_negation_reach_the_packet_unchanged(self):
        case = _case("A swan blocked the exit lane. I did not leave the site.")
        _resolve(case, _reply(
            events=[{"event_id": "E1", "event_type": "ACCESS_ISSUE",
                     "description": "A swan blocked the exit lane",
                     "polarity": "AFFIRMED", "source_text": "A swan blocked the exit lane"},
                    {"event_id": "E2", "event_type": "DEPARTURE",
                     "description": "The vehicle did not leave the site",
                     "polarity": "NEGATED", "source_text": "I did not leave the site"}],
            narrative_atoms=[{"atom_id": "A1", "category": "access",
                              "proposition": "A swan blocked the exit lane",
                              "polarity": "AFFIRMED", "source_text": "swan"}],
            relationships=[{"source_id": "E1", "relationship": "CAUSES", "target_id": "E2"}],
            uncertainties=[{"about": "how long the exit was blocked", "source_text": ""}]))
        p = U.load_packet(case)
        self.assertIn("swan", json.dumps(p["events"]).lower())
        self.assertEqual([e["polarity"] for e in p["events"]], ["AFFIRMED", "NEGATED"])
        self.assertEqual(len(p["relationships"]), 1)
        self.assertEqual(p["uncertainties"][0]["about"], "how long the exit was blocked")
        self.assertIn("swan", json.dumps(p["narrative_atoms"]).lower())

    def test_a_phrase_pattern_cannot_overrule_the_models_reading(self):
        case = _case("I paid, I think, but I am not sure the card went through.")
        _resolve(case, _reply(concepts=[{
            "concept": "PAYMENT_MADE", "polarity": "UNCERTAIN", "attribution": "CUSTOMER",
            "source_text": "I think", "confidence": 0.8}]))
        by = {c["concept"]: c["polarity"] for c in U.load_packet(case)["concepts"]}
        self.assertEqual(by["PAYMENT_MADE"], "UNCERTAIN")

    def test_the_phrase_floor_still_covers_a_read_that_found_nothing(self):
        case = _case("I paid at the machine.")
        _resolve(case, _reply())          # a model that returned no meaning at all
        by = {c["concept"] for c in U.load_packet(case)["concepts"]}
        self.assertIn("PAYMENT_MADE", by)


class ClarificationReachesTheCustomerThroughTheAuthority(unittest.TestCase):

    def test_reanalyse_asks_the_clarification_first_and_only_it(self):
        case = _case("My mum said she would sort it.")
        llm = ReferenceAnalysisLLM({"semantic_extraction": [_ask()]})
        pipe = AppealPipeline(llm)
        case.ensure_run("test")
        out = pipe._reanalyse(case, case.raw_answers["narrative"])
        self.assertEqual([q["fact"] for q in out], ["account_clarification_1"])
        self.assertEqual(set(out[0]), {"fact", "text", "type"})

    def test_after_the_answer_it_is_not_asked_again(self):
        case = _case("My mum said she would sort it.")
        llm = ReferenceAnalysisLLM({"semantic_extraction": [_ask(), _ask()]})
        pipe = AppealPipeline(llm)
        case.ensure_run("test")
        pipe._reanalyse(case, case.raw_answers["narrative"])
        out = pipe.answer(case, {"account_clarification_1": "The phone payment."})
        self.assertNotIn("account_clarification_1", [q["fact"] for q in out])
        self.assertNotIn("account_clarification_2", [q["fact"] for q in out])


if __name__ == "__main__":
    unittest.main()
