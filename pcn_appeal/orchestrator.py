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
        self.drafter = drafter or TemplateDrafter(self.kg)
        self.fallback = TemplateDrafter(self.kg)
        self.validation = ValidationEngine(judge)

    # step 1-2
    def ingest(self, case: CaseFile) -> list[str]:
        return self.extraction.run(case)

    def confirm(self, case: CaseFile, corrections: dict, confirmed: list[str], narrative: str) -> list[dict]:
        self.extraction.confirm(case, corrections, confirmed)
        self.reasoning.enrich(case)
        self.questions.route_hints(case, narrative)
        return self.questions.next_questions(case)

    # step 3 (called per answer batch; returns follow-ups or [] when done)
    def answer(self, case: CaseFile, answers: dict) -> list[dict]:
        for fact, raw in answers.items():
            self.questions.record_answer(case, fact, raw)
        return self.questions.next_questions(case)

    # ---------------------------------------------------------------- one click
    def auto_appeal(self, case: CaseFile, narrative: str = "",
                    answers: Optional[dict] = None) -> AutoAppealResult:
        """Run the whole journey unattended, pausing only where a missing fact
        actually gates a ground worth having.

        The customer's confirmation step is not skipped so much as defaulted:
        facts the extractor read confidently are auto-confirmed, while anything
        UNCERTAIN is left unconfirmed (EX-02 already bars those from grounding a
        defect) and reported in `flags` so the UI can still query them.
        """
        flags: list[str] = []
        if case.state == CaseState.CREATED:
            flags = self.ingest(case)
            questions = self.confirm(case, {}, self._auto_confirmable(case), narrative)
        else:
            questions = self.questions.next_questions(case)
        if answers:
            questions = self.answer(case, answers)

        blocking = self._blocking_questions(case, questions)
        skipped = [q["fact"] for q in questions if q not in blocking]
        if blocking:
            case.audit.append({"event": "auto_appeal_paused",
                               "asking": [q["fact"] for q in blocking], "skipped": skipped})
            return AutoAppealResult(case.case_id, case.state, blocking, None, flags, skipped)

        case.audit.append({"event": "auto_appeal_generating", "skipped": skipped})
        out = self.generate(case)
        return AutoAppealResult(case.case_id, out.state, [], out, flags, skipped)

    @staticmethod
    def _auto_confirmable(case: CaseFile) -> list[str]:
        """Names of facts the extractor read above the confidence threshold.
        UNCERTAIN facts are deliberately excluded - auto-confirming a value the
        model was unsure of is exactly how a fabricated defect gets into a letter."""
        return [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]

    def _blocking_questions(self, case: CaseFile, questions: list[dict]) -> list[dict]:
        """Keep only questions whose fact gates a module strong enough to lead or
        support a ground (>= SUPPORTING_THRESHOLD). A question that would only
        unlock a weak point is not worth interrupting the customer for."""
        if not questions:
            return []
        facts = case.fact_view()
        hints = set(case.get("route_hints", []))
        gating: set[str] = set()
        for m in self.kg.active_modules():
            if m.strength < SUPPORTING_THRESHOLD or m.route not in hints:
                continue
            if evaluate(m.do_not_use_when, facts):
                continue
            gating |= self.kg.gating_facts(m.module_id)
        return [q for q in questions if q["fact"] in gating]

    # step 4-6
    def generate(self, case: CaseFile) -> AppealOutput:
        pack = self.reasoning.analyse(case)
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
