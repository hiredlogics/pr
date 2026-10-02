"""One sentence must never supply the other half of another sentence's fact.

The circumstance rules in engines/account.py pair a subject word with a
qualifier across a short gap (`.{0,40}`). Unconstrained, that gap runs past a
full stop, so two unrelated statements combine into a fact the customer never
stated:

    "I used the machine to pay. My phone battery failed so I lost the receipt."
    -> machine ... failed -> payment_attempt_failed = True

That matters most for the facts that are NOT hypothesis-gated (P7 B2): a
claimed outcome like payment_made is proposed as a question, so a bad read is
caught by the customer's answer, but a directly observed report sets its fact
straight away. payment_attempt_failed gates KB-PAY-02/03 and
child_occupant_present feeds the restricted-bay account rebuttal (KB-BAY-02),
where a fabricated material fact would be restated to the operator in the
keeper's name.

These are the reproductions from the P7 review, pinned so the gap can never
be widened back across a sentence end.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines import account
from pcn_appeal.hypotheses import KINDS


def _rule(fact_name: str) -> account.CircumstanceRule:
    return next(r for r in account._RULES if r.fact_name == fact_name)


def _matches(fact_name: str, text: str) -> bool:
    return bool(_rule(fact_name).pattern.search(text))


class TwoSentencesAreNotOneFact(unittest.TestCase):
    """Each case: the two halves are real words in the account, but they belong
    to different statements, so the fact is not established."""

    def test_a_working_machine_and_an_unrelated_failure(self):
        self.assertFalse(_matches(
            "payment_attempt_failed",
            "I used the machine to pay. My phone battery failed so I lost the receipt."))

    def test_an_untroubled_kiosk_and_a_broken_barrier(self):
        self.assertFalse(_matches(
            "payment_attempt_failed",
            "I paid at the kiosk without trouble. The barrier was broken on the way out."))

    def test_an_errand_for_a_child_who_was_not_in_the_car(self):
        # The parent/child bay rebuttal is asserted in the keeper's name, so
        # inventing an occupant is the most damaging read of all.
        self.assertFalse(_matches(
            "child_occupant_present",
            "I was collecting my daughter's prescription. My wife stayed in the car."))

    def test_a_parcel_for_a_son_and_a_colleague_waiting(self):
        self.assertFalse(_matches(
            "child_occupant_present",
            "I had to drop off a parcel for my son. A colleague waited with me."))

    def test_someone_elses_badge_and_a_resident_permit(self):
        self.assertFalse(_matches(
            "blue_badge_displayed",
            "My neighbour has a blue badge. I displayed my resident permit in the window."))

    def test_a_disabled_relative_and_a_slow_queue(self):
        # "my father is disabled" is not this customer needing extra time;
        # the queue is the reason given, and it is a different sentence.
        self.assertFalse(_matches(
            "disability_extra_time",
            "My father is disabled. The queue at the machine took extra time."))


class ADisabilityMustBeTheCustomersAndAboutTime(unittest.TestCase):
    """KB-EQ-01 activates on disability_extra_time ALONE (strength 65), and the
    proposition it carries asserts that additional time *was required*. A bare
    mention of disability anywhere in the account is not that assertion, so the
    rule requires the customer's own condition or a disability reason given for
    the time taken. Bounding the gap alone could not fix this: the old pattern
    matched the single word "disabled" with no gap at all.
    """

    def test_a_third_partys_condition_is_not_the_customers_need(self):
        self.assertFalse(_matches("disability_extra_time",
                                  "I was visiting my disabled neighbour."))

    def test_a_disabled_access_sign_is_not_a_need(self):
        self.assertFalse(_matches(
            "disability_extra_time",
            "The sign by the disabled bays was obscured by a van."))

    def test_the_customers_own_disability_still_reads(self):
        self.assertTrue(_matches("disability_extra_time",
                                 "I am disabled and could not walk back quickly."))

    def test_my_disability_still_reads(self):
        self.assertTrue(_matches("disability_extra_time",
                                 "My disability means I need longer to return to the car."))

    def test_a_wheelchair_the_customer_uses_still_reads(self):
        self.assertTrue(_matches("disability_extra_time",
                                 "I use a wheelchair and needed the extra space."))

    def test_extra_time_given_a_disability_reason_still_reads(self):
        self.assertTrue(_matches(
            "disability_extra_time",
            "It took me extra time because of my mobility impairment."))

    def test_my_blue_badge_still_reads(self):
        self.assertTrue(_matches("disability_extra_time",
                                 "My blue badge was in the windscreen."))

    def test_a_newline_separated_account_is_two_statements_too(self):
        # Customers type accounts in short lines as often as in prose.
        self.assertFalse(_matches(
            "payment_attempt_failed",
            "I paid at the machine when I arrived\nMy key fob would not work later"))


class TheRulesStillReadWhatTheCustomerDidSay(unittest.TestCase):
    """Tightening must not cost recall: the genuine single-sentence reports,
    including ones that span a comma or a clause, still extract."""

    def test_a_broken_machine_in_one_sentence(self):
        self.assertTrue(_matches("payment_attempt_failed",
                                 "The machine was out of order so I could not pay."))

    def test_a_failing_app_across_a_comma(self):
        self.assertTrue(_matches(
            "payment_attempt_failed",
            "I tried the app three times, and it would not accept my card."))

    def test_a_payment_that_did_not_go_through(self):
        self.assertTrue(_matches("payment_attempt_failed",
                                 "My payment did not go through."))

    def test_children_in_the_car_in_one_sentence(self):
        self.assertTrue(_matches("child_occupant_present",
                                 "My two children were in the car with me the whole time."))

    def test_children_across_a_clause(self):
        self.assertTrue(_matches(
            "child_occupant_present",
            "I had both kids, aged three and five, in the vehicle."))

    def test_a_badge_the_customer_displayed(self):
        self.assertTrue(_matches("blue_badge_displayed",
                                 "My blue badge was clearly displayed on the dashboard."))

    def test_the_customers_own_extra_time(self):
        self.assertTrue(_matches("disability_extra_time",
                                 "I am disabled and need extra time to get back to the car."))


class EveryGapIsSentenceBounded(unittest.TestCase):
    """A contract, not a spot check: no rule may pair across a sentence end.

    Written as a property so a NEW rule with an unconstrained `.{0,N}` gap
    fails here rather than shipping. The facts outside KINDS are the ones
    whose bad read is never caught by a confirming question, so they are
    where this is load-bearing - but the property holds for all of them.
    """

    def test_no_rule_uses_a_raw_unbounded_gap(self):
        offenders = [r.fact_name for r in account._RULES
                     if ".{0," in r.pattern.pattern]
        self.assertEqual(offenders, [],
                         "these rules use a raw .{0,N} gap instead of account._gap(N), "
                         "so they can pair across a sentence boundary")

    def test_every_gap_that_exists_is_the_bounded_one(self):
        # The rules that pair across a gap must all carry the bounded token.
        bounded = [r.fact_name for r in account._RULES
                   if account._GAP in r.pattern.pattern]
        self.assertTrue(bounded, "expected gap-using rules to exist")
        for name in ("payment_attempt_failed", "child_occupant_present",
                     "blue_badge_displayed", "disability_extra_time"):
            self.assertIn(name, bounded)

    def test_the_shared_gap_excludes_sentence_punctuation(self):
        # The one place the constraint lives, pinned directly.
        self.assertIsNone(account._gap_re().search(". "))
        self.assertIsNone(account._gap_re().search("\n"))
        self.assertIsNotNone(account._gap_re().search("a, b and c"))

    def test_ungated_facts_are_the_ones_this_protects(self):
        # Documents why this class of defect matters: these facts set
        # themselves with no confirming question in between, so a bad read is
        # never caught by the customer's answer.
        ungated = {r.fact_name for r in account._RULES
                   if account._GAP in r.pattern.pattern and r.fact_name not in KINDS}
        self.assertEqual(
            ungated,
            {"payment_attempt_failed", "child_occupant_present",
             "blue_badge_displayed", "disability_extra_time"})


if __name__ == "__main__":
    unittest.main()
