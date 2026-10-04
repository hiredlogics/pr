"""Run CP Plus T07 three independent times against the live aligned API."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import _env
from . import fixtures as fx
from .client import DEFAULT_BASE, LiveClient
from .suite import _letter_cov

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reports" / "live"


def _one(client: LiveClient, run: int) -> dict:
    ntk = fx.late_ntk(pcn=f"LIVE_TEST_PCN_CPPLUS_R{run}", days_late=True)
    answers = {
        "multiple_visits": "yes", "left_site": "yes", "returned_same_day": "yes",
        "payment_made": "no", "site_postcode": "M1 1AA",
    }
    t0 = time.perf_counter()
    r = client.post("/appeal", {
        "documents": fx.docs(ntk["front"], ntk["back"]),
        "narrative": fx.NARRATIVES["CP_PLUS"],
        "answers": answers,
    }, timeout=420)
    body = r.get("body") if isinstance(r.get("body"), dict) else {}
    case_id = body.get("case_id")
    for _ in range(8):
        qs = body.get("questions") or []
        if not qs or body.get("letter"):
            break
        ans = {q["fact"]: answers.get(q["fact"], "yes") for q in qs if q.get("fact")}
        r = client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=420)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
    letter = body.get("letter") or ""
    cov = _letter_cov(letter)
    ms = int((time.perf_counter() - t0) * 1000)
    ok = (
        body.get("state") == "RELEASED"
        and cov["shopping"] and cov["reason"] and cov["left"] and cov["returned"]
        and cov["cancel"] and not cov["driver_id"] and not cov["placeholder"]
    )
    # Admin claim plan / console snapshot
    admin = {}
    if case_id and client.admin_headers:
        for key, path in (
            ("claim_plans", f"/admin/cases/{case_id}/claim-plans"),
            ("console", f"/admin/cases/{case_id}/console"),
        ):
            tr = client.get(path, admin=True, timeout=120)
            b = tr.get("body") if isinstance(tr.get("body"), dict) else {}
            if key == "console" and isinstance(b, dict):
                admin[key] = {
                    "status": tr["status"],
                    "why_stopped": b.get("why_stopped"),
                    "health": b.get("health"),
                    "summary": b.get("summary"),
                    "validation": b.get("validation"),
                }
            else:
                admin[key] = {"status": tr["status"], "ok": tr["ok"],
                              "approved": (b.get("plans") or [{}])[0].get("approved")
                              if isinstance(b.get("plans"), list) and b.get("plans")
                              else b.get("approved")}
    return {
        "run": run,
        "case_id": case_id,
        "state": body.get("state"),
        "outcome": body.get("outcome"),
        "ms": ms,
        "coverage": cov,
        "pass": ok,
        "letter_excerpt": letter[:900],
        "admin": admin,
    }


def main() -> int:
    _env.load_env()
    client = LiveClient(DEFAULT_BASE, admin_headers=_env.admin_headers())
    OUT.mkdir(parents=True, exist_ok=True)
    health = client.get("/health", timeout=60).get("body") or {}
    rows = []
    for i in range(1, 4):
        print(f"T07 run {i}...", flush=True)
        row = _one(client, i)
        rows.append(row)
        print(json.dumps({
            "run": row["run"], "case_id": row["case_id"], "state": row["state"],
            "outcome": row["outcome"], "pass": row["pass"], "coverage": row["coverage"],
            "ms": row["ms"],
        }, indent=2), flush=True)
    report = {
        "health": {
            "commit": health.get("commit"), "build_id": health.get("build_id"),
            "kb_release": health.get("kb_release"),
            "prompt_versions": health.get("prompt_versions"),
            "kb_drift": health.get("kb_drift"),
        },
        "runs": rows,
        "all_pass": all(r["pass"] for r in rows),
    }
    path = OUT / "P17_3_T07_X3.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print("wrote", path, "all_pass", report["all_pass"], flush=True)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
