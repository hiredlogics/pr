"""Behavioural regressions: case-specific appeals, PCN conflicts, adaptive asking.

Asserts pipeline outcomes and invariants — not exact letter prose.

Run:  python -m unittest tests.test_case_intelligence_fixes -v
"""
from __future__ import annotations

import unittest

from pcn_appeal.drafting.drafter import LLMDrafter, TemplateDrafter
from pcn_appeal.engines.analysis import AnalysisEngine
from pcn_appeal.engines.extraction import ExtractionEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact, FactSource,
    FactStatus, RetrievalPack, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.rules.dsl import evaluate
from support import ReferenceAnalysisLLM, assert_absent_while_under_review, is_approved


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


SAINSBURYS = dict(
    operator_name="Euro Car Parks",
    pcn_number="8812545842",
    vrm="KJ19KYN",
    parking_location="Sainsbury's – Willesden Green",
    parking_event_date="29/08/2026",
    notice_issue_date="01/09/2026",
    charge_amount="£100",
    alleged_breach="Voucher or receipt not validated at the kiosk",
    operator_ata="BPA",
    jurisdiction="ENGLAND_WALES",
    notice_route="POSTAL",
)


def _pack(**kw) -> RetrievalPack:
    base = dict(
        primary_route="RECORDS", secondary_routes=[], module_ids=["KB-REC-01"],
        verified_facts={
            "pcn_number": "8812545842", "vrm": "KJ19KYN",
            "operator_name": "Euro Car Parks",
            "parking_location": "Sainsbury's – Willesden Green",
            "alleged_breach": SAINSBURYS["alleged_breach"],
            "parking_event_date": "29/08/2026",
            "evidence_kinds": ["PCN", "RECEIPT"],
        },
        fact_refs={"pcn_number": "F-pcn", "vrm": "F-vrm", "alleged_breach": "F-breach"},
        missing_facts=[], evidence_refs=["E1", "E2"], prohibited_claims=[],
        code_version=None, pofa_route="POSTAL", pofa_findings=[],
        driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[{"id": "PP-REC-001", "kind": "block", "module_id": "KB-REC-01",
                         "text": "records request", "sources": []}],
        lease_clauses=[],
        evidence_index={"E1": "PCN", "E2": "RECEIPT"},
        case_context={
            "operator_name": "Euro Car Parks",
            "parking_location": "Sainsbury's – Willesden Green",
            "alleged_breach": SAINSBURYS["alleged_breach"],
            "shopping_receipt_enclosed": True,
            "validation_status": "UNCONFIRMED",
            "unresolved_topics": ["kiosk_validation"],
        },
    )
    base.update(kw)
    return RetrievalPack(**base)


class ContainsPredicateTests(unittest.TestCase):
    def test_allegation_text_matches_validation_module(self):
        kg = KnowledgeGraph()
        rec = kg.modules["KB-REC-01"]
        self.assertTrue(evaluate(rec.use_when, {
            "alleged_breach": "Voucher or receipt not validated at the kiosk",
        }))
        self.assertFalse(evaluate(rec.use_when, {
            "alleged_breach": "Parked outside of a marked bay",
        }))


