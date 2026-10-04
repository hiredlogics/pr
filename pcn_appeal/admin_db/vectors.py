"""pgvector discovery, embedding explorer, health, similarity (read-only)."""
from __future__ import annotations

from typing import Any, Optional

from .catalog import vector_columns, vector_extension, vector_indexes
from .connection import qident, read_only_connect, timed_fetch
from .masking import mask_row


def status() -> dict[str, Any]:
    """Fast vector overview — avoid COUNT(*) on every discovered column."""
    ext = vector_extension()
    if not ext.get("installed"):
        return {
            "installed": False,
            "message": "PGVECTOR NOT CONFIGURED",
            "vector_tables": [],
            "embedding_rows": 0,
            "columns": [],
            "indexes": [],
        }
    from .connection import read_only_connect, timed_fetch, qident
    columns = []
    indexes = []
    total = 0
    with read_only_connect() as conn:
        rows, _, _ = timed_fetch(conn, """
            SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
            WHERE a.attnum > 0 AND NOT a.attisdropped
              AND c.relkind IN ('r', 'p')
              AND t.typname = 'vector'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1, 2, 3
        """)
        for schema, table, column, typ in rows:
            dim = None
            if "(" in typ and ")" in typ:
                try:
                    dim = int(typ.split("(")[1].split(")")[0])
                except Exception:
                    dim = None
            # Prefer estimated rows for overview speed
            est = conn.execute(
                "SELECT GREATEST(c.reltuples, 0)::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname = %s",
                (schema, table),
            ).fetchone()
            cnt = int(est[0]) if est else None
            if table == "kb_embeddings":
                try:
                    cnt = conn.execute(
                        f"SELECT count(*) FROM {qident(schema)}.{qident(table)}"
                    ).fetchone()[0]
                except Exception:
                    pass
                total = cnt or 0
            columns.append({
                "schema": schema, "table": table, "column": column,
                "type": typ, "dimensions": dim, "non_null_row_count": cnt,
            })
        idx_rows, _, _ = timed_fetch(conn, """
            SELECT schemaname, tablename, indexname, indexdef
            FROM pg_indexes
            WHERE indexdef ILIKE '%%hnsw%%' OR indexdef ILIKE '%%ivfflat%%'
               OR indexdef ILIKE '%%vector%%'
            ORDER BY schemaname, tablename, indexname
        """)
        for schema, table, name, definition in idx_rows:
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
        if not total and columns:
            total = sum(c.get("non_null_row_count") or 0 for c in columns)
    return {
        "installed": True,
        "extname": ext.get("extname"),
        "extversion": ext.get("extversion"),
        "columns": columns,
        "indexes": indexes,
        "embedding_rows": total,
        "message": None,
    }


