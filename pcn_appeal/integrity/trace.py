"""The complete execution trace of one run (P5.5 §1, §6).

Assembled from what the engines already record - the run's audit entries
(each stamped with run_id and time), the fact history, the state history, the
knowledge match, the question reviews, the claim plan and the AI call log -
into one timeline:

    DOCUMENT_EXTRACTION -> FACT_EXTRACTION -> KNOWLEDGE_MATCH -> CASE_ANALYSIS
    -> QUESTIONS -> CLAIM_PLAN -> RETRIEVAL -> DRAFTING -> VALIDATION -> OUTCOME

Each stage has a status (SUCCESS / FAILED / HELD / NOT_RUN), timing and
counts. Nothing here decides anything: it reads.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

STAGES = ("INTAKE", "DOCUMENT_EXTRACTION", "FACT_EXTRACTION", "KNOWLEDGE_MATCH",
          "CASE_ANALYSIS", "QUESTIONS", "CLAIM_PLAN", "RETRIEVAL", "DRAFTING",
          "VALIDATION", "OUTCOME")

# audit event -> stage
_EVENT_STAGE = {
    "intake_classified": "INTAKE", "route_decided": "INTAKE", "route_completeness": "INTAKE",
    "classification_failed": "INTAKE", "no_appeal_right": "INTAKE",
    "upload_read": "DOCUMENT_EXTRACTION", "injection_flag": "DOCUMENT_EXTRACTION",
    "notice_sides_assessment": "DOCUMENT_EXTRACTION",
    "blocked_notice_sides_incomplete": "DOCUMENT_EXTRACTION",
    "upload_rejected_incomplete_sides": "DOCUMENT_EXTRACTION",
    "pcn_read_disagreement": "DOCUMENT_EXTRACTION",
    "fact_recovery": "FACT_EXTRACTION", "material_account": "FACT_EXTRACTION",
    "narrative_understanding": "FACT_EXTRACTION", "fact_set": "FACT_EXTRACTION",
    "fact_api_write": "FACT_EXTRACTION", "fact_conflict": "FACT_EXTRACTION",
    "fact_conflict_resolved": "FACT_EXTRACTION", "pcn_conflict": "FACT_EXTRACTION",
    "hypothesis_created": "FACT_EXTRACTION", "hypothesis_withdrawn": "FACT_EXTRACTION",
    "hypothesis_reopened": "FACT_EXTRACTION", "driver_disclosure_set": "FACT_EXTRACTION",
    "driver_disclosure_corrected": "FACT_EXTRACTION",
    "held_needs_fact_confirmation": "FACT_EXTRACTION", "blocked_pcn_conflict": "FACT_EXTRACTION",
    "knowledge_match": "KNOWLEDGE_MATCH",
    "case_analysis_completed": "CASE_ANALYSIS", "case_analysis_error": "CASE_ANALYSIS",
    "case_analysis": "CASE_ANALYSIS", "case_analysis_reassessment_error": "CASE_ANALYSIS",
    "analysis_claim_plan": "CASE_ANALYSIS", "claim_plan": "CASE_ANALYSIS",
    "analysis_round": "CASE_ANALYSIS", "ground_recovery": "CASE_ANALYSIS",
    "ground_recovery_preserved": "CASE_ANALYSIS",
    "analysis_failed_nothing_selected": "CASE_ANALYSIS",
    "analysis_complete_no_supported_grounds": "CASE_ANALYSIS",
    "held_needs_site_postcode": "CASE_ANALYSIS", "site_postcode_material": "CASE_ANALYSIS",
    "question_review": "QUESTIONS", "question_round": "QUESTIONS",
    "hypothesis_question_asked": "QUESTIONS", "auto_appeal_paused": "QUESTIONS",
    "claim_plan_locked": "CLAIM_PLAN", "claim_plan_reused": "CLAIM_PLAN",
    "retrieval_pack": "RETRIEVAL", "legal_findings": "CASE_ANALYSIS",
    "draft_error": "DRAFTING", "no_ground": "DRAFTING", "no_ground_after_widen": "DRAFTING",
    "closing_added": "DRAFTING", "auto_appeal_generating": "DRAFTING",
    "validation": "VALIDATION", "dropped_failing_sentences": "VALIDATION",
    "draft_context": "DRAFTING", "draft_version": "DRAFTING",
    "draft_validation": "VALIDATION", "shadow_judge": "VALIDATION",
    "customer_outcome": "OUTCOME", "run_completed": "OUTCOME",
    "execution_manifest": "OUTCOME", "execution_manifest_failed": "OUTCOME",
    "integrity_check": "OUTCOME",
}
_AI_TASK_STAGE = {"classification": "INTAKE", "page_references": "INTAKE",
                  "extraction": "DOCUMENT_EXTRACTION", "case_analysis": "CASE_ANALYSIS",
                  "drafting": "DRAFTING", "validation": "VALIDATION"}
_FAILED = {"classification_failed", "case_analysis_error", "draft_error",
           "case_analysis_reassessment_error", "execution_manifest_failed",
           "analysis_failed_nothing_selected", "no_ground_after_widen"}
_HELD = {"held_needs_fact_confirmation", "blocked_pcn_conflict", "held_needs_site_postcode",
         "analysis_complete_no_supported_grounds", "blocked_notice_sides_incomplete",
         "upload_rejected_incomplete_sides", "auto_appeal_paused", "no_appeal_right"}


def _stage_of(entry: dict) -> Optional[str]:
    event = entry.get("event")
    if event == "ai_call":
        return _AI_TASK_STAGE.get(entry.get("task"), "DOCUMENT_EXTRACTION")
    return _EVENT_STAGE.get(event)


def _parse(at: Any) -> Optional[datetime]:
    if not at or not isinstance(at, str):
        return None
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_audit(case, run_id: Optional[int] = None) -> list[dict]:
    run_id = case.run_id if run_id is None else run_id
    if not run_id:
        return list(case.audit)
    return [a for a in case.audit if a.get("run_id", 0) == run_id]


def versions(case, run_id: Optional[int] = None) -> dict:
    """What produced this run: code, KB, prompts, models - from the run's
    manifest, else its claim plan, else what the AI calls recorded."""
    audit = run_audit(case, run_id)
    manifest = next((a.get("manifest") for a in reversed(audit)
                     if a.get("event") == "execution_manifest"), None) or {}
    plan = next((p for p in reversed(getattr(case, "claim_plans", []) or [])
                 if p.run_number == (run_id or case.run_id)), None)
    trust = dict(plan.trust) if plan is not None else {}
    calls = [a for a in audit if a.get("event") == "ai_call"]
    return {
        "code": manifest.get("commit") or trust.get("code_version"),
        "build_id": manifest.get("build_id"),
        "kb": (manifest.get("kb") or {}).get("digest")
        or (dict(trust.get("kb_version") or {})).get("digest"),
        "kb_release": (manifest.get("kb") or {}).get("release_id"),
        "relations_version": (manifest.get("kb") or {}).get("relations_version"),
        "prompts": manifest.get("prompts") or dict(trust.get("prompt_versions") or {}),
        "models": manifest.get("models") or dict(trust.get("model_versions") or {})
        or {c["task"]: c.get("model") for c in calls},
        "provider": manifest.get("provider") or trust.get("provider"),
        "validator": (manifest.get("validator") or {}).get("version"),
        "claim_plan_version": plan.version if plan is not None else None,
    }


def state_history(case, run_id: Optional[int] = None) -> list[dict]:
    """State transitions with the reason: the audit event recorded at the
    transition - the one appended next (the orchestrator sets the state, then
    records why) or, failing that, the one just before it. A transition
    loaded from the store carries the reason it was saved with."""
    rows = [dict(t) for t in getattr(case, "state_history", []) or []
            if run_id is None or t.get("run_id", 0) == run_id]
    full = list(case.audit)
    for t in rows:
        t.pop("_persisted", None)
        if t.get("audit_index") is not None:
            t["preceded_by"] = _preceded_by(t, full)
        if t.get("reason"):
            continue
        t["reason"] = transition_reason(t, full)
    return rows


def _explanatory(entries: list[dict]) -> list[dict]:
    return [a for a in entries if a.get("event") not in _NOT_A_REASON]


def _preceded_by(t: dict, audit: list[dict]) -> Optional[str]:
    """The last explanatory event before the transition - what had just
    happened when the state changed."""
    idx = t["audit_index"]
    before = _explanatory(audit[max(0, idx - 8):idx])
    return _reason(before[-1]) if before else None


def transition_reason(t: dict, audit: list[dict]) -> Optional[str]:
    """The explanatory event recorded next after the transition (the engines
    set the state, then record why); failing that, the one just before it;
    failing that, the stage the case was in."""
    idx = t.get("audit_index")
    if idx is not None:
        after = _explanatory(audit[idx:idx + 8])
        if after:
            return _reason(after[0])
        before = _explanatory(audit[max(0, idx - 8):idx])
        if before:
            return _reason(before[-1])
        near = audit[idx] if idx < len(audit) else (audit[idx - 1] if idx and audit else None)
        stage = _stage_of(near) if near else None
        return f"during {stage}" if stage else None
    # no index (an older record): the first explanatory event at or after the time
    when = _parse(t.get("at"))
    for a in audit:
        at = _parse(a.get("at"))
        if at is None or when is None or at < when or a.get("event") in _NOT_A_REASON:
            continue
        return _reason(a)
    return None


# Bookkeeping events that follow a transition without explaining it.
_NOT_A_REASON = {"ai_call", "fact_set", "fact_api_write", "fact_recovery", "material_account",
                 "narrative_understanding", "hypothesis_created", "hypothesis_withdrawn",
                 "hypothesis_reopened", "knowledge_match", "notice_sides_assessment"}


def _reason(a: dict) -> str:
    event = a.get("event", "")
    for key in ("reason", "outcome", "message", "trigger"):
        if a.get(key) and isinstance(a[key], str):
            return f"{event}: {a[key]}"
    if event == "validation":
        return f"validation: {'passed' if a.get('passed') else 'failed'} {a.get('issues') or []}"
    if event == "analysis_round":
        return f"analysis_round: asking {a.get('asking') or []}"
    return event


def execution_trace(case, run_id: Optional[int] = None) -> dict:
    run_id = case.run_id if run_id is None else run_id
    audit = run_audit(case, run_id)
    from ..engines.claim_plan_authority import run_uuid
    stages: dict[str, dict] = {s: {"stage": s, "status": "NOT_RUN", "events": [], "errors": [],
                                   "started_at": None, "ended_at": None}
                               for s in STAGES}
    for a in audit:
        stage = _stage_of(a)
        if stage is None:
            continue
        st = stages[stage]
        name = a.get("event") if a.get("event") != "ai_call" else f"ai_call:{a.get('task')}"
        st["events"].append(name)
        at = a.get("at")
        if at and (st["started_at"] is None or at < st["started_at"]):
            st["started_at"] = at
        end = at
        if a.get("event") == "ai_call" and at and a.get("duration_ms"):
            # the call's start is stamped; its end is start + duration
            start = _parse(at)
            if start is not None:
                end = (start + timedelta(milliseconds=a["duration_ms"])).isoformat(
                    timespec="milliseconds")
        if end and (st["ended_at"] is None or end > st["ended_at"]):
            st["ended_at"] = end
        if a.get("event") in _FAILED or (a.get("event") == "ai_call" and a.get("status") == "ERROR"):
            st["errors"].append(a.get("error") or a.get("reason") or a.get("event"))
        if a.get("event") in _HELD:
            st["held"] = a.get("reason") or a.get("event")
    for st in stages.values():
        if st["events"]:
            st["status"] = ("FAILED" if st["errors"] else "HELD" if st.get("held")
                            else "SUCCESS")
            s, e = _parse(st["started_at"]), _parse(st["ended_at"])
            st["duration_ms"] = int((e - s).total_seconds() * 1000) if s and e else None
    _counts(case, audit, stages)
    plan = next((p for p in reversed(getattr(case, "claim_plans", []) or [])
                 if p.run_number == run_id), None)
    steps = [stages[s] for s in STAGES]
    return {
        "case_id": case.case_id, "run_id": run_id,
        "execution_id": run_uuid(case.case_id, run_id),
        "versions": versions(case, run_id),
        "final_state": getattr(case.state, "value", str(case.state)),
        "state_history": state_history(case, run_id),
        "steps": steps,
        "ai_calls": [{k: v for k, v in c.items() if k not in ("case_id",)}
                     for c in getattr(case, "ai_calls", []) or [] if c.get("run_id") == run_id],
        "claim_plan": None if plan is None else {
            "claim_plan_id": plan.claim_plan_id, "version": plan.version,
            "status": plan.status, "approved": plan.supported_ids, "trace": plan.trace()},
        # P6.1: every legal defect assessment for the case - the evidence a
        # defect claim or defect sentence stands on.
        "legal_findings": [{k: r.get(k) for k in (
            "finding_id", "finding_type", "status", "supporting_facts",
            "calculation_result", "legal_module_id")}
            for r in getattr(case, "legal_findings", []) or []],
        "audit_events": len(audit),
    }


def _counts(case, audit: list[dict], stages: dict) -> None:
    run_id = audit[0].get("run_id") if audit else case.run_id
    history = [h for h in getattr(case, "fact_history", []) or []
               if run_id is None or h.get("run_id") == run_id]
    stages["FACT_EXTRACTION"]["facts_created"] = sum(
        1 for h in history if h.get("outcome") == "APPLIED" and h.get("previous") is None)
    stages["FACT_EXTRACTION"]["facts_changed"] = sum(
        1 for h in history if h.get("outcome") == "APPLIED" and h.get("previous") is not None)
    stages["FACT_EXTRACTION"]["conflicts"] = sum(1 for h in history if h.get("outcome") == "CONFLICT")
    km = [a for a in audit if a.get("event") == "knowledge_match"]
    if km:
        last = km[-1]
        stages["KNOWLEDGE_MATCH"]["modules_found"] = len(last.get("selected") or [])
        stages["KNOWLEDGE_MATCH"]["modules_relevant"] = len(last.get("relevant") or [])
        stages["KNOWLEDGE_MATCH"]["modules_rejected"] = len(last.get("rejected") or [])
    ca = [a for a in audit if a.get("event") == "case_analysis"]
    if ca:
        stages["CASE_ANALYSIS"]["proposed"] = ca[-1].get("proposed") or []
        stages["CASE_ANALYSIS"]["kept"] = ca[-1].get("kept") or []
        stages["CASE_ANALYSIS"]["rounds"] = len(ca)
    qr = [a for a in audit if a.get("event") == "question_review"]
    if qr:
        stages["QUESTIONS"]["questions_asked"] = sum(1 for a in qr if a.get("shown"))
        stages["QUESTIONS"]["questions_approved"] = sum(1 for a in qr if a.get("decision") == "APPROVED")
        stages["QUESTIONS"]["questions_rejected"] = sum(1 for a in qr if a.get("decision") == "REJECTED")
    cp = [a for a in audit if a.get("event") in ("claim_plan_locked", "claim_plan_reused")]
    if cp:
        stages["CLAIM_PLAN"]["decision"] = cp[-1]["event"]
        stages["CLAIM_PLAN"]["version"] = cp[-1].get("version")
        stages["CLAIM_PLAN"]["approved"] = cp[-1].get("approved")
    rp = [a for a in audit if a.get("event") == "retrieval_pack"]
    if rp:
        stages["RETRIEVAL"]["chunks"] = len(rp[-1].get("chunk_ids") or [])
        stages["RETRIEVAL"]["module_ids"] = rp[-1].get("module_ids")
    val = [a for a in audit if a.get("event") == "validation"]
    if val:
        stages["VALIDATION"]["attempts"] = len(val)
        stages["VALIDATION"]["passed"] = bool(val[-1].get("passed"))
        stages["VALIDATION"]["issues"] = val[-1].get("issues") or []
    stages["DRAFTING"]["attempts"] = sum(1 for a in audit if a.get("event") == "ai_call"
                                         and a.get("task") == "drafting")
    out = [a for a in audit if a.get("event") == "customer_outcome"]
    if out:
        stages["OUTCOME"]["outcome"] = out[-1].get("outcome")
    rc = [a for a in audit if a.get("event") == "run_completed"]
    if rc:
        stages["OUTCOME"]["run_outcome"] = rc[-1].get("outcome")


__all__ = ["execution_trace", "versions", "state_history", "transition_reason", "run_audit",
           "STAGES"]
