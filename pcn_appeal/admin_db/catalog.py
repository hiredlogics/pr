"""Discover schemas, tables, columns, keys, indexes, and vector columns."""
from __future__ import annotations

from typing import Any, Optional

from .connection import qident, read_only_connect, safe_db_identity, timed_fetch


IMPORTANT_HINTS = (
    "cases", "facts", "fact_sources", "fact_history", "fact_versions",
    "fact_conflicts", "master_case_state", "document_baselines",
    "legal_findings", "claim_plans", "claim_plan_items", "draft_versions",
    "drafts", "validations", "kb_modules", "kb_embeddings", "kb_releases",
    "knowledge_modules", "case_execution_trace", "ai_execution_logs",
    "audit_log", "evidence", "graph_nodes", "graph_edges",
)


def status() -> dict[str, Any]:
    info = safe_db_identity()
    if not info.get("database_name") and not __import__("os").getenv("DATABASE_URL"):
        return {**info, "connected": False, "error": "DATABASE_URL not set"}
    try:
        with read_only_connect() as conn:
            ver = conn.execute("SHOW server_version").fetchone()[0]
            info["postgres_version"] = ver
            info["connected"] = True
            ext = conn.execute(
                "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            info["pgvector"] = (
                {"installed": True, "extname": ext[0], "extversion": ext[1]}
                if ext else {"installed": False, "message": "PGVECTOR NOT CONFIGURED"}
            )
    except Exception as exc:
        info["connected"] = False
        info["error"] = type(exc).__name__
        info["pgvector"] = {"installed": False, "message": "PGVECTOR NOT CONFIGURED"}
    return info


def list_tables(*, include_counts: bool = True, exact_counts: bool = False) -> dict[str, Any]:
    """List user tables. Row counts default to pg_class.reltuples (fast estimate).

    Set exact_counts=True only when a precise COUNT(*) is required — that is
    slow over remote Postgres and must not gate the overview UI.
    """
    with read_only_connect() as conn:
        rows, _, elapsed = timed_fetch(conn, """
            SELECT n.nspname AS schema, c.relname AS table_name,
                   c.relkind, GREATEST(c.reltuples, 0)::bigint AS est_rows
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_toast%%'
            ORDER BY n.nspname, c.relname
        """)
        # Primary keys in one query (avoid N+1)
        pk_rows, _, _ = timed_fetch(conn, """
            SELECT n.nspname, c.relname, a.attname
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indisprimary
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY n.nspname, c.relname, a.attnum
        """)
        pks: dict[tuple[str, str], list[str]] = {}
        for schema, name, col in pk_rows:
            pks.setdefault((schema, name), []).append(col)

        tables = []
        for schema, name, kind, est_rows in rows:
            row_count = int(est_rows) if include_counts else None
            count_kind = "estimated" if include_counts else None
            if include_counts and exact_counts and name in IMPORTANT_HINTS:
                try:
                    row_count = conn.execute(
                        f"SELECT count(*) FROM {qident(schema)}.{qident(name)}"
                    ).fetchone()[0]
                    count_kind = "exact"
                except Exception:
                    pass
            tables.append({
                "schema": schema,
                "table": name,
                "relkind": kind,
                "row_count": row_count,
                "row_count_kind": count_kind,
                "primary_key": pks.get((schema, name), []),
                "columns": [],
                "foreign_keys": [],
                "indexes": [],
                "important": name in IMPORTANT_HINTS,
            })
        return {
            "tables": tables,
            "query_ms": round(elapsed, 2),
            "n": len(tables),
            "counts": "exact_important" if exact_counts else ("estimated" if include_counts else "none"),
        }


