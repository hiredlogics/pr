"""P7 B2 - claimed outcomes in the account are hypotheses, not facts.

There is a material difference between a customer saying "The machine wasn't
working" (a report of what they saw - stays a free-text fact) and "I paid"
(a conclusion they can be wrong about - a declined card, an unregistered app
payment). The second kind - payment_made, vehicle_immobilised,
immobilisation_prevented_departure - is proposed as a hypothesis, asked about,
and only the customer's answer (or a document) sets the fact. Confirmed, the
account's proposition re-enters drafting exactly as before.
"""
from __future__ import annotations

import unittest

from pcn_appeal import hypotheses as hyp
from pcn_appeal.engines import narrative
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.hypotheses import KINDS, Hypotheses
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind

from test_scenarios import make_case


def _case(breach: str, text: str) -> CaseFile:
    case = CaseFile("C-B2")
    case.put(Fact("F-br", "alleged_breach", breach, FactStatus.CONFIRMED,
                  FactSource(SourceKind.DOCUMENT, "E1")))
    case.raw_answers["narrative"] = text
    return case


def _by_fact(case: CaseFile, name: str) -> dict | None:
    return next((h for h in case.fact_hypotheses if h["fact_name"] == name), None)


class ClaimedOutcomesAreHypotheses(unittest.TestCase):
    def test_i_paid_is_a_question_not_a_fact(self):
        case = _case("No valid payment", "I paid via the app for the visit.")
        dig = assess_material_account(case)
        self.assertIsNone(case.get("payment_made"))
        h = _by_fact(case, "payment_made")
        self.assertIsNotNone(h)
        self.assertEqual(h["status"], hyp.UNCONFIRMED)
        self.assertEqual(h["rule"], "account.circumstance")
        self.assertEqual(h["required_confirmation_question"]["text"],
                         KINDS["payment_made"].question)
        self.assertFalse(any("payment was made" in p.lower()
                             for p in dig["propositions"]))

    def test_machine_not_working_stays_a_direct_fact(self):
        case = _case("No valid payment",
                     "The machine would not take my card and the payment failed.")
        dig = assess_material_account(case)
        self.assertTrue(case.get("payment_attempt_failed"))
        self.assertIsNone(_by_fact(case, "payment_attempt_failed"))
        self.assertTrue(any("payment facility" in p.lower()
                            for p in dig["propositions"]))

    def test_breakdown_claims_are_gated(self):
        case = _case("Overstay", "The car broke down and we couldn't leave.")
        assess_material_account(case)
        for name in ("vehicle_immobilised", "immobilisation_prevented_departure"):
            self.assertIsNone(case.get(name), name)
            h = _by_fact(case, name)
            self.assertIsNotNone(h, name)
            self.assertEqual(h["status"], hyp.UNCONFIRMED, name)

    def test_negated_payment_proposes_nothing(self):
        case = _case("No valid payment", "I had not paid for the parking that day.")
        assess_material_account(case)
        self.assertIsNone(_by_fact(case, "payment_made"))
        self.assertIsNone(case.get("payment_made"))

    def test_confirmed_answer_restores_the_proposition(self):
        case = _case("No valid payment", "I paid via the app for the visit.")
        assess_material_account(case)
        case.put(Fact("F-payment_made", "payment_made", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "q:payment_made")))
        h = _by_fact(case, "payment_made")
        self.assertEqual(h["status"], hyp.CONFIRMED)
        dig = assess_material_account(case)
        self.assertTrue(case.get("payment_made"))
        self.assertIn(KINDS["payment_made"].proposition, dig["propositions"])
        # The customer's phrase is provenance, not the proposition.
        self.assertFalse(any("i paid" in p.lower() for p in dig["propositions"]))

    def test_rejecting_answer_closes_the_hypothesis(self):
        case = _case("No valid payment", "I paid via the app for the visit.")
        assess_material_account(case)
        case.put(Fact("F-payment_made", "payment_made", False, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "q:payment_made")))
        self.assertEqual(_by_fact(case, "payment_made")["status"], hyp.REJECTED)
        dig = assess_material_account(case)
        self.assertNotIn(KINDS["payment_made"].proposition, dig["propositions"])

    def test_existing_answer_supersedes_instead_of_asking(self):
        case = _case("No valid payment", "I paid via the app for the visit.")
        case.put(Fact("F-payment_made", "payment_made", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "q:payment_made")))
        dig = assess_material_account(case)
        self.assertEqual(_by_fact(case, "payment_made")["status"], hyp.SUPERSEDED)
        # The answered fact still carries the circumstance into drafting.
        self.assertTrue(any("payment" in p.lower() for p in dig["propositions"]))


class WithdrawalIsScopedToTheProposer(unittest.TestCase):
    def test_narrative_reread_does_not_withdraw_account_hypotheses(self):
        case = _case("No valid payment",
                     "I paid via the app. We left and came back later that day.")
        assess_material_account(case)
        pay = _by_fact(case, "payment_made")
        self.assertEqual(pay["status"], hyp.UNCONFIRMED)
        # narrative re-reads text that supports neither hypothesis family.
        narrative.understand(case, ["Nothing new to add."])
        self.assertEqual(pay["status"], hyp.UNCONFIRMED,
                         "narrative's withdraw must not touch account proposals")
        visits = _by_fact(case, "multiple_visits")
        self.assertEqual(visits["status"], hyp.WITHDRAWN,
                         "narrative's own unsupported proposal is withdrawn")

    def test_account_rewrite_withdraws_its_own_proposal(self):
        case = _case("No valid payment", "I paid via the app for the visit.")
        assess_material_account(case)
        self.assertEqual(_by_fact(case, "payment_made")["status"], hyp.UNCONFIRMED)
        case.raw_answers["narrative"] = "I never found the machine."
        assess_material_account(case)
        self.assertEqual(_by_fact(case, "payment_made")["status"], hyp.WITHDRAWN)
        # And the account saying it again reopens the same hypothesis.
        case.raw_answers["narrative"] = "I paid via the app for the visit."
        assess_material_account(case)
        self.assertEqual(_by_fact(case, "payment_made")["status"], hyp.UNCONFIRMED)


class PipelineAsksAndUsesTheAnswer(unittest.TestCase):
    def test_payment_claim_is_asked_and_the_answer_reaches_the_pack(self):
        case, pipe = make_case({"alleged_breach": "No valid payment for vehicle"})
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I paid on the app for this visit")
        h = _by_fact(case, "payment_made")
        self.assertIsNotNone(h)
        self.assertIsNone(case.get("payment_made"))
        # Material: a KB module is gated on the fact, so the question stands.
        qs = Hypotheses.questions(case, pipe._could_change_a_ground)
        self.assertTrue(h["status"] != hyp.UNCONFIRMED or
                        any(q["target_fact"] == "payment_made" for q in qs) or
                        "payment_made" in case.asked_questions)
        pipe.answer(case, {"payment_made": "yes", "payment_method": "APP"})
        self.assertTrue(case.get("payment_made"))
        self.assertEqual(_by_fact(case, "payment_made")["status"], hyp.CONFIRMED)
        out = pipe.generate(case)
        self.assertTrue(out.pack.verified_facts.get("payment_made"))


if __name__ == "__main__":
    unittest.main()
