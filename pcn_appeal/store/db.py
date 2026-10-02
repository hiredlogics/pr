"""Connection handling and schema bootstrap.

`psycopg` is imported lazily so the reference implementation and the test
suite keep running with no database and no driver installed.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = Path(__file__).resolve().parent.parent.parent / "infra" / "postgres_schema.sql"

DATABASE_URL = os.getenv("DATABASE_URL")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))     # must match vector(N) in the schema


def enabled() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; the store layer is disabled")
    return url


@contextmanager
def connect(autocommit: bool = False) -> Iterator["psycopg.Connection"]:  # noqa: F821
    import psycopg
    from pgvector.psycopg import register_vector
    with psycopg.connect(_url(), autocommit=autocommit) as conn:
        try:
            register_vector(conn)
        except Exception:
            pass                       # vector extension not created yet (pre-init)
        yield conn


def init_schema() -> None:
    """Apply infra/postgres_schema.sql. Idempotent for the extensions and the
    CREATE TABLEs, which is why the file uses IF NOT EXISTS on the extensions;
    re-running against a populated database is a no-op that errors on nothing
    we depend on here."""
    sql = SCHEMA.read_text()
    with connect(autocommit=True) as conn:
        for statement in _split(sql):
            try:
                conn.execute(statement)
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise


def _split(sql: str) -> list[str]:
    """Statements, one per trailing ";" - except inside a $$ ... $$ body (a DO
    block), whose own semicolons belong to it."""
    out, buf = [], []
    in_body = False
    for line in sql.splitlines():
        if line.strip().startswith("--") and not in_body:
            continue
        buf.append(line)
        if line.count("$$") % 2:
            in_body = not in_body
        if not in_body and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out
