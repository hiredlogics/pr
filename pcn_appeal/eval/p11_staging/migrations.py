"""Static + live migration-chain audit for staging PostgreSQL."""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIG_DIR = ROOT / "infra" / "migrations"

APPEND_ONLY_MARKERS = (
    "append-only", "immutable", "RAISE EXCEPTION", "BEFORE UPDATE", "BEFORE DELETE",
)


def audit_static() -> dict:
    files = sorted(MIG_DIR.glob("*.sql"))
    numbers = []
    rows = []
    dupes = []
    for path in files:
        m = re.match(r"(\d+)_", path.name)
        num = int(m.group(1)) if m else None
        if num is not None:
            if num in numbers:
                dupes.append(num)
            numbers.append(num)
        text = path.read_text(encoding="utf-8", errors="ignore")
        rows.append({
            "file": path.name,
            "number": num,
            "bytes": path.stat().st_size,
            "has_rollback_comment": "Rollback:" in text or "rollback:" in text.lower(),
            "append_only_markers": sum(1 for k in APPEND_ONLY_MARKERS if k in text),
            "creates_index": "CREATE INDEX" in text.upper() or "CREATE UNIQUE INDEX" in text.upper(),
            "foreign_key": "REFERENCES" in text.upper(),
            "trigger": "CREATE TRIGGER" in text.upper() or "CREATE OR REPLACE FUNCTION" in text.upper(),
        })
    expected = list(range(1, (max(numbers) if numbers else 0) + 1))
    missing = [n for n in expected if n not in numbers]
    ordered = numbers == sorted(numbers)
    return {
        "passed": ordered and not dupes and not missing and len(files) >= 12,
        "migration_count": len(files),
        "numbers": numbers,
        "duplicates": dupes,
        "missing_numbers": missing,
        "ordered": ordered,
        "files": rows,
        "rollback_docs": (
            "Each migration file documents a Rollback: section. "
            "Greenfield: python -m pcn_appeal.store init (postgres_schema.sql). "
            "Existing DB: apply infra/migrations/0001..0011 in order via psql. "
            "Recovery: restore from staging backup / point-in-time recovery; "
            "do not UPDATE/DELETE append-only audit tables (triggers block)."
        ),
    }


def audit_live() -> dict:
    """Apply/verify schema against real DATABASE_URL."""
    url = os.getenv("DATABASE_URL") or os.getenv("STAGING_DATABASE_URL")
    if not url:
        return {
            "passed": False,
            "ran": False,
            "reason": "DATABASE_URL / STAGING_DATABASE_URL not set (Postgres-only gate)",
        }
    try:
        from pcn_appeal.store import db
        if not db.enabled():
            return {"passed": False, "ran": False, "reason": "store.db.enabled() false"}
        db.init_schema()
        checks = {}
        with db.connect() as conn:
            checks["select_1"] = conn.execute("SELECT 1").fetchone()[0] == 1
            # Core tables
            for table in (
                "cases", "facts", "fact_history", "claim_plans", "draft_versions",
                "legal_findings", "master_case_state", "document_baselines",
            ):
                try:
                    conn.execute(f"SELECT 1 FROM {table} LIMIT 0")
                    checks[f"table:{table}"] = True
                except Exception as exc:  # noqa: BLE001
                    checks[f"table:{table}"] = False
                    checks[f"table:{table}:error"] = f"{type(exc).__name__}: {exc}"[:160]
            # Append-only: attempt UPDATE on master_case_state should fail if rows exist
            # or trigger exists — probe pg_trigger
            try:
                n = conn.execute(
                    "SELECT count(*) FROM pg_trigger WHERE tgname ILIKE %s",
                    ("%master_case%",),
                ).fetchone()[0]
                checks["append_only_triggers_present"] = n > 0
            except Exception:
                checks["append_only_triggers_present"] = None
        passed = all(v is True for k, v in checks.items() if not k.endswith(":error"))
        return {"passed": passed, "ran": True, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": False,
            "ran": True,
            "reason": f"{type(exc).__name__}: {exc}"[:300],
        }
