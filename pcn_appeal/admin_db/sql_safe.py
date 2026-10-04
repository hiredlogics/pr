"""Predefined read-only diagnostics + guarded SELECT-only runner."""
from __future__ import annotations

import re
from typing import Any, Optional

from .connection import read_only_connect, timed_fetch
from .masking import mask_row

PREDEFINED: dict[str, str] = {
    "table_counts": """
        SELECT n.nspname AS schema, c.relname AS table_name,
               c.reltuples::bigint AS est_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog','information_schema')
        ORDER BY c.reltuples DESC NULLS LAST
        LIMIT 100
    """,
    "latest_cases": """
        SELECT case_id, state, kb_release_id, created_at, commit_sha
        FROM cases
        ORDER BY created_at DESC NULLS LAST
        LIMIT 50
    """,
    "latest_facts": """
        SELECT fact_id, case_id, name, status, created_at
        FROM facts
        ORDER BY created_at DESC NULLS LAST
        LIMIT 50
    """,
    "latest_legal_findings": """
        SELECT * FROM legal_findings
        ORDER BY created_at DESC NULLS LAST
        LIMIT 50
    """,
    "latest_claim_plans": """
        SELECT claim_plan_id, case_id, status, plan_digest, locked_at, created_at
        FROM claim_plans
        ORDER BY created_at DESC NULLS LAST
        LIMIT 50
    """,
    "latest_drafts": """
        SELECT * FROM draft_versions
        ORDER BY created_at DESC NULLS LAST
        LIMIT 50
    """,
    "latest_validation_failures": """
        SELECT v.* FROM validations v
        WHERE v.passed IS DISTINCT FROM true
        ORDER BY 1 DESC
        LIMIT 50
    """,
    "vector_column_discovery": """
        SELECT n.nspname AS schema, c.relname AS table_name, a.attname AS column,
               format_type(a.atttypid, a.atttypmod) AS type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND t.typname = 'vector'
          AND n.nspname NOT IN ('pg_catalog','information_schema')
        ORDER BY 1,2,3
    """,
    "embedding_counts": """
        SELECT kind, count(*) AS n
        FROM kb_embeddings
        GROUP BY kind
        ORDER BY n DESC
    """,
}

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"COPY|CALL|DO|EXECUTE|VACUUM|REINDEX|CLUSTER|COMMENT|SECURITY)\b",
    re.I,
)


def list_predefined() -> list[dict[str, str]]:
    return [{"id": k, "description": k.replace("_", " ")} for k in PREDEFINED]


def run_predefined(query_id: str, *, show_sensitive: bool = False) -> dict[str, Any]:
    sql = PREDEFINED.get(query_id)
    if not sql:
        raise LookupError(f"unknown predefined query: {query_id}")
    return _run_select(sql, show_sensitive=show_sensitive, label=query_id)


def run_select_only(
    sql: str,
    *,
    show_sensitive: bool = False,
    row_limit: int = 100,
) -> dict[str, Any]:
    text = (sql or "").strip().rstrip(";")
    if not text:
        raise ValueError("empty SQL")
    if ";" in text:
        raise ValueError("single statement only")
    if not re.match(r"(?is)^\s*SELECT\b", text):
        raise ValueError("SELECT-only")
    if _FORBIDDEN.search(text):
        raise ValueError("forbidden keyword in SQL")
    # Force limit
    limited = f"SELECT * FROM ({text}) AS _q LIMIT {max(1, min(int(row_limit), 200))}"
    return _run_select(limited, show_sensitive=show_sensitive, label="adhoc_select")


def _run_select(sql: str, *, show_sensitive: bool, label: str) -> dict[str, Any]:
    with read_only_connect(statement_timeout_ms=10000) as conn:
        try:
            rows, cols, elapsed = timed_fetch(conn, sql)
        except Exception as exc:
            # Table may not exist for some predefined queries
            return {
                "id": label,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "rows": [],
                "columns": [],
                "query_ms": 0,
            }
        return {
            "id": label,
            "ok": True,
            "columns": cols,
            "rows": [mask_row(cols, r, show_sensitive=show_sensitive) for r in rows],
            "n": len(rows),
            "query_ms": round(elapsed, 2),
            "read_only": True,
        }
