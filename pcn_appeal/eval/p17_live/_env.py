"""Load local env for optional admin token — never print secret values."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_env() -> dict[str, bool]:
    # Prefer Railway-pulled pilot secrets; never print values.
    for name in (
        ".env.p17.local", ".env.staging.local", ".env.local", ".env", ".env.production",
    ):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip().strip("\"'")
            # .env.p17.local wins for admin/DB; others only fill gaps.
            if not k or not v:
                continue
            if name == ".env.p17.local" or k not in os.environ:
                os.environ[k] = v
    keys = (
        "ADMIN_TOKEN", "ADMIN_TRACE_TOKEN", "PCN_ADMIN_TOKEN",
        "OPENAI_API_KEY", "DATABASE_URL", "PCN_API_URL",
    )
    return {k: bool(os.environ.get(k)) for k in keys}


def admin_headers() -> dict[str, str]:
    token = (
        os.getenv("ADMIN_TRACE_TOKEN")
        or os.getenv("ADMIN_TOKEN")
        or os.getenv("PCN_ADMIN_TOKEN")
        or ""
    ).strip()
    if not token:
        return {}
    return {"X-Admin-Token": token, "Authorization": f"Bearer {token}"}
