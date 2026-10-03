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
                fname = row.get("fact") or row.get("condition")
                if fname in by_name and mid not in by_name[fname]["used_by"]:
                    by_name[fname]["used_by"].append(mid)
                for dep in row.get("because_of") or []:
                    dname = dep.get("fact") if isinstance(dep, dict) else dep
                    if dname in by_name and mid not in by_name[dname]["used_by"]:
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
    if any(k in low for k in ("overstay", "maximum stay", "time limit", "exceeded")):
        canonical = "OVERSTAY"
    elif any(k in low for k in ("parent", "child", "family")):
        canonical = "PARENT_CHILD_BAY"
    elif any(k in low for k in ("permit", "authoris", "authoriz")):
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
    # Deduplicate
    seen = set()
    out = []
    for e in edges:
        key = (e.get("from"), e.get("to"), e.get("type"))
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
            "family": f.get("family") or f.get("code"),
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
    for row in km.get("selected") or []:
        buckets["SUPPORTED"].append({
            "module_id": row.get("module") or row.get("module_id"),
            "reason": ", ".join(row.get("selected_because") or []) or row.get("reason"),
            "facts_available": row.get("selected_because") or [],
            "missing_facts": row.get("missing") or [],
            "final_decision": "SUPPORTED",
            "in_claim_plan": None,
        })
    for row in km.get("relevant") or []:
        mid = row if isinstance(row, str) else (row.get("module") or row.get("module_id"))
        buckets["RELEVANT"].append({
            "module_id": mid, "reason": "relevant", "final_decision": "RELEVANT",
            "facts_available": [], "missing_facts": [], "in_claim_plan": None,
        })
    for row in km.get("rejected") or []:
        status = row.get("status") or "REJECTED"
        bucket = "BLOCKED" if status == "BLOCKED" else "REJECTED"
        buckets[bucket].append({
            "module_id": row.get("module") or row.get("module_id"),
            "reason": row.get("reason"),
            "blocking_condition": row.get("reason") if status == "BLOCKED" else None,
            "facts_available": row.get("facts") or [],
            "missing_facts": row.get("missing") or [],
            "final_decision": status,
            "in_claim_plan": False,
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

    # Integrity: VERIFIED finding module missing from final without invalidation.
    integrity_errors = []
    final_ids = {g["module_id"] for g in final}
    for mid in verified_ids:
        if mid and mid not in final_ids:
            inv = any(g.get("module_id") == mid for g in invalidated)
            if not inv:
                integrity_errors.append({
                    "code": "GROUND_INTEGRITY_FAILURE",
                    "message": (
                        f"{mid} was licensed by a VERIFIED legal finding but "
                        "disappeared from the final ground set with no INVALIDATES record."
                    ),
                    "ground": mid,
                })

    # Flag when a previous locked plan had a ground the current final lacks.
    plans = list(getattr(case, "claim_plans", []) or [])
    if len(plans) >= 2:
        a, b = plans[-2], plans[-1]
        a_ids = set(a.supported_ids)
        b_ids = set(b.supported_ids)
        for mid in sorted(a_ids - b_ids):
            if not any(g.get("module_id") == mid for g in invalidated):
                integrity_errors.append({
                    "code": "GROUND_INTEGRITY_FAILURE",
                    "message": (
                        f"{mid} existed in plan v{a.version} but disappeared in "
                        f"v{b.version}. No INVALIDATES relationship exists."
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


def _support_bundle(item) -> dict:
    facts, derived, calc, findings, evidence, rels, allegation = (
        [], [], [], [], [], [], [],
    )
    for row in item.supporting_facts or ():
        r = dict(row) if not isinstance(row, dict) else dict(row)
        name = r.get("fact") or r.get("condition")
        entry = {
            "condition": r.get("condition"),
            "fact": name,
            "value": r.get("value"),
            "fact_id": r.get("fact_id"),
            "source": r.get("source"),
        }
        if r.get("because_of"):
            derived_names = [
                (d.get("fact") if isinstance(d, dict) else d) for d in r["because_of"]
            ]
            derived.extend([n for n in derived_names if n])
            entry["because_of"] = derived_names
        facts.append(entry)
    for e in item.evidence_refs or ():
        evidence.append(dict(e) if not isinstance(e, dict) else dict(e))
    for rel in item.relationships or ():
        rels.append(dict(rel) if not isinstance(rel, dict) else dict(rel))
    # Heuristic split for derived vs supporting
    derived_set = set(derived) | {f["fact"] for f in facts
                                  if f.get("fact") in ("multiple_visits",)}
    supporting = [f for f in facts if f.get("fact") not in derived_set]
    must_express = []
    for f in supporting:
        if f.get("fact"):
            must_express.append(f["fact"])
    for d in sorted(derived_set):
        must_express.append(d)
    if item.decision == "VERIFIED_FINDING":
        must_express.extend(["event_date", "issue_date", "statutory_deadline",
                             "deemed_delivery", "days_outside"])
    if item.module_id.startswith("KB-ANPR"):
        for p in ("purpose_of_visit", "left_site", "returned_same_day",
                  "why_anpr_does_not_prove_continuous_stay"):
            if p not in must_express:
                must_express.append(p)
    return {
        "allegation_refs": allegation or [item.topic or item.claim_type],
        "supporting_facts": supporting,
        "derived_facts": sorted(derived_set),
        "calculated_facts": calc,
        "verified_findings": findings,
        "evidence": evidence,
        "relationship_ids": [r.get("id") or r.get("type") for r in rels],
        "draft_requirement": {
            "must_express": must_express,
            "must_not_express": ["driver_identity_unless_formally_identified"],
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
    plan_facts = set()
    if plan is not None:
        for item in plan.supported:
            for row in item.supporting_facts or ():
                r = dict(row) if not isinstance(row, dict) else dict(row)
                if r.get("fact"):
                    plan_facts.add(r["fact"])
                for dep in r.get("because_of") or []:
                    dname = dep.get("fact") if isinstance(dep, dict) else dep
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
        "evidence_refs": [
            e.evidence_id for e in getattr(case, "evidence", []) or []
            if getattr(e, "uploaded", True)
        ],
        "driver_status": driver,
        "operator": _plain(case.get("operator_name")) if hasattr(case, "get") else None,
        "pcn_number": _plain(case.get("pcn_number")) if hasattr(case, "get") else None,
        "vrm": _plain(case.get("vrm")) if hasattr(case, "get") else None,
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
        "VAL-IDENTITY", "VAL-PLACEHOLDER", "VAL-INTEGRITY",
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
            "NO_SUPPORTED_GROUNDS", "PROCESSING_ERROR", "NEEDS_DOCUMENTS", "NEEDS_FACTS"):
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
        "narrative": narrative,
        "allegations": allegations,
        "relationships": relationships,
        "legal_findings": findings,
        "knowledge": knowledge,
        "grounds": grounds,
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
    integrity_errors = []
    for mid in removed:
        integrity_errors.append({
            "code": "GROUND_INTEGRITY_FAILURE",
            "message": (
                f"Ground {mid} present in run A (plan v{a.version}) but missing in "
                f"run B (plan v{b.version}) after further customer information."
            ),
            "ground": mid,
        })

    facts_added, facts_removed = [], []
    if a.run_number != b.run_number:
        # Approximate: narrative / account facts present on the live case
        for n in ("left_site", "returned_same_day", "purpose_of_visit",
                  "visited_premises", "multiple_visits", "forgotten_item"):
            f = case.facts.get(n)
            if f is not None and f.usable:
                facts_added.append({"name": n, "value": _plain(f.value)})

    findings = _legal_findings(case)
    finding_ids = [f.get("family") or f.get("finding_id") for f in findings
                   if f.get("status") == "VERIFIED"]

    return {
        "a": {"label": f"Run A — plan v{a.version}", "version": a.version,
              "run_number": a.run_number, "grounds": ground_ids(a),
              "findings": finding_ids},
        "b": {"label": f"Run B — plan v{b.version}", "version": b.version,
              "run_number": b.run_number, "grounds": ground_ids(b),
              "findings": finding_ids},
        "grounds": {"added": added, "removed": removed, "unchanged": sorted(a_ids & b_ids)},
        "facts": {"added": facts_added, "removed": facts_removed},
        "legal_findings": {"a": finding_ids, "b": finding_ids},
        "integrity_errors": integrity_errors,
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
