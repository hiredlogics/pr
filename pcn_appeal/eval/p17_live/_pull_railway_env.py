"""Pull Railway variables into .env.p17.local (gitignored). Never prints secrets."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / ".env.p17.local"


def main() -> int:
    railway = "railway.cmd" if (ROOT.drive and True) else "railway"
    # Windows npm shim is railway.cmd; bare "railway" may not resolve in CreateProcess.
    for candidate in ("railway.cmd", "railway"):
        try:
            raw = subprocess.check_output(
                [candidate, "variables", "--json"],
                cwd=ROOT, text=True, stderr=subprocess.STDOUT, shell=False,
            )
            break
        except FileNotFoundError:
            continue
    else:
        raise FileNotFoundError("railway CLI not found")
    data = json.loads(raw)
    if isinstance(data, list):
        vars_ = {(item.get("name") or item.get("key")): item.get("value") for item in data}
    elif isinstance(data, dict) and "variables" in data:
        vars_ = data["variables"]
    else:
        vars_ = data

    print("n_vars", len(vars_))
    for n in sorted(vars_):
        v = vars_.get(n)
        print(f"{n}: set={bool(v)} len={len(str(v) if v is not None else '')}")

    wanted = (
        "ADMIN_TOKEN", "ADMIN_TRACE_TOKEN", "DATABASE_URL", "GIT_COMMIT",
        "RAILWAY_GIT_COMMIT_SHA", "KB_RELEASE_ID", "OPENAI_API_KEY",
        "ALLOW_KB_DRIFT", "APP_ENV",
    )
    lines = []
    for k in wanted:
        if vars_.get(k):
            lines.append(f"{k}={vars_[k]}")
    if vars_.get("ADMIN_TRACE_TOKEN") and not vars_.get("ADMIN_TOKEN"):
        lines.append(f"ADMIN_TOKEN={vars_['ADMIN_TRACE_TOKEN']}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT.name, "n_keys", len(lines))

    gi = ROOT / ".gitignore"
    txt = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".env.p17.local" not in txt:
        gi.write_text(txt.rstrip() + "\n.env.p17.local\n", encoding="utf-8")
        print("gitignore_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
