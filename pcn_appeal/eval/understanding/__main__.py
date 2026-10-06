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

from .cases import CASES, HOLDOUTS, NOTICE

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
            "polarity_kept": all(held) if held else True,
            "order_kept": _order_kept(case.get("order"), packet),
            "cause_kept": _cause_kept(case.get("cause"), packet),
            "leaked": leaked}


def _rows(packet: dict) -> list[tuple[str, dict]]:
    """Every event and atom with its searchable text, in the packet's own order."""
    out = []
    for key in ("events", "narrative_atoms"):
        for row in packet.get(key) or []:
            text = " ".join(str(row.get(k) or "") for k in
                            ("description", "proposition", "source_text")).lower()
            out.append((text, row))
    return out


def _order_kept(order, packet: dict) -> bool:
    """The stated order survives as event order or as a PRECEDES/FOLLOWS edge."""
    if not order:
        return True
    rows = _rows(packet)

    def find(group):
        return [i for i, (text, _r) in enumerate(rows) if any(a in text for a in group)]
    spots = [find(g) for g in order]
    if any(not x for x in spots):
        return False
    ids = lambda group: {str(r.get("event_id") or r.get("atom_id")) for text, r in rows
                         if any(a in text for a in group)}
    for a, b in zip(order, order[1:]):
        edge = any((rel.get("relationship") == "PRECEDES" and rel.get("source_id") in ids(a)
                    and rel.get("target_id") in ids(b))
                   or (rel.get("relationship") == "FOLLOWS" and rel.get("source_id") in ids(b)
                       and rel.get("target_id") in ids(a))
                   for rel in packet.get("relationships") or [])
        if not (edge or min(find(a)) < max(find(b))):
            return False
    return True


def _cause_kept(cause, packet: dict) -> bool:
    if not cause:
        return True
    causes, effects = cause
    rows = _rows(packet)
    ids = lambda group: {str(r.get("event_id") or r.get("atom_id")) for text, r in rows
                         if any(a in text for a in group)}
    if any(rel.get("relationship") == "CAUSES" and rel.get("source_id") in ids(causes)
           and rel.get("target_id") in ids(effects) for rel in packet.get("relationships") or []):
        return True
    return any(any(a in text for a in causes) and any(b in text for b in effects)
               for text, _r in rows)


def _ai_rows(case, start: int) -> list[dict]:
    return [r for r in case.ai_calls[start:] if r.get("task") == "semantic_extraction"]


