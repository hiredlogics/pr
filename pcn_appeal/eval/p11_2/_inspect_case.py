"""Inspect the P11.2 held case for drafting/validation failure detail."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _env() -> None:
    for name in (".env.staging.local", ".env.local", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("\"'")
                if k and v and k not in os.environ:
                    os.environ[k] = v


def main() -> None:
    _env()
    from pcn_appeal import config
    from pcn_appeal.store import cases as cs

    config.load()
    cid = "c20b62f3-497a-421a-9334-92261cb96307"
    case = cs.load(cid)
    print("state", case.state.value)
    print("drafts", len(case.draft_versions or []))
    for i, d in enumerate(case.draft_versions or []):
        letter = d.get("letter") or d.get("text") or ""
        print(f"--- draft {i} released={d.get('released')} status={d.get('validation_status')} chars={len(letter)}")
        issues = d.get("validation_issues") or d.get("issues") or []
        if isinstance(issues, list):
            for iss in issues[:12]:
                if isinstance(iss, dict):
                    print(" ", iss.get("rule"), (iss.get("message") or "")[:160])
                else:
                    print(" ", str(iss)[:160])
        if letter:
            print(letter[:800])
            print("...")
    for a in case.audit:
        if a.get("event") in ("validation", "customer_outcome", "release_metadata_incomplete",
                               "release_blocked", "draft_error", "dropped_failing_sentences"):
            print("AUDIT", a.get("event"), json.dumps({
                k: a.get(k) for k in ("passed", "outcome", "missing", "attempt", "dropped")
                if k in a
            }, default=str))
            if a.get("issues"):
                print("  issues sample", a["issues"][:6])
            if a.get("detail"):
                print("  detail", str(a["detail"])[:200])


if __name__ == "__main__":
    main()
