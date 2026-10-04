"""Joined live case view from PostgreSQL (no LLM reconstruction)."""
from __future__ import annotations

import json
from typing import Any, Optional

from .connection import read_only_connect, timed_fetch
from .masking import mask_row


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s", (name,)
    ).fetchone())


def _cols(conn, table: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,),
    ).fetchall()]


def _select_rows(conn, table: str, where_sql: str, params: tuple,
                 *, show_sensitive: bool, limit: int = 500) -> list[dict]:
    cols = _cols(conn, table)
    if not cols:
        return []
    select = ", ".join(f'"{c}"' for c in cols)
    rows, colnames, _ = timed_fetch(
        conn,
        f'SELECT {select} FROM "{table}" WHERE {where_sql} LIMIT %s',
        params + (limit,),
    )
    return [mask_row(colnames, r, show_sensitive=show_sensitive) for r in rows]


def case_overview(*, show_sensitive: bool = False, limit: int = 50) -> dict[str, Any]:
    with read_only_connect() as conn:
        if not _table_exists(conn, "cases"):
            return {"cases": [], "error": "cases table not found"}
        cols = _cols(conn, "cases")
        order = "created_at DESC NULLS LAST" if "created_at" in cols else "case_id DESC"
        select = ", ".join(f'"{c}"' for c in cols)
        rows, colnames, elapsed = timed_fetch(
            conn, f'SELECT {select} FROM cases ORDER BY {order} LIMIT %s', (limit,))
        return {
            "cases": [mask_row(colnames, r, show_sensitive=show_sensitive) for r in rows],
            "query_ms": round(elapsed, 2),
        }