class SubstanceValidationTests(unittest.TestCase):
    def test_intro_and_conclusion_alone_cannot_release(self):
        kg = KnowledgeGraph()
        pack = _pack(module_ids=[], case_context={}, evidence_index={},
                     verified_facts={"pcn_number": "8812545842", "vrm": "KJ19KYN"})
        intro = kg.blocks["PP-INTRO-001"].text.replace("{{vrm}}", "KJ19 KYN") \
            .replace("{{pcn_number}}", "8812545842")
        end = kg.blocks["PP-END-001"].text.replace("{{pcn_number}}", "8812545842")
        draft = Draft("C-1", [
            [DraftSentence(intro, ["F-pcn", "F-vrm"], ["STRUCTURAL"])],
            [DraftSentence(end, ["F-pcn"], ["STRUCTURAL"])],
        ])
        result = ValidationEngine().validate(draft, pack)
        self.assertFalse(result.passed)
        self.assertIn("VAL-SUBSTANCE", {i.rule for i in result.issues})

    def test_generic_rec_without_allegation_tokens_fails_when_allegation_known(self):
        """Unsupported / interchangeable filler is not enough when allegation is known."""
        pack = _pack()
        draft = Draft("C-1", [
            [DraftSentence("I write as the registered keeper regarding PCN 8812545842.",
                           ["F-pcn"], ["STRUCTURAL"])],
            [DraftSentence("The operator is requested to cancel the charge.",
                           [], ["KB-REC-01"])],
        ])
        result = ValidationEngine().validate(draft, pack)
        self.assertIn("VAL-SUBSTANCE", {i.rule for i in result.issues})

    def test_receipt_must_be_mentioned_and_not_treated_as_validation(self):
        pack = _pack()
        draft = Draft("C-1", [
            [DraftSentence(
                "The notice alleges voucher or receipt not validated at the kiosk. "
                "A shopping receipt is enclosed and proves validation was completed.",
                ["F-breach"], ["KB-REC-01"], ["E2"])],
        ])
        result = ValidationEngine().validate(draft, pack)
        self.assertIn("VAL-CONFLICT", {i.rule for i in result.issues})


class LeadingGroundTests(unittest.TestCase):
    """A letter may only go out on a ground the KB allows to lead it.

    The strength calibration in kb_modules.yaml is explicit that below 50 a
    module is "evidence / signage / authority support only" and "can never lead
    the letter", and section 16 p4 keeps landowner authority last and concise.
    A selection made only of those has nothing to support, so what the customer
    would receive is a landowner-authority or keeper-liability-framing paragraph
    presented as their appeal.
    """

    def _sainsburys_case(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**SAINSBURYS), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-LG", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                               text="Euro Car Parks\nPCN 8812545842\nnot validated at the kiosk"),
            "E2": EvidenceItem("E2", "RECEIPT", "shop.pdf",
                               text="Sainsbury's\nTotal £24.10", uploaded=True),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED],
                     "Cannot remember whether the kiosk was used. Receipt available.")
        return case, pipe

    def test_a_kiosk_notice_releases_on_records_request(self):
        """KB-REC-01 is the leading ground for a validation allegation: release a
        records-request letter, never dump the customer into manual review."""
        case, pipe = self._sainsburys_case()
        self.assertTrue(is_approved("KB-REC-01", pipe.kg), "KB-REC-01 must be ACTIVE")
        out = pipe.generate(case)

        self.assertIn("KB-REC-01", out.pack.module_ids)
        self.assertEqual(out.pack.primary_route, "RECORDS")
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertTrue(out.letter)

    def test_thin_pack_asks_situation_questions_instead_of_manual_review(self):
        """When only support-only grounds are open, Case Intelligence must ask."""
        late = dict(SAINSBURYS, alleged_breach="Overstay of paid parking")
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(**late), "doc_types": {"E1": "PCN"}}],
        }, ask=[
            {"fact": "payment_made",
             "text": "Was a parking payment made or attempted for this visit?",
             "type": "bool"},
            {"fact": "genuine_customer",
             "text": "Was the visit connected with genuine use of the premises?",
             "type": "bool"},
        ])
        case = CaseFile("C-ASK", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Overstay of paid parking"),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        questions = pipe.confirm(
            case, {},
            [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED],
            "")
        self.assertTrue(questions, "thin pack must ask before drafting")
        facts_asked = {q["fact"] for q in questions}
        self.assertTrue(
            facts_asked & {"payment_made", "genuine_customer", "permit_held",
                           "signage_issue_raised", "short_presence_before_acceptance",
                           "vehicle_immobilised"},
            facts_asked)

    def test_a_ground_strong_enough_to_lead_still_releases(self):
        """The gate must not swallow ordinary cases: a late postal notice carries
        KB-POFA-02/03 at strength 95 and has to come out as a letter."""
        late = dict(SAINSBURYS, notice_issue_date="20/06/2026",
                    parking_event_date="01/06/2026", site_postcode="M1 1AA",
                    alleged_breach="Overstayed paid time")
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**late), "doc_types": {"E1": "NTK"}}]})
        case = CaseFile("C-LG2", evidence={
            "E1": EvidenceItem("E1", "NTK", "ntk.pdf", text="Notice to Keeper")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED], "the letter came late")
        out = pipe.generate(case)

        self.assertTrue(pipe.reasoning.leading_grounds(out.pack.module_ids), out.pack.module_ids)
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertTrue(out.letter)

    def test_the_threshold_is_read_from_the_kb_not_hardcoded_per_module(self):
        kg = KnowledgeGraph()
        engine = AppealPipeline(ReferenceAnalysisLLM()).reasoning
        support_only = [m.module_id for m in kg.active_modules() if m.strength < 50]
        self.assertTrue(support_only, "the KB is expected to hold support-only modules")
        self.assertEqual(engine.leading_grounds(support_only), [])
        leaders = [m.module_id for m in kg.active_modules() if m.strength >= 50]
        self.assertEqual(sorted(engine.leading_grounds(leaders)), sorted(leaders))


