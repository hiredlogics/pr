"""Support-only grounds never become the letter, and an unreadable site
postcode is asked for only when it would unlock a leading ground.

Live, a notice whose site postcode could not be read lost every Schedule 4
ground (jurisdiction unknown) and went out as a landowner-authority letter.
Landowner authority (KB-LAND-01) and the general keeper-liability framing
(KB-POFA-01) are support-only: they can never lead.

All data is fictitious. Nothing is operator- or case-specific.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines.recovery import postcode_unlocks
from pcn_appeal.models import EvidenceItem
from test_question_authority import case_with as _one_page_case, run


def case_with(**kw):
    """The shared harness notice, with its reverse uploaded as a second page."""
    case, pipe = _one_page_case(**kw)
    case.evidence["E1"].images = [b"FRONT-PAGE"]
    case.evidence["E2"] = EvidenceItem("E2", "OTHER", "notice_back.jpg", images=[b"REVERSE-PAGE"])
    return case, pipe

# Both sides are uploaded (case_with), as in the live case: a one-sided upload
# is asked for its other side first (NEEDS_DOCUMENTS), a separate, earlier rule.
LATE = {"parking_event_date": "01/06/2026", "notice_issue_date": "05/07/2026"}     # 34 days
IN_TIME = {"parking_event_date": "01/06/2026", "notice_issue_date": "05/06/2026"}  # 4 days


def outcome(case, pipe, narrative="I would like to appeal this charge."):
    flags, questions = run(case, pipe, narrative)
    out = pipe.generate(case)
    return questions, out


class UnreadablePostcode(unittest.TestCase):

    def test_asked_when_it_would_unlock_a_leading_ground(self):
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        _, questions = run(case, pipe, "I would like to appeal this charge.")
        self.assertIn("KB-POFA-02", postcode_unlocks(case, pipe.kg))
        self.assertIn("site_postcode", [q["fact"] for q in questions])

    def test_left_unanswered_it_holds_for_the_detail_not_a_weaker_letter(self):
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        _, out = outcome(case, pipe)
        self.assertIsNone(out.letter)
        self.assertEqual(out.outcome, "NEEDS_FACTS")
        last = [a for a in case.audit if a.get("event") == "customer_outcome"][-1]
        self.assertEqual(last["detail"], {"missing": ["site_postcode"]})

    def test_answered_it_resolves_the_jurisdiction_and_the_ground_leads(self):
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        run(case, pipe, "I would like to appeal this charge.")
        pipe.answer(case, {"site_postcode": "M1 1AA"})
        out = pipe.generate(case)
        self.assertEqual(case.get("jurisdiction"), "ENGLAND_WALES")
        self.assertIn("KB-POFA-02", out.pack.module_ids)
        self.assertIsNotNone(out.letter)

    def test_not_asked_when_it_would_change_nothing(self):
        case, pipe = case_with(drop=("site_postcode",), extra=IN_TIME)
        questions, out = outcome(case, pipe)
        self.assertEqual(postcode_unlocks(case, pipe.kg), [])
        self.assertNotIn("site_postcode", [q["fact"] for q in questions])
        # P8: nothing to unlock, so the default keeper appeal is drafted rather
        # than ending with no letter (client instruction 2026-10-07).
        self.assertNotEqual(out.outcome, "NO_SUPPORTED_GROUNDS")
        self.assertIn("KB-KEEPER-01", out.pack.module_ids)


class SupportOnlyGroundsAreNotALetter(unittest.TestCase):

    def test_landowner_only_is_no_supported_grounds(self):
        """Support-only grounds still never lead. P8: instead of no letter, the
        default keeper appeal (KB-KEEPER-01) leads and they may support it."""
        case, pipe = case_with(extra=IN_TIME)          # postcode known, nothing to argue
        _, out = outcome(case, pipe)
        self.assertNotEqual(out.outcome, "NO_SUPPORTED_GROUNDS")
        self.assertEqual(out.pack.module_ids[0], "KB-KEEPER-01", out.pack.module_ids)
        self.assertTrue(set(out.pack.module_ids) <= {"KB-KEEPER-01", "KB-LAND-01", "KB-POFA-01"},
                        out.pack.module_ids)
        self.assertTrue(any(a.get("event") == "default_keeper_appeal" for a in case.audit))


class HeldCaseCarriesItsQuestion(unittest.TestCase):

    def test_a_needs_facts_hold_returns_the_postcode_question(self):
        from pcn_appeal.api import _held_questions
        case, pipe = case_with(drop=("site_postcode",), extra=LATE)
        _, out = outcome(case, pipe)
        self.assertEqual([q["fact"] for q in _held_questions(case, out)["questions"]], ["site_postcode"])
        # Answering on the same case releases the letter.
        pipe.answer(case, {"site_postcode": "M1 1AA"})
        self.assertIsNotNone(pipe.generate(case).letter)

    def test_other_holds_return_no_question(self):
        from pcn_appeal.api import _held_questions
        case, pipe = case_with(extra=IN_TIME)
        _, out = outcome(case, pipe)
        self.assertEqual(_held_questions(case, out), {})


class SkippingThePostcodeDoesNotLoop(unittest.TestCase):
    """Live: "Skip the rest" on the postcode question held the case for the
    postcode, which showed the same question again, forever."""

    def test_a_skipped_postcode_is_not_asked_again_and_a_letter_follows(self):
        from datetime import date
        from pcn_appeal.api import _held_questions
        from pcn_appeal.orchestrator import declined_questions
        from test_p8_client_instructions import released_on
        with released_on(date(2026, 7, 10)):
            case, pipe = case_with(drop=("site_postcode",), extra=LATE)
            first = pipe.auto_appeal(case, "I would like to appeal this charge.")
            self.assertEqual([q["fact"] for q in first.questions], ["site_postcode"])
            out = pipe.auto_appeal(case, "", None, skip_remaining=True).output
        self.assertIn("site_postcode", declined_questions(case))
        self.assertNotEqual(out.outcome, "NEEDS_FACTS")
        if out.outcome:
            self.assertNotIn("site_postcode",
                             [q["fact"] for q in _held_questions(case, out).get("questions", [])])
        self.assertEqual(out.pack.module_ids[0], "KB-KEEPER-01")


if __name__ == "__main__":
    unittest.main()