def case_detail(case_id: str, *, show_sensitive: bool = False) -> dict[str, Any]:
    with read_only_connect() as conn:
        if not _table_exists(conn, "cases"):
            raise LookupError("cases table not found")
        case_rows = _select_rows(conn, "cases", "case_id::text = %s", (case_id,),
                                 show_sensitive=show_sensitive, limit=1)
        if not case_rows:
            raise LookupError(f"unknown case {case_id}")
        case = case_rows[0]

        facts = _select_rows(conn, "facts", "case_id::text = %s", (case_id,),
                             show_sensitive=show_sensitive) if _table_exists(conn, "facts") else []
        # Partition facts by status / source kind when columns exist
        notice_facts, customer_facts, derived_facts = [], [], []
        for f in facts:
            status = str(f.get("status") or f.get("engine_status") or "").upper()
            src = str(f.get("source_kind") or f.get("source_type") or "").upper()
            if status in ("DERIVED",) or src in ("CALCULATION", "SYSTEM_DERIVED"):
                derived_facts.append(f)
            elif src in ("CUSTOMER_ANSWER", "CUSTOMER_FREE_TEXT", "ANSWER") or status in (
                "ANSWERED", "CONFIRMED",
            ):
                customer_facts.append(f)
            else:
                notice_facts.append(f)

        sources = (_select_rows(conn, "fact_sources", "case_id::text = %s", (case_id,),
                                show_sensitive=show_sensitive)
                   if _table_exists(conn, "fact_sources") else [])
        # fact_sources may only have fact_id — join via facts if needed
        if not sources and facts and _table_exists(conn, "fact_sources"):
            fact_ids = [f.get("fact_id") for f in facts if f.get("fact_id")]
            if fact_ids:
                sources = _select_rows(
                    conn, "fact_sources",
                    "fact_id::text = ANY(%s)", (fact_ids,),
                    show_sensitive=show_sensitive,
                )

        history = (_select_rows(conn, "fact_history", "case_id::text = %s", (case_id,),
                                show_sensitive=show_sensitive)
                   if _table_exists(conn, "fact_history") else [])
        conflicts = (_select_rows(conn, "fact_conflicts", "case_id::text = %s", (case_id,),
                                  show_sensitive=show_sensitive)
                     if _table_exists(conn, "fact_conflicts") else [])
        findings = (_select_rows(conn, "legal_findings", "case_id::text = %s", (case_id,),
                                 show_sensitive=show_sensitive)
                    if _table_exists(conn, "legal_findings") else [])
        plans = (_select_rows(conn, "claim_plans", "case_id::text = %s", (case_id,),
                              show_sensitive=show_sensitive)
                 if _table_exists(conn, "claim_plans") else [])
        plan_items = []
        if plans and _table_exists(conn, "claim_plan_items"):
            pids = [p.get("claim_plan_id") for p in plans if p.get("claim_plan_id")]
            if pids:
                plan_items = _select_rows(
                    conn, "claim_plan_items",
                    "claim_plan_id::text = ANY(%s)", (pids,),
                    show_sensitive=show_sensitive,
                )
        drafts = []
        if _table_exists(conn, "draft_versions"):
            drafts = _select_rows(conn, "draft_versions", "case_id::text = %s", (case_id,),
                                  show_sensitive=show_sensitive)
        elif _table_exists(conn, "drafts"):
            drafts = _select_rows(conn, "drafts", "case_id::text = %s", (case_id,),
                                  show_sensitive=show_sensitive)
        validations = []
        if _table_exists(conn, "validations") and drafts:
            dids = [d.get("draft_id") or d.get("id") for d in drafts
                    if d.get("draft_id") or d.get("id")]
            if dids:
                validations = _select_rows(
                    conn, "validations", "draft_id::text = ANY(%s)", (dids,),
                    show_sensitive=show_sensitive,
                )
        master = (_select_rows(conn, "master_case_state", "case_id::text = %s", (case_id,),
                               show_sensitive=show_sensitive)
                  if _table_exists(conn, "master_case_state") else [])
        baselines = (_select_rows(conn, "document_baselines", "case_id::text = %s", (case_id,),
                                  show_sensitive=show_sensitive)
                     if _table_exists(conn, "document_baselines") else [])
        traces = []
        for tname in ("case_execution_trace", "ai_execution_logs", "audit_log"):
            if _table_exists(conn, tname):
                # audit_log may not have case_id
                cols = _cols(conn, tname)
                if "case_id" in cols:
                    traces.extend(_select_rows(
                        conn, tname, "case_id::text = %s", (case_id,),
                        show_sensitive=show_sensitive, limit=100,
                    ))

        # Knowledge matches / ground decisions from claim plan items + plan JSON
        knowledge_matches = []
        ground_decisions = []
        for item in plan_items:
            ground_decisions.append({
                "module_id": item.get("module_id") or item.get("ground_id"),
                "origin": item.get("origin") or item.get("selection_origin"),
                "role": item.get("module_role") or item.get("role"),
                "included": item.get("included", True),
                "raw": item,
            })
        for plan in plans:
            for key in ("support_bundle", "draft_requirement", "plan", "payload", "manifest"):
                blob = plan.get(key)
                if isinstance(blob, str):
                    try:
                        blob = json.loads(blob)
                    except Exception:
                        continue
                if isinstance(blob, dict):
                    if key == "support_bundle" or "source_fact_ids" in blob:
                        plan["_parsed_support_bundle"] = blob
                    if key == "draft_requirement" or "required_particulars" in blob:
                        plan["_parsed_draft_requirement"] = blob

        # Derived-from relationships when present on facts
        lineage = []
        for f in facts:
            derived = f.get("derived_from") or f.get("depends_on") or f.get("parents")
            if derived:
                lineage.append({
                    "fact_id": f.get("fact_id"),
                    "name": f.get("fact_name") or f.get("name"),
                    "value": f.get("fact_value") if "fact_value" in f else f.get("value"),
                    "derived_from": derived,
                })
            elif str(f.get("source_type") or "").upper() in ("CALCULATION", "SYSTEM_DERIVED"):
                lineage.append({
                    "fact_id": f.get("fact_id"),
                    "name": f.get("fact_name"),
                    "value": f.get("fact_value"),
                    "derived_from": f.get("source_ref"),
                })

        # DraftPlan is not a table — surface from audit / draft grounding.
        draft_plan = None
        for t in traces:
            detail = t.get("detail") if isinstance(t.get("detail"), dict) else t
            if isinstance(detail, dict) and detail.get("event") in (
                "draft_plan", "draft_coverage_trace",
            ):
                draft_plan = detail
                break

        payload = {
            "case_id": case_id,
            "case": case,
            "notice_facts": notice_facts,
            "customer_facts": customer_facts,
            "derived_facts": derived_facts,
            "all_facts": facts,
            "fact_sources": sources,
            "fact_lineage": lineage,
            "fact_history": history[:200],
            "fact_conflicts": conflicts,
            "legal_findings": findings,
            "knowledge_matches": knowledge_matches,
            "ground_decisions": ground_decisions,
            "claim_plans": plans,
            "claim_plan_items": plan_items,
            "draft_plan": draft_plan,
            "drafts": drafts,
            "validations": validations,
            "master_case_state": master,
            "document_baselines": baselines,
            "traces": traces[:100],
            "final_outcome": {
                "state": case.get("state"),
                "kb_release_id": case.get("kb_release_id"),
                "commit_sha": case.get("commit_sha"),
                "release_metadata": case.get("release_metadata"),
            },
            "pipeline": [
                "CASE", "DOCUMENT BASELINE", "NOTICE FACTS", "CUSTOMER FACTS",
                "FACT SOURCES", "DERIVED FACTS", "FACT LINEAGE", "LEGAL FINDINGS",
                "KNOWLEDGE MATCHES", "GROUND DECISIONS", "CLAIM PLAN",
                "DRAFT PLAN", "DRAFT VERSION", "VALIDATION", "FINAL OUTCOME",
            ],
        }
        from pcn_appeal.release_trace import (
            assert_released_invariants,
            build_release_trace,
            classify_case_row,
            CASE_FIELD_CLASS,
            CASE_FIELD_NULL_NOTES,
        )
        payload["release_trace"] = build_release_trace(payload)
        payload["release_invariants"] = assert_released_invariants(payload)
        payload["legacy_class"] = classify_case_row(case)
        payload["case_field_class"] = CASE_FIELD_CLASS
        payload["case_field_null_notes"] = CASE_FIELD_NULL_NOTES
        return payload
