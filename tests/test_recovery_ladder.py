"""The customer flow recovers instead of stopping.

A private-parking customer uploads a notice and expects a letter. The pipeline
must therefore try, in order: ask for the facts a proposed ground actually
needs, retrieve more of the knowledge base, re-analyse, drop a sentence it
cannot stand behind, and re-validate. Holding the case is the last thing it
does, not the first.

These tests pin each rung of that ladder. They do not assert letter prose.

Run:  python -m unittest discover -s tests -p "test_recovery_ladder.py"
"""
from __future__ import annotations

import json
import unittest
from typing import Any, Optional

from pcn_appeal.engines.analysis import AnalysisEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (
    BuildingBlock, CaseFile, Draft, DraftSentence, Fact, FactSource, FactStatus,
    SourceKind, ValidationIssue, ValidationResult,
)
from pcn_appeal.orchestrator import AppealPipeline


def confirmed(case: CaseFile, **facts: Any) -> None:
    for name, value in facts.items():
        case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))


def answered(case: CaseFile, **facts: Any) -> None:
    for name, value in facts.items():
        case.put(Fact(f"F-{name}", name, value, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, f"answer:{name}")))
        if name not in case.asked_questions:
            case.asked_questions.append(name)


class ProposingLLM:
    """Proposes named modules whether or not their gate is satisfied.

    `support.ReferenceAnalysisLLM` evaluates `use_when` itself, so it can never
    produce a suppressed ground - and a suppressed ground is exactly what the
    unlocking questions are built from. This stand-in is deliberately blunt: it
    is the production case where the model reads the account, proposes a ground,
    and the deterministic veto holds it back for want of a fact.
    """

    def __init__(self, module_ids: list[str], questions: Optional[list[dict]] = None):
        self.module_ids = list(module_ids)
        self.questions = list(questions or [])
        self.payloads: list[dict] = []

    def complete_json(self, *, task, system, user, images=None):
        if task != "case_analysis":
            raise RuntimeError(f"no response queued for task {task!r}")
        self.payloads.append(json.loads(user))
        return {
            "grounds": [{"module_id": m, "supported_by": [], "note": "proposed"}
                        for m in self.module_ids],
            "questions": list(self.questions),
            "not_supported": [],
        }


class UnlockingQuestionTests(unittest.TestCase):
    """A ground the model proposed but the veto suppressed becomes a question."""

    def setUp(self):
        self.kg = KnowledgeGraph()

    def _analyse(self, case, module_ids, questions=None):
        llm = ProposingLLM(module_ids, questions)
        return AnalysisEngine(self.kg, llm).analyse(case), llm

    def test_a_suppressed_ground_asks_for_the_fact_its_gate_names(self):
        case = CaseFile("C-U1")
        confirmed(case, alleged_breach="Parked without a valid permit displayed",
                  parking_event_date="12/07/2026")
        result, _ = self._analyse(case, ["KB-SIGN-02"])

        self.assertNotIn("KB-SIGN-02", result.module_ids,
                         "the gate is unsatisfied, so the ground must stay suppressed")
        asked = [q["fact"] for q in result.questions]
        self.assertIn("signage_issue_raised", asked,
                      f"nothing asked for the gating fact; trace={result.trace}")

    def test_both_halves_of_one_gate_are_asked_although_they_share_a_topic(self):
        """KB-SIGN-02 needs signage_issue_raised AND signage_issue_type.

        Both names touch the `signage` topic cluster. That cluster exists to stop
        the model asking the same question twice under two invented names; it
        must not stop the KB asking for the second half of its own gate, or the
        ground can never be unlocked and the case is held for a fact nobody
        ever requested.
        """
        case = CaseFile("C-U2")
        confirmed(case, alleged_breach="Parked without a valid permit displayed",
                  parking_event_date="12/07/2026")
        result, _ = self._analyse(case, ["KB-SIGN-02"])

        asked = [q["fact"] for q in result.questions]
        self.assertIn("signage_issue_raised", asked)
        self.assertIn("signage_issue_type", asked,
                      f"second half of the gate was swallowed by its own topic; "
                      f"trace={result.trace}")

    def test_the_second_half_is_still_asked_on_the_round_after_the_first(self):
        case = CaseFile("C-U3")
        confirmed(case, alleged_breach="Parked without a valid permit displayed",
                  parking_event_date="12/07/2026")
        answered(case, signage_issue_raised=True)
        result, _ = self._analyse(case, ["KB-SIGN-02"])

        asked = [q["fact"] for q in result.questions]
        self.assertNotIn("signage_issue_raised", asked, "Q-07: asked at most once")
        self.assertIn("signage_issue_type", asked,
                      f"answering the first gating fact must not close the topic; "
                      f"trace={result.trace}")

    def test_a_topic_the_customer_cannot_answer_stays_closed(self):
        """The other half of the same distinction.

        `cannot remember` is not a missing fact to chase - it is an answer. No
        further question in that topic can help, whoever asks it.
        """
        case = CaseFile("C-U4")
        confirmed(case, alleged_breach="Parked without a valid permit displayed",
                  parking_event_date="12/07/2026")
        answered(case, signage_recollection="I cannot remember what the signs said")
        result, _ = self._analyse(case, ["KB-SIGN-02"])

        asked = [q["fact"] for q in result.questions]
        self.assertEqual([], [q for q in asked if "signage" in q],
                         f"topic is unresolved for this customer; trace={result.trace}")

    def test_no_question_is_invented_for_a_fact_with_no_approved_wording(self):
        """The wording comes from the fact vocabulary, never from this code."""
        case = CaseFile("C-U5")
        confirmed(case, alleged_breach="Parked without a valid permit displayed")
        result, _ = self._analyse(case, ["KB-POFA-04"])

        for q in result.questions:
            shape = self.kg.question_for(q["fact"]) or {}
            self.assertEqual(shape.get("text"), q["text"],
                             f"{q['fact']}: wording is not the approved wording")

    def test_unlocking_questions_still_obey_the_driver_identity_rule(self):
        """Q-01 is not relaxed for a question the KB asked for."""
        case = CaseFile("C-U6")
        confirmed(case, alleged_breach="Parked without a valid permit displayed",
                  parking_event_date="12/07/2026")
        result, _ = self._analyse(case, ["KB-SIGN-02", "KB-SIGN-04", "KB-POFA-02"])
        banned = AnalysisEngine(self.kg, ProposingLLM([])).banned

        for q in result.questions:
            low = q["text"].lower()
            self.assertFalse(any(b in low for b in banned), q["text"])


