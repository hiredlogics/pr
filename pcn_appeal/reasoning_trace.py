"""P17.9 — joined diagnostic reasoning trace (observability).

INPUT → DOCUMENT → SEMANTIC → FACTMANAGER → MODULES → CI → CLAIM PLAN → …
"""
from __future__ import annotations

import json
from typing import Any, Optional


STAGES = (
    "INPUT",
    "DOCUMENT_OBSERVATIONS",
    "SEMANTIC_STATE",
    "FACTMANAGER",
    "FACT_VIEW",
    "MODULE_CANDIDATES",
    "MODULE_ELIGIBILITY",
    "CASE_INTELLIGENCE",
    "CLAIM_PLAN",
    "SUPPORT_BUNDLE",
    "DRAFT_PLAN",
    "VALIDATION",
)


def build_reasoning_trace(case) -> dict[str, Any]:
    """Assemble a joined diagnostic from case audit + resolver attachments."""
    raw = case.raw_answers or {}
    sem_resolve = _json(raw.get("_semantic_resolve_trace"))
    mod_resolve = _json(raw.get("_module_resolve"))
    sem_state = _json(raw.get("_semantic_case_state"))

    events = list(getattr(case, "audit", None) or [])
    by_event = {}
    for e in events:
        if isinstance(e, dict) and e.get("event"):
            by_event.setdefault(e["event"], []).append(e)

    claim = None
    for e in reversed(events):
        if e.get("event") == "claim_plan_locked":
            claim = e
            break

    selected_why = []
    if isinstance(mod_resolve, dict):
        rows = mod_resolve.get("rows") or {}
        for mid, row in rows.items():
            if (row or {}).get("status") == "SUPPORTED":
                selected_why.append({
                    "module_id": mid,
                    "supporting_facts": (row or {}).get("supporting_facts") or [],
                    "supporting_events": (row or {}).get("supporting_events") or [],
                    "narrative_atoms": (row or {}).get("narrative_atoms") or [],
                    "relationships": (row or {}).get("relationships") or [],
                    "evidence_refs": (row or {}).get("evidence_refs") or [],
                    "candidate_reason": (row or {}).get("candidate_reason"),
                    "matcher_status": (row or {}).get("matcher_status"),
                    "eligible": (row or {}).get("eligible"),
                })

    return {
        "stages": list(STAGES),
        "INPUT": (sem_resolve or {}).get("input"),
        "DOCUMENT_OBSERVATIONS": (
            (sem_resolve or {}).get("input", {}) or {}
        ).get("extracted_document_observations"),
        "SEMANTIC_STATE": {
            "revision": (sem_state or {}).get("revision") or raw.get("_semantic_revision"),
            "concepts_n": len((sem_state or {}).get("concepts") or []),
            "events_n": len((sem_state or {}).get("events") or []),
            "atoms_n": len((sem_state or {}).get("narrative_atoms") or []),
            "relationships_n": len((sem_state or {}).get("relationships") or []),
            "material_relevance": (sem_state or {}).get("material_relevance"),
        },
        "FACTMANAGER": {
            "material_conflicts": (sem_resolve or {}).get("material_conflicts"),
            "handoff_ready": (sem_resolve or {}).get("handoff_ready"),
            "handoff_reasons": (sem_resolve or {}).get("handoff_reasons"),
        },
        "FACT_VIEW": {
            k: case.get(k)
            for k in (
                "multiple_visits", "left_site", "returned_same_day", "payment_made",
                "dropoff_activity", "vehicle_immobilised", "pcn_number", "vrm",
            )
            if case.has(k)
        },
        "MODULE_CANDIDATES": (mod_resolve or {}).get("candidates"),
        "MODULE_ELIGIBILITY": {
            "eligible_ids": (mod_resolve or {}).get("eligible_ids"),
            "unresolved_ids": (mod_resolve or {}).get("unresolved_ids"),
            "invariant": (mod_resolve or {}).get("invariant"),
        },
        "CASE_INTELLIGENCE": [
            {k: e.get(k) for k in ("proposed", "kept", "suppressed", "candidates")}
            for e in by_event.get("case_analysis", [])
        ],
        "CLAIM_PLAN": claim,
        "SUPPORT_BUNDLE": selected_why,
        "selected_module_why": selected_why,
        "audit_resolver_events": {
            "semantic_case_resolver": by_event.get("semantic_case_resolver", [])[-1:],
            "knowledge_module_resolver": by_event.get("knowledge_module_resolver", [])[-1:],
        },
    }


def _json(raw: Any) -> Optional[dict]:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None