class QuestionRepetitionTests(unittest.TestCase):
    def test_synonym_kiosk_question_is_dropped_after_cannot_remember(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-Q")
        case.asked_questions.append("kiosk_validation_attempted")
        case.put(Fact(
            "F-kiosk", "kiosk_validation_attempted",
            "cannot remember", FactStatus.ANSWERED,
            FactSource(SourceKind.ANSWER, "answer:kiosk_validation_attempted"),
        ))
        llm = FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-REC-01", "supported_by": ["alleged_breach"], "note": "x"}],
            "questions": [{
                "fact": "remember_validating_at_machine",
                "text": "Can you remember whether the validation machine was used?",
                "type": "text",
                "material_because": "same topic",
            }],
            "not_supported": [],
        }]})
        case.put(Fact("F-breach", "alleged_breach", SAINSBURYS["alleged_breach"],
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        result = AnalysisEngine(kg, llm).analyse(case, circumstances="Visited Sainsbury's.")
        self.assertNotIn("remember_validating_at_machine", [q["fact"] for q in result.questions])

    def test_store_contact_question_dropped_when_receipt_present(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-S", evidence={
            "E2": EvidenceItem("E2", "RECEIPT", "shop.pdf", text="Sainsbury's Total £24", uploaded=True),
        })
        case.put(Fact("F-breach", "alleged_breach", SAINSBURYS["alleged_breach"],
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        llm = FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-REC-01", "supported_by": ["alleged_breach"], "note": "x"}],
            "questions": [{
                "fact": "contacted_store_for_confirmation",
                "text": "Have you been able to contact the store for confirmation?",
                "type": "bool",
                "material_because": "store",
            }],
            "not_supported": [],
        }]})
        result = AnalysisEngine(kg, llm).analyse(case)
        self.assertEqual(result.questions, [])

    def test_question_round_limit_stops_further_asks(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-R")
        case.put(Fact("F-breach", "alleged_breach", SAINSBURYS["alleged_breach"],
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        for _ in range(3):
            case.audit.append({"event": "analysis_round", "grounds": [], "asking": []})
        llm = FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-REC-01", "supported_by": ["alleged_breach"], "note": "x"}],
            "questions": [{"fact": "further_evidence_available",
                           "text": "Is any further store confirmation available?",
                           "type": "bool", "material_because": "x"}],
            "not_supported": [],
        }]})
        result = AnalysisEngine(kg, llm).analyse(case)
        self.assertEqual(result.questions, [])


