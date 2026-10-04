"""Rewrite DATABASE_URL in .env.p17.local to the Railway TCP proxy (local tools)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = ROOT / ".env.p17.local"


def public_database_url() -> str:
    # Windows npm shim is railway.cmd / railway.ps1 — prefer .cmd for CreateProcess.
    cli = "railway.cmd" if os.name == "nt" else "railway"
    raw = subprocess.check_output(
        [cli, "variables", "-s", "Postgres", "--json"],
        text=True,
        shell=False,
    )
    d = json.loads(raw)
    pw = d.get("PGPASSWORD") or d.get("POSTGRES_PASSWORD") or ""
    user = d.get("POSTGRES_USER") or d.get("PGUSER") or "postgres"
    db = d.get("PGDATABASE") or d.get("POSTGRES_DB") or "railway"
    host = d["RAILWAY_TCP_PROXY_DOMAIN"]
    port = d["RAILWAY_TCP_PROXY_PORT"]
    return f"postgresql://{user}:{quote(pw)}@{host}:{port}/{db}"


def write_env(url: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.exists() else []
    out, seen = [], False
    for line in lines:
        if line.startswith("DATABASE_URL="):
            out.append(f"DATABASE_URL={url}")
            seen = True
        elif line.startswith("# DATABASE_URL rewritten"):
            continue
        else:
            out.append(line)
    if not seen:
        out.append(f"DATABASE_URL={url}")
    out.append("# DATABASE_URL rewritten to Railway TCP proxy for local admin tools")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    url = public_database_url()
    write_env(url)
    os.environ["DATABASE_URL"] = url
    from pcn_appeal.store import db

    print("enabled", db.enabled())
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT kb_release_id, published_at FROM kb_releases "
            "ORDER BY published_at DESC NULLS LAST LIMIT 8"
        ).fetchall()
        print("releases", [(r[0], str(r[1])) for r in rows])
        prompts = conn.execute(
            "SELECT prompt_id, version, active FROM prompts "
            "WHERE prompt_id IN ('drafting','semantic_extraction') "
            "ORDER BY prompt_id, version"
        ).fetchall()
        print("prompts", prompts)
        mods = conn.execute(
            "SELECT module_id, version FROM kb_modules "
            "WHERE module_id IN ('KB-BAY-01','KB-BAY-02','KB-POFA-01','KB-REC-01') "
            "ORDER BY module_id, version"
        ).fetchall()
        print("modules", mods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
