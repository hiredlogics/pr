"""KB-CON-02: "terms were considered but not accepted and the vehicle then left".

The live failure this fixes: a customer wrote "I drove in, read the sign and
left because I did not agree" on a 3-minute stay - the textbook no-contract
case - and the system selected NO grounds at all and asked an unrelated
question. The cause was generic, not case-specific: `no_parking_took_place` is
the fact KB-CON-02's gate reads, and NOTHING could produce it except a direct
question. The semantic layer had no concept for the single most basic private
parking defence, so an account that stated the fact in plain English was not
understood, and the engine had to ask for something it had already been told.

The fix is one ontology concept (TERMS_REJECTED_LEFT -> no_parking_took_place)
defined by MEANING, from KB-CON-02's own USE WHEN. These tests hold the
behaviour that matters: the meaning is recognised however it is worded, it is
NOT read into accounts that merely mention leaving, and the ground becomes
reachable from the customer's own words.
"""
from __future__ import annotations

import unittest

from pcn_appeal.semantics.meaning_bridge import extract_concepts_meaning_bridge
from pcn_appeal.semantics.ontology import (CONCEPT_DEFINITIONS, CONCEPT_TO_FACTS,
                                           CONCEPTS)
from pcn_appeal.semantics.state import SEMANTIC_OWNED_FACTS

CONCEPT = "TERMS_REJECTED_LEFT"

# Different wordings of ONE meaning: the terms were declined and the vehicle
# left. Short, long, well written, badly written - none of these is a keyword
# the implementation may special-case.
MEANS_IT = (
    "I drove in, read the sign and left because I did not agree",
    "I read the terms and did not accept them so I drove out",
    "The charge was too expensive so I left immediately",
    "I looked at the tariff, changed my mind and went elsewhere",
    "did not agree with the conditions and then left",
    "I left because I would not pay that much",
    "I never parked there",
    "I didnt park, I just turned around",
    # Same meaning, other orderings and movement verbs. These are the wordings
    # an earlier version missed: each half of the meaning (declined / departed)
    # has one vocabulary shared by both orders, and movement verbs take an
    # adverb the way people write them ("drove STRAIGHT back out").
    "I pulled in, saw the price on the sign, decided against it and drove straight back out.",
    "Too expensive so I turned around",
    "I refused to accept those terms and exited",
    "decided against it and went right back out",
    "no parking took place",
)

# Accounts that mention leaving, signage or not parking as INTENDED, but do not
# mean "the terms were declined, so no contract was formed". Reading the concept
# into any of these would invent a defence the customer never raised.
DOES_NOT_MEAN_IT = (
    "I parked and paid for two hours",
    "The sign was unclear and hard to read",
    "I left the site after shopping",
    "I could not find a space so I waited",
    "I overstayed by ten minutes",
    "The barrier was broken so I could not get out",
    "I parked in the wrong bay by mistake",
    # The hard negatives: both halves of the meaning are present in words, but
    # the customer accepted the terms anyway, so no contract defence exists.
    "It was too expensive but I paid anyway",
    "I left after two hours of shopping",
    "I drove out after paying",
)


def concepts(text):
    return {c["concept"] for c in extract_concepts_meaning_bridge([text])}


class TheOntologyCarriesTheMeaning(unittest.TestCase):

    def test_the_concept_exists_and_is_defined_by_meaning(self):
        self.assertIn(CONCEPT, CONCEPTS)
        definition = CONCEPT_DEFINITIONS.get(CONCEPT, "")
        self.assertTrue(definition, "the concept must state its meaning for the model")
        # Defined by what it means, not by the words to look for.
        self.assertIn("accept", definition.lower())

    def test_it_promotes_the_fact_the_kb_gate_actually_reads(self):
        self.assertEqual(CONCEPT_TO_FACTS.get(CONCEPT), ("no_parking_took_place", True))

    def test_the_fact_is_now_owned_by_the_semantic_layer(self):
        """Owned means no regex path may write it behind the layer's back."""
        self.assertIn("no_parking_took_place", SEMANTIC_OWNED_FACTS)


class TheMeaningIsRecognisedHoweverItIsWorded(unittest.TestCase):

    def test_every_wording_of_the_meaning_reaches_the_concept(self):
        for text in MEANS_IT:
            with self.subTest(account=text):
                self.assertIn(CONCEPT, concepts(text))

    def test_accounts_that_do_not_mean_it_are_left_alone(self):
        for text in DOES_NOT_MEAN_IT:
            with self.subTest(account=text):
                self.assertNotIn(CONCEPT, concepts(text))


class TheGroundBecomesReachable(unittest.TestCase):
    """End to end: the account alone establishes the gate fact, and KB-CON-02
    is argued once its remaining leg is answered."""

    NOTICE = dict(
        parking_location="Canada Water Estate, SE16 7LL", site_postcode="SE16 7LL",
        alleged_breach="Breach of terms and conditions",
        parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
        entry_time="14:00", exit_time="14:03",
    )
    ACCOUNT = ("I drove in, read the sign and left because I did not agree "
               "with the terms.")

    def _journey(self):
        from test_question_authority import case_with, run
        case, pipe = case_with(extra=self.NOTICE)
        _, qs = run(case, pipe, self.ACCOUNT)
        asked = []
        for _ in range(6):
            if not qs:
                break
            asked += [q["fact"] for q in qs]
            qs = pipe.answer(case, {q["fact"]: "no" for q in qs})
        return case, asked, pipe.generate(case)

    def test_the_account_alone_establishes_the_gate_fact(self):
        case, _, _ = self._journey()
        self.assertIs(case.get("no_parking_took_place"), True)

    def test_the_customer_is_not_asked_what_they_already_said(self):
        _, asked, _ = self._journey()
        self.assertNotIn("no_parking_took_place", asked)

    def test_the_no_contract_ground_is_argued(self):
        _, _, out = self._journey()
        self.assertIn("KB-CON-02", out.pack.module_ids)


if __name__ == "__main__":
    unittest.main()
