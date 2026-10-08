"""P8 derivation layer: notice facts -> KB gate facts.

Fixtures are the facts read off real notices from the 7 Oct 2026 test run
(names and addresses omitted). Each test states what the notice alone should
settle, so a gate the document can answer is never left for the customer.
"""
import unittest
from datetime import date

from pcn_appeal.engines.derivation import classify_breach, derive
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind


def doc(case, name, value, status=FactStatus.CONFIRMED):
    case.put(Fact(f"F-{name}", name, value, status, FactSource(SourceKind.DOCUMENT, "test")))


def calc(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.DERIVED,
                  FactSource(SourceKind.CALCULATION, "duration_calc")))


def notice(location, breach, event=date(2026, 7, 9), ata="BPA", minutes=None):
    case = CaseFile("T")
    doc(case, "parking_location", location)
    doc(case, "alleged_breach", breach)
    doc(case, "parking_event_date", event)
    doc(case, "operator_ata", ata)
    if minutes is not None:
        calc(case, "total_recorded_duration_min", minutes)
    return case


class BreachClassification(unittest.TestCase):
    def test_wordings_from_real_notices(self):
        self.assertEqual(classify_breach(
            "Your vehicle has overstayed the maximum time period allowed"), "OVERSTAY")
        self.assertEqual(classify_breach(
            "the vehicle exceeded the maximum stay prominently displayed"), "OVERSTAY")
        self.assertEqual(classify_breach(
            "Use of Pick Up / Drop Off Zone without making a valid payment"), "DROPOFF_ZONE")
        self.assertEqual(classify_breach("No valid parking session"), "NO_PAYMENT")
        self.assertEqual(classify_breach(
            "Parked longer than the time paid for"), "OVERSTAY")

    def test_unknown_wording_is_not_guessed(self):
        self.assertIsNone(classify_breach("Breach of terms and conditions"))
        self.assertIsNone(classify_breach(None))


class Derivation(unittest.TestCase):
    def test_luton_dropoff_three_minutes(self):
        case = notice("Luton Airport Pick Up / Drop Off Zone",
                      "Use of Pick Up / Drop Off Zone without making a valid payment",
                      minutes=3)
        derive(case)
        self.assertEqual(case.get("alleged_breach_type"), "DROPOFF_ZONE")
        # Client 2026-10-08: not because it is a drop-off zone, but because no
        # overstay is alleged and the whole stay is inside the consideration period.
        self.assertIs(case.get("permitted_period_ended"), False)
        self.assertIn("no_overstay_alleged", case.facts["permitted_period_ended"].source.ref)
        self.assertIs(case.get("short_presence_before_acceptance"), True)
        self.assertIs(case.get("dropoff_site"), True)
        self.assertIs(case.get("relevant_land"), False)

    def test_supermarket_overstay(self):
        case = notice("Sainsburys - Harringay",
                      "Your vehicle has overstayed the maximum time period allowed",
                      minutes=207)
        derive(case)
        self.assertEqual(case.get("alleged_breach_type"), "OVERSTAY")
        self.assertIs(case.get("permitted_period_ended"), True)
        self.assertIs(case.get("customer_only_site"), True)
        # A long stay does not prove no time was spent considering terms (D-03).
        self.assertFalse(case.has("short_presence_before_acceptance"))
        self.assertFalse(case.has("relevant_land"))

    def test_bm_is_a_customer_site_a_shopping_centre_only_prompts(self):
        # Client 2026-10-08: named stores are customer sites; a retail park or
        # shopping centre only prompts the genuine-customer questions.
        case = notice("B&M Chatham - ME4 4HA", "No valid parking session")
        derive(case)
        self.assertIs(case.get("customer_only_site"), True)
        case = notice("Quayside Shopping Centre (M50 3AH)", "No valid parking session")
        derive(case)
        self.assertFalse(case.has("customer_only_site"))
        self.assertIs(case.get("retail_site"), True)
        # NO_PAYMENT does not settle whether a permitted period existed.
        self.assertFalse(case.has("permitted_period_ended"))

    def test_short_presence_needs_a_resolved_code_version(self):
        case = notice("Luton Airport Pick Up / Drop Off Zone", "drop off zone",
                      ata="NOT_SHOWN", minutes=3)
        derive(case)
        self.assertFalse(case.has("short_presence_before_acceptance"))

    def test_customer_answer_is_never_overwritten(self):
        case = notice("Luton Airport Pick Up / Drop Off Zone", "drop off zone", minutes=3)
        case.put(Fact("F-short_presence_before_acceptance", "short_presence_before_acceptance",
                      False, FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "answer")))
        derive(case)
        self.assertIs(case.get("short_presence_before_acceptance"), False)

    def test_railway_station_is_relevant_land_since_the_2025_order(self):
        # Railway Byelaws land was brought back into "relevant land" from
        # 26 Dec 2025, so a station car park must not be marked as byelaw land.
        case = notice("Stevenage Railway Station Car Park", "No valid parking session")
        derive(case)
        self.assertFalse(case.has("relevant_land"))

    def test_nothing_written_when_nothing_matches(self):
        case = notice("Canada Water Estate, SE16 7LL", "Breach of terms and conditions")
        self.assertEqual(derive(case), {})


if __name__ == "__main__":
    unittest.main()
