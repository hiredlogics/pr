"""P17.9 — KnowledgeModuleResolver: candidate discovery ≠ eligibility.

Hard invariant:
  semantic similarity / pgvector / LLM proposal = CANDIDATE only
  authoritative facts + KB use_when / do_not_use_when = eligibility
  Claim Plan = final substantive authority
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .knowledge_matcher import (
    BLOCKED, OFFERABLE, OPEN, REJECTED, RELEVANT, SUPPORTED, KnowledgeMatcher, Match,
)

# Fact sources that are the customer's own account rather than the documents.
# A ground standing on one of these is the ground the account particularises.
_ACCOUNT_SOURCES = frozenset({"ANSWER", "CUSTOMER_FREE_TEXT"})

# Resolver-facing status vocabulary (maps matcher + eligibility).
STATUS_SUPPORTED = "SUPPORTED"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_REJECTED = "REJECTED"
STATUS_BLOCKED = "BLOCKED"


@dataclass
class ModuleResolveRow:
    module_id: str
    candidate_reason: str = ""
    status: str = STATUS_REJECTED
    supporting_facts: list[dict] = field(default_factory=list)
    supporting_events: list[dict] = field(default_factory=list)
    narrative_atoms: list[dict] = field(default_factory=list)
    required_facts: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    blocking_facts: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    required_particulars: list[str] = field(default_factory=list)
    matcher_status: str = ""
    eligible: bool = False
    topic: str = ""
    route: str = ""
    strength: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModuleResolveResult:
    fact_view: dict = field(default_factory=dict)
    match: Optional[Match] = None
    candidates: list[str] = field(default_factory=list)
    rows: dict[str, ModuleResolveRow] = field(default_factory=dict)
    eligible_ids: list[str] = field(default_factory=list)
    unresolved_ids: list[str] = field(default_factory=list)
    gate_why: dict = field(default_factory=dict)
    semantic_revision: str = ""
    trace: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "candidates": list(self.candidates),
            "eligible_ids": list(self.eligible_ids),
            "unresolved_ids": list(self.unresolved_ids),
            "rows": {k: v.as_dict() for k, v in self.rows.items()},
            "gate_why": dict(self.gate_why),
            "semantic_revision": self.semantic_revision,
            "match_trace": self.match.trace() if self.match is not None else {},
            "trace": list(self.trace),
            "invariant": {
                "candidate_is_not_eligibility": True,
                "vector_cannot_create_supported": True,
                "llm_cannot_create_supported": True,
            },
        }

    def by_status(self, *statuses: str) -> list[ModuleResolveRow]:
        return [r for r in self.rows.values() if r.status in statuses]


def _semantic_material(case) -> dict[str, list]:
    from ..semantics.understanding import customer_semantic_raw
    raw = customer_semantic_raw(case, "_semantic_case_state")
    if not raw:
        return {"events": [], "atoms": [], "relationships": [], "concepts": []}
    try:
        state = json.loads(raw)
    except Exception:
        return {"events": [], "atoms": [], "relationships": [], "concepts": []}
    return {
        "events": list(state.get("events") or state.get("customer_reported_events") or []),
        "atoms": list(state.get("narrative_atoms") or []),
        "relationships": list(state.get("relationships") or []),
        "concepts": list(state.get("concepts") or []),
    }


def _map_status(matcher_status: str, eligible: bool, missing: list[str]) -> str:
    if matcher_status == BLOCKED:
        return STATUS_BLOCKED
    if matcher_status == REJECTED:
        return STATUS_REJECTED
    if eligible and matcher_status == SUPPORTED:
        return STATUS_SUPPORTED
    if matcher_status in (RELEVANT, OPEN) or missing:
        return STATUS_UNRESOLVED
    if matcher_status == SUPPORTED and not eligible:
        # Gate held in matcher but eligibility withheld (e.g. code version).
        return STATUS_UNRESOLVED
    return STATUS_REJECTED


def _particulars_for(module, facts: dict) -> list[str]:
    req = list(getattr(module, "required_facts", None) or [])
    # Prefer facts that are present — drafting needs valued particulars.
    return [f for f in req if facts.get(f) not in (None, "", [], "UNKNOWN")]


class KnowledgeModuleResolver:
    """Candidate discovery + deterministic eligibility + explanation."""

    def __init__(self, kg, reasoning=None, retriever=None):
        self.kg = kg
        self.reasoning = reasoning
        self.retriever = retriever

    def resolve(
        self,
        case,
        *,
        fact_view: Optional[dict] = None,
        semantic_state: Optional[dict] = None,
        circumstances: str = "",
        code_version=None,
        analysis_engine=None,
    ) -> ModuleResolveResult:
        from ..legal import findings as legal_findings

        facts = dict(fact_view if fact_view is not None else case.fact_view())
        # Align with Claim Plan: gate facts include verified PoFA codes.
        calc = list(case.get("pofa_findings") or [])
        facts = legal_findings.gate_facts(facts, calc)

        trace = [
            "FACT_VIEW",
            "MODULE_CANDIDATE_DISCOVERY",
        ]
        match = KnowledgeMatcher(self.kg).match(case, facts)
        trace.append(f"matcher_supported={len(match.supported)}")
        trace.append(f"matcher_relevant={len(match.by_status(RELEVANT))}")

        # Candidate discovery (may use vector rank) — NEVER creates SUPPORTED alone.
        candidate_modules = []
        if analysis_engine is not None and hasattr(analysis_engine, "_candidates"):
            candidate_modules = analysis_engine._candidates(
                case, circumstances, facts, match)
        else:
            from .knowledge_retrieval import record_retrieval, retrieve_for_case
            retrieved = retrieve_for_case(self.kg, case, facts)
            offerable = {c.module_id for c in match.by_status(*OFFERABLE)}
            candidate_modules = [
                self.kg.modules[mid] for mid in retrieved.module_ids
                if mid in offerable and mid in self.kg.modules
            ][:24]
            record_retrieval(case, retrieved, [m.module_id for m in candidate_modules])
        candidate_ids = [m.module_id for m in candidate_modules]
        trace.append(f"candidates={len(candidate_ids)}")
        trace.append("MODULE_ELIGIBILITY")

        # Deterministic eligibility (R-03/R-04).
        eligible_mods: list = []
        gate_why: dict = {}
        if self.reasoning is not None:
            version = code_version
            if version is None:
                version, _ = self.reasoning.applicability(case)
            eligible_mods, gate_why = self.reasoning.eligibility(facts, version, [])
            eligible_ids = [m.module_id for m in eligible_mods]
            trace.append("eligibility_source=reasoning_engine")
        else:
            # Without the reasoning engine this pass used to report NOTHING as
            # eligible, so every row came back UNRESOLVED and `status` could not
            # tell a ground whose conditions already hold from one still missing
            # a fact. The matcher's SUPPORTED status is the same deterministic
            # gate (use_when and not do_not_use_when, nothing blocking) read off
            # the same fact view, so it stands in. Still deterministic, still
            # facts-and-KB only: no model and no retrieval can reach it.
            eligible_ids = [c.module_id for c in match.supported]
            trace.append("eligibility_source=matcher_gate")
        eligible_set = set(eligible_ids)

        sem = semantic_state or _semantic_material(case)
        events = list(sem.get("events") or [])
        atoms = list(sem.get("atoms") or sem.get("narrative_atoms") or [])
        rels = list(sem.get("relationships") or [])

        rows: dict[str, ModuleResolveRow] = {}
        # Explain every matched module + every candidate.
        seen = set(match.candidates) | set(candidate_ids) | eligible_set
        for mid in sorted(seen):
            mod = self.kg.modules.get(mid)
            if mod is None:
                continue
            cand = match.candidates.get(mid)
            matcher_status = cand.status if cand else OPEN
            missing = list(cand.missing) if cand else list(mod.required_facts or [])
            eligible = mid in eligible_set
            status = _map_status(matcher_status, eligible, missing)
            support_facts = list(cand.selected_because) if cand else []
            # Attach material semantic events/atoms generically (category tags
            # only) — but only to a ground that actually rests on the
            # customer's account. A statutory timing ground rests on notice
            # dates, so the shopping trip is not its particular: attaching the
            # account to every ground put the keeper's errand inside the PoFA
            # paragraph and made the validator demand it there. The test is the
            # SOURCE of the facts the ground stands on, never the module id.
            rests_on_account = any(
                str(r.get("source_kind") or "") in _ACCOUNT_SOURCES
                for r in support_facts if isinstance(r, dict))
            tagged_events = [
                e for e in events
                if isinstance(e, dict) and (
                    e.get("material") or e.get("kind") or e.get("type")
                )
            ][:8] if rests_on_account else []
            tagged_atoms = [
                a for a in atoms
                if isinstance(a, dict) and (
                    a.get("material") is not False
                )
            ][:8] if rests_on_account else []
            row = ModuleResolveRow(
                module_id=mid,
                candidate_reason=(
                    (cand.reason if cand else "")
                    or ("offered_by_relation_or_retrieval" if mid in candidate_ids else "")
                ),
                status=status,
                supporting_facts=support_facts,
                supporting_events=tagged_events,
                narrative_atoms=tagged_atoms,
                required_facts=list(mod.required_facts or []),
                missing_facts=missing,
                blocking_facts=list(cand.blocked_by) if cand else [],
                relationships=rels[:12],
                evidence_refs=list(cand.evidence) if cand else [],
                required_particulars=_particulars_for(mod, facts),
                matcher_status=matcher_status,
                eligible=eligible,
                topic=getattr(mod, "topic", "") or (cand.topic if cand else ""),
                route=str(getattr(mod, "route", "") or (cand.route if cand else "")),
                strength=int(getattr(mod, "strength", 0) or (cand.strength if cand else 0)),
            )
            rows[mid] = row

        unresolved = [
            mid for mid, r in rows.items()
            if r.status == STATUS_UNRESOLVED and mid in candidate_ids
        ]
        result = ModuleResolveResult(
            fact_view=facts,
            match=match,
            candidates=candidate_ids,
            rows=rows,
            eligible_ids=eligible_ids,
            unresolved_ids=unresolved,
            gate_why=dict(gate_why or {}),
            semantic_revision=str(
                (case.raw_answers or {}).get("_semantic_revision") or ""),
            trace=trace + [
                f"eligible={len(eligible_ids)}",
                f"unresolved={len(unresolved)}",
                "CASE_INTELLIGENCE_CONSUMES_CANDIDATES_ONLY",
            ],
        )
        case.raw_answers["_module_resolve"] = json.dumps(
            result.as_dict(), default=str)[:20000]
        case.audit.append({
            "event": "knowledge_module_resolver",
            "candidates": candidate_ids,
            "eligible_ids": eligible_ids,
            "unresolved_ids": unresolved,
            "supported": [r.module_id for r in result.by_status(STATUS_SUPPORTED)],
            "blocked": [r.module_id for r in result.by_status(STATUS_BLOCKED)],
            "trace": result.trace,
            "invariant_candidate_ne_eligibility": True,
        })
        return result


def resolve(case, kg, *, reasoning=None, retriever=None, **kwargs) -> ModuleResolveResult:
    return KnowledgeModuleResolver(kg, reasoning=reasoning, retriever=retriever).resolve(
        case, **kwargs)
