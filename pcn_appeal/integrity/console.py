"""P7.5 — structured Case Intelligence Trace for the in-page admin console.

Read-only aggregation over CaseFile + P5.5 execution trace. Does not re-run
reasoning, recalculate PoFA, or select grounds. Values are whatever the
pipeline already recorded.
"""
from __future__ import annotations

from typing import Any, Optional

from .checks import check_case, passed
from .report import REDACTED
from .trace import execution_trace, run_audit, versions

# Narrative atoms (provenance). Never shown as raw customer prose.
NARRATIVE_FACT_NAMES = frozenset({
    "visited_premises", "purpose_of_visit", "left_site", "returned_same_day",
    "possible_vehicle_departure", "returned_to_vehicle",
})

LEGAL_CRITICAL = frozenset({
    "pcn_number", "vrm", "parking_event_date", "notice_issue_date",
    "operator_name", "alleged_breach", "entry_time", "exit_time",
})


def _flat_text(value: Any) -> str:
    """Stringify nested lists/dicts from audit rows for UI join fields."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_flat_text(x) for x in value]
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        return ", ".join(f"{k}={_flat_text(v)}" for k, v in value.items())
    return str(value)


def _fact_name(value: Any) -> Optional[str]:
    """Extract a hashable fact name from support-row / because_of shapes."""
    if value is None or value is False:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("fact", "name", "id", "condition"):
            nested = _fact_name(value.get(key))
            if nested:
                return nested
        return None
    if isinstance(value, (list, tuple)):
        # Prefer first resolvable name; otherwise join for display-only use cases.
        for item in value:
            nested = _fact_name(item)
            if nested:
                return nested
        return None
    return str(value)


# Map integrity/execution stages onto the console pipeline labels the UI shows.
PIPELINE = (
    ("UPLOAD", "DOCUMENT_EXTRACTION"),
    ("CLASSIFICATION", "INTAKE"),
    ("EXTRACTION", "DOCUMENT_EXTRACTION"),
    ("LEGAL_CRITICAL_FACT_GATE", "FACT_EXTRACTION"),
    ("FACT_GRAPH", "FACT_EXTRACTION"),
    ("NARRATIVE_HYPOTHESES", "FACT_EXTRACTION"),
    ("ALLEGATION_PROPOSITIONS", "FACT_EXTRACTION"),
    ("RELATIONSHIPS", "KNOWLEDGE_MATCH"),
    ("LEGAL_CALCULATIONS", "CASE_ANALYSIS"),
    ("VERIFIED_FINDINGS", "CASE_ANALYSIS"),
    ("DOCUMENT_BASELINE", "CASE_ANALYSIS"),
    ("CASE_INTELLIGENCE", "CASE_ANALYSIS"),
    ("GROUND_MERGE", "CLAIM_PLAN"),
    ("CLAIM_PLAN", "CLAIM_PLAN"),
    ("DRAFT_REQUIREMENTS", "RETRIEVAL"),
    ("DRAFT", "DRAFTING"),
    ("VALIDATION", "VALIDATION"),
    ("OUTCOME", "OUTCOME"),
)

_STATUS_MAP = {
    "SUCCESS": "PASS",
    "FAILED": "FAIL",
    "HELD": "BLOCKED",
    "NOT_RUN": "NOT_RUN",
}


def _plain(v: Any) -> Any:
    if hasattr(v, "value"):
        return v.value
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return str(v)


def _fact_row(name: str, fact) -> dict:
    redacted = name in REDACTED
    used_by: list[str] = []
    return {
        "fact_id": fact.fact_id,
        "name": name,
        "value": "(redacted)" if redacted else _plain(fact.value),
        "value_redacted": redacted,
        "status": _plain(fact.status),
        "source": {"kind": _plain(fact.source.kind), "ref": fact.source.ref},
        "confidence": float(getattr(fact, "confidence", 1.0) or 1.0),
        "usable": bool(fact.usable),
        "disputed": bool(getattr(fact, "disputed", False)),
        "legal_critical": name in LEGAL_CRITICAL,
        "conflict": bool(getattr(fact, "disputed", False)),
        "used_by": used_by,
        "provenance": f"{_plain(fact.source.kind)}:{fact.source.ref}",
    }


def _fact_lifecycle(case, facts: list[dict]) -> list[dict]:
    """P8.4: FACT CREATED → USED → UPDATED → SUPERSEDED."""
    from ..fact_lifecycle import lifecycle_trace
    used = {f["name"]: list(f.get("used_by") or []) for f in facts}
    rows = lifecycle_trace(case)
    for row in rows:
        row["used_by"] = used.get(row.get("name")) or row.get("used_by") or []
        if row["used_by"] and row.get("event") == "FACT CREATED":
            row["used"] = True
    return rows


def _fact_write_trace(case) -> list[dict]:
    """P8.7: first write, later writes, IGNORED_DUPLICATE decisions."""
    from ..fact_lifecycle import fact_write_trace
    return fact_write_trace(case)


def _enrich_used_by(facts: list[dict], case, plan) -> None:
    by_name = {f["name"]: f for f in facts}
    # Derived lineage from narrative atoms → multiple_visits
    multi = by_name.get("multiple_visits")
    if multi and multi.get("value") is True:
        for src in ("left_site", "returned_same_day", "visited_premises", "purpose_of_visit"):
            if src in by_name:
                by_name[src]["used_by"].append("multiple_visits")
                multi["used_by"].append(f"derived_from:{src}")
    if plan is not None:
        for item in plan.supported:
            mid = item.module_id
            for row in item.supporting_facts or ():
                row = dict(row) if not isinstance(row, dict) else dict(row)
                fname = _fact_name(row.get("fact") or row.get("condition"))
                if fname and fname in by_name and mid not in by_name[fname]["used_by"]:
                    by_name[fname]["used_by"].append(mid)
                for dep in row.get("because_of") or []:
                    dname = _fact_name(dep)
                    if dname and dname in by_name and mid not in by_name[dname]["used_by"]:
                        by_name[dname]["used_by"].append(mid)


def _narrative(case) -> dict:
    customer_facts = []
    for name in sorted(NARRATIVE_FACT_NAMES):
        f = case.facts.get(name)
        if f is None:
            continue
        customer_facts.append({
            "name": name,
            "value": _plain(f.value),
            "status": _plain(f.status),
            "fact_id": f.fact_id,
        })
    derived = None
    mv = case.facts.get("multiple_visits")
    if mv is not None:
        sources = [n for n in ("left_site", "returned_same_day", "visited_premises")
                   if case.facts.get(n) is not None]
        derived = {
            "name": "multiple_visits",
            "value": _plain(mv.value),
            "status": _plain(mv.status),
            "derived_from": sources,
            "fact_id": mv.fact_id,
        }
    hyps = []
    for h in getattr(case, "fact_hypotheses", []) or []:
        if isinstance(h, dict):
            hyps.append({k: h.get(k) for k in (
                "fact", "value", "status", "confidence", "signals", "hypothesis_id")
                if k in h})
    # Raw narrative is never inlined; only a presence flag + optional redacted peek.
    has_raw = bool(
        getattr(case, "customer_source_texts", None)
        or any(
            a.get("event") in ("narrative_understanding", "material_account")
            for a in case.audit
        )
    )
    return {
        "customer_facts": customer_facts,
        "derived": derived,
        "hypotheses": hyps,
        "raw_available": has_raw,
        "raw_text": None,  # filled only when explicitly requested by the API
    }


def _allegations(case) -> dict:
    raw = case.get("alleged_breach") if hasattr(case, "get") else None
    if raw is None and "alleged_breach" in getattr(case, "facts", {}):
        raw = case.facts["alleged_breach"].value
    text = str(raw or "").strip()
    low = text.lower()
    canonical = "UNKNOWN"
    from ..allegation import has as _alleges
    if _alleges(text, "OVERSTAY"):
        canonical = "OVERSTAY"
    elif any(k in low for k in ("parent", "child", "family")):
        canonical = "PARENT_CHILD_BAY"
    elif _alleges(text, "PERMIT"):
        canonical = "PERMIT"
    elif any(k in low for k in ("payment", "pay and display", "ticket")):
        canonical = "PAYMENT"
    props = []
    if canonical == "OVERSTAY":
        props = [
            {"id": "A-01", "name": "continuous_single_visit", "value": True},
            {"id": "A-02", "name": "duration_exceeds_maximum", "value": True},
        ]
    elif canonical == "PARENT_CHILD_BAY":
        props = [
            {"id": "A-01", "name": "restricted_bay_conditions_apply", "value": True},
            {"id": "A-02", "name": "child_occupant_required", "value": True},
        ]
    # Surface account contradiction / rebuttal propositions when present.
    for name in ("account_contradicts_allegation", "material_account_proposition",
                 "child_occupant_present"):
        f = case.facts.get(name)
        if f is not None and f.usable:
            props.append({
                "id": f"P-{name[:12]}",
                "name": name,
                "value": _plain(f.value),
                "kind": "customer_proposition",
            })
    return {"raw": text or None, "canonical": canonical, "propositions": props}


def _relationships(case, plan) -> list[dict]:
    """Typed relationship list for mobile; no layout graph required."""
    edges: list[dict] = []
    mv = case.facts.get("multiple_visits")
    if mv is not None and mv.value:
        for src in ("left_site", "returned_same_day"):
            if case.facts.get(src):
                edges.append({
                    "from": src, "to": "multiple_visits",
                    "type": "DERIVED_FROM",
                    "from_id": case.facts[src].fact_id,
                    "to_id": mv.fact_id,
                })
        # Contradicts continuous-visit allegation premise when overstay-shaped.
        edges.append({
            "from": "multiple_visits", "to": "continuous_single_visit",
            "type": "CONTRADICTS",
            "from_id": mv.fact_id, "to_id": None,
        })
    audit = run_audit(case)
    km = next((a for a in reversed(audit) if a.get("event") == "knowledge_match"), None)
    if km:
        for row in km.get("selected") or []:
            mid = row.get("module") or row.get("module_id")
            for because in row.get("selected_because") or []:
                edges.append({
                    "from": because, "to": mid, "type": "SUPPORTS",
                    "from_id": None, "to_id": mid,
                })
        for row in km.get("rejected") or []:
            if row.get("status") == "BLOCKED":
                edges.append({
                    "from": row.get("module") or row.get("module_id"),
                    "to": row.get("blocked_by") or "BLOCK",
                    "type": "BLOCKS",
                    "reason": row.get("reason"),
                    "from_id": None, "to_id": None,
                })
    if plan is not None:
        for item in plan.items:
            for rel in item.relationships or ():
                r = dict(rel) if not isinstance(rel, dict) else dict(rel)
                edges.append({
                    "from": r.get("source") or r.get("from") or item.module_id,
                    "to": r.get("target") or r.get("to") or item.module_id,
                    "type": r.get("type") or r.get("relationship") or "RELATED",
                    "from_id": None, "to_id": item.module_id,
                })
    def _edge_key(edge: dict) -> tuple:
        """Make from/to hashable — selected_because / blocked_by can be lists."""
        def _norm(v):
            if isinstance(v, list):
                return tuple(_norm(x) for x in v)
            if isinstance(v, dict):
                return tuple(sorted((k, _norm(val)) for k, val in v.items()))
            return v
        return (_norm(edge.get("from")), _norm(edge.get("to")), edge.get("type"))

    seen: set = set()
    out = []
    for e in edges:
        # Flatten list endpoints so the UI gets readable strings.
        for end in ("from", "to"):
            v = e.get(end)
            if isinstance(v, list):
                e[end] = ", ".join(str(x) for x in v) if v else None
            elif isinstance(v, dict):
                e[end] = str(v)
        key = _edge_key(e)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _legal_findings(case) -> list[dict]:
    rows = []
    for f in getattr(case, "legal_findings", []) or []:
        if not isinstance(f, dict):
            continue
        calc = f.get("calculation_result") or f.get("calculation") or {}
        rows.append({
            "finding_id": f.get("finding_id") or f.get("code") or f.get("family"),
            "family": f.get("finding_type") or f.get("family") or f.get("code"),
            "status": f.get("status"),
            "legal_module_id": f.get("legal_module_id") or f.get("module_id"),
            "particulars": f.get("particulars") or {},
            "calculation": calc if isinstance(calc, dict) else {"raw": calc},
            "reason": f.get("reason") or f.get("detail"),
        })
    return rows


def _knowledge(case) -> dict:
    audit = run_audit(case)
    km = next((a for a in reversed(audit) if a.get("event") == "knowledge_match"), None) or {}
    buckets = {
        "SUPPORTED": [], "RELEVANT": [], "REJECTED": [], "BLOCKED": [], "UNRESOLVED": [],
    }
    # P8.6: join KM buckets to the LOCKED claim plan (not KM alone).
    from ..engines.claim_plan_authority import latest_locked
    plan = latest_locked(case)
    plan_ids = set(plan.supported_ids) if plan is not None else set()

    for row in km.get("selected") or []:
        because = row.get("selected_because") or []
        mid = row.get("module") or row.get("module_id")
        buckets["SUPPORTED"].append({
            "module_id": mid,
            "reason": _flat_text(because) or row.get("reason"),
            "facts_available": because if isinstance(because, list) else [because],
            "missing_facts": row.get("missing") or [],
            "final_decision": "SUPPORTED",
            "in_claim_plan": mid in plan_ids if mid else None,
        })
    for row in km.get("relevant") or []:
        mid = row if isinstance(row, str) else (row.get("module") or row.get("module_id"))
        buckets["RELEVANT"].append({
            "module_id": mid, "reason": "relevant", "final_decision": "RELEVANT",
            "facts_available": [], "missing_facts": [],
            "in_claim_plan": mid in plan_ids if mid else None,
        })
    for row in km.get("rejected") or []:
        status = row.get("status") or "REJECTED"
        bucket = "BLOCKED" if status == "BLOCKED" else "REJECTED"
        mid = row.get("module") or row.get("module_id")
        buckets[bucket].append({
            "module_id": mid,
            "reason": row.get("reason"),
            "blocking_condition": row.get("reason") if status == "BLOCKED" else None,
            "facts_available": row.get("facts") or [],
            "missing_facts": row.get("missing") or [],
            "final_decision": status,
            # May still be in plan via VERIFIED_FINDING / CARRIED_FORWARD.
            "in_claim_plan": mid in plan_ids if mid else False,
        })
    return buckets

def _ground_sets(case, plan) -> dict:
    """Critical merge view: independent / narrative / verified / carried / final."""
    findings = _legal_findings(case)
    verified_ids = {
        (f.get("legal_module_id") or "")
        for f in findings if f.get("status") == "VERIFIED" and f.get("legal_module_id")
    }
    verified_families = [f.get("family") for f in findings if f.get("status") == "VERIFIED"]

    independent, narrative, evidence, verified_finding, carried, invalidated, final = (
        [], [], [], [], [], [], [],
    )
    if plan is not None:
        for item in plan.items:
            entry = {
                "module_id": item.module_id,
                "status": item.status,
                "decision": item.decision,
                "reason": item.reason,
                "label": item.module_id,
            }
            if item.status != "SUPPORTED":
                if item.decision in ("BLOCKED",) or "invalid" in (item.reason or "").lower():
                    invalidated.append(entry)
                continue
            final.append(entry)
            d = item.decision or ""
            if d == "VERIFIED_FINDING":
                verified_finding.append(entry)
                independent.append(entry)
            elif d == "CARRIED_FORWARD":
                carried.append(entry)
            elif item.module_id.startswith("KB-ANPR") or "multiple_visits" in (item.reason or ""):
                narrative.append(entry)
            elif item.module_id.startswith("KB-EV") or item.claim_type == "EVIDENCE":
                evidence.append(entry)
            elif item.module_id.startswith("KB-POFA"):
                independent.append(entry)
                if item.module_id in verified_ids or d == "SELECTED":
                    # selected PoFA may still be notice-independent
                    pass
            else:
                narrative.append(entry)

    # P8.6: integrity only when a VERIFIED-licensed module is absent AND the
    # builder left no reject reason (GATE / NO_SUPPORT / BLOCKED / …). Legitimate
    # gate lapses are recorded on plan items, not as corruption.
    integrity_errors = []
    final_ids = {g["module_id"] for g in final}
    rejected_with_reason = {
        i.module_id: i.decision
        for i in (plan.items if plan is not None else [])
        if i.status != "SUPPORTED" and i.decision
    }
    from .module_decisions import expected_rejection
    rejected_items = {
        i.module_id: i
        for i in (plan.items if plan is not None else [])
        if i.status != "SUPPORTED"
    }
    for mid in verified_ids:
        if mid and mid not in final_ids:
            item = rejected_items.get(mid)
            if item is not None and expected_rejection(item.decision, item.reason):
                continue
            if rejected_with_reason.get(mid) and expected_rejection(
                    rejected_with_reason.get(mid), ""):
                continue
            inv = any(g.get("module_id") == mid for g in invalidated)
            if not inv:
                integrity_errors.append({
                    "code": "GROUND_INTEGRITY_FAILURE",
                    "message": (
                        f"{mid} was licensed by a VERIFIED legal finding but "
                        "disappeared from the final ground set with no builder "
                        "reject reason (GATE/NO_SUPPORT/BLOCKED/…)."
                    ),
                    "ground": mid,
                })

    # Prior plan drop: only flag when the new plan has no reject reason for it.
    plans = list(getattr(case, "claim_plans", []) or [])
    if len(plans) >= 2:
        a, b = plans[-2], plans[-1]
        a_ids = set(a.supported_ids)
        b_ids = set(b.supported_ids)
        b_reject_items = {i.module_id: i for i in b.items}
        b_reject = {
            i.module_id: i.decision
            for i in b.items
            if i.status != "SUPPORTED" and i.decision
        }
        for mid in sorted(a_ids - b_ids):
            item = b_reject_items.get(mid)
            if item is not None and expected_rejection(item.decision, item.reason):
                continue
            if expected_rejection(b_reject.get(mid) or "", ""):
                continue
            if not any(g.get("module_id") == mid for g in invalidated):
                integrity_errors.append({
                    "code": "GROUND_INTEGRITY_FAILURE",
                    "message": (
                        f"{mid} existed in plan v{a.version} but disappeared in "
                        f"v{b.version} without a recorded gate/support/block reason."
                    ),
                    "ground": mid,
                    "from_version": a.version,
                    "to_version": b.version,
                })
    return {
        "independent_notice": independent,
        "narrative": narrative,
        "evidence": evidence,
        "verified_finding": verified_finding,
        "carried_forward": carried,
        "invalidated": invalidated,
        "final_merged": final,
        "verified_families": verified_families,
        "integrity_errors": integrity_errors,
    }


def _ground_sources(case, plan) -> dict:
    """P8.2 trace: verified findings vs CI selection vs the final plan."""
    from ..engines.claim_plan_authority import VERIFIED_FINDING, latest_locked
    plan = plan or latest_locked(case)
    findings = _legal_findings(case)
    verified = [
        {"finding_type": f.get("family") or f.get("finding_id"),
         "module_id": f.get("legal_module_id"), "status": "VERIFIED"}
        for f in findings if f.get("status") == "VERIFIED"
    ]
    selected = []
    if plan is not None:
        selected = list((_thaw_trust(plan) or {}).get("proposals", {}).get("selected") or [])
        if not selected:
            selected = list(getattr(case, "analysis_module_ids", None) or [])
    vf_modules = []
    if plan is not None:
        vf_modules = [i.module_id for i in plan.supported if i.decision == VERIFIED_FINDING]
    omitted = [mid for mid in vf_modules if mid not in set(selected)]
    overrides = [
        {"module_id": mid,
         "reason": "Verified finding authority overrides omission."}
        for mid in omitted
    ]
    return {
        "verified_findings": verified,
        "case_intelligence": {
            "selected": selected,
            "not_selected": omitted,
        },
        "final_claim_plan": list(plan.supported_ids) if plan is not None else [],
        "overrides": overrides,
        "source_trace": plan.source_trace() if plan is not None else ["GROUND SOURCES"],
        "invalidations": list((_thaw_trust(plan) or {}).get("invalidations") or [])
        if plan is not None else [],
    }


def _document_baseline_view(case, plan) -> dict:
    """Trace: DOCUMENT BASELINE → CUSTOMER DELTA → FINAL."""
    from ..document_baseline import baseline_trace, latest_analysis_state, latest_baseline
    base = latest_baseline(case)
    state = latest_analysis_state(case)
    return {
        "created": ["extraction", "calculations", "findings"] if base is not None else [],
        "version": getattr(base, "version", None),
        "digest": getattr(base, "digest", None),
        "legal_findings": list(getattr(base, "legal_findings", None) or []),
        "document_grounds": list(getattr(base, "document_grounds", None) or []),
        "document_finding_types": list(getattr(base, "document_finding_types", None) or []),
        "customer_delta": list(getattr(state, "add_ground_candidates", None) or []),
        "final": list(plan.supported_ids) if plan is not None else list(
            getattr(base, "document_grounds", None) or []),
        "analysis_version": getattr(state, "analysis_version", None),
        "trace": baseline_trace(case, plan),
    }


def _thaw_trust(plan) -> dict:
    t = getattr(plan, "trust", None) or {}
    if hasattr(t, "items"):
        return {k: (dict(v) if hasattr(v, "items") else v) for k, v in t.items()}
    return {}


def _support_bundle(item) -> dict:
    from ..drafting.support_contract import bundle_for_item, requirement_for_item
    bundle = bundle_for_item(item)
    req = requirement_for_item(item)
    supporting = [{"fact": n, "value": bundle.values.get(n),
                   "fact_id": bundle.source_fact_ids[i] if i < len(bundle.source_fact_ids) else n}
                  for i, n in enumerate(bundle.source_fact_names)]
    evidence = [dict(e) if not isinstance(e, dict) else dict(e)
                for e in (item.evidence_refs or ())]
    return {
        "allegation_refs": [item.topic or item.claim_type],
        "supporting_facts": supporting,
        "derived_facts": list(bundle.derived_fact_names),
        "source_fact_ids": list(bundle.source_fact_ids),
        "derived_fact_ids": list(bundle.derived_fact_ids),
        "calculated_facts": [],
        "verified_findings": list(bundle.legal_finding_ids),
        "evidence": evidence,
        "relationship_ids": list(bundle.relationship_ids),
        "complete": bundle.complete(),
        "draft_requirement": {
            "must_express": list(req.required_particulars),
            "must_not_express": list(req.prohibited_content),
            "explanation_goal": list(req.explanation_goal),
            "legal_licence": item.decision,
        },
    }


def _claim_plan_view(plan) -> Optional[dict]:
    if plan is None:
        return None
    items = []
    for item in plan.items:
        bundle = _support_bundle(item)
        items.append({
            "item_id": item.item_id,
            "module_id": item.module_id,
            "claim_type": item.claim_type,
            "status": item.status,
            "decision": item.decision,
            "origin": item.decision,
            "priority": item.priority,
            "reason": item.reason,
            "topic": item.topic,
            "support_bundle": bundle,
            "draft_requirement": bundle["draft_requirement"],
        })
    return {
        "claim_plan_id": plan.claim_plan_id,
        "version": plan.version,
        "status": plan.status,
        "plan_digest": plan.plan_digest,
        "approved": plan.supported_ids,
        "items": items,
        "material_fact_accounting": [
            dict(r) if not isinstance(r, dict) else dict(r)
            for r in (plan.material_fact_accounting or ())
        ],
        "trace": plan.trace(),
        "source_trace": plan.source_trace(),
        "invalidations": list((_thaw_trust(plan) or {}).get("invalidations") or []),
    }


def _draft_context(case, plan, out) -> dict:
    """Structured view of what drafting received — not the hidden prompt."""
    audit = run_audit(case)
    dc = next((a for a in reversed(audit) if a.get("event") == "draft_context"), None)
    verified_facts = {}
    fact_basis = {}
    for name, f in sorted(case.facts.items()):
        if not f.usable or name in REDACTED or name in NARRATIVE_FACT_NAMES:
            continue
        # Free-text strings withheld (same policy as ReasoningEngine)
        if f.source.kind.value == "CUSTOMER_FREE_TEXT" and isinstance(f.value, str):
            continue
        verified_facts[name] = _plain(f.value)
        fact_basis[name] = f.source.kind.value
    # Narrative atoms that are in a support bundle SHOULD be in draft context;
    # flag when they are missing (lossy boundary detector).
    plan_facts: set[str] = set()
    if plan is not None:
        for item in plan.supported:
            for row in item.supporting_facts or ():
                r = dict(row) if not isinstance(row, dict) else dict(row)
                fname = _fact_name(r.get("fact") or r.get("condition"))
                if fname:
                    plan_facts.add(fname)
                for dep in r.get("because_of") or []:
                    dname = _fact_name(dep)
                    if dname:
                        plan_facts.add(dname)
    missing_from_context = sorted(
        n for n in plan_facts
        if n not in verified_facts and n not in REDACTED
        and case.facts.get(n) is not None
    )
    losses = [{
        "code": "DRAFT_CONTEXT_LOSS",
        "fact": n,
        "message": f"FACT EXISTS IN CLAIM PLAN ({n}) BUT MISSING FROM DRAFT CONTEXT",
        "value": _plain(case.facts[n].value) if n not in REDACTED else "(redacted)",
    } for n in missing_from_context]

    findings = [f for f in _legal_findings(case) if f.get("status") == "VERIFIED"]
    driver = _plain(getattr(case, "driver_status", None)) or "UNIDENTIFIED"
    return {
        "approved_grounds": plan.supported_ids if plan else [],
        "verified_facts": verified_facts,
        "fact_basis": fact_basis,
        "verified_findings": findings,
        "evidence_refs": sorted(str(k) for k in (getattr(case, "evidence", None) or {})),
        "driver_status": driver,
        "operator": _plain(case.facts["operator_name"].value) if case.facts.get("operator_name") else None,
        "pcn_number": _plain(case.facts["pcn_number"].value) if case.facts.get("pcn_number") else None,
        "vrm": _plain(case.facts["vrm"].value) if case.facts.get("vrm") else None,
        "context_audit": {
            "context_sha256": (dc or {}).get("context_sha256"),
            "claim_plan_id": (dc or {}).get("claim_plan_id"),
        } if dc else None,
        "lossy_boundaries": losses,
        "draft_ran": out is not None and getattr(out, "draft", None) is not None,
    }


def _draft_view(out) -> Optional[dict]:
    if out is None or getattr(out, "draft", None) is None:
        return None
    draft = out.draft
    paragraphs = []
    try:
        sentences = list(draft.sentences())
    except Exception:
        sentences = []
    if not sentences and getattr(draft, "text", None):
        return {"letter_present": True, "paragraphs": [], "raw_length": len(draft.text or "")}
    for i, s in enumerate(sentences):
        paragraphs.append({
            "index": i,
            "text": s.text,
            "ground": (s.module_refs or [None])[0] if s.module_refs else None,
            "module_refs": list(s.module_refs or []),
            "fact_refs": list(s.fact_refs or []),
            "evidence_refs": list(s.evidence_refs or []),
        })
    return {"letter_present": True, "paragraphs": paragraphs}


def _validation(case, out) -> dict:
    checks = []
    # Prefer live validation on output, else audit
    if out is not None and getattr(out, "validation", None) is not None:
        val = out.validation
        issues = list(getattr(val, "issues", None) or [])
        for iss in issues:
            if isinstance(iss, dict):
                checks.append({
                    "rule": iss.get("rule") or iss.get("code") or "VAL",
                    "status": "FAIL" if iss.get("severity", True) else "WARNING",
                    "reason": iss.get("message") or iss.get("detail") or "",
                })
            else:
                checks.append({
                    "rule": getattr(iss, "rule", None) or "VAL",
                    "status": "FAIL",
                    "reason": str(getattr(iss, "message", iss)),
                })
        if getattr(val, "ok", None) is True or getattr(val, "passed", None) is True:
            if not checks:
                checks.append({"rule": "VALIDATION", "status": "PASS", "reason": "all clear"})
    for a in reversed(run_audit(case)):
        if a.get("event") in ("validation", "draft_validation"):
            for iss in a.get("issues") or []:
                if isinstance(iss, dict):
                    checks.append({
                        "rule": iss.get("rule") or iss.get("code") or "VAL",
                        "status": "FAIL" if iss.get("severity", True) else "WARNING",
                        "reason": iss.get("message") or "",
                    })
            if a.get("passed") and not checks:
                checks.append({"rule": "VALIDATION", "status": "PASS",
                               "reason": "passed (audit)"})
            break
    # Identity / placeholder integrity from case facts
    pcn = case.get("pcn_number") if hasattr(case, "get") else None
    if not pcn:
        checks.append({
            "rule": "VAL-IDENTITY",
            "status": "FAIL",
            "reason": "pcn_number missing — heading would render unresolved placeholder",
        })
    elif str(pcn).strip() in ("[PCN number]", "{{pcn_number}}", "UNKNOWN", ""):
        checks.append({
            "rule": "VAL-PLACEHOLDER",
            "status": "FAIL",
            "reason": f"unresolved identifier placeholder: {pcn}",
        })
    # Expected validator catalogue for UI completeness
    catalogue = [
        "VAL-PLAN", "VAL-GROUND-COVERAGE", "VAL-MATERIAL-FACT-COVERAGE",
        "VAL-PARTICULARS", "VAL-LEGAL-FINDING", "VAL-POFA-AUTHORITY",
        "VAL-DRIVER", "VAL-FACT", "VAL-EVIDENCE", "VAL-CONFLICT",
        "VAL-CRITICAL-DOCUMENT-IDENTITY",
        "VAL-IDENTITY", "VAL-PLACEHOLDER", "VAL-INTEGRITY",
        "VAL-MODULE-TRACE", "VAL-VERIFIED-GROUND-PRESENCE", "VAL-EXPECTED-REJECTION",
        "VAL-FACT-AUTHORITY", "VAL-FACT-STABILITY", "VAL-DERIVED-CONSISTENCY",
    ]
    seen = {c["rule"] for c in checks}
    for rule in catalogue:
        if rule not in seen and not any(rule in (c["rule"] or "") for c in checks):
            checks.append({"rule": rule, "status": "NOT_RUN", "reason": ""})
    return {"checks": checks, "ran": bool(checks and any(c["status"] != "NOT_RUN" for c in checks))}


def _pipeline_fixed(case, trace, grounds, plan, draft_ctx, validation) -> list[dict]:
    by_stage = {s["stage"]: s for s in trace.get("steps") or []}
    findings = _legal_findings(case)
    rows = []
    for label, src in PIPELINE:
        st = by_stage.get(src) or {}
        status = _STATUS_MAP.get(st.get("status") or "NOT_RUN", "NOT_RUN")
        detail = st.get("held") or ("; ".join(st.get("errors") or [])[:160] or None)
        if label == "VERIFIED_FINDINGS":
            if any(f.get("status") == "VERIFIED" for f in findings):
                status = "PASS"
            elif findings:
                status = "UNRESOLVED"
            elif st.get("status") == "NOT_RUN":
                status = "NOT_RUN"
        if label == "DOCUMENT_BASELINE":
            from ..document_baseline import latest_baseline
            base = latest_baseline(case)
            if base is not None:
                status = "PASS"
                detail = detail or f"v{base.version} digest={base.digest[:12]}"
        if label == "GROUND_MERGE":
            if grounds.get("integrity_errors"):
                status, detail = "FAIL", grounds["integrity_errors"][0]["message"][:200]
            elif grounds.get("final_merged"):
                status = "PASS"
        if label == "CLAIM_PLAN" and plan is not None:
            if plan.supported_ids:
                status = "PASS"
            else:
                status = "BLOCKED"
                detail = detail or "no supported grounds in locked plan"
        if label == "DRAFT_REQUIREMENTS" and draft_ctx.get("lossy_boundaries"):
            status = "FAIL"
            detail = f"{len(draft_ctx['lossy_boundaries'])} fact(s) lost before drafting"
        if label == "DRAFT":
            if draft_ctx.get("draft_ran"):
                status = "PASS" if status == "NOT_RUN" else status
        if label == "VALIDATION":
            fails = [c for c in validation.get("checks") or [] if c["status"] == "FAIL"]
            warns = [c for c in validation.get("checks") or [] if c["status"] == "WARNING"]
            if fails:
                status, detail = "FAIL", fails[0].get("reason") or fails[0]["rule"]
            elif validation.get("ran") and not warns:
                status = "PASS"
            elif warns:
                status = "WARNING"
        if label == "LEGAL_CRITICAL_FACT_GATE":
            missing = [n for n in ("pcn_number", "vrm", "parking_event_date")
                       if not (hasattr(case, "get") and case.get(n))]
            if missing:
                status, detail = "FAIL", "missing: " + ", ".join(missing)
            elif status == "NOT_RUN" and by_stage.get("FACT_EXTRACTION", {}).get("status") == "SUCCESS":
                status = "PASS"
        rows.append({
            "stage": label,
            "status": status,
            "duration_ms": st.get("duration_ms"),
            "detail": detail,
        })
    return rows


def _health(pipeline: list[dict], grounds: dict, findings: list, plan, draft_ctx, validation) -> list[dict]:
    def pick(stage: str) -> str:
        for p in pipeline:
            if p["stage"] == stage:
                return p["status"]
        return "NOT_RUN"

    return [
        {"label": "Extraction", "status": pick("EXTRACTION")},
        {"label": "Facts", "status": pick("FACT_GRAPH")},
        {"label": "Narrative", "status": pick("NARRATIVE_HYPOTHESES")},
        {"label": "Legal findings",
         "status": ("VERIFIED" if any(f.get("status") == "VERIFIED" for f in findings)
                    else pick("VERIFIED_FINDINGS"))},
        {"label": "Ground merge", "status": pick("GROUND_MERGE")},
        {"label": "Claim Plan",
         "status": ("BLOCKED" if plan is not None and not plan.supported_ids
                    else pick("CLAIM_PLAN"))},
        {"label": "Draft", "status": pick("DRAFT")},
        {"label": "Validation", "status": pick("VALIDATION")},
    ]


def _why_stopped(case, out, grounds, plan, pipeline, validation) -> Optional[dict]:
    outcome = getattr(out, "outcome", None) if out else None
    if outcome is None:
        for a in reversed(case.audit):
            if a.get("event") == "customer_outcome":
                outcome = a.get("outcome")
                break
    state = _plain(case.state)
    blocking = next((p for p in pipeline if p["status"] in ("FAIL", "BLOCKED")), None)
    reasons = []
    if grounds.get("integrity_errors"):
        for err in grounds["integrity_errors"]:
            reasons.append(err["message"])
    if plan is not None and not plan.supported_ids:
        reasons.append("No supported grounds in the locked Claim Plan.")
        # Candidate counts from knowledge
        km = _knowledge(case)
        reasons.append(
            f"Candidates — supported: {len(km['SUPPORTED'])}, "
            f"rejected: {len(km['REJECTED'])}, blocked: {len(km['BLOCKED'])}, "
            f"relevant: {len(km['RELEVANT'])}."
        )
    if grounds.get("final_merged") and len(grounds["final_merged"]) < (
            len(grounds["independent_notice"]) + len(grounds["narrative"])):
        # Soft signal when sets look inconsistent
        pass
    fails = [c for c in validation.get("checks") or [] if c["status"] == "FAIL"]
    for c in fails[:5]:
        reasons.append(f"{c['rule']}: {c.get('reason') or 'failed'}")
    if not reasons and state in ("RELEASED",):
        return None
    if not reasons and not blocking and outcome not in (
            "NO_SUPPORTED_GROUNDS", "ACCOUNT_UNRESOLVED", "PROCESSING_ERROR", "NEEDS_DOCUMENTS",
            "NEEDS_FACTS"):
        if state == "RELEASED":
            return None
    return {
        "final_state": state,
        "outcome": outcome,
        "blocking_stage": (blocking or {}).get("stage"),
        "blocking_status": (blocking or {}).get("status"),
        "reasons": reasons or [
            (blocking or {}).get("detail") or f"Case stopped in state {state}."
        ],
        "integrity_errors": grounds.get("integrity_errors") or [],
    }


def _events(case, trace: dict) -> list[dict]:
    """Chronological console log from audit + stage summary."""
    rows = []
    for a in run_audit(case):
        event = a.get("event") or "event"
        if event == "ai_call":
            summary = f"{a.get('task')} {a.get('status') or ''} model={a.get('model') or ''}".strip()
        elif event == "fact_set":
            summary = f"{a.get('fact') or a.get('name')}={a.get('value')}"
        elif event in ("narrative_understanding", "hypothesis_created"):
            summary = str({k: a.get(k) for k in ("facts", "fact", "value", "signals")
                           if a.get(k) is not None})[:160]
        elif event == "legal_findings":
            summary = str(a.get("findings") or a.get("verified") or a.get("status") or "")[:160]
        elif event == "claim_plan_locked":
            summary = f"v{a.get('version')} approved={a.get('approved') or a.get('module_ids')}"
        elif event in ("validation", "draft_validation"):
            summary = f"passed={a.get('passed')} issues={len(a.get('issues') or [])}"
        else:
            summary = (a.get("reason") or a.get("outcome") or a.get("message") or "")[:160]
        rows.append({
            "at": a.get("at"),
            "event": event.upper(),
            "summary": summary,
            "run_id": a.get("run_id"),
        })
    return rows[-200:]  # cap for mobile


def _identity_issues(case, draft_ctx) -> list[dict]:
    issues = []
    pcn = draft_ctx.get("pcn_number")
    if pcn in (None, "", "[PCN number]", "{{pcn_number}}"):
        issues.append({
            "code": "UNRESOLVED_PLACEHOLDER",
            "field": "pcn_number",
            "message": "PCN number missing or placeholder — release must block",
            "extracted": _plain(case.get("pcn_number")) if hasattr(case, "get") else None,
        })
    return issues


def _synthetic_label(case) -> Optional[str]:
    cid = (case.case_id or "").upper()
    if cid.startswith("REG_") or cid.startswith("SYN_") or cid.startswith("TEST_"):
        return "TEST CASE / SYNTHETIC DATA"
    for a in case.audit:
        if a.get("synthetic") or a.get("golden"):
            return "TEST CASE / SYNTHETIC DATA"
    return None


def build_console(case, out=None, kg=None, *, include_raw_narrative: bool = False) -> dict:
    """Assemble the full Case Intelligence Trace payload."""
    from ..engines.claim_plan_authority import latest_locked

    plan = latest_locked(case)
    trace = execution_trace(case)
    ver = versions(case)
    findings = _legal_findings(case)
    facts = [_fact_row(n, f) for n, f in sorted(case.facts.items())]
    _enrich_used_by(facts, case, plan)
    narrative = _narrative(case)
    if include_raw_narrative:
        texts = list(getattr(case, "customer_source_texts", None) or [])
        # Redact aggressively — show length + first/last 12 chars only
        redacted = []
        for t in texts:
            s = str(t or "")
            if len(s) <= 24:
                redacted.append("[redacted short text]")
            else:
                redacted.append(f"{s[:12]}…[{len(s)} chars]…{s[-12:]}")
        narrative["raw_text"] = redacted
    allegations = _allegations(case)
    relationships = _relationships(case, plan)
    knowledge = _knowledge(case)
    grounds = _ground_sets(case, plan)
    from .module_decisions import build_module_journey
    module_view = build_module_journey(
        case, plan, getattr(out, "draft", None) if out else None,
        integrity_errors=grounds.get("integrity_errors") or [])
    # Mark knowledge in_claim_plan
    approved = set(plan.supported_ids) if plan else set()
    for bucket in knowledge.values():
        for row in bucket:
            mid = row.get("module_id")
            if mid is not None:
                row["in_claim_plan"] = mid in approved
    claim_plan = _claim_plan_view(plan)
    draft_ctx = _draft_context(case, plan, out)
    draft = _draft_view(out)
    validation = _validation(case, out)
    pipeline = _pipeline_fixed(case, trace, grounds, plan, draft_ctx, validation)
    health = _health(pipeline, grounds, findings, plan, draft_ctx, validation)
    why = _why_stopped(case, out, grounds, plan, pipeline, validation)
    checks = check_case(case, out, kg)
    identity = _identity_issues(case, draft_ctx)

    # Extraction slice (legal-critical + high-confidence document facts)
    extraction = [f for f in facts if f["legal_critical"]
                  or f["source"]["kind"] == "DOCUMENT"]

    outcome = getattr(out, "outcome", None) if out else None
    if outcome is None:
        for a in reversed(case.audit):
            if a.get("event") == "customer_outcome":
                outcome = a.get("outcome")
                break

    return {
        "schema": "case_console.v1",
        "summary": {
            "case_id": case.case_id,
            "execution_id": trace.get("execution_id"),
            "case_revision": case.run_id,
            "final_outcome": outcome or _plain(case.state),
            "final_state": _plain(case.state),
            "app_version": ver.get("build_id") or ver.get("code"),
            "git_commit": ver.get("code"),
            "kb_release": ver.get("kb_release") or ver.get("kb"),
            "prompt_versions": ver.get("prompts") or {},
            "models": ver.get("models") or {},
            "provider": ver.get("provider"),
            "claim_plan_version": ver.get("claim_plan_version"),
            "synthetic_label": _synthetic_label(case),
        },
        "health": health,
        "pipeline": pipeline,
        "extraction": extraction,
        "facts": facts,
        "fact_lifecycle": _fact_lifecycle(case, facts),
        "fact_write_trace": _fact_write_trace(case),
        "narrative": narrative,
        "allegations": allegations,
        "relationships": relationships,
        "legal_findings": findings,
        "knowledge": knowledge,
        "module_journey": module_view["journey"],
        "module_decisions": module_view["decisions"],
        "module_trace_checks": module_view["checks"],
        "grounds": grounds,
        "ground_sources": _ground_sources(case, plan),
        "document_baseline": _document_baseline_view(case, plan),
        "claim_plan": claim_plan,
        "draft_context": draft_ctx,
        "draft": draft,
        "validation": validation,
        "why_stopped": why,
        "events": _events(case, trace),
        "identity_issues": identity,
        "integrity": {"passed": passed(checks),
                      "checks": [{"check": c.get("check"), "status": c.get("status"),
                                  "detail": c.get("detail") or c.get("message")}
                                 for c in checks]},
        "plan_versions": [
            {"version": p.version, "status": p.status, "approved": p.supported_ids,
             "run_number": p.run_number}
            for p in getattr(case, "claim_plans", []) or []
        ],
    }


def compare_runs(case, run_a: Optional[int] = None, run_b: Optional[int] = None) -> dict:
    """Compare two claim-plan / fact snapshots for the same case (A/B narrative)."""
    plans = list(getattr(case, "claim_plans", []) or [])
    if len(plans) < 1:
        return {"error": "no claim plans to compare", "a": None, "b": None}

    def pick(run_id: Optional[int], fallback_index: int):
        if run_id is not None:
            for p in plans:
                if p.run_number == run_id or p.version == run_id:
                    return p
        return plans[fallback_index]

    if run_a is None and run_b is None:
        if len(plans) >= 2:
            a, b = plans[-2], plans[-1]
        else:
            a = b = plans[-1]
    else:
        a = pick(run_a, 0)
        b = pick(run_b, -1)

    def ground_ids(p):
        return list(p.supported_ids)

    a_ids, b_ids = set(ground_ids(a)), set(ground_ids(b))
    added = sorted(b_ids - a_ids)
    removed = sorted(a_ids - b_ids)
    a_mods = {i.module_id for i in a.items}
    b_mods = {i.module_id for i in b.items}
    from .module_decisions import expected_rejection
    b_items = {i.module_id: i for i in b.items}
    b_inv = {
        str(i.get("ground_id")): i
        for i in (_thaw_trust(b).get("invalidations") or [])
        if isinstance(i, dict) and i.get("ground_id")
    }
    integrity_errors = []
    removals = []
    for mid in removed + sorted(a_mods - b_mods):
        if any(r.get("module_id") == mid for r in removals):
            continue
        item = b_items.get(mid)
        inv = b_inv.get(mid)
        if inv:
            removals.append({
                "module_id": mid, "kind": "invalidation",
                "reason": inv.get("reason") or "invalidated",
                "explain": inv.get("reason") or "invalidated",
            })
        elif item is not None and expected_rejection(item.decision, item.reason):
            removals.append({
                "module_id": mid, "kind": "requirements_unavailable",
                "reason": item.reason or item.decision,
                "explain": "not selected because requirements unavailable",
            })
        elif mid in removed:
            removals.append({
                "module_id": mid, "kind": "unexplained",
                "reason": "",
                "explain": "removed with no invalidation or expected-rejection reason",
            })
            integrity_errors.append({
                "code": "GROUND_INTEGRITY_FAILURE",
                "message": (
                    f"Ground {mid} present in run A (plan v{a.version}) but missing in "
                    f"run B (plan v{b.version}) after further customer information, "
                    "with no recorded gate/support/block reason."
                ),
                "ground": mid,
            })

    def _facts_of(p):
        used = (_thaw_trust(p) or {}).get("facts_used") or {}
        if isinstance(used, dict):
            return {str(k): (v.get("value") if isinstance(v, dict) else v)
                    for k, v in used.items()}
        return {}

    fa, fb = _facts_of(a), _facts_of(b)
    facts_added = [{"name": n, "value": fb[n]} for n in sorted(set(fb) - set(fa))]
    facts_removed = [{"name": n, "value": fa[n]} for n in sorted(set(fa) - set(fb))]
    facts_changed = [{"name": n, "from": fa[n], "to": fb[n]}
                     for n in sorted(set(fa) & set(fb)) if fa[n] != fb[n]]
    if not fa and not fb and a.run_number != b.run_number:
        for n in ("left_site", "returned_same_day", "purpose_of_visit",
                  "visited_premises", "multiple_visits", "forgotten_item"):
            f = case.facts.get(n)
            if f is not None and f.usable:
                facts_added.append({"name": n, "value": _plain(f.value)})

    def _findings_of(p):
        trust = _thaw_trust(p) or {}
        rows = trust.get("verified_finding_types") or trust.get("findings") or []
        if isinstance(rows, list) and rows:
            return [str(x.get("family") if isinstance(x, dict) else x) for x in rows]
        return []

    findings = _legal_findings(case)
    live = [f.get("family") or f.get("finding_id") for f in findings
            if f.get("status") == "VERIFIED"]
    find_a = _findings_of(a) or live
    find_b = _findings_of(b) or live

    return {
        "a": {"label": f"Run A — plan v{a.version}", "version": a.version,
              "run_number": a.run_number, "grounds": ground_ids(a),
              "modules": sorted(a_mods), "findings": find_a},
        "b": {"label": f"Run B — plan v{b.version}", "version": b.version,
              "run_number": b.run_number, "grounds": ground_ids(b),
              "modules": sorted(b_mods), "findings": find_b},
        "modules": {"added": sorted(b_mods - a_mods), "removed": sorted(a_mods - b_mods),
                    "unchanged": sorted(a_mods & b_mods)},
        "grounds": {"added": added, "removed": removed, "unchanged": sorted(a_ids & b_ids)},
        "facts": {"added": facts_added, "removed": facts_removed, "changed": facts_changed},
        "findings": {"added": sorted(set(find_b) - set(find_a)),
                     "removed": sorted(set(find_a) - set(find_b)),
                     "unchanged": sorted(set(find_a) & set(find_b))},
        "legal_findings": {"a": find_a, "b": find_b},
        "removals": removals,
        "integrity_errors": integrity_errors,
        "explained": all(r.get("kind") != "unexplained" for r in removals),
    }


def copy_report(console: dict) -> str:
    """PII-safe pasteable diagnostic summary."""
    s = console.get("summary") or {}
    lines = [
        f"Case: {s.get('case_id')}",
        f"Execution: {s.get('execution_id')}",
        f"Revision: {s.get('case_revision')}",
        f"Commit: {s.get('git_commit')}",
        f"KB: {s.get('kb_release')}",
        f"Claim plan: v{s.get('claim_plan_version')}",
        f"Outcome: {s.get('final_outcome')} / {s.get('final_state')}",
        "",
        "Health:",
    ]
    for h in console.get("health") or []:
        lines.append(f"  {h.get('label')}: {h.get('status')}")
    lines.append("")
    lines.append("Pipeline:")
    for p in console.get("pipeline") or []:
        lines.append(f"  {p.get('stage')}: {p.get('status')}"
                     + (f" — {p.get('detail')}" if p.get("detail") else ""))
    db = console.get("document_baseline") or {}
    lines += ["", "DOCUMENT BASELINE"]
    for t in db.get("document_finding_types") or db.get("document_grounds") or []:
        lines.append(f"  ✓ {t}")
    lines.append("CUSTOMER DELTA")
    delta = db.get("customer_delta") or []
    if delta:
        lines.extend(f"  + {m}" for m in delta)
    else:
        lines.append("  (none)")
    lines.append("FINAL")
    for mid in db.get("final") or []:
        lines.append(f"  ✓ {mid}")
    lines += ["", "MODULE JOURNEY"]
    for row in console.get("module_journey") or []:
        km = (row.get("knowledge") or {}).get("decision") or "—"
        ci = (row.get("case_intelligence") or {}).get("decision") or "—"
        cp = (row.get("claim_plan") or {}).get("decision") or "—"
        dr = (row.get("draft") or {}).get("decision") or "—"
        lines.append(f"  {row.get('module_id')}")
        lines.append(f"    Knowledge Matcher: {km}")
        req = row.get("required_facts") or []
        if req:
            lines.append(f"    Required facts: {', '.join(req)}")
        miss = row.get("missing_facts") or []
        if miss:
            lines.append(f"    Missing: {', '.join(miss)}")
        lines.append(f"    Case Intelligence: {ci}")
        lines.append(f"    Claim Plan: {cp}")
        lines.append(f"    Draft: {dr}")
        reason = ((row.get("claim_plan") or {}).get("reason")
                  or (row.get("case_intelligence") or {}).get("reason")
                  or (row.get("applicability") or {}).get("reason"))
        if reason:
            lines.append(f"    Reason: {reason}")
        lines.append(f"    Integrity: {row.get('integrity') or 'PASS'}")
    gs = console.get("ground_sources") or {}
    lines += ["", "GROUND SOURCES", "Verified findings:"]
    for x in gs.get("verified_findings") or []:
        lines.append(f"  ✓ {x.get('finding_type') or x.get('module_id')}")
    ci = gs.get("case_intelligence") or {}
    lines.append("Case Intelligence:")
    if ci.get("not_selected"):
        for mid in ci["not_selected"]:
            lines.append(f"  not selected {mid}")
    elif ci.get("selected"):
        lines.append("  selected " + ", ".join(ci["selected"]))
    else:
        lines.append("  none")
    lines.append("Final Claim Plan:")
    for mid in gs.get("final_claim_plan") or []:
        lines.append(f"  ✓ {mid}")
    for ov in gs.get("overrides") or []:
        lines.append(f"Reason: {ov.get('reason')}")
    g = console.get("grounds") or {}
    lines += ["", "Grounds independent:"]
    for x in g.get("independent_notice") or []:
        lines.append(f"  ✓ {x.get('module_id')}")
    lines.append("Grounds narrative:")
    for x in g.get("narrative") or []:
        lines.append(f"  ✓ {x.get('module_id')}")
    lines.append("Grounds final:")
    for x in g.get("final_merged") or []:
        lines.append(f"  ✓ {x.get('module_id')}")
    if g.get("integrity_errors"):
        lines.append("")
        lines.append("INTEGRITY ERRORS:")
        for e in g["integrity_errors"]:
            lines.append(f"  ! {e.get('message')}")
    why = console.get("why_stopped")
    if why:
        lines += ["", "Why stopped:", f"  stage={why.get('blocking_stage')}"]
        for r in why.get("reasons") or []:
            lines.append(f"  - {r}")
    # Facts (non-redacted names/values already scrubbed)
    lines += ["", "PARTICULARISATION:"]
    for item in (console.get("claim_plan") or {}).get("items") or []:
        if item.get("status") != "SUPPORTED":
            continue
        b = item.get("support_bundle") or {}
        req = item.get("draft_requirement") or {}
        lines.append(f"  GROUND {item.get('module_id')}")
        src = [f.get("fact") for f in (b.get("supporting_facts") or []) if f.get("fact")]
        lines.append(f"    SOURCE FACTS: {', '.join(src) or '—'}")
        lines.append(f"    DERIVED FACTS: {', '.join(b.get('derived_facts') or []) or '—'}")
        lines.append(f"    SUPPORT BUNDLE: {'complete' if b.get('complete') else 'incomplete'}")
        lines.append(f"    DRAFT REQUIREMENTS: {', '.join(req.get('must_express') or []) or '—'}")
        linked = [p.get("text") for p in ((console.get("draft") or {}).get("paragraphs") or [])
                  if item.get("module_id") in (p.get("module_refs") or [])]
        lines.append(f"    DRAFT SENTENCES: {len(linked)}")
    lines += ["", "FACT WRITE TRACE:"]
    for row in console.get("fact_write_trace") or []:
        first = row.get("first_write") or {}
        lines.append(f"  FACT: {row.get('fact')}={row.get('value')}")
        lines.append(f"    First write: {first.get('authority') or first.get('source') or '—'}")
        for w in row.get("writes") or []:
            lines.append(
                f"    Later: {w.get('authority') or w.get('source')} "
                f"→ {w.get('decision')} ({w.get('reason') or ''})")
        if row.get("held_authority"):
            lines.append(f"    Held authority: {row['held_authority']}")
    lines += ["", "FACT LIFECYCLE:"]
    for row in console.get("fact_lifecycle") or []:
        extra = f" used by {', '.join(row.get('used_by') or [])}" if row.get("used_by") else ""
        lines.append(f"  {row.get('event')}: {row.get('name')}={row.get('value')}{extra}")
    lines += ["", "Key facts:"]
    for f in console.get("extraction") or []:
        lines.append(f"  {f.get('name')}={f.get('value')} ({f.get('status')})")
    lines += ["", "Findings:"]
    for f in console.get("legal_findings") or []:
        lines.append(f"  {f.get('family')}: {f.get('status')}")
    losses = (console.get("draft_context") or {}).get("lossy_boundaries") or []
    if losses:
        lines += ["", "Draft-context losses:"]
        for L in losses:
            lines.append(f"  ! {L.get('message')}")
    lines.append("")
    lines.append("(Raw customer narrative omitted. Keeper name/address redacted.)")
    return "\n".join(lines)
