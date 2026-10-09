"""Appeal quality judge (client brief 2026-10-09 §8 and §9).

The deterministic validators decide what a letter MAY say. They cannot tell
whether what it said is an argument about this particular charge or a template
that would suit any charge - and the brief's worked example of a bad letter
("I dispute this charge. The signage was inadequate and the operator should
prove landowner authority.") passes every legal check while saying nothing
about the case.

So the judge scores writing, and these tests hold the two properties that make
it safe to add at all:

  it has no authority over the law  - it cannot introduce a ground, and a
                                     ground it claims was omitted is ignored
                                     unless the claim plan already approved it;
  its verdict is reproducible      - the thresholds are the brief's, applied by
                                     `verdict()`, not left to the model's mood.

The judge is given intentionally bad and intentionally good drafts (brief §9)
through a stub model, so what is tested is the gate, not the model's taste.
"""
from __future__ import annotations

import unittest

from pcn_appeal.drafting.quality_judge import (PASS, REWRITE, SCORES, THRESHOLDS,
                                               QualityJudge, enabled, verdict)
from pcn_appeal.models import (Draft, DraftSentence, FactSource, FactStatus,
                               RetrievalPack, SourceKind)

GOOD = {name: 10 for name in SCORES}


def flags(**over):
    base = {"explains_why_cancelled": True, "sendable_for_any_pcn": False,
            "unsupported_statements": [], "omitted_grounds": [],
            "weakened_customer_facts": []}
    base.update(over)
    return base


class TheThresholdsAreTheBriefs(unittest.TestCase):

    def test_a_strong_letter_passes(self):
        self.assertEqual(verdict(GOOD, flags()), PASS)

    def test_a_letter_that_would_suit_any_charge_is_rewritten(self):
        """The central test: generic prose is a rewrite however polished."""
        self.assertEqual(verdict(GOOD, flags(sendable_for_any_pcn=True)), REWRITE)

    def test_a_letter_that_never_says_why_it_should_be_cancelled_is_rewritten(self):
        self.assertEqual(verdict(GOOD, flags(explains_why_cancelled=False)), REWRITE)

    def test_an_unsupported_statement_is_a_rewrite(self):
        self.assertEqual(
            verdict(GOOD, flags(unsupported_statements=["The signage was inadequate."])),
            REWRITE)

    def test_each_threshold_is_enforced_at_its_own_floor(self):
        for name, floor in THRESHOLDS.items():
            with self.subTest(score=name):
                self.assertEqual(verdict({**GOOD, name: floor}, flags()), PASS)
                self.assertEqual(verdict({**GOOD, name: floor - 1}, flags()), REWRITE)

    def test_a_soft_score_alone_does_not_hold_a_letter(self):
        """Persuasiveness and polish are advice, not a release gate."""
        for name in ("persuasiveness", "clarity", "conciseness"):
            with self.subTest(score=name):
                self.assertEqual(verdict({**GOOD, name: 4}, flags()), PASS)


class _StubLLM:
    """Returns one scripted judgement, and records what it was asked."""

    def __init__(self, payload):
        self.payload = payload
        self.seen = None

    # Keyword-only, exactly like the real clients (llm.py LLMClient).
    def complete_json(self, *, task, system, user, images=None):
        self.seen = {"task": task, "system": system, "user": user}
        return self.payload


def pack(approved=("KB-CON-02",)):
    return RetrievalPack(
        primary_route="CONSIDERATION", secondary_routes=[], module_ids=list(approved),
        verified_facts={"total_recorded_duration_min": 3}, fact_refs={},
        missing_facts=[], evidence_refs=[], prohibited_claims=["free allowance"],
        code_version=None, pofa_route="POSTAL", pofa_findings=[],
        driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[{"module_id": "KB-CON-02", "kind": "module",
                         "text": "Analyse whether a parking contract was ever accepted."}],
        lease_clauses=[],
        claim_plan={"status": "LOCKED", "approved": list(approved)})


def draft(text):
    return Draft("C-Q", [[DraftSentence(text, [], ["KB-CON-02"])]])


