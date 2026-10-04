"""Server-side paginated table browse (never loads full tables)."""
from __future__ import annotations

from typing import Any, Optional

from .connection import qident, read_only_connect, timed_fetch
from .masking import SECRET_COLUMNS, mask_row


def browse_table(
    schema: str,
    table: str,
    *,
    page: int = 1,
    page_size: int = 50,
    sort: Optional[str] = None,
    sort_dir: str = "desc",
    search: Optional[str] = None,
    columns: Optional[list[str]] = None,
    show_sensitive: bool = False,
    show_raw_vectors: bool = False,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    offset = (page - 1) * page_size
    sort_dir = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

    with read_only_connect() as conn:
        hit = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchone()
        if not hit:
            raise LookupError(f"unknown table {schema}.{table}")

        all_cols = [
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, table),
            ).fetchall()
        ]
        # Drop secret columns entirely
        safe_cols = [c for c in all_cols if c.lower() not in SECRET_COLUMNS
                     and not any(s in c.lower() for s in ("password", "api_key"))]
        if columns:
            wanted = [c for c in columns if c in safe_cols]
            if wanted:
                safe_cols = wanted
        if not safe_cols:
            raise ValueError("no selectable columns")

        # Resolve sort column
        order_col = sort if sort in safe_cols else None
        if order_col is None:
            for candidate in ("created_at", "updated_at", "published_at",
                              "case_id", "id", safe_cols[0]):
                if candidate in safe_cols:
                    order_col = candidate
                    break

        select_list = ", ".join(qident(c) for c in safe_cols)
        from_sql = f"{qident(schema)}.{qident(table)}"
        where = ""
        params: list[Any] = []
        if search:
            # Search across text-ish columns only
            text_cols = [
                r[0] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "AND data_type IN ('text','character varying','character','uuid','json','jsonb')",
                    (schema, table),
                ).fetchall()
                if r[0] in safe_cols
            ][:12]
            if text_cols:
                clauses = [f"CAST({qident(c)} AS text) ILIKE %s" for c in text_cols]
                where = " WHERE (" + " OR ".join(clauses) + ")"
                params.extend([f"%{search}%"] * len(text_cols))

        count_sql = f"SELECT count(*) FROM {from_sql}{where}"
        total = conn.execute(count_sql, tuple(params)).fetchone()[0]

        order = f" ORDER BY {qident(order_col)} {sort_dir} NULLS LAST" if order_col else ""
        data_sql = (
            f"SELECT {select_list} FROM {from_sql}{where}{order} "
            f"LIMIT %s OFFSET %s"
        )
        rows, cols, elapsed = timed_fetch(
            conn, data_sql, tuple(params) + (page_size, offset))

        out_rows = []
        for row in rows:
            masked = mask_row(cols, row, show_sensitive=show_sensitive)
            if show_raw_vectors:
                for col, raw in zip(cols, row):
                    if hasattr(raw, "tolist"):
                        try:
                            arr = raw.tolist()
                            if isinstance(arr, list) and arr and isinstance(arr[0], (int, float)):
                                masked[col] = {
                                    "values": [float(x) for x in arr],
                                    "dimensions": len(arr),
                                    "_raw": True,
                                }
                        except Exception:
                            pass
            out_rows.append(masked)

        return {
            "schema": schema,
            "table": table,
            "page": page,
            "page_size": page_size,
            "total": total,
            "sort": order_col,
            "sort_dir": sort_dir.lower(),
            "columns": cols,
            "rows": out_rows,
            "query_ms": round(elapsed, 2),
        }
