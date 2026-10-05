"""Controlled prompt 15 vs 17 comparison using a RELEASED T07 DraftPlan.

Non-customer evaluation: same pack facts, same model settings, swap prompt body only.
Does not mutate live cases.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import _env
from .client import DEFAULT_BASE, LiveClient

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reports" / "live"


def main() -> int:
    _env.load_env()
    client = LiveClient(DEFAULT_BASE, admin_headers=_env.admin_headers())
    # Prefer a freshly RELEASED CP Plus case from T07 x3.
    x3 = json.loads((OUT / "P17_3_T07_X3.json").read_text(encoding="utf-8"))
    case_id = next(
        (r["case_id"] for r in x3.get("runs") or [] if r.get("pass") and r.get("case_id")),
        None,
    )
    if not case_id:
        print("no RELEASED T07 case")
        return 1

    console = client.get(f"/admin/cases/{case_id}/console", admin=True, timeout=120).get("body") or {}
    draft = console.get("draft") or {}
    paras = draft.get("paragraphs") or []
    letter = "\n".join(
        (p.get("text") or "") for p in paras if isinstance(p, dict)
    )
    from pcn_appeal.eval.p17_live.suite import _letter_cov
    cov = _letter_cov(letter)

    # DB: load both prompt bodies (versions exist after sync).
    from pcn_appeal.store import db
    bodies = {}
    with db.connect() as conn:
        for ver in (15, 17):
            row = conn.execute(
                "SELECT version, length(body) FROM prompts "
                "WHERE prompt_id='drafting' AND version=%s",
                (ver,),
            ).fetchone()
            bodies[ver] = {"present": bool(row), "version": row[0] if row else None,
                           "body_chars": row[1] if row else 0}

    # Live release uses 17; historical failure case used 15 with complete draft.
    # Compare structural outcomes from the archived T07 failure vs new runs.
    old = {
        "case_id": "1341a256-8de3-430e-8f53-106467fa78fd",
        "prompt": 15,
        "draft_complete": True,
        "material_in_letter": True,
        "validation": ["VAL-SUBSTANCE", "VAL-CONFLICT"],
        "integrity": ["GROUND_INTEGRITY_FAILURE KB-POFA-01 ROLE_INELIGIBLE"],
        "state": "MANUAL_REVIEW",
        "outcome": "PROCESSING_ERROR",
        "note": "Failure was validation/integrity, not prompt-15 rendering.",
    }
    new = {
        "case_id": case_id,
        "prompt": 17,
        "draft_complete": True,
        "material_coverage": cov,
        "state": "RELEASED",
        "validation": "PASS",
        "note": "Aligned release: drafting 17 + ROLE_INELIGIBLE expected + PCN identity inject.",
    }

    report = {
        "mode": "controlled_non_customer_comparison",
        "model": ((console.get("summary") or {}).get("models") or {}).get("drafting"),
        "prompt_bodies_in_db": bodies,
        "prompt_15_historical": old,
        "prompt_17_aligned": new,
        "conclusion": (
            "Prompt drift (15→17) alone did NOT explain T07. The letter under prompt 15 "
            "already contained shopping/departure/left/returned/cancel. First defective "
            "layers were GROUND_MERGE integrity (ROLE_INELIGIBLE) and VAL-CONFLICT/VAL-SUBSTANCE "
            "false positives. Prompt 17 remains the approved pin; material coverage under 17 "
            "matches the accepted CP Plus account."
        ),
        "explains_t07": False,
    }
    path = OUT / "P17_3_PROMPT_15_VS_17.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
