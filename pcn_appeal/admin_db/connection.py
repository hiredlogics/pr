"""Read-only database connections for the admin explorer."""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional
from urllib.parse import urlparse


def enabled() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def safe_db_identity() -> dict[str, Any]:
    """Environment + database name without credentials or full URL."""
    url = os.getenv("DATABASE_URL") or ""
    env = (os.getenv("APP_ENV") or os.getenv("RAILWAY_ENVIRONMENT_NAME")
           or os.getenv("VERCEL_ENV") or "local").lower()
    if env in ("prod", "production"):
        label = "production"
    elif env in ("stage", "staging"):
        label = "staging"
    elif env in ("dev", "development", "local", ""):
        label = "local"
    else:
        label = env
    db_name = None
    host_kind = None
    if url:
        try:
            parsed = urlparse(url)
            db_name = (parsed.path or "").lstrip("/").split("?")[0] or None
            host = (parsed.hostname or "").lower()
            if "neon" in host:
                host_kind = "neon"
            elif host in ("localhost", "127.0.0.1"):
                host_kind = "localhost"
            else:
                host_kind = "remote"
        except Exception:
            db_name = None
    return {
        "environment": label,
        "database_name": db_name,
        "host_kind": host_kind,
        "connected": False,
        "postgres_version": None,
        "credentials_redacted": True,
    }


@contextmanager
def read_only_connect(statement_timeout_ms: int = 15000) -> Iterator[Any]:
    """Open a connection and force a READ ONLY transaction."""
    from pcn_appeal.store import db
    if not db.enabled():
        raise RuntimeError("DATABASE_URL is not set")
    timeout = max(1000, min(int(statement_timeout_ms), 60000))
    with db.connect() as conn:
        # Clear any implicit aborted state from driver setup.
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        # Prefer a read-only transaction; fall back to ordinary txn if unsupported.
        try:
            conn.execute("BEGIN READ ONLY")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            conn.execute("BEGIN")
        try:
            conn.execute(f"SET LOCAL statement_timeout = '{timeout}ms'")
        except Exception:
            # SET failure aborts the txn — reopen a clean read-only txn.
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            try:
                conn.execute("BEGIN READ ONLY")
            except Exception:
                conn.execute("BEGIN")
        try:
            yield conn
            try:
                conn.execute("COMMIT")
            except Exception:
                pass
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


def timed_fetch(conn, sql: str, params: Optional[tuple] = None) -> tuple[list[tuple], list[str], float]:
    """Execute SELECT; return rows, column names, elapsed_ms."""
    t0 = time.perf_counter()
    cur = conn.execute(sql, params or ())
    rows = cur.fetchall()
    cols = [d.name for d in cur.description] if cur.description else []
    elapsed = (time.perf_counter() - t0) * 1000.0
    return list(rows), cols, elapsed


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def qident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"invalid identifier: {name!r}")
    return f'"{name}"'
