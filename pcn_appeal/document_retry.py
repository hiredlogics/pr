"""Reopen a held case for replacement pages without losing its audit or account."""
from .document_baseline import _document_fact_names
from .document_identity import CRITICAL_FIELDS
from .fact_graph import FactManager, now
from .models import CaseFile, CaseState, SourceKind


def reopen(case: CaseFile) -> CaseFile:
    if case.state == CaseState.RELEASED:
        raise ValueError("A released appeal cannot have its documents replaced")
    case.begin_run("replace_documents")
    document_names = _document_fact_names() | set(CRITICAL_FIELDS)
    for name, fact in list(case.facts.items()):
        if name in document_names or fact.source.kind != SourceKind.ANSWER:
            FactManager.retract(case, name, reason="documents_replaced")
    fresh = CaseFile(case.case_id, driver_status=case.driver_status)
    fresh.facts = case.facts
    fresh.fact_conflicts = case.fact_conflicts
    for conflict in fresh.fact_conflicts:
        if conflict["fact"] not in fresh.facts and conflict["status"] != "RESOLVED":
            conflict.update(status="RESOLVED", resolution={"reason": "documents_replaced"},
                            resolved_by="document_retry", resolved_at=now())
    fresh.run_id, fresh.run_status = case.run_id, case.run_status
    fresh.frontend_version = case.frontend_version
    fresh.raw_answers = {k: v for k, v in case.raw_answers.items()
                         if not k.startswith("_") and k not in document_names
                         and k != "notice_reverse_pages"}
    for name in ("audit", "ai_calls", "state_history", "fact_history", "fact_sources",
                 "draft_versions", "claim_plans", "document_baselines"):
        setattr(fresh, name, getattr(case, name))
    for plan in fresh.claim_plans:
        if plan.status == "LOCKED":
            plan.status = "SUPERSEDED"
    fresh.audit.append({"event": "documents_reopened", "previous_state": case.state.value,
                        "previous_evidence_ids": list(case.evidence), "run_id": fresh.run_id})
    return fresh
