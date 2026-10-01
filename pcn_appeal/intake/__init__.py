"""Intake: the common front of every service.

    upload -> file validation (ingest.py) -> OCR / vision pages
           -> classify (classifier.py)      neutral, once per upload
           -> resolve route (router.py)     exactly one route
           -> route outcome                 redirect / stage stop, if any
           -> route completeness            that route's own upload rule
           -> service engine                (services/)

Nothing service-specific runs before the route is known, so no service's
assumptions decide which service a document belongs to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import CaseFile, CaseState
from ..rules import scope
from ..rules.scope import ScopeStop
from .classifier import ClassificationFailed, DocumentClassification, classify
from .router import RouteDecision, resolve


@dataclass
class IntakeResult:
    decision: Optional[RouteDecision]
    # The route's completeness check; None when the case stopped before it.
    check: Optional[object] = None
    stop: Optional[ScopeStop] = None

    @property
    def proceed(self) -> bool:
        return self.stop is None and self.check is not None and self.check.ok


def _timeline(classifications: dict[str, DocumentClassification]) -> list[dict]:
    from ..engines.extraction import parse_uk_date
    events = []
    for c in classifications.values():
        d = parse_uk_date(c.document_date) if c.document_date else None
        if d is not None:
            events.append({"date": d.isoformat(), "event": f"{c.document_type} dated",
                           "evidence_id": c.evidence_id, "source": "classification"})
    return sorted(events, key=lambda e: e["date"])


def reset(case: CaseFile) -> None:
    """Forget the intake decision, for a re-upload into the same case."""
    case.route = case.document_type = case.stage = None
    case.classifications = {}
    case.timeline = []
    case.scope_stop = None


def run_intake(case: CaseFile, llm) -> IntakeResult:
    """Classify the uploaded documents, pick the route and apply its gates.

    Sets case.route / document_type / stage / classifications. A redirect or
    stage stop sets NO_APPEAL_RIGHT; a classifier failure sets
    CLASSIFICATION_FAILED. Neither runs any service engine.
    """
    from ..services import engine_for

    try:
        classifications = classify(case, llm)
    except ClassificationFailed as exc:
        case.state = CaseState.CLASSIFICATION_FAILED
        case.scope_stop = "CLASSIFICATION_FAILED"
        case.audit.append({"event": "classification_failed", "stage": "intake",
                           "reason": str(exc)})
        return IntakeResult(None, stop=scope.STOPS["CLASSIFICATION_FAILED"])

    case.classifications = {k: v.to_dict() for k, v in classifications.items()}
    decision = resolve(classifications)
    case.route, case.document_type, case.stage = (decision.route, decision.document_type,
                                                  decision.stage)
    case.timeline = _timeline(classifications)
    case.audit.append({"event": "intake_classified",
                       "documents": {k: {"type": v.document_type, "stage": v.stage,
                                         "confidence": v.confidence,
                                         "ambiguity": v.ambiguity_reason, "notes": v.notes}
                                     for k, v in classifications.items()}})
    case.audit.append({"event": "route_decided", **decision.to_dict()})

    engine = engine_for(decision.route)
    stop = engine.outcome(case)
    if stop is not None:
        case.state = CaseState.NO_APPEAL_RIGHT
        case.scope_stop = stop.code
        case.audit.append({"event": "no_appeal_right", "stage": "intake",
                           "route": decision.route, "document_class": stop.code})
        return IntakeResult(decision, stop=stop)

    check = engine.validate_documents(case)
    case.audit.append({"event": "route_completeness", "route": decision.route,
                       "policy": check.policy, "ok": check.ok, "reason": check.reason,
                       "enforced": check.enforced})
    return IntakeResult(decision, check=check)


__all__ = ["IntakeResult", "run_intake", "reset", "ClassificationFailed"]
