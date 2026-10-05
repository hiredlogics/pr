"""Adaptive question materiality gate (generic — no case/operator rules).

A question may be asked only when a currently viable module needs the fact,
the answer can change outcome, and the fact is not already resolved.
"""
from __future__ import annotations

from typing import Any, Optional

from ..models import CaseFile
from ..rules.dsl import evaluate


def modules_requiring(case: CaseFile, kg, fact: str) -> list[dict]:
    """Active modules that gate on / require `fact`, with current status."""
    facts = case.fact_view()
    out = []
    for m in kg.active_modules():
        gated = fact in (kg.gating_facts(m.module_id) or set())
        required = fact in (m.required_facts or [])
        if not gated and not required:
            continue
        try:
            use_ok = bool(evaluate(m.use_when, facts))
        except Exception:
            use_ok = False
        try:
            blocked = bool(evaluate(m.do_not_use_when, facts))
        except Exception:
            blocked = False
        if blocked:
            status = "BLOCKED"
        elif use_ok:
            status = "SUPPORTED"
        else:
            status = "UNRESOLVED"
        out.append({
            "module_id": m.module_id,
            "status": status,
            "requires": required,
            "gates_on": gated,
        })
    return out


def fact_already_resolved(case: CaseFile, fact: str) -> tuple[bool, list[str]]:
    """Whether the fact is already reliably available; list sources checked."""
    checked: list[str] = []
    node = case.facts.get(fact) if getattr(case, "facts", None) else None
    if node is not None:
        checked.append(f"fact_graph:{fact}")
        if getattr(node, "usable", False) and node.value not in (None, "", [], "UNKNOWN"):
            return True, checked
    if fact == "jurisdiction":
        checked.append("jurisdiction")
        if case.get("jurisdiction") not in (None, "", "UNKNOWN"):
            return True, checked
    if fact == "site_postcode":
        checked.extend(["site_postcode", "jurisdiction", "parking_location"])
        if case.has("site_postcode"):
            return True, checked
        if case.get("jurisdiction") not in (None, "", "UNKNOWN"):
            return True, checked
    # Prior explicit answer of same fact (non-prose closed form).
    raw = (case.raw_answers or {}).get(fact)
    if raw not in (None, "") and fact in (case.asked_questions or []):
        checked.append("prior_answer")
        if str(raw).strip().lower() not in ("", "unknown", "not sure"):
            # Only count if already promoted to usable fact.
            if node is not None and getattr(node, "usable", False):
                return True, checked
    return False, checked


def unresolved_module_ids(case: CaseFile) -> set[str]:
    """UNRESOLVED modules from KnowledgeModuleResolver (adaptive question source)."""
    import json
    raw = (case.raw_answers or {}).get("_module_resolve")
    if not raw:
        return set()
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return set()
    return set(data.get("unresolved_ids") or [])


def annotate(case: CaseFile, kg, question: dict, *,
             case_revision: Optional[str] = None) -> dict:
    """Return materiality record for one candidate question."""
    fact = question.get("fact") or ""
    requesters = modules_requiring(case, kg, fact) if fact else []
    viable = [r for r in requesters if r["status"] in ("SUPPORTED", "UNRESOLVED")]
    blocked_only = bool(requesters) and not viable
    resolved, sources = fact_already_resolved(case, fact)
    can_change = bool(viable) and not resolved and not blocked_only
    # Unlock metadata (e.g. postcode → PoFA modules) also counts as change.
    unlocks = list(question.get("unlocks") or [])
    if unlocks and not resolved:
        can_change = True
        for mid in unlocks:
            if not any(r["module_id"] == mid for r in requesters):
                requesters.append({
                    "module_id": mid, "status": "UNRESOLVED",
                    "requires": True, "gates_on": True,
                })
        viable = [r for r in requesters if r["status"] in ("SUPPORTED", "UNRESOLVED")]

    ask = bool(fact) and can_change and (bool(viable) or bool(unlocks)) and not resolved
    # P17.9: prefer questions that unlock KnowledgeModuleResolver UNRESOLVED candidates.
    unres = unresolved_module_ids(case)
    if ask and unres and not unlocks:
        viable_ids = {r["module_id"] for r in viable}
        if viable_ids and not (viable_ids & unres):
            ask = False
    suppress = None
    if not fact:
        suppress = "missing_target_fact"
    elif resolved:
        suppress = "already_resolved_from_existing_evidence"
    elif blocked_only:
        suppress = "modules_blocked_unrelated"
    elif not viable and not unlocks:
        suppress = "no_viable_module_requires_fact"
    elif not can_change:
        suppress = "answer_cannot_change_outcome"
    elif not ask and unres and viable and not unlocks:
        suppress = "not_required_by_unresolved_candidate"

    rev = case_revision or (case.raw_answers or {}).get("_semantic_revision") or str(
        getattr(case, "run_id", 0))
    return {
        "question_id": question.get("question_id") or f"Q-{fact}",
        "target_fact": fact,
        "requesting_module_ids": [r["module_id"] for r in requesters],
        "current_module_status": {
            r["module_id"]: r["status"] for r in requesters
        },
        "why_fact_is_missing": (
            "not present or not usable in FactManager"
            if not resolved else "already resolved"
        ),
        "why_answer_can_change_outcome": (
            f"affects modules: {[r['module_id'] for r in viable] or unlocks}"
            if ask else "no material effect"
        ),
        "existing_sources_checked": sources,
        "case_revision": rev,
        "ask": ask,
        "suppress_reason": suppress,
        "text": question.get("text"),
        "source": question.get("source"),
    }


def filter_material(case: CaseFile, kg, questions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split candidates into askable vs suppressed; audit both."""
    keep, suppressed = [], []
    for q in questions or []:
        rec = annotate(case, kg, q)
        q = dict(q)
        q["materiality"] = rec
        if rec["ask"]:
            keep.append(q)
        else:
            suppressed.append(q)
    return keep, suppressed
