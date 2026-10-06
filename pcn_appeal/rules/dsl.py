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
    {"missing": "name"}          fact absent
    {"eq": ["name", value]}
    {"ne": ["name", value]}
    {"in": ["name", [v1, v2]]}
    {"gt"/"gte"/"lt"/"lte": ["name", number]}
    {"has_evidence": "KIND"}     an uploaded evidence item of that kind exists
    {"contains": ["name", "substr"]}  fact string contains substr (case-insensitive)
    {"always": true}
No eval(), no attribute access, unknown operators raise.

Evaluation is three-valued (see evaluate3): a condition on a fact the case does not
reliably hold is UNKNOWN, never true and never false. `evaluate` is True only when
the predicate is deterministically true.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

_MISSING = object()


class PredicateError(ValueError):
    pass


def _val(facts: Mapping[str, Any], name: str) -> Any:
    return facts.get(name, _MISSING)


TRUE, FALSE, UNKNOWN = True, False, None

# Reserved fact-view keys the evaluator understands (neither is a case fact):
#   evidence_complete    True once no further evidence is expected, so an evidence
#                        kind that is not uploaded is known to be absent
#   pofa_finding_states  {finding code: VERIFIED | NOT_SUPPORTED | UNRESOLVED}
#                        as recorded by the legal calculation engine


def _state(facts: Mapping[str, Any], name: str, unreliable) -> tuple[str, Any]:
    """KNOWN / MISSING / UNRELIABLE for one fact, and its value when KNOWN.

    MISSING covers an absent fact, an unanswered one and an empty value. UNRELIABLE
    is a fact the case holds but does not trust (UNCERTAIN or CONFLICTED).
    """
    if name in unreliable:
        return "UNRELIABLE", None
    v = _val(facts, name)
    if v is _MISSING or v is None or v == "" or v == []:
        return "MISSING", None
    return "KNOWN", v


def evaluate3(pred: Any, facts: Mapping[str, Any], *,
              unreliable: Iterable[str] = frozenset()) -> Optional[bool]:
    """Three-valued evaluation: True, False, or None (UNKNOWN).

    A condition on a fact the case does not (reliably) hold is UNKNOWN - never
    true and never false - whatever the operator, including the negative ones
    (`ne`, `not`). Only a known, usable value compared with the reference can make
    a condition True or False. UNKNOWN passes through `not` unchanged and through
    `all` / `any` by Kleene's rules, so an UNKNOWN blocker blocks nothing and an
    UNKNOWN requirement is not satisfied.

    Per operator, for a fact that is KNOWN / MISSING / UNRELIABLE:
        is  exists  missing  eq  ne  in  gt  gte  lt  lte  contains
            -> compared / UNKNOWN / UNKNOWN
        has_evidence   kind uploaded -> True; absent -> False only when
                       `evidence_complete` is True, else UNKNOWN
        pofa_finding   eq / in: a named code verified -> True; every named code
                       recorded NOT_SUPPORTED -> False; otherwise UNKNOWN
                       (unresolved, or never calculated). ne: the negation.
        always         constant
    A comparison that cannot be made (text against a number) is UNKNOWN.
    """
    unreliable = frozenset(unreliable or ())
    if pred in (None, {}):
        return FALSE
    if not isinstance(pred, dict) or len(pred) != 1:
        raise PredicateError(f"Predicate must be a single-key dict: {pred!r}")
    op, arg = next(iter(pred.items()))

    if op == "always":
        return bool(arg)
    if op == "all":
        vals = [evaluate3(p, facts, unreliable=unreliable) for p in arg]
        if any(v is FALSE for v in vals):
            return FALSE
        return TRUE if all(v is TRUE for v in vals) else UNKNOWN
    if op == "any":
        vals = [evaluate3(p, facts, unreliable=unreliable) for p in arg]
        if any(v is TRUE for v in vals):
            return TRUE
        return FALSE if all(v is FALSE for v in vals) else UNKNOWN
    if op == "not":
        v = evaluate3(arg, facts, unreliable=unreliable)
        return UNKNOWN if v is UNKNOWN else (not v)
    if op == "has_evidence":
        if arg in (facts.get("evidence_kinds") or []):
            return TRUE
        return FALSE if facts.get("evidence_complete") is True else UNKNOWN
    if op not in _FACT_OPS:
        raise PredicateError(f"Unknown operator: {op}")

    name = arg if isinstance(arg, str) else arg[0]
    if name == "pofa_finding" and op in ("eq", "ne", "in"):
        return _pofa_gate(op, arg[1], facts)
    state, v = _state(facts, name, unreliable)
    if state != "KNOWN":
        return UNKNOWN
    if op == "is":
        return bool(v)
    if op == "exists":
        return TRUE
    if op == "missing":
        return FALSE
    ref = arg[1]
    if op == "contains":
        return str(ref).lower() in str(v).lower()
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
        return v <= ref
    except TypeError:
        return UNKNOWN


