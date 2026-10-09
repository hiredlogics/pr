"""Departure, negation, and what was left behind (client brief §1, §2, §5).

Found by a holdout case with an invented operator and site, where the customer
wrote:

    "read their board, didnt like the terms at all so i turned round and went
     straight back out. never left the car there"

Three defects, all general, none specific to that wording:

  1. "never left the car there" flipped LEFT_SITE to NEGATED. The customer
     means the vehicle was never parked; the system read it as the driver never
     departing - the opposite - and held it alongside the departure asserted by
     the same sentence. A case cannot be both.

  2. "The terms were not acceptable and I drove away" read the departure as
     negated, because the negation window was clause-scoped and a clause can
     carry a negation attached to a different predicate.

  3. "turned round and went straight back out" was not a departure at all:
     TERMS_REJECTED_LEFT knew that vocabulary and LEFT_SITE did not.

The distinction the first and third share is whether something was left FROM
(a site) or left BEHIND (a purse). One object class governs both the positive
cue and the negation, so the two can never disagree again.
"""
from __future__ import annotations

import unittest

from pcn_appeal.semantics.meaning_bridge import extract_concepts_meaning_bridge


def concepts(text: str) -> set[tuple[str, str]]:
    return {(c["concept"], c["polarity"])
            for c in extract_concepts_meaning_bridge([text])}


class LeavingASiteIsNotLeavingAThingBehind(unittest.TestCase):
    """§2: an AI guess must not become a fact. "I left my purse" is not a
    statement that the vehicle departed, in either direction."""

    DEPARTED = [
        "I left the site.",
        "i left straight away",
        "i turned round and went back out",
        "i turned around and drove straight out",
        "drove straight off",
        "i exited immediately",
        "went right back out again",
    ]

    LEFT_BEHIND = [
        "I left my purse in the car.",
        "i left the keys with the attendant",
        "i left my phone on the seat",
        "I never left the car there.",
        "i did not leave my car there at all",
        "never left the van on site",
        "i never left my keys in it",
    ]

    STAYED = [
        "I never left the site.",
        "I did not leave the car park.",
        "I never left.",
        "I didn't leave at any time.",
        "i never left the retail park",
        "I did not leave the car parking area.",
    ]

    def test_a_departure_is_recognised(self):
        for text in self.DEPARTED:
            with self.subTest(text=text):
                self.assertIn(("LEFT_SITE", "AFFIRMED"), concepts(text))

    def test_a_thing_left_behind_is_not_a_departure(self):
        for text in self.LEFT_BEHIND:
            with self.subTest(text=text):
                self.assertNotIn(("LEFT_SITE", "AFFIRMED"), concepts(text))

    def test_a_thing_left_behind_is_not_a_denial_of_departure_either(self):
        """The sharp end of the defect: reading it as NEGATED asserts the
        opposite of what the customer said."""
        for text in self.LEFT_BEHIND:
            with self.subTest(text=text):
                self.assertNotIn(("LEFT_SITE", "NEGATED"), concepts(text))

    def test_staying_is_still_recognised_as_staying(self):
        """The fix must not cost the real negation: "I never left the site"
        means the vehicle stayed, and some grounds turn on that."""
        for text in self.STAYED:
            with self.subTest(text=text):
                self.assertIn(("LEFT_SITE", "NEGATED"), concepts(text))

    def test_a_car_park_is_a_place_not_a_thing(self):
        """"car" heads the object class and "car park" starts with it; the
        place must not be swallowed by the object."""
        for text in ("I did not leave the car park.",
                     "i never left the car parking area",
                     "I did not leave the car park bay."):
            with self.subTest(text=text):
                self.assertIn(("LEFT_SITE", "NEGATED"), concepts(text))


