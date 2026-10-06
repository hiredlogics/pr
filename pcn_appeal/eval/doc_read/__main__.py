"""Phase 1 harness: upload a notice -> read it -> structured DocumentReadResult.

Ten existing notices x three runs, through the real intake and extraction path
(`api._ingest_upload`), with every provider call taken from the audit log.
Nothing downstream of the read runs: no questions, no narrative, no knowledge
base, no claim plan.

    python -m pcn_appeal.eval.doc_read --provider scripted
    python -m pcn_appeal.eval.doc_read --provider live      # needs API credit

`scripted` answers from each notice's own expected values. It proves call
counts, persistence and the front/back rules; it can NOT prove reading accuracy
or latency, and its report says so. Only `live` measures those.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "datasets" / "p9_v1" / "notice_only"
OUT = ROOT / "reports" / "doc_read"
FIELDS = ("operator_name", "pcn_number", "vrm", "parking_event_date",
          "notice_issue_date", "parking_location", "entry_time", "exit_time",
          "alleged_breach")
RUNS = 3

CPPLUS = {
    "operator_name": "LIVE_TEST Parking Ltd", "pcn_number": "FRONTEND_LIVE_TEST_CPPLUS",
    "vrm": "LT12EST", "parking_event_date": "01/06/2026", "notice_issue_date": "20/06/2026",
    "parking_location": "LIVE_TEST Retail Park", "entry_time": "10:00", "exit_time": "12:47",
    "alleged_breach": "Overstayed paid time",
}


def _front_text(v: dict) -> str:
    rows = [("Operator Name", "operator_name"), ("PCN Number", "pcn_number"),
            ("Vehicle Registration", "vrm"), ("Location", "parking_location"),
            ("Date of Contravention", "parking_event_date"), ("Date of Issue", "notice_issue_date"),
            ("Entry Time", "entry_time"), ("Exit Time", "exit_time"),
            ("Alleged Breach", "alleged_breach")]
    return "PARKING CHARGE NOTICE\n" + "\n".join(
        f"{label}: {v[k]}" for label, k in rows if v.get(k))


def _back_text(pcn: str) -> str:
    return (f"NOTICE TO KEEPER - REVERSE\nPCN {pcn}\nSchedule 4 Protection of Freedoms "
            "Act 2012\nIf you were not the driver you may pass this notice to the driver.\n"
            "Pay or appeal within 28 days of the date of issue.")


def notices() -> list[dict]:
    out = []
    for path in sorted(DATASET.glob("REF_*.json")):
        d = json.loads(path.read_text())
        exp = {k: d["expected"]["extraction"]["values"].get(k) for k in FIELDS}
        exp = {k: v for k, v in exp.items() if v not in (None, "")}
        out.append({"id": d["case_id"], "operator": d["operator"], "expected": exp})
    out.append({"id": "FIXTURE_CPPLUS", "operator": "LIVE_TEST", "expected": CPPLUS})
    return out[:10]


def _render(text: str, title: str) -> bytes:
    import io
    from pcn_appeal.eval.p17_live.p174_fixtures import render_page
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = render_page(text, Path(d) / "page.jpg", title=title)
        return p.read_bytes()


# ------------------------------------------------------------ comparison
def _norm(name: str, v) -> str:
    from pcn_appeal.engines.extraction import parse_uk_date
    if v is None:
        return ""
    if name in ("parking_event_date", "notice_issue_date"):
        d = parse_uk_date(v)
        return d.isoformat() if d else str(v).strip().lower()
    if name in ("vrm", "pcn_number"):
        return "".join(str(v).split()).upper()
    if name in ("entry_time", "exit_time"):
        return str(v).strip()[:5]
    return " ".join(str(v).split()).casefold()


def compare(result: dict, expected: dict) -> dict:
    out = {}
    for name in FIELDS:
        if name not in expected:
            continue
        got = (result["fields"].get(name) or {}).get("value")
        out[name] = {"expected": expected[name], "got": got,
                     "pass": _norm(name, got) == _norm(name, expected[name])}
    return out


# ---------------------------------------------------------- scripted model
class Scripted:
    """Answers from the notice's expected values. Counts calls; proves nothing
    about reading accuracy or latency."""
    SUPPORTS_IMAGES = True
    models = {"identity_verification": "scripted", "extraction": "scripted"}

    def __init__(self, expected: dict, pcn_conf: float = 0.97, back_pcn=None,
                 blind_classifier: bool = False):
        self.expected, self.pcn_conf, self.back_pcn = expected, pcn_conf, back_pcn
        self.blind_classifier = blind_classifier  # classifier cannot read the PCN

    def complete_json(self, *, task, system, user, images=None):
        e = self.expected
        if task == "classification":
            n = len(images or []) or 1
            rows = []
            for i in range(n):
                other = i > 0 and self.back_pcn
                pcn = self.back_pcn if other else e["pcn_number"]
                refs = {} if self.blind_classifier else {
                    "pcn_number": pcn, "vrm": "ZZ99ZZZ" if other else e["vrm"]}
                rows.append({"evidence_id": f"E{i + 1}", "document_type": "PRIVATE_PARKING_NOTICE",
                             "service_family": "PRIVATE_PARKING", "stage": "NOTICE_TO_KEEPER",
                             "confidence": 0.95, "references": refs})
            return {"documents": rows}
        if task == "page_references":
            other = self.back_pcn and "page2" in (user or "")
            return {"pcn_number": self.back_pcn if other else e["pcn_number"],
                    "vrm": "ZZ99ZZZ" if other else e["vrm"]}
        if task == "extraction":
            fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
                      for k, v in e.items()}
            fields["pcn_number"]["confidence"] = self.pcn_conf
            return {"fields": fields, "doc_types": {"E1": "NTK"}}
        if task == "identity_verification":
            return {"fields": {"pcn_number": {"candidate_value": e["pcn_number"],
                                              "read_status": "VERIFIED", "confidence": 0.95,
                                              "evidence_id": "E1", "page": 1}}}
        raise RuntimeError(f"unexpected task {task}")


# -------------------------------------------------------------------- run
def _case(pages: list[bytes]):
    from pcn_appeal.models import CaseFile, EvidenceItem
    case = CaseFile("doc-read")
    for i, img in enumerate(pages, 1):
        case.evidence[f"E{i}"] = EvidenceItem(f"E{i}", "OTHER", f"page{i}.jpg", text="", images=[img])
    return case


def _stages(case) -> dict:
    by = {"classification": 0.0, "extraction": 0.0, "verification": 0.0}
    calls = []
    for row in case.ai_calls:
        sec = (row.get("duration_ms") or 0) / 1000
        kind = {"classification": "classification", "page_references": "classification",
                "extraction": "extraction", "identity_verification": "verification"}.get(row["task"])
        if kind:
            by[kind] += sec
        calls.append({"task": row["task"], "model": row.get("model"), "seconds": round(sec, 3),
                      "status": row.get("status"), "images": row.get("images"),
                      "retries": row.get("retries", 0), "attempt_seconds": row.get("attempt_seconds")})
    return {"stage_seconds": {k: round(v, 3) for k, v in by.items()}, "calls": calls}


def read_once(llm, pages: list[bytes], expected: dict, *, case=None, pipe=None) -> dict:
    from fastapi import HTTPException
    from pcn_appeal import api
    from pcn_appeal.document_read import load_document_read_result
    from pcn_appeal.orchestrator import AppealPipeline
    case = case or _case(pages)
    pipe = pipe or AppealPipeline(llm)
    n0 = len(case.ai_calls)
    started = time.perf_counter()
    status, detail = "OK", None
    try:
        api._ingest_upload({"case": case, "pipe": pipe, "flags": []}, [])
    except HTTPException as exc:
        status, detail = f"HTTP_{exc.status_code}", exc.detail
    except Exception as exc:  # a provider failure is a result, not a crash
        status, detail = type(exc).__name__, str(exc)[:160]
    total = time.perf_counter() - started
    result = load_document_read_result(case) or {"fields": {}, "uncertain": []}
    fresh = _stages(type("C", (), {"ai_calls": case.ai_calls[n0:]}))
    return {"status": status, "detail": detail, "total_seconds": round(total, 3),
            **fresh, "model_calls": len(fresh["calls"]),
            "retries": sum(c["retries"] or 0 for c in fresh["calls"]),
            "fields": compare(result, expected), "uncertain": result.get("uncertain"),
            "document_read_result": result, "case": case, "pipe": pipe}


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("live", "scripted"), default="scripted")
    ap.add_argument("--runs", type=int, default=RUNS)
    args = ap.parse_args()

    from pcn_appeal.llm import default_client
    rows, scenarios = [], {}
    lib = notices()
    pages = {n["id"]: (_render(_front_text(n["expected"]), "FRONT"),
                       _render(_back_text(n["expected"]["pcn_number"]), "REVERSE")) for n in lib}

    if args.provider == "live":
        probe = default_client()
        if type(probe).__name__ != "OpenAIClient":
            print(f"live mode needs a working OpenAI client; got {type(probe).__name__} "
                  "(the key is missing, rejected or out of credit). Nothing was measured.")
            return 2

    def llm_for(n, **kw):
        if args.provider == "scripted":
            return Scripted(n["expected"], **kw)
        return default_client()

    for n in lib:
        for run in range(1, args.runs + 1):
            r = read_once(llm_for(n), [pages[n["id"]][0]], n["expected"])
            rows.append({"notice": n["id"], "operator": n["operator"], "run": run,
                         **{k: v for k, v in r.items() if k not in ("case", "pipe")}})
            if r["status"] != "OK" and "insufficient" in str(r["detail"]).lower() + str(r["status"]):
                break

    first = lib[0]
    f, b = pages[first["id"]]
    other = lib[1]

    def scen(name, r):
        scenarios[name] = {k: v for k, v in r.items() if k not in ("case", "pipe", "document_read_result")}
        return r

    scen("A_front_only", read_once(llm_for(first), [f], first["expected"]))
    scen("B_front_plus_correct_back", read_once(llm_for(first), [f, b], first["expected"]))
    scen("C_front_plus_wrong_back", read_once(
        llm_for(first, back_pcn=other["expected"]["pcn_number"]) if args.provider == "scripted"
        else llm_for(first), [f, pages[other["id"]][1]], first["expected"]))

    base = read_once(llm_for(first), [f], first["expected"])
    d = read_once(base["pipe"].llm, [f], first["expected"], case=base["case"], pipe=base["pipe"])
    scenarios["D_same_unchanged_image_again"] = {"model_calls": d["model_calls"], "status": d["status"],
        "same_result": d["document_read_result"]["fields"] == base["document_read_result"]["fields"]}
    from pcn_appeal.orchestrator import AppealPipeline
    e = read_once(base["pipe"].llm, [f], first["expected"], case=base["case"],
                  pipe=AppealPipeline(base["pipe"].llm))
    scenarios["E_refresh_resume_new_pipeline"] = {"model_calls": e["model_calls"], "status": e["status"],
        "same_result": e["document_read_result"]["fields"] == base["document_read_result"]["fields"]}
    if args.provider == "scripted":
        scen("F_low_confidence_critical_field", read_once(
            Scripted(first["expected"], pcn_conf=0.6, blind_classifier=True), [f], first["expected"]))
    else:
        scenarios["F_low_confidence_critical_field"] = "not induced on a live model"

    # ----------------------------------------------------------- report
    ok = [r for r in rows if r["status"] == "OK"]
    total = [r["total_seconds"] for r in ok]
    acc = {k: [] for k in FIELDS}
    for r in ok:
        for k, v in r["fields"].items():
            acc[k].append(v["pass"])
    drift = {}
    for n in lib:
        got = {tuple(sorted((k, str(v["got"])) for k, v in r["fields"].items()))
               for r in ok if r["notice"] == n["id"]}
        drift[n["id"]] = len(got) > 1
    report = {
        "provider": args.provider, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accuracy_and_latency_are_measured": args.provider == "live",
        "runs": rows, "scenarios": scenarios,
        "field_accuracy": {k: (sum(v), len(v)) for k, v in acc.items() if v},
        "random_change_between_runs": drift,
        "latency_seconds": {s: {"p50": _pct([r["stage_seconds"][s] for r in ok], .5),
                                "p95": _pct([r["stage_seconds"][s] for r in ok], .95)}
                            for s in ("classification", "extraction", "verification")}
                           | {"total": {"p50": _pct(total, .5), "p95": _pct(total, .95)}},
        "model_calls_per_case": {"min": min((r["model_calls"] for r in ok), default=None),
                                 "max": max((r["model_calls"] for r in ok), default=None),
                                 "mean": round(statistics.mean(r["model_calls"] for r in ok), 2) if ok else None},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.provider}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("provider", "accuracy_and_latency_are_measured",
          "field_accuracy", "random_change_between_runs", "latency_seconds",
          "model_calls_per_case", "scenarios")}, indent=1, default=str))
    print("report:", path)
    return 0 if ok or args.provider == "live" else 1


if __name__ == "__main__":
    raise SystemExit(main())
