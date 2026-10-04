"""Audit admin explorer access (no secrets recorded)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional


def _looks_uuid(value: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value or "",
    ))


def record_access(
    *,
    admin_user: str,
    page: str,
    case_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Best-effort append to audit_log when Postgres is available."""
    from .connection import enabled

    if not enabled():
        return
    try:
        from pcn_appeal.store import db
        payload = {
            "event": "admin_db_explorer",
            "admin_user": (admin_user or "admin")[:120],
            "page": page[:200],
            "case_id": case_id,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "detail": {k: v for k, v in (detail or {}).items()
                       if k not in ("password", "token", "url", "sql")},
        }
        with db.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='audit_log'"
            ).fetchone()
            if not exists:
                return
            cols = {
                r[0] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='audit_log'"
                ).fetchall()
            }
            if {"event", "detail", "actor"}.issubset(cols):
                conn.execute(
                    "INSERT INTO audit_log (actor, event, detail, case_id) "
                    "VALUES (%s, %s, %s::jsonb, %s)",
                    (
                        payload["admin_user"],
                        "admin_db_explorer",
                        json.dumps(payload),
                        case_id if case_id and _looks_uuid(case_id) else None,
                    ),
                )
            conn.commit()
    except Exception:
        return