class ANegationBelongsToItsOwnPredicate(unittest.TestCase):
    """§5: no contradictory states. A negation before a coordinator belongs to
    the clause it is in, not to the one after it."""

    def test_a_rejection_of_the_terms_does_not_negate_the_departure(self):
        for text in ("The terms were not acceptable and I drove away.",
                     "I did not agree so I left the site.",
                     "i didnt accept their conditions so i went straight out"):
            with self.subTest(text=text):
                self.assertIn(("LEFT_SITE", "AFFIRMED"), concepts(text))

    def test_an_unclear_sign_does_not_negate_the_payment(self):
        self.assertIn(("PAYMENT_MADE", "AFFIRMED"),
                      concepts("The sign was not clear but I paid."))

    def test_a_negation_on_its_own_verb_still_holds(self):
        """The guard must not become a way to lose every negation."""
        for text, want in (("I did not pay.", ("PAYMENT_MADE", "NEGATED")),
                           ("no payment was made", ("PAYMENT_MADE", "NEGATED")),
                           ("I did not pay and I left.", ("PAYMENT_MADE", "NEGATED")),
                           ("I never left the site.", ("LEFT_SITE", "NEGATED"))):
            with self.subTest(text=text):
                self.assertIn(want, concepts(text))


class LeavingABuildingIsNotLeavingTheSite(unittest.TestCase):
    """§2 again. LEFT_SITE means the VEHICLE left the site - downstream rules
    (a second visit, the ANPR sequence) rely on that. A shopper walking out of
    a store and back in has not moved the vehicle, so affirming LEFT_SITE from
    it would put a fact in the case the customer never gave; narrative.py
    proposes that as a hypothesis for the customer to confirm instead.

    The distinction lives in the bridge, where the concept is decided, not in
    the rule that later reads the concept.
    """

    ON_FOOT = [
        "I left the store for a minute and came back in",
        "I left the shop, walked back to the car to get my purse and came back",
        "I went back to the car and then back into the store",
        "I left the supermarket and came back",
        "came out of the cafe and went back in",
    ]

    THE_VEHICLE = [
        "Entered for shopping, forgot my purse, left the site and returned later.",
        "Attended for shopping, left the site, returned later the same day.",
        "I drove off and came back later",
        "I pulled out and re-entered through the barrier",
        "I left the car park and came back",
    ]

    def test_walking_out_of_a_building_is_not_a_site_departure(self):
        for text in self.ON_FOOT:
            with self.subTest(text=text):
                self.assertNotIn("LEFT_SITE", {c for c, _ in concepts(text)},
                                 "claimed the vehicle left on a shopper's walk")

    def test_the_vehicle_leaving_the_site_still_is_one(self):
        """The guard must not cost the real departures: a genuine second visit
        is a live ANPR ground."""
        for text in self.THE_VEHICLE:
            with self.subTest(text=text):
                self.assertIn(("LEFT_SITE", "AFFIRMED"), concepts(text))


class TheHoldoutSentence(unittest.TestCase):
    """The case that found all three defects, in the customer's own words."""

    TEXT = ("read their board, didnt like the terms at all so i turned round "
            "and went straight back out. never left the car there")

    def test_it_reaches_the_no_contract_meaning(self):
        self.assertIn(("TERMS_REJECTED_LEFT", "AFFIRMED"), concepts(self.TEXT))

    def test_it_states_the_departure_it_describes(self):
        self.assertIn(("LEFT_SITE", "AFFIRMED"), concepts(self.TEXT))

    def test_it_holds_no_contradiction(self):
        found = concepts(self.TEXT)
        self.assertNotIn(("LEFT_SITE", "NEGATED"), found)
        polarities = [p for c, p in found if c == "LEFT_SITE"]
        self.assertLessEqual(len(polarities), 1,
                             f"LEFT_SITE held more than one polarity: {polarities}")

    def test_the_same_meaning_in_the_briefs_wording_agrees(self):
        """§1: same meaning, different words, same facts."""
        mine = {c for c, _ in concepts(self.TEXT)}
        for other in ("I read the sign and didn't agree, so I left.",
                      "The terms weren't acceptable and I drove away.",
                      "I looked at the conditions and decided not to stay."):
            with self.subTest(other=other):
                self.assertIn("TERMS_REJECTED_LEFT", {c for c, _ in concepts(other)})
        self.assertIn("TERMS_REJECTED_LEFT", mine)


if __name__ == "__main__":
    unittest.main()
