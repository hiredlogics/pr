"""Run P11 Postgres gates against .env.staging.local DATABASE_URL."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def _load_env() -> None:
    for line in Path(".env.staging.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
    os.environ.setdefault("LLM_PROVIDER", "openai")
    from pcn_appeal import config
    config.load()


def main() -> int:
    _load_env()
    from pcn_appeal.eval.p11_staging import migrations, postgres_roundtrip
    from pcn_appeal.store import db, kb_sync

    print("init_schema...")
    db.init_schema()
    print("schema_ok")

    print("kb_sync...")
    try:
        sync = kb_sync.sync(published_by="p11_staging")
        print("sync", {k: sync.get(k) for k in (
            "release_id", "modules", "blocks", "embeddings", "embedder")})
    except Exception as exc:  # noqa: BLE001
        sync = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        print("sync_error", sync["error"])

    print("migrations_live...")
    mig_live = migrations.audit_live()
    print("migrations_live", mig_live.get("passed"), mig_live.get("reason") or "")

    print("postgres_roundtrip...")
    pg = postgres_roundtrip.run()
    print("postgres", pg.get("passed"), pg.get("reason") or pg.get("error") or "")

    out = {
        "sync": sync if isinstance(sync, dict) else {},
        "migrations_live": mig_live,
        "postgresql": pg,
        "database": "pcn_appeal_p11",
        "host": os.environ["DATABASE_URL"].split("@")[-1].split("/")[0],
    }
    dest = ROOT / "reports" / "p11_staging" / "postgres_gates.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print("wrote", dest)
    return 0 if pg.get("passed") and mig_live.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
