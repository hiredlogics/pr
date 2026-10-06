"""Run the Phase 3A fixtures through knowledge retrieval and score the result.

`current` drives the production path exactly as the pipeline does:
KnowledgeMatcher.match(case) then AnalysisEngine._candidates(...). `candidates`
there is the window shown to Case Intelligence (capped); `connected` is the part
of the match the case's facts, evidence, allegation or semantic material point at.

A module being retrieved never means it is supported. Nothing here scores
eligibility.
"""
from __future__ import annotations

import json
import statistics
import time
from typing import Any, Callable, Optional

from pcn_appeal.engines.analysis import AnalysisEngine
from pcn_appeal.engines.knowledge_matcher import (OPEN, RELEVANT, SUPPORTED,
                                                  KnowledgeMatcher)
from pcn_appeal.engines.reasoning import ReasoningEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (CaseFile, EvidenceItem, Fact, FactSource,
                               FactStatus, SourceKind)
from pcn_appeal.semantics import understanding as U

from .fixtures import FIXTURES, GROUPS

_KG: Optional[KnowledgeGraph] = None
_ENGINE: Optional[AnalysisEngine] = None


def kg() -> KnowledgeGraph:
    global _KG
    if _KG is None:
        _KG = KnowledgeGraph()
    return _KG


def engine() -> AnalysisEngine:
    """The analysis engine wired as the pipeline wires it: the reasoning engine's
    retriever, no model."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = AnalysisEngine(kg(), None, retriever=ReasoningEngine(kg()).retriever)
    return _ENGINE


def build_case(fx: dict, *, customer: bool = True) -> CaseFile:
    """A case as it stands after Phases 1-2: authoritative facts, the semantic
    packet, and the semantic state stored where the pipeline stores it - under the
    live keys when the customer stream is ready, under `_held` when it is not."""
    case = CaseFile(f"fx-{fx['id']}")
    for i, (name, value) in enumerate(sorted(fx["facts"].items())):
        case.put(Fact(f"F{i}", name, value, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "fixture#1")))
    for i, kind in enumerate(fx.get("evidence_kinds") or []):
        case.evidence[f"EV{i}"] = EvidenceItem(f"EV{i}", kind, f"{kind.lower()}.dat")
    packet = fx.get("packet")
    if packet is not None and customer:
        U.remember(case, packet)
        ready = U.is_ready(packet)
        suffix = "" if ready else "_held"
        state = {k: packet.get(k) or [] for k in
                 ("concepts", "events", "narrative_atoms", "relationships")}
        state["material_relevance"] = []
        case.raw_answers["_semantic_case_state" + suffix] = json.dumps(state)
        case.raw_answers["_semantic_narrative_atoms" + suffix] = json.dumps(
            state["narrative_atoms"])
    return case


def run_current(fx: dict, *, customer: bool = True) -> dict:
    case = build_case(fx, customer=customer)
    facts = case.fact_view()
    t0 = time.perf_counter()
    match = KnowledgeMatcher(kg()).match(case, facts)
    window = engine()._candidates(case, "", facts, match)
    ms = (time.perf_counter() - t0) * 1000
    status = {m: c.status for m, c in match.candidates.items()}
    connected = sorted(m for m, s in status.items() if s in (SUPPORTED, RELEVANT))
    return {"candidates": [m.module_id for m in window],
            "connected": connected, "status": status, "ms": ms}


_RETRIEVER = None


def run_new(fx: dict, *, customer: bool = True) -> dict:
    """The retrieval layer itself: what knowledge retrieval returns, before any
    eligibility pre-filter or window is applied."""
    global _RETRIEVER
    from pcn_appeal.engines.knowledge_retrieval import KnowledgeRetrieval, build_input
    if _RETRIEVER is None:
        _RETRIEVER = KnowledgeRetrieval(kg())
    case = build_case(fx, customer=customer)
    t0 = time.perf_counter()
    res = _RETRIEVER.retrieve(build_input(kg(), case))
    ms = (time.perf_counter() - t0) * 1000
    return {"candidates": res.module_ids, "connected": res.module_ids, "ms": ms,
            "detail": [c.as_dict() for c in res.candidates], "diagnostics": res.diagnostics}


def run_window(fx: dict, *, customer: bool = True) -> dict:
    """What Case Intelligence is shown: production's window, as wired now."""
    return run_current(fx, customer=customer)


def retrieved(result: dict, mode: str) -> set[str]:
    """`window`: what Case Intelligence is shown. `connected`: what the case's
    material points at. `new` results carry a single candidate list."""
    if mode == "connected":
        return set(result["connected"])
    return set(result["candidates"])


# A module the notice's own facts always connect: KB-POFA-01 (keeper liability
# threshold) is gated on jurisdiction and driver status alone, so every England &
# Wales notice with an unidentified driver legitimately yields it. Never a miss,
# never a false positive, and it does not count as retrieval for "zero" fixtures.
NOTICE_ONLY_ACCEPTABLE = frozenset({"KB-POFA-01"})


