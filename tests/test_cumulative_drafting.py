"""P6.2 - cumulative grounds and particularised drafting.

The two production defects this phase closes:

    1. A ground the system had already established disappeared when the
       customer added a new fact (Case Intelligence stopped proposing it, and
       the old plan builder treated the model's selection as the only route to
       SUPPORTED). Now a verified legal finding licenses its module by itself
       (VERIFIED_FINDING), and a ground supported in the previous locked plan
       survives as long as its gate and facts hold (CARRIED_FORWARD).
    2. A calculated statutory timing defect was drafted as the generic "not
       delivered within the applicable statutory period". Now the letter must
       state the calculation itself - the event date, the issue date, the
       statutory deadline, the deemed delivery date and the day count - and
       VAL-PARTICULARS fails the draft before a customer sees it. VAL-COVERAGE
       likewise fails a letter that never argues an approved ground.

Everything generic: the rules read the finding's own calculation and the
plan's own items. No operator- or PCN-specific wording anywhere.
"""
from __future__ import annotations

import re
import unittest

from test_scenarios import make_case, run
from test_verified_legal_findings import CANCEL, DEFECT, LATE, rules

from pcn_appeal.engines.claim_plan_authority import (CARRIED_FORWARD, REJECTED, SUPPORTED,
                                                     VERIFIED_FINDING, latest_locked)
from pcn_appeal.legal import findings as lf
from pcn_appeal.models import (CaseState, Draft, DraftSentence, Fact, FactSource, FactStatus,
                               SourceKind)

# LATE: event 01/06/2026, issued 20/06/2026 -> deadline 15/06/2026,
# presumed delivery 23/06/2026, 8 days outside the period.
PARTICULARS = ("1 June 2026", "20 June 2026", "15 June 2026", "23 June 2026")


def sentence(text, modules, facts=()):
    return DraftSentence(text, list(facts), list(modules), [])


def draft(case, *sentences):
    return Draft(case.case_id, [[s] for s in sentences]
                 + [[DraftSentence(CANCEL, module_refs=["STRUCTURAL"])]])


def answered(name, value):
    return Fact(f"F-{name}", name, value, FactStatus.ANSWERED,
                FactSource(SourceKind.ANSWER, f"answer:{name}"))


# ------------------------------------------------------- cumulative grounds
class CumulativeGrounds(unittest.TestCase):
    def test_a_verified_finding_supports_its_ground_without_the_selection(self):
        """Case Intelligence omits the timing module entirely; the verified
        calculation licenses it anyway."""
        case, pipe = make_case(LATE)
        run(case, pipe, "", {})
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        item = plan.item("KB-POFA-02")
        self.assertEqual(item.status, SUPPORTED)
        self.assertEqual(item.decision, VERIFIED_FINDING)
        self.assertIn("POFA_POSTAL_LATE", item.reason)

    def test_adding_a_customer_fact_does_not_drop_the_timing_ground(self):
        """Alpha's production case: the PoFA ground vanished when the
        multiple-visit account was added. Both grounds must now coexist."""
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27"))
        out1 = run(case, pipe, "", {})
        self.assertIn("KB-POFA-02", latest_locked(case).supported_ids)
        pipe.answer(case, {"multiple_visits": True})
        out2 = pipe.generate(case)
        plan = latest_locked(case)
        self.assertIn("KB-POFA-02", plan.supported_ids, plan.trace())
        self.assertIn("KB-ANPR-01", plan.supported_ids, plan.trace())
        self.assertEqual(out2.state, CaseState.RELEASED, out2.validation.issues)
        # and the released letter argues both
        self.assertTrue(all(p in out2.letter for p in PARTICULARS), out2.letter)
        grounding = [v for v in case.draft_versions if v["released"]][-1]["grounding"]
        argued = {m for g in grounding if g["status"] == "GROUNDED"
                  for m in g["claim_plan_items"]}
        self.assertIn("KB-POFA-02", argued)
        self.assertIn("KB-ANPR-01", argued)

    def test_a_carried_ground_lapses_when_its_gate_no_longer_holds(self):
        """Cumulative never means zombie: when the facts change under a
        supported ground, the carried item is rejected with the reason."""
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27"))
        run(case, pipe, "", {"multiple_visits": True})
        self.assertIn("KB-ANPR-01", latest_locked(case).supported_ids)
        case.put(answered("multiple_visits", False))
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        item = plan.item("KB-ANPR-01")
        self.assertEqual(item.status, REJECTED)
        self.assertIn("no longer holds", item.reason)

    def test_carry_forward_names_the_previous_plan(self):
        case, pipe = make_case(dict(LATE, entry_time="10:00", exit_time="13:27"))
        run(case, pipe, "", {"multiple_visits": True})
        previous = latest_locked(case)
        case.analysis_module_ids = []
        plan = pipe.claim_authority.build(case, version=9)
        item = plan.item("KB-ANPR-01")
        self.assertEqual(item.status, SUPPORTED)
        self.assertIn(item.decision, (VERIFIED_FINDING, CARRIED_FORWARD))
        if item.decision == CARRIED_FORWARD:
            self.assertIn(f"plan v{previous.version}", item.reason)