class HeldCaseIsNotACustomerOutcomeTests(unittest.TestCase):
    """What a held case may and may not show the customer."""

    def test_a_held_case_sends_no_grounds_and_no_validator_messages(self):
        from fastapi.testclient import TestClient

        from pcn_appeal import api

        client = TestClient(api.app)
        res = client.post("/cases").json()
        case_id = res["case_id"]
        # A notice with nothing disputable in it: no fact the KB can build on.
        client.post(f"/cases/{case_id}/documents", json={"documents": [{
            "doc_id": "E1", "doc_type": "PCN", "filename": "pcn.pdf",
            "text": "PARKING CHARGE NOTICE\nCharge: GBP 100\n",
        }]})
        out = client.post(f"/cases/{case_id}/auto-appeal",
                          json={"circumstances": "I do not think this is fair."}).json()

        if out.get("state") == "RELEASED":
            self.skipTest("this notice released; nothing held to assert about")
        self.assertNotIn("blocking_issues", out,
                         "validator messages name rules and facts: internal reasoning")
        self.assertNotIn("grounds", out,
                         "grounds describe a letter the customer never received")


class DrafterNoteTests(unittest.TestCase):
    """A block's usage note is guidance for the drafter, not letter prose."""

    def test_letter_text_drops_the_note_and_keeps_the_argument(self):
        blk = BuildingBlock(
            block_id="B-X",
            text=("The operator has produced no evidence of a valid contract. "
                  "Use only where the uploaded instrument genuinely supports this."),
        )
        self.assertIn("no evidence of a valid contract", blk.letter_text)
        self.assertNotIn("Use only where", blk.letter_text)

    def test_a_block_that_is_only_a_note_is_left_alone(self):
        """Better a block that reads oddly than a block rendered as nothing."""
        blk = BuildingBlock(block_id="B-Y",
                            text="Use only where a photograph was uploaded.")
        self.assertEqual(blk.text, blk.letter_text)

    def test_every_approved_block_still_says_something_after_the_strip(self):
        for block in KnowledgeGraph().blocks.values():
            self.assertTrue(block.letter_text.strip(), block.block_id)


class FailingSentenceRecoveryTests(unittest.TestCase):
    """Rung four: drop the sentence that failed, keep the letter."""

    @staticmethod
    def _draft() -> Draft:
        return Draft("C-F", [[
            DraftSentence("The charge is disputed.", [], ["KB-A"], []),
            DraftSentence("A permit was displayed at all times.", [], ["KB-B"], []),
        ]], attempt=3)

    @staticmethod
    def _blocked(sentence: str) -> ValidationResult:
        return ValidationResult(False, [ValidationIssue(
            "R-08b", "BLOCK", "asserts an unproven fact", sentence=sentence)])

    def test_the_blocking_sentence_goes_and_the_rest_stays(self):
        draft, dropped = AppealPipeline._without_failing_sentences(
            self._draft(), self._blocked("A permit was displayed at all times."))

        kept = [s.text for s in draft.sentences()]
        self.assertEqual(["The charge is disputed."], kept)
        self.assertEqual(["A permit was displayed at all times."], dropped)

    def test_a_document_level_failure_cannot_be_fixed_by_dropping_anything(self):
        """No sentence named means the whole letter failed. Nothing is removed."""
        result = ValidationResult(False, [ValidationIssue(
            "VAL-NO-GROUND", "BLOCK", "no substantive ground paragraph")])
        draft, dropped = AppealPipeline._without_failing_sentences(self._draft(), result)

        self.assertEqual([], dropped)
        self.assertEqual(2, len(list(draft.sentences())))

    def test_an_empty_paragraph_is_removed_rather_than_left_blank(self):
        only = Draft("C-F", [[DraftSentence("A permit was displayed.", [], ["KB-B"], [])]],
                     attempt=3)
        draft, dropped = AppealPipeline._without_failing_sentences(
            only, self._blocked("A permit was displayed."))

        self.assertEqual([], draft.paragraphs)
        self.assertEqual(["A permit was displayed."], dropped)


if __name__ == "__main__":
    unittest.main()
