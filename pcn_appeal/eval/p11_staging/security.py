"""Security / privacy static checks for staging sign-off."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Live-looking OpenAI keys (not placeholders).
LIVE_KEY = re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}\b")
PLACEHOLDER = re.compile(r"sk-(?:x|test|live-secret|\.\.\.|\[\.\.\.\])\b", re.I)


def run() -> dict:
    findings = []
    scanned = 0
    skip_dirs = {".git", ".venv", "node_modules", ".vercel", "__pycache__", "reports"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in {
            ".py", ".ts", ".tsx", ".js", ".md", ".yml", ".yaml", ".json", ".sql", ".txt", ".sh"
        }:
            continue
        if path.name.startswith(".env"):
            findings.append({
                "severity": "INFO",
                "issue": "env_file_present_locally",
                "path": str(path.relative_to(ROOT)),
                "note": "Must not be committed; verify gitignored",
            })
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        scanned += 1
        for m in LIVE_KEY.finditer(text):
            frag = m.group(0)
            if PLACEHOLDER.search(frag) or "redacted" in frag.lower():
                continue
            # Allow documentation ellipsis patterns already excluded
            if frag.endswith("...") or "sk-..." in frag:
                continue
            findings.append({
                "severity": "CRITICAL",
                "issue": "possible_live_api_key_in_repo",
                "path": str(path.relative_to(ROOT)),
                "fragment": frag[:10] + "...[redacted]",
            })

    # Gitignore must cover .env
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8", errors="ignore")
    if ".env" not in gi:
        findings.append({"severity": "HIGH", "issue": "gitignore_missing_env"})

    # Admin token required in production — code presence check
    api = (ROOT / "pcn_appeal" / "api.py").read_text(encoding="utf-8", errors="ignore")
    if "_require_admin" not in api and "ADMIN_TRACE_TOKEN" not in api:
        findings.append({"severity": "HIGH", "issue": "admin_auth_missing"})

    critical = [f for f in findings if f.get("severity") == "CRITICAL"]
    return {
        "passed": len(critical) == 0,
        "ran": True,
        "files_scanned": scanned,
        "critical_count": len(critical),
        "findings": findings[:50],
        "note": (
            "Rotate/revoke any historically exposed live key before staging sign-off. "
            "No secret values are printed."
        ),
    }
