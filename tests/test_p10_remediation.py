"""P10 systemic remediations: one failing case, one near-miss, one negative control."""
from __future__ import annotations

import re
import unittest

from pcn_appeal.drafting.context import DraftContext, fill_placeholder_text
from pcn_appeal.drafting.drafter import apply_one_argument_rules
from pcn_appeal.engines.extraction import _hhmm, _minutes
from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.models import (
    CaseFile, Draft, DraftSentence, EvidenceItem, RetrievalPack,
)
from test_scenarios import fields, make_case, run
from support import ReferenceAnalysisLLM


def _pack(**kw) -> RetrievalPack:
    base = dict(
        primary_route="POFA", secondary_routes=[], module_ids=["KB-POFA-02"],
        verified_facts={"pcn_number": "PCN123456", "vrm": "AB12CDE",
                        "parking_event_date": "01/06/2026",
                        "notice_issue_date": "20/06/2026"},
        fact_refs={"pcn_number": "F-pcn", "vrm": "F-vrm"},
        missing_facts=[], evidence_refs=["E1"], prohibited_claims=[],
        code_version="SCOP-1.1", pofa_route="POSTAL",
        pofa_findings=["POFA_POSTAL_LATE"], driver_status="UNIDENTIFIED",
        jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
        claim_plan={"approved": ["KB-POFA-02"], "status": "LOCKED"},
        legal_findings=[{
            "finding_type": "POFA_POSTAL_LATE", "status": "VERIFIED",
            "calculation_result": {
                "parking_event_date": "2026-06-01",
                "notice_issue_date": "2026-06-20",
                "deadline": "2026-06-15",
                "days_between": 8,
            },
            "legal_module_id": "KB-POFA-02",
        }],
    )
    base.update(kw)
    return RetrievalPack(**base)


def _draft(*sentences: DraftSentence, case_id="C-P10") -> Draft:
    return Draft(case_id, [[s] for s in sentences])


def _rules(draft, pack) -> set:
    return {i.rule for i in ValidationEngine().validate(draft, pack).issues}


class P10TimePrecision(unittest.TestCase):
    def test_seconds_are_preserved(self):
        self.assertEqual(_hhmm("12:59:17"), "12:59:17")
        self.assertEqual(_hhmm("Entry Time: 13:01:37"), "13:01:37")

    def test_hhmm_only_notice_does_not_invent_seconds(self):
        self.assertEqual(_hhmm("19/09/2026 12:23"), "12:23")
        self.assertEqual(_hhmm("9:05"), "09:05")
        self.assertIsNone(_hhmm("no time here"))

    def test_duration_uses_seconds_without_breaking_minute_math(self):
        self.assertEqual(_minutes("12:59:17", "13:01:37"), 2)
        self.assertEqual(_minutes("10:00", "12:47"), 167)
        self.assertIsNone(_minutes("10:00", None))


class P10ExtractionLanding(unittest.TestCase):
    def test_injected_anpr_seconds_land_on_the_graph(self):
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(
                operator_name="Acme", pcn_number="PCN1", vrm="AB12CDE",
                parking_event_date="15/05/2026", notice_issue_date="22/05/2026",
                entry_time="12:59:17", exit_time="13:01:37",
                parking_location="Airport Drop Off", alleged_breach="unpaid",
            ), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-TIME", evidence={
            "E1": EvidenceItem("E1", "PCN", "n.txt", text="NOTICE TO KEEPER")})
        from pcn_appeal.orchestrator import AppealPipeline
        AppealPipeline(llm).ingest(case)
        self.assertEqual(case.get("entry_time"), "12:59:17")
        self.assertEqual(case.get("exit_time"), "13:01:37")

    def test_combined_date_time_still_normalises(self):
        self.assertEqual(_hhmm("15/05/2026 12:59:17"), "12:59:17")

    def test_unrelated_duration_ground_is_not_created(self):
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(
                operator_name="Acme", pcn_number="PCN1", vrm="AB12CDE",
                parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                entry_time="10:00", exit_time="10:02",
            ), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-DUR", evidence={
            "E1": EvidenceItem("E1", "PCN", "n.txt", text="NOTICE")})
        from pcn_appeal.orchestrator import AppealPipeline
        AppealPipeline(llm).ingest(case)
        self.assertEqual(case.get("total_recorded_duration_min"), 2)
        self.assertNotIn("KB-ACT-02", getattr(case, "analysis_module_ids", []) or [])