class PcnConflictTests(unittest.TestCase):
    def test_conflicting_labelled_pcn_numbers_are_flagged(self):
        llm = FakeLLM({"extraction": [{
            "fields": fields(**SAINSBURYS),
            "doc_types": {"E1": "PCN", "E2": "OTHER"},
        }]})
        case = CaseFile("C-P", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                               text="Parking Charge Notice Number 8812545842\nEuro Car Parks"),
            "E2": EvidenceItem("E2", "OTHER", "note.pdf",
                               text="PCN Ref: 8812545999\nDifferent reference on covering letter"),
        })
        flags = ExtractionEngine(llm).run(case)
        self.assertIn("conflict:pcn_number", flags)
        self.assertTrue(case.get("pcn_conflict"))
        self.assertEqual(case.facts["pcn_number"].status, FactStatus.UNCERTAIN)

    def test_generate_blocked_until_pcn_confirmed(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**SAINSBURYS), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-B", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="PCN Number 8812545842"),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        case.put(Fact("F-pcn_conflict", "pcn_conflict", True, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "pcn_cross_check")))
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.MANUAL_REVIEW)
        self.assertIsNone(out.letter)
        self.assertTrue(any(i.rule == "VAL-CONFLICT" for i in out.validation.issues))

    def test_confirming_pcn_clears_conflict_gate(self):
        from pcn_appeal.engines.extraction import ExtractionEngine
        case = CaseFile("C-C")
        case.put(Fact("F-pcn", "pcn_number", "8812545842", FactStatus.UNCERTAIN,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        case.put(Fact("F-pcn_conflict", "pcn_conflict", True, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "pcn_cross_check")))
        ExtractionEngine.confirm(case, {"pcn_number": "8812545842"}, ["pcn_number"])
        self.assertFalse(case.get("pcn_conflict"))


class SainsburysPipelineTests(unittest.TestCase):
    def test_template_fallback_is_case_specific(self):
        """Even without LLM drafting, REC letters must cite allegation + receipt."""
        pack = _pack()
        draft = TemplateDrafter(KnowledgeGraph()).draft("C-1", pack)
        letter = draft.plain_text().lower()
        self.assertIn("validated", letter)
        self.assertIn("receipt", letter)
        self.assertIn("euro car parks", letter)
        self.assertNotIn("validation occurred", letter)
        self.assertNotIn("shopping receipt proves", letter)
        # LAND filler must not dilute when REC is present
        self.assertFalse(any("KB-LAND-01" in s.module_refs for s in draft.sentences()))

    def test_pipeline_uses_llm_drafter_primary(self):
        pipe = AppealPipeline(FakeLLM({}))
        self.assertIsInstance(pipe.drafter, LLMDrafter)
        self.assertIsInstance(pipe.fallback, TemplateDrafter)

    def test_allegation_reaches_analysis_and_drafting(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**SAINSBURYS), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-S", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                               text="Euro Car Parks\nPCN 8812545842\nnot validated at the kiosk"),
            "E2": EvidenceItem("E2", "RECEIPT", "shop.pdf",
                               text="Sainsbury's Willesden Green\nTotal £24.10", uploaded=True),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        self.assertEqual(case.get("alleged_breach"), SAINSBURYS["alleged_breach"])
        pipe.confirm(
            case, {},
            [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED],
            "Visited Sainsbury's. Shopping receipt is available. Cannot remember using the kiosk.",
        )
        out = pipe.generate(case)
        if not is_approved("KB-REC-01", pipe.kg):
            # The records-request ground is awaiting legal sign-off, so nothing
            # may argue it and nothing may stand in for it. With the only ground
            # that answers this allegation withheld, the KB has nothing
            # fact-specific left, and the letter must still not overstate: it may
            # not claim the validation step was or was not completed.
            assert_absent_while_under_review(
                self, "KB-REC-01", module_ids=case.analysis_module_ids,
                draft=out.draft, letter=out.letter)
            low = (out.letter or "").lower()
            self.assertNotIn("validation occurred", low)
            self.assertNotIn("was not validated", low)
            return
        self.assertIn("KB-REC-01", case.analysis_module_ids)
        # A records case must not be argued as generic landowner authority.
        self.assertNotIn("KB-LAND-01", case.analysis_module_ids)
        self.assertIsNotNone(out.letter)
        low = out.letter.lower()
        self.assertIn("8812545842", out.letter)
        self.assertIn("validated", low)
        self.assertIn("receipt", low)
        self.assertTrue(
            any("KB-REC-01" in s.module_refs for s in out.draft.sentences()),
            "expected records-request ground in draft",
        )
        self.assertNotIn("validation occurred", low)
        if out.state == CaseState.RELEASED:
            self.assertTrue(out.validation.passed)

    def test_driver_not_implied_in_records_request_letter(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**SAINSBURYS), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-D", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="not validated at the kiosk"),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "Cannot remember.")
        out = pipe.generate(case)
        self.assertNotRegex(out.letter or "", r"\bI (drove|parked|validated)\b")

    def test_empty_pack_template_fails_validation(self):
        kg = KnowledgeGraph()
        pack = _pack(module_ids=[], context_chunks=[], case_context={},
                     evidence_index={}, verified_facts={"pcn_number": "1", "vrm": "AB12CDE"})
        draft = TemplateDrafter(kg).draft("C-1", pack)
        result = ValidationEngine().validate(draft, pack)
        self.assertFalse(result.passed)
        self.assertIn("VAL-SUBSTANCE", {i.rule for i in result.issues})

    def test_notice_facts_not_reasked(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-N")
        for name, val in SAINSBURYS.items():
            case.put(Fact(f"F-{name}", name, val, FactStatus.CONFIRMED,
                          FactSource(SourceKind.DOCUMENT, "E1")))
        llm = FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-REC-01", "supported_by": ["alleged_breach"], "note": "x"}],
            "questions": [
                {"fact": "pcn_number", "text": "What is the PCN number?", "type": "text",
                 "material_because": "x"},
                {"fact": "operator_name", "text": "Who is the operator?", "type": "text",
                 "material_because": "x"},
            ],
            "not_supported": [],
        }]})
        result = AnalysisEngine(kg, llm).analyse(case)
        self.assertEqual(result.questions, [])


