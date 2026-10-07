"""Client instructions of 7 Oct 2026, end to end.

  1. KB-POFA-07: confirmed statutory-control locations (Luton, Stansted drop-off
     zones) lead the letter with the client's wording plus a strict-proof
     fallback. A location is never assumed covered because the airport has
     byelaws; railway land stays out of the rule.
  2. Default keeper appeal (KB-KEEPER-01): no keeper case ends with no letter.
  3. Reminders and driver letters are not timed as a first Notice to Keeper;
     the original is linked where possible, else its date is asked once.
  4. After the appeal period: flagged and worded, never stopped.
  5. PP-POFA-007 no longer says "verified".
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date
from unittest import mock

import pcn_appeal.llm as llm_mod
from pcn_appeal.engines import derivation
from pcn_appeal.engines.derivation import derive, match_statutory_site, notice_stage
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.legal import pofa
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from test_question_authority import case_with, run

THIN = "I am the registered keeper. I do not accept this charge and want to appeal it."
DROPOFF = "Use of Pick Up / Drop Off Zone without making a valid payment"
LUTON = dict(operator_name="APCOA Parking", parking_location="Luton Airport Pick Up / Drop Off Zone",
             site_postcode="", alleged_breach=DROPOFF, parking_event_date="22/06/2026",
             notice_issue_date="29/06/2026", entry_time="02:38", exit_time="02:41")
STANSTED = dict(LUTON, parking_location="London Stansted Airport",
                parking_event_date="15/05/2026", notice_issue_date="22/05/2026",
                entry_time="12:59", exit_time="13:01")
NO_GROUND = dict(parking_location="Canada Water Estate", site_postcode="SE16 7LL",
                 alleged_breach="Breach of terms and conditions", parking_event_date="22/04/2026",
                 notice_issue_date="25/04/2026", entry_time="11:50", exit_time="18:15")
NCP = dict(operator_name="National Car Parks Limited", parking_location="Wolverhampton Fryer Street",
           site_postcode="WV1 1HT", alleged_breach="Parked longer than the time paid for",
           parking_event_date="20/03/2026", notice_issue_date="02/06/2026",
           entry_time="21:12", exit_time="00:13")
NCP_TEXT = ("Parking Charge to Driver\nOperator: National Car Parks Limited\nA Parking Charge to "
            "Keeper has previously been served on the registered keeper of the vehicle. We have "
            "been advised that you were the driver.")


@contextmanager
def released_on(day: date):
    """Release metadata the test LLM cannot supply, and a fixed 'today'."""
    real = llm_mod.probe
    with mock.patch.object(llm_mod, "probe",
                           lambda: {**real(), "provider": "test", "models": {"drafting": "ref"}}), \
            mock.patch.object(derivation, "today", lambda: day):
        yield


def journey(extra, answers=None, text=None):
    kw = {"extra": extra}
    if text:
        kw["text"] = text
    case, pipe = case_with(**kw)
    _, qs = run(case, pipe, THIN)
    asked = []
    for _ in range(6):
        if not qs:
            break
        asked += [q["fact"] for q in qs]
        qs = pipe.answer(case, {q["fact"]: (answers or {}).get(
            q["fact"], "5" if q.get("type") == "int" else "no") for q in qs})
    out = pipe.generate(case)
    plans = [a for a in case.audit if a.get("event") == "claim_plan_locked"]
    return case, pipe, out, asked, (plans[-1]["approved"] if plans else [])


def doc(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                  FactSource(SourceKind.DOCUMENT, "t")))


class StatutoryControlLocations(unittest.TestCase):
    def test_only_confirmed_locations_are_active(self):
        self.assertEqual(match_statutory_site("Luton Airport Pick Up / Drop Off Zone", DROPOFF)["status"],
                         "CONFIRMED")
        self.assertEqual(match_statutory_site("London Stansted Airport", DROPOFF)["status"], "CONFIRMED")
        # Same airport, a location not confirmed: recorded, changes nothing.
        self.assertEqual(match_statutory_site("Luton Airport Mid Stay Car Park",
                                              "Overstayed paid time")["status"], "PENDING")
        self.assertEqual(match_statutory_site("Heathrow Terminal 5 Forecourt", DROPOFF)["status"],
                         "PENDING")

    def test_a_pending_location_never_sets_relevant_land(self):
        case = CaseFile("T")
        doc(case, "parking_location", "Luton Airport Mid Stay Car Park")
        doc(case, "alleged_breach", "Overstayed paid time")
        derive(case)
        self.assertFalse(case.has("relevant_land"))
        self.assertTrue(any(a.get("event") == "statutory_control_site_pending" for a in case.audit))

    def test_railway_land_stays_out_of_the_airport_rule(self):
        self.assertIsNone(match_statutory_site("Stevenage Railway Station Car Park",
                                               "No valid parking session"))

    def test_the_calculator_emits_a_verified_finding(self):
        res = pofa.assess(jurisdiction="ENGLAND_WALES", relevant_land=False, notice_route="POSTAL",
                          parking_event_date=date(2026, 6, 22), notice_issue_date=date(2026, 6, 29))
        self.assertEqual(res.findings, ["POFA_NOT_RELEVANT_LAND"])


class AirportGround(unittest.TestCase):
    """Switched on and tested against the Luton and Stansted notices."""

    def _check(self, notice):
        with released_on(date(2026, 7, 1)):
            case, _, out, _, approved = journey(notice)
        self.assertEqual(approved[0], "KB-POFA-07", approved)      # leads
        self.assertEqual(case.get("pofa_findings"), ["POFA_NOT_RELEVANT_LAND"])
        self.assertTrue(out.letter, out.validation.issues if out.validation else None)
        self.assertIn("not ‘relevant land’ for the purposes of Schedule 4", out.letter)
        self.assertIn("put to strict proof of that position", out.letter)
        # PP-POFA-006 says Schedule 4 conditions "have also not been satisfied":
        # wrong for land outside Schedule 4, so it is not appended.
        self.assertNotIn("have also not been satisfied", out.letter)
        return case, out

    def test_luton(self):
        case, _ = self._check(LUTON)
        self.assertEqual(case.get("statutory_control_site"), "LTN-PUDO")
        # No postcode on the Luton notice: the confirmed location supplies it.
        self.assertEqual(case.get("jurisdiction"), "ENGLAND_WALES")

    def test_stansted(self):
        case, _ = self._check(STANSTED)
        self.assertEqual(case.get("statutory_control_site"), "STN-PUDO")

    def test_an_inferred_customer_fact_is_not_asserted(self):
        # The 3-minute stay opens KB-CON-01, but "time was required to consider
        # the terms" is the customer's to say (R-08c).
        with released_on(date(2026, 7, 1)):
            _, _, out, _, _ = journey(LUTON)
        self.assertNotIn("Time was required to locate and consider", out.letter)


class DefaultKeeperAppeal(unittest.TestCase):
    def test_no_specific_ground_still_gets_a_keeper_letter(self):
        with released_on(date(2026, 5, 1)):
            case, pipe, out, _, approved = journey(NO_GROUND)
        self.assertEqual(approved, ["KB-KEEPER-01"])
        self.assertTrue(out.letter)
        self.assertIn("strict proof of the legal basis on which it says the registered keeper is liable",
                      out.letter)
        pack = pipe.reasoning.pack_for(case, __import__(
            "pcn_appeal.engines.claim_plan_authority", fromlist=["x"]).latest_locked(case))
        self.assertEqual([c["id"] for c in pack.context_chunks if c["kind"] == "block"][:3],
                         ["PP-KEEPER-001", "PP-KEEPER-002", "PP-KEEPER-003"])

    def test_it_never_stacks_on_a_stronger_ground(self):
        with released_on(date(2026, 7, 1)):
            _, _, _, _, approved = journey(LUTON)
        self.assertNotIn("KB-KEEPER-01", approved)


class DocumentStage(unittest.TestCase):
    def test_driver_letter_is_not_timed_as_a_first_notice(self):
        with released_on(date(2026, 6, 10)):
            case, _, out, asked, approved = journey(NCP, {"original_notice_issue_date": "not sure"},
                                                    text=NCP_TEXT)
        self.assertEqual(case.get("notice_stage"), "DRIVER_LETTER")
        self.assertIn("original_notice_issue_date", asked)
        self.assertNotIn("POFA_POSTAL_LATE", case.get("pofa_findings") or [])  # no "62 days late"
        self.assertTrue(out.letter)

    def test_a_late_original_notice_is_argued_on_its_own_date(self):
        with released_on(date(2026, 6, 10)):
            case, _, out, _, approved = journey(NCP, {"original_notice_issue_date": "10/04/2026"},
                                                text=NCP_TEXT)
        self.assertIn("KB-POFA-02", approved)
        self.assertIn("10 April 2026", out.letter)
        self.assertNotIn("2 June 2026", out.letter)

    def test_reminder_links_to_the_uploaded_original(self):
        case = CaseFile("T")
        doc(case, "pcn_number", "PCN1")
        case.classifications = {
            "E1": {"document_type": "PRIVATE_PARKING_NOTICE", "stage": "INITIAL_NOTICE",
                   "document_date": "05/06/2026", "references": {"pcn_number": "PCN1"}},
            "E2": {"document_type": "PRIVATE_PARKING_NOTICE", "stage": "REMINDER",
                   "document_date": "20/07/2026", "references": {"pcn_number": "PCN1"}},
        }
        self.assertEqual(notice_stage(case), ("REMINDER", date(2026, 6, 5)))


class LateAppeal(unittest.TestCase):
    def test_flagged_and_worded_not_stopped(self):
        with released_on(date(2026, 10, 8)):
            case, _, out, _, _ = journey(LUTON)
        self.assertTrue(case.get("appeal_period_expired"))
        self.assertTrue(out.letter)
        self.assertIn("submitted after the appeal period stated on the notice", out.letter)
        self.assertEqual([n["code"] for n in out.customer_notices], ["APPEAL_PERIOD_MAY_HAVE_EXPIRED"])

    def test_in_time_no_late_wording(self):
        with released_on(date(2026, 7, 1)):
            case, _, out, _, _ = journey(LUTON)
        self.assertFalse(case.get("appeal_period_expired"))
        self.assertNotIn("submitted after the appeal period", out.letter)
        self.assertFalse(out.customer_notices)


class Wording(unittest.TestCase):
    def test_pp_pofa_007_drops_verified(self):
        text = KnowledgeGraph().blocks["PP-POFA-007"].letter_text
        self.assertTrue(text.startswith("For the reasons set out above,"))
        self.assertNotIn("verified", text)


if __name__ == "__main__":
    unittest.main()
