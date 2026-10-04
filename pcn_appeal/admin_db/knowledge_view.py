"""KB modules view for admin explorer (read-only)."""
from __future__ import annotations

from typing import Any, Optional

from .connection import read_only_connect, timed_fetch
from .masking import mask_row


def list_knowledge(
    *,
    q: Optional[str] = None,
    role: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    offset = (page - 1) * page_size

    with read_only_connect() as conn:
        # Prefer live reasoning KB table; fall back to ingestion table.
        table = None
        for candidate in ("kb_modules", "knowledge_modules"):
            if conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s", (candidate,)
            ).fetchone():
                table = candidate
                break
        if not table:
            return {"modules": [], "total": 0, "table": None,
                    "note": "No kb_modules / knowledge_modules table"}

        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        ).fetchall()]
        select = ", ".join(f'"{c}"' for c in cols)
        where_parts = []
        params: list[Any] = []
        if q:
            text_cols = [c for c in cols if c in (
                "module_id", "topic", "title", "core_proposition", "proposition",
                "use_when", "route", "drafting_notes", "name",
            )]
            if text_cols:
                where_parts.append(
                    "(" + " OR ".join(f'CAST("{c}" AS text) ILIKE %s' for c in text_cols) + ")"
                )
                params.extend([f"%{q}%"] * len(text_cols))
        if role and "module_role" in cols:
            where_parts.append('"module_role" ILIKE %s')
            params.append(f"%{role}%")

        where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        total = conn.execute(f'SELECT count(*) FROM "{table}"{where}', tuple(params)).fetchone()[0]
        order_col = "module_id" if "module_id" in cols else cols[0]
        rows, colnames, elapsed = timed_fetch(
            conn,
            f'SELECT {select} FROM "{table}"{where} '
            f'ORDER BY "{order_col}" LIMIT %s OFFSET %s',
            tuple(params) + (page_size, offset),
        )
        modules = [mask_row(colnames, r, show_sensitive=True) for r in rows]

        # Enrich with in-memory module roles (metadata only; not a DB write).
        try:
            from pcn_appeal.module_roles import role_of, can_lead_letter, can_be_claim_ground
            for m in modules:
                mid = m.get("module_id")
                if mid and not m.get("module_role"):
                    m["module_role"] = role_of(mid)
                if mid:
                    m["can_lead_letter"] = can_lead_letter(mid)
                    m["can_be_claim_ground"] = can_be_claim_ground(mid)
        except Exception:
            pass

        release = None
        if conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='kb_releases'"
        ).fetchone():
            rel = conn.execute(
                "SELECT kb_release_id, published_at, published_by "
                "FROM kb_releases ORDER BY published_at DESC NULLS LAST LIMIT 1"
            ).fetchone()
            if rel:
                release = {
                    "kb_release_id": str(rel[0]),
                    "published_at": rel[1].isoformat() if hasattr(rel[1], "isoformat") else rel[1],
                    "published_by": rel[2],
                }

        return {
            "table": table,
            "modules": modules,
            "total": total,
            "page": page,
            "page_size": page_size,
            "kb_release": release,
            "query_ms": round(elapsed, 2),
        }
