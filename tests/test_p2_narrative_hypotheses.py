"""P2: narrative hypotheses are kept apart from facts.

A hypothesis read from the customer's account may ask one question. It never
selects a ground, enters drafting or passes validation; only the customer's
answer, written to the Fact Graph, can do that.

Spec tests 1-5 first, then the guarantees behind them. All text is synthetic
and generic.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import sqlite_store
from pcn_appeal import customer_safe
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.narrative import NARRATIVE_FACTS, read
from pcn_appeal.hypotheses import (CONFIRMED, KINDS, REJECTED, SUPERSEDED, UNCONFIRMED,
                                   WITHDRAWN, Hypotheses)
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.rules.dsl import evaluate
from pcn_appeal.store import cases as store
from test_no_weak_fallback import LATE, case_with
from test_question_authority import run

KG = KnowledgeGraph()
ANPR = KG.modules["KB-ANPR-01"]
SHOPPING = "I went shopping at the supermarket, forgot my purse, left and came back."


def account(text: str, case: CaseFile | None = None) -> CaseFile:
    case = case or CaseFile("C-N")
    case.raw_answers["narrative"] = text
    assess_material_account(case)
    return case


def anpr_eligible(case: CaseFile) -> bool:
    facts = case.fact_view()
    return evaluate(ANPR.use_when, facts) and not evaluate(ANPR.do_not_use_when, facts)


def pipeline(narrative: str = SHOPPING):
    case, pipe = case_with(extra=LATE)
    _, questions = run(case, pipe, narrative)
    return case, pipe, questions


# ------------------------------------------------------------------ spec tests
class SpecTests(unittest.TestCase):

    def test_1_left_and_came_back_is_a_hypothesis_with_a_question_and_no_ground(self):
        case, pipe, questions = pipeline("I left and came back.")
        [h] = case.fact_hypotheses
        self.assertEqual((h["hypothesis"], h["possible_value"], h["status"]),
                         ("possible_multiple_visits", True, UNCONFIRMED))
        self.assertEqual(h["source_text"], "I left and came back.")
        self.assertNotIn("multiple_visits", case.facts)              # not a fact
        q = next(q for q in questions if q["fact"] == "multiple_visits")
        self.assertEqual(q["text"], "Did the vehicle leave the car park and return later that day?")
        self.assertFalse(anpr_eligible(case))
        out = pipe.generate(case)
        self.assertNotIn("KB-ANPR-01", out.pack.module_ids)

    def test_2_yes_creates_the_fact_and_allows_the_anpr_relationship(self):
        case, pipe, _ = pipeline()
        pipe.answer(case, {"multiple_visits": "yes"})
        f = case.facts["multiple_visits"]
        self.assertEqual((f.value, f.status, f.source.kind),
                         (True, FactStatus.ANSWERED, SourceKind.ANSWER))
        self.assertEqual(case.fact_hypotheses[0]["status"], CONFIRMED)
        self.assertTrue(anpr_eligible(case))
        out = pipe.generate(case)
        self.assertIn("KB-ANPR-01", out.pack.module_ids)

    def test_3_no_creates_the_false_fact_and_no_multiple_visit_ground(self):
        case, pipe, _ = pipeline()
        pipe.answer(case, {"multiple_visits": "no"})
        self.assertIs(case.facts["multiple_visits"].value, False)
        self.assertEqual(case.fact_hypotheses[0]["status"], REJECTED)
        self.assertFalse(anpr_eligible(case))
        self.assertNotIn("KB-ANPR-01", pipe.generate(case).pack.module_ids)

    def test_3b_no_answer_keeps_the_hypothesis_only(self):
        case, pipe, _ = pipeline()
        out = pipe.generate(case)
        self.assertEqual(case.fact_hypotheses[0]["status"], UNCONFIRMED)
        self.assertNotIn("multiple_visits", case.facts)
        self.assertNotIn("KB-ANPR-01", out.pack.module_ids)

    def test_4_walking_back_to_the_car_is_not_a_vehicle_visit(self):
        for text in ("I walked back to the car to get my purse.",
                     "I left the shop, walked back to the car to get my purse and came back",
                     "I went back to the car and then back into the store",
                     "I left the store for a minute and came back in"):
            with self.subTest(text=text):
                case = account(text)
                self.assertEqual(case.fact_hypotheses, [])
                self.assertNotIn("multiple_visits", case.facts)
                self.assertNotIn("possible_vehicle_departure", case.facts)

    def test_5_the_same_narrative_gives_the_same_hypothesis(self):
        case = account(SHOPPING)
        first = ([dict(h) for h in case.fact_hypotheses],
                 {n: case.facts[n].value for n in case.facts})
        for _ in range(3):
            assess_material_account(case)                            # re-read each round
        case.raw_answers["other"] = SHOPPING                         # same words, twice
        assess_material_account(case)
        self.assertEqual(len(case.fact_hypotheses), 1)
        self.assertEqual(case.fact_hypotheses[0]["hypothesis_id"], first[0][0]["hypothesis_id"])
        self.assertEqual({k: v for k, v in case.fact_hypotheses[0].items() if k != "updated_at"},
                         {k: v for k, v in first[0][0].items() if k != "updated_at"})
        self.assertEqual({n: case.facts[n].value for n in case.facts}, first[1])
        self.assertEqual(read(SHOPPING).facts, read(SHOPPING).facts)
        self.assertEqual(read(SHOPPING).hypothesis, read(SHOPPING).hypothesis)


# ------------------------------------------------------------------ atomic facts
class AtomicFacts(unittest.TestCase):

    def test_the_spec_narrative(self):
        case = account(SHOPPING)
        facts = {n: case.facts[n].value for n in NARRATIVE_FACTS if n in case.facts}
        self.assertEqual(facts, {"visited_premises": True, "purpose_of_visit": "shopping",
                                 "left_site": True, "returned_same_day": True,
                                 "possible_vehicle_departure": True})
        for n in facts:
            self.assertEqual(case.facts[n].source.kind, SourceKind.CUSTOMER_FREE_TEXT)
            self.assertEqual(case.facts[n].status, FactStatus.DERIVED)

    def test_names_are_generic(self):
        case = account("I went to a well-known supermarket chain, forgot my purse, "
                       "left and came back.")
        named = set(case.facts) - {"account_contradicts_allegation",
                                   "material_account_propositions",
                                   "material_account_proposition"}
        self.assertLessEqual(named, NARRATIVE_FACTS | {"customer_described_event"})
        src = "".join(Path(p).read_text() for p in ("pcn_appeal/engines/narrative.py",
                                                     "pcn_appeal/hypotheses.py"))
        self.assertNotRegex(src, re.compile(r"sainsbury|tesco|asda|aldi|lidl|morrisons", re.I))

    def test_atomic_facts_are_not_letter_content_or_questions(self):
        from pcn_appeal.engines.analysis import is_internal_fact
        for n in NARRATIVE_FACTS:
            self.assertTrue(is_internal_fact(n), n)


# ------------------------------------------------------------------ negation (P2.7)
class Negation(unittest.TestCase):

    def test_a_denial_creates_no_hypothesis(self):
        for text in ("I did not leave and come back.", "I didn't leave and come back",
                     "I never left the car park", "We did not drive off and return",
                     "At no point did I leave. I did not come back later either."):
            with self.subTest(text=text):
                self.assertIsNone(read(text).hypothesis)
                self.assertEqual(account(text).fact_hypotheses, [])

    def test_negation_does_not_reach_back_into_an_earlier_clause(self):
        self.assertIsNotNone(read("I left and came back, not realising the time").hypothesis)

    def test_a_return_another_day_is_not_a_second_visit(self):
        self.assertIsNone(read("I left and came back the next day").hypothesis)

    def test_explicit_and_vehicle_accounts_are_more_confident(self):
        self.assertEqual(read("I drove home and came back later").hypothesis["confidence"], 0.8)
        self.assertEqual(read("There were two separate visits").hypothesis["confidence"], 0.8)
        self.assertEqual(read("I left and came back").hypothesis["confidence"], 0.5)


# ------------------------------------------------------------------ separation
class HypothesesAreNotFacts(unittest.TestCase):

    def test_a_hypothesis_reaches_no_fact_view_pack_or_drafter(self):
        case, pipe, _ = pipeline()
        self.assertNotIn("multiple_visits", case.fact_view())
        self.assertNotIn("possible_multiple_visits", case.fact_view())
        out = pipe.generate(case)
        self.assertNotIn("multiple_visits", out.pack.verified_facts)
        self.assertFalse(any("more than once" in str(v) for v in out.pack.verified_facts.values()))
        self.assertNotIn("more than once", out.letter or "")

    def test_a_hypothesis_never_overrides_a_confirmed_fact(self):
        case = CaseFile("C-O")
        case.put(Fact("F-multiple_visits", "multiple_visits", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        account("I left and came back.", case)
        self.assertIs(case.get("multiple_visits"), False)
        self.assertEqual(case.fact_hypotheses[0]["status"], SUPERSEDED)
        self.assertEqual(Hypotheses.questions(case, lambda f: True), [])

    def test_a_document_or_evidence_value_supersedes_it(self):
        case = account("I left and came back.")
        case.put(Fact("F-multiple_visits", "multiple_visits", True, FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E2#p1")))
        self.assertEqual(case.fact_hypotheses[0]["status"], SUPERSEDED)

    def test_free_text_never_settles_a_hypothesis(self):
        case = account("I left and came back.")
        case.put(Fact("F-multiple_visits", "multiple_visits", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:multiple_visits")))
        self.assertEqual(case.fact_hypotheses[0]["status"], UNCONFIRMED)

    def test_rewriting_the_account_withdraws_it(self):
        case = account("I left and came back.")
        account("I stayed the whole time.", case)
        self.assertEqual(case.fact_hypotheses[0]["status"], WITHDRAWN)
        account("I left and came back.", case)
        self.assertEqual(case.fact_hypotheses[0]["status"], UNCONFIRMED)
        self.assertEqual(len(case.fact_hypotheses), 1)

    def test_it_is_asked_once(self):
        case, pipe, _ = pipeline()
        again = pipe.answer(case, {})                               # skipped
        self.assertNotIn("multiple_visits", [q["fact"] for q in (again or [])])
        self.assertEqual(case.fact_hypotheses[0]["status"], UNCONFIRMED)

    def test_not_asked_when_no_ground_depends_on_it(self):
        case = account("I left and came back.")
        self.assertEqual(Hypotheses.questions(case, lambda f: False), [])


# ------------------------------------------------------------------ questions (P2.6)
class QuestionShape(unittest.TestCase):

    def test_internal_fields_are_present_and_never_shown(self):
        # P3: the candidate carries the hypothesis' reason and impact; the
        # question returned (and pending) is only what the customer answers.
        case = account(SHOPPING)
        [q] = Hypotheses.questions(case, lambda f: True)
        self.assertEqual((q["target_fact"], q["reason"], q["possible_impact"]),
                         ("multiple_visits", "Could change ANPR interpretation",
                          "Determines whether continuous stay is valid"))
        case, _, questions = pipeline()
        shown = next(q for q in questions if q["fact"] == "multiple_visits")
        self.assertEqual(set(shown), {"fact", "text", "type"})
        self.assertEqual(customer_safe.leaks(shown), [])

    def test_no_internal_ids_in_any_wording(self):
        for kind in KINDS.values():
            for text in (kind.question, kind.reason, kind.possible_impact):
                self.assertEqual(customer_safe.internal_ids(text), [], text)


# ------------------------------------------------------------------ trace and store
class TraceAndReload(unittest.TestCase):

    def test_admin_trace_shows_the_whole_chain(self):
        case, pipe, _ = pipeline("I left and came back")
        pipe.answer(case, {"multiple_visits": "yes"})
        [row] = Hypotheses.trace(case)
        self.assertEqual(row["narrative"], "I left and came back")
        self.assertEqual(row["hypothesis"], "possible_multiple_visits=true")
        self.assertEqual(row["question_asked"],
                         "Did the vehicle leave the car park and return later that day?")
        self.assertEqual(row["answer"], "yes")
        self.assertEqual((row["status"], row["final_fact"]), (CONFIRMED, "multiple_visits=true"))

    def test_the_trace_is_admin_only(self):
        from pcn_appeal import api
        case, _, _ = pipeline()
        api.CASES[case.case_id] = {"case": case, "pipe": None, "flags": [], "questions": [],
                                   "output": None}
        self.addCleanup(api.CASES.pop, case.case_id, None)
        client = TestClient(api.app)
        with mock.patch.dict("os.environ", {"ADMIN_TRACE_TOKEN": "", "ADMIN_TOKEN": "",
                                            "APP_ENV": "development"}):
            body = client.get(f"/cases/{case.case_id}/facts").json()
        self.assertEqual(body["hypothesis_trace"][0]["hypothesis"], "possible_multiple_visits=true")
        self.assertTrue(all(not k.startswith("_") for h in body["fact_hypotheses"] for k in h))
        with mock.patch.dict("os.environ", {"ADMIN_TRACE_TOKEN": "t"}):
            self.assertEqual(client.get(f"/cases/{case.case_id}/facts").status_code, 401)
        customer = client.get(f"/cases/{case.case_id}").json()
        self.assertNotRegex(str(customer), "possible_multiple_visits|hypothes")

    def test_hypotheses_survive_a_reload(self):
        db = sqlite_store.install(self)
        case = store.new_case()
        account("I left and came back.", case)
        Hypotheses.questions(case, lambda f: True)
        store.save(case)
        again = store.load(case.case_id)
        self.assertEqual(again.fact_hypotheses, case.fact_hypotheses)
        again.put(Fact("F-multiple_visits", "multiple_visits", True, FactStatus.ANSWERED,
                       FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        store.save(again)
        third = store.load(case.case_id)
        self.assertEqual(third.fact_hypotheses[0]["status"], CONFIRMED)
        self.assertEqual(sqlite_store.count(db, "fact_hypotheses"), 1)


if __name__ == "__main__":
    unittest.main()
