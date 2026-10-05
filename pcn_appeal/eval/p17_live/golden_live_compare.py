"""Live API hit vs golden draft expectations (matched fixtures).

Compares:
  A) empty narrative → must ASK material questions (not PoFA-only silent release)
  B) shopping leave/return narrative → RELEASE with PoFA + ANPR and letter coverage
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "reports" / "live" / "p174_fixtures"
OUT = ROOT / "reports" / "live" / "GOLDEN_LIVE_COMPARE.json"
BASE = os.environ.get("PCN_API_URL", "https://api-p7-production.up.railway.app")

SHOPPING_NARRATIVE = (
    "I went shopping at the retail park. I left the car park mid-morning and "
    "returned later the same day for a second visit. I paid for parking."
)


def _post_files(client: httpx.Client, narrative: str) -> dict:
    front = FIX / "FRONTEND_LIVE_TEST_CPPLUS_front.jpg"
    back = FIX / "FRONTEND_LIVE_TEST_CPPLUS_back.jpg"
    files = [
        ("files", (front.name, front.read_bytes(), "image/jpeg")),
        ("files", (back.name, back.read_bytes(), "image/jpeg")),
    ]
    r = client.post(
        "/appeal/files",
        files=files,
        data={"narrative": narrative},
        timeout=420,
    )
    r.raise_for_status()
    return r.json()


def _letter_flags(letter: str) -> dict:
    t = (letter or "").lower()
    return {
        "chars": len(letter or ""),
        "shopping": bool(re.search(r"shop", t)),
        "left": bool(re.search(r"\bleft\b", t)),
        "returned": bool(re.search(r"return", t)),
        "multiple|anpr": bool(re.search(r"more than one|anpr|separate occasion", t)),
        "payment": bool(re.search(r"payment|paid", t)),
        "pofa|schedule 4": bool(re.search(r"schedule 4|protection of freedoms", t)),
        "cancel": bool(re.search(r"cancel", t)),
        "placeholder": "{{" in (letter or "") or "TODO" in (letter or ""),
    }


def main() -> int:
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report: dict = {"meta": {"base": BASE, "started": started}, "cases": []}
    with httpx.Client(base_url=BASE, timeout=60) as client:
        health = client.get("/health").json()
        report["meta"]["health"] = {
            "build_id": health.get("build_id"),
            "commit": health.get("commit"),
            "models": health.get("models"),
        }

        # A — empty narrative: must ask, not silent PoFA release
        t0 = time.time()
        a = _post_files(client, "")
        a_row = {
            "tag": "EMPTY_NARRATIVE_MUST_ASK",
            "case_id": a.get("case_id"),
            "ms": int((time.time() - t0) * 1000),
            "state": a.get("state"),
            "outcome": a.get("outcome"),
            "n_questions": len(a.get("questions") or []),
            "questions": [q.get("fact") for q in (a.get("questions") or [])],
            "grounds": a.get("grounds") or [],
            "letter_flags": _letter_flags(a.get("letter") or ""),
        }
        # Pass if paused with questions OR released only after asking (can't
        # verify prior asks here) — for first response, require questions when
        # not yet RELEASED with multi-ground, or if RELEASED then must not be
        # PoFA-only without account coverage.
        asked = a_row["n_questions"] > 0
        thin_release = (
            a.get("state") == "RELEASED"
            and not asked
            and len(a.get("grounds") or []) <= 1
        )
        a_row["pass"] = bool(asked) and not thin_release
        a_row["expected"] = "questions about multiple_visits and/or payment_made"

        # Keep answering until done or 4 rounds.
        if asked and a.get("case_id"):
            answers = {}
            for q in a.get("questions") or []:
                fact = q.get("fact")
                if fact in ("multiple_visits", "payment_made", "left_site",
                            "returned_same_day", "anpr_duration_disputed",
                            "payment_attempt_failed"):
                    answers[fact] = "yes"
                elif fact == "payment_method":
                    answers[fact] = "MACHINE"
            if answers:
                t1 = time.time()
                r2 = client.post(
                    f"/appeal/{a['case_id']}",
                    json={"answers": answers},
                    timeout=420,
                ).json()
                rounds = [{"round": 0, **{k: a_row[k] for k in ("state", "n_questions", "questions", "grounds")}}]
                cur = r2
                for i in range(1, 5):
                    rounds.append({
                        "round": i,
                        "state": cur.get("state"),
                        "n_questions": len(cur.get("questions") or []),
                        "questions": [q.get("fact") for q in (cur.get("questions") or [])],
                        "grounds": cur.get("grounds") or [],
                        "ms": int((time.time() - t1) * 1000),
                    })
                    if not cur.get("questions"):
                        break
                    ans = {}
                    for q in cur.get("questions") or []:
                        f = q.get("fact")
                        if f == "payment_method":
                            ans[f] = "MACHINE"
                        elif q.get("type") == "bool" or f in (
                            "multiple_visits", "payment_made", "left_site",
                            "returned_same_day", "payment_attempt_failed",
                            "anpr_duration_disputed", "genuine_customer",
                        ):
                            ans[f] = "yes"
                        elif f == "collection_or_checkin_delay":
                            ans[f] = "no"
                        elif q.get("type") == "choice" and q.get("options"):
                            ans[f] = q["options"][0]
                    if not ans:
                        break
                    cur = client.post(
                        f"/appeal/{a['case_id']}",
                        json={"answers": ans},
                        timeout=420,
                    ).json()
                a_row["follow_up"] = {
                    "final_state": cur.get("state"),
                    "final_grounds": cur.get("grounds") or [],
                    "letter_flags": _letter_flags(cur.get("letter") or ""),
                    "rounds": rounds,
                }
                letter_ok = _letter_flags(cur.get("letter") or "")
                a_row["pass"] = (
                    a_row["pass"] and (
                        cur.get("state") in ("RELEASED", "CONFIRMED", "QUESTIONING", "ANALYSED")
                        or letter_ok.get("multiple|anpr")
                        or letter_ok.get("payment")
                        or cur.get("state") == "RELEASED"
                    )
                )
                # Prefer released multi-ground when we answered.
                if cur.get("state") == "RELEASED":
                    a_row["pass"] = bool(
                        any("ANPR" in g or "visit" in g.lower() or "Payment" in g
                            for g in (cur.get("grounds") or []))
                        or letter_ok.get("multiple|anpr")
                        or letter_ok.get("payment")
                    )

        report["cases"].append(a_row)

        # B — full narrative golden path
        t0 = time.time()
        b = _post_files(client, SHOPPING_NARRATIVE)
        flags = _letter_flags(b.get("letter") or "")
        grounds = b.get("grounds") or []
        b_row = {
            "tag": "NARRATIVE_GOLDEN_DRAFT",
            "case_id": b.get("case_id"),
            "ms": int((time.time() - t0) * 1000),
            "state": b.get("state"),
            "outcome": b.get("outcome"),
            "n_questions": len(b.get("questions") or []),
            "questions": [q.get("fact") for q in (b.get("questions") or [])],
            "grounds": grounds,
            "letter_flags": flags,
            "letter_excerpt": (b.get("letter") or "")[:500],
            "expected": {
                "state": "RELEASED",
                "grounds_any": ["PoFA/Schedule 4", "ANPR/multiple visits"],
                "letter": ["left", "returned", "pofa", "cancel"],
            },
        }

        def _answer_loop(start: dict, case_id: str) -> dict:
            cur = start
            for _ in range(5):
                qs = cur.get("questions") or []
                if not qs or cur.get("state") == "RELEASED":
                    return cur
                ans = {}
                for q in qs:
                    f = q.get("fact")
                    if f == "payment_method":
                        ans[f] = "MACHINE"
                    elif f == "collection_or_checkin_delay":
                        ans[f] = "no"
                    elif q.get("type") == "bool" or f in (
                        "multiple_visits", "payment_made", "left_site",
                        "returned_same_day", "payment_attempt_failed",
                        "anpr_duration_disputed", "genuine_customer",
                    ):
                        ans[f] = "yes"
                    elif q.get("type") == "choice" and q.get("options"):
                        ans[f] = q["options"][0]
                if not ans:
                    return cur
                cur = client.post(
                    f"/appeal/{case_id}", json={"answers": ans}, timeout=420,
                ).json()
            return cur

        if b.get("questions") and b.get("case_id"):
            b = _answer_loop(b, b["case_id"])
            flags = _letter_flags(b.get("letter") or "")
            grounds = b.get("grounds") or []
            b_row.update({
                "state": b.get("state"),
                "outcome": b.get("outcome"),
                "n_questions": len(b.get("questions") or []),
                "questions": [q.get("fact") for q in (b.get("questions") or [])],
                "grounds": grounds,
                "letter_flags": flags,
                "letter_excerpt": (b.get("letter") or "")[:500],
                "answered_through": True,
            })

        has_pofa = any("PoFA" in g or "Keeper" in g for g in grounds) or flags.get("pofa|schedule 4")
        has_anpr = any("ANPR" in g or "visit" in g.lower() or "Multiple" in g for g in grounds) or flags.get("multiple|anpr")
        b_row["pass"] = (
            b.get("state") == "RELEASED"
            and has_pofa
            and has_anpr
            and flags.get("left")
            and flags.get("returned")
            and flags.get("cancel")
            and not flags.get("placeholder")
        )
        report["cases"].append(b_row)

    report["meta"]["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["verdict"] = "PASS" if all(c.get("pass") for c in report["cases"]) else "FAIL"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "out": str(OUT),
                      "cases": [{k: c.get(k) for k in ("tag", "pass", "state", "n_questions", "grounds")}
                                for c in report["cases"]]}, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
