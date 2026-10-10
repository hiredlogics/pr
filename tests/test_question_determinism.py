"""Question selection is a function of the case, not of the model.

Same canonical facts + same viable grounds + same missing material facts must always
produce the same next question fact, whatever words, type, order or number of
questions a case-analysis model happened to propose that run.

It did not. Three things let a model's randomness decide what a customer was asked:

  * the candidate it proposed was judged under the TYPE it chose, so the same fact was
    material in one run and "every answer leads to the same result" in the next;
  * its proposals were processed first and the analysis keeps only four, so they took
    the cap's slots and shadowed the KB-gate version of the same fact;
  * between otherwise equal questions, whichever came first won.

Nothing here is about breakdowns, grace periods or any one case. Each family below is a
different allegation and account; the assertion is the same for all of them.
"""
from __future__ import annotations

import unittest
from unittest import mock

import test_scenarios as ts
from support import analysis_llm
from pcn_appeal.engines.question_authority import KB_GATE, MODEL, QuestionAuthority
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import CaseFile, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline

ANSWERS = {"immobilisation_prevented_departure": "yes", "vehicle_immobilised": "yes",
           "recovery_attended": "yes", "immobilisation_cause": "flat battery",
           "exit_delay_min": 47, "permitted_period_ended": "yes", "payment_made": "no"}


def _answer(q: dict):
    fact = q["fact"]
    if fact in ANSWERS:
        return ANSWERS[fact]
    if q.get("type") == "choice" and q.get("options"):
        return q["options"][0]
    return 5 if q.get("type") == "int" else "yes"


def asked(ask: list, breach: str, story: str) -> list[tuple]:
    """The facts the customer is asked, round by round, for one case."""
    fields = dict(ts.BASE, alleged_breach=breach)
    evidence = {"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice ...")}
    llm = analysis_llm({"fields": ts.fields(**fields), "doc_types": {"E1": "PCN"}}, ask=ask)
    case, pipe = CaseFile("C-D", evidence=evidence), None
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    questions = pipe.confirm(case, {}, list(case.facts), story)
    rounds = []
    for _ in range(10):
        if not questions:
            break
        rounds.append(tuple(q["fact"] for q in questions))
        questions = pipe.answer(case, {q["fact"]: _answer(q) for q in questions})
    return rounds


def q(fact, text, type_="text"):
    return {"fact": fact, "text": text, "type": type_}


# What a model might propose for the same case, in different words, types, orders and
# amounts: nothing, a little, the gates' own facts reworded, everything reversed, noise.
MODEL_RUNS = {
    "proposes nothing": [],
    "proposes some, plainly": [q("exit_delay_min", "How long were you delayed?", "int"),
                               q("recovery_attended", "Did a recovery service attend?", "bool")],
    "reworded and retyped": [q("immobilisation_cause", "Was the cause a flat battery?", "bool"),
                             q("exit_delay_min", "Roughly how long did you wait?", "text"),
                             q("multiple_visits", "Did you leave and come back?", "text")],
    "reversed with noise": [q("child_occupant_present", "Were there children with you?", "bool"),
                            q("immobilisation_cause", "Cause?", "text"),
                            q("recovery_attended", "Recovery?", "bool"),
                            q("exit_delay_min", "Minutes?", "int")],
    "four questions of its own": [q("child_occupant_present", "Children present?", "bool"),
                                  q("blue_badge_displayed", "Was a blue badge displayed?", "bool"),
                                  q("loading_activity", "Were you loading?", "bool"),
                                  q("ev_charging_session", "Were you charging?", "bool")],
}

FAMILIES = {
    "an overstay and a breakdown": (
        "Stayed beyond the permitted period",
        "The engine would not turn over and I waited for the recovery truck."),
    "a payment that would not go through": (
        "No valid payment was made for the vehicle's stay",
        "The parking app would not accept my card, I tried several times."),
    "a permit": (
        "Parked without a permit",
        "I live in the flats opposite and use the visitor bays."),
}


