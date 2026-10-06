"""Phase 4: Question Authority asks only what could change a module's eligibility.

    Phase 3 eligibility (authoritative fact view) -> only UNRESOLVED modules ->
    the exact missing fact -> would the answer change the module? -> ask / don't

The scenario matrix (tests/qa4_matrix.py) drives the real pipeline; each lettered
scenario of the Phase 4 specification, and the extra ones the KB-gate path needs,
is one test here. The rest pin the rules the matrix cannot see: nothing in the
question code names a module, an operator, a site or a phrase, the customer never
sees the internal contract, and what a customer answers goes through FactManager
and nowhere else.

Run:  PYTHONPATH=.:tests python -m unittest tests.test_question_authority_phase4 -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from pcn_appeal import customer_safe
from pcn_appeal.engines import question_authority as qa
from pcn_appeal.engines.question_authority import QuestionAuthority
from pcn_appeal.kg.graph import KnowledgeGraph

import qa4_matrix as M

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "pcn_appeal" / "engines" / "question_authority.py").read_text(encoding="utf-8")


class Matrix(unittest.TestCase):
    """One test per scenario; a failure names the defect class and the detail."""

    def _check(self, letter: str):
        v = next(fn() for l, _, fn in M.SCENARIOS if l == letter)
        self.assertTrue(v.passed, f"{v.case} [{v.defect}] {v.title}: {v.detail}")


def _add(letter: str):
    def test(self):
        self._check(letter)
    test.__name__ = f"test_{letter.lower()}"
    return test


for _letter, _title, _ in M.SCENARIOS:
    setattr(Matrix, f"test_{_letter.lower()}_{re.sub(r'[^a-z0-9]+', '_', _title.lower())[:60].strip('_')}",
            _add(_letter))


class TheMatrixIsComplete(unittest.TestCase):

    def test_every_lettered_scenario_of_the_specification_is_present(self):
        letters = {l for l, _, _ in M.SCENARIOS}
        self.assertTrue(set("ABCDEFGHIJKLMNOPQRST") <= letters, sorted(set("ABCDEFGHIJKLMNOPQRST") - letters))


class NothingIsSpecificToACaseOrAPhrase(unittest.TestCase):
    """The rules are over statuses, roles and the shape of a condition."""

    def test_the_authority_names_no_module_operator_or_site(self):
        code = re.sub(r'"""[\s\S]*?"""', "", SRC)               # docstrings explain, code decides
        code = "\n".join(l.split("#")[0] for l in code.splitlines())
        self.assertNotRegex(code, r"KB-[A-Z]+-\d+")
        self.assertNotRegex(code, r"(?i)\b(tesco|sainsbury|apcoa|ringgo|parkingeye|euro car parks|"
                                  r"horizon|smart parking|upside|ncp|bpa|ipc)\b")

    def test_the_authority_no_longer_carries_its_own_gate_evaluator(self):
        self.assertFalse(hasattr(qa, "tri"))
        self.assertNotIn("evaluate(", re.sub(r"evaluate3\(", "", SRC))

    def test_eligibility_is_phase_3s(self):
        self.assertIn("evaluate_module_id", SRC)
        self.assertIn("decide", SRC)


class TheCustomerSeesOnlyTheQuestion(unittest.TestCase):

    def test_the_contract_stays_internal(self):
        h = M.Harness(ask=[M.PAY_Q])
        self.assertEqual(h.shown, ["payment_made"])
        for q in h.last + list(h.case.pending_questions):
            self.assertLessEqual(set(q), {"fact", "text", "type", "options"})
            self.assertEqual(customer_safe.leaks(q), [])
        row = next(r for r in h.reviews() if r["decision"] == "APPROVED" and r["shown"])
        for k in ("question_id", "fact_key", "question", "source_module_ids", "current_status",
                  "materiality_reason", "possible_effect"):
            self.assertIn(k, row)
        self.assertEqual(row["current_status"], "UNRESOLVED")
        self.assertTrue(set(row["possible_effect"]) <= {"SUPPORTED", "REJECTED", "BLOCKED"})
        for k in ("fact_key", "source_module_ids", "materiality_reason", "possible_effect"):
            self.assertIn(k, customer_safe.INTERNAL_KEYS)

    def test_the_wording_carries_no_internal_vocabulary(self):
        h = M.Harness(ask=[M.PAY_Q])
        for q in h.last:
            self.assertEqual(customer_safe.internal_ids(q["text"]), [])
            self.assertIsNone(qa.DRIVER_IDENTITY.search(q["text"]))
            self.assertIsNone(qa.LEGAL_INTERPRETATION.search(q["text"]))


class AnAnswerGoesThroughFactManagerOnly(unittest.TestCase):

    def test_an_answer_is_a_fact_and_changes_no_module_status_by_itself(self):
        h = M.Harness(ask=[M.PAY_Q])
        self.assertEqual(h.status("KB-PAY-01"), "UNRESOLVED")
        h.answer("payment_made", "No")
        node = h.case.facts.get("payment_made")
        self.assertEqual((node.value, node.status.value, node.source.kind.value),
                         (False, "ANSWERED", "ANSWER"))
        # The status moved because eligibility was recomputed from the fact.
        self.assertEqual(h.status("KB-PAY-01"), "REJECTED")
        self.assertNotIn("payment_made", {k for k in (h.case.raw_answers or {}) if k.startswith("_module")})

    def test_a_not_sure_answer_is_not_a_fact(self):
        for raw in ("not sure", "I don't know", "maybe", ""):
            with self.subTest(raw=raw):
                h = M.Harness(ask=[M.PAY_Q])
                h.answer("payment_made", raw)
                self.assertNotIn("payment_made", h.case.fact_view())
                self.assertNotIn("payment_made", h.round())
                self.assertEqual(h.status("KB-PAY-01"), "UNRESOLVED")


class Lifecycle(unittest.TestCase):

    def test_states_follow_the_case(self):
        h = M.Harness(ask=[M.PAY_Q])
        st = lambda: {r["target_fact"]: r["state"] for r in qa.lifecycle(h.case)}
        self.assertEqual(st()["payment_made"], qa.PENDING)
        h.answer("payment_made", "Yes")
        self.assertEqual(st()["payment_made"], qa.ANSWERED)
        g = M.Harness(ask=[M.PAY_Q])
        g.round()
        self.assertEqual({r["target_fact"]: r["state"] for r in qa.lifecycle(g.case)}["payment_made"],
                         qa.UNRESOLVED_STATE)

    def test_a_fact_that_became_known_another_way_supersedes_its_question(self):
        h = M.Harness(ask=[M.PAY_Q])
        h.set("payment_made", True, kind=M.SourceKind.DOCUMENT, status=M.FactStatus.CONFIRMED)
        self.assertEqual({r["target_fact"]: r["state"] for r in qa.lifecycle(h.case)}["payment_made"],
                         qa.SUPERSEDED)


class ExclusionsAreNotAskedBeforeTheGroundIsOpen(unittest.TestCase):
    """A `not ...` condition or a hard blocker is asked about once the ground's
    own requirements stand; before that the answer spends the customer's patience
    on a ground that may not exist."""

    def test_an_exclusion_waits_for_the_requirement(self):
        # CON-01 needs a short presence and "no payment"; with the presence unknown
        # the payment answer is not asked on its account.
        h = M.Harness(extra_fields={"alleged_breach": "Parked in a no parking area"},
                      ask=[M.gate_q("payment_made", M.PAY_Q["text"], "KB-CON-01")])
        approved = [r for r in h.reviews() if r["target_fact"] == "payment_made"
                    and r["decision"] == "APPROVED"]
        self.assertEqual(approved, [])

    def test_the_same_exclusion_is_asked_once_the_requirement_stands(self):
        h = M.Harness(extra_fields={"alleged_breach": "Parked in a no parking area"},
                      kg_facts={"short_presence_before_acceptance": True},
                      ask=[M.gate_q("payment_made", M.PAY_Q["text"], "KB-CON-01")])
        approved = [r for r in h.reviews() if r["target_fact"] == "payment_made"
                    and r["decision"] == "APPROVED"]
        self.assertTrue(approved)
        self.assertIn("KB-CON-01", approved[-1]["source_module_ids"])


class OneAtATimeAndPriority(unittest.TestCase):

    def test_one_question_is_shown_the_rest_wait_and_are_not_lost(self):
        h = M.Harness(ask=[M.HOSP_Q, M.PAY_Q])
        self.assertEqual(h.shown, ["payment_made"])
        h.answer("payment_made", "not sure")
        self.assertNotIn("payment_made", h.shown)
        self.assertTrue(set(h.shown) & {"hospital_attendance", "multiple_visits"}, h.shown)

    def test_integrity_outranks_every_ground(self):
        h = M.Harness()
        r = h.pipe.authority.review(h.case, [
            dict(M.PAY_Q),
            {"fact": "pcn_number", "text": "Which charge number is on the notice?",
             "type": "choice", "options": ["A1", "B2"], "source": qa.CONFLICT}])
        self.assertEqual([q["fact"] for q in r.shown], ["pcn_number"])
        self.assertEqual(r.shown[0]["tier"], qa.TIER_INTEGRITY)

    def test_tiers_are_ordered_unlock_then_blocker_then_support(self):
        self.assertLess(qa.TIER_INTEGRITY, qa.TIER_UNLOCK)
        self.assertLess(qa.TIER_UNLOCK, qa.TIER_BLOCKER)
        self.assertLess(qa.TIER_BLOCKER, qa.TIER_SUPPORT)


if __name__ == "__main__":
    unittest.main()
