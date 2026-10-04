"""Probe which staging credentials exist without printing secret values."""
from __future__ import annotations

import os
from pathlib import Path

WATCH = (
    "OPENAI_API_KEY", "DATABASE_URL", "STAGING_DATABASE_URL", "LLM_PROVIDER",
    "ADMIN_TOKEN", "ADMIN_TRACE_TOKEN", "APP_ENV", "PCN_API_URL",
    "RAILWAY_ENVIRONMENT_NAME", "BLOB_ALLOWED_HOSTS",
)


def main() -> None:
    file_keys: dict[str, bool] = {}
    for name in (".env", ".env.local", ".env.staging"):
        path = Path(name)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            v = v.strip().strip("\"'")
            file_keys[k.strip()] = bool(v)
    for key in WATCH:
        present = bool(os.getenv(key)) or bool(file_keys.get(key))
        print(f"{key}: {'present' if present else 'absent'}")
    print("env_files:", [n for n in (".env", ".env.local", ".env.staging") if Path(n).exists()])


if __name__ == "__main__":
    main()