class _Probe(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("pcn_appeal.llm.probe", return_value={
            "provider": "test", "models": {"drafting": "reference"}})
        patcher.start()
        self.addCleanup(patcher.stop)


class TheSameCaseIsAskedTheSameQuestionsWhateverTheModelProposes(_Probe):

    def test_a_settled_bay_account_does_not_open_unrelated_model_topics(self):
        breach = "Parked in a Parent and Child bay without being accompanied by a child"
        story = "My two children remained in the vehicle throughout the short stop."
        baseline = asked([], breach, story)
        unrelated = [q("disability_extra_time", "Did you need extra time?", "bool"),
                     q("ev_charging_session", "Were you charging?", "bool"),
                     q("loading_activity", "Were you loading?", "bool")]
        noisy = asked(unrelated, breach, story)
        self.assertEqual(noisy, baseline)
        self.assertFalse({"disability_extra_time", "ev_charging_session", "loading_activity"}
                         & {fact for round_ in noisy for fact in round_})

    def test_every_run_of_the_model_gives_the_same_questions(self):
        for family, (breach, story) in FAMILIES.items():
            runs = {name: asked(ask, breach, story) for name, ask in MODEL_RUNS.items()}
            with self.subTest(family=family):
                self.assertEqual(
                    len({tuple(v) for v in runs.values()}), 1,
                    "\n".join(f"{k}: {v}" for k, v in runs.items()))

    def test_it_is_actually_asking_something(self):
        """An empty sequence for every run would pass the test above for the wrong reason."""
        breach, story = FAMILIES["an overstay and a breakdown"]
        self.assertTrue(asked([], breach, story))


class TheModelsPhrasingIsNotTheQuestion(unittest.TestCase):
    """The authority decides on the KB's own shape of a question."""

    def setUp(self):
        self.kg = KnowledgeGraph()
        self.authority = QuestionAuthority(self.kg)

    def canonical(self, *candidates):
        return self.authority._canonical_candidates(candidates)

    def a_fact_with_a_bank_question(self):
        fact = next(f for f, shape in self.kg.questions.items()
                    if shape.get("type") == "bool" and shape.get("text"))
        return fact, self.kg.questions[fact]

    def test_a_fact_has_the_banks_answer_type_whoever_proposed_it(self):
        """The answer space decides whether an answer could matter, so it is the KB's;
        the wording stays the proposer's (a reworded duplicate is still a duplicate)."""
        fact, shape = self.a_fact_with_a_bank_question()
        out = self.canonical(q(fact, "Some other wording entirely?", "text"))
        self.assertEqual(out[0]["type"], shape["type"])
        self.assertEqual(out[0]["text"], "Some other wording entirely?")

    def test_the_gates_version_is_decided_first_and_the_models_repeat_has_the_same_type(self):
        fact, shape = self.a_fact_with_a_bank_question()
        gate = dict(q(fact, shape["text"], "bool"), source=KB_GATE, notice_pointed=True)
        model = dict(q(fact, "Reworded?", "text"), source=MODEL)
        for order in ((model, gate), (gate, model)):
            out = self.canonical(*order)
            self.assertEqual([c["source"] for c in out], [KB_GATE, MODEL])
            self.assertTrue(out[0].get("notice_pointed"))
            self.assertEqual({c["type"] for c in out}, {shape["type"]})

    def test_the_sources_come_in_a_fixed_order_whatever_order_they_arrive_in(self):
        facts = list(self.kg.questions)[:6]
        mixed = [dict(q(f, f"about {f}?"), source=src)
                 for f, src in zip(facts, (MODEL, KB_GATE, MODEL, KB_GATE, MODEL, KB_GATE))]
        for arrival in (mixed, list(reversed(mixed))):
            sources = [c["source"] for c in self.canonical(*arrival)]
            self.assertEqual(sources, [KB_GATE] * 3 + [MODEL] * 3)

    def test_a_fact_the_bank_has_no_question_for_keeps_the_proposers_shape(self):
        out = self.canonical(dict(q("a_fact_nobody_modelled", "A new thing?", "bool"), source=MODEL))
        self.assertEqual((out[0]["text"], out[0]["type"]), ("A new thing?", "bool"))

    def test_integrity_questions_are_never_rewritten_or_dropped_as_duplicates(self):
        fact, shape = self.a_fact_with_a_bank_question()
        conflict = dict(q(fact, "Your notice says X but you said Y - which is right?", "text"),
                        source="document_conflict")
        out = self.canonical(conflict, dict(q(fact, "Reworded?", "text"), source=MODEL))
        self.assertEqual(out[0]["text"], conflict["text"])


if __name__ == "__main__":
    unittest.main()
