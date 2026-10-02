"""P7 B4 - critical integrity failures are hard release gates.

The integrity checks were observational (P5.5). Now the CRITICAL subset runs
before a release is final: a candidate letter that passes draft validation but
fails a critical check is held in MANUAL_REVIEW with the letter withheld. The
gate fails closed: an error in the gate itself refuses the release.
"""
from __future__ import annotations

import unittest
from unittest import mock

from test_scenarios import make_case, run
from test_verified_legal_findings import LATE

from pcn_appeal.integrity.checks import CRITICAL, critical_failures
from pcn_appeal.models import CaseState


class HonestRunsAreUntouched(unittest.TestCase):
    def test_a_clean_case_still_releases_and_records_the_gate(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        gate = [a for a in case.audit if a.get("event") == "integrity_gate"]
        self.assertTrue(gate)
        self.assertTrue(gate[-1]["passed"])
        self.assertEqual(gate[-1]["failed"], [])


class CriticalFailureHolds(unittest.TestCase):
    def test_a_leak_outside_the_letter_is_caught_only_by_the_gate(self):
        """An internal id in a pending question: DV/VAL check the letter only,
        so without the gate this case would release."""
        case, pipe = make_case(LATE)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "")
        pipe.answer(case, {})
        case.pending_questions.append(
            {"fact": "x", "text": "We ask because KB-POFA-02 requires it", "type": "bool"})
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertIsNone(out.letter)
        rules = {i.rule for i in out.validation.issues}
        self.assertIn("VAL-INTEGRITY", rules)
        msg = next(i.message for i in out.validation.issues if i.rule == "VAL-INTEGRITY")
        self.assertIn("NO_CUSTOMER_LEAKAGE", msg)
        # the draft version for this attempt is not marked released
        self.assertFalse(any(v["released"] for v in case.draft_versions
                             if v.get("run_id") == case.run_id))
        gate = [a for a in case.audit if a.get("event") == "integrity_gate"][-1]
        self.assertFalse(gate["passed"])
        self.assertIn("NO_CUSTOMER_LEAKAGE", gate["failed"])

    def test_any_critical_check_failure_blocks(self):
        case, pipe = make_case(LATE)
        with mock.patch("pcn_appeal.integrity.check_case",
                        return_value=[{"check": "FACTS_HAVE_SOURCES", "status": "FAIL"}]):
            out = run(case, pipe, "", {})
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertIsNone(out.letter)

    def test_the_gate_fails_closed_on_its_own_error(self):
        case, pipe = make_case(LATE)
        with mock.patch("pcn_appeal.integrity.check_case",
                        side_effect=RuntimeError("boom")):
            out = run(case, pipe, "", {})
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertIsNone(out.letter)
        msg = next(i.message for i in out.validation.issues if i.rule == "VAL-INTEGRITY")
        self.assertIn("INTEGRITY_GATE_ERROR", msg)
        self.assertTrue(any(a.get("event") == "integrity_gate_error" for a in case.audit))

    def test_a_non_critical_failure_does_not_block(self):
        case, pipe = make_case(LATE)
        with mock.patch("pcn_appeal.integrity.check_case",
                        return_value=[{"check": "MANIFEST_RECORDED", "status": "FAIL"}]):
            out = run(case, pipe, "", {})
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)


class TheCriticalSet(unittest.TestCase):
    def test_the_set_is_the_agreed_one(self):
        self.assertEqual(set(CRITICAL),
                         {"NO_CLAIM_OUTSIDE_PLAN", "FACTS_HAVE_SOURCES",
                          "NO_CUSTOMER_LEAKAGE", "DRIVER_NOT_IDENTIFIED",
                          "LEGAL_DEFECTS_VERIFIED", "STATE_MACHINE_CONSISTENT",
                          "SUPPORTED_ITEMS_HAVE_SUPPORT"})

    def test_critical_failures_filters(self):
        rows = [{"check": "NO_CUSTOMER_LEAKAGE", "status": "FAIL"},
                {"check": "MANIFEST_RECORDED", "status": "FAIL"},
                {"check": "DRIVER_NOT_IDENTIFIED", "status": "PASS"}]
        self.assertEqual(critical_failures(rows), ["NO_CUSTOMER_LEAKAGE"])

    def test_supported_items_have_support_is_checked_in_memory(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        from pcn_appeal.integrity.checks import check_case
        rows = {c["check"]: c["status"] for c in check_case(case, out, pipe.kg)}
        self.assertEqual(rows.get("SUPPORTED_ITEMS_HAVE_SUPPORT"), "PASS")


if __name__ == "__main__":
    unittest.main()
