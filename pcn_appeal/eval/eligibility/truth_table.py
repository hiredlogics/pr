"""Operator truth table: every DSL operator x {match, nonmatch, missing, uncertain, conflicted}.

The expected values are the Phase 3B contract, written before the production
evaluator was changed. Cells marked N/A are states an operator cannot be in.
"""
from __future__ import annotations

from typing import Any, Optional

T, F, U = True, False, None
NA = "N/A"

STATES = ("match", "nonmatch", "missing", "uncertain", "conflicted")

# (operator label, predicate, value that matches, value that does not match)
OPERATORS: list[tuple[str, dict, Any, Any]] = [
    ("is",       {"is": "f"},                 True, False),
    ("exists",   {"exists": "f"},             "x", NA),
    ("missing",  {"missing": "f"},            NA, "x"),
    ("eq",       {"eq": ["f", "A"]},          "A", "B"),
    ("ne",       {"ne": ["f", "A"]},          "B", "A"),
    ("in",       {"in": ["f", ["A", "B"]]},   "A", "C"),
    ("gt",       {"gt": ["f", 5]},            6, 5),
    ("gte",      {"gte": ["f", 5]},           5, 4),
    ("lt",       {"lt": ["f", 5]},            4, 5),
    ("lte",      {"lte": ["f", 5]},           5, 6),
    ("contains", {"contains": ["f", "perm"]}, "a permit here", "nothing"),
]

# The same operators under `not` (a negative condition on the same fact).
NEGATED = [(f"not {lbl}", {"not": p}, m, n) for lbl, p, m, n in OPERATORS]


def expected(label: str, state: str) -> Any:
    """The contract value of one cell."""
    base = label[4:] if label.startswith("not ") else label
    neg = label.startswith("not ")
    row = {
        "is":       {"match": T, "nonmatch": F},
        "exists":   {"match": T, "nonmatch": NA},
        "missing":  {"match": NA, "nonmatch": F},
    }.get(base, {"match": T, "nonmatch": F})
    if state in ("match", "nonmatch"):
        v = row[state]
        if v == NA:
            return NA
    else:
        v = U                    # missing / uncertain / conflicted: never TRUE, never FALSE
    return v if (v is U or not neg) else (not v)


# Connectives, over leaf values drawn from {T, F, U}.
def kleene_all(vals): return F if F in vals else (T if all(v is T for v in vals) else U)
def kleene_any(vals): return T if T in vals else (F if all(v is F for v in vals) else U)
