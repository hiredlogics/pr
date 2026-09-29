"""Orchestrator - the only component that moves a case between states.

    CREATED -> EXTRACTED -> CONFIRMED -> QUESTIONING -> ANALYSED -> DRAFTED
            -> (VALIDATION_FAILED -> regenerate x N) -> RELEASED | MANUAL_REVIEW

Production: run each step as a durable workflow activity (Temporal, or Celery
+ Postgres state table). The customer-facing steps (confirm, answer) are
signals/API calls that resume the workflow; LLM steps are retried idempotently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .drafting.drafter import LLMDrafter, TemplateDrafter
from .engines.account import assess_material_account
from .engines.analysis import AnalysisEngine
from .engines.extraction import ExtractionEngine
from .engines.questioning import QuestionEngine
from .engines.reasoning import SUPPORTING_THRESHOLD, ReasoningEngine
from .engines.recovery import FactRecoveryEngine
from .engines.validation import ValidationEngine
from .kg.graph import KnowledgeGraph
from .models import CaseFile, CaseState, Draft, FactStatus, RetrievalPack, ValidationResult
from .rules.dsl import evaluate

MAX_ATTEMPTS = 3


@dataclass
class AppealOutput:
    state: CaseState
    letter: Optional[str]
    pack: RetrievalPack
    draft: Draft
    validation: ValidationResult
    evidence_list: list[str]


@dataclass
class AutoAppealResult:
    """One-click result. `questions` non-empty means the run paused because a
    missing fact gates a ground that would change the letter; answer them and
    call auto_appeal again to finish. Otherwise `output` holds the appeal.

    `NO_APPEAL_RIGHT` (debt recovery / closed window) sets `stop_reason` and
    never produces an ordinary parking appeal letter.
    """
    case_id: str
    state: CaseState
    questions: list[dict]
    output: Optional[AppealOutput]
    flags: list[str]
    skipped_questions: list[str]
    stop_reason: Optional[str] = None
    recommendation: Optional[str] = None


class AppealPipeline:
    def __init__(self, llm, drafter=None, judge=None, kg: Optional[KnowledgeGraph] = None):
        self.kg = kg or KnowledgeGraph()
        self.extraction = ExtractionEngine(llm)
        self.questions = QuestionEngine(self.kg, llm)
        self.reasoning = ReasoningEngine(self.kg)
        # Exhaust documents + UK rule calculators before any customer question.
        self.recovery = FactRecoveryEngine(self.kg)
        # The only substantive authority over which grounds are argued and which
        # questions are asked. Shares the reasoning engine's retriever so the
        # candidate set comes from the same approved KB index.
        self.analysis = AnalysisEngine(self.kg, llm, retriever=self.reasoning.retriever)
        # LLM drafting is primary so letters weave allegation, evidence and
        # unresolved facts. TemplateDrafter remains the outage / last-attempt
        # fallback and still builds case-specific REC paragraphs from the pack.
        self.drafter = drafter or LLMDrafter(llm)
        self.fallback = TemplateDrafter(self.kg)
        self.validation = ValidationEngine(judge)

    # step 1-2
    def ingest(self, case: CaseFile) -> list[str]:
        return self.extraction.run(case)

    def confirm(self, case: CaseFile, corrections: dict, confirmed: list[str], narrative: str) -> list[dict]:
        self.extraction.confirm(case, corrections, confirmed)
        if case.get("debt_recovery_stage"):
            case.state = CaseState.NO_APPEAL_RIGHT
            case.audit.append({"event": "no_appeal_right",
                               "reason": "debt_recovery_stage after confirmation"})
            return []
        self.reasoning.enrich(case)
        # Stored raw for audit; assess_material_account promotes keeper-safe
        # points into facts when they materially address the allegation.
        case.raw_answers["narrative"] = narrative
        return self._reanalyse(case, narrative)

    # step 3 (called per answer batch; returns follow-ups or [] when done)
    def answer(self, case: CaseFile, answers: dict) -> list[dict]:
        for fact, raw in answers.items():
            self.questions.record_answer(case, fact, raw)
        return self._reanalyse(case, case.raw_answers.get("narrative", ""))

    # ------------------------------------------------------------ V2 analysis
    def _reanalyse(self, case: CaseFile, narrative: str) -> list[dict]:
        """Re-run case analysis against everything now known, and return only the
        questions it still genuinely needs.

        Called after confirmation and again after every answer, because an answer
        changes what is material: it can settle a ground, open one, or make a
        question that looked necessary pointless.
        """
        assess_material_account(case)
        analysis = self.analysis_of(case, narrative)
        case.analysis_module_ids = analysis.module_ids
        case.pending_questions = analysis.questions
        # Q-07: once shown, a question must not reappear under a new name on the
        # next round. Mark as asked when presented; record_answer is idempotent.
        for q in analysis.questions:
            fact = q.get("fact")
            if fact and fact not in case.asked_questions:
                case.asked_questions.append(fact)
        case.audit.append({"event": "analysis_round", "grounds": analysis.module_ids,
                           "asking": [q["fact"] for q in analysis.questions]})
        return analysis.questions

    def analysis_of(self, case: CaseFile, narrative: str):
        """Case analysis with the deterministic inputs it must respect.

        Order: recover from documents/calculators → applicability → LLM analysis.
        Questions are only proposed after recovery has exhausted automatic sources.
        """
        self.recovery.recover(case)
        version, pofa_res = self.reasoning.applicability(case)
        return self.analysis.analyse(case, narrative, pofa=pofa_res,
                                     code_version=getattr(version, "version_id", None))

    # ---------------------------------------------------------------- one click
    def auto_appeal(self, case: CaseFile, narrative: str = "", answers: Optional[dict] = None,
                    skip_remaining: bool = False) -> AutoAppealResult:
        """Run the whole journey unattended, pausing only where a missing fact
        actually gates a ground worth having.

        The customer's confirmation step is not skipped so much as defaulted:
        facts the extractor read confidently are auto-confirmed, while anything
        UNCERTAIN is left unconfirmed (EX-02 already bars those from grounding a
        defect) and reported in `flags` so the UI can still query them.

        `skip_remaining` is the customer declining to answer. It is safe rather
        than a shortcut: the facts stay absent, so every module they gate fails
        its own use_when and simply never fires. The letter gets narrower, never
        less supported - which is why a "skip" button cannot produce a claim the
        customer did not substantiate.
        """
        flags: list[str] = []
        if case.state == CaseState.CREATED:
            flags = self.ingest(case)
            stopped = self._stop_if_no_appeal_right(case, flags)
            if stopped:
                return stopped
            questions = self.confirm(case, {}, self._auto_confirmable(case), narrative)
        else:
            stopped = self._stop_if_no_appeal_right(case, flags)
            if stopped:
                return stopped
            questions = self._reanalyse(case, case.raw_answers.get("narrative", ""))
        if case.state == CaseState.NO_APPEAL_RIGHT:
            return self._stop_if_no_appeal_right(case, flags)  # type: ignore[return-value]
        if answers:
            questions = self.answer(case, answers)

        blocking = [] if skip_remaining else questions
        skipped = [q["fact"] for q in questions if q not in blocking]
        if blocking:
            case.audit.append({"event": "auto_appeal_paused",
                               "asking": [q["fact"] for q in blocking], "skipped": skipped})
            return AutoAppealResult(case.case_id, case.state, blocking, None, flags, skipped)

        case.audit.append({"event": "auto_appeal_generating", "skipped": skipped,
                           "skipped_by_customer": bool(skip_remaining)})
        out = self.generate(case)
        return AutoAppealResult(case.case_id, out.state, [], out, flags, skipped)

    def _stop_if_no_appeal_right(self, case: CaseFile, flags: list[str]) -> Optional[AutoAppealResult]:
        """Debt-recovery / closed appeal window: do not draft an ordinary appeal.

        The fact is document-derived (EX-13). This is a product eligibility stop,
        not a legal ground selector - there is no question path and no letter.
        """
        if not case.get("debt_recovery_stage"):
            return None
        case.state = CaseState.NO_APPEAL_RIGHT
        reason = ("We cannot proceed with a parking appeal because the documents "
                  "show the case has reached debt recovery and the right to appeal "
                  "is no longer available.")
        recommendation = ("Use the Debt Recovery Letter service instead of an "
                          "ordinary parking appeal.")
        case.audit.append({"event": "no_appeal_right", "reason": reason,
                           "recommendation": recommendation})
        return AutoAppealResult(case.case_id, case.state, [], None, flags, [],
                                stop_reason=reason, recommendation=recommendation)

    @staticmethod
    def _auto_confirmable(case: CaseFile) -> list[str]:
        """Names of facts the extractor read above the confidence threshold.
        UNCERTAIN facts are deliberately excluded - auto-confirming a value the
        model was unsure of is exactly how a fabricated defect gets into a letter."""
        return [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]


    # step 4-6
    def generate(self, case: CaseFile) -> AppealOutput:
        if case.get("debt_recovery_stage"):
            case.state = CaseState.NO_APPEAL_RIGHT
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
                prohibited_claims=[], code_version=None, pofa_route="NOT_APPLICABLE",
                pofa_findings=[], driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=["stopped: debt_recovery_stage - no ordinary appeal"])
            return AppealOutput(case.state, None, empty,
                                Draft(case.case_id, []), ValidationResult(False, []), [])
        # Unresolved PCN-number conflict must not ship a letter that may cite
        # the wrong reference. Confirm/correct on the confirmation screen first.
        if case.get("pcn_conflict"):
            case.state = CaseState.MANUAL_REVIEW
            case.audit.append({"event": "blocked_pcn_conflict",
                               "message": "conflicting PCN numbers remain unresolved"})
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts=case.fact_view(), fact_refs={}, missing_facts=["pcn_number"],
                evidence_refs=[], prohibited_claims=[], code_version=None,
                pofa_route="UNRESOLVED", pofa_findings=[],
                driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=["blocked: conflicting PCN numbers"])
            from .models import ValidationIssue
            issues = [ValidationIssue("VAL-CONFLICT", "BLOCK",
                                      "Conflicting PCN numbers were read from the documents. "
                                      "Confirm the correct number before a letter can be released.")]
            return AppealOutput(case.state, None, empty, Draft(case.case_id, []),
                                ValidationResult(False, issues), [])
        pack = self.reasoning.analyse(case, selected_ids=getattr(case, 'analysis_module_ids', None))
        # Structured diagnostic for audit / support — never invents retrieval hits.
        case.audit.append({
            "event": "retrieval_pack",
            "primary_route": pack.primary_route,
            "secondary_routes": list(pack.secondary_routes or []),
            "module_ids": list(pack.module_ids or []),
            "pofa_route": pack.pofa_route,
            "pofa_findings": list(pack.pofa_findings or []),
            "code_version": pack.code_version,
            "missing_facts": list(pack.missing_facts or []),
            "chunk_ids": [c.get("id") for c in (pack.context_chunks or [])],
            "case_context_keys": sorted((pack.case_context or {}).keys()),
            "validation_status": (pack.case_context or {}).get("validation_status"),
            "shopping_receipt_enclosed": (pack.case_context or {}).get("shopping_receipt_enclosed"),
            "trace": list(pack.trace or [])[-40:],
            "recovery_summary": {
                "recovered": list(((case.recovery_report or {}).get("recovered") or {}).keys()),
                "unknown_material": [
                    g.get("fact") for g in ((case.recovery_report or {}).get("unknown_material") or [])
                ],
                "conflicts": list((case.recovery_report or {}).get("conflicts") or []),
                "operator_requestable": list(
                    (case.recovery_report or {}).get("operator_requestable") or []),
            },
        })
        feedback: list[str] = []
        draft = result = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            drafter = self.drafter if attempt < MAX_ATTEMPTS else self.fallback
            try:
                draft = drafter.draft(case.case_id, pack, feedback, attempt)
            except Exception as exc:                     # LLM outage / bad JSON / demo reader
                case.audit.append({"event": "draft_error", "attempt": attempt, "error": str(exc)})
                draft = self.fallback.draft(case.case_id, pack, feedback, attempt)
            case.state = CaseState.DRAFTED
            result = self.validation.validate(draft, pack)
            case.audit.append({"event": "validation", "attempt": attempt, "passed": result.passed,
                               "issues": [i.rule for i in result.issues]})
            if result.passed:
                case.state = CaseState.RELEASED
                return AppealOutput(case.state, render(draft), pack, draft, result, self._evidence_list(case))
            case.state = CaseState.VALIDATION_FAILED
            feedback = [f"{i.rule}: {i.message} :: {i.sentence}" for i in result.issues]
        case.state = CaseState.MANUAL_REVIEW
        return AppealOutput(case.state, None, pack, draft, result, self._evidence_list(case))

    @staticmethod
    def _evidence_list(case: CaseFile) -> list[str]:
        return [f"{e.kind}: {e.filename}" for e in case.evidence.values()
                if e.uploaded and e.kind not in ("PCN", "NTK", "NTD")]


def render(draft: Draft) -> str:
    """Strip provenance and return the customer-facing letter body.
    Production: Jinja2 -> HTML -> WeasyPrint PDF, plus evidence list page."""
    return draft.plain_text()