# ------------------------------------------------- particularised drafting
class ParticularisedDrafting(unittest.TestCase):
    def test_the_released_letter_states_the_full_calculation(self):
        """Alpha's system-wide rule: event date, issue date, deadline, deemed
        delivery, the day count, then the transfer-failure conclusion."""
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        for p in PARTICULARS:
            self.assertIn(p, out.letter, out.letter)
        self.assertTrue(lf.days_stated(8, out.letter), out.letter)
        self.assertIn("does not transfer", out.letter)

    def test_generic_defect_wording_is_blocked(self):
        """The P6.1-legal sentence ("not delivered within the applicable
        statutory period") is verified but unparticularised: VAL-PARTICULARS
        names every missing value."""
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        d = draft(case, sentence(DEFECT, ("KB-POFA-02",)))
        r = pipe.draft_validation.check(d, out.pack,
                                        {f.fact_id for f in case.facts.values()})
        self.assertIn("VAL-PARTICULARS", rules(r))
        issue = next(i for i in r.issues if i.rule == "VAL-PARTICULARS")
        self.assertIsNone(issue.sentence)              # letter-level: untrimmable
        for v in ("2026-06-15", "2026-06-23", "8 days"):
            self.assertIn(v, issue.message)
        self.assertNotIn("VAL-LEGAL-FINDING", rules(r))  # the defect itself is verified

    def test_the_particularised_letter_passes(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        rec = next(f for f in out.pack.legal_findings
                   if f["finding_type"] == "POFA_POSTAL_LATE")
        text = lf.particularised_sentence(rec)
        d = draft(case, sentence(text, ("KB-POFA-02",),
                                 (case.facts["parking_event_date"].fact_id,
                                  case.facts["notice_issue_date"].fact_id)))
        r = pipe.draft_validation.check(d, out.pack,
                                        {f.fact_id for f in case.facts.values()})
        self.assertNotIn("VAL-PARTICULARS", rules(r), r.issues)

    def test_a_content_defect_requires_no_timing_particulars(self):
        """Only a timed finding is argued on dates; a content defect is not."""
        rec = {"finding_type": "NTK_CONTENT_DEFECT",
               "calculation": {"deadline": "2026-06-15", "presumed_delivery": "2026-06-23",
                               "days_between": 8}}
        self.assertEqual(lf.particulars(rec), {})
        self.assertEqual(lf.particularised_sentence(rec), "")


# ----------------------------------------------------------------- coverage
class Coverage(unittest.TestCase):
    def test_an_unargued_approved_ground_blocks_the_draft(self):
        case, pipe = make_case(LATE)
        out = run(case, pipe, "", {})
        self.assertGreater(len(out.pack.module_ids), 1)
        rec = next(f for f in out.pack.legal_findings
                   if f["finding_type"] == "POFA_POSTAL_LATE")
        d = draft(case, sentence(lf.particularised_sentence(rec), ("KB-POFA-02",),
                                 (case.facts["parking_event_date"].fact_id,
                                  case.facts["notice_issue_date"].fact_id)))
        r = pipe.draft_validation.check(d, out.pack,
                                        {f.fact_id for f in case.facts.values()})
        self.assertIn("VAL-COVERAGE", rules(r))
        issue = next(i for i in r.issues if i.rule == "VAL-COVERAGE")
        self.assertIsNone(issue.sentence)

    def test_a_stubborn_drafter_fails_before_the_customer(self):
        """A drafter that keeps omitting an approved ground never releases:
        the letter-level issue cannot be trimmed away."""
        case, pipe = make_case(LATE)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "")
        pipe.answer(case, {})
        bad = {"paragraphs": [
            [{"text": "I write as the registered keeper in respect of Parking Charge "
                      "Notice PCN123456.", "fact_refs": [], "module_refs": ["STRUCTURAL"],
              "evidence_refs": [], "quote_of": None}],
            [{"text": CANCEL, "fact_refs": [], "module_refs": ["STRUCTURAL"],
              "evidence_refs": [], "quote_of": None}]], "no_ground_reason": None}
        pipe.extraction.llm.responses["drafting"] = [bad, bad, bad]
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertIn("VAL-COVERAGE", {i.rule for i in out.validation.issues})


# ------------------------------------------------------------- date formats
class DateRenderings(unittest.TestCase):
    def test_accepted_renderings(self):
        for text in ("delivered by 15 June 2026", "by 15th June 2026",
                     "by the 15th of June 2026", "on June 15, 2026",
                     "on 15/06/2026", "by 2026-06-15"):
            self.assertTrue(lf.date_stated("2026-06-15", text), text)

    def test_other_dates_do_not_count(self):
        self.assertFalse(lf.date_stated("2026-06-15", "by 16 June 2026"))
        self.assertFalse(lf.date_stated("2026-06-15", "by 15 July 2026"))
        self.assertFalse(lf.date_stated("not-a-date", "by 15 June 2026"))

    def test_day_counts(self):
        self.assertTrue(lf.days_stated(8, "that is 8 days outside the period"))
        self.assertTrue(lf.days_stated(8, "eight days late"))
        self.assertTrue(lf.days_stated(3, "3 calendar days after"))
        self.assertFalse(lf.days_stated(8, "several days late"))
        self.assertFalse(lf.days_stated(8, "9 days late"))

    def test_render_matches_the_validator(self):
        self.assertEqual(lf.render_date("2026-06-15"), "15 June 2026")
        self.assertTrue(lf.date_stated("2026-06-15", lf.render_date("2026-06-15")))


if __name__ == "__main__":
    unittest.main()
