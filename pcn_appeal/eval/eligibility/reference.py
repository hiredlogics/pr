"""The reference semantics for a KB predicate, written from the Phase 3B contract.

It is deliberately independent of pcn_appeal.rules.dsl: the production evaluator
is checked AGAINST this, never derived from it. Three values: True, False and
None (UNKNOWN).

The one idea: a fact the case does not (reliably) hold makes a condition on it
UNKNOWN - never true, never false. Only a fact that is present, usable and
compared can make a condition TRUE or FALSE. UNKNOWN is carried through the
connectives by Kleene's rules.

  per-fact state     present & usable -> KNOWN
                     absent / empty   -> MISSING      (this includes "UNKNOWN":
                                                       an unanswered question)
                     held but not trusted (UNCERTAIN, CONFLICTED) -> UNRELIABLE
  every operator     KNOWN -> TRUE/FALSE by comparison; MISSING or UNRELIABLE -> UNKNOWN
  a comparison that cannot be made (a string against a number) -> UNKNOWN
  has_evidence       kind uploaded -> TRUE; absent and evidence_complete -> FALSE;
                     absent otherwise -> UNKNOWN (it may still be uploaded)
  pofa_finding       eq/in: a named code is a verified finding -> TRUE; every named
                     code recorded NOT_SUPPORTED -> FALSE; anything else (unresolved,
                     or never calculated) -> UNKNOWN. ne: the exact negation.
  always             a constant
  not                True<->False, UNKNOWN stays
  all / any          Kleene AND / OR
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

T, F, U = True, False, None
_ABSENT = object()


def _known(facts: Mapping[str, Any], name: str, unreliable) -> tuple[str, Any]:
    if name in unreliable:
        return "UNRELIABLE", None
    v = facts.get(name, _ABSENT)
    if v is _ABSENT or v is None or v == "" or v == []:
        return "MISSING", None
    return "KNOWN", v


def leaf(op: str, arg: Any, facts: Mapping[str, Any], unreliable=frozenset()) -> Optional[bool]:
    if op == "always":
        return bool(arg)
    if op == "has_evidence":
        if arg in (facts.get("evidence_kinds") or []):
            return T
        return F if facts.get("evidence_complete") is True else U
    name = arg if isinstance(arg, str) else arg[0]
    if name in ("pofa_finding", "pofa_findings") and op in ("eq", "ne", "in"):
        codes = set(str(c) for c in (facts.get("pofa_findings") or []))
        if facts.get("pofa_finding"):
            codes.add(str(facts["pofa_finding"]))
        states = facts.get("pofa_finding_states") or {}
        ref = arg[1]
        wanted = set(map(str, ref)) if op == "in" else {str(ref)}
        if wanted & codes:
            hit = T
        elif all(states.get(w) == "NOT_SUPPORTED" for w in wanted):
            hit = F                       # every named defect was calculated and is not there
        else:
            return U                      # unresolved, or never calculated: nothing is known
        return (not hit) if op == "ne" else hit
    state, v = _known(facts, name, unreliable)
    if state != "KNOWN":
        return U
    if op == "is":
        return bool(v)
    if op == "exists":
        return T
    if op == "missing":
        return F
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
        return {"gt": v > ref, "gte": v >= ref, "lt": v < ref, "lte": v <= ref}[op]
    except TypeError:
        return U


def tree(pred: Any, facts: Mapping[str, Any], unreliable=frozenset()) -> Optional[bool]:
    if pred in (None, {}):
        return F
    op, arg = next(iter(pred.items()))
    if op == "all":
        vals = [tree(p, facts, unreliable) for p in arg]
        return F if any(v is F for v in vals) else (T if all(v is T for v in vals) else U)
    if op == "any":
        vals = [tree(p, facts, unreliable) for p in arg]
        return T if any(v is T for v in vals) else (F if all(v is F for v in vals) else U)
    if op == "not":
        v = tree(arg, facts, unreliable)
        return U if v is U else (not v)
    return leaf(op, arg, facts, unreliable)


def status(use_when: Any, do_not_use_when: Any, facts: Mapping[str, Any],
           unreliable=frozenset()) -> str:
    """BLOCKED only when a blocker is deterministically TRUE; otherwise SUPPORTED
    when use_when is TRUE, REJECTED when it is FALSE, UNRESOLVED when it is UNKNOWN.
    An UNKNOWN blocker blocks nothing."""
    if tree(do_not_use_when, facts, unreliable) is T:
        return "BLOCKED"
    gate = tree(use_when, facts, unreliable)
    return "SUPPORTED" if gate is T else ("REJECTED" if gate is F else "UNRESOLVED")