def embeddings(
    *,
    page: int = 1,
    page_size: int = 50,
    show_raw_vector: bool = False,
    q: Optional[str] = None,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size
    st = status()
    if not st.get("installed"):
        return {**st, "rows": [], "total": 0}

    with read_only_connect() as conn:
        # Prefer kb_embeddings if present
        if not conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='kb_embeddings'"
        ).fetchone():
            # Generic: first vector table
            cols = st.get("columns") or []
            if not cols:
                return {**st, "rows": [], "total": 0, "table": None}
            schema, table, vcol = cols[0]["schema"], cols[0]["table"], cols[0]["column"]
            total = conn.execute(
                f'SELECT count(*) FROM {qident(schema)}.{qident(table)}'
            ).fetchone()[0]
            rows, colnames, elapsed = timed_fetch(
                conn,
                f'SELECT * FROM {qident(schema)}.{qident(table)} '
                f'LIMIT %s OFFSET %s',
                (page_size, offset),
            )
            out = []
            for row in rows:
                item = mask_row(colnames, row, show_sensitive=True)
                if not show_raw_vector:
                    for c in colnames:
                        if isinstance(item.get(c), dict) and item[c].get("_raw_omitted"):
                            pass
                out.append(item)
            return {
                "installed": True, "table": f"{schema}.{table}",
                "rows": out, "total": total, "page": page, "page_size": page_size,
                "query_ms": round(elapsed, 2),
            }

        where = ""
        params: list[Any] = []
        if q:
            where = " WHERE item_id ILIKE %s OR kind ILIKE %s OR version ILIKE %s"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        total = conn.execute(
            f"SELECT count(*) FROM kb_embeddings{where}", tuple(params)
        ).fetchone()[0]
        # Never select full embedding unless requested
        if show_raw_vector:
            sql = (
                "SELECT item_id, version, kind, embedding, "
                "vector_dims(embedding) AS dimensions "
                f"FROM kb_embeddings{where} "
                "ORDER BY item_id LIMIT %s OFFSET %s"
            )
        else:
            sql = (
                "SELECT item_id, version, kind, "
                "vector_dims(embedding) AS dimensions, "
                "(embedding IS NULL) AS is_null "
                f"FROM kb_embeddings{where} "
                "ORDER BY item_id LIMIT %s OFFSET %s"
            )
        try:
            rows, colnames, elapsed = timed_fetch(
                conn, sql, tuple(params) + (page_size, offset))
        except Exception:
            # Older PG without vector_dims
            sql = (
                f"SELECT item_id, version, kind FROM kb_embeddings{where} "
                "ORDER BY item_id LIMIT %s OFFSET %s"
            )
            rows, colnames, elapsed = timed_fetch(
                conn, sql, tuple(params) + (page_size, offset))

        out = []
        for row in rows:
            item = mask_row(colnames, row, show_sensitive=True)
            if show_raw_vector and "embedding" in item:
                emb = item["embedding"]
                if isinstance(emb, dict) and emb.get("_vector_preview"):
                    item["embedding_preview"] = emb
                elif hasattr(row[colnames.index("embedding")], "tolist"):
                    arr = row[colnames.index("embedding")].tolist()
                    item["embedding_preview"] = {
                        "values_head": [round(float(x), 4) for x in arr[:3]],
                        "dimensions": len(arr),
                    }
                    if show_raw_vector:
                        item["embedding_raw"] = [float(x) for x in arr]
                item.pop("embedding", None)
            # Enrich from kb_modules topic when kind=module
            out.append(item)

        # Attach source text from kb_modules / kb_blocks when available
        for item in out:
            mid = item.get("item_id")
            kind = item.get("kind")
            if kind == "module" and mid:
                hit = conn.execute(
                    "SELECT topic, core_proposition FROM kb_modules "
                    "WHERE module_id = %s LIMIT 1", (mid,)
                ).fetchone()
                if hit:
                    item["source_text"] = f"{hit[0]}. {hit[1] or ''}".strip()
                    item["entity_type"] = "module"
                    item["module_id"] = mid
            elif kind == "block" and mid:
                hit = conn.execute(
                    "SELECT left(text, 240) FROM kb_blocks WHERE block_id = %s LIMIT 1",
                    (mid,),
                ).fetchone()
                if hit:
                    item["source_text"] = hit[0]
                    item["entity_type"] = "block"

        # Model from latest release manifest
        model = None
        rel = conn.execute(
            "SELECT manifest FROM kb_releases ORDER BY published_at DESC NULLS LAST LIMIT 1"
        ).fetchone()
        if rel and isinstance(rel[0], dict):
            model = rel[0].get("embedder")
        for item in out:
            item["embedding_model"] = model or "unknown"

        return {
            "installed": True,
            "table": "public.kb_embeddings",
            "rows": out,
            "total": total,
            "page": page,
            "page_size": page_size,
            "embedding_model": model,
            "query_ms": round(elapsed, 2),
        }


