"""Knowledge matcher (P4): verified facts + evidence -> knowledge candidates,
each with the relationships that put it there or kept it out.

Answers, per in-force module, the four questions case analysis used to have to
infer from text similarity:

  Why does this module apply?           SUPPORTS / EVIDENCE_SUPPORTS edges whose
                                        condition holds, with the fact's source
  Why does another module not apply?    the unmet conditions, or the BLOCKS edge
  What fact supports it?                selected_because
  What fact blocks it?                  blocked_by

Statuses:
  SUPPORTED     the module's gate holds on the verified facts (exactly the
                reasoning gate R-03: use_when and not do_not_use_when) and
                nothing blocks it. A candidate for the claim plan; case analysis
                still decides, and the claim plan still vetoes.
  RELEVANT      the gate does not hold yet but could (its missing facts are
                unknown), and the case's facts, evidence or allegation connect to
                it through a relationship. Offered to case analysis.
  OPEN          could still apply, but nothing in the case points at it yet.
                Offered only if semantic retrieval ranks it in.
  REJECTED      the gate cannot hold on what is known.
  BLOCKED       a BLOCKS relationship fires (wrong evidence type, an allegation
                no payment can answer, a blocked condition). Never offered; a
                proposal of it is suppressed before the claim plan.

Deterministic: no model, no retrieval, sorted output - the same facts give the
same candidates and the same trace every run (spec test 5). Reads facts only:
writes nothing to the Fact Graph.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..kg.relations import (BLOCKS, CONFLICTS_WITH, EVIDENCE_SUPPORTS, SUPPORTS,
                            RelationGraph)
from ..models import CaseFile
from ..rules.dsl import PredicateError, evaluate, referenced_facts

SUPPORTED, RELEVANT, OPEN = "SUPPORTED", "RELEVANT", "OPEN"
REJECTED, BLOCKED = "REJECTED", "BLOCKED"
OFFERABLE = (SUPPORTED, RELEVANT, OPEN)
_ORDER = {SUPPORTED: 0, RELEVANT: 1, OPEN: 2, REJECTED: 3, BLOCKED: 4}

def _semantic_candidate_signals(case: CaseFile) -> dict:
    """Read SemanticCaseState channels for candidate discovery (observational)."""
    # Customer-account semantics are used only while that stream is ready.
    from ..semantics.understanding import customer_semantic_raw
    raw = customer_semantic_raw(case, "_semantic_case_state")
    state = {}
    if raw:
        try:
            state = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            state = {}
    concepts = list(state.get("concepts") or [])
    atoms = list(state.get("narrative_atoms") or [])
    events = list(state.get("events") or state.get("customer_reported_events") or [])
    rels = list(state.get("relationships") or [])
    # Compact atom cache if full state truncated.
    if not atoms:
        compact = customer_semantic_raw(case, "_semantic_narrative_atoms")
        if compact:
            try:
                atoms = json.loads(compact) if isinstance(compact, str) else list(compact)
            except Exception:
                atoms = []
    return {
        "concepts": concepts,
        "atoms": atoms,
        "events": events,
        "relationships": rels,
    }


@dataclass
class Candidate:
    module_id: str
    status: str
    topic: str
    route: str
    strength: int
    selected_because: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    blocked_by: list[dict] = field(default_factory=list)
    relevant_because: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def for_analysis(self) -> dict:
        """What case analysis is told about the relationship - no fact sources,
        no KB-internal ids beyond the module itself."""
        return {"status": self.status,
                "supported_by": [s["condition"] for s in self.selected_because],
                "missing": self.missing,
                "relevant_because": [r["reason"] for r in self.relevant_because]}


@dataclass
class Match:
    signals: dict[str, dict]
    candidates: dict[str, Candidate]
    relations_version: str

    def by_status(self, *statuses: str) -> list[Candidate]:
        return sorted((c for c in self.candidates.values() if c.status in statuses),
                      key=lambda c: (_ORDER[c.status], -c.strength, c.module_id))

    @property
    def supported(self) -> list[Candidate]:
        return self.by_status(SUPPORTED)

    @property
    def blocked(self) -> list[Candidate]:
        return self.by_status(BLOCKED)

    def is_blocked(self, module_id: str) -> bool:
        c = self.candidates.get(module_id)
        return c is not None and c.status == BLOCKED

    def trace(self) -> dict:
        """Admin trace: facts -> relationships -> knowledge, and every rejection."""
        return {
            "relations_version": self.relations_version,
            "signals": self.signals,
            "selected": [_trace_row(c) for c in self.by_status(SUPPORTED)],
            "relevant": [_trace_row(c) for c in self.by_status(RELEVANT)],
            "rejected": [{"module": c.module_id, "status": c.status, "reason": c.reason,
                          "missing": list(c.missing or []),
                          **({"blocked_by": c.blocked_by} if c.blocked_by else {})}
                         for c in self.by_status(REJECTED, BLOCKED)],
        }


def _trace_row(c: Candidate) -> dict:
    return {"module": c.module_id, "status": c.status,
            "selected_because": [s["condition"] for s in c.selected_because],
            "facts": c.selected_because, "relevant_because": c.relevant_because,
            "missing": c.missing, "evidence": c.evidence}


# ------------------------------------------------------------- helpers
def _show(leaf: dict) -> str:
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
    name, ref = arg
    sym = {"eq": "=", "ne": "!=", "in": " in ", "gt": ">", "gte": ">=", "lt": "<",
           "lte": "<="}[op]
    return f"{name}{sym}{json.dumps(ref) if not isinstance(ref, str) else ref}"


def _negate(text: str) -> str:
    return text.replace("=true", " not true") if text.endswith("=true") else f"not ({text})"


def _holds(leaf: dict, facts: dict) -> bool:
    try:
        return bool(evaluate(leaf, facts))
    except PredicateError:
        return False


def _tri(pred: Any, facts: dict, unknown: set[str]) -> Optional[bool]:
    from .question_authority import tri
    return tri(pred, facts, unknown)


def signals(graph: RelationGraph, facts: dict) -> dict[str, dict]:
    """Each case signal's value and the condition that set it."""
    out = {}
    for name, spec in sorted((graph.signals or {}).items()):
        value, basis = spec.get("default", "UNKNOWN"), None
        for opt in spec.get("values") or []:
            if _holds(opt["when"], facts):
                value, basis = opt["value"], opt["when"]
                break
        out[name] = {"value": value, "basis": basis}
    return out