def table_metadata(conn, schema: str, table: str) -> dict[str, Any]:
    cols, _, _ = timed_fetch(conn, """
        SELECT column_name, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (schema, table))
    pk, _, _ = timed_fetch(conn, """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE i.indisprimary AND n.nspname = %s AND c.relname = %s
        ORDER BY a.attnum
    """, (schema, table))
    fks, _, _ = timed_fetch(conn, """
        SELECT
          kcu.column_name,
          ccu.table_schema AS foreign_schema,
          ccu.table_name AS foreign_table,
          ccu.column_name AS foreign_column,
          tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = %s AND tc.table_name = %s
    """, (schema, table))
    idxs, _, _ = timed_fetch(conn, """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname
    """, (schema, table))
    return {
        "schema": schema,
        "table": table,
        "columns": [
            {
                "name": r[0], "data_type": r[1], "udt_name": r[2],
                "nullable": r[3] == "YES", "default": r[4],
            }
            for r in cols
        ],
        "primary_key": [r[0] for r in pk],
        "foreign_keys": [
            {
                "column": r[0], "references": f"{r[1]}.{r[2]}({r[3]})",
                "constraint": r[4],
            }
            for r in fks
        ],
        "indexes": [{"name": r[0], "definition": r[1]} for r in idxs],
    }


def get_table(schema: str, table: str) -> dict[str, Any]:
    with read_only_connect() as conn:
        # Ensure exists
        hit = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchone()
        if not hit:
            raise LookupError(f"unknown table {schema}.{table}")
        meta = table_metadata(conn, schema, table)
        try:
            meta["row_count"] = conn.execute(
                f"SELECT count(*) FROM {qident(schema)}.{qident(table)}"
            ).fetchone()[0]
        except Exception:
            meta["row_count"] = None
        return meta


def vector_extension() -> dict[str, Any]:
    with read_only_connect() as conn:
        ext = conn.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if not ext:
            return {
                "installed": False,
                "message": "PGVECTOR NOT CONFIGURED",
                "vector_tables": [],
                "embedding_rows": 0,
            }
        return {"installed": True, "extname": ext[0], "extversion": ext[1]}


def vector_columns() -> dict[str, Any]:
    with read_only_connect() as conn:
        ext = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if not ext:
            return {
                "installed": False,
                "message": "PGVECTOR NOT CONFIGURED",
                "columns": [],
            }
        rows, _, elapsed = timed_fetch(conn, """
            SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
            WHERE a.attnum > 0 AND NOT a.attisdropped
              AND c.relkind IN ('r', 'p', 'm', 'v')
              AND (t.typname = 'vector'
                   OR format_type(a.atttypid, a.atttypmod) LIKE 'vector%%')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1, 2, 3
        """)
        out = []
        for schema, table, column, typ in rows:
            dim = None
            if "(" in typ and ")" in typ:
                try:
                    dim = int(typ.split("(")[1].split(")")[0])
                except Exception:
                    dim = None
            try:
                cnt = conn.execute(
                    f"SELECT count(*) FROM {qident(schema)}.{qident(table)} "
                    f"WHERE {qident(column)} IS NOT NULL"
                ).fetchone()[0]
            except Exception:
                cnt = None
            out.append({
                "schema": schema, "table": table, "column": column,
                "type": typ, "dimensions": dim, "non_null_row_count": cnt,
            })
        return {"installed": True, "columns": out, "query_ms": round(elapsed, 2)}


def vector_indexes() -> dict[str, Any]:
    with read_only_connect() as conn:
        ext = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if not ext:
            return {"installed": False, "indexes": [], "message": "PGVECTOR NOT CONFIGURED"}
        rows, _, elapsed = timed_fetch(conn, """
            SELECT schemaname, tablename, indexname, indexdef
            FROM pg_indexes
            WHERE indexdef ILIKE '%%vector%%'
               OR indexdef ILIKE '%%hnsw%%'
               OR indexdef ILIKE '%%ivfflat%%'
            ORDER BY schemaname, tablename, indexname
        """)
        indexes = []
        for schema, table, name, definition in rows:
            itype = "other"
            low = (definition or "").lower()
            if "hnsw" in low:
                itype = "HNSW"
            elif "ivfflat" in low:
                itype = "IVFFlat"
            indexes.append({
                "schema": schema, "table": table, "index_name": name,
                "index_type": itype, "definition": definition,
            })
        return {"installed": True, "indexes": indexes, "query_ms": round(elapsed, 2)}
