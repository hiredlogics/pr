"""P8 follow-ups (8 Oct 2026): the remaining development items from live testing,
and the client's review of the lists-and-wording document.

Development items
  * skipping the postcode question no longer loops (test_no_weak_fallback)
  * site postcode / jurisdiction read from the location wording
  * a back page printed by a different operator is set aside
  * letter quality: recorded times, no repeated closing, letterhead
  * a receipt is asked for when it is the only thing a ground lacks

Client review, 8 Oct 2026
  * drop-off zones carry no blanket "no permitted period"
  * a retail park / shopping centre prompts questions, never customer-only
  * the overstay is calculated and compared with the Code grace period
  * a reminder never starts a fresh appeal period
  * the airport list is an aid, not a whitelist (KB-POFA-08)
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from pcn_appeal.engines import derivation
from pcn_appeal.engines.derivation import derive, overstay_minutes, permitted_minutes
from pcn_appeal.engines.extraction import derive_jurisdiction, place_jurisdiction, postcode_in
from pcn_appeal.ingest import Ingested
from pcn_appeal.letterhead import full_letter, letter_document
from pcn_appeal.models import (CaseFile, Draft, DraftSentence, Fact, FactSource,
                               FactStatus, SourceKind)
from pcn_appeal.notice_completeness import different_notices, same_operator
from test_p8_client_instructions import DROPOFF, released_on
from test_question_authority import case_with, run

THIN = "I am the registered keeper. I do not accept this charge and want to appeal it."


def doc(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                  FactSource(SourceKind.DOCUMENT, "t")))


def notice(**facts):
    case = CaseFile("T")
    for k, v in facts.items():
        doc(case, k, v)
    return case


class SiteJurisdiction(unittest.TestCase):
    def test_postcode_printed_in_the_location(self):
        self.assertEqual(postcode_in("Quayside Shopping Centre (M50 3AH)"), "M50 3AH")
        case = notice(parking_location="Quayside Shopping Centre (M50 3AH)")
        self.assertEqual(derive_jurisdiction(case), "ENGLAND_WALES")
        self.assertEqual(case.get("site_postcode"), "M50 3AH")

    def test_place_names(self):
        self.assertEqual(place_jurisdiction("Sainsburys - Harringay, London"), "ENGLAND_WALES")
        self.assertEqual(place_jurisdiction("Stoke on Trent Retail Park"), "ENGLAND_WALES")
        self.assertEqual(place_jurisdiction("Buchanan Galleries, Glasgow"), "SCOTLAND")
        self.assertEqual(place_jurisdiction("Belfast City Centre"), "NORTHERN_IRELAND")
        # Two jurisdictions named: not decided, the postcode is asked as before.
        self.assertEqual(place_jurisdiction("London Road, Glasgow"), "UNKNOWN")
        self.assertEqual(place_jurisdiction("Canada Water Estate"), "UNKNOWN")


class BackPageFromAnotherOperator(unittest.TestCase):
    def test_names_compared(self):
        self.assertTrue(same_operator("Euro Car Parks Limited", "ECP Ltd"))
        self.assertTrue(same_operator("UK Parking Control", "UKPC"))
        self.assertTrue(same_operator("ParkMaven", "Park Maven Ltd"))
        self.assertFalse(same_operator("CP Plus Ltd", "Euro Car Parks"))
        self.assertIsNone(same_operator("Parking Ltd", "Car Parks"))   # nothing to compare

    def test_mismatched_back_page_is_another_notice(self):
        case = CaseFile("T")
        case.classifications = {
            "E1": {"document_type": "PRIVATE_PARKING_NOTICE", "pages": [{"page": 1, "side": "FRONT"}],
                   "issuer": {"name": "CP Plus Ltd", "kind": "PRIVATE_OPERATOR"}},
            "E2": {"document_type": "PRIVATE_PARKING_NOTICE", "pages": [{"page": 1, "side": "REVERSE"}],
                   "issuer": {"name": "Euro Car Parks Limited", "kind": "PRIVATE_OPERATOR"}},
        }
        self.assertEqual(different_notices(case)["field"], "operator_name")

    def test_appeals_body_on_the_reverse_is_not_an_operator(self):
        case = CaseFile("T")
        case.classifications = {
            "E1": {"document_type": "PRIVATE_PARKING_NOTICE",
                   "issuer": {"name": "CP Plus Ltd", "kind": "PRIVATE_OPERATOR"}},
            "E2": {"document_type": "PRIVATE_PARKING_NOTICE",
                   "issuer": {"name": "POPLA", "kind": "OTHER"}},
        }
        self.assertIsNone(different_notices(case))


class LetterQuality(unittest.TestCase):
    def test_recorded_times_added_once(self):
        from pcn_appeal.orchestrator import AppealPipeline
        draft = Draft("T", [[DraftSentence("I write as the registered keeper.")],
                            [DraftSentence("The Parking Charge Notice alleges: overstay at X.")]])
        pack = mock.Mock(verified_facts={"entry_time": "13:39", "exit_time": "17:06"}, fact_refs={})
        self.assertTrue(AppealPipeline._with_recorded_times(draft, pack))
        self.assertIn("entering at 13:39 and leaving at 17:06", draft.plain_text())
        self.assertFalse(AppealPipeline._with_recorded_times(draft, pack))

    def test_repeated_sentence_removed(self):
        from pcn_appeal.orchestrator import _without_repeats
        draft = Draft("T", [[DraftSentence("A point.")], [DraftSentence("A point."),
                                                          DraftSentence("Another.")]])
        self.assertEqual(_without_repeats(draft), 1)
        self.assertEqual(draft.plain_text().count("A point."), 1)

    def test_late_ntk_letter_has_one_closing(self):
        extra = dict(operator_name="Euro Car Parks", parking_location="Sainsburys - Harringay",
                     site_postcode="N4 1AA", alleged_breach="Overstayed the maximum time period",
                     parking_event_date="01/06/2026", notice_issue_date="05/07/2026",
                     entry_time="13:39", exit_time="17:06")
        with released_on(date(2026, 7, 10)):
            case, pipe = case_with(extra=extra)
            out = pipe.auto_appeal(case, THIN, None, skip_remaining=True).output
        self.assertTrue(out.letter)
        self.assertEqual(out.letter.count("For the reasons set out above"), 1, out.letter)
        self.assertIn("13:39", out.letter)

    def test_letterhead(self):
        case = notice(keeper_name="A Keeper", keeper_address="1 High St\nTown\nAB1 2CD",
                      operator_name="Euro Car Parks", pcn_number="PCN1", vrm="AB12CDE")
        d = letter_document(case, on=date(2026, 10, 8))
        self.assertEqual(d["from_lines"], ["A Keeper", "1 High St", "Town", "AB1 2CD"])
        self.assertFalse(d["to_complete"])          # no operator address read
        self.assertIn("[", d["to_lines"][-1])
        self.assertEqual(d["date"], "8 October 2026")
        self.assertEqual(d["subject"], "Re: Parking Charge Notice PCN1, vehicle AB12 CDE")
        text = full_letter("Body.", d)
        self.assertTrue(text.startswith("A Keeper"))
        self.assertIn("Dear Sir or Madam,\n\nBody.\n\nYours faithfully,", text)


class ReceiptRequest(unittest.TestCase):
    EXTRA = dict(operator_name="Euro Car Parks", parking_location="Sainsburys - Harringay",
                 site_postcode="N4 1AA",
                 alleged_breach="Your vehicle has overstayed the maximum time period allowed",
                 parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                 entry_time="13:39", exit_time="17:06")

    def test_asked_once_and_the_upload_opens_the_ground(self):
        with released_on(date(2026, 6, 20)):
            case, pipe = case_with(extra=self.EXTRA)
            run(case, pipe, THIN)
            pipe.answer(case, {"genuine_customer": True})
            asked = pipe.evidence_requests(case)
            self.assertEqual([(q["fact"], q["type"]) for q in asked], [("evidence:RECEIPT", "upload")])
            self.assertEqual(pipe.evidence_requests(case), [])        # once
            pipe.add_customer_evidence(case, "RECEIPT", [Ingested("X1", "receipt.jpg", text="34.20")])
            out = pipe.generate(case)
        self.assertIn("KB-CUST-01", out.pack.module_ids)
        self.assertIn("evidence of a purchase made during that visit is enclosed", out.letter)
        self.assertIn("RECEIPT: receipt.jpg", out.evidence_list)

    def test_not_asked_without_a_genuine_visit(self):
        case, pipe = case_with(extra=self.EXTRA)
        run(case, pipe, THIN)
        self.assertEqual(pipe.evidence_requests(case), [])


class ClientReview8Oct(unittest.TestCase):
    def test_dropoff_zone_alone_settles_no_permitted_period(self):
        # A long stay in a drop-off zone: nothing is assumed about a period.
        case = notice(parking_location="Some Airport Drop Off Zone", alleged_breach=DROPOFF)
        case.put(Fact("F-total_recorded_duration_min", "total_recorded_duration_min", 45,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
        derive(case)
        self.assertFalse(case.has("permitted_period_ended"))

    def test_overstay_in_a_dropoff_zone_is_an_overstay(self):
        case = notice(parking_location="Airport Drop Off Zone",
                      alleged_breach="Exceeded the free 10 minute period in the drop off zone")
        derive(case)
        self.assertEqual(case.get("alleged_breach_type"), "OVERSTAY")
        self.assertIs(case.get("permitted_period_ended"), True)

    def test_overstay_calculated_against_the_code_grace_period(self):
        self.assertEqual(permitted_minutes("Max stay 3 hours"), 180)
        self.assertEqual(permitted_minutes("1 hour 30 minutes"), 90)
        for mins, within in ((187, True), (195, False)):
            case = notice(parking_location="Sainsburys", alleged_breach="Overstayed the maximum stay",
                          permitted_period="Max stay 3 hours", parking_event_date=date(2026, 6, 1),
                          operator_ata="BPA")
            case.put(Fact("F-total_recorded_duration_min", "total_recorded_duration_min", mins,
                          FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
            derive(case)
            self.assertEqual(case.get("overstay_min"), mins - 180)
            self.assertIs(case.get("within_grace_period"), within, mins)

    def test_paid_until_time(self):
        case = notice(exit_time="15:38", paid_until_time="15:30")
        self.assertEqual(overstay_minutes(case), 8)

    def test_reminder_never_starts_a_fresh_period(self):
        with mock.patch.object(derivation, "today", lambda: date(2026, 7, 1)):
            case = notice(notice_issue_date=date(2026, 6, 25))     # the reminder's own date
            case.put(Fact("F-notice_stage", "notice_stage", "REMINDER", FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "t")))
            derive(case)
            self.assertEqual(case.get("appeal_period_status"), "MAY_HAVE_EXPIRED")
            self.assertIs(case.get("appeal_period_expired"), True)

    def test_printed_deadline_first(self):
        with mock.patch.object(derivation, "today", lambda: date(2026, 7, 1)):
            case = notice(notice_issue_date=date(2026, 5, 1), appeal_deadline_date=date(2026, 7, 15))
            derive(case)
            self.assertEqual(case.get("appeal_period_status"), "IN_TIME")

    def test_an_unlisted_airport_is_still_recognised(self):
        # Not in any list: the notice shows airport land, so the operator is
        # put to strict proof that it is relevant land (KB-POFA-08).
        loc = dict(operator_name="Example Parking", parking_location="Southampton Airport Forecourt",
                   site_postcode="SO18 2NL", alleged_breach="Stopping in a restricted area",
                   parking_event_date="22/06/2026", notice_issue_date="29/06/2026",
                   entry_time="09:00", exit_time="09:20")
        with released_on(date(2026, 7, 1)):
            case, pipe = case_with(extra=loc)
            out = pipe.auto_appeal(case, THIN, None, skip_remaining=True).output
        self.assertIs(case.get("statutory_control_possible"), True)
        self.assertFalse(case.has("relevant_land"))
        self.assertIn("KB-POFA-08", out.pack.module_ids)
        self.assertIn("strict proof that the location concerned is relevant land", out.letter)

    def test_model_reading_counts_without_any_pattern(self):
        case = notice(parking_location="Terminal Road North", alleged_breach="Stopped where prohibited",
                      statutory_land_indicator="AIRPORT")
        derive(case)
        self.assertIs(case.get("statutory_control_possible"), True)

    def test_railway_land_is_not_flagged(self):
        case = notice(parking_location="Stevenage Station Car Park", alleged_breach="No valid ticket",
                      statutory_land_indicator="RAILWAY")
        derive(case)
        self.assertFalse(case.has("statutory_control_possible"))

    def test_driver_letter_wording_stays_off(self):
        from pcn_appeal.kg.graph import KnowledgeGraph
        self.assertNotEqual(KnowledgeGraph().blocks["PP-DRIVER-001"].status, "ACTIVE")


class Notices7And8(unittest.TestCase):
    """The two notices not yet run on 7 Oct, in the harness (names omitted)."""

    MORRISONS = dict(operator_name="Euro Car Parks", pcn_number="88811908015", vrm="YG25JVV",
                     parking_location="Morrisons - Keighley - Market Car Park", site_postcode="",
                     site_country="ENGLAND",
                     alleged_breach="Your vehicle has overstayed the maximum time period allowed",
                     parking_event_date="28/05/2026", notice_issue_date="02/06/2026",
                     appeal_deadline_date="30/06/2026", entry_time="08:31", exit_time="10:46")
    QUAYSIDE = dict(operator_name="ParkMaven", parking_location="Quayside Shopping Centre (M50 3AH)",
                    site_postcode="", alleged_breach="No valid parking session",
                    parking_event_date="08/07/2026", notice_issue_date="13/07/2026",
                    entry_time="22:23", exit_time="23:02")

    def journey(self, extra, answers, upload=False, today=date(2026, 6, 10)):
        with released_on(today):
            case, pipe = case_with(extra=extra)
            r = pipe.auto_appeal(case, THIN)
            asked = []
            for _ in range(6):
                if not r.questions:
                    break
                asked += [q["fact"] for q in r.questions]
                if any(q.get("type") == "upload" for q in r.questions) and upload:
                    pipe.add_customer_evidence(case, "RECEIPT", [Ingested("X1", "receipt.jpg")])
                    r = pipe.auto_appeal(case, "", None)
                else:
                    r = pipe.auto_appeal(case, "", {q["fact"]: answers.get(
                        q["fact"], "20" if q.get("type") == "int" else "no") for q in r.questions})
        return case, asked, r.output

    def test_morrisons_customer_with_a_receipt(self):
        case, asked, out = self.journey(self.MORRISONS, {"genuine_customer": True}, upload=True)
        self.assertEqual(case.get("jurisdiction"), "ENGLAND_WALES")   # model: Keighley
        self.assertEqual(case.get("appeal_period_status"), "IN_TIME")  # printed deadline
        self.assertIn("genuine_customer", asked)
        self.assertIn("evidence:RECEIPT", asked)
        self.assertIn("KB-CUST-01", out.pack.module_ids)
        self.assertTrue(out.letter)

    def test_morrisons_thin_account_still_gets_a_letter(self):
        _, _, out = self.journey(self.MORRISONS, {})
        self.assertTrue(out.letter)

    def test_parkmaven_no_valid_session(self):
        case, asked, out = self.journey(self.QUAYSIDE, {}, today=date(2026, 7, 20))
        self.assertEqual(case.get("site_postcode"), "M50 3AH")
        self.assertIn("payment_made", asked)
        self.assertFalse(case.has("customer_only_site"))   # shopping centre: asked, not assumed
        self.assertTrue(out.letter)


class WordingVariants(unittest.TestCase):
    """Coverage: contravention wordings and store names seen on real notices."""

    BREACH = {
        "Your vehicle has overstayed the maximum time period allowed": "OVERSTAY",
        "Parked longer than the time paid for": "OVERSTAY",
        "Exceeded the maximum stay": "OVERSTAY",
        "Overstayed paid time": "OVERSTAY",
        "No valid parking session": "NO_PAYMENT",
        "Failed to make a valid payment": "NO_PAYMENT",
        "Use of Pick Up / Drop Off Zone without making a valid payment": "DROPOFF_ZONE",
        "Parked in a disabled bay without displaying a valid badge": "RESTRICTED_BAY",
        "Parked without a valid permit": "PERMIT",
    }
    CUSTOMER = ("Sainsbury's Harringay", "SAINSBURYS LOCAL", "Tesco Extra", "ASDA Superstore",
                "Morrisons - Keighley", "Aldi", "Lidl GB", "B & M Bargains", "Marks & Spencer",
                "M&S Foodhall", "McDonald's Drive Thru", "Costa Coffee")
    RETAIL = ("Quayside Shopping Centre", "Fort Retail Park", "Bicester Outlet Village")

    def test_breach_wordings(self):
        from pcn_appeal.engines.derivation import classify_breach
        for text, kind in self.BREACH.items():
            self.assertEqual(classify_breach(text), kind, text)

    def test_store_names(self):
        for loc in self.CUSTOMER:
            case = notice(parking_location=loc)
            derive(case)
            self.assertIs(case.get("customer_only_site"), True, loc)
        for loc in self.RETAIL:
            case = notice(parking_location=loc)
            derive(case)
            self.assertFalse(case.has("customer_only_site"), loc)
            self.assertIs(case.get("retail_site"), True, loc)


if __name__ == "__main__":
    unittest.main()
