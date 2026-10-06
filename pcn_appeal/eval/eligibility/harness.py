"""Run eligibility cases through production and through the reference."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Optional

from pcn_appeal.engines.knowledge_matcher import (BLOCKED, OPEN, REJECTED, RELEVANT,
                                                  SUPPORTED, KnowledgeMatcher)
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import CaseFile
from pcn_appeal.rules import dsl

from . import matrix, reference as R, truth_table as TT

_KG: Optional[KnowledgeGraph] = None


def kg() -> KnowledgeGraph:
    global _KG
    if _KG is None:
        _KG = KnowledgeGraph()
    return _KG


# ------------------------------------------------------------ operator table
def _view_for(state: str, value: Any) -> tuple[dict, frozenset]:
    """A (view, unreliable) pair that puts fact `f` in `state`, built the way the
    case builds it: an UNCERTAIN or disputed fact is held but absent from the view."""
    if state in ("match", "nonmatch"):
        return {"f": value}, frozenset()
    if state == "missing":
        return {}, frozenset()
    return {}, frozenset({"f"})            # uncertain / conflicted: held, not usable


def production_value(pred: dict, view: dict, unreliable: frozenset) -> Any:
    """What the production evaluator says. Three-valued when it can be."""
    if hasattr(dsl, "evaluate3"):
        return dsl.evaluate3(pred, view, unreliable=unreliable)
    return dsl.evaluate(pred, view)


def operator_table() -> dict:
    rows = []
    for label, pred, match_v, non_v in TT.OPERATORS + TT.NEGATED:
        for state in TT.STATES:
            want = TT.expected(label, state)
            if want == TT.NA:
                continue
            value = match_v if state == "match" else non_v
            view, unreliable = _view_for(state, value)
            got = production_value(pred, view, unreliable)
            ref = R.tree(pred, view, unreliable)
            rows.append({"op": label, "state": state, "expected": want, "reference": ref,
                         "production": got, "ok": (got is None and want is None)
                         or (got is not None and want is not None and got == want
                             and type(got) is type(want))})
    return {"rows": rows, "bad": [r for r in rows if not r["ok"]],
            "reference_matches_contract": all(r["reference"] is r["expected"] for r in rows)}


# --------------------------------------------------------------- module matrix
_MAP = {SUPPORTED: "SUPPORTED", RELEVANT: "UNRESOLVED", OPEN: "UNRESOLVED",
        REJECTED: "REJECTED", BLOCKED: "BLOCKED"}


def production_status(module, view: dict) -> str:
    """Production's verdict on one module for a fact view, through the matcher
    (the path the pipeline uses). Retrieval is not involved in the status."""
    case = CaseFile("elig")
    match = KnowledgeMatcher(kg()).match(case, dict(view))
    return _MAP[match.candidates[module.module_id].status]


def module_matrix(status_fn: Callable = production_status, limit: int = 400) -> dict:
    from pcn_appeal.engines.knowledge_matcher import signals
    graph = kg().relations
    block_sources = {e.source_id for e in graph.edges
                     if e.relationship_type == "BLOCKS" and e.source_type == "SIGNAL"}
    per_module: dict[str, dict] = {}
    skipped = Counter()
    for m in sorted(kg().active_modules(), key=lambda m: m.module_id):
        agree = disagree = 0
        counts, first_bad = Counter(), []
        for view, _assign in matrix.cases_for(m, limit):
            sig = signals(graph, view)
            if {f"{k}={v['value']}" for k, v in sig.items()} & block_sources:
                skipped["a curated blocking signal is active"] += 1
                continue
            want = R.status(m.use_when, m.do_not_use_when, view)
            got = status_fn(m, view)
            counts[want] += 1
            if got == want:
                agree += 1
            else:
                disagree += 1
                if len(first_bad) < 3:
                    first_bad.append({"view": view, "expected": want, "got": got})
        per_module[m.module_id] = {"cases": agree + disagree, "agree": agree,
                                   "disagree": disagree, "expected_mix": dict(counts),
                                   "examples": first_bad}
    return {"modules": per_module, "skipped": dict(skipped),
            "cases": sum(v["cases"] for v in per_module.values()),
            "disagree": sum(v["disagree"] for v in per_module.values()),
            "modules_with_disagreement": sorted(k for k, v in per_module.items() if v["disagree"])}


# -------------------------------------------------------------- named scenarios
def _scenario_view(sc: dict) -> dict:
    view = dict(sc["view"])
    view.setdefault("driver_status", "UNIDENTIFIED")
    view["evidence_kinds"] = list(sc["evidence"])
    if sc["findings"]:
        view["pofa_findings"] = list(sc["findings"])
        view["pofa_finding"] = sc["findings"][0]
    return view


def production_scenario_status(sc: dict) -> str:
    """The production path as it stands: the matcher over the fact view. Inputs the
    old code had no way to receive (finding states, evidence completeness, the
    untrusted-fact set) are simply not passed."""
    module = kg().modules[sc["module"]]
    return production_status(module, _scenario_view(sc))


def run_scenarios(status_fn: Callable = production_scenario_status) -> dict:
    from .scenarios import SCENARIOS
    rows = []
    for sc in SCENARIOS:
        got = status_fn(sc)
        rows.append({"id": sc["id"], "module": sc["module"], "family": sc["family"],
                     "expected": sc["expect"], "got": got, "ok": got == sc["expect"],
                     "changed": sc["changed"]})
    return {"rows": rows, "bad": [r for r in rows if not r["ok"]],
            "counts": dict(Counter(r["got"] for r in rows))}


# --------------------------------------------- the direct eligibility API, and the case path
def eligibility_status(module, view: dict) -> str:
    """Status through the standalone eligibility API (facts only, no case)."""
    from pcn_appeal.engines.module_eligibility import evaluate_module_id
    return evaluate_module_id(kg(), module.module_id, dict(view)).status


def eligibility_scenario_status(sc: dict) -> str:
    """The scenario through the standalone API with every input it can receive."""
    from pcn_appeal.engines.module_eligibility import evaluate_module_id
    view = dict(sc["view"])
    view.setdefault("driver_status", "UNIDENTIFIED")
    return evaluate_module_id(
        kg(), sc["module"], view, verified_findings=sc["findings"],
        finding_states=sc["finding_states"],
        evidence_state={"kinds": sc["evidence"], "complete": sc["evidence_complete"]},
        unreliable=sc["unreliable"]).status


def _case_for(sc: dict) -> tuple[CaseFile, dict]:
    """A real case: the untrusted facts held as UNCERTAIN, findings as case records."""
    from dataclasses import replace  # noqa: F401
    from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
    case = CaseFile("elig")
    for name in sc["unreliable"]:
        case.put(Fact(f"f-{name}", name, "x", FactStatus.UNCERTAIN,
                      FactSource(next(iter(SourceKind)), "t")))
    for code in set(sc["findings"]) | set(sc["finding_states"]):
        state = "VERIFIED" if code in sc["findings"] else sc["finding_states"][code]
        case.legal_findings.append({"finding_type": code, "status": state})
    view = dict(sc["view"])
    view.setdefault("driver_status", "UNIDENTIFIED")
    view["evidence_kinds"] = list(sc["evidence"])
    view["evidence_complete"] = bool(sc["evidence_complete"]) if sc["evidence_complete"] else None
    if view["evidence_complete"] is None:
        del view["evidence_complete"]
    if sc["findings"]:
        view["pofa_findings"] = list(sc["findings"])
        view["pofa_finding"] = sc["findings"][0]
    return case, view


def case_scenario_status(sc: dict) -> str:
    """The scenario through the pipeline's own path: the matcher over a real case."""
    case, view = _case_for(sc)
    match = KnowledgeMatcher(kg()).match(case, view)
    return _MAP[match.candidates[sc["module"]].status]
