"""Deterministic module eligibility: one candidate module, the authoritative fact
view and the verified findings in; SUPPORTED / UNRESOLVED / REJECTED / BLOCKED out.

    module + fact_view + verified_findings + evidence_state
        -> use_when and do_not_use_when, evaluated three-valued (TRUE/FALSE/UNKNOWN)
        -> a status and the conditions behind it

No model is consulted. Nothing is read but what is passed in: the fact view is
FactManager's usable facts - never the customer's words, never a semantic atom,
never a UI answer. A finding is consumed only as a code the legal calculation
engine verified (or recorded as not supported / unresolved).

Status contract
  SUPPORTED   use_when is TRUE and no blocker is TRUE. A blocker that is UNKNOWN
              does not prevent support and does not cause a block; it is listed
              in `unverified_blockers` so nothing is hidden.
  UNRESOLVED  the module may apply: a required condition is UNKNOWN (a fact is
              missing, unanswered, uncertain, conflicted, or evidence/finding not
              yet in) and nothing known contradicts it.
  REJECTED    known authoritative facts make use_when FALSE.
  BLOCKED     a do_not_use_when condition (or a curated blocking signal) is
              deterministically TRUE. Absence is never a contradiction.

The status never changes the module's role: a SUPPORTED SUPPORTING_PROPOSITION is
not a SUPPORTED SUBSTANTIVE_GROUND.

Missing information is not rejection, is not a blocker, and is not support.
Uncertainty is not support. Retrieval is not support. Only a deterministically
TRUE use_when on authoritative facts, with no TRUE blocker, yields SUPPORTED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from ..module_roles import role_of
from ..rules.dsl import (PredicateError, evaluate3, leaf_report, referenced_facts)

SUPPORTED, UNRESOLVED, REJECTED, BLOCKED = "SUPPORTED", "UNRESOLVED", "REJECTED", "BLOCKED"
STATUSES = (SUPPORTED, UNRESOLVED, REJECTED, BLOCKED)

VERIFIED, NOT_SUPPORTED, FINDING_UNRESOLVED = "VERIFIED", "NOT_SUPPORTED", "UNRESOLVED"


@dataclass
class EligibilityOutcome:
    module_id: str
    module_role: str
    status: str
    kb_release_id: Optional[str] = None
    satisfied_conditions: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    rejected_conditions: list[str] = field(default_factory=list)
    blocking_conditions: list[str] = field(default_factory=list)
    fact_keys_used: list[str] = field(default_factory=list)
    unverified_blockers: list[str] = field(default_factory=list)
    use_when: Optional[bool] = None
    do_not_use_when: Optional[bool] = None
    error: str = ""

    def diagnostics(self) -> dict:
        return {
            "module_id": self.module_id, "status": self.status,
            "satisfied_conditions": list(self.satisfied_conditions),
            "missing_conditions": list(self.missing_conditions),
            "rejected_conditions": list(self.rejected_conditions),
            "blocking_conditions": list(self.blocking_conditions),
            "fact_keys_used": list(self.fact_keys_used),
            "unverified_blockers": list(self.unverified_blockers),
            "module_role": self.module_role, "kb_release_id": self.kb_release_id,
            "use_when": _name(self.use_when), "do_not_use_when": _name(self.do_not_use_when),
            **({"error": self.error} if self.error else {}),
        }


def _name(t: Optional[bool]) -> str:
    return "TRUE" if t is True else ("FALSE" if t is False else "UNKNOWN")


def decide(use_when: Optional[bool], do_not_use_when: Optional[bool],
           signal_block: bool = False) -> str:
    """The one place a status is decided from the two truth values."""
    if do_not_use_when is True or signal_block:
        return BLOCKED
    if use_when is True:
        return SUPPORTED
    if use_when is False:
        return REJECTED
    return UNRESOLVED


def effective_view(fact_view: Mapping[str, Any], *, verified_findings: Iterable[str] = (),
                   finding_states: Optional[Mapping[str, str]] = None,
                   evidence_state: Optional[Mapping[str, Any]] = None) -> dict:
    """The fact view the conditions are evaluated on: the authoritative facts plus
    the verified findings and the evidence state, in the keys the evaluator reads."""
    view = dict(fact_view or {})
    ev = dict(evidence_state or {})
    kinds = ev.get("kinds")
    view["evidence_kinds"] = sorted({str(k) for k in (kinds if kinds is not None
                                                      else view.get("evidence_kinds") or [])})
    if "complete" in ev:
        view["evidence_complete"] = bool(ev["complete"])
    verified = [str(c) for c in verified_findings or ()]
    if verified:
        codes = [str(c) for c in (view.get("pofa_findings") or []) if c]
        view["pofa_findings"] = list(dict.fromkeys(codes + verified))
    states = dict(view.get("pofa_finding_states") or {})
    states.update({str(k): str(v) for k, v in (finding_states or {}).items()})
    for c in verified:
        states[c] = VERIFIED
    if states:
        view["pofa_finding_states"] = states
    return view


def evaluate_module(module, fact_view: Mapping[str, Any], *,
                    verified_findings: Iterable[str] = (),
                    finding_states: Optional[Mapping[str, str]] = None,
                    evidence_state: Optional[Mapping[str, Any]] = None,
                    unreliable: Iterable[str] = (),
                    blocking_signals: Iterable[str] = (),
                    kb_release_id: Optional[str] = None) -> EligibilityOutcome:
    """Eligibility of one module on the given authoritative inputs.

    `unreliable`: facts the case holds but does not trust (UNCERTAIN, CONFLICTED).
    They are absent from `fact_view` by construction; naming them lets the
    diagnostics say "held but not trusted" rather than "not known".
    `blocking_signals`: curated blocking signals that are active (declared in the
    KB, e.g. an evidence-method exclusion). Only declared blocks block.
    """
    view = effective_view(fact_view, verified_findings=verified_findings,
                          finding_states=finding_states, evidence_state=evidence_state)
    unreliable = frozenset(unreliable or ())
    blocks = [str(b) for b in blocking_signals or ()]
    out = EligibilityOutcome(module.module_id, role_of(module), UNRESOLVED,
                             kb_release_id=kb_release_id)
    try:
        gate = evaluate3(module.use_when, view, unreliable=unreliable)
        dnuw = evaluate3(module.do_not_use_when, view, unreliable=unreliable)
    except PredicateError as exc:
        out.status, out.error = REJECTED, f"predicate error: {exc}"
        out.rejected_conditions.append(out.error)
        return out
    out.use_when, out.do_not_use_when = gate, dnuw
    out.status = decide(gate, dnuw, bool(blocks))

    used: set[str] = set()
    for row in leaf_report(module.use_when, view, unreliable=unreliable):
        text = row["condition"]
        if row["truth"] is True:
            out.satisfied_conditions.append(text)
        elif row["truth"] is False:
            out.rejected_conditions.append(text)
        else:
            out.missing_conditions.append(f"{text} ({row['why']})")
        if row["truth"] is not None and row["fact"]:
            used.add(row["fact"])
    for row in leaf_report(module.do_not_use_when, view, unreliable=unreliable):
        if row["truth"] is True:
            out.blocking_conditions.append(row["condition"])
            if row["fact"]:
                used.add(row["fact"])
        elif row["truth"] is None:
            out.unverified_blockers.append(f"{row['condition']} ({row['why']})")
    out.blocking_conditions += [f"signal {b}" for b in blocks]
    # Only a verified code counts as a fact the gate stood on.
    if "pofa_finding" in used:
        used.discard("pofa_finding")
        used.update(f"finding:{c}" for c in view.get("pofa_findings") or [])
    out.fact_keys_used = sorted(used & (referenced_facts(module.use_when)
                                        | referenced_facts(module.do_not_use_when)
                                        | {u for u in used if u.startswith("finding:")}))
    return out


def evaluate_module_id(kg, module_id: str, fact_view: Mapping[str, Any], **kwargs) -> EligibilityOutcome:
    """As evaluate_module, for a module of the KB, with the release id recorded and
    any curated blocking signal that is active on this view applied."""
    from .knowledge_matcher import signals
    module = kg.modules[module_id]
    view = effective_view(fact_view, verified_findings=kwargs.get("verified_findings") or (),
                          finding_states=kwargs.get("finding_states"),
                          evidence_state=kwargs.get("evidence_state"))
    graph = kg.relations
    sig = signals(graph, view)
    sig_ids = {f"{k}={v['value']}" for k, v in sig.items()}
    blocks = [e.source_id for e in graph.edges_of(module_id, "BLOCKS")
              if e.target_id == module_id and e.source_type == "SIGNAL" and e.source_id in sig_ids]
    kwargs.setdefault("kb_release_id", getattr(kg, "release_id", None))
    kwargs["blocking_signals"] = list(kwargs.get("blocking_signals") or []) + blocks
    return evaluate_module(module, fact_view, **kwargs)


__all__ = ["EligibilityOutcome", "evaluate_module", "evaluate_module_id", "decide",
           "effective_view", "SUPPORTED", "UNRESOLVED", "REJECTED", "BLOCKED", "STATUSES"]