def health() -> dict[str, Any]:
    st = status()
    if not st.get("installed"):
        return {
            "installed": False,
            "message": "PGVECTOR NOT CONFIGURED",
            "total_embedding_rows": 0,
            "flags": [],
        }
    with read_only_connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='kb_embeddings'"
        ).fetchone():
            return {
                "installed": True,
                "total_embedding_rows": st.get("embedding_rows") or 0,
                "table": None,
                "flags": ["no_kb_embeddings_table"],
                "columns": st.get("columns"),
            }
        total = conn.execute("SELECT count(*) FROM kb_embeddings").fetchone()[0]
        try:
            nulls = conn.execute(
                "SELECT count(*) FROM kb_embeddings WHERE embedding IS NULL"
            ).fetchone()[0]
        except Exception:
            nulls = 0
        try:
            dims = conn.execute(
                "SELECT vector_dims(embedding), count(*) FROM kb_embeddings "
                "WHERE embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
            dim_dist = {str(r[0]): r[1] for r in dims}
        except Exception:
            dim_dist = {}
        dupes = conn.execute(
            "SELECT item_id, count(*) FROM kb_embeddings "
            "GROUP BY item_id HAVING count(*) > 1 LIMIT 50"
        ).fetchall()
        models = []
        for r in conn.execute(
            "SELECT manifest->>'embedder' AS m, count(*) "
            "FROM kb_releases GROUP BY 1"
        ).fetchall():
            if r[0]:
                models.append({"model": r[0], "releases": r[1]})

        flags = []
        if nulls:
            flags.append("missing_vector")
        if len(dim_dist) > 1:
            flags.append("wrong_dimension")
        if len({m["model"] for m in models}) > 1:
            flags.append("multiple_embedding_models_in_releases")
        if dupes:
            flags.append("duplicate_embedding")
        # Orphans: embeddings whose item_id not in modules/blocks
        orphans = conn.execute("""
            SELECT e.item_id, e.kind FROM kb_embeddings e
            WHERE (e.kind = 'module' AND NOT EXISTS (
                SELECT 1 FROM kb_modules m WHERE m.module_id = e.item_id))
               OR (e.kind = 'block' AND NOT EXISTS (
                SELECT 1 FROM kb_blocks b WHERE b.block_id = e.item_id))
            LIMIT 50
        """).fetchall()
        if orphans:
            flags.append("orphan_embedding")

        return {
            "installed": True,
            "table": "public.kb_embeddings",
            "total_embedding_rows": total,
            "null_embeddings": nulls,
            "dimension_distribution": dim_dist,
            "models_used": models,
            "duplicate_entity_embeddings": [
                {"item_id": r[0], "count": r[1]} for r in dupes
            ],
            "orphan_embeddings": [
                {"item_id": r[0], "kind": r[1]} for r in orphans
            ],
            "flags": flags,
            "indexes": st.get("indexes") or [],
            "columns": st.get("columns") or [],
        }


def similarity_search(query: str, *, limit: int = 10) -> dict[str, Any]:
    """Embed query with configured HashingEmbedder; cosine search kb_embeddings.

    Results are retrieval candidates only — never mutates Claim Plan / case state.
    """
    st = status()
    if not st.get("installed"):
        return {
            "installed": False,
            "message": "PGVECTOR NOT CONFIGURED",
            "results": [],
        }
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    limit = max(1, min(int(limit), 25))

    from pcn_appeal.rag.embedder import HashingEmbedder
    from pcn_appeal.store.db import EMBED_DIM

    emb = HashingEmbedder(dim=EMBED_DIM)
    vec = emb([q])[0].tolist()

    with read_only_connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='kb_embeddings'"
        ).fetchone():
            return {
                "installed": True,
                "results": [],
                "note": "No kb_embeddings table; similarity unavailable",
                "read_only": True,
            }
        # Cosine distance operator <=>
        rows, colnames, elapsed = timed_fetch(conn, """
            SELECT item_id, version, kind,
                   embedding <=> %s::vector AS distance
            FROM kb_embeddings
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (vec, vec, limit))

        results = []
        for row in rows:
            item = dict(zip(colnames, row))
            dist = float(item["distance"]) if item.get("distance") is not None else None
            sim = (1.0 - dist) if dist is not None else None
            mid = item.get("item_id")
            kind = item.get("kind")
            source_text = None
            role = None
            eligibility = "UNKNOWN"
            claim_plan = "NOT EVALUATED (debug retrieval only)"
            if kind == "module" and mid:
                hit = conn.execute(
                    "SELECT topic, core_proposition, required_facts "
                    "FROM kb_modules WHERE module_id = %s LIMIT 1", (mid,)
                ).fetchone()
                if hit:
                    source_text = f"{hit[0]}. {hit[1] or ''}".strip()
                    req = hit[2]
                else:
                    req = None
                try:
                    from pcn_appeal.module_roles import (
                        role_of, can_be_claim_ground, can_lead_letter, is_support_only,
                    )
                    role = role_of(mid)
                    if can_be_claim_ground(mid):
                        eligibility = "ROLE_ELIGIBLE_FOR_CLAIM_GROUND"
                    elif is_support_only(mid):
                        eligibility = "REJECTED_SUPPORT_ONLY"
                    else:
                        eligibility = "REJECTED_ROLE_INELIGIBLE"
                    if not can_lead_letter(mid) and can_be_claim_ground(mid):
                        eligibility += "_CANNOT_LEAD"
                except Exception:
                    role = None
                results.append({
                    "rank": len(results) + 1,
                    "entity_id": mid,
                    "module_id": mid,
                    "entity_type": kind,
                    "version": item.get("version"),
                    "source_text": source_text,
                    "distance": dist,
                    "similarity": sim,
                    "module_role": role,
                    "required_facts": req if isinstance(req, (list, dict)) else req,
                    "eligibility": eligibility,
                    "claim_plan_status": claim_plan,
                    "pipeline": {
                        "vector_match": True,
                        "typed_eligibility": eligibility,
                        "module_role": role,
                        "final_status": claim_plan,
                    },
                })
            else:
                results.append({
                    "rank": len(results) + 1,
                    "entity_id": mid,
                    "entity_type": kind,
                    "version": item.get("version"),
                    "distance": dist,
                    "similarity": sim,
                    "eligibility": "N/A",
                    "claim_plan_status": claim_plan,
                })

        return {
            "installed": True,
            "read_only": True,
            "mutates_claim_plan": False,
            "embedder": emb.id,
            "dimensions": EMBED_DIM,
            "query": q,
            "results": results,
            "query_ms": round(elapsed, 2),
            "note": (
                "Similarity results are retrieval candidates only. "
                "They do not alter Claim Plan or case state."
            ),
        }
