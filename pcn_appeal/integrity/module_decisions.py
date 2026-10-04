"""P8.6: joined Knowledge Matcher → CI → Claim Plan → Draft authority trace.

Diagnostic only. Does not select grounds, change gates, or rewrite the plan.
Every module that appears in matching, analysis or the locked plan gets a
lifecycle of ModuleDecision rows and one joined journey.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .trace import run_audit

KNOWLEDGE_MATCH = "KNOWLEDGE_MATCH"
APPLICABILITY = "APPLICABILITY"
CASE_INTELLIGENCE = "CASE_INTELLIGENCE"
CLAIM_PLAN = "CLAIM_PLAN"
DRAFT_AUTHORITY = "DRAFT_AUTHORITY"
STAGES = (KNOWLEDGE_MATCH, APPLICABILITY, CASE_INTELLIGENCE, CLAIM_PLAN, DRAFT_AUTHORITY)

MATCHED, SUPPORTED, SELECTED, REJECTED, BLOCKED, UNRESOLVED, INVALIDATED = (
    "MATCHED", "SUPPORTED", "SELECTED", "REJECTED", "BLOCKED", "UNRESOLVED", "INVALIDATED")
USED = "USED"
DECISIONS = (MATCHED, SUPPORTED, SELECTED, REJECTED, BLOCKED, UNRESOLVED, INVALIDATED, USED)

# Claim-plan / CI decisions that are expected, not corruption.
EXPECTED_REJECTION = frozenset({
    "GATE", "NO_SUPPORTING_FACTS", "NO_VERIFIED_FINDING", "BLOCKED",
    "NOT_ACTIVE", "EVIDENCE_REQUIRED", "MISSING_FACTS", "NOT_SELECTED",
})

_MISSING_TOKENS = (
    "missing", "not established", "not yet met", "could apply",
    "required fact", "use_when",
)
_BLOCK_TOKENS = ("blocked", "blocks")
_GATE_TOKENS = ("gate does not", "gate no longer", "does not hold", "not an in-force")


@dataclass
class ModuleDecision:
    module_id: str
    case_id: str
    stage: str
    decision: str
    reason: str = ""
    supporting_fact_ids: tuple = ()
    missing_fact_ids: tuple = ()
    blocking_conditions: tuple = ()
    timestamp: str = ""
    created_by: str = ""

    def as_dict(self) -> dict:
        return {
            "module_id": self.module_id, "case_id": self.case_id,
            "stage": self.stage, "decision": self.decision, "reason": self.reason,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "missing_fact_ids": list(self.missing_fact_ids),
            "blocking_conditions": list(self.blocking_conditions),
            "timestamp": self.timestamp, "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleDecision":
        return cls(
            d.get("module_id") or "", d.get("case_id") or "",
            d.get("stage") or "", d.get("decision") or "",
            d.get("reason") or "",
            tuple(d.get("supporting_fact_ids") or ()),
            tuple(d.get("missing_fact_ids") or ()),
            tuple(d.get("blocking_conditions") or ()),
            d.get("timestamp") or "", d.get("created_by") or "",
        )


def expected_rejection(decision: str = "", reason: str = "") -> bool:
    """True when a module is out because facts, a block or a gate said so."""
    if decision in EXPECTED_REJECTION:
        return True
    low = (reason or "").lower()
    if any(tok in low for tok in _BLOCK_TOKENS):
        return True
    if any(tok in low for tok in _GATE_TOKENS):
        return True
    if any(tok in low for tok in _MISSING_TOKENS):
        return True
    return False


def _names(value: Any) -> list[str]:
    if value is None or value is False:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        for key in ("fact", "name", "module", "module_id", "condition"):
            if value.get(key):
                return _names(value.get(key))
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_names(item))
        return out
    return [str(value)]


def _mid(row: Any) -> str:
    if isinstance(row, str):
        return row
    if isinstance(row, dict):
        return str(row.get("module") or row.get("module_id") or row.get("ground_id") or "")
    return ""


def collect_decisions(case, plan=None, draft=None) -> list[ModuleDecision]:
    """Project the joined lifecycle from audit + locked plan + draft.

    Deterministic: timestamps come from the stored events, not the clock, so
    a reloaded case produces the same history.
    """
    from ..engines.claim_plan_authority import latest_locked, VERIFIED_FINDING
    audit = run_audit(case)
    plan = plan if plan is not None else latest_locked(case)
    cid = getattr(case, "case_id", "") or ""
    rows: list[ModuleDecision] = []

    km = next((a for a in reversed(audit) if a.get("event") == "knowledge_match"), None) or {}
    km_at = str(km.get("at") or "")
    for row in km.get("selected") or []:
        mid = _mid(row)
        if not mid:
            continue
        facts = _names(row.get("facts") or row.get("selected_because"))
        rows.append(ModuleDecision(
            mid, cid, KNOWLEDGE_MATCH, MATCHED,
            reason=_flat(row.get("selected_because") or row.get("reason"))
            or "relationship available",
            supporting_fact_ids=tuple(facts),
            missing_fact_ids=tuple(_names(row.get("missing"))),
            timestamp=km_at, created_by="knowledge_matcher"))
        rows.append(ModuleDecision(
            mid, cid, APPLICABILITY, SUPPORTED,
            reason=row.get("reason") or "use_when holds",
            supporting_fact_ids=tuple(facts),
            timestamp=km_at, created_by="knowledge_matcher"))
    for row in km.get("relevant") or []:
        mid = _mid(row)
        if not mid:
            continue
        rec = row if isinstance(row, dict) else {}
        rows.append(ModuleDecision(
            mid, cid, KNOWLEDGE_MATCH, MATCHED,
            reason=rec.get("reason") or "relevant",
            missing_fact_ids=tuple(_names(rec.get("missing"))),
            timestamp=km_at, created_by="knowledge_matcher"))
        rows.append(ModuleDecision(
            mid, cid, APPLICABILITY, UNRESOLVED,
            reason=rec.get("reason") or "gate not yet met",
            missing_fact_ids=tuple(_names(rec.get("missing"))),
            timestamp=km_at, created_by="knowledge_matcher"))
    for row in km.get("rejected") or []:
        mid = _mid(row)
        if not mid:
            continue
        status = row.get("status") or REJECTED
        decision = BLOCKED if status == BLOCKED else REJECTED
        missing = tuple(_names(row.get("missing")))
        blocking = tuple(_names(row.get("blocked_by") or (
            [row.get("reason")] if decision == BLOCKED else [])))
        rows.append(ModuleDecision(
            mid, cid, KNOWLEDGE_MATCH, MATCHED,
            reason="module considered", timestamp=km_at,
            created_by="knowledge_matcher"))
        rows.append(ModuleDecision(
            mid, cid, APPLICABILITY, decision,
            reason=row.get("reason") or status,
            missing_fact_ids=missing, blocking_conditions=blocking,
            timestamp=km_at, created_by="knowledge_matcher"))

    if not km:
        rows.extend(_from_master_matches(case, cid))

    ci = next((a for a in reversed(audit) if a.get("event") == "case_analysis"), None) or {}
    ci_at = str(ci.get("at") or "")
    selected: set[str] = set()
    for x in (ci.get("kept") or []) + (ci.get("add_ground_candidates") or []) \
            + (ci.get("support_existing_ground") or []):
        mid = _mid(x)
        if mid:
            selected.add(mid)
    proposed = [_mid(x) for x in (ci.get("proposed") or []) if _mid(x)]
    not_supported = {_mid(x): x for x in (ci.get("not_supported") or []) if _mid(x)}
    invalidations = {_mid(x): x for x in (ci.get("proposed_invalidations") or []) if _mid(x)}
    ci_seen = set(selected) | set(proposed) | set(not_supported) | set(invalidations)
    for mid in sorted(ci_seen):
        if mid in selected:
            rows.append(ModuleDecision(
                mid, cid, CASE_INTELLIGENCE, SELECTED,
                reason="selected by Case Intelligence",
                timestamp=ci_at, created_by="case_intelligence"))
        elif mid in invalidations:
            inv = invalidations[mid]
            rows.append(ModuleDecision(
                mid, cid, CASE_INTELLIGENCE, INVALIDATED,
                reason=(inv.get("reason") if isinstance(inv, dict) else "") or "invalidated",
                timestamp=ci_at, created_by="case_intelligence"))
        else:
            ns = not_supported.get(mid)
            reason = ""
            missing = ()
            if isinstance(ns, dict):
                reason = ns.get("reason") or ns.get("why") or ""
                missing = tuple(_names(ns.get("missing")))
            rows.append(ModuleDecision(
                mid, cid, CASE_INTELLIGENCE, REJECTED,
                reason=reason or "not selected",
                missing_fact_ids=missing,
                timestamp=ci_at, created_by="case_intelligence"))

    if plan is not None:
        at = getattr(plan, "locked_at", None) or getattr(plan, "confirmed_at", None) or ""
        trust = dict(getattr(plan, "trust", None) or {})
        invs = {str(i.get("ground_id")): i for i in (trust.get("invalidations") or [])
                if isinstance(i, dict) and i.get("ground_id")}
        for item in plan.items:
            mid = item.module_id
            if item.status == "SUPPORTED":
                decision = SUPPORTED
            elif item.decision == "BLOCKED" or mid in invs:
                decision = INVALIDATED if mid in invs else BLOCKED
            elif item.status == "UNRESOLVED":
                decision = UNRESOLVED
            else:
                decision = REJECTED
            facts = []
            for row in item.supporting_facts or ():
                r = dict(row) if not isinstance(row, dict) else dict(row)
                facts.extend(_names(r.get("fact") or r.get("condition")))
            rows.append(ModuleDecision(
                mid, cid, CLAIM_PLAN, decision,
                reason=item.reason or item.decision or item.status,
                supporting_fact_ids=tuple(facts),
                timestamp=str(at), created_by="claim_plan_authority"))
        _fill_missing_stages(rows, plan, cid, at)

    used: set[str] = set()
    if draft is not None:
        try:
            sentences = list(draft.sentences())
        except Exception:
            sentences = []
        for s in sentences:
            used.update(s.module_refs or [])
    elif draft is None:
        try:
            from ..drafting import versions as dv
            latest = (getattr(case, "draft_versions", None) or [None])[-1]
            if latest:
                for para in latest.get("content") or []:
                    for s in (para if isinstance(para, list) else [para]):
                        if isinstance(s, dict):
                            used.update(s.get("module_refs") or [])
        except Exception:
            pass
    if plan is not None:
        for mid in plan.supported_ids:
            rows.append(ModuleDecision(
                mid, cid, DRAFT_AUTHORITY, USED if mid in used else REJECTED,
                reason="used in draft" if mid in used else "approved but not cited",
                timestamp=str(getattr(plan, "locked_at", "") or ""),
                created_by="draft"))
        for mid in used:
            if mid in ("STRUCTURAL",) or (plan is not None and mid in plan.supported_ids):
                continue
            rows.append(ModuleDecision(
                mid, cid, DRAFT_AUTHORITY, REJECTED,
                reason="cited but not in locked plan",
                created_by="draft"))

    return rows


def join_journey(decisions: Iterable[ModuleDecision], plan=None) -> list[dict]:
    """One row per module: KM → applicability → CI → claim plan → draft."""
    by_mod: dict[str, list[ModuleDecision]] = {}
    for d in decisions:
        by_mod.setdefault(d.module_id, []).append(d)
    out = []
    for mid in sorted(by_mod):
        stages = {d.stage: d for d in by_mod[mid]}
        km = stages.get(KNOWLEDGE_MATCH)
        app = stages.get(APPLICABILITY)
        ci = stages.get(CASE_INTELLIGENCE)
        cp = stages.get(CLAIM_PLAN)
        dr = stages.get(DRAFT_AUTHORITY)
        reject_decision = (cp.decision if cp else "") or (ci.decision if ci else "") \
            or (app.decision if app else "")
        reject_reason = (cp.reason if cp else "") or (ci.reason if ci else "") \
            or (app.reason if app else "")
        rejected = (
            (cp and cp.decision in (REJECTED, BLOCKED, UNRESOLVED, INVALIDATED))
            or (ci and ci.decision in (REJECTED, BLOCKED, INVALIDATED)
                and (cp is None or cp.decision != SUPPORTED))
            or (app and app.decision in (REJECTED, BLOCKED, UNRESOLVED)
                and (cp is None or cp.decision != SUPPORTED)
                and (ci is None or ci.decision != SELECTED))
        )
        expected = bool(rejected and expected_rejection(reject_decision, reject_reason))
        item = None
        if plan is not None:
            item = next((i for i in plan.items if i.module_id == mid), None)
        origin = getattr(item, "decision", None) if item is not None else None
        required = list((km or app or cp).supporting_fact_ids if (km or app or cp) else [])
        missing = []
        for src in (app, km, ci, cp):
            if src and src.missing_fact_ids:
                missing = list(src.missing_fact_ids)
                break
        out.append({
            "module_id": mid,
            "knowledge": _stage_view(km, default="—"),
            "applicability": _stage_view(app, default="—"),
            "case_intelligence": _stage_view(ci, default="—"),
            "claim_plan": _stage_view(cp, default="—", extra={
                "status": getattr(item, "status", None),
                "origin": origin,
            }),
            "draft": _stage_view(dr, default="—"),
            "required_facts": required,
            "missing_facts": missing,
            "expected_rejection": expected,
            "integrity": "PASS",
            "history": [d.as_dict() for d in by_mod[mid]],
        })
    return out


def _stage_view(d: Optional[ModuleDecision], default: str = "—", extra: Optional[dict] = None) -> dict:
    row = {
        "decision": d.decision if d else default,
        "reason": d.reason if d else "",
        "supporting_fact_ids": list(d.supporting_fact_ids) if d else [],
        "missing_fact_ids": list(d.missing_fact_ids) if d else [],
        "blocking_conditions": list(d.blocking_conditions) if d else [],
    }
    if extra:
        row.update(extra)
    return row


def _flat(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(_flat(x) for x in value if _flat(x))
    if isinstance(value, dict):
        return value.get("condition") or value.get("reason") or _flat(value.get("fact"))
    return str(value)


def module_trace_issues(journey: list[dict], plan=None, findings: Iterable[dict] = (),
                        integrity_errors: Iterable[dict] = ()) -> list[dict]:
    """VAL-MODULE-TRACE / VAL-VERIFIED-GROUND-PRESENCE / VAL-EXPECTED-REJECTION."""
    issues = []
    supported = set(getattr(plan, "supported_ids", None) or [])
    by_mid = {row["module_id"]: row for row in journey}
    for mid in supported:
        row = by_mid.get(mid)
        item = next((i for i in (getattr(plan, "items", None) or [])
                     if i.module_id == mid), None)
        origin = getattr(item, "decision", "") if item is not None else ""
        reason = getattr(item, "reason", "") if item is not None else ""
        km = (row or {}).get("knowledge") or {}
        has_km = km.get("decision") in (MATCHED, SUPPORTED)
        has_vf = origin == "VERIFIED_FINDING"
        has_history = bool((row or {}).get("history"))
        if not (has_km or has_vf):
            issues.append({
                "rule": "VAL-MODULE-TRACE", "status": "FAIL",
                "module_id": mid,
                "message": f"{mid} is a final ground with no knowledge source "
                           "or verified finding source",
            })
        elif not has_history or not reason:
            issues.append({
                "rule": "VAL-MODULE-TRACE", "status": "FAIL",
                "module_id": mid,
                "message": f"{mid} has no decision history or support reason",
            })
        else:
            issues.append({
                "rule": "VAL-MODULE-TRACE", "status": "PASS", "module_id": mid,
            })

    verified_modules = {
        str(f.get("legal_module_id"))
        for f in (findings or [])
        if f.get("status") == "VERIFIED" and f.get("legal_module_id")
    }
    trust = dict(getattr(plan, "trust", None) or {}) if plan is not None else {}
    invalidated = {str(i.get("ground_id")) for i in (trust.get("invalidations") or [])
                   if isinstance(i, dict)}
    for mid in sorted(verified_modules):
        item = next((i for i in (getattr(plan, "items", None) or [])
                     if i.module_id == mid), None)
        if mid in supported:
            issues.append({"rule": "VAL-VERIFIED-GROUND-PRESENCE", "status": "PASS",
                           "module_id": mid})
            continue
        decision = getattr(item, "decision", "") if item is not None else ""
        reason = getattr(item, "reason", "") if item is not None else ""
        if mid in invalidated or expected_rejection(decision, reason):
            issues.append({"rule": "VAL-VERIFIED-GROUND-PRESENCE", "status": "PASS",
                           "module_id": mid, "detail": "invalidated or expected rejection"})
            continue
        issues.append({
            "rule": "VAL-VERIFIED-GROUND-PRESENCE", "status": "FAIL",
            "module_id": mid,
            "message": f"verified finding licenses {mid} but the claim plan "
                       "has no corresponding ground and no invalidation",
        })

    failed_grounds = {
        str(e.get("ground") or e.get("module_id") or "")
        for e in (integrity_errors or [])
        if e.get("code") == "GROUND_INTEGRITY_FAILURE"
    }
    for row in journey:
        if not row.get("expected_rejection"):
            continue
        mid = row["module_id"]
        if mid in failed_grounds:
            issues.append({
                "rule": "VAL-EXPECTED-REJECTION", "status": "FAIL",
                "module_id": mid,
                "message": f"{mid} was rejected for missing facts / a block / a "
                           "gate, but the console reported GROUND_INTEGRITY_FAILURE",
            })
        else:
            issues.append({
                "rule": "VAL-EXPECTED-REJECTION", "status": "PASS",
                "module_id": mid,
                "message": (row.get("claim_plan") or {}).get("reason")
                or (row.get("applicability") or {}).get("reason")
                or "expected rejection",
            })
    return issues


def _from_master_matches(case, cid: str) -> list[ModuleDecision]:
    rows = []
    try:
        matches = list(case.master.knowledge_matches)
    except Exception:
        matches = []
    for m in matches:
        mid = getattr(m, "module_id", "") or ""
        if not mid:
            continue
        rel = getattr(m, "relationship", "") or ""
        at = getattr(m, "at", "") or ""
        missing = tuple(_names(getattr(m, "missing", None)))
        support = tuple(_names(getattr(m, "supporting_conditions", None)))
        blocking = tuple(_names(getattr(m, "blocked_by", None)))
        rows.append(ModuleDecision(
            mid, cid, KNOWLEDGE_MATCH, MATCHED,
            reason=getattr(m, "reason", "") or rel, supporting_fact_ids=support,
            missing_fact_ids=missing, timestamp=at, created_by="knowledge_matcher"))
        if rel == "SUPPORTED":
            app = SUPPORTED
        elif rel == "BLOCKED":
            app = BLOCKED
        elif rel == "REJECTED":
            app = REJECTED
        else:
            app = UNRESOLVED
        rows.append(ModuleDecision(
            mid, cid, APPLICABILITY, app,
            reason=getattr(m, "reason", "") or rel, supporting_fact_ids=support,
            missing_fact_ids=missing, blocking_conditions=blocking,
            timestamp=at, created_by="knowledge_matcher"))
    return rows


def _fill_missing_stages(rows: list[ModuleDecision], plan, cid: str, at: str) -> None:
    """Complete the join from the locked plan when an upstream audit row is absent."""
    have = {(d.module_id, d.stage) for d in rows}
    for item in plan.items:
        mid = item.module_id
        facts = []
        for row in item.supporting_facts or ():
            r = dict(row) if not isinstance(row, dict) else dict(row)
            facts.extend(_names(r.get("fact") or r.get("condition")))
        facts_t = tuple(facts)
        if (mid, KNOWLEDGE_MATCH) not in have and item.status == "SUPPORTED" \
                and item.decision != "VERIFIED_FINDING":
            rows.append(ModuleDecision(
                mid, cid, KNOWLEDGE_MATCH, MATCHED,
                reason=item.reason or "knowledge source on claim plan",
                supporting_fact_ids=facts_t, timestamp=str(at),
                created_by="claim_plan_authority"))
            have.add((mid, KNOWLEDGE_MATCH))
        if (mid, CASE_INTELLIGENCE) not in have:
            if item.decision == "SELECTED" and item.status == "SUPPORTED":
                rows.append(ModuleDecision(
                    mid, cid, CASE_INTELLIGENCE, SELECTED,
                    reason="selected by Case Intelligence",
                    supporting_fact_ids=facts_t, timestamp=str(at),
                    created_by="case_intelligence"))
            elif item.status != "SUPPORTED":
                rows.append(ModuleDecision(
                    mid, cid, CASE_INTELLIGENCE, REJECTED,
                    reason=item.reason or item.decision or "not selected",
                    timestamp=str(at), created_by="case_intelligence"))
            elif item.decision == "VERIFIED_FINDING":
                rows.append(ModuleDecision(
                    mid, cid, CASE_INTELLIGENCE, UNRESOLVED,
                    reason="verified finding authority; CI selection optional",
                    timestamp=str(at), created_by="case_intelligence"))
            have.add((mid, CASE_INTELLIGENCE))


def build_module_journey(case, plan=None, draft=None,
                         integrity_errors: Iterable[dict] = (),
                         persist: bool = True) -> dict:
    """Console/check payload: decisions, joined journey, VAL-* results."""
    decisions = collect_decisions(case, plan, draft)
    if persist:
        persist_decisions(case, decisions)
    journey = join_journey(decisions, plan)
    findings = [f for f in (getattr(case, "legal_findings", None) or [])
                if isinstance(f, dict)]
    checks = module_trace_issues(journey, plan, findings, integrity_errors)
    fail_by = {}
    for iss in checks:
        if iss.get("status") == "FAIL" and iss.get("module_id"):
            fail_by.setdefault(iss["module_id"], []).append(iss)
    for row in journey:
        fails = fail_by.get(row["module_id"]) or []
        if fails:
            row["integrity"] = "FAIL"
            row["integrity_message"] = fails[0].get("message") or fails[0]["rule"]
        else:
            row["integrity"] = "PASS"
    return {
        "decisions": [d.as_dict() for d in decisions],
        "journey": journey,
        "checks": checks,
    }


def persist_decisions(case, decisions: list[ModuleDecision]) -> None:
    """Stash on the case and as one audit event so reload can replay."""
    case.module_decisions = [d.as_dict() for d in decisions]
    existing = [a for a in case.audit if a.get("event") == "module_journey"]
    payload = [d.as_dict() for d in decisions]
    if existing and existing[-1].get("decisions") == payload:
        return
    case.audit.append({"event": "module_journey", "decisions": payload})


__all__ = [
    "ModuleDecision", "STAGES", "DECISIONS", "EXPECTED_REJECTION",
    "collect_decisions", "join_journey", "module_trace_issues",
    "expected_rejection", "persist_decisions", "build_module_journey",
    "KNOWLEDGE_MATCH", "APPLICABILITY", "CASE_INTELLIGENCE", "CLAIM_PLAN",
    "DRAFT_AUTHORITY", "MATCHED", "SUPPORTED", "SELECTED", "REJECTED",
    "BLOCKED", "UNRESOLVED", "INVALIDATED", "USED",
]
