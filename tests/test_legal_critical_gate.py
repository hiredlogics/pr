"""P7 B1 - the Legal-Critical Input Gate.

A field a legal conclusion can turn on is never trusted on one reading alone:
the deterministic reader cross-checks the model on every extraction (EX-18),
impossible chronology never continues confidently (EX-03), an UNCERTAIN field
is never promoted by default at confirmation, an answer is only a fact when
its question was asked, and a VERIFIED legal finding needs confirmed or
cross-checked supporting facts.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from pcn_appeal.engines import extraction as ex
from pcn_appeal.engines.extraction import ExtractionEngine
from pcn_appeal.legal import findings as lf
from pcn_appeal.legal import pofa
from pcn_appeal.models import (CaseFile, EvidenceItem, Fact, FactSource, FactStatus,
                               SourceKind)
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM

NOTICE = """Parking Charge Notice
Operator Name: Acme Parking Ltd
PCN Number: PCN123456
Vehicle Registration: AB12 CDE
Location: Retail Park
Postcode: M1 1AA
Date of Contravention: 12/06/2026
Date of Issue: {issue}
Entry Time: 14:05
Exit Time: 16:58
Charge: £100
Alleged Breach: Overstayed the maximum permitted period
Trade Association: BPA
"""


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


FULL = dict(
    operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12CDE",
    parking_location="Retail Park", site_postcode="M1 1AA",
    parking_event_date="12/06/2026", notice_issue_date="16/06/2026",
    entry_time="14:05", exit_time="16:58",
    charge_amount="£100", alleged_breach="Overstayed the maximum permitted period",
    operator_ata="BPA",
)


def case_with(extra=None, *, notice_issue="16/06/2026", text=None):
    f = dict(FULL, **(extra or {}))
    f = {k: v for k, v in f.items() if v is not None}
    llm = ReferenceAnalysisLLM(
        {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]})
    case = CaseFile("C-B1", evidence={"E1": EvidenceItem(
        "E1", "NTK", "notice.pdf",
        text=text if text is not None else NOTICE.format(issue=notice_issue))})
    return case, AppealPipeline(llm)


class CrossCheckIsAlwaysOn(unittest.TestCase):
    def test_agreement_is_recorded(self):
        case, pipe = case_with()
        flags = pipe.ingest(case)
        checked = ex.cross_checked(case)
        self.assertIn("notice_issue_date", checked)
        self.assertIn("entry_time", checked)
        self.assertIn("alleged_breach", checked)
        self.assertFalse([f for f in flags if f.startswith("conflict:")], flags)

    def test_disagreement_makes_the_field_uncertain(self):
        # The model reads 16/07 where the notice prints 16/06: never continued
        # with confidently - UNCERTAIN, flagged, audited with both readings.
        case, pipe = case_with({"notice_issue_date": "16/07/2026"})
        flags = pipe.ingest(case)
        self.assertIn("conflict:notice_issue_date", flags)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)
        self.assertIsNone(case.get("notice_issue_date"))
        audit = [a for a in case.audit if a.get("event") == "cross_check_mismatch"]
        self.assertTrue(audit)
        self.assertIn("2026-06-16", str(audit[0]["document_values"]))

    def test_no_text_proves_nothing(self):
        # A photographed notice has no text layer: the reader returning nothing
        # neither confirms nor contradicts; the value stays EXTRACTED.
        case, pipe = case_with(text="")
        pipe.ingest(case)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.EXTRACTED)
        self.assertNotIn("notice_issue_date", ex.cross_checked(case))

    def test_prose_label_corroboration_survives_a_second_wording(self):
        text = NOTICE.format(issue="16/06/2026") + \
            "\nReason for charge: the vehicle overstayed the maximum permitted period\n"
        case, pipe = case_with(text=text)
        pipe.ingest(case)
        self.assertIn("alleged_breach", ex.cross_checked(case))

    def test_two_labelled_dates_are_a_real_conflict(self):
        text = NOTICE.format(issue="16/06/2026") + "\nIssue Date: 18/06/2026\n"
        case, pipe = case_with(text=text)
        flags = pipe.ingest(case)
        self.assertIn("conflict:notice_issue_date", flags)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)


class ChronologyNeverContinuesConfidently(unittest.TestCase):
    def _bare(self, **facts):
        case = CaseFile("C-CHR")
        for n, v in facts.items():
            case.put(Fact(f"F-{n}", n, v, FactStatus.EXTRACTED,
                          FactSource(SourceKind.DOCUMENT, "E1#p1")))
        return case

    def test_issue_before_event(self):
        case = self._bare(parking_event_date=date(2026, 6, 12),
                          notice_issue_date=date(2026, 6, 1))
        flags = ex.validate_chronology(case, stage="test")
        self.assertIn("chronology:issue_before_event", flags)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)

    def test_future_date(self):
        case = self._bare(notice_issue_date=date.today() + timedelta(days=30))
        flags = ex.validate_chronology(case, stage="test")
        self.assertIn("chronology:future:notice_issue_date", flags)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)

    def test_ntd_before_event_marks_both(self):
        case = self._bare(parking_event_date=date(2026, 6, 12),
                          ntd_date=date(2026, 6, 1))
        ex.validate_chronology(case, stage="test")
        self.assertEqual(case.facts["ntd_date"].status, FactStatus.UNCERTAIN)
        self.assertEqual(case.facts["parking_event_date"].status, FactStatus.UNCERTAIN)

    def test_ntk_before_ntd_marks_both(self):
        case = self._bare(ntd_date=date(2026, 6, 12),
                          notice_issue_date=date(2026, 6, 5))
        ex.validate_chronology(case, stage="test")
        self.assertEqual(case.facts["ntd_date"].status, FactStatus.UNCERTAIN)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)

    def test_received_before_issue(self):
        case = self._bare(notice_issue_date=date(2026, 6, 16),
                          notice_received_date=date(2026, 6, 10))
        flags = ex.validate_chronology(case, stage="test")
        self.assertIn("chronology:received_before_issue", flags)
        self.assertEqual(case.facts["notice_received_date"].status, FactStatus.UNCERTAIN)

    def test_inverted_times_mark_both(self):
        case = self._bare(entry_time="16:58", exit_time="14:05")
        flags = ex.validate_chronology(case, stage="test")
        self.assertIn("chronology:times_inverted", flags)
        self.assertEqual(case.facts["entry_time"].status, FactStatus.UNCERTAIN)
        self.assertEqual(case.facts["exit_time"].status, FactStatus.UNCERTAIN)

    def test_a_plausible_chronology_raises_nothing(self):
        case = self._bare(parking_event_date=date(2026, 6, 12),
                          ntd_date=date(2026, 6, 12),
                          notice_issue_date=date(2026, 6, 16),
                          notice_received_date=date(2026, 6, 18),
                          entry_time="14:05", exit_time="16:58")
        self.assertEqual(ex.validate_chronology(case, stage="test"), [])


class ConfirmationNeverDefaultsUncertain(unittest.TestCase):
    def test_uncertain_is_not_promoted_by_blanket_confirmation(self):
        case, pipe = case_with({"notice_issue_date": "16/07/2026"})  # mismatch
        pipe.ingest(case)
        ExtractionEngine.confirm(case, {}, list(case.facts))
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)
        self.assertTrue(any(a.get("event") == "uncertain_not_auto_confirmed"
                            and a.get("fact") == "notice_issue_date"
                            for a in case.audit))

    def test_an_explicit_correction_resolves_it(self):
        case, pipe = case_with({"notice_issue_date": "16/07/2026"})
        pipe.ingest(case)
        ExtractionEngine.confirm(case, {"notice_issue_date": "16/06/2026"},
                                 list(case.facts))
        f = case.facts["notice_issue_date"]
        self.assertEqual(f.status, FactStatus.CORRECTED)
        self.assertEqual(f.value, date(2026, 6, 16))

    def test_an_unparseable_corrected_date_is_an_error(self):
        case, pipe = case_with()
        pipe.ingest(case)
        with self.assertRaises(ValueError):
            ExtractionEngine.confirm(case, {"notice_issue_date": "soonish"}, [])

    def test_a_correction_creating_impossible_chronology_is_revalidated(self):
        # The cross-check made the issue date UNCERTAIN, so it is the
        # customer's to correct - but a correction that puts issue before the
        # event is impossible and goes straight back to UNCERTAIN.
        case, pipe = case_with({"notice_issue_date": "16/07/2026"})
        pipe.ingest(case)
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)
        ExtractionEngine.confirm(case, {"notice_issue_date": "01/06/2026"},
                                 list(case.facts))
        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)
        self.assertTrue(any(a.get("event") == "chronology_flags"
                            and a.get("stage") == "confirm" for a in case.audit))

    def test_a_correction_to_a_confident_reading_needs_confirmation(self):
        # A confident, cross-checked document reading is not silently replaced
        # by a typed value: the write opens a confirmation instead.
        case, pipe = case_with()
        pipe.ingest(case)
        ExtractionEngine.confirm(case, {"notice_issue_date": "17/06/2026"}, [])
        self.assertEqual(case.facts["notice_issue_date"].value, date(2026, 6, 16))

    def test_pcn_conflict_cleared_only_by_explicit_entry(self):
        case = CaseFile("C-PCN")
        case.put(Fact("F-pcn_number", "pcn_number", "PCN123456",
                      FactStatus.UNCERTAIN, FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case.put(Fact("F-pcn_conflict", "pcn_conflict", True, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "pcn_cross_check")))
        ExtractionEngine.confirm(case, {}, ["pcn_number"])
        self.assertTrue(case.get("pcn_conflict"),
                        "blanket confirmation must not pick between two charge numbers")
        ExtractionEngine.confirm(case, {"pcn_number": "PCN123456"}, [])
        self.assertFalse(case.get("pcn_conflict"))


class RouteIsConfirmedNotGuessed(unittest.TestCase):
    def test_unknown_route_raises_the_question_once(self):
        case = CaseFile("C-RT", evidence={"E1": EvidenceItem("E1", "OTHER", "x.pdf", text="x")})
        case.put(Fact("F-notice_route", "notice_route", "UNKNOWN", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_route_rule")))
        qs = AppealPipeline._route_question(case)
        self.assertEqual([q["fact"] for q in qs], ["notice_route"])
        self.assertEqual(sorted(qs[0]["options"]), ["POSTAL", "WINDSCREEN"])
        case.asked_questions.append("notice_route")
        self.assertEqual(AppealPipeline._route_question(case), [])

    def test_a_resolved_route_asks_nothing(self):
        case = CaseFile("C-RT2", evidence={"E1": EvidenceItem("E1", "NTK", "x.pdf", text="x")})
        case.put(Fact("F-notice_route", "notice_route", "POSTAL", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_route_rule")))
        self.assertEqual(AppealPipeline._route_question(case), [])

    def test_outside_england_wales_asks_nothing(self):
        case = CaseFile("C-RT3", evidence={"E1": EvidenceItem("E1", "OTHER", "x.pdf", text="x")})
        case.put(Fact("F-notice_route", "notice_route", "UNKNOWN", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_route_rule")))
        case.put(Fact("F-jurisdiction", "jurisdiction", "SCOTLAND", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "postcode_jurisdiction")))
        self.assertEqual(AppealPipeline._route_question(case), [])


class AnswersRequireTheirQuestion(unittest.TestCase):
    def test_an_unasked_fact_is_refused_and_audited(self):
        case, pipe = case_with()
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "")
        pipe.answer(case, {"driver_was_me": "yes"})
        self.assertIsNone(case.get("driver_was_me"))
        self.assertNotIn("driver_was_me", case.raw_answers)
        self.assertTrue(any(a.get("event") == "answer_refused_unasked"
                            and a.get("fact") == "driver_was_me" for a in case.audit))

    def test_an_asked_fact_is_recorded(self):
        case, pipe = case_with()
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I paid on the app for this stay")
        self.assertIn("payment_made", [q["fact"] for q in case.pending_questions])
        pipe.answer(case, {"payment_made": "yes"})
        self.assertIs(case.get("payment_made"), True)


class FindingsNeedTrustedSupport(unittest.TestCase):
    LATE = dict(parking_event_date="01/06/2026", notice_issue_date="20/06/2026")

    def _case(self, status: FactStatus, checked=False):
        case = CaseFile("C-LF")
        for n, v in self.LATE.items():
            case.put(Fact(f"F-{n}", n, ex.parse_uk_date(v), status,
                          FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case.put(Fact("F-notice_route", "notice_route", "POSTAL", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_route_rule")))
        case.put(Fact("F-jurisdiction", "jurisdiction", "ENGLAND_WALES", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "postcode_jurisdiction")))
        if checked:
            case.put(Fact("F-cross_checked_fields", "cross_checked_fields",
                          sorted(self.LATE), FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "legal_critical_cross_check")))
        res = pofa.assess(jurisdiction="ENGLAND_WALES", relevant_land=True,
                          notice_route="POSTAL",
                          parking_event_date=date(2026, 6, 1),
                          notice_issue_date=date(2026, 6, 20))
        return case, res

    def _finding(self, case, res):
        recs = lf.evaluate(case, res)
        return next(r for r in recs if r["finding_type"] == "POFA_POSTAL_LATE")

    def test_extracted_dates_alone_do_not_verify_a_breach(self):
        case, res = self._case(FactStatus.EXTRACTED)
        rec = self._finding(case, res)
        self.assertEqual(rec["status"], lf.UNRESOLVED)
        self.assertIn("not confirmed or cross-checked",
                      rec["calculation_result"].get("note", ""))

    def test_confirmed_dates_verify(self):
        case, res = self._case(FactStatus.CONFIRMED)
        self.assertEqual(self._finding(case, res)["status"], lf.VERIFIED)

    def test_cross_checked_dates_verify(self):
        case, res = self._case(FactStatus.EXTRACTED, checked=True)
        self.assertEqual(self._finding(case, res)["status"], lf.VERIFIED)


if __name__ == "__main__":
    unittest.main()