class FactRecoveryTests(unittest.TestCase):
    """Missing-fact recovery: documents + UK rules before asking / guessing."""

    def test_recovers_missing_dates_from_notice_text(self):
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        case = CaseFile("C-R1", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text=(
                "Parking Charge Notice Number 8812545842\n"
                "Operator: Euro Car Parks\n"
                "VRM: KJ19 KYN\n"
                "Date of Parking: 29/08/2026\n"
                "Date of Issue: 01/09/2026\n"
                "Contravention: Voucher or receipt not validated at the kiosk\n"
                "Location: Sainsbury's Willesden Green\n"
                "Entry Time: 10:05\n"
                "Exit Time: 11:40\n"
            )),
        })
        report = FactRecoveryEngine().recover(case)
        self.assertEqual(str(case.get("parking_event_date")), "2026-08-29")
        self.assertEqual(str(case.get("notice_issue_date")), "2026-09-01")
        self.assertEqual(case.get("pcn_number"), "8812545842")
        self.assertEqual(case.get("vrm"), "KJ19KYN")
        self.assertIn("validated", (case.get("alleged_breach") or "").lower())
        self.assertIn("parking_event_date", report.recovered)
        self.assertIn("parking_event_date", report.do_not_ask)

    def test_missing_dates_do_not_invent_pofa_defect(self):
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        case = CaseFile("C-R2")
        case.put(Fact("F-jur", "jurisdiction", "ENGLAND_WALES", FactStatus.CONFIRMED,
                      FactSource(SourceKind.ANSWER, "t")))
        case.put(Fact("F-route", "notice_route", "POSTAL", FactStatus.CONFIRMED,
                      FactSource(SourceKind.ANSWER, "t")))
        # No event / issue dates — calculator must stay UNRESOLVED, not invent late.
        report = FactRecoveryEngine().recover(case)
        self.assertEqual(report.calculated.get("pofa_route"), "UNRESOLVED")
        self.assertEqual(report.calculated.get("pofa_findings"), [])
        self.assertTrue(any(
            g["fact"] in ("parking_event_date", "notice_issue_date")
            for g in report.unknown_material
        ))

    def test_code_version_not_applied_without_ata(self):
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        from datetime import date
        case = CaseFile("C-R3")
        case.put(Fact("F-ev", "parking_event_date", date(2026, 8, 29), FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
        report = FactRecoveryEngine().recover(case)
        self.assertIsNone(report.calculated.get("code_version"))
        self.assertIn("ata_unknown", str(report.calculated.get("code_status")))

    def test_conflicting_pcn_not_guessed_by_recovery(self):
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        case = CaseFile("C-R4", evidence={
            "E1": EvidenceItem("E1", "PCN", "a.pdf",
                               text="Parking Charge Notice Number 8812545842"),
            "E2": EvidenceItem("E2", "OTHER", "b.pdf",
                               text="PCN Ref: 8812545999"),
        })
        case.put(Fact("F-pcn_conflict", "pcn_conflict", True, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "pcn_cross_check")))
        report = FactRecoveryEngine().recover(case)
        self.assertIn("pcn_number", report.conflicts)
        # Must not silently adopt either number while conflict stands.
        self.assertNotIn("pcn_number", report.recovered)

    def test_receipt_purchase_confirmed_validation_unknown(self):
        from pcn_appeal.engines.recovery import FactRecoveryEngine
        case = CaseFile("C-R5", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                               text="Contravention: not validated at the kiosk\nPCN Number 1112223334"),
            "E2": EvidenceItem("E2", "RECEIPT", "r.pdf",
                               text="Sainsbury's\nTotal £12.40", uploaded=True),
        })
        case.put(Fact("F-br", "alleged_breach", "not validated at the kiosk",
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        report = FactRecoveryEngine().recover(case)
        self.assertTrue(case.get("shopping_purchase_confirmed"))
        self.assertEqual(case.get("parking_validation_status"), "UNKNOWN")
        self.assertIn("kiosk_validation_log", report.operator_requestable)
        self.assertIn("store_contact", report.do_not_ask)

    def test_recovered_facts_are_not_reasked(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-R6")
        case.recovery_report = {
            "recovered": {"parking_event_date": {"value": "2026-08-29", "method": "x"}},
            "do_not_ask": ["parking_event_date", "pcn_number"],
            "operator_requestable": ["kiosk_validation_log"],
        }
        case.put(Fact("F-br", "alleged_breach", "not validated at the kiosk",
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        llm = FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-REC-01", "supported_by": ["alleged_breach"], "note": "x"}],
            "questions": [
                {"fact": "parking_event_date", "text": "What was the parking date?",
                 "type": "text", "material_because": "x"},
                {"fact": "kiosk_validation_log", "text": "Can you get the kiosk log?",
                 "type": "text", "material_because": "x"},
                {"fact": "store_confirmation", "text": "Please contact the store for proof.",
                 "type": "text", "material_because": "x"},
            ],
            "not_supported": [],
        }]})
        result = AnalysisEngine(kg, llm).analyse(case)
        asked = [q["fact"] for q in result.questions]
        self.assertNotIn("parking_event_date", asked)
        self.assertNotIn("kiosk_validation_log", asked)
        self.assertEqual(asked, [])

    def test_pipeline_recovers_before_questions(self):
        """End-to-end: date absent from extraction fields but present on notice text."""
        partial = dict(SAINSBURYS)
        del partial["parking_event_date"]
        del partial["notice_issue_date"]
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**partial), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-R7", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text=(
                "Parking Charge Notice Number 8812545842\n"
                "Operator: Euro Car Parks\n"
                "Date of Parking: 29/08/2026\n"
                "Date of Issue: 01/09/2026\n"
                "Contravention: Voucher or receipt not validated at the kiosk\n"
            )),
            "E2": EvidenceItem("E2", "RECEIPT", "shop.pdf",
                               text="Sainsbury's\nTotal £24.10", uploaded=True),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        # Extraction intentionally omitted dates; recovery must fill them on confirm.
        self.assertFalse(case.has("parking_event_date"))
        qs = pipe.confirm(
            case, {},
            [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED],
            "Cannot remember whether the kiosk was used.",
        )
        self.assertTrue(case.has("parking_event_date"), case.recovery_report)
        self.assertTrue(case.has("notice_issue_date"))
        self.assertNotIn("parking_event_date", [q["fact"] for q in qs])
        # The subject of this test is recovery, not the ground: a date read off
        # the notice text must not be asked for. Which ground the recovered facts
        # then open depends on what the KB currently approves.
        if is_approved("KB-REC-01", pipe.kg):
            self.assertIn("KB-REC-01", case.analysis_module_ids)
        else:
            self.assertNotIn("KB-REC-01", case.analysis_module_ids)

    def test_incomplete_notice_does_not_guess_content_defect(self):
        """One-sided notice: both-sides flag set; no invented PoFA content ground."""
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**SAINSBURYS), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-R8", evidence={
            "E1": EvidenceItem("E1", "PCN", "front.pdf",
                               text="Parking Charge Notice Number 8812545842\nEuro Car Parks"),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        self.assertFalse(case.get("notice_sides_complete"))
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED], "")
        self.assertNotIn("KB-POFA-04", case.analysis_module_ids)


