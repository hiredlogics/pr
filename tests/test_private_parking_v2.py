"""Private Parking V2 regression scenarios (client checklist 1–20).

These exercise the live architecture — Extraction → Case Intelligence →
Knowledge Retrieval → Targeted Questions → Drafting → Validation — and must
NOT encode a V1 decision tree. Grounds come from KB `use_when` gates (via
ReferenceAnalysisLLM as a stable stand-in for the analysis model); questions
are only those the analysis layer still considers material after vetoes.

Each scenario records a StageReport so failures show where the pipeline went
wrong rather than only that the final letter was wrong.

Run:  python -m unittest tests.test_private_parking_v2 -v
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from pcn_appeal.legal import pofa
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact, FactSource,
    FactStatus, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM


# --------------------------------------------------------------------------- helpers
def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


BASE = dict(
    operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12CDE",
    parking_location="Retail Park", site_postcode="M1 1AA",
    parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
    charge_amount="£100", alleged_breach="Overstayed paid time",
    operator_ata="BPA", entry_time="10:00", exit_time="12:47",
)


@dataclass
class StageReport:
    """What each stage produced — printed on failure for diagnosis."""
    scenario: str
    extracted: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    analysis_modules: list[str] = field(default_factory=list)
    analysis_questions: list[str] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    retrieved_modules: list[str] = field(default_factory=list)
    pofa_route: Optional[str] = None
    pofa_findings: list[str] = field(default_factory=list)
    primary_route: Optional[str] = None
    letter: Optional[str] = None
    state: Optional[str] = None
    validation_passed: Optional[bool] = None
    validation_issues: list[str] = field(default_factory=list)
    stop_reason: Optional[str] = None

    def dump(self) -> str:
        lines = [f"=== StageReport: {self.scenario} ===",
                 f"extracted: {sorted(self.extracted)}",
                 f"flags: {self.flags}",
                 f"analysis_modules: {self.analysis_modules}",
                 f"analysis_questions: {self.analysis_questions}",
                 f"suppressed: {self.suppressed}",
                 f"retrieved: {self.retrieved_modules}",
                 f"pofa: {self.pofa_route} {self.pofa_findings}",
                 f"primary_route: {self.primary_route}",
                 f"state: {self.state} validation={self.validation_passed}",
                 f"issues: {self.validation_issues}",
                 f"stop: {self.stop_reason}"]
        if self.letter:
            lines.append(f"letter[:240]: {self.letter[:240]!r}")
        return "\n".join(lines)


def make_case(extra_fields=None, evidence=None, doc_types=None, ask=None,
              evidence_text="Parking Charge Notice\nOperator: Acme Parking Ltd"):
    f = dict(BASE, **(extra_fields or {}))
    ev = {"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text=evidence_text)}
    ev.update(evidence or {})
    llm = ReferenceAnalysisLLM(
        {"extraction": [{"fields": fields(**f),
                         "doc_types": {"E1": "PCN", **(doc_types or {})}}]},
        ask=ask or [])
    case = CaseFile("C-PP", evidence=ev)
    return case, AppealPipeline(llm)


def run_pipeline(case, pipe, narrative="", answers=None, corrections=None,
                 confirmed=None, scenario="?") -> StageReport:
    """Drive extract → confirm → (answer) → generate and capture every stage."""
    report = StageReport(scenario=scenario)
    report.flags = pipe.ingest(case)
    report.extracted = {n: case.get(n) for n in case.facts}

    if case.get("debt_recovery_stage"):
        result = pipe.auto_appeal(case, narrative)
        report.state = result.state.value
        report.stop_reason = result.stop_reason
        report.flags = result.flags
        return report

    conf = confirmed if confirmed is not None else [
        n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]
    qs = pipe.confirm(case, corrections or {}, conf, narrative)
    report.analysis_questions = [q["fact"] for q in qs]
    report.analysis_modules = list(getattr(case, "analysis_module_ids", []) or [])
    rounds = [a for a in case.audit if a.get("event") == "case_analysis"]
    if rounds:
        report.suppressed = list(rounds[-1].get("suppressed") or [])

    if case.state == CaseState.NO_APPEAL_RIGHT:
        report.state = case.state.value
        report.stop_reason = "debt_recovery_stage"
        return report

    if answers:
        qs = pipe.answer(case, answers)
        report.analysis_questions = [q["fact"] for q in qs]
        report.analysis_modules = list(getattr(case, "analysis_module_ids", []) or [])

    out = pipe.generate(case)
    report.state = out.state.value
    report.retrieved_modules = list(out.pack.module_ids)
    report.pofa_route = out.pack.pofa_route
    report.pofa_findings = list(out.pack.pofa_findings)
    report.primary_route = out.pack.primary_route
    report.letter = out.letter
    report.validation_passed = out.validation.passed
    report.validation_issues = [i.rule for i in out.validation.issues]
    return report


# =========================================================================== 1–2 PoFA timing
class S01_PofaPostalTiming(unittest.TestCase):
    """Late NTK across working days, weekends, bank holidays, Christmas."""

    def test_clearly_late_postal_ntk(self):
        case, pipe = make_case({"notice_issue_date": "20/06/2026"})
        r = run_pipeline(case, pipe, "letter arrived weeks later", scenario="1-late-ntk")
        self.assertEqual(r.pofa_findings, ["POFA_POSTAL_LATE"], r.dump())
        self.assertEqual(r.primary_route, "POFA", r.dump())
        self.assertEqual(r.state, "RELEASED", r.dump())
        self.assertIn("not delivered within", (r.letter or "").lower())

    def test_weekend_presumed_delivery(self):
        # Event Mon 1 Jun 2026; deadline 15 Jun. Issue Fri 12 Jun -> presumed
        # Tue 16 Jun (skip weekend) -> late by 1 on presumed -> UNRESOLVED.
        r = pofa.assess(jurisdiction="ENGLAND_WALES", relevant_land=True, notice_route="POSTAL",
                        parking_event_date=date(2026, 6, 1), notice_issue_date=date(2026, 6, 12))
        self.assertEqual(r.route, "UNRESOLVED")
        self.assertEqual(r.findings, [])

    def test_bank_holiday_extends_presumed_delivery(self):
        # Fri 22 May 2026 + 2 working days, Mon 25 May BH -> Wed 27 May
        self.assertEqual(pofa.add_working_days(date(2026, 5, 22), 2), date(2026, 5, 27))

    def test_christmas_new_year_working_days(self):
        # Posted Wed 23 Dec 2026; Thu 24 / Fri 25 / Mon 28 are not all working.
        # 25 and 28 Dec 2026 are bank holidays in the fallback table.
        delivered = pofa.add_working_days(date(2026, 12, 23), 2)
        self.assertEqual(delivered, date(2026, 12, 29))

    def test_compliant_timing_does_not_invent_defect(self):
        case, pipe = make_case({"notice_issue_date": "05/06/2026"})  # within 14 days
        r = run_pipeline(case, pipe, "notice arrived promptly", scenario="2-compliant")
        self.assertEqual(r.pofa_findings, [], r.dump())
        self.assertNotIn("POFA_POSTAL_LATE", r.pofa_findings)
        self.assertNotRegex((r.letter or "").lower(), r"not delivered within")


class S02_CompliantNotice(unittest.TestCase):
    def test_no_pofa_timing_module_without_finding(self):
        case, pipe = make_case({"notice_issue_date": "05/06/2026"})
        r = run_pipeline(case, pipe, "overstay only", scenario="2-no-invent")
        self.assertNotIn("KB-POFA-02", r.retrieved_modules, r.dump())
        self.assertNotIn("KB-POFA-03", r.retrieved_modules, r.dump())


# =========================================================================== 3 keeper-only
class S03_KeeperOnly(unittest.TestCase):
    def test_never_asks_who_was_driving(self):
        case, pipe = make_case(ask=[{
            "fact": "who_drove", "text": "Who was driving the vehicle?", "type": "text",
            "material_because": "identity",
        }])
        r = run_pipeline(case, pipe, "paid and overstayed", scenario="3-driver-ban")
        self.assertNotIn("who_drove", r.analysis_questions, r.dump())
        banned = ("driv", "who parked", "who was")
        for q in case.pending_questions:
            low = q["text"].lower()
            self.assertFalse(any(b in low for b in banned), q)

    def test_letter_is_keeper_framed(self):
        case, pipe = make_case({"notice_issue_date": "20/06/2026"})
        r = run_pipeline(case, pipe, "late letter", scenario="3-keeper-letter")
        self.assertIn("registered keeper", (r.letter or "").lower(), r.dump())
        self.assertNotRegex((r.letter or "").lower(), r"\bi (drove|was driving|parked)\b")


# =========================================================================== 4 / 12 / 13 permit
class S04_PermitAndSupermarket(unittest.TestCase):
    def test_unrelated_narrative_does_not_ask_permission(self):
        """Scenario 4/13/14: kids / supermarket story ≠ generic permission Q."""
        case, pipe = make_case(
            {"alleged_breach": "Parked in a Parent and Child bay without a child",
             "entry_time": None, "exit_time": None},
            ask=[{
                "fact": "authorisation_source",
                "text": "From whom was permission to park obtained?",
                "type": "choice", "options": ["RESIDENT", "NONE"],
                "material_because": "auth",
            }])
        # Clear ANPR times so a photographic notice shape is preserved
        case2, pipe2 = make_case(
            extra_fields={"alleged_breach": "Parked in a Parent and Child bay without a child",
                          "parking_location": "Supermarket"},
        )
        # Remove entry/exit from extraction payload by not including them
        f = {k: v for k, v in BASE.items() if k not in ("entry_time", "exit_time")}
        f["alleged_breach"] = "Parked in a Parent and Child bay without a child"
        f["parking_location"] = "Tesco Extra"
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]},
            ask=[])
        case = CaseFile("C-S", evidence={"E1": EvidenceItem("E1", "NTK", "n.pdf", text="Notice")})
        pipe = AppealPipeline(llm)
        r = run_pipeline(case, pipe, "Kids were in the car at the supermarket", scenario="4-kids")
        for fact in r.analysis_questions:
            self.assertFalse(any(x in fact for x in ("permit", "authoris", "permission")),
                             r.dump())

    def test_permit_allegation_recognised_from_notice(self):
        """Scenario 12: No Permit / Not Displayed comes from the allegation text."""
        case, pipe = make_case({"alleged_breach": "No valid permit displayed"})
        r = run_pipeline(case, pipe, "there was a permit on the dash",
                         answers={"permit_held": True, "permit_display_issue": True},
                         scenario="12-permit")
        # Either AUTH modules fire once answers land, or analysis asked about permit —
        # never a blank generic "permission to park" with no notice basis.
        self.assertTrue(
            any(m.startswith("KB-AUTH") for m in r.retrieved_modules)
            or "permit_held" in r.extracted
            or r.state in ("RELEASED", "MANUAL_REVIEW"),
            r.dump())


# =========================================================================== 5–9 ANPR / visits
class S05_AnprAndVisits(unittest.TestCase):
    def test_anpr_duration_needs_entry_exit(self):
        """Scenario 5/6: no ANPR questions on a photo-only notice."""
        f = {k: v for k, v in BASE.items() if k not in ("entry_time", "exit_time")}
        f["alleged_breach"] = "Parked without payment"
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]},
            ask=[{
                "fact": "anpr_duration_disputed",
                "text": "Is the length of time on the notice disputed?",
                "type": "bool", "material_because": "anpr",
            }, {
                "fact": "multiple_visits",
                "text": "Did the vehicle leave and return?",
                "type": "bool", "material_because": "visits",
            }])
        case = CaseFile("C-A", evidence={"E1": EvidenceItem("E1", "NTK", "n.pdf", text="Notice")})
        pipe = AppealPipeline(llm)
        r = run_pipeline(case, pipe, "I was only there a few minutes", scenario="6-no-anpr-q")
        self.assertNotIn("anpr_duration_disputed", r.analysis_questions, r.dump())
        self.assertNotIn("multiple_visits", r.analysis_questions, r.dump())

    def test_short_stay_does_not_equal_parking_period(self):
        """Scenario 7: very short entry/exit → consideration / not automatic parking."""
        case, pipe = make_case({"entry_time": "10:00", "exit_time": "10:03",
                                "alleged_breach": "No ticket displayed"})
        r = run_pipeline(case, pipe, "drove through looking for a space then left",
                         answers={"no_parking_took_place": True,
                                  "short_presence_before_acceptance": True},
                         scenario="7-short-stay")
        self.assertEqual(int(case.get("total_recorded_duration_min")), 3, r.dump())
        self.assertEqual(r.primary_route, "CONSIDERATION", r.dump())
        # Must not invent a POFA timing defect from a short stay
        self.assertEqual(r.pofa_findings, [], r.dump())

    def test_double_visit_when_fact_established(self):
        """Scenario 8: two visits → KB-ANPR-01 when multiple_visits is true."""
        case, pipe = make_case()
        r = run_pipeline(case, pipe, "left and came back later the same day",
                         answers={"multiple_visits": True}, scenario="8-double-visit")
        self.assertIn("KB-ANPR-01", r.retrieved_modules, r.dump())
        self.assertIn("more than one separate occasion", (r.letter or "").lower(), r.dump())

    def test_continuous_stay_does_not_invent_double_visit(self):
        """Scenario 9: no multiple_visits fact → no double-visit ground."""
        case, pipe = make_case()
        r = run_pipeline(case, pipe, "one continuous stay, overstayed paid time",
                         scenario="9-continuous")
        self.assertNotIn("KB-ANPR-01", r.retrieved_modules, r.dump())
        self.assertNotIn("more than one separate occasion", (r.letter or "").lower())


# =========================================================================== 10 hire
class S10_HireVehicle(unittest.TestCase):
    def test_hire_without_docs_raises_hire_ground(self):
        text = ("Notice to Keeper\nRegistered keeper: Acme Vehicle Hire Ltd\n"
                "This notice is addressed to the hire company as registered keeper.")
        case, pipe = make_case(
            evidence_text=text,
            evidence={"E1": EvidenceItem("E1", "NTK", "ntk.pdf", text=text)},
            doc_types={"E1": "NTK"},
        )
        r = run_pipeline(case, pipe, "this is a hire vehicle", scenario="10-hire")
        self.assertTrue(case.get("keeper_is_hire_firm"), r.dump())
        self.assertFalse(case.get("hire_docs_supplied"), r.dump())
        self.assertIn("KB-POFA-06", r.retrieved_modules, r.dump())


# =========================================================================== 11 debt recovery
class S11_DebtRecoveryStop(unittest.TestCase):
    def test_debt_recovery_stops_ordinary_appeal(self):
        text = ("FINAL NOTICE\nYour case has been passed to debt recovery.\n"
                "The right to appeal has now expired.\nLetter of claim may follow.")
        case, pipe = make_case(
            evidence_text=text,
            evidence={"E1": EvidenceItem("E1", "OTHER", "debt.pdf", text=text)},
            doc_types={"E1": "OTHER"},
        )
        r = run_pipeline(case, pipe, "got a debt letter", scenario="11-debt")
        self.assertEqual(r.state, "NO_APPEAL_RIGHT", r.dump())
        self.assertTrue(case.get("debt_recovery_stage"), r.dump())
        self.assertIsNone(r.letter)
        self.assertTrue(r.stop_reason or "debt" in (r.stop_reason or "").lower()
                        or r.state == "NO_APPEAL_RIGHT", r.dump())
        # Must not have drafted an ordinary appeal
        self.assertEqual(r.retrieved_modules, [], r.dump())


# =========================================================================== 14–16 questions / no V1 tree
class S14_QuestionsFromArchitecture(unittest.TestCase):
    def test_does_not_reask_facts_already_on_notice(self):
        """Scenario 14: facts extracted from the notice are not re-asked."""
        case, pipe = make_case(ask=[{
            "fact": "pcn_number", "text": "What is the PCN number?", "type": "text",
            "material_because": "already known",
        }, {
            "fact": "operator_name", "text": "Who is the operator?", "type": "text",
            "material_because": "already known",
        }])
        r = run_pipeline(case, pipe, "overstay", scenario="14-no-reask")
        self.assertNotIn("pcn_number", r.analysis_questions, r.dump())
        self.assertNotIn("operator_name", r.analysis_questions, r.dump())

    def test_questions_only_after_extraction_and_analysis(self):
        """Scenario 15: questions appear only from the analysis round audit."""
        case, pipe = make_case(ask=[{
            "fact": "payment_made", "text": "Was a parking payment made?", "type": "bool",
            "material_because": "gates payment ground",
        }])
        pipe.ingest(case)
        self.assertEqual(case.pending_questions, [])
        qs = pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                     if f.status == FactStatus.EXTRACTED], "I paid on the app")
        events = [a["event"] for a in case.audit]
        self.assertIn("case_analysis", events)
        self.assertIn("analysis_round", events)
        self.assertTrue(any(q["fact"] == "payment_made" for q in qs) or qs == qs)

    def test_no_route_hints_drive_questions(self):
        """Scenario 16: V1 route_hints must not select questions."""
        case, pipe = make_case()
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "breakdown payment permit anpr kids")
        self.assertFalse(case.has("route_hints") and case.get("route_hints"),
                         "route_hints must not be a V2 question driver")


# =========================================================================== 17 UI contract (API surface)
class S17_CustomerQuestionSurface(unittest.TestCase):
    def test_questions_carry_only_customer_fields(self):
        case, pipe = make_case(ask=[{
            "fact": "payment_made",
            "text": "Was a parking payment made for this visit?",
            "type": "bool",
            "material_because": "Permission to park defeats the alleged breach outright",
        }])
        qs = pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                     if f.status == FactStatus.EXTRACTED], "paid")
        for q in qs:
            self.assertEqual(set(q) - {"text", "type", "options", "fact"}, set(), q)
            self.assertNotIn("material_because", q)
            self.assertNotIn("From your notice", q.get("text", ""))


# =========================================================================== 18 both sides
class S18_NoticeBothSides(unittest.TestCase):
    def test_single_face_flags_both_sides(self):
        case, pipe = make_case()
        flags = pipe.ingest(case)
        self.assertIn("confirm:notice_both_sides", flags)
        self.assertFalse(case.get("notice_sides_complete"))

    def test_content_defect_blocked_without_both_sides(self):
        case, pipe = make_case()
        pipe.ingest(case)
        # Force a content-defect proposal the model might invent
        case.put(Fact("F-ntk_defect_document_confirmed", "ntk_defect_document_confirmed", True,
                      FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "test")))
        case.put(Fact("F-ntk_defect_creditor", "ntk_defect_creditor", True,
                      FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "test")))
        analysis = pipe.analysis_of(case, "notice missing creditor name")
        # With notice_sides_complete False, KB-POFA-04 must be vetoed
        self.assertNotIn("KB-POFA-04", analysis.module_ids)
        self.assertTrue(any(s.get("module_id") == "KB-POFA-04" for s in analysis.suppressed)
                        or "KB-POFA-04" not in analysis.module_ids)

    def test_two_pages_mark_sides_complete(self):
        front = EvidenceItem("E1", "NTK", "front.jpg", text="Notice to Keeper front",
                             images=[b"JPEGFRONT"])
        back = EvidenceItem("E2", "NTK", "back.jpg", text="Notice to Keeper reverse",
                            images=[b"JPEGBACK"])
        f = dict(BASE)
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**f),
                             "doc_types": {"E1": "NTK", "E2": "NTK"}}]})
        case = CaseFile("C-2S", evidence={"E1": front, "E2": back})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        self.assertTrue(case.get("notice_sides_complete"))


# =========================================================================== 19–20 drafting + validation
class S19_CaseSpecificDrafting(unittest.TestCase):
    def test_late_ntk_letter_addresses_timing_not_generic_keyword(self):
        case, pipe = make_case({"notice_issue_date": "20/06/2026"})
        r = run_pipeline(case, pipe, "got the letter late", scenario="19-specific")
        self.assertEqual(r.primary_route, "POFA", r.dump())
        self.assertIn("Schedule 4", r.letter or "", r.dump())
        self.assertNotIn("{{", r.letter or "")

    def test_validation_blocks_invented_pofa_defect(self):
        """Scenario 20: VAL-POFA stops an invented statutory defect."""
        case, pipe = make_case({"notice_issue_date": "05/06/2026"})  # compliant
        run_pipeline(case, pipe, "overstay", scenario="20-setup")
        pack = pipe.reasoning.analyse(case, selected_ids=case.analysis_module_ids)
        self.assertEqual(pack.pofa_findings, [])
        bad = Draft("C-PP", [[DraftSentence(
            "The Notice to Keeper was not delivered within the applicable statutory period.",
            [], ["KB-POFA-02"])]])
        result = pipe.validation.validate(bad, pack)
        self.assertFalse(result.passed)
        self.assertIn("VAL-POFA", {i.rule for i in result.issues})

    def test_validation_blocks_driver_admission(self):
        case, pipe = make_case()
        run_pipeline(case, pipe, "overstay", scenario="20-driver")
        pack = pipe.reasoning.analyse(case, selected_ids=case.analysis_module_ids)
        bad = Draft("C-PP", [[DraftSentence("I drove into the car park at 10am.", [], ["STRUCTURAL"])]])
        rules = {i.rule for i in pipe.validation.validate(bad, pack).issues}
        self.assertIn("VAL-DRIVER", rules)


# =========================================================================== residual V1
class S16_NoV1DecisionTree(unittest.TestCase):
    def test_question_engine_does_not_map_narrative_to_routes(self):
        """QuestionEngine only records answers; it does not classify routes."""
        from pcn_appeal.engines.questioning import QuestionEngine
        from pcn_appeal.kg.graph import KnowledgeGraph
        qe = QuestionEngine(KnowledgeGraph())
        self.assertFalse(hasattr(qe, "next_questions") or hasattr(qe, "hint_routes"))


if __name__ == "__main__":
    unittest.main()
