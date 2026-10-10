"""Fixes from the v21 live retest.

  VAL-ATTRIBUTION     a fact the system calculated is not "what the notice records"
  coverage            stand-in account atoms/events are advisory; a ground's own are required
  held for review     drafting that runs out of retries is held honestly, not rerun by "continue"
  released payload    every released letter carries the whole frame (page, copy, PDF)
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from pcn_appeal.engines import attribution_check as ac
from pcn_appeal.engines import outcome
from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.models import (CaseFile, CaseState, Draft, DraftSentence, Fact, FactSource,
                               FactStatus, RetrievalPack, SourceKind)

GRACE = ("I note that the notice itself records this as additional time beyond the permitted "
         "period and also records that this overstay falls within the applicable grace period.")


def pack(**ctx):
    facts = {"operator_name": "SAMPLE CAR PARKS LTD", "parking_event_date": "2 October 2026",
             "within_grace_period": True, "overstay_min": 9}
    return RetrievalPack(
        primary_route="GRACE", secondary_routes=[], module_ids=["KB-GRACE-01"], verified_facts=facts,
        fact_refs={k: f"F-{k}" for k in facts}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
        code_version=None, pofa_route="POSTAL", pofa_findings=[], driver_status="UNIDENTIFIED",
        jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
        claim_plan={"status": "LOCKED", "approved": ["KB-GRACE-01"]},
        case_context={"document_established_facts": ["operator_name", "parking_event_date"],
                      "customer_reported_facts": [], **ctx})


REFS = ["F-operator_name", "F-parking_event_date", "F-overstay_min", "F-within_grace_period"]


# --------------------------------------------------------------------------- 1
class ACalculationIsNotWhatTheNoticeRecords(unittest.TestCase):

    def test_crediting_the_notice_with_a_calculated_finding_is_caught(self):
        self.assertEqual(ac.credited_to_notice(GRACE, REFS, pack()), "grace")

    def test_the_calculation_or_a_plain_statement_is_fine(self):
        for text in ("The calculation shows that this overstay falls within the applicable grace period.",
                     "On the times shown on the notice, the overstay is 9 minutes, within the grace period.",
                     "This overstay falls within the applicable grace period.",
                     "I note that the notice shows the vehicle entering at 08:15 and leaving at 11:24."):
            with self.subTest(text=text):
                self.assertIsNone(ac.credited_to_notice(text, REFS, pack()))

    def test_what_the_notice_does_print_may_be_credited_to_it(self):
        text = "The notice states that SAMPLE CAR PARKS LTD alleges an overstay on 2 October 2026."
        self.assertIsNone(ac.credited_to_notice(text, REFS, pack()))
        # even when the notice's own vocabulary shares a word with a derived fact's name
        self.assertIsNone(ac.credited_to_notice("The notice states the overstay was 9 minutes.", REFS, pack()))

    def test_a_new_derived_fact_is_covered_by_its_name_not_by_a_new_phrase(self):
        p = pack()
        p.verified_facts["consideration_met"] = True
        p.fact_refs["consideration_met"] = "F-consideration_met"
        text = "The notice records that the consideration period had been met."
        self.assertEqual(ac.credited_to_notice(text, REFS + ["F-consideration_met"], p), "consideration")

    def test_the_engine_refuses_it_as_VAL_ATTRIBUTION_naming_the_sentence(self):
        d = Draft("C", [[DraftSentence(GRACE, list(REFS), ["KB-GRACE-01"], [])]])
        issues = ValidationEngine(None).validate(d, pack()).issues
        hit = [i for i in issues if i.rule == "VAL-ATTRIBUTION"]
        self.assertEqual([i.sentence for i in hit], [GRACE])
        self.assertIn("calculation shows", hit[0].message)


# --------------------------------------------------------------------------- 2
class OnlyWhatAGroundRelliesOnIsRequired(unittest.TestCase):
    EVENT = {"event_id": "E1", "event_type": "ACTIVITY", "polarity": "AFFIRMED", "attribution": "CUSTOMER",
             "description": "The customer queued at a second payment machine after the first would not work."}

    def check(self, bundle_events, ctx_events, sentence):
        bundle = {"source_fact_ids": [], "source_fact_names": [], "derived_fact_ids": [],
                  "derived_fact_names": [], "evidence_ids": [], "legal_finding_ids": [],
                  "relationship_ids": [], "values": {}, "material_narrative_atoms": [],
                  "supporting_events": bundle_events, "required_particulars": []}
        plan = {"status": "LOCKED", "claim_plan_id": "CP", "approved": ["KB-AUTH-01"],
                "module_ids": ["KB-AUTH-01"], "labels": {"KB-AUTH-01": "Authorisation"},
                "support_bundles": {"KB-AUTH-01": bundle},
                "draft_requirements": {"KB-AUTH-01": {"required_particulars": [],
                                                      "prohibited_content": [], "explanation_goal": []}}}
        p = RetrievalPack(
            primary_route="AUTHORISATION", secondary_routes=[], module_ids=["KB-AUTH-01"],
            verified_facts={"authorisation_source": "BUSINESS"}, fact_refs={"authorisation_source": "F1"},
            missing_facts=[], evidence_refs=[], prohibited_claims=[], code_version=None, pofa_route="POSTAL",
            pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
            case_context={"claim_plan": plan, "material_events": ctx_events})
        d = Draft("C", [[DraftSentence("I appeal as the registered keeper.", [], ["STRUCTURAL"], [])],
                        [DraftSentence(sentence, ["F1"], ["KB-AUTH-01"], [])]])
        return [i for i in DraftValidationEngine().check(d, p, {"F1"}).issues
                if i.rule == "VAL-MATERIAL-FACT-COVERAGE"]

    OTHER = "I say the vehicle was on site with the operator's permission for a business purpose."

    def test_an_event_offered_only_because_the_section_stood_alone_is_not_required(self):
        self.assertEqual(self.check([], [self.EVENT], self.OTHER), [])

    def test_an_event_the_grounds_own_bundle_carries_is_still_required(self):
        self.assertTrue(self.check([self.EVENT], [], self.OTHER))

    def test_the_stand_in_is_still_offered_to_the_drafter(self):
        from pcn_appeal.drafting.plan import build_draft_plan
        p = pack()
        p.case_context["material_events"] = [self.EVENT]
        p.module_ids = ["KB-GRACE-01"]
        plan = {"status": "LOCKED", "claim_plan_id": "CP", "approved": ["KB-GRACE-01"], "module_ids": ["KB-GRACE-01"],
                "labels": {"KB-GRACE-01": "Grace"}, "support_bundles": {}, "draft_requirements": {}}
        p.case_context["claim_plan"] = plan
        sections = build_draft_plan(p, case_id="C").sections
        events = [e for s in sections for e in (s.supporting_events or [])]
        self.assertTrue(events and all(e.get("standin") for e in events), events)


# --------------------------------------------------------------------------- 3
class ADraftingThatRanOutIsHeldHonestly(unittest.TestCase):

    def case(self, answers=None):
        c = CaseFile("C-H")
        c.state = CaseState.VALIDATION_FAILED
        c.raw_answers = dict(answers or {"genuine_customer": "yes"})
        c.begin_run("auto_appeal")
        from pcn_appeal.orchestrator import answers_digest
        c.audit.append({"event": "validation", "attempt": 3, "passed": False, "issues": ["VAL-CUSTOMER-COPY"]})
        c.audit.append({"event": "drafting_exhausted", "attempts": 3, "issues": ["VAL-CUSTOMER-COPY"],
                        "answers_digest": answers_digest(c)})
        return c

    def test_the_outcome_is_held_for_review_not_a_processing_error_and_cannot_be_rerun(self):
        hold = outcome.classify_hold(self.case(), None, None)
        self.assertEqual(hold["outcome"], outcome.OUTCOME_HELD_FOR_REVIEW)
        self.assertFalse(hold["can_continue"])
        self.assertFalse(hold["cta_label"])
        text = (hold["outcome_title"] + hold["outcome_message"]).lower()
        self.assertIn("held", text)
        self.assertNotIn("processing error", text)
        self.assertNotIn("try continuing", hold["outcome_next"].lower())

    def test_a_draft_error_is_still_a_retryable_processing_error(self):
        c = self.case()
        c.audit.insert(0, {"event": "draft_error", "error": "provider down"})
        self.assertEqual(outcome.classify_hold(c, None, None)["outcome"], outcome.OUTCOME_PROCESSING_ERROR)

    def test_continue_with_nothing_new_returns_the_hold_without_running_anything(self):
        from pcn_appeal import api
        payload = api._held_for_review(self.case())
        self.assertEqual(payload["outcome"], outcome.OUTCOME_HELD_FOR_REVIEW)
        self.assertFalse(payload["can_continue"])
        self.assertEqual(payload["questions"], [])

    def test_a_new_answer_lets_it_run_again(self):
        from pcn_appeal import api
        c = self.case()
        c.raw_answers["authorisation_source"] = "BUSINESS"          # the customer gave us something new
        self.assertIsNone(api._held_for_review(c))

    def test_a_case_that_is_not_held_is_never_short_circuited(self):
        from pcn_appeal import api
        c = self.case()
        c.state = CaseState.CONFIRMED
        self.assertIsNone(api._held_for_review(c))


# --------------------------------------------------------------------------- 4
class EveryReleasedLetterCarriesTheWholeFrame(unittest.TestCase):

    def test_the_released_payload_has_the_page_the_copy_button_and_the_pdf_link(self):
        from pcn_appeal import api
        c = CaseFile("C-R")
        out = NS(letter="BODY", pack=RetrievalPack(
            primary_route="GRACE", secondary_routes=[], module_ids=[], verified_facts={}, fact_refs={},
            missing_facts=[], evidence_refs=[], prohibited_claims=[], code_version=None, pofa_route="POSTAL",
            pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[]), state=CaseState.RELEASED, customer_notices=[])
        fields = api._released_fields(c, out)
        doc = fields["letter_document"]
        self.assertEqual(doc["from_lines"], ["[YOUR FULL NAME]", "[YOUR ADDRESS]"])
        self.assertEqual(doc["to_lines"], ["[PARKING COMPANY NAME]", "[OPERATOR APPEALS ADDRESS]"])
        self.assertEqual(doc["salutation"], "Dear Sir or Madam,")
        self.assertEqual(doc["signature"], "[YOUR FULL NAME]")
        for part in ("[YOUR FULL NAME]", "Dear Sir or Madam,", "BODY", "Yours faithfully,"):
            self.assertIn(part, fields["letter_full"])
        self.assertEqual(fields["letter"], "BODY")
        self.assertTrue(fields["letter_pdf_url"].endswith("/letter.pdf"))

    def test_both_endpoints_build_it_the_same_way(self):
        """The step-by-step endpoint returned `letter` alone, so a case finished through it
        showed a bare body. Neither may build a released payload by hand again."""
        import inspect
        from pcn_appeal import api
        src = inspect.getsource(api)
        self.assertNotIn('payload["letter"] = out.letter', src)
        self.assertGreaterEqual(src.count("_released_fields(case, out)"), 2)

    def test_the_pdf_draws_the_same_frame_with_placeholders(self):
        import re
        from pcn_appeal import pdf
        doc = {"from_lines": ["[YOUR FULL NAME]", "[YOUR ADDRESS]"],
               "to_lines": ["SAMPLE CAR PARKS LTD", "[OPERATOR APPEALS ADDRESS]"],
               "subject": "Re: Parking Charge Notice SCP-998877, vehicle XY70 ZZZ",
               "salutation": "Dear Sir or Madam,", "sign_off": "Yours faithfully,",
               "signature": "[YOUR FULL NAME]"}
        html = pdf._compiled.render(
            keeper_name=None, keeper_address=None, keeper_address_lines=[], brand_logo="", brand_name="B",
            brand_colour="#000", case_id="C-1", brand_wordmark="W", brand_footer="f", today="10 October 2026",
            operator_name="SAMPLE CAR PARKS LTD", from_lines=doc["from_lines"], subject=doc["subject"],
            to_lines=doc["to_lines"], salutation=doc["salutation"], sign_off=doc["sign_off"],
            signature=doc["signature"], pcn_number="SCP-998877", vrm="XY70ZZZ", pcn_label="PCN ",
            paragraphs=["BODY"], evidence_list=[], grounds=[])
        text = re.sub(r"<style.*?</style>", "", html, flags=re.S)
        for part in ("[YOUR FULL NAME]", "[YOUR ADDRESS]", "[OPERATOR APPEALS ADDRESS]",
                     "vehicle XY70 ZZZ", "Dear Sir or Madam,", "Yours faithfully,"):
            self.assertIn(part, text)
        self.assertEqual(text.count("[YOUR FULL NAME]"), 2)          # From block and signature


# --------------------------------------------------------------------------- 5
class ExhaustedDraftingThroughThePipeline(unittest.TestCase):
    """Every draft refused by our own checks: held, said honestly, and not rerun."""

    def test_end_to_end(self):
        from unittest import mock
        import test_scenarios as ts
        from pcn_appeal import api
        from pcn_appeal.models import EvidenceItem
        with mock.patch("pcn_appeal.llm.probe", return_value={"provider": "t", "models": {"drafting": "r"}}):
            case, pipe = ts.make_case(
                {}, {"E2": EvidenceItem("E2", "RECOVERY_REPORT", "r.pdf", text="job")}, {"E2": "RECOVERY_REPORT"})
            llm = getattr(pipe.extraction.llm, "inner", pipe.extraction.llm)
            bad = {"paragraphs": [[{"text": "The verified facts show the vehicle broke down.",
                                    "fact_refs": [], "module_refs": ["KB-BREAK-01"], "evidence_refs": []}]]}
            llm.responses["drafting"] = [bad] * 12
            out = ts.run(case, pipe, "The engine failed and I waited for recovery.",
                         {"vehicle_immobilised": "yes", "immobilisation_prevented_departure": "yes",
                          "recovery_attended": "yes", "permitted_period_ended": "yes", "exit_delay_min": 40})
        self.assertEqual(out.state, CaseState.VALIDATION_FAILED)
        self.assertIsNone(out.letter)
        self.assertEqual(out.outcome, outcome.OUTCOME_HELD_FOR_REVIEW)
        self.assertFalse(out.can_continue)
        event = [a for a in case.audit if a.get("event") == "drafting_exhausted"]
        self.assertEqual(len(event), 1)
        calls_before = len(llm.calls)
        held = api._held_for_review(case)
        self.assertEqual(held["outcome"], outcome.OUTCOME_HELD_FOR_REVIEW)
        self.assertEqual(len(llm.calls), calls_before, "a bare continue must not call the model again")



class NoticeCreditedOnlyWithWhatItPrints(unittest.TestCase):
    """A sentence crediting the notice passes only if every time, duration and amount in it is
    printed on the notice, and it adds no size or verdict the notice does not contain."""

    def pack(self, breach="Parked longer than the time paid for"):
        return NS(verified_facts={"entry_time": "08:15", "exit_time": "11:24", "permitted_period": "3 hours",
                                  "alleged_breach": breach, "charge_amount": "GBP 85.00"},
                  fact_refs={}, case_context={"document_established_facts": [
                      "entry_time", "exit_time", "permitted_period", "alleged_breach", "charge_amount"]})

    def test_vague_size_and_grace_verdict_fail(self):
        for text in ("The notice records that the additional time on site was only a few minutes beyond the permitted period.",
                     "The notice records that this overstay falls within the applicable grace period."):
            self.assertIsNotNone(ac.unprinted_claim(text, self.pack()), text)

    def test_unprinted_figure_fails_and_printed_figure_passes(self):
        text = "The notice records that the vehicle overstayed by 9 minutes."
        self.assertIn("9 min", ac.unprinted_claim(text, self.pack()))
        self.assertIsNone(ac.unprinted_claim(text, self.pack("Overstayed by 9 minutes")))

    def test_printed_times_period_and_charge_pass(self):
        for text in ("The notice shows the vehicle entering at 08:15 and leaving at 11:24.",
                     "The notice states that the permitted period was three hours.",
                     "The notice states a charge of £85.00."):
            self.assertIsNone(ac.unprinted_claim(text, self.pack()), text)

    def test_a_sentence_that_does_not_credit_the_notice_is_left_alone(self):
        self.assertIsNone(ac.unprinted_claim("On the times shown, the extra period is 9 minutes.", self.pack()))


if __name__ == "__main__":
    unittest.main()
