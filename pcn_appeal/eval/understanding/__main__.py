"""Phase 2 harness: 20 customer accounts -> understood packet or one question.

    python -m pcn_appeal.eval.understanding --provider live    # needs API credit
    python -m pcn_appeal.eval.understanding --provider demo    # offline reader

Every case goes through the production semantic boundary
(`SemanticCaseResolver.resolve`, as the answer loop does), the clarification is
answered with the case's scripted reply, and the second reading must resolve.

`demo` runs the offline reference reader (no model). It exists to show what the
no-model path does, NOT how a model understands: its report is labelled so. Only
`live` measures understanding, and it refuses to run on a stand-in client.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from .cases import CASES, NOTICE

OUT = Path(__file__).resolve().parents[3] / "reports" / "understanding"
LEAK = ("KB-", "PoFA", "Schedule 4", "claim plan", "appeal ground")


def _case(text: str):
    from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
    case = CaseFile("understanding")
    for name, value in NOTICE.items():
        case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
    case.raw_answers["narrative"] = text
    return case


def _blob(packet: dict) -> str:
    parts = [packet.get("summary") or ""]
    for key in ("events", "narrative_atoms"):
        for row in packet.get(key) or []:
            parts += [str(row.get("description") or ""), str(row.get("proposition") or ""),
                      str(row.get("source_text") or "")]
    for c in packet.get("concepts") or []:
        parts += [str(c.get("concept") or ""), str(c.get("source_text") or "")]
    for u in packet.get("uncertainties") or []:
        parts += [str(u.get("about") or ""), str(u.get("source_text") or "")]
    return " ".join(parts).lower()


def _items(packet: dict):
    for key in ("events", "narrative_atoms", "concepts"):
        for row in packet.get(key) or []:
            text = " ".join(str(row.get(k) or "") for k in
                            ("description", "proposition", "source_text", "concept")).lower()
            yield text, str(row.get("polarity") or "AFFIRMED").upper()


def score_meaning(case: dict, packet: dict) -> dict:
    blob = _blob(packet)
    keep = [any(tok in blob for tok in group) for group in case.get("keep") or []]
    held = []
    for alts, polarity in case.get("held") or []:
        hit = [pol for text, pol in _items(packet) if any(a in text for a in alts)]
        ok = polarity in hit
        if polarity == "UNCERTAIN" and not ok:
            ok = any(any(a in (u.get("about", "") + u.get("source_text", "")).lower() for a in alts)
                     for u in packet.get("uncertainties") or [])
        held.append(ok)
    leaked = [w for w in LEAK if w.lower() in json.dumps(packet).lower()]
    return {"specifics_kept": all(keep) if keep else True,
            "polarity_kept": all(held) if held else True, "leaked": leaked}


def _ai_rows(case, start: int) -> list[dict]:
    return [r for r in case.ai_calls[start:] if r.get("task") == "semantic_extraction"]


def run_case(spec: dict, make_llm) -> dict:
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.semantics import understanding as U
    from pcn_appeal.semantics.resolver import SemanticCaseResolver

    case = _case(spec["text"])
    pipe = AppealPipeline(make_llm())
    case.ensure_run("understanding")
    row = {"id": spec["id"], "form": spec["form"], "input": spec["text"],
           "context": {k: NOTICE[k] for k in ("alleged_breach", "parking_location")},
           "expected": spec["expect"]}

    def read():
        n0 = len(case.ai_calls)
        t0 = time.perf_counter()
        try:
            SemanticCaseResolver.resolve(case, llm=pipe.llm, narrative=case.raw_answers["narrative"])
            err = None
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:120]}"
        rows = _ai_rows(case, n0)
        return {"seconds": round(time.perf_counter() - t0, 3), "model_calls": len(rows),
                "model_seconds": round(sum((r.get("duration_ms") or 0) for r in rows) / 1000, 3),
                "retries": sum(r.get("retries") or 0 for r in rows), "error": err}

    t1 = read()
    p1 = U.load_packet(case) or {}
    row.update(turn1={**t1, "status": p1.get("status"), "summary": p1.get("summary"),
                      "clarification": p1.get("clarification"), "notes": p1.get("notes"),
                      "semantic_mode": p1.get("semantic_mode"),
                      "packet": {k: p1.get(k) for k in
                                 ("concepts", "events", "narrative_atoms", "relationships",
                                  "uncertainties")}},
               **{f"meaning_{k}": v for k, v in score_meaning(spec, p1).items()})

    got = p1.get("status")
    exp = spec["expect"]
    row["status_ok"] = exp == "EITHER" and got in ("UNDERSTOOD", "NEEDS_CLARIFICATION") or got == exp
    row["unnecessary_clarification"] = exp == "UNDERSTOOD" and got == "NEEDS_CLARIFICATION"
    row["missed_ambiguity"] = exp == "NEEDS_CLARIFICATION" and got == "UNDERSTOOD"
    row["one_clarification"] = (len(U.pending_question(case)) == 1) if got == "NEEDS_CLARIFICATION" else None

    row["resolved"] = row["repeat_attempted"] = row["repeat_shown"] = None
    if got == "NEEDS_CLARIFICATION" and spec.get("answer"):
        first_q = p1["clarification"]["question"]
        pipe.questions.record_answer(case, p1["clarification"]["fact"], spec["answer"])
        t2 = read()
        p2 = U.load_packet(case) or {}
        notes = " ".join(p2.get("notes") or [])
        row["turn2"] = {**t2, "answer": spec["answer"], "status": p2.get("status"),
                        "summary": p2.get("summary"), "clarification": p2.get("clarification"),
                        "notes": p2.get("notes")}
        row["resolved"] = p2.get("status") == "UNDERSTOOD"
        row["repeat_attempted"] = "already asked" in notes
        row["repeat_shown"] = bool(p2.get("clarification")) and U.same_enquiry(
            p2["clarification"]["question"], first_q)
        row["answer_read_with_account"] = bool(
            case.raw_answers.get("narrative") == spec["text"]
            and U.answered(U.clarification_history(case)))
    score2 = score_meaning(spec, U.load_packet(case) or {}) if row["resolved"] else None
    row["meaning_after_answer"] = score2

    ok = bool(row["status_ok"] and not t1["error"] and not row["meaning_leaked"]
              and (exp != "NEEDS_CLARIFICATION" or got == "NEEDS_CLARIFICATION") )
    if got == "UNDERSTOOD":
        ok = ok and row["meaning_specifics_kept"] and row["meaning_polarity_kept"]
    if got == "NEEDS_CLARIFICATION":
        ok = ok and bool(row["one_clarification"]) and (
            spec.get("answer") is None or (row["resolved"] and not row["repeat_shown"]))
        if score2:
            ok = ok and score2["specifics_kept"] and score2["polarity_kept"]
    row["pass"] = ok
    return row


def _pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("live", "demo"), default="demo")
    args = ap.parse_args()
    from pcn_appeal.llm import default_client
    if args.provider == "live":
        probe = default_client()
        if type(probe).__name__ != "OpenAIClient":
            print(f"live mode needs a working OpenAI client; got {type(probe).__name__} "
                  "(the key is missing, rejected or out of credit). Nothing was measured.")
            return 2
    rows = [run_case(c, default_client) for c in CASES]

    exp_u = [r for r in rows if r["expected"] == "UNDERSTOOD"]
    exp_n = [r for r in rows if r["expected"] == "NEEDS_CLARIFICATION"]
    secs = [r["turn1"]["seconds"] for r in rows]
    report = {
        "provider": args.provider, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "understanding_is_measured": args.provider == "live",
        "note": ("offline reference reader; it has no model and cannot judge ambiguity"
                 if args.provider == "demo" else "live model"),
        "cases": rows,
        "summary": {
            "passed": sum(r["pass"] for r in rows), "of": len(rows),
            "unnecessary_clarifications": sum(bool(r["unnecessary_clarification"]) for r in rows),
            "over": len(exp_u),
            "missed_ambiguities": sum(bool(r["missed_ambiguity"]) for r in rows),
            "missed_of": len(exp_n),
            "repeated_questions_shown": sum(bool(r["repeat_shown"]) for r in rows),
            "repeats_attempted_by_model": sum(bool(r["repeat_attempted"]) for r in rows),
            "model_calls_per_input": sorted({r["turn1"]["model_calls"] for r in rows}),
            "turn1_seconds": {"p50": _pct(secs, .5), "p95": _pct(secs, .95)},
            "retries": sum(r["turn1"]["retries"] for r in rows),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.provider}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report["summary"], indent=1))
    for r in rows:
        print(f"{r['id']:>2} {r['form'][:34]:34} exp={r['expected'][:5]:5} got={str(r['turn1']['status'])[:5]:5} "
              f"calls={r['turn1']['model_calls']} pass={r['pass']}")
    print("report:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