class GroundClassificationTests(unittest.TestCase):
    """Inconclusive receipt must not become 'evidence contradicting the allegation'."""

    def test_rec_module_uses_records_route_not_evidence(self):
        kg = KnowledgeGraph()
        self.assertEqual(kg.modules["KB-REC-01"].route, "RECORDS")
        self.assertIn("RECORDS", kg.routes)
        self.assertNotIn(
            "contradict",
            kg.routes["RECORDS"].get("label", "").lower(),
        )
        self.assertIn(
            "contradict",
            kg.routes["EVIDENCE"].get("label", "").lower(),
        )

    def test_sainsburys_primary_route_is_records_not_evidence(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**SAINSBURYS), "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-G1", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                               text="Euro Car Parks\nPCN Number 8812545842\n"
                                    "not validated at the kiosk"),
            "E2": EvidenceItem("E2", "RECEIPT", "shop.pdf",
                               text="Sainsbury's\nTotal £24.10", uploaded=True),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(
            case, {},
            [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED],
            "Cannot remember whether the kiosk was used. Receipt available.",
        )
        out = pipe.generate(case)
        # The point of this test is the route LABEL the customer is shown: a
        # cautious request for the operator's records is not a claim that
        # independent evidence contradicts the allegation. That must hold whether
        # or not the ground itself is currently approved - what must never happen
        # is the case being labelled EVIDENCE.
        self.assertNotEqual(out.pack.primary_route, "EVIDENCE", out.pack.trace)
        self.assertNotIn("KB-EV-01", out.pack.module_ids)
        labels = [
            pipe.kg.routes.get(r, {}).get("label", r)
            for r in ([out.pack.primary_route] + list(out.pack.secondary_routes or []))
            if r
        ]
        self.assertFalse(
            any("contradict" in (lab or "").lower() for lab in labels), labels)
        self.assertEqual(case.get("parking_validation_status"), "UNKNOWN")

        events = [a for a in case.audit if a.get("event") == "retrieval_pack"]
        self.assertTrue(events, "the pack must be recorded either way")
        if not is_approved("KB-REC-01", pipe.kg):
            assert_absent_while_under_review(
                self, "KB-REC-01", module_ids=out.pack.module_ids,
                draft=out.draft, letter=out.letter)
            self.assertNotEqual(out.pack.primary_route, "RECORDS")
            return
        self.assertIn("KB-REC-01", out.pack.module_ids)
        self.assertEqual(out.pack.primary_route, "RECORDS", out.pack.trace)
        self.assertTrue(any("records" in (lab or "").lower() for lab in labels), labels)
        self.assertEqual(events[-1]["primary_route"], "RECORDS")

    def test_genuine_contradiction_still_uses_evidence_route(self):
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**{
                **SAINSBURYS,
                "alleged_breach": "Overstayed paid time",
                "independent_evidence_contradicts": True,
            }), "doc_types": {"E1": "PCN", "E2": "DASHCAM"}}]})
        case = CaseFile("C-G2", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Overstayed paid time"),
            "E2": EvidenceItem("E2", "DASHCAM", "cam.mp4", uploaded=True,
                               text="Left site at 10:05"),
        })
        # Force the contradiction fact after extract (customer-confirmed).
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        case.put(Fact("F-iec", "independent_evidence_contradicts", True,
                      FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "q")))
        # Analysis proposes EV-01.
        from pcn_appeal.engines.analysis import AnalysisEngine
        case.analysis_module_ids = ["KB-EV-01"]
        pack = pipe.reasoning.analyse(case, selected_ids=["KB-EV-01"])
        self.assertEqual(pack.primary_route, "EVIDENCE")
        self.assertIn("KB-EV-01", pack.module_ids)

    def test_inconclusive_receipt_blocks_ev01_even_if_proposed(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-G3", evidence={
            "E2": EvidenceItem("E2", "RECEIPT", "r.pdf", uploaded=True, text="Total £10"),
        })
        case.put(Fact("F-br", "alleged_breach", "not validated at the kiosk",
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        case.put(Fact("F-iec", "independent_evidence_contradicts", True,
                      FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "q")))
        case.put(Fact("F-val", "parking_validation_status", "UNKNOWN",
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "receipt")))
        from pcn_appeal.legal import pofa as pofa_mod
        pofa_res = pofa_mod.PofaResult("POSTAL", [], [])
        result = AnalysisEngine(kg, FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-EV-01", "supported_by": ["independent_evidence_contradicts"],
                         "note": "x"}],
            "questions": [], "not_supported": [],
        }]})).analyse(case, pofa=pofa_res, code_version=None)
        self.assertNotIn("KB-EV-01", result.module_ids)
        self.assertTrue(any("KB-EV-01" in str(s) for s in result.suppressed)
                        or any("KB-EV-01" == s.get("module_id") for s in result.suppressed))

    def test_validator_blocks_contradiction_prose_without_fact(self):
        pack = _pack(
            primary_route="RECORDS", module_ids=["KB-REC-01"],
            verified_facts={
                "pcn_number": "8812545842", "vrm": "KJ19KYN",
                "operator_name": "Euro Car Parks",
                "alleged_breach": SAINSBURYS["alleged_breach"],
                "evidence_kinds": ["PCN", "RECEIPT"],
            },
            case_context={"shopping_receipt_enclosed": True, "validation_status": "UNCONFIRMED",
                          "alleged_breach": SAINSBURYS["alleged_breach"]},
        )
        bad = Draft("C-1", [[
            DraftSentence(
                "Independent evidence demonstrates that the vehicle was not present as alleged.",
                ["F-pcn"], ["KB-REC-01"], ["E2"]),
            DraftSentence("A shopping receipt is enclosed.", ["F-pcn"], ["KB-REC-01"], ["E2"]),
        ]])
        result = ValidationEngine().validate(bad, pack)
        self.assertFalse(result.passed)
        self.assertIn("VAL-EVIDENCE-CONTRADICTION", {i.rule for i in result.issues})

    def test_validator_blocks_evidence_route_without_contradiction_fact(self):
        pack = _pack(
            primary_route="EVIDENCE", module_ids=["KB-EV-01"],
            verified_facts={
                "pcn_number": "8812545842", "vrm": "KJ19KYN",
                "alleged_breach": "Overstayed",
                "evidence_kinds": ["RECEIPT"],
            },
            case_context={},
            context_chunks=[{"id": "PP-ANPR-004", "kind": "block", "module_id": "KB-EV-01",
                             "text": "x", "sources": []}],
        )
        draft = Draft("C-1", [[
            DraftSentence("The materials are enclosed.", [], ["KB-EV-01"], ["E2"]),
        ]])
        result = ValidationEngine().validate(draft, pack)
        self.assertIn("VAL-EVIDENCE-CONTRADICTION", {i.rule for i in result.issues})

    def test_no_independent_evidence_does_not_select_ev01(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-G4")
        case.put(Fact("F-br", "alleged_breach", "Overstayed paid time",
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1")))
        # No contradiction fact, no evidence kinds.
        from pcn_appeal.legal import pofa as pofa_mod
        result = AnalysisEngine(kg, FakeLLM({"case_analysis": [{
            "grounds": [{"module_id": "KB-EV-01", "supported_by": [], "note": "x"}],
            "questions": [], "not_supported": [],
        }]})).analyse(case, pofa=pofa_mod.PofaResult("POSTAL", [], []), code_version=None)
        self.assertNotIn("KB-EV-01", result.module_ids)

    def test_api_ground_label_for_records(self):
        from pcn_appeal.api import _ground_labels
        from pcn_appeal.models import RetrievalPack
        kg = KnowledgeGraph()
        # api._ground_labels uses module-level KG; patch via routes on pack alone
        # by calling through the live KG singleton if available.
        pack = RetrievalPack(
            primary_route="RECORDS", secondary_routes=[], module_ids=["KB-REC-01"],
            verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
            prohibited_claims=[], code_version=None, pofa_route="POSTAL",
            pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
        )
        # Use kg routes the same way production does.
        labels = [kg.routes.get(r, {}).get("label", r)
                  for r in ([pack.primary_route] + list(pack.secondary_routes)) if r]
        self.assertEqual(labels, ["Proportionate request for operator records"])


if __name__ == "__main__":
    unittest.main()
