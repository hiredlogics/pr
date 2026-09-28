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

from .drafting.drafter import TemplateDrafter
from .engines.analysis import AnalysisEngine
from .engines.extraction import ExtractionEngine
from .engines.questioning import QuestionEngine
from .engines.reasoning import SUPPORTING_THRESHOLD, ReasoningEngine
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
    call auto_appeal again to finish. Otherwise `output` holds the appeal."""
    case_id: str
    state: CaseState
    questions: list[dict]
    output: Optional[AppealOutput]
    flags: list[str]
    skipped_questions: list[str]


class AppealPipeline:
    def __init__(self, llm, drafter=None, judge=None, kg: Optional[KnowledgeGraph] = None):
        self.kg = kg or KnowledgeGraph()
        self.extraction = ExtractionEngine(llm)
        self.questions = QuestionEngine(self.kg, llm)
        self.reasoning = ReasoningEngine(self.kg)
        # The only substantive authority over which grounds are argued and which
        # questions are asked. Shares the reasoning engine's retriever so the
        # candidate set comes from the same approved KB index.
        self.analysis = AnalysisEngine(self.kg, llm, retriever=self.reasoning.retriever)
        self.drafter = drafter or TemplateDrafter(self.kg)
        self.fallback = TemplateDrafter(self.kg)
        self.validation = ValidationEngine(judge)

    # step 1-2
    def ingest(self, case: CaseFile) -> list[str]:
        return self.extraction.run(case)

    def confirm(self, case: CaseFile, corrections: dict, confirmed: list[str], narrative: str) -> list[dict]:
        self.extraction.confirm(case, corrections, confirmed)
        self.reasoning.enrich(case)
        case.raw_answers["narrative"] = narrative        # audit only; never drafted from
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
        analysis = self.analysis_of(case, narrative)
        case.analysis_module_ids = analysis.module_ids
        case.pending_questions = analysis.questions
        case.audit.append({"event": "analysis_round", "grounds": analysis.module_ids,
                           "asking": [q["fact"] for q in analysis.questions]})
        return analysis.questions

    def analysis_of(self, case: CaseFile, narrative: str):
        """Case analysis with the deterministic inputs it must respect."""
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
            questions = self.confirm(case, {}, self._auto_confirmable(case), narrative)
        else:
            questions = self._reanalyse(case, case.raw_answers.get("narrative", ""))
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

    @staticmethod
    def _auto_confirmable(case: CaseFile) -> list[str]:
        """Names of facts the extractor read above the confidence threshold.
        UNCERTAIN facts are deliberately excluded - auto-confirming a value the
        model was unsure of is exactly how a fabricated defect gets into a letter."""
        return [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]


    # step 4-6
    def generate(self, case: CaseFile) -> AppealOutput:
        pack = self.reasoning.analyse(case, selected_ids=getattr(case, 'analysis_module_ids', None))
        feedback: list[str] = []
        draft = result = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            drafter = self.drafter if attempt < MAX_ATTEMPTS else self.fallback
            try:
                draft = drafter.draft(case.case_id, pack, feedback, attempt)
            except Exception as exc:                     # LLM outage / bad JSON
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
