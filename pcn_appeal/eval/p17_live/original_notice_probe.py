"""Live probe: original notice fronts + generic reverse on Railway api-p7."""
from __future__ import annotations

import json
import re
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
GENERIC_BACK = (
    ROOT / "reports" / "live" / "p174_fixtures" / "FRONTEND_LIVE_TEST_CPPLUS_back.jpg"
)
OUT = ROOT / "reports" / "live" / "ORIGINAL_NOTICE_LIVE_COMPARE.json"

# Unique fronts only (skip duplicate copies).
CASES = [
    {
        "tag": "CPPLUS_PCN_00347261120013",
        "front": ASSETS
        / "c__Users_Abdul_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_WhatsApp_Image_2026-10-03_at_11.42.27_PM-200c7a5b-4ad9-4afb-b027-7654c1337bd9.png",
        "expected": {
            "operator": "CP Plus",
            "pcn": "00347261120013",
            "vrm": "EX15CZT",
            "location": "Canada Water",
            "postcode": "SE16 7LL",
        },
        "narrative": (
            "I attended the car park at the estate for shopping. "
            "Partway through I realised I had forgotten my purse at home, "
            "so I left the site. I returned later the same day to continue my visit."
        ),
        "answers": {
            "multiple_visits": "yes",
            "left_site": "yes",
            "returned_same_day": "yes",
            "payment_made": "no",
        },
    },
    {
        "tag": "CPPLUS_REMINDER_00347261100014",
        "front": ASSETS
        / "c__Users_Abdul_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_WhatsApp_Image_2026-10-03_at_11.43.35_PM-a4c3c576-d68c-4a56-8245-d0feaeccd6af.png",
        "expected": {
            "operator": "CP Plus",
            "pcn": "00347261100014",
            "vrm": "EX15CZT",
            "location": "Canada Water",
            "postcode": "SE16 7LL",
        },
        "narrative": (
            "I attended the car park at the estate for shopping. "
            "Partway through I realised I had forgotten my purse at home, "
            "so I left the site. I returned later the same day to continue my visit."
        ),
        "answers": {
            "multiple_visits": "yes",
            "left_site": "yes",
            "returned_same_day": "yes",
            "payment_made": "no",
        },
    },
    {
        "tag": "CPM_70377302",
        "front": ASSETS
        / "c__Users_Abdul_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_WhatsApp_Image_2026-10-03_at_11.41.59_PM_-_Copy-ff3e9545-43c9-4772-97b2-3c05d2dae7f1.png",
        "expected": {
            "operator": "CPM",
            "pcn": "70377302",
            "vrm": "LX26ZSE",
            "location": "Weavers Quarter",
            "postcode": "IG11 7TS",
        },
        "narrative": (
            "I attended briefly to drop off a passenger and returned later "
            "the same day to collect them. I did not stay continuously."
        ),
        "answers": {
            "multiple_visits": "yes",
            "left_site": "yes",
            "returned_same_day": "yes",
            "payment_made": "no",
            "dropoff_activity": "yes",
        },
    },
]


def _letter_flags(letter: str) -> dict:
    t = (letter or "").lower()
    return {
        "has_letter": bool(letter),
        "chars": len(letter or ""),
        "shopping": "shop" in t,
        "left": bool(re.search(r"\bleft\b|\bdepart", t)),
        "returned": bool(re.search(r"\breturn", t)),
        "dropoff_or_passenger": bool(re.search(r"drop.?off|passenger|collect", t)),
        "cancel": bool(re.search(r"cancel|withdraw", t)),
        "driver_id": bool(re.search(r"driver.*(name|identif)|i was the driver", t)),
        "placeholder": bool(re.search(r"\[TODO|FIXME|XXXX|placeholder", t)),
    }


def _extract_hits(blob: str, expected: dict) -> dict:
    text = blob or ""
    hits = {}
    for k, v in expected.items():
        hits[k] = bool(v) and (str(v).lower() in text.lower())
    return hits


