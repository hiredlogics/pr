"""Retrieval-only metadata for the approved KB modules (brief 2026-10-09 §4/§5).

One retrieval unit per legal proposition. The corpus text for a module used to
be `topic + core_proposition + drafting_notes`, which meant the proposition was
retrievable WITHOUT its conditions - the split the brief forbids, because the
model could find "analyse whether a contract was accepted" without ever seeing
"USE WHEN terms were considered but not accepted and the vehicle then left".

`retrieval_text()` keeps them together and appends the customer-language
concepts, so a case reaches the right proposition from the customer's own
words. Measured effect on the brief's own examples: "I looked at the conditions
and decided not to stay" retrieved KB-BAY-01 / KB-REC-01 / KB-POFA-04 before
this, and KB-CON-02 after it.

This is retrieval reach ONLY. Nothing here can make a ground supported: that is
decided by `use_when` / `do_not_use_when` in kb_modules.yaml against verified
facts (`KnowledgeMatcher`), and a module this file helps FIND is still rejected
there unless its facts actually hold. Adding a phrase widens what can be looked
at; it can never widen what can be argued, nor supply a fact, nor reach a
letter.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DATA = Path(__file__).resolve().parent / "data" / "kb_retrieval.yaml"


@lru_cache(maxsize=1)
def load(path: str = str(DATA)) -> dict[str, dict[str, Any]]:
    """module_id -> {use_when, must_check, concepts}. Empty when absent."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for mid, row in (data.get("modules") or {}).items():
        row = row or {}
        out[str(mid)] = {
            "use_when": str(row.get("use_when") or "").strip(),
            "must_check": str(row.get("must_check") or "").strip(),
            "concepts": [str(c).strip() for c in (row.get("concepts") or []) if str(c).strip()],
        }
    return out


def concepts_for(module_id: str) -> list[str]:
    return list(load().get(str(module_id), {}).get("concepts") or ())


def retrieval_text(module) -> str:
    """The text one module is retrieved on: proposition WITH its conditions.

    Order matters only for readability; the retriever is bag-of-words plus an
    optional embedding. The client's USE WHEN comes first because it is the
    sentence that describes when the ground applies, which is what a customer's
    account is actually being compared against.
    """
    meta = load().get(getattr(module, "module_id", ""), {})
    parts = [
        str(getattr(module, "topic", "") or ""),
        meta.get("use_when", ""),
        str(getattr(module, "core_proposition", "") or ""),
        meta.get("must_check", ""),
        str(getattr(module, "drafting_notes", "") or ""),
        " ".join(meta.get("concepts") or ()),
    ]
    return " ".join(p.strip() for p in parts if p and p.strip())
