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

    def test_a_material_ambiguity_with_no_question_is_unresolved_not_understood(self):
        case = _case()
        _resolve(case, _reply("NEEDS_CLARIFICATION", None))
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["ready_for_knowledge"]), ("UNRESOLVED", False))
        self.assertTrue(any("not asked" in n for n in p["notes"]))

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
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["ready_for_knowledge"]), ("UNRESOLVED", False))


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
        self.assertEqual((p["status"], p["clarification"]), ("UNRESOLVED", None))
        self.assertFalse(p["ready_for_knowledge"])
        self.assertTrue(any("already asked" in n for n in p["notes"]))

    def test_a_reworded_question_is_the_same_question(self):
        case = self._answered(_ask("Was it the ticket or the card payment you meant?",
                                   "which thing 'it' refers to"))
        p = U.load_packet(case)
        self.assertEqual((p["status"], p["ready_for_knowledge"]), ("UNRESOLVED", False))

    def test_a_new_ambiguity_may_be_asked_once_more(self):
        case = self._answered(_ask("What time did the doctor's appointment finish?",
                                   "the end time of the appointment is not stated"))
        p = U.load_packet(case)
        self.assertEqual(p["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(p["clarification"]["fact"], "account_clarification_2")


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
        self.assertEqual((p["status"], p["clarification"]), ("UNRESOLVED", None))
        self.assertFalse(p["ready_for_knowledge"])
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


def _journey(*replies, answers=()):
    """Resolve once, then answer and resolve again for each answer given."""
    case = _case("My mum said she would sort it.")
    llm = ReferenceAnalysisLLM({"semantic_extraction": list(replies)})
    pipe = AppealPipeline(llm)
    case.ensure_run("test")
    run = lambda: SemanticCaseResolver.resolve(
        case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
    run()
    packets = [dict(U.load_packet(case))]
    for n, answer in enumerate(answers, 1):
        pipe.questions.record_answer(case, f"account_clarification_{n}", answer)
        run()
        packets.append(dict(U.load_packet(case)))
    return case, llm, pipe, packets


class ReadinessContract(unittest.TestCase):
    """A question limit stops a loop. It never certifies an account."""

    def test_A_material_ambiguity_with_a_valid_question_needs_clarification(self):
        case, *_ , packets = _journey(_ask())
        p = packets[0]
        self.assertEqual((p["status"], p["ambiguity"], p["ready_for_knowledge"]),
                         ("NEEDS_CLARIFICATION", "MATERIAL", False))
        self.assertEqual(len(p["open_material_ambiguities"]), 1)
        self.assertFalse(U.ready_for_knowledge(case))

    def test_B_material_ambiguity_with_a_vetoed_question_is_unresolved(self):
        for q in ("Who was driving the car?", "Was the notice compliant with PoFA?",
                  "Does KB-POFA-02 apply here?"):
            case, *_ , packets = _journey(_ask(q, "what 'it' refers to is not stated"))
            p = packets[0]
            self.assertEqual((p["status"], p["ambiguity"], p["ready_for_knowledge"]),
                             ("UNRESOLVED", "MATERIAL", False), q)
            self.assertIsNone(p["clarification"])
            self.assertEqual(U.pending_question(case), [])
            self.assertFalse(U.ready_for_knowledge(case))
            self.assertIn("what 'it' refers to is not stated",
                          [u["about"] for u in p["uncertainties"]])

    def test_C_material_ambiguity_at_the_question_limit_is_unresolved(self):
        case, *_ , packets = _journey(
            _ask(),
            _ask("What time did the doctor's appointment finish?", "the end time is not stated"),
            _ask("Where did the appointment take place exactly?", "the place is not stated"),
            answers=("The phone payment.", "About noon."))
        self.assertEqual([p["status"] for p in packets],
                         ["NEEDS_CLARIFICATION", "NEEDS_CLARIFICATION", "UNRESOLVED"])
        self.assertEqual([p["ready_for_knowledge"] for p in packets], [False, False, False])
        self.assertTrue(any("cap" in n for n in packets[-1]["notes"]))
        self.assertTrue(packets[-1]["open_material_ambiguities"])

    def test_D_a_non_material_ambiguity_may_be_understood_and_is_preserved(self):
        case, *_ , packets = _journey(_reply(uncertainties=[
            {"about": "the exact minute the customer returned", "source_text": "around then"}]))
        p = packets[0]
        self.assertEqual((p["status"], p["ambiguity"], p["ready_for_knowledge"]),
                         ("UNDERSTOOD", "NON_MATERIAL", True))
        self.assertEqual(p["open_material_ambiguities"], [])
        self.assertEqual(p["uncertainties"][0]["about"], "the exact minute the customer returned")
        self.assertTrue(U.ready_for_knowledge(case))

    def test_E_an_answered_clarification_that_settles_it_is_understood(self):
        case, *_ , packets = _journey(_ask(), _reply(), answers=("The phone payment.",))
        self.assertEqual([p["status"] for p in packets], ["NEEDS_CLARIFICATION", "UNDERSTOOD"])
        self.assertEqual([p["ready_for_knowledge"] for p in packets], [False, True])
        self.assertEqual(packets[1]["open_material_ambiguities"], [])
        self.assertTrue(U.ready_for_knowledge(case))

    def test_F_an_answer_that_does_not_settle_it_is_never_ready(self):
        # The customer could not say, and the model reads the account as understood.
        for answer in ("not sure", "I don't know", "no idea", "can't remember"):
            case, *_ , packets = _journey(_ask(), _reply(), answers=(answer,))
            p = packets[1]
            self.assertEqual((p["status"], p["ready_for_knowledge"]), ("UNRESOLVED", False), answer)
            self.assertTrue(p["open_material_ambiguities"], answer)
            self.assertFalse(U.ready_for_knowledge(case))

    def test_F_an_answer_that_opens_a_new_ambiguity_asks_once_more_but_is_not_ready(self):
        case, *_ , packets = _journey(
            _ask(), _ask("What time did the doctor's appointment finish?", "end time not stated"),
            answers=("not sure",))
        p = packets[1]
        self.assertEqual((p["status"], p["ready_for_knowledge"]), ("NEEDS_CLARIFICATION", False))
        self.assertEqual(len(p["open_material_ambiguities"]), 2)

    def test_F_a_model_that_repeats_itself_after_an_answer_leaves_it_unresolved(self):
        case, *_ , packets = _journey(_ask(), _ask(), answers=("The phone payment.",))
        self.assertEqual((packets[1]["status"], packets[1]["ready_for_knowledge"]),
                         ("UNRESOLVED", False))

    def test_G_replay_gives_the_same_status_and_one_question(self):
        for first, expect in ((_ask(), "NEEDS_CLARIFICATION"),
                              (_ask("Who was driving the car?", "unclear"), "UNRESOLVED")):
            case = _case("My mum said she would sort it.")
            llm = ReferenceAnalysisLLM({"semantic_extraction": [first, _reply()]})
            pipe = AppealPipeline(llm)
            case.ensure_run("test")
            seen = []
            for _ in range(3):
                SemanticCaseResolver.resolve(case, llm=pipe.llm,
                                             narrative=case.raw_answers["narrative"])
                p = U.load_packet(case)
                seen.append((p["status"], p["ready_for_knowledge"],
                             [q["fact"] for q in U.pending_question(case)]))
            self.assertEqual(len(set(map(repr, seen))), 1, seen)
            self.assertEqual(seen[0][0], expect)
            self.assertFalse(seen[0][1])
            self.assertEqual(sum(c["task"] == "semantic_extraction" for c in llm.calls), 1)
            self.assertEqual(len(seen[0][2]), 1 if expect == "NEEDS_CLARIFICATION" else 0)

    def test_a_model_that_reports_unresolved_is_not_ready(self):
        case, *_ , packets = _journey(_reply("UNRESOLVED", None))
        self.assertEqual((packets[0]["status"], packets[0]["ready_for_knowledge"]),
                         ("UNRESOLVED", False))

    def test_a_stored_ready_flag_is_never_trusted(self):
        case, *_ , packets = _journey(_ask("Who was driving?", "unclear"))
        state = json.loads(case.raw_answers[U.STATE_KEY])
        state["packet"]["ready_for_knowledge"] = True       # a corrupted or hand-edited record
        case.raw_answers[U.STATE_KEY] = json.dumps(state)
        self.assertFalse(U.ready_for_knowledge(case))

    def test_no_input_whatever_makes_an_open_material_ambiguity_ready(self):
        """Exhaustive over the product the model can hand back: every status, with
        and without a question, a repeat, after the cap, after a non-answer."""
        questions = (None, {"question": "Which payment did you mean?", "ambiguity": "which payment"},
                     {"question": "Who was driving?", "ambiguity": "driver"},
                     {"question": "", "ambiguity": ""})
        histories = ([], [{"fact": "a", "question": "Which payment did you mean?",
                           "ambiguity": "which payment", "answer": "The phone one."}],
                     [{"fact": "a", "question": "x one", "ambiguity": "x", "answer": "not sure"}],
                     [{"fact": "a", "question": "q one", "ambiguity": "q", "answer": "yes"},
                      {"fact": "b", "question": "r two", "ambiguity": "r", "answer": "ok"}])
        for status in ("UNDERSTOOD", "NEEDS_CLARIFICATION", "UNRESOLVED", "", "nonsense", None):
            for clar in questions:
                for hist in histories:
                    for mode in ("LIVE", "FALLBACK"):
                        p = U.build_packet({"status": status, "clarification": clar,
                                            "semantic_mode": mode}, hist)
                        if p["open_material_ambiguities"] or p["status"] != "UNDERSTOOD":
                            self.assertFalse(p["ready_for_knowledge"], (status, clar, hist, mode, p))
                        if p["ready_for_knowledge"]:
                            self.assertEqual(p["status"], "UNDERSTOOD")
                            self.assertEqual(p["open_material_ambiguities"], [])
                        if status in ("NEEDS_CLARIFICATION", "UNRESOLVED"):
                            self.assertFalse(p["ready_for_knowledge"], (status, clar, hist, mode))


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
