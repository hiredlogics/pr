"""P17.8 live E2E: original front + generic back → identity trace → outcome."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pcn_appeal.eval.p17_live._env import admin_headers, load_env
from pcn_appeal.eval.p17_live.client import DEFAULT_BASE, LiveClient

ASSETS = Path(
    r"C:\Users\Abdul\.cursor\projects\c-Users-Abdul-Downloads-New-folder-4-pr\assets"
)
FRONT = ASSETS / (
    "c__Users_Abdul_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_"
    "WhatsApp_Image_2026-10-03_at_11.42.27_PM-200c7a5b-4ad9-4afb-b027-7654c1337bd9.png"
)
GENERIC_BACK = (
    ROOT / "reports" / "live" / "p174_fixtures" / "FRONTEND_LIVE_TEST_CPPLUS_back.jpg"
)
OUT = ROOT / "reports" / "live" / "P17_8_IDENTITY_E2E_TRACE.json"


def main() -> int:
    load_env()
    client = LiveClient(DEFAULT_BASE, admin_headers=admin_headers())
    health = client.get("/health", timeout=60)
    if not FRONT.exists() or not GENERIC_BACK.exists():
        print("missing fixtures")
        return 2
    narrative = (
        "I attended the car park for shopping. Partway through I realised I had "
        "forgotten my purse at home, so I left the site. I returned later the same day."
    )
    r = client.post_multipart(
        "/appeal/files",
        files=[
            ("files", "cpplus_front.png", FRONT.read_bytes()),
            ("files", "generic_back.jpg", GENERIC_BACK.read_bytes()),
        ],
        fields={"narrative": narrative},
        timeout=420,
    )
    body = r.get("body") if isinstance(r.get("body"), dict) else {}
    case_id = body.get("case_id")
    answers = {
        "multiple_visits": "yes", "left_site": "yes",
        "returned_same_day": "yes", "payment_made": "no",
    }
    for _ in range(6):
        qs = body.get("questions") or []
        if not qs or body.get("letter") or body.get("state") in (
            "RELEASED", "HELD", "MANUAL_REVIEW", "NO_APPEAL_RIGHT",
        ):
            break
        ans = {q["fact"]: answers.get(q["fact"], "no") for q in qs if q.get("fact")}
        if not ans or not case_id:
            break
        r = client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=420)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}

    console = claim = audit = None
    if case_id and client.admin_headers:
        console = client.get(f"/admin/cases/{case_id}/console", admin=True, timeout=120).get("body")
        claim = client.get(f"/admin/cases/{case_id}/claim-plans", admin=True, timeout=120).get("body")
        audit = client.get(f"/admin/cases/{case_id}/audit", admin=True, timeout=120).get("body")

    identity_events = []
    if isinstance(audit, dict):
        rows = audit.get("events") or audit.get("audit") or audit.get("entries") or []
        if isinstance(rows, list):
            for e in rows:
                if isinstance(e, dict) and "identity" in str(e.get("event") or "").lower():
                    identity_events.append(e)
                if isinstance(e, dict) and e.get("event") in (
                    "document_identity_state", "document_identity_conflict",
                    "document_identity_flags", "held_document_identity",
                    "document_identity_revision", "blocked_notice_sides_incomplete",
                ):
                    identity_events.append(e)

    approved = []
    if isinstance(console, dict):
        cp = console.get("claim_plan") or {}
        approved = list(cp.get("approved") or [])

    report = {
        "meta": {
            "base": DEFAULT_BASE,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "health": health.get("body"),
            "front": str(FRONT.name),
            "back": "GENERIC_p174_reverse",
            "note": "Trace after P17.8 identity gate deploy (local code may not be live yet)",
        },
        "path": {
            "case_id": case_id,
            "http_status": r.get("status"),
            "state": body.get("state"),
            "outcome": body.get("outcome"),
            "questions": [
                q.get("fact") for q in (body.get("questions") or []) if q.get("fact")
            ],
            "letter_excerpt": (body.get("letter") or "")[:1200],
            "approved_grounds": approved,
            "identity_events": identity_events[:40],
            "claim_plans": claim,
        },
    }
    # First disagreement heuristic from letter vs expected plate on photo.
    letter = body.get("letter") or ""
    report["first_disagreement"] = None
    if "EX64" in letter or ("EX15" not in letter and "EX" in letter):
        report["first_disagreement"] = {
            "field": "vrm",
            "expected_on_photo": "EX15CZT",
            "draft_or_extract": "see letter_excerpt / identity_events",
            "note": "OCR/identity mismatch risk — gate should block release when conflicted",
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "case_id": case_id,
        "state": body.get("state"),
        "grounds": approved,
        "identity_events": len(identity_events),
        "wrote": str(OUT),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
