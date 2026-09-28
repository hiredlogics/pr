"""Tiny, safe predicate language for KB `use_when` / `do_not_use_when` and engine rules.

Why: in the source docs `use_when` is prose. Prose cannot be enforced, so the
AI could "decide" a module applies. Every gate is therefore a JSON/YAML
predicate evaluated deterministically BEFORE any retrieval or drafting.
Admins edit these in the KB admin UI (no code deploy needed).

Operators
    {"all": [p, ...]}            logical AND
    {"any": [p, ...]}            logical OR
    {"not": p}                   negation
    {"is": "name"}               fact is truthy (not "true": YAML turns that key into a bool)
    {"exists": "name"}           fact present and usable
    {"missing": "name"}          fact absent (used to drive questions)
    {"eq": ["name", value]}
    {"ne": ["name", value]}
    {"in": ["name", [v1, v2]]}
    {"gt"/"gte"/"lt"/"lte": ["name", number]}
    {"has_evidence": "KIND"}     an uploaded evidence item of that kind exists
    {"always": true}
No eval(), no attribute access, unknown operators raise.
"""
from __future__ import annotations

from typing import Any, Mapping

_MISSING = object()


class PredicateError(ValueError):
    pass


def _val(facts: Mapping[str, Any], name: str) -> Any:
    return facts.get(name, _MISSING)


def evaluate(pred: Any, facts: Mapping[str, Any]) -> bool:
    if pred in (None, {}):
        return False
    if not isinstance(pred, dict) or len(pred) != 1:
        raise PredicateError(f"Predicate must be a single-key dict: {pred!r}")
    op, arg = next(iter(pred.items()))

    if op == "always":
        return bool(arg)
    if op == "all":
        return all(evaluate(p, facts) for p in arg)
    if op == "any":
        return any(evaluate(p, facts) for p in arg)
    if op == "not":
        return not evaluate(arg, facts)
    if op == "is":
        v = _val(facts, arg)
        return v is not _MISSING and bool(v)
    if op == "exists":
        v = _val(facts, arg)
        return v is not _MISSING and v not in (None, "", [])
    if op == "missing":
        v = _val(facts, arg)
        return v is _MISSING or v in (None, "", [])
    if op == "has_evidence":
        return arg in facts.get("evidence_kinds", [])
    if op in {"eq", "ne", "in", "gt", "gte", "lt", "lte"}:
        name, ref = arg
        v = _val(facts, name)
        if v is _MISSING:
            return op == "ne"
        if op == "eq":
            return v == ref
        if op == "ne":
            return v != ref
        if op == "in":
            return v in ref
        try:
            if op == "gt":
                return v > ref
            if op == "gte":
                return v >= ref
            if op == "lt":
                return v < ref
            if op == "lte":
                return v <= ref
        except TypeError:
            return False
    raise PredicateError(f"Unknown operator: {op}")


def referenced_facts(pred: Any) -> set[str]:
    """All fact names a predicate depends on - used by the question engine."""
    out: set[str] = set()
    if not isinstance(pred, dict):
        return out
    for op, arg in pred.items():
        if op in ("all", "any"):
            for p in arg:
                out |= referenced_facts(p)
        elif op == "not":
            out |= referenced_facts(arg)
        elif op in ("is", "exists", "missing"):
            out.add(arg)
        elif op in ("eq", "ne", "in", "gt", "gte", "lt", "lte"):
            out.add(arg[0])
    return out