_FACT_OPS = frozenset({"is", "exists", "missing", "eq", "ne", "in", "gt", "gte", "lt", "lte",
                       "contains"})


def _pofa_gate(op: str, ref: Any, facts: Mapping[str, Any]) -> Optional[bool]:
    """A gate that names one or more defect codes. P8.1: pofa_finding may be
    multi-valued via pofa_findings[]; a code counts when it is anywhere among the
    verified findings, not only the first."""
    codes = set(_pofa_codes(facts) or [])
    states = facts.get("pofa_finding_states") or {}
    wanted = {str(w) for w in (ref if op == "in" else [ref]) if w is not None}
    if wanted & codes:
        hit: Optional[bool] = TRUE
    elif wanted and all(states.get(w) == "NOT_SUPPORTED" for w in wanted):
        hit = FALSE
    else:
        return UNKNOWN
    return (not hit) if op == "ne" else hit


def evaluate(pred: Any, facts: Mapping[str, Any], *,
             unreliable: Iterable[str] = frozenset()) -> bool:
    """True only when the predicate is deterministically true. UNKNOWN is not true."""
    return evaluate3(pred, facts, unreliable=unreliable) is TRUE


def describe_leaf(leaf: dict) -> str:
    """A leaf condition in words, for traces and diagnostics."""
    import json
    op, arg = next(iter(leaf.items()))
    if op == "is":
        return f"{arg}=true"
    if op == "exists":
        return f"{arg} present"
    if op == "missing":
        return f"{arg} absent"
    if op == "has_evidence":
        return f"evidence {arg} uploaded"
    if op == "contains":
        return f"{arg[0]} mentions '{arg[1]}'"
    if op == "always":
        return f"always {arg}"
    name, ref = arg
    sym = {"eq": "=", "ne": "!=", "in": " in ", "gt": ">", "gte": ">=", "lt": "<",
           "lte": "<="}[op]
    return f"{name}{sym}{json.dumps(ref) if not isinstance(ref, str) else ref}"


def leaf_report(pred: Any, facts: Mapping[str, Any], *,
                unreliable: Iterable[str] = frozenset()) -> list[dict]:
    """Every leaf of a predicate with its effective truth (after `not`s) and the
    reason it is unknown when it is: {condition, fact, truth, why}. Diagnostics
    only - the decision is evaluate3's."""
    unreliable = frozenset(unreliable or ())
    out: list[dict] = []

    def walk(p: Any, positive: bool) -> None:
        if not isinstance(p, dict) or len(p) != 1:
            return
        op, arg = next(iter(p.items()))
        if op in ("all", "any"):
            for q in arg:
                walk(q, positive)
        elif op == "not":
            walk(arg, not positive)
        elif op != "always":
            truth = evaluate3(p, facts, unreliable=unreliable)
            if op == "has_evidence":
                name, why = None, ("no evidence of that kind uploaded and more may follow"
                                   if truth is UNKNOWN else "")
            else:
                name = arg if isinstance(arg, str) else arg[0]
                state = _state(facts, name, unreliable)[0] if name != "pofa_finding" else (
                    "KNOWN" if truth is not UNKNOWN else "MISSING")
                why = "" if truth is not UNKNOWN else (
                    "held but not trusted (uncertain or conflicted)" if state == "UNRELIABLE"
                    else "not known")
            out.append({"condition": describe_leaf(p) if positive else "not (" + describe_leaf(p) + ")",
                        "fact": name,
                        "truth": truth if truth is UNKNOWN or positive else (not truth),
                        "why": why})

    walk(pred, True)
    return out


def _pofa_codes(facts: Mapping[str, Any]) -> Optional[List[str]]:
    """Authoritative defect codes for gate eval, or None when unset.

    Union of pofa_findings[] and scalar pofa_finding. An empty list plus a
    scalar (test injection / legacy) still yields the scalar — empty list alone
    does not mask a present scalar.
    """
    has_list = "pofa_findings" in facts
    has_scalar = "pofa_finding" in facts
    if not has_list and not has_scalar:
        return None
    codes: List[str] = []
    raw = facts.get("pofa_findings")
    if isinstance(raw, (list, tuple, set)):
        codes.extend(str(c) for c in raw if c)
    elif raw:
        codes.append(str(raw))
    single = facts.get("pofa_finding")
    if single not in (None, "", _MISSING) and str(single) not in codes:
        codes.append(str(single))
    return codes


def referenced_facts(pred: Any) -> set[str]:
    """All fact names a predicate depends on - used to build GATED_BY edges."""
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
        elif op in ("eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"):
            out.add(arg[0])
    return out
