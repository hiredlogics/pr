"""Factual rebuttal grounds: a confirmed fact that contradicts the notice.

The allegation layer (`pcn_appeal.allegation`) says what the operator asserts.
This module pairs those propositions against the case's usable facts and, where
one established fact contradicts one proposition, produces a structured ground
that the claim plan can carry and the drafter must honour.

Why this is a ground and not a drafting hint: an operator's allegation is a
factual claim, and the primary answer to "there was no child in the bay" is
"there was a child", not "you have not proved there was not". The evidential
put-to-proof argument is real and worth making, but it is support. Before this
module existed the evidential module was the only thing in the plan, so the
support was all that reached the letter and the answer itself was lost.

Nothing here is specific to one kind of allegation. A permit, payment or
continuity contradiction travels the same path as a parent-and-child one,
because the comparison is between a typed proposition and a fact, not between
strings.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .allegation import (
    CONTRADICTS, AllegationProposition, derive_allegation_propositions, relate,
)
from .models import CaseFile, Fact

FACTUAL_REBUTTAL = "FACTUAL_REBUTTAL"

# Particular types the drafter must satisfy.
STATE_FACT = "STATE_FACT"
CONNECT_FACT_TO_ALLEGATION = "CONNECT_FACT_TO_ALLEGATION"

_NS = uuid.UUID("6f1b6f1e-1f2a-4c3b-9d4e-5a6b7c8d9e01")


@dataclass
class FactualRebuttal:
    """One established fact answering one thing the notice asserts."""
    ground_id: str
    allegation_ref: str
    allegation_type: str
    relationship: str
    subject: str
    fact_value: Any
    supporting_fact_ids: list[str] = field(default_factory=list)
    supporting_module_ids: list[str] = field(default_factory=list)
    required_particulars: list[dict] = field(default_factory=list)
    # How the fact came to be known, so a reviewer can see at a glance why it
    # was allowed to carry an assertion.
    provenance: str = ""
    drafting_proposition: str = ""
    priority: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "ground_id": self.ground_id,
            "ground_type": FACTUAL_REBUTTAL,
            "allegation_ref": self.allegation_ref,
            "allegation_type": self.allegation_type,
            "relationship": self.relationship,
            "subject": self.subject,
            "fact_value": self.fact_value,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "supporting_module_ids": list(self.supporting_module_ids),
            "required_particulars": [dict(p) for p in self.required_particulars],
            "provenance": self.provenance,
            "drafting_proposition": self.drafting_proposition,
            "priority": self.priority,
        }


def _provenance_of(case: CaseFile, fact_name: str) -> tuple[str, bool, str]:
    """(provenance, customer_asserted, drafting_proposition) for a fact, from
    the account engine's own record of how the customer put it forward."""
    for row in (getattr(case, "free_text_provenance", None) or []):
        if row.get("fact_name") == fact_name:
            return (row.get("provenance") or "",
                    bool(row.get("customer_asserted")),
                    row.get("drafting_proposition") or "")
    return "", False, ""


def _modules_reading(fact_name: str, module_ids: list[str], kg) -> list[str]:
    """Approved modules that read this fact, so the rebuttal can name the KB
    framing that supports it without being replaced by it."""
    out = []
    for mid in module_ids or []:
        module = getattr(kg, "modules", {}).get(mid) if kg is not None else None
        if module is None:
            continue
        names = set(module.required_facts or [])
        try:
            names |= set(kg.gating_facts(mid))
        except Exception:
            pass
        if fact_name in names:
            out.append(mid)
    return out


def derive_factual_rebuttals(
        case: CaseFile, *, module_ids: Optional[list[str]] = None,
        kg: Any = None) -> list[FactualRebuttal]:
    """Every established fact that directly contradicts the notice.

    Only CONTRADICTS produces a ground. A fact about the same subject that is
    not established enough to assert comes back PARTIALLY_ADDRESSES from
    `relate` and is deliberately dropped here: it may still support other
    grounds through the normal module gates, but it may not put a proposition
    into the keeper's mouth.
    """
    breach = case.get("alleged_breach")
    props = derive_allegation_propositions(breach, case.fact_view() or {})
    if not props:
        return []

    out: list[FactualRebuttal] = []
    for prop in props:
        fact: Optional[Fact] = case.facts.get(prop.subject)
        if fact is None:
            continue
        prov, asserted, proposition = _provenance_of(case, prop.subject)
        if relate(prop, fact, customer_asserted=asserted) != CONTRADICTS:
            continue
        fact_id = case.facts.node_id(prop.subject)
        # An answered or document-sourced fact has no free-text provenance row;
        # name its source so the trace still says where it came from.
        if not prov:
            prov = f"{fact.source.kind.value}:{fact.status.value}"
        out.append(FactualRebuttal(
            ground_id=str(uuid.uuid5(_NS, f"{case.case_id}|{prop.proposition_id}")),
            allegation_ref=prop.proposition_id,
            allegation_type=prop.allegation_type,
            relationship=CONTRADICTS,
            subject=prop.subject,
            fact_value=fact.value,
            supporting_fact_ids=[fid for fid in [fact_id] if fid],
            supporting_module_ids=_modules_reading(prop.subject, module_ids or [], kg),
            required_particulars=[
                {"type": STATE_FACT, "fact_name": prop.subject, "fact_id": fact_id},
                {"type": CONNECT_FACT_TO_ALLEGATION,
                 "allegation_ref": prop.proposition_id,
                 "allegation_type": prop.allegation_type},
            ],
            provenance=prov,
            drafting_proposition=proposition,
        ))
    # Deterministic order so the plan digest is stable across runs.
    out.sort(key=lambda r: (r.allegation_type, r.subject))
    for n, r in enumerate(out, 1):
        r.priority = n
    return out


def record(case: CaseFile, rebuttals: list[FactualRebuttal]) -> None:
    """Put the rebuttals on the case and audit them."""
    case.factual_rebuttals = [r.as_dict() for r in rebuttals]
    if rebuttals:
        case.audit.append({
            "event": "factual_rebuttals_derived",
            "grounds": [{"ground_id": r.ground_id, "subject": r.subject,
                         "allegation_ref": r.allegation_ref,
                         "relationship": r.relationship,
                         "provenance": r.provenance} for r in rebuttals],
        })
