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
from .engines.reasoning import ReasoningEngine
from .engines.validation import ValidationEngine
from .kg.graph import KnowledgeGraph
from .models import CaseFile, CaseState, Draft, RetrievalPack, ValidationResult

MAX_ATTEMPTS = 3


@dataclass
class AppealOutput:
    state: CaseState
    letter: Optional[str]
    pack: RetrievalPack
    draft: Draft
    validation: ValidationResult
    evidence_list: list[str]


class AppealPipeline:
    def __init__(self, llm, drafter=None, judge=None, kg: Optional[KnowledgeGraph] = None):
        self.kg = kg or KnowledgeGraph()
        self.extraction = ExtractionEngine(llm)
        self.questions = QuestionEngine(self.kg)
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