class KnowledgeMatcher:
    def __init__(self, kg):
        self.kg = kg
        self.graph: RelationGraph = kg.relations

    # --------------------------------------------------------------- match
    def match(self, case: CaseFile, facts: Optional[dict] = None) -> Match:
        facts = dict(case.fact_view() if facts is None else facts)
        sig = signals(self.graph, facts)
        sig_ids = {f"{k}={v['value']}" for k, v in sig.items()}
        evidence_kinds = set(facts.get("evidence_kinds") or [])
        # Semantic meaning may elevate a module to RELEVANT (never to SUPPORTED),
        # through the retrieval layer's typed routes - not by comparing words.
        # A vector hit alone is a suggestion, not a connection.
        from .knowledge_retrieval import retrieve_for_case
        retrieved = {c.module_id: c for c in
                     retrieve_for_case(self.kg, case, facts).candidates}
        semantic_routes = {"SEMANTIC_CONCEPT", "SEMANTIC_EVENT", "NARRATIVE_ATOM", "RELATIONSHIP"}
        out: dict[str, Candidate] = {}
        for module in sorted(self.kg.active_modules(), key=lambda m: m.module_id):
            c = self._one(case, module, facts, sig, sig_ids, evidence_kinds)
            # P17.9: semantic meaning may elevate OPEN → RELEVANT for discovery.
            # Never creates SUPPORTED. Eligibility remains use_when on facts.
            if c.status in (OPEN, RELEVANT):
                got = retrieved.get(module.module_id)
                hints = [f"{r.lower()}:{i}" for r in (got.retrieval_sources if got else [])
                         if r in semantic_routes
                         for i in (got.matched_concept_ids + got.matched_event_ids
                                   + got.matched_atom_ids + got.matched_relationship_ids)]
                if hints:
                    if c.status == OPEN:
                        c.status = RELEVANT
                    c.relevant_because.append({
                        "signal": "semantic_material",
                        "reason": "material semantic events/atoms/concepts connect",
                        "hints": hints[:6],
                    })
                    if "semantic candidate signal" not in (c.reason or ""):
                        c.reason = (c.reason or "") + "; semantic candidate signal"
            out[module.module_id] = c
        match = Match(sig, out, self.graph.version)
        # P8.1: the relationships are case state, not a value that lives only in
        # whichever engine happened to ask. Recorded through the master object so
        # "what blocked this module?" is answered from the case afterwards.
        case.master.record_knowledge_matches(match)
        return match

    def _one(self, case, module, facts, sig, sig_ids, evidence_kinds) -> Candidate:
        mid = module.module_id
        c = Candidate(mid, OPEN, module.topic, str(getattr(module.route, "value", module.route)),
                      module.strength)
        edges = self.graph.edges_of(mid)
        c.conflicts_with = sorted(e.target_id for e in edges
                                  if e.relationship_type == CONFLICTS_WITH and e.source_id == mid)

        # ---- BLOCKS: a curated signal edge, or a blocked condition that holds.
        for e in edges:
            if e.relationship_type != BLOCKS or e.target_id != mid:
                continue
            if e.source_type == "SIGNAL" and e.source_id in sig_ids:
                name = e.source_id.split("=", 1)[0]
                c.blocked_by.append({"signal": e.source_id, "relationship": BLOCKS,
                                     "reason": e.metadata.get("reason", ""),
                                     "basis": sig[name]["basis"], "edge_id": e.edge_id})
        try:
            dnuw = evaluate(module.do_not_use_when, facts)
            gate = evaluate(module.use_when, facts)
        except PredicateError as exc:
            c.status, c.reason = REJECTED, f"predicate error: {exc}"
            return c
        if dnuw:
            held = [_show(e.metadata["condition"]) for e in edges
                    if e.relationship_type == BLOCKS and e.metadata.get("from_field") == "do_not_use_when"
                    and _holds(e.metadata["condition"], facts)]
            c.blocked_by.append({"condition": held or ["do_not_use_when"],
                                 "relationship": BLOCKS,
                                 "reason": "blocked condition holds: " + ", ".join(
                                     held or ["do_not_use_when"])})
        if c.blocked_by:
            c.status = BLOCKED
            first = c.blocked_by[0]
            c.reason = first.get("reason") or "blocked"
            return c

        # ---- what holds now, with the fact (and its source) that makes it hold.
        for e in edges:
            if e.relationship_type not in (SUPPORTS, EVIDENCE_SUPPORTS) or e.target_id != mid:
                continue
            cond = e.metadata.get("condition")
            if cond is None:
                if e.source_type == "SIGNAL" and e.source_id in sig_ids:
                    c.relevant_because.append({"signal": e.source_id,
                                               "reason": e.metadata.get("reason", "")})
                continue
            if not _holds(cond, facts):
                continue
            if e.source_type == "EVIDENCE":
                c.evidence.append(e.source_id)
                c.selected_because.append({"condition": _show(cond), "evidence_kind": e.source_id})
            else:
                c.selected_because.append(self._fact_basis(case, e.source_id, cond))

        unknown = {f for f in referenced_facts(module.use_when) | set(module.required_facts or [])
                   if f not in facts}
        if gate:
            c.status = SUPPORTED
            c.missing = sorted(f for f in (module.required_facts or []) if f not in facts)
            c.reason = "use_when holds on verified facts and nothing blocks it"
            return c
        c.missing = sorted(f for f in unknown)
        possible = _tri(module.use_when, facts, unknown)
        unmet = [_show(leaf) for positive, leaf in _leaf_pairs(module.use_when)
                 if positive and not _holds(leaf, facts)]
        unmet += [_negate(_show(leaf)) for positive, leaf in _leaf_pairs(module.use_when)
                  if not positive and _holds(leaf, facts)]
        if possible is False:
            c.status = REJECTED
            c.reason = "use_when cannot hold on what is known: " + "; ".join(
                f"not established: {u}" for u in unmet[:4])
            return c
        c.reason = "use_when not yet met: " + "; ".join(
            f"no confirmed {u}" for u in unmet[:4]) + self._hypothesis_note(case, unmet)
        connected = bool(c.selected_because or c.relevant_because)
        c.status = RELEVANT if connected else OPEN
        return c

    # --------------------------------------------------------------- facts
    def _fact_basis(self, case: CaseFile, name: str, cond: dict) -> dict:
        """The fact behind a condition: value, source, node id - and, for a
        derived fact, the facts it was computed from (DEPENDS_ON)."""
        f = case.facts.get(name)
        row = {"condition": _show(cond), "fact": name}
        if f is not None:
            row.update(value=_plain(f.value), source=f.source.ref,
                       source_kind=f.source.kind.value, fact_id=case.facts.node_id(name))
        deps = self.graph.depends_on.get(name) or []
        held = []
        for d in deps:
            g = case.facts.get(d)
            if g is not None and g.usable and g.value not in (None, "", [], False):
                held.append({"fact": d, "value": _plain(g.value), "source": g.source.ref})
        if held:
            row["because_of"] = held
        return row

    @staticmethod
    def _hypothesis_note(case: CaseFile, unmet: list[str]) -> str:
        notes = [f" (hypothesis {h['hypothesis']} is {h['status']})"
                 for h in getattr(case, "fact_hypotheses", []) or []
                 if any(u.startswith(h["fact_name"]) for u in unmet)]
        return "".join(sorted(set(notes)))


def _leaf_pairs(pred):
    from ..kg.relations import _leaves
    return list(_leaves(pred))


def _plain(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


def match_case(kg, case: CaseFile) -> Match:
    return KnowledgeMatcher(kg).match(case)


__all__ = ["KnowledgeMatcher", "Match", "Candidate", "match_case", "signals",
           "SUPPORTED", "RELEVANT", "OPEN", "REJECTED", "BLOCKED"]