def run_case(spec: dict, make_llm, provider: str = "demo") -> dict:
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.semantics import understanding as U
    from pcn_appeal.semantics.resolver import SemanticCaseResolver

    case = _case(spec["text"])
    pipe = AppealPipeline(make_llm())
    case.ensure_run("understanding")
    row = {"id": spec["id"], "form": spec["form"], "input": spec["text"],
           "provider": provider, "ambiguity": spec.get("ambiguity"), "tags": spec.get("tags", ""),
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
        try:
            raw = json.loads(case.raw_answers.get("_doc_read_memo") or "{}").get(
                "semantic_extraction", {}).get("output")
        except Exception:
            raw = None
        return {"model_response": raw, "seconds": round(time.perf_counter() - t0, 3), "model_calls": len(rows),
                "model_seconds": round(sum((r.get("duration_ms") or 0) for r in rows) / 1000, 3),
                "retries": sum(r.get("retries") or 0 for r in rows), "error": err}

    t1 = read()
    p1 = U.load_packet(case) or {}
    degraded = next((a.get("llm_degraded_reason") for a in reversed(case.audit)
                     if a.get("event") == "semantic_concepts"), "")
    t1["degraded_reason"] = degraded
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
    row["missed_ambiguity"] = exp == "NEEDS_CLARIFICATION" and bool(p1.get("ready_for_knowledge"))
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
    row["failure_reasons"] = _reasons(spec, row, p1, score2)
    row["pass"] = ok and not row["failure_reasons"]
    return row


def _reasons(spec: dict, row: dict, p1: dict, score2) -> list[str]:
    out = []
    exp, got = spec["expect"], p1.get("status")
    if row["turn1"].get("error"):
        out.append(f"error: {row['turn1']['error']}")
    if row["turn1"].get("semantic_mode") != "LIVE" and row.get("provider") == "live":
        out.append("the model did not read this input")
    if row["unnecessary_clarification"]:
        out.append("unnecessary clarification: asked about an account that was already clear")
    if row["missed_ambiguity"]:
        out.append("missed material ambiguity: certified an account it could not safely read")
    if exp == "NEEDS_CLARIFICATION" and got == "UNRESOLVED":
        out.append("material ambiguity could not be asked about (UNRESOLVED, not ready)")
    if spec.get("ambiguity") == "MATERIAL" and p1.get("ready_for_knowledge"):
        out.append("READY_FOR_KNOWLEDGE with a material ambiguity unresolved")
    m = {k: v for k, v in row.items() if k.startswith("meaning_")}
    final = score2 if score2 else None
    shown = final or {"specifics_kept": row["meaning_specifics_kept"],
                      "polarity_kept": row["meaning_polarity_kept"],
                      "order_kept": row["meaning_order_kept"],
                      "cause_kept": row["meaning_cause_kept"],
                      "leaked": row["meaning_leaked"]}
    if got == "UNDERSTOOD" or final:
        if not shown["specifics_kept"]:
            out.append("lost specificity: a stated detail is missing from the packet")
        if not shown["polarity_kept"]:
            out.append("lost negation or uncertainty")
        if not shown["order_kept"]:
            out.append("lost temporal order")
        if not shown["cause_kept"]:
            out.append("lost cause and effect")
    if shown["leaked"]:
        out.append(f"legal or KB content in the packet: {shown['leaked']}")
    if got == "NEEDS_CLARIFICATION":
        if not row["one_clarification"]:
            out.append("more or fewer than one pending clarification")
        if spec.get("answer") and not row["resolved"]:
            out.append("the answer did not resolve the clarification")
        if row["repeat_shown"]:
            out.append("repeated clarification shown to the customer")
    return out


def _by_tag(rows):
    out: dict[str, list[int]] = {}
    for r in rows:
        for tag in str(r.get("tags") or "").split():
            out.setdefault(tag, [0, 0])
            out[tag][1] += 1
            out[tag][0] += bool(r["pass"])
    return {k: f"{a}/{b}" for k, (a, b) in sorted(out.items())}


def _pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("live", "demo"), default="demo")
    ap.add_argument("--set", choices=("fixtures", "holdout", "both"), default="fixtures",
                    dest="which")
    args = ap.parse_args()
    from pcn_appeal.llm import default_client
    if args.provider == "live":
        probe = default_client()
        if type(probe).__name__ != "OpenAIClient":
            print(f"live mode needs a working OpenAI client; got {type(probe).__name__} "
                  "(the key is missing, rejected or out of credit). Nothing was measured.")
            return 2
    specs = {"fixtures": CASES, "holdout": HOLDOUTS, "both": CASES + HOLDOUTS}[args.which]
    rows = [run_case(c, default_client, args.provider) for c in specs]
    if args.provider == "live":
        unread = [r for r in rows if r["turn1"].get("semantic_mode") != "LIVE"]
        if unread:
            reason = next((r["turn1"].get("degraded_reason") for r in unread
                           if r["turn1"].get("degraded_reason")), "unknown")
            print(f"INVALID RUN: the model did not read {len(unread)} of {len(rows)} inputs; "
                  f"the offline reader answered instead. First cause: {reason[:240]}\n"
                  "Nothing was measured and no report was written.")
            return 2

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
            "by_ability": _by_tag(rows),
        },
        "set": args.which,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.provider}_{args.which}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report["summary"], indent=1))
    for r in rows:
        print(f"{r['id']:>2} {r['form'][:34]:34} exp={r['expected'][:5]:5} got={str(r['turn1']['status'])[:5]:5} "
              f"calls={r['turn1']['model_calls']} pass={r['pass']}")
    print("report:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