class P10ValidatorPlaceholder(unittest.TestCase):
    def test_single_brace_placeholder_is_blocked(self):
        d = _draft(DraftSentence(
            "The keeper refers to {OPERATOR_NAME} and [PLACEHOLDER].",
            [], ["STRUCTURAL"]))
        self.assertIn("VAL-LEAK", _rules(d, _pack()))

    def test_near_miss_operator_name_from_facts_is_not_a_placeholder(self):
        d = _draft(DraftSentence(
            "The keeper writes to Acme Parking Ltd to request cancellation.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-LEAK", _rules(d, _pack(
            verified_facts={"operator_name": "Acme Parking Ltd",
                            "pcn_number": "PCN123456"})))

    def test_negative_control_clean_letter_has_no_leak(self):
        d = _draft(DraftSentence(
            "The Notice to Keeper was not delivered within the statutory period.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-LEAK", _rules(d, _pack()))


class P10ValidatorInventedFact(unittest.TestCase):
    def test_invented_permit_number_is_blocked(self):
        d = _draft(DraftSentence(
            "A valid permit numbered Z-999 was clearly displayed in the windscreen.",
            [], ["STRUCTURAL"]))
        self.assertIn("VAL-INVENTED", _rules(d, _pack()))

    def test_near_miss_known_bay_is_allowed(self):
        d = _draft(DraftSentence(
            "The tenant is entitled to bay numbered 14 under the agreement.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-INVENTED", _rules(d, _pack(
            verified_facts={"allocated_bay": "14", "pcn_number": "PCN123456"})))

    def test_negative_control_generic_permit_word_is_not_an_identifier(self):
        d = _draft(DraftSentence(
            "A valid permit was displayed.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-INVENTED", _rules(d, _pack()))


class P10ValidatorWrongCalculation(unittest.TestCase):
    def test_contradictory_day_count_is_blocked(self):
        d = _draft(DraftSentence(
            "The notice was given three days after the event.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertIn("VAL-CALC", _rules(d, _pack()))

    def test_near_miss_correct_day_count_is_allowed(self):
        d = _draft(DraftSentence(
            "The notice was given 8 days after the event.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-CALC", _rules(d, _pack()))

    def test_negative_control_statutory_28_day_window_is_not_a_calc(self):
        d = _draft(DraftSentence(
            "Pay or appeal within 28 days of the date of issue.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-CALC", _rules(d, _pack()))


class P10ValidatorMissingGround(unittest.TestCase):
    def _dv_rules(self, draft, pack) -> set:
        return {i.rule for i in DraftValidationEngine().check(draft, pack).issues}

    def test_approved_ground_with_no_sentence_is_blocked(self):
        d = _draft(DraftSentence("Please cancel this charge.", [], ["STRUCTURAL"]))
        self.assertIn("VAL-COVERAGE", self._dv_rules(d, _pack()))

    def test_near_miss_ground_is_covered_when_cited(self):
        d = _draft(DraftSentence(
            "The Notice to Keeper was not delivered within the statutory period.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-COVERAGE", self._dv_rules(d, _pack()))

    def test_negative_control_unrelated_module_is_not_required(self):
        d = _draft(DraftSentence(
            "The Notice to Keeper was not delivered within the statutory period.",
            ["F-pcn"], ["KB-POFA-02"]))
        self.assertNotIn("VAL-COVERAGE", self._dv_rules(d, _pack(
            module_ids=["KB-POFA-02"],
            claim_plan={"approved": ["KB-POFA-02"]})))


class P10NarrativeKeying(unittest.TestCase):
    def test_typo_language_writes_minor_keying(self):
        case, pipe = make_case({"alleged_breach": "No valid payment for vehicle"})
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I paid on the app but typo in reg")
        self.assertEqual(case.get("keying_error_type"), "MINOR")
        self.assertTrue(case.get("payment_made"))

    def test_near_miss_payment_without_typo_does_not_set_keying(self):
        case, pipe = make_case({"alleged_breach": "No valid payment for vehicle"})
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I paid at the machine when I arrived")
        self.assertTrue(case.get("payment_made"))
        self.assertNotEqual(case.get("keying_error_type"), "MINOR")

    def test_negative_control_overstay_does_not_activate_keying(self):
        case, pipe = make_case()
        out = run(case, pipe, "Got a letter weeks later", {})
        self.assertNotIn("KB-KEY-01", out.pack.module_ids)


class P10DraftContextPlaceholders(unittest.TestCase):
    def test_verified_bay_fills_placeholder_map(self):
        pack = _pack(
            primary_route="RESIDENTIAL", module_ids=["KB-RES-03"],
            verified_facts={"allocated_bay": "14", "pcn_number": "PCN123456"},
            evidence_index={"E3": "TENANCY"},
            claim_plan={"status": "LOCKED", "approved": ["KB-RES-03"]},
            context_chunks=[{
                "id": "AI-RES-003", "module_id": "KB-RES-03", "kind": "block",
                "text": "rights connected with the allocated space {{bay_reference}}.",
                "placeholder_map": {"bay_reference": "allocated_bay"},
            }, {
                "id": "AI-RES-001", "module_id": "KB-RES-03", "kind": "block",
                "text": "evidenced by the uploaded {{lease_or_tenancy}}.",
            }],
        )
        ctx = DraftContext.from_pack(pack)
        text = " ".join(c["text"] for c in ctx.guidance["context_chunks"])
        self.assertIn("14", text)
        self.assertIn("tenancy agreement", text)
        self.assertNotIn("{{", text)

    def test_near_miss_missing_fact_does_not_invent_a_value(self):
        text = fill_placeholder_text(
            "allocated space {{bay_reference}}",
            {}, {"bay_reference": "allocated_bay"})
        self.assertEqual(text, "allocated space {{bay_reference}}")

    def test_negative_control_does_not_invent_unrelated_identifier(self):
        text = fill_placeholder_text(
            "allocated space {{bay_reference}}",
            {"allocated_bay": "14"}, {"bay_reference": "allocated_bay"})
        self.assertIn("14", text)
        self.assertNotIn("Z-999", text)


class P10OneArgumentMerge(unittest.TestCase):
    def test_repeated_payment_sentences_collapse(self):
        draft = Draft("C-PAY", [
            [DraftSentence("A payment was made in connection with the visit.",
                           [], ["KB-PAY-01"])],
            [DraftSentence("Payment was nevertheless made. The case should not "
                           "be treated as though the parking facility was used "
                           "without payment.", [], ["KB-KEY-01"])],
            [DraftSentence("The applicable tariff was paid.", [], ["KB-KEY-01"])],
        ])
        out = apply_one_argument_rules(draft)
        made = [s.text for s in out.sentences()
                if re.search(r"payment was (nevertheless |duly )?made"
                             r"|tariff was paid", s.text, re.I)]
        self.assertEqual(len(made), 1, made)
        self.assertEqual(len([p for p in out.paragraphs if any(
            m.startswith(("KB-PAY-", "KB-KEY-"))
            for s in p for m in s.module_refs)]), 1)
        self.assertIn("without payment", out.plain_text().lower())

    def test_near_miss_single_payment_sentence_is_kept(self):
        draft = Draft("C-PAY", [[DraftSentence(
            "A payment was made in connection with the visit.",
            [], ["KB-PAY-01"])]])
        out = apply_one_argument_rules(draft)
        self.assertEqual(len(out.sentences()), 1)

    def test_negative_control_unrelated_pofa_is_not_merged(self):
        draft = Draft("C-PAY", [
            [DraftSentence("A payment was made in connection with the visit.",
                           [], ["KB-PAY-01"])],
            [DraftSentence("The Notice to Keeper was not delivered within the "
                           "statutory period.", [], ["KB-POFA-02"])],
        ])
        out = apply_one_argument_rules(draft)
        self.assertEqual(len(out.paragraphs), 2)
        self.assertIn("statutory period", out.plain_text())


if __name__ == "__main__":
    unittest.main()
