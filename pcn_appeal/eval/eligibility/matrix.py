"""Generated eligibility cases for every active KB module.

For each module, each distinct leaf condition of its use_when / do_not_use_when
is put in one of the states TRUE / FALSE / UNKNOWN by constructing a fact view
that really produces it, and the combination is checked against the reference
(reference.py). No case is written by hand and none names a module: the cases
come from the module's own predicates.
"""
from __future__ import annotations

import itertools
import json
import random
from typing import Any, Iterable, Optional

from pcn_appeal.kg.relations import _leaves

from . import reference as R

T, F, U = True, False, None


def _leaf_key(leaf: dict) -> str:
    return json.dumps(leaf, sort_keys=True)


def _unique_leaves(module) -> list[dict]:
    seen, out = set(), []
    for pred in (module.use_when, module.do_not_use_when):
        for _, leaf in _leaves(pred):
            k = _leaf_key(leaf)
            if k not in seen:
                seen.add(k)
                out.append(leaf)
    return out


def allowed_states(leaf: dict) -> tuple:
    """The states a fact view can put this leaf in."""
    op, arg = next(iter(leaf.items()))
    if op in ("exists",):
        return (T, U)
    if op == "missing":
        return (F, U)
    if op == "has_evidence":
        return (T, F, U)
    name = arg if isinstance(arg, str) else arg[0]
    if name == "driver_status":
        return (T, F)                 # the case always has a driver status
    return (T, F, U)


def realise(leaf: dict, want: Optional[bool], view: dict) -> bool:
    """Write into `view` what puts `leaf` in state `want`. False if impossible or
    it contradicts what another leaf already wrote."""
    op, arg = next(iter(leaf.items()))

    def put(name: str, value: Any) -> bool:
        if name in view and view[name] != value:
            # Another leaf already fixed this fact: fine if that value already
            # puts THIS leaf in the wanted state (e.g. eq X false, because it is Y).
            return R.leaf(op, arg, view) is want
        view[name] = value
        return True

    def absent(name: str) -> bool:
        return name not in view

    if op == "always":
        return True
    if op == "has_evidence":
        kinds = view.setdefault("evidence_kinds", [])
        if want is T:
            if arg not in kinds:
                kinds.append(arg)
            return True
        if arg in kinds:
            return False
        if want is F:
            view["evidence_complete"] = True
        else:
            if view.get("evidence_complete"):
                return False
        return True
    name = arg if isinstance(arg, str) else arg[0]
    ref = None if isinstance(arg, str) else arg[1]
    if name in ("pofa_finding", "pofa_findings") and op in ("eq", "in", "ne"):
        wanted = [str(x) for x in (ref if op == "in" else [ref])]
        codes = view.setdefault("pofa_findings", [])
        states = view.setdefault("pofa_finding_states", {})
        positive = {"eq": T, "in": T, "ne": F}[op]          # does the leaf want the code present?
        present = want is positive if want is not U else None
        if want is U:
            for w in wanted:
                if w in codes:
                    return False
                states[w] = "UNRESOLVED"
        elif present:
            if not wanted[0] in codes:
                codes.append(wanted[0])
        else:
            if any(w in codes for w in wanted):
                return False
            for w in wanted:
                states[w] = "NOT_SUPPORTED"
        return True
    if want is U:
        return absent(name)
    if op == "is":
        return put(name, bool(want))
    if op == "exists":
        return put(name, "x") if want is T else False
    if op == "missing":
        return put(name, "x") if want is F else False
    if op == "eq":
        return put(name, ref if want else f"{ref}__other")
    if op == "ne":
        return put(name, f"{ref}__other" if want else ref)
    if op == "in":
        return put(name, ref[0] if want else "__not_in_list__")
    if op == "contains":
        return put(name, f"xx {ref} xx" if want else "zzzz")
    num = {"gt": (ref + 1, ref - 1), "gte": (ref, ref - 1),
           "lt": (ref - 1, ref + 1), "lte": (ref, ref + 1)}.get(op)
    if num:
        return put(name, num[0] if want else num[1])
    raise ValueError(f"unhandled operator {op}")


def cases_for(module, limit: int = 400, seed: int = 7) -> Iterable[tuple[dict, dict]]:
    """(view, assignment) for the leaf-state combinations of one module."""
    leaves = _unique_leaves(module)
    if not leaves:
        yield {"evidence_kinds": []}, {}
        return
    options = [allowed_states(l) for l in leaves]
    if len(leaves) <= 6:
        combos = list(itertools.product(*options))
    else:
        rng = random.Random(seed)
        combos = [tuple(o[0] for o in options), tuple(o[-1] for o in options)]
        for i in range(len(leaves)):
            for st in options[i]:
                c = [o[0] for o in options]
                c[i] = st
                combos.append(tuple(c))
        while len(combos) < limit:
            combos.append(tuple(rng.choice(o) for o in options))
    seen = set()
    for combo in combos[:limit * 3]:
        view: dict = {"evidence_kinds": []}
        ok = True
        for leaf, want in zip(leaves, combo):
            if not realise(leaf, want, view):
                ok = False
                break
        if not ok:
            continue
        key = json.dumps(view, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        yield view, {_leaf_key(l): w for l, w in zip(leaves, combo)}
        if len(seen) >= limit:
            return