class TheJudgeJudgesWritingNotLaw(unittest.TestCase):

    BAD = ("I dispute this charge. The signage was inadequate and the operator "
           "should prove landowner authority. Please cancel the PCN.")
    SPECIFIC = ("The terms were considered, they were not accepted, and the vehicle "
                "then left the site after three minutes.")

    def test_the_briefs_bad_generic_draft_is_failed(self):
        llm = _StubLLM({
            "scores": {**GOOD, "case_specificity": 2},
            "explains_why_cancelled": False, "sendable_for_any_pcn": True,
            "unsupported_statements": ["The signage was inadequate."],
            "summary": "generic; introduces grounds without support",
        })
        out = QualityJudge(llm).review(draft(self.BAD), pack())
        self.assertEqual(out["status"], REWRITE)
        self.assertTrue(out["sendable_for_any_pcn"])
        self.assertEqual(out["scores"]["case_specificity"], 2)

    def test_the_briefs_good_specific_draft_passes(self):
        llm = _StubLLM({"scores": GOOD, "explains_why_cancelled": True,
                        "sendable_for_any_pcn": False, "summary": "case specific"})
        out = QualityJudge(llm).review(draft(self.SPECIFIC), pack())
        self.assertEqual(out["status"], PASS)

    def test_a_ground_the_plan_never_approved_is_ignored(self):
        """The judge may not introduce law. It can only name a ground the claim
        plan already approved; anything else is dropped rather than reported."""
        llm = _StubLLM({"scores": GOOD, "explains_why_cancelled": True,
                        "sendable_for_any_pcn": False,
                        "omitted_grounds": ["KB-EQ-02", "KB-POFA-04", "KB-CON-02"]})
        out = QualityJudge(llm).review(draft(self.SPECIFIC), pack(("KB-CON-02",)))
        self.assertEqual(out["omitted_grounds"], ["KB-CON-02"])

    def test_it_runs_on_its_own_task_so_it_is_not_the_writer(self):
        llm = _StubLLM({"scores": GOOD, "explains_why_cancelled": True,
                        "sendable_for_any_pcn": False})
        QualityJudge(llm).review(draft(self.SPECIFIC), pack())
        self.assertEqual(llm.seen["task"], "appeal_quality")

    def test_the_customers_own_facts_are_given_to_the_judge(self):
        """§7: it cannot see a specific fact being flattened into generic
        language unless it is told which facts came from the customer."""
        from pcn_appeal.models import CaseFile, Fact
        case = CaseFile("C-Q")
        case.put(Fact("F1", "no_parking_took_place", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.CUSTOMER_FREE_TEXT, "narrative")))
        llm = _StubLLM({"scores": GOOD, "explains_why_cancelled": True,
                        "sendable_for_any_pcn": False})
        QualityJudge(llm).review(draft(self.SPECIFIC), pack(), case)
        self.assertIn("no_parking_took_place", llm.seen["user"])

    def test_a_judge_failure_never_breaks_the_case(self):
        class Boom:
            def complete_json(self, **kw):
                raise RuntimeError("provider down")
        out = QualityJudge(Boom()).review(draft(self.SPECIFIC), pack())
        self.assertEqual(out["status"], "ERROR")
        self.assertFalse(out["blocking"])

    def test_it_never_blocks_a_release_yet(self):
        """Introduced the way the shadow judge was: recorded, not acted on,
        until the client has seen its agreement rate."""
        llm = _StubLLM({"scores": {**GOOD, "case_specificity": 1},
                        "explains_why_cancelled": False, "sendable_for_any_pcn": True})
        self.assertFalse(QualityJudge(llm).review(draft(self.BAD), pack())["blocking"])

    def test_it_is_off_unless_asked_for(self):
        self.assertFalse(enabled(False))
        self.assertTrue(enabled(True))


class ThePipelineActuallyCallsIt(unittest.TestCase):
    """The unit tests above exercise the judge. This exercises the wiring:
    an observer that is never constructed, or never reached, scores nothing."""

    def _pipeline(self, on, payload=None):
        from pcn_appeal.orchestrator import AppealPipeline
        llm = _StubLLM(payload or {"scores": GOOD, "explains_why_cancelled": True,
                                   "sendable_for_any_pcn": False})
        return AppealPipeline(llm, quality_judge_enabled=on), llm

    def test_the_flag_builds_it(self):
        self.assertIsNone(self._pipeline(False)[0].quality)
        self.assertIsNotNone(self._pipeline(True)[0].quality)

    def test_a_released_draft_is_scored_and_recorded(self):
        from pcn_appeal.models import CaseFile
        pipe, _ = self._pipeline(True)
        case = CaseFile("C-W")
        pipe._record_version(case, {"claim_plan_id": "P1", "approved": ["KB-CON-02"]},
                             draft("The vehicle left after three minutes."),
                             type("R", (), {"passed": True, "issues": []})(),
                             None, pack(), released=True)
        events = [a for a in case.audit if a.get("event") == "appeal_quality"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], PASS)
        self.assertFalse(events[0]["blocking"])

    def test_a_draft_that_is_not_going_out_is_not_scored(self):
        from pcn_appeal.models import CaseFile
        pipe, _ = self._pipeline(True)
        case = CaseFile("C-W")
        pipe._record_version(case, {"claim_plan_id": "P1", "approved": ["KB-CON-02"]},
                             draft("An intermediate attempt."),
                             type("R", (), {"passed": False, "issues": []})(),
                             None, pack(), released=False)
        self.assertEqual([a for a in case.audit if a.get("event") == "appeal_quality"], [])


if __name__ == "__main__":
    unittest.main()
