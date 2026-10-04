"""Load DATABASE_URL from an external env file into the process (no secret echo)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def load_database_url(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        key = key.strip()
        if key != "DATABASE_URL":
            continue
        value = value.strip().strip("\"'")
        if value:
            return value
    raise SystemExit(f"DATABASE_URL not found in {path}")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        raise SystemExit("usage: _load_db_url.py <env-file>")
    url = load_database_url(path)
    os.environ["DATABASE_URL"] = url
    host = url.split("@")[-1].split("/")[0]
    print(f"loaded DATABASE_URL host={host} len={len(url)}")
    # Emit for PowerShell capture via a marker line (host only already printed)
    # Write to a gitignored local file for subsequent commands in this shell session
    out = Path(".env.staging.local")
    out.write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    print(f"wrote {out} (gitignored pattern .env*)")


if __name__ == "__main__":
    main()
