"""P7 B3 - the question budget belongs to the customer.

Internal analysis rounds (ground recovery inside generate()) consume none of
the question budget, mark nothing as asked, and leave the customer's pending
questions untouched. A question is "asked" only when it was actually
presented. The stopping rule is materiality (the Question Authority approving
nothing); the round cap is a circuit breaker.
"""
from __future__ import annotations

import unittest

from test_scenarios import make_case


def rounds(case, event="analysis_round"):
    return [a for a in case.audit if a.get("event") == event]


class InternalRounds(unittest.TestCase):
    def setUp(self):
        self.case, self.pipe = make_case()
        self.pipe.ingest(self.case)
        self.pipe.confirm(self.case, {}, list(self.case.facts), "")

    def test_an_internal_round_consumes_no_budget_and_marks_nothing(self):
        budget_before = len(rounds(self.case))
        asked_before = list(self.case.asked_questions)
        pending_before = list(self.case.pending_questions)
        self.pipe._reanalyse(self.case, "", internal=True)
        self.pipe._reanalyse(self.case, "", internal=True)
        self.assertEqual(len(rounds(self.case)), budget_before)
        self.assertEqual(self.case.asked_questions, asked_before)
        self.assertEqual(self.case.pending_questions, pending_before)
        internal = rounds(self.case, "analysis_round_internal")
        self.assertEqual(len(internal), 2)
        self.assertIn("would_ask", internal[-1])

    def test_a_customer_round_consumes_budget_and_marks_asked(self):
        budget_before = len(rounds(self.case))
        qs = self.pipe._reanalyse(self.case, "")
        self.assertEqual(len(rounds(self.case)), budget_before + 1)
        for q in qs:
            self.assertIn(q["fact"], self.case.asked_questions)

    def test_generate_consumes_no_question_budget(self):
        """The whole drafting stage, recovery rounds included, spends nothing."""
        self.pipe.answer(self.case, {})
        budget_before = len(rounds(self.case))
        asked_before = list(self.case.asked_questions)
        self.pipe.generate(self.case)
        self.assertEqual(len(rounds(self.case)), budget_before)
        self.assertEqual(self.case.asked_questions, asked_before)


if __name__ == "__main__":
    unittest.main()