def score_one(fx: dict, got: set[str], customer_only: set[str]) -> dict:
    e = fx["expect"]
    must, acc, bad = set(e["must"]), set(e["acceptable"]), set(e["must_not"])
    ok = must | acc | NOTICE_ONLY_ACCEPTABLE
    missed = sorted(must - got)
    return {
        "id": fx["id"], "title": fx["title"], "got": sorted(got), "n": len(got),
        "must": sorted(must), "missed": missed,
        "must_not_hit": sorted(bad & got),
        "zero_violation": bool(e["zero"] and (got - ok)),
        "tp": len(got & must), "relevant_hit": len(got & ok),
        "customer_derived": sorted(customer_only),
        "blocked": bool(fx["stream_state"] and fx["stream_state"] != "READY"),
        "leak": sorted(customer_only) if fx["stream_state"] not in (None, "READY") else [],
        "pass": not missed and not (bad & got) and not (e["zero"] and (got - ok))
                and not (fx["stream_state"] not in (None, "READY") and customer_only),
    }


def evaluate(run: Callable[[dict, bool], dict], mode: str, only: Optional[set] = None) -> dict:
    rows, ms = [], []
    results: dict[str, dict] = {}
    for fx in FIXTURES:
        if only and fx["id"] not in only:
            continue
        with_customer = run(fx, True)
        notice_only = run(fx, False) if fx["packet"] is not None else with_customer
        got = retrieved(with_customer, mode)
        extra = got - retrieved(notice_only, mode)
        results[fx["id"]] = with_customer
        ms.append(with_customer["ms"])
        rows.append(score_one(fx, got, extra))
    return {"mode": mode, "rows": rows, "metrics": metrics(rows, ms, results),
            "groups": group_checks(results, mode)}


def group_checks(results: dict, mode: str) -> dict:
    out = {}
    for name, ids in GROUPS.items():
        sets = {i: retrieved(results[i], mode) for i in ids if i in results}
        out[name] = {i: sorted(s) for i, s in sets.items()}
    eq = [retrieved(results[i], mode) for i in ("F01", "F02", "F27") if i in results]
    if len(eq) == 3:
        union = set().union(*eq)
        inter = set.intersection(*eq)
        out["equivalent_meaning"] = {"identical": len({frozenset(s) for s in eq}) == 1,
                                     "jaccard": round(len(inter) / len(union), 3) if union else 1.0,
                                     "anpr01_in_all": all("KB-ANPR-01" in s for s in eq)}
    return out


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0.0


def metrics(rows: list[dict], ms: list[float], results: dict) -> dict:
    must_total = sum(len(r["must"]) for r in rows)
    must_hit = sum(r["tp"] for r in rows)
    got_total = sum(r["n"] for r in rows)
    rel_hit = sum(r["relevant_hit"] for r in rows)
    scored = [r for r in rows if r["must"]]
    return {
        "fixtures": len(rows), "passed": sum(r["pass"] for r in rows),
        "candidate_recall": round(must_hit / must_total, 3) if must_total else None,
        "candidate_precision": round(rel_hit / got_total, 3) if got_total else None,
        "top1_relevant": round(sum(1 for r in scored if r["got"] and r["got"][0] in r["must"])
                               / len(scored), 3) if scored else None,
        "avg_candidates": round(statistics.mean(r["n"] for r in rows), 2) if rows else 0,
        "max_candidates": max((r["n"] for r in rows), default=0),
        "missed_modules": sum(len(r["missed"]) for r in rows),
        "must_not_hits": sum(len(r["must_not_hit"]) for r in rows),
        "zero_violations": sum(r["zero_violation"] for r in rows),
        "blocked_stream_leakage": sum(len(r["leak"]) for r in rows),
        "latency_ms_p50": round(_q(ms, 0.5), 2), "latency_ms_p95": round(_q(ms, 0.95), 2),
    }


def breakdown(results: dict) -> dict:
    """Which routes found what (new retrieval only)."""
    from collections import Counter
    routes, vec_only, vec_false = Counter(), 0, 0
    for fid, r in results.items():
        fx = next(f for f in FIXTURES if f["id"] == fid)
        ok = set(fx["expect"]["must"]) | set(fx["expect"]["acceptable"]) | NOTICE_ONLY_ACCEPTABLE
        for c in r.get("detail") or []:
            for src in c["retrieval_sources"]:
                routes[src] += 1
            if c["retrieval_sources"] == ["VECTOR"]:
                vec_only += 1
                vec_false += c["module_id"] not in ok
    return {"by_route": dict(routes), "vector_only": vec_only,
            "vector_only_false_positive": vec_false}


def run_and_report(mode: str = "current", view: str = "window") -> dict:
    if mode == "new":
        results: dict[str, dict] = {}

        def run(fx, cust):
            r = run_new(fx, customer=cust)
            if cust:
                results[fx["id"]] = r
            return r
        rep = evaluate(run, "retrieval")
        rep["breakdown"] = breakdown(results)
        rep["detail"] = {k: v["detail"] for k, v in results.items()}
        return rep
    return evaluate(lambda fx, cust: run_current(fx, customer=cust), view)
