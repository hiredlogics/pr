"""Run every case in case_matrix.py through the real pipeline and report.

Usage:
    PYTHONPATH=.:scratchpad .venv/bin/python scratchpad/run_matrix.py [--limit N]
                                                                     [--cohort SEEN|UNSEEN]
                                                                     [--offline]

Default is the LIVE pipeline against the configured model, because the offline
test double cannot write a letter (it emits one sentence per building block and
dedupes shared text), so it can tell you nothing about appeal quality. Pass
--offline to exercise only the fact/ground layers without model calls.

What is checked per case, and why:

  theory        the grounds selected must match the case theory. A payment case
                that argues "no contract" has misread the case; a generic
                signage/landowner letter has read nothing (brief §1).
  must_not      grounds that would be WRONG on these facts. Retrieval may
                REACH them; eligibility must refuse them (brief §2, §3).
  questions     only questions whose answer could change the appeal, and never
                one the customer already answered (brief §4).
  contradiction no fact may be held in two opposed states at once (brief §5).
  letter        a released letter must say why THIS charge should be cancelled;
                a generic one is a failure even if every validator passed
                (brief §6, §8).

Results are written as JSON so they can be diffed between runs, and as a table
on stdout grouped by the layer that failed first - the brief's §12 rule is to
fix the first bad layer, not the symptom.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from case_matrix import build  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(__file__), "matrix_runs")

# Grounds that answer "the operator has not proved its own case" rather than
# this customer's case. Fine as SUPPORT alongside a case-specific ground; a
# letter that leads with ONLY these has not found the case theory (brief §1,
# §6). KEEPER-01 (no identified driver) is excluded deliberately - it is a
# legitimate independent ground on facts, not evidence of a missed theory, and
# flagging it caused false positives on every driver-liability case.
GENERIC_GROUNDS = {"KB-LAND-01", "KB-SIGN-01", "KB-SIGN-02", "KB-REC-01",
                   "KB-AUTH-01", "KB-AUTH-02"}

# theory -> ground id fragments that would satisfy it. Fragments, not exact
# ids, because the KB owns the ids and this is a report, not a gate.
THEORY_GROUNDS = {
    "payment": ("PAY", "KEY", "TARIFF"),
    "keying": ("KEY", "PAY"),
    "no-contract": ("CON",),
    "grace": ("GRACE", "TIME", "ACT", "DROP"),
    "multiple-visits": ("ANPR", "TIME"),
    "presence-not-parking": ("TIME", "ANPR"),
    "immobilised": ("BREAK", "ACT"),
    "bay-entitlement": ("BAY", "EQ", "CHILD"),
    "signage": ("SIGN", "CON"),
    "observation-window": ("OBS", "TIME", "BAY", "SIGN"),
    "permit": ("PERMIT", "AUTH", "RES"),
    "authority": ("AUTH", "LAND", "RES", "LEASE"),
    "lease": ("LEASE", "RES", "LAND"),
    "driver-identity": ("POFA", "KEEPER", "DRIVER"),
    "notice-route": ("POFA", "NOTICE", "KEEPER"),
    "pofa-late": ("POFA",),
    "allegation-particulars": ("PART", "ALLEG", "REC", "TIME", "ANPR"),
    "facility-failure": ("PAY", "TARIFF", "ACT"),
    "penalty": ("PEN", "GPEOL", "CHARGE"),
    "unknown": (),
}


def make_client(offline: bool):
    """The real FastAPI app, in-process - the exact code path the live server
    runs (/appeal, /appeal/{case_id}), so this exercises the actual contract
    rather than a hand-rolled call into the orchestrator.
    """
    from fastapi.testclient import TestClient
    if offline:
        os.environ.pop("OPENAI_API_KEY", None)
    from pcn_appeal import api
    return TestClient(api.app)


def internal(client, case_id):
    """The internal view of a case the customer payload deliberately omits
    (module ids, routes, facts) - read from the in-process api.CASES store
    rather than re-deriving case setup."""
    from pcn_appeal import api
    rec = api.CASES.get(case_id) or {}
    case = rec.get("case")
    out = rec.get("output")
    facts = {n: f.value for n, f in (getattr(case, "facts", None) or {}).items()
            if getattr(f, "usable", False)} if case else {}
    module_ids = list(getattr(getattr(out, "pack", None), "module_ids", None) or [])
    plan = getattr(getattr(out, "pack", None), "claim_plan", None) or {}
    approved = list(plan.get("approved") or module_ids)
    asked = list(getattr(case, "asked_questions", None) or []) if case else []
    return {"facts": facts, "approved": approved, "asked": asked}


def answer_for(q, spec):
    """A plausible answer, so a journey continues instead of stalling.

    Never invents a fact direction the account did not raise. A field the
    notice already carries (pcn_number, vrm, dates) is echoed back from the
    spec rather than guessed; a boolean the account never touched is refused
    rather than defaulted, because a careless True/False here would be the
    HARNESS inventing a fact, not the pipeline - exactly the failure mode
    brief §2 is about, just one layer up.
    """
    fact = q.get("fact") or ""
    notice_echo = {"pcn_number": spec.get("ref") or spec.get("notice_number"),
                   "vrm": spec.get("vrm"), "site_postcode": None}
    if fact in notice_echo and notice_echo[fact]:
        return notice_echo[fact]
    choices = q.get("choices") or q.get("options") or []
    if choices:
        first = choices[0]
        return first.get("value", first) if isinstance(first, dict) else first
    kind = (q.get("kind") or q.get("type") or "").lower()
    if kind in ("bool", "boolean", "yes_no"):
        return None          # refuse: see docstring
    if kind in ("number", "integer", "minutes", "int", "float"):
        return 5
    if kind == "date":
        return "2026-08-20"
    return "I do not have that to hand."


CONTRADICTIONS = [
    # (fact A, fact B, why holding both is incoherent)
    ("overstay_min", "permitted_period_ended",
     lambda a, b: isinstance(a, (int, float)) and a <= 0 and b is True,
     "no overstay recorded yet the permitted period is said to have ended"),
    ("no_parking_took_place", "payment_made",
     lambda a, b: a is True and b is True,
     "never parked yet paid to park"),
    ("left_site", "no_parking_took_place",
     lambda a, b: a is False and b is True,
     "did not leave the site yet never parked"),
]


def check(payload, inner, expect, theory_checks=True):
    """Findings for one case, each tagged with the layer that owns it.

    theory_checks=False in --offline mode: the offline test double (3
    hardcoded module branches, one sentence per PP- block) cannot thread
    case-specific grounds, so THEORY findings there are harness noise, not a
    product defect. FACTS/contradiction checks are real either way - they
    read verified facts, not drafted prose.
    """
    bad = []
    facts = inner["facts"]
    approved = inner["approved"]
    asked = inner["asked"]
    letter = payload.get("letter") or ""

    for a, b, pred, why in CONTRADICTIONS:
        if a in facts and b in facts and pred(facts[a], facts[b]):
            bad.append(("FACTS", f"{why} ({a}={facts[a]!r}, {b}={facts[b]!r})"))

    for g in expect.get("must_not") or ():
        if g in approved:
            bad.append(("ELIGIBILITY", f"argued {g}, which these facts exclude"))
    for f in expect.get("must_not_fact") or ():
        if facts.get(f) is not None:
            bad.append(("FACTS", f"invented {f}={facts[f]!r} from context alone"))

    theory = expect.get("theory")
    if theory_checks:
        wanted = THEORY_GROUNDS.get(theory, ())
        if wanted and approved:
            if not any(frag in g for g in approved for frag in wanted):
                bad.append(("THEORY", f"theory is {theory!r} but argued {approved}"))
        if approved and all(g in GENERIC_GROUNDS for g in approved) and theory != "unknown":
            bad.append(("THEORY", f"only operator-proof grounds for a {theory} case: "
                                  f"{approved}"))

    if expect.get("expect_question") and not (asked or payload.get("questions")):
        bad.append(("QUESTIONS", "a silent account asked nothing and proceeded"))

    if letter:
        body = " ".join(letter.split())
        if len(body) < 200:
            bad.append(("LETTER", f"letter is only {len(body)} characters"))
    return bad, {"grounds": approved, "asked": asked, "letter_chars": len(letter),
                 "state": payload.get("state"), "theory": theory}


def run_one(client, spec, max_rounds=6):
    """Drive POST /appeal then POST /appeal/{case_id} exactly as a customer's
    browser would, answering only the questions the server actually asks."""
    r = client.post("/appeal", json={
        "documents": [{"evidence_id": "E1", "kind": "PCN",
                       "filename": "notice.txt", "text": spec["notice_text"]}],
        "narrative": spec["account"]})
    r.raise_for_status()
    payload = r.json()
    case_id = payload.get("case_id")
    rounds = 0
    while payload.get("questions") and rounds < max_rounds:
        answers = {}
        for q in payload["questions"]:
            if not q.get("fact"):
                continue
            a = answer_for(q, spec)
            if a is not None:
                answers[q["fact"]] = a
        if not answers:
            break
        r = client.post(f"/appeal/{case_id}", json={"answers": answers})
        r.raise_for_status()
        payload = r.json()
        rounds += 1
    return payload, case_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cohort", choices=["SEEN", "UNSEEN"])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--tag", default=time.strftime("%Y%m%dT%H%M%S"))
    args = ap.parse_args()

    cases = build()
    if args.cohort:
        cases = [c for c in cases if c["cohort"] == args.cohort]
    if args.limit:
        cases = cases[:args.limit]

    client = make_client(args.offline)
    os.makedirs(OUT_DIR, exist_ok=True)
    rows, layers = [], Counter()

    for i, spec in enumerate(cases, 1):
        started = time.time()
        try:
            payload, case_id = run_one(client, spec)
            inner = internal(client, case_id)
            bad, info = check(payload, inner, spec["expect"],
                              theory_checks=not args.offline)
            err = None
        except Exception as exc:
            bad = [("CRASH", f"{type(exc).__name__}: {exc}")]
            info = {"traceback": traceback.format_exc()[-1200:]}
            err = str(exc)
        for layer, _ in bad:
            layers[layer] += 1
        rows.append({"case_id": spec["case_id"], "cohort": spec["cohort"],
                     "family": spec["family"], "operator": spec["operator"],
                     "account": spec["account"], "findings": bad,
                     "seconds": round(time.time() - started, 1), **info})
        mark = "ok " if not bad else "XX "
        print(f"{mark}[{i:3d}/{len(cases)}] {spec['case_id'][:52]:52s} "
              f"{info.get('grounds') or err or ''}")
        for layer, msg in bad:
            print(f"        {layer}: {msg}")

    path = os.path.join(OUT_DIR, f"matrix-{args.tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"cases": len(cases), "rows": rows,
                   "by_layer": dict(layers)}, fh, indent=2, default=str)

    clean = sum(1 for r in rows if not r["findings"])
    print(f"\n{clean}/{len(rows)} clean")
    if layers:
        print("first bad layer (brief §12 - fix the earliest):")
        for layer in ("CRASH", "FACTS", "ELIGIBILITY", "THEORY", "QUESTIONS", "LETTER"):
            if layers.get(layer):
                print(f"  {layer:12s} {layers[layer]}")
    print(f"\nwritten: {path}")
    return 0 if clean == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
