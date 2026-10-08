"""P8 fact-to-ground matching, end to end on notices from the 7 Oct 2026 test run.

Each notice gets the same thin account the live test used ("I am the keeper, I
do not accept this"). Before P8 every one of them was asked nothing and ended
with no supported ground. Now the notice's own facts point at grounds
(derivation), the gates decide the questions, and an answer that settles a gate
is put to the customer.

Names, addresses and registrations are omitted; locations, wording and times
are as printed.
"""
from __future__ import annotations

import unittest
from datetime import date

from pcn_appeal.engines.analysis import document_pointed_gaps
from pcn_appeal.engines.derivation import derive
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from test_question_authority import case_with, run

THIN = "I am the registered keeper. I do not accept this charge and want to appeal it."

LUTON = dict(parking_location="Luton Airport Pick Up / Drop Off Zone", site_postcode="LU2 9LY",
             alleged_breach="Use of Pick Up / Drop Off Zone without making a valid payment",
             parking_event_date="22/06/2026", notice_issue_date="29/06/2026",
             entry_time="02:38", exit_time="02:41")
SAINSBURYS = dict(parking_location="Sainsburys - Harringay", site_postcode="N4 1AA",
                  alleged_breach="Your vehicle has overstayed the maximum time period allowed",
                  parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                  entry_time="13:39", exit_time="17:06")
QUAYSIDE = dict(parking_location="Quayside Shopping Centre (M50 3AH)", site_postcode="M50 3AH",
                alleged_breach="No valid parking session",
                parking_event_date="08/07/2026", notice_issue_date="13/07/2026",
                entry_time="22:23", exit_time="23:02")


def journey(extra, answers):
    """Run confirm + answer rounds; return (facts asked, approved claim modules)."""
    case, pipe = case_with(extra=extra)
    _, qs = run(case, pipe, THIN)
    asked = []
    for _ in range(8):
        if not qs:
            break
        asked += [q["fact"] for q in qs]
        qs = pipe.answer(case, {q["fact"]: answers.get(q["fact"], "no") for q in qs})
    pipe.generate(case)
    plans = [a for a in case.audit if a.get("event") == "claim_plan_locked"]
    return case, asked, (plans[-1]["approved"] if plans else [])


class DocumentPointedGaps(unittest.TestCase):
    """Which grounds the notice alone points at, and what is left to ask."""

    @classmethod
    def setUpClass(cls):
        cls.kg = KnowledgeGraph()

    def _case(self, loc, breach, mins):
        case = CaseFile("T")
        for n, v in (("parking_location", loc), ("alleged_breach", breach),
                     ("parking_event_date", date(2026, 7, 9)), ("operator_ata", "BPA"),
                     ("entry_time", "10:00"), ("exit_time", "10:03")):
            case.put(Fact(f"F-{n}", n, v, FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "t")))
        case.put(Fact("F-total_recorded_duration_min", "total_recorded_duration_min", mins,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "duration_calc")))
        derive(case)
        return case

    def gaps(self, *args):
        case = self._case(*args)
        return dict(document_pointed_gaps(self.kg, case, case.fact_view()))

    def test_airport_dropoff_points_at_consideration_and_dropoff(self):
        g = self.gaps(LUTON["parking_location"], LUTON["alleged_breach"], 3)
        self.assertEqual(g.get("KB-CON-01"), {"payment_made"})
        # payment_made is ACT-02's blocker; unknown, it is asked too (P8 follow-up).
        self.assertEqual(g.get("KB-ACT-02"), {"dropoff_activity", "payment_made"})

    def test_supermarket_overstay_points_at_grace_and_customer(self):
        g = self.gaps(SAINSBURYS["parking_location"], SAINSBURYS["alleged_breach"], 207)
        self.assertEqual(g.get("KB-GRACE-01"), {"exit_delay_min"})
        self.assertEqual(g.get("KB-CUST-01"), {"genuine_customer"})
        self.assertNotIn("KB-CON-01", g)

    def test_a_universal_exists_condition_points_at_nothing(self):
        # Every notice has alleged_breach and entry/exit times; those alone must
        # not make loading, EV charging or ANPR-duration grounds look pointed.
        g = self.gaps("Canada Water Estate, SE16 7LL", "Breach of terms and conditions", 384)
        self.assertEqual(g, {})


class ThinAccountJourneys(unittest.TestCase):
    """The live failure: thin account -> no questions -> no ground."""

    def test_airport_dropoff_reaches_consideration(self):
        case, asked, approved = journey(LUTON, {"payment_made": "no"})
        self.assertIn("payment_made", asked)
        self.assertIn("KB-CON-01", approved)
        self.assertIs(case.get("relevant_land"), False)

    def test_supermarket_overstay_reaches_grace_period(self):
        _, asked, approved = journey(SAINSBURYS, {"exit_delay_min": "8"})
        self.assertIn("exit_delay_min", asked)
        self.assertIn("KB-GRACE-01", approved)

    def test_no_valid_session_asks_about_payment(self):
        _, asked, approved = journey(QUAYSIDE, {})
        self.assertIn("payment_made", asked)
        # "No payment" and nothing else: no specific ground is invented; the
        # default keeper appeal (client instruction 2026-10-07) carries it.
        self.assertEqual(approved, ["KB-KEEPER-01"])

    def test_a_long_empty_account_is_still_thin(self):
        # 79 characters, no facts. The old 40-char rule treated this as a full
        # account and asked nothing.
        _, asked, _ = journey(LUTON, {"payment_made": "no"})
        self.assertTrue(asked)


if __name__ == "__main__":
    unittest.main()
