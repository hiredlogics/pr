"""The word "charge" is the parking charge, not an EV charging session.

The EV rule in engines/account.py matched a bare `charg(e|ing)`, so any account
containing "appeal the charge" or "parking charge notice" - almost every
customer's - became ev_charging_session=True. Live, against a photographed
Parent and Child bay notice, that released a letter asserting "a genuine
charging session" with EV_CHARGING as the lead ground. Nothing in the documents
or the customer's words said so; validation passed it because the fact existed.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines.account import _RULES, _relevant_to_allegation, assess_material_account
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.orchestrator import AppealPipeline

from support import analysis_llm

EV = next(r for r in _RULES if r.fact_name == "ev_charging_session")
BAY_BREACH = "Your vehicle was parked in a Parent and Child bay without being accompanied by a child"

# Generic accounts that mention the charge and nothing else.
GENERIC = [
    "I would like to appeal the charge.",
    "I received a parking charge notice in the post.",
    "This charge is unfair and I want to challenge it.",
    "They are charging me 100 pounds.",
    "I do not think I should have to pay this charge.",
    "The charge was issued at the car park.",
    "I want to dispute the parking charge.",
    "The operator is charging an excessive amount.",
    "I was charged for parking.",
    "The parking company charged my card.",
    "I received a Charge Certificate.",
    "Every time I visit I get a charge.",
    "CHARGE",
    "It is a charge of £100 and the event time is 12:23.",
]

# Accounts that genuinely say the vehicle was charging.
GENUINE = [
    "I was charging my electric car.",
    "My car was plugged in at the charger.",
    "I was using the EV charging bay.",
    "The vehicle was on charge at the charging point.",
    "I parked in the EV bay to charge my car.",
    "I was charging the car.",
    "My EV was charging.",
    "I parked at a rapid charger.",
    "The car was charging the whole time.",
    "I used the chargepoint.",
]


def _case(narrative: str, breach: str = BAY_BREACH) -> CaseFile:
    case = CaseFile("C-ev")
    case.put(Fact("F-alleged_breach", "alleged_breach", breach, FactStatus.EXTRACTED,
                  FactSource(SourceKind.DOCUMENT, "E1#p1")))
    case.raw_answers["narrative"] = narrative
    return case


class ChargeAloneIsNotEvCharging(unittest.TestCase):

    def test_the_word_charge_alone_never_sets_ev_charging(self):
        for text in GENERIC:
            with self.subTest(text=text):
                case = _case(text)
                result = assess_material_account(case)
                self.assertNotIn("ev_charging_session", case.facts)
                self.assertFalse(any("charging session" in p for p in result["propositions"]))
                self.assertFalse(case.get("account_contradicts_allegation"))

    def test_genuine_charging_accounts_are_still_read(self):
        for text in GENUINE:
            with self.subTest(text=text):
                case = _case(text)
                assess_material_account(case)
                self.assertIs(case.get("ev_charging_session"), True)
                self.assertEqual(case.facts["ev_charging_session"].source.kind,
                                 SourceKind.CUSTOMER_FREE_TEXT)

    def test_a_denied_charging_session_is_not_read_as_one(self):
        for text in ("I was not charging my car.", "The car was never plugged in."):
            with self.subTest(text=text):
                case = _case(text)
                assess_material_account(case)
                self.assertNotIn("ev_charging_session", case.facts)

    def test_ev_relevance_is_not_read_from_ordinary_words(self):
        """The family tokens "ev" and "charg" matched inside "every", "event" and
        "parking charge", so any allegation looked like an EV one."""
        for breach in ("Parked in a Parent and Child bay on every visit",
                       "Parking charge: event at 12:23, remained over the time limit",
                       "Overstayed the maximum stay at a retail park"):
            with self.subTest(breach=breach):
                self.assertFalse(_relevant_to_allegation(EV, breach.lower()))
        self.assertTrue(_relevant_to_allegation(EV, "remained in an ev bay while not charging"))


class NoEvGroundFromTheWordCharge(unittest.TestCase):
    """End to end through the private pipeline: a bay notice and a generic
    account must not produce the EV ground or any charging wording."""

    FIELDS = {
        k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
        for k, v in dict(operator_name="Example Parking Ltd", pcn_number="PCN000111",
                         vrm="AB12CDE", parking_location="Example Store car park",
                         site_postcode="M1 1AA", parking_event_date="01/06/2026",
                         notice_issue_date="03/06/2026", charge_amount="£100",
                         alleged_breach=BAY_BREACH, observation_time="12:23",
                         event_time="12:23", operator_ata="BPA").items()}

    def test_generic_account_releases_no_ev_ground(self):
        from pcn_appeal.models import EvidenceItem
        pipe = AppealPipeline(analysis_llm({"fields": self.FIELDS, "doc_types": {"E1": "NTK"}}))
        case = CaseFile("C-ev-e2e")
        case.evidence["E1"] = EvidenceItem("E1", "OTHER", "notice.txt",
                                           text="Notice to Keeper front\fReverse terms")
        pipe.ingest(case)
        confirmed = [n for n in self.FIELDS]
        pipe.confirm(case, {}, confirmed, "I would like to appeal the charge.")
        out = pipe.generate(case)
        routes = [out.pack.primary_route, *out.pack.secondary_routes]
        self.assertNotIn("EV_CHARGING", routes)
        self.assertNotIn("ev_charging_session", case.facts)
        self.assertNotIn("charging session", (out.letter or "").lower())


if __name__ == "__main__":
    unittest.main()