def run_one(client: LiveClient, spec: dict, back_bytes: bytes) -> dict:
    front_path: Path = spec["front"]
    if not front_path.exists():
        return {"tag": spec["tag"], "error": f"missing front: {front_path}"}
    front_bytes = front_path.read_bytes()
    t0 = time.perf_counter()
    r = client.post_multipart(
        "/appeal/files",
        files=[
            ("files", f"{spec['tag']}_front.png", front_bytes),
            ("files", f"{spec['tag']}_back.jpg", back_bytes),
        ],
        fields={"narrative": spec["narrative"]},
        timeout=420,
    )
    body = r.get("body") if isinstance(r.get("body"), dict) else {}
    case_id = body.get("case_id")
    answers = dict(spec.get("answers") or {})
    rounds = []
    for i in range(8):
        qs = body.get("questions") or []
        state = body.get("state")
        rounds.append({
            "round": i,
            "state": state,
            "n_questions": len(qs),
            "facts": [q.get("fact") for q in qs if q.get("fact")],
            "has_letter": bool(body.get("letter")),
            "outcome": body.get("outcome"),
        })
        if not qs or body.get("letter") or state in ("RELEASED", "HELD", "FAILED"):
            break
        ans = {}
        for q in qs:
            fact = q.get("fact")
            if not fact:
                continue
            ans[fact] = answers.get(fact, "no")
        if not ans or not case_id:
            break
        r2 = client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=420)
        body = r2.get("body") if isinstance(r2.get("body"), dict) else {}

    letter = body.get("letter") or ""
    # Admin enrichment (optional)
    claim = console = None
    if case_id and client.admin_headers:
        cp = client.get(f"/admin/cases/{case_id}/claim-plans", admin=True, timeout=120)
        claim = cp.get("body") if isinstance(cp.get("body"), dict) else None
        cons = client.get(f"/admin/cases/{case_id}/console", admin=True, timeout=120)
        console = cons.get("body") if isinstance(cons.get("body"), dict) else None

    selected = []
    if isinstance(claim, dict):
        plan = claim.get("plan") or claim.get("claim_plan") or claim
        grounds = plan.get("selected_grounds") or plan.get("grounds") or []
        if isinstance(grounds, list):
            for g in grounds:
                if isinstance(g, dict):
                    selected.append(g.get("module_id") or g.get("id") or g.get("ground"))
                else:
                    selected.append(str(g))

    facts_view = {}
    if isinstance(console, dict):
        fv = console.get("fact_view") or console.get("facts") or {}
        if isinstance(fv, dict):
            for k in (
                "operator_name", "pcn_number", "vrm", "site_postcode",
                "parking_location", "allegation", "multiple_visits",
                "left_site", "returned_same_day", "dropoff_activity",
                "jurisdiction",
            ):
                if k in fv:
                    facts_view[k] = fv.get(k)

    search_blob = json.dumps({
        "body": {k: body.get(k) for k in (
            "state", "outcome", "extracted", "facts", "summary", "confirmation",
            "questions",
        ) if k in body},
        "letter": letter[:4000],
        "claim": claim,
        "console_facts": facts_view,
    }, default=str)

    return {
        "tag": spec["tag"],
        "case_id": case_id,
        "http_status": r.get("status"),
        "ms_total": int((time.perf_counter() - t0) * 1000),
        "state": body.get("state"),
        "outcome": body.get("outcome"),
        "rejected": body.get("rejected"),
        "needs_documents": body.get("needs_documents"),
        "rounds": rounds,
        "questions_final": [
            {"fact": q.get("fact"), "text": (q.get("text") or q.get("prompt") or "")[:160]}
            for q in (body.get("questions") or [])
        ],
        "selected_grounds": selected,
        "facts_view": facts_view,
        "letter_flags": _letter_flags(letter),
        "letter_excerpt": letter[:1500],
        "expected_field_hits": _extract_hits(search_blob, spec["expected"]),
        "front_bytes": len(front_bytes),
        "back_bytes": len(back_bytes),
        "back_source": "GENERIC_p174_CPPLUS_back",
    }


def main() -> int:
    present = load_env()
    hdrs = admin_headers()
    client = LiveClient(DEFAULT_BASE, admin_headers=hdrs)
    health = client.get("/health", timeout=60)
    if not GENERIC_BACK.exists():
        print("MISSING generic back", GENERIC_BACK)
        return 2
    back_bytes = GENERIC_BACK.read_bytes()
    results = []
    for spec in CASES:
        print(f"RUN {spec['tag']} ...", flush=True)
        row = run_one(client, spec, back_bytes)
        results.append(row)
        print(
            f"  -> case_id={row.get('case_id')} state={row.get('state')} "
            f"grounds={row.get('selected_grounds')} letter={row.get('letter_flags', {}).get('has_letter')}",
            flush=True,
        )

    report = {
        "meta": {
            "base": DEFAULT_BASE,
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "health": health.get("body"),
            "admin_configured": bool(hdrs),
            "env_keys_present": present,
            "generic_back": str(GENERIC_BACK),
            "note": "Original WhatsApp front photos + generic NTK reverse page",
        },
        "cases": results,
        "compare": {
            "states": {r["tag"]: r.get("state") for r in results},
            "grounds": {r["tag"]: r.get("selected_grounds") for r in results},
            "letter": {r["tag"]: r.get("letter_flags") for r in results},
            "expected_hits": {r["tag"]: r.get("expected_field_hits") for r in results},
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("WROTE", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
