"""Claim Plan Authority (P5): the one decision object a letter is written from.

    FACTS + KNOWLEDGE RELATIONSHIPS + EVIDENCE
        -> CASE INTELLIGENCE (proposes)          engines/analysis.py, engines/claim_plan.py
        -> CLAIM PLAN BUILDER (decides)          ClaimPlanBuilder.build
        -> FINAL CLAIM PLAN -> LOCK              FinalClaimPlan.confirm / lock
        -> DRAFTING (locked plan only)           ReasoningEngine.pack_for
        -> VALIDATION (every argument in plan)   ValidationEngine VAL-PLAN

Everything upstream PROPOSES: case analysis, its bounded reassessment, the
orchestrator's ground recovery, the analysis-stage veto in engines/claim_plan.py.
Only this builder DECIDES. Every proposed, offered or blocked module becomes one
item, SUPPORTED / REJECTED / UNRESOLVED, with why it was selected or rejected and
which facts, relationships and evidence support it.

A claim is SUPPORTED only when all of these hold:
  * Case Intelligence selected it (the plan never adds a ground on its own);
  * it is in force and no BLOCKS relationship fires for this case;
  * the reasoning gate keeps it (R-03 use_when / do_not_use_when, R-01 Code
    version, R-04 conflicts) - the same function the pack used to apply, so the
    pack can never silently drop an approved claim;
  * at least one verified fact (or uploaded evidence) supports it;
  * evidence it cannot be argued without has been uploaded (else UNRESOLVED).

LOCKED is final: no item can be added, removed or re-prioritised and no reason
changes, in memory (ClaimPlanLockedError) or in the database (triggers in
0006_claim_plan_authority.sql). New information produces a new version; the old
one is only marked SUPERSEDED. The same inputs give the same plan: an unchanged
case re-uses its locked plan rather than minting a version.

Deterministic: no model, no retrieval, sorted output.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Iterable, Optional

from ..legal import findings as legal_findings
from ..models import CaseFile
from ..rules.dsl import PredicateError, evaluate, referenced_facts

BUILDER_VERSION = "1"

DRAFT, CONFIRMED, LOCKED, SUPERSEDED = "DRAFT", "CONFIRMED", "LOCKED", "SUPERSEDED"
PLAN_STATUSES = (DRAFT, CONFIRMED, LOCKED, SUPERSEDED)
SUPPORTED, REJECTED, UNRESOLVED = "SUPPORTED", "REJECTED", "UNRESOLVED"
ITEM_STATUSES = (SUPPORTED, REJECTED, UNRESOLVED)

# Why an item has its status. Admin trace and "why excluded?" read these.
SELECTED = "SELECTED"                       # supported, argued
BLOCKED = "BLOCKED"                         # a BLOCKS relationship fires
NOT_ACTIVE = "NOT_ACTIVE"                   # not an in-force module
GATE = "GATE"                               # reasoning gate / Code / conflict
VETOED = "VETOED"                           # analysis-stage veto (engines/claim_plan.py)
NO_SUPPORTING_FACTS = "NO_SUPPORTING_FACTS"
EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
NOT_SELECTED = "NOT_SELECTED"               # offered / gate holds, CI did not choose it
MISSING_FACTS = "MISSING_FACTS"             # could apply; facts still unknown
NO_VERIFIED_FINDING = "NO_VERIFIED_FINDING"  # P6.1: legal defect not verified
# P6.2: supported grounds are cumulative.
VERIFIED_FINDING = "VERIFIED_FINDING"       # a calculated statutory defect: argued on the
                                            # calculation, independently of model selection
CARRIED_FORWARD = "CARRIED_FORWARD"         # supported in the previous locked plan and the
                                            # gate + supporting facts still hold

_NS = uuid.UUID("6b1f4c1e-5f0a-4d8e-9c55-0d7a5c1a9e05")
_MUTABLE_WHEN_LOCKED = frozenset({"status", "superseded_at", "superseded_by"})


class ClaimPlanLockedError(RuntimeError):
    """A LOCKED (or SUPERSEDED) plan was asked to change."""


class ClaimPlanIntegrityError(RuntimeError):
    """A stored plan's content no longer matches the digest it was locked with."""


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _freeze(v: Any) -> Any:
    if isinstance(v, dict):
        return MappingProxyType({k: _freeze(x) for k, x in v.items()})
    if isinstance(v, (list, tuple)):
        return tuple(_freeze(x) for x in v)
    return v


def _thaw(v: Any) -> Any:
    if isinstance(v, (dict, MappingProxyType)):
        return {k: _thaw(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_thaw(x) for x in v]
    return v


def _plain(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


def claim_label(module_id: str) -> str:
    """The family a module argues, for customer-neutral messages:
    KB-ANPR-01 -> ANPR, KB-POFA-04 -> POFA."""
    m = re.match(r"^KB-([A-Z]+)-", module_id or "")
    return m.group(1) if m else (module_id or "UNKNOWN")


def run_uuid(case_id: str, run_id: int) -> str:
    return str(uuid.uuid5(_NS, f"{case_id}:run:{int(run_id or 0)}"))


# ------------------------------------------------------------------ model
@dataclass(frozen=True)
class ClaimPlanItem:
    item_id: str
    knowledge_id: str
    module_id: str
    claim_type: str
    status: str
    decision: str
    reason: str
    supporting_facts: tuple = ()
    evidence_refs: tuple = ()
    relationships: tuple = ()
    priority: Optional[int] = None
    topic: str = ""

    def content(self) -> dict:
        """What the plan decided about this claim - no ids, no timestamps -
        so two cases on the same facts compare equal."""
        return {"module_id": self.module_id, "claim_type": self.claim_type,
                "status": self.status, "decision": self.decision, "reason": self.reason,
                "supporting_facts": [{k: v for k, v in _thaw(f).items()
                                      if k not in ("fact_id", "source")}
                                     for f in self.supporting_facts],
                "evidence_refs": sorted({_thaw(e).get("kind") for e in self.evidence_refs}),
                "priority": self.priority}

    def as_dict(self) -> dict:
        return {"item_id": self.item_id, "knowledge_id": self.knowledge_id,
                "module_id": self.module_id, "claim_type": self.claim_type,
                "status": self.status, "decision": self.decision, "reason": self.reason,
                "supporting_facts": _thaw(self.supporting_facts),
                "evidence_refs": _thaw(self.evidence_refs),
                "relationships": _thaw(self.relationships),
                "priority": self.priority, "topic": self.topic}

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimPlanItem":
        return cls(d["item_id"], d["knowledge_id"], d["module_id"], d.get("claim_type") or "",
                   d["status"], d.get("decision") or "", d.get("reason") or "",
                   _freeze(d.get("supporting_facts") or []), _freeze(d.get("evidence_refs") or []),
                   _freeze(d.get("relationships") or []), d.get("priority"),
                   d.get("topic") or "")


class FinalClaimPlan:
    """One version of a case's claim plan. See the module docstring."""

    def __init__(self, *, claim_plan_id: str, case_id: str, analysis_run_id: str,
                 run_number: int, version: int, inputs_digest: str, trust: dict,
                 material_fact_accounting: Optional[list] = None,
                 created_at: Optional[str] = None):
        self._locked = False
        self.claim_plan_id = claim_plan_id
        self.case_id = case_id
        self.analysis_run_id = analysis_run_id
        self.run_number = int(run_number or 0)
        self.version = int(version)
        self.status = DRAFT
        self.created_at = created_at or _now()
        self.confirmed_at: Optional[str] = None
        self.locked_at: Optional[str] = None
        self.superseded_at: Optional[str] = None
        self.superseded_by: Optional[str] = None
        self.inputs_digest = inputs_digest
        self.plan_digest: Optional[str] = None
        self.trust = _freeze(dict(trust))
        self.material_fact_accounting = _freeze(list(material_fact_accounting or []))
        self.items: tuple = ()

    # -------------------------------------------------------- immutability
    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_locked", False):
            if name not in _MUTABLE_WHEN_LOCKED:
                raise ClaimPlanLockedError(
                    f"claim plan {self.claim_plan_id} v{self.version} is {self.status}: "
                    f"{name} cannot change - create a new version")
            if name == "status" and not (self.status == LOCKED and value == SUPERSEDED):
                raise ClaimPlanLockedError(
                    f"claim plan v{self.version}: {self.status} -> {value} is not allowed")
        object.__setattr__(self, name, value)

    @property
    def is_locked(self) -> bool:
        return self._locked

    def add_item(self, item: ClaimPlanItem) -> None:
        if self.status != DRAFT:
            raise ClaimPlanLockedError(f"items can only be added to a DRAFT plan, not {self.status}")
        if any(i.module_id == item.module_id for i in self.items):
            raise ValueError(f"{item.module_id} is already in the plan")
        self.items = self.items + (item,)

    # ------------------------------------------------------------- lifecycle
    def confirm(self) -> "FinalClaimPlan":
        """DRAFT -> CONFIRMED once the plan is internally sound: every item has
        a reason, every supported claim names what supports it, priorities are
        1..n over the supported claims."""
        if self.status != DRAFT:
            raise ClaimPlanLockedError(f"only a DRAFT plan can be confirmed, not {self.status}")
        problems = [f"{i.module_id}: no reason" for i in self.items if not i.reason]
        problems += [f"{i.module_id}: supported without supporting facts"
                     for i in self.items if i.status == SUPPORTED and not i.supporting_facts]
        problems += [f"{i.module_id}: bad status {i.status}"
                     for i in self.items if i.status not in ITEM_STATUSES]
        prios = sorted(i.priority for i in self.items if i.status == SUPPORTED)
        if prios != list(range(1, len(prios) + 1)):
            problems.append(f"supported priorities are not 1..n: {prios}")
        if problems:
            raise ValueError("claim plan cannot be confirmed: " + "; ".join(problems))
        self.status = CONFIRMED
        self.confirmed_at = _now()
        return self

    def lock(self) -> "FinalClaimPlan":
        if self.status != CONFIRMED:
            raise ClaimPlanLockedError(f"only a CONFIRMED plan can be locked, not {self.status}")
        self.plan_digest = self.content_digest()
        self.status = LOCKED
        self.locked_at = _now()
        self._locked = True
        return self

    def supersede(self, by: "FinalClaimPlan") -> None:
        if self.status != LOCKED:
            raise ClaimPlanLockedError(f"only a LOCKED plan can be superseded, not {self.status}")
        self.status = SUPERSEDED
        self.superseded_at = _now()
        self.superseded_by = by.claim_plan_id

    def content_digest(self) -> str:
        return _sha([i.content() for i in self.items])

    # --------------------------------------------------------------- views
    @property
    def supported(self) -> list[ClaimPlanItem]:
        return sorted((i for i in self.items if i.status == SUPPORTED), key=lambda i: i.priority)

    @property
    def supported_ids(self) -> list[str]:
        return [i.module_id for i in self.supported]

    @property
    def rejected(self) -> list[ClaimPlanItem]:
        return [i for i in self.items if i.status == REJECTED]

    @property
    def unresolved(self) -> list[ClaimPlanItem]:
        return [i for i in self.items if i.status == UNRESOLVED]

    def item(self, module_id: str) -> Optional[ClaimPlanItem]:
        return next((i for i in self.items if i.module_id == module_id), None)

    def required_evidence(self) -> list[dict]:
        return [{"module_id": i.module_id, "evidence": [_thaw(e) for e in i.evidence_refs
                                                        if not _thaw(e).get("uploaded")]}
                for i in self.unresolved if i.decision == EVIDENCE_REQUIRED]

    def for_drafting(self) -> dict:
        """What the drafter may see: approved claims, in order, and nothing
        about rejected, blocked or candidate modules."""
        approved = set(self.supported_ids)
        accounting = []
        for row in _thaw(self.material_fact_accounting):
            row = dict(row)
            row["relations"] = [r for r in row.get("relations") or []
                                if r.get("module_id") in approved]
            accounting.append(row)
        return {
            "claim_plan_id": self.claim_plan_id, "version": self.version,
            "status": self.status,
            "module_ids": self.supported_ids,
            "claims": [{"module_id": i.module_id, "claim_type": i.claim_type,
                        "priority": i.priority,
                        "supported_by": [_thaw(f).get("condition") for f in i.supporting_facts]}
                       for i in self.supported],
            "material_fact_accounting": accounting,
        }

    def for_validation(self) -> dict:
        return {"claim_plan_id": self.claim_plan_id, "version": self.version,
                "status": self.status, "plan_digest": self.plan_digest,
                "approved": self.supported_ids,
                "labels": {i.module_id: claim_label(i.module_id) for i in self.items}}

    def trace(self) -> list[str]:
        """Admin-only. One line per item: what was selected and why, what was
        rejected or blocked and why, what is unresolved."""
        out = []
        for i in self.items:
            if i.status == SUPPORTED:
                why = ", ".join(_because(f) for f in i.supporting_facts)
                rels = sorted({_thaw(r).get("relationship") for r in i.relationships
                               if _thaw(r).get("relationship") in ("SUPPORTS", "EVIDENCE_SUPPORTS")}
                              ) or ["SUPPORTS"]
                out.append(f"Selected {i.module_id} (priority {i.priority}), because {why}, "
                           f"relationship {'/'.join(rels)}")
            elif i.decision == BLOCKED:
                out.append(f"Blocked {i.module_id}, reason: {i.reason}")
            elif i.status == UNRESOLVED:
                out.append(f"Unresolved {i.module_id}, reason: {i.reason}")
            else:
                out.append(f"Rejected {i.module_id}, reason: {i.reason}")
        return out

    def explain(self, module_id: str) -> str:
        """"Why was this argument included?" / "Why excluded?"."""
        i = self.item(module_id)
        if i is None:
            return (f"{module_id} was not proposed, offered or blocked for this case, "
                    f"so claim plan v{self.version} does not consider it")
        line = next(t for t, it in zip(self.trace(), self.items) if it is i)
        return f"claim plan v{self.version} ({self.status}): {line}"

    # -------------------------------------------------------- serialisation
    def as_dict(self) -> dict:
        return {"claim_plan_id": self.claim_plan_id, "case_id": self.case_id,
                "analysis_run_id": self.analysis_run_id, "run_number": self.run_number,
                "version": self.version, "status": self.status,
                "created_at": self.created_at, "confirmed_at": self.confirmed_at,
                "locked_at": self.locked_at, "superseded_at": self.superseded_at,
                "superseded_by": self.superseded_by, "inputs_digest": self.inputs_digest,
                "plan_digest": self.plan_digest, "trust": _thaw(self.trust),
                "material_fact_accounting": _thaw(self.material_fact_accounting),
                "items": [i.as_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, d: dict) -> "FinalClaimPlan":
        plan = cls(claim_plan_id=d["claim_plan_id"], case_id=d["case_id"],
                   analysis_run_id=d["analysis_run_id"], run_number=d.get("run_number") or 0,
                   version=d["version"], inputs_digest=d.get("inputs_digest") or "",
                   trust=d.get("trust") or {},
                   material_fact_accounting=d.get("material_fact_accounting") or [],
                   created_at=d.get("created_at"))
        plan.items = tuple(ClaimPlanItem.from_dict(i) for i in d.get("items") or [])
        for k in ("confirmed_at", "locked_at", "superseded_at", "superseded_by", "plan_digest"):
            object.__setattr__(plan, k, d.get(k))
        object.__setattr__(plan, "status", d["status"])
        if plan.status in (LOCKED, SUPERSEDED):
            if plan.plan_digest != plan.content_digest():
                raise ClaimPlanIntegrityError(
                    f"claim plan {plan.claim_plan_id} v{plan.version}: stored items do not "
                    f"match the digest it was locked with")
            object.__setattr__(plan, "_locked", True)
        return plan


def _because(f: Any) -> str:
    f = _thaw(f)
    if f.get("evidence_kind"):
        return f"evidence {f['evidence_kind']} uploaded"
    text = f"fact {f.get('condition') or f.get('fact')}"
    deps = [f"{d['fact']}={json.dumps(d.get('value'), default=str)}"
            for d in f.get("because_of") or []]
    return text + (f" (from {', '.join(deps)})" if deps else "")


def diff(a: Optional[FinalClaimPlan], b: FinalClaimPlan) -> dict:
    """"What changed between runs?" - claims, priorities, facts and versions."""
    old = {i.module_id: i for i in (a.items if a else ())}
    new = {i.module_id: i for i in b.items}
    changed = []
    for mid in sorted(set(old) & set(new)):
        x, y = old[mid], new[mid]
        if (x.status, x.decision, x.reason, x.priority) != (y.status, y.decision, y.reason,
                                                             y.priority):
            changed.append({"module_id": mid, "from": {"status": x.status, "priority": x.priority,
                                                       "reason": x.reason},
                            "to": {"status": y.status, "priority": y.priority,
                                   "reason": y.reason}})
    fa = _thaw(a.trust).get("facts_used", {}) if a else {}
    fb = _thaw(b.trust).get("facts_used", {})
    ta = {k: v for k, v in (_thaw(a.trust) if a else {}).items() if k not in ("facts_used",
                                                                           "relationships_used")}
    tb = {k: v for k, v in _thaw(b.trust).items() if k not in ("facts_used", "relationships_used")}
    return {
        "from_version": a.version if a else None, "to_version": b.version,
        "approved_before": a.supported_ids if a else [], "approved_after": b.supported_ids,
        "added": [new[m].as_dict() for m in sorted(set(new) - set(old))],
        "removed": [old[m].as_dict() for m in sorted(set(old) - set(new))],
        "changed": changed,
        "facts": {"added": sorted(set(fb) - set(fa)), "removed": sorted(set(fa) - set(fb)),
                  "changed": sorted(k for k in set(fa) & set(fb)
                                    if fa[k].get("value") != fb[k].get("value"))},
        "versions": {k: {"from": ta.get(k), "to": tb.get(k)} for k in sorted(set(ta) | set(tb))
                     if ta.get(k) != tb.get(k)},
    }


def latest_locked(case: CaseFile) -> Optional[FinalClaimPlan]:
    return next((p for p in reversed(case.claim_plans or []) if p.status == LOCKED), None)


# ------------------------------------------------------------------ builder
class ClaimPlanBuilder:
    """The single builder. Input: Case Intelligence's proposals (as recorded on
    the case), the knowledge relationships, the verified facts and the uploaded
    evidence. Output: one FinalClaimPlan."""

    def __init__(self, kg, reasoning):
        self.kg = kg
        self.reasoning = reasoning

    # ------------------------------------------------------------- inputs
    @staticmethod
    def proposals(case: CaseFile) -> dict:
        """What Case Intelligence proposed and what the analysis-stage veto said.
        Read from the case (selection + the latest analysis audit), so a
        reloaded case decides from what was recorded, not from memory."""
        event = next((a for a in reversed(case.audit) if a.get("event") == "case_analysis"),
                     None) or {}
        cp = event.get("claim_plan") or {}
        vetoed = {}
        for c in cp.get("claims") or []:
            if c.get("status") == "excluded" and c.get("module_id"):
                vetoed.setdefault(c["module_id"], c.get("reason") or "excluded")
        for s in event.get("suppressed") or []:
            if s.get("module_id"):
                vetoed.setdefault(s["module_id"], s.get("why") or "suppressed")
        return {"selected": [m for m in (case.analysis_module_ids or []) if m],
                "proposed": [m for m in (event.get("proposed") or []) if m],
                "vetoed": dict(sorted(vetoed.items())),
                "omitted": sorted(cp.get("omitted_gate_satisfied") or []),
                "candidates": sorted(event.get("candidates") or []),
                "material_fact_accounting": list(cp.get("material_fact_accounting") or [])}

    def inputs_digest(self, case: CaseFile, facts: dict, proposals: dict,
                      code_version: Optional[str]) -> str:
        from ..manifest import kb_digest
        return _sha({"facts": {k: _plain(v) for k, v in sorted(facts.items())},
                     "evidence": sorted((e.kind, e.evidence_id) for e in case.evidence.values()
                                        if e.uploaded),
                     "proposals": {k: v for k, v in proposals.items()
                                   if k != "material_fact_accounting"},
                     "kb": kb_digest(self.kg),
                     "relations": getattr(self.kg.relations, "version", None),
                     "practice_code_version": code_version,
                     "builder": BUILDER_VERSION})

    # -------------------------------------------------------------- build
    def build(self, case: CaseFile, *, version: int = 1, trust: Optional[dict] = None,
              proposals: Optional[dict] = None) -> FinalClaimPlan:
        """A DRAFT plan for the case as it stands. Pure apart from the
        applicability facts the reasoning engine always derives."""
        from ..knowledge_ingestion.graph import knowledge_id
        from ..manifest import kb_digest
        from .knowledge_matcher import KnowledgeMatcher

        code, _pofa = self.reasoning.applicability(case)
        code_version = getattr(code, "version_id", None)
        facts = case.fact_view()
        # P6.1: the VERIFIED legal findings the applicability run just recorded.
        verified_findings = legal_findings.verified_types(
            case.legal_findings, getattr(_pofa, "findings", []) or [])
        proposals = proposals if proposals is not None else self.proposals(case)
        match = KnowledgeMatcher(self.kg).match(case, facts)
        kept, gate_why = self.reasoning.eligibility(facts, code)
        kept_ids = {m.module_id for m in kept}
        uploaded = {e.kind: e.evidence_id for e in sorted(case.evidence.values(),
                                                           key=lambda e: e.evidence_id)
                    if e.uploaded}
        digest = self.inputs_digest(case, facts, proposals, code_version)
        plan_id = str(uuid.uuid5(_NS, f"{case.case_id}:claim-plan:v{version}:{digest}"))

        decided: dict[str, dict] = {}

        def put(mid: str, status: str, decision: str, reason: str, support=(), evidence=()):
            if mid in decided:
                return
            m = self.kg.modules.get(mid)
            decided[mid] = dict(status=status, decision=decision, reason=reason,
                                support=list(support), evidence=list(evidence),
                                claim_type=str(getattr(getattr(m, "route", None), "value",
                                                       getattr(m, "route", "")) or ""),
                                topic=getattr(m, "topic", "") or "")

        # 1. Case Intelligence's selection - the only route to SUPPORTED.
        for mid in proposals["selected"]:
            m = self.kg.modules.get(mid)
            cand = match.candidates.get(mid)
            if m is None or m.status != "ACTIVE":
                put(mid, REJECTED, NOT_ACTIVE, "not an in-force knowledge module")
                continue
            if cand is not None and cand.status == "BLOCKED":
                put(mid, REJECTED, BLOCKED, cand.reason or "blocked by relationship")
                continue
            # P6.1: a defect ground stands or falls with its VERIFIED finding.
            finding_refusal = legal_findings.rejection(m, facts, verified_findings)
            if finding_refusal:
                put(mid, REJECTED, NO_VERIFIED_FINDING, finding_refusal)
                continue
            if mid not in kept_ids:
                why = gate_why.get(mid, "reasoning gate does not keep it")
                if legal_findings.referenced_findings(m) and not (
                        legal_findings.referenced_findings(m) & verified_findings):
                    why = (f"{legal_findings.REJECTION_REASON}: no verified legal finding "
                           "licenses this ground")
                put(mid, REJECTED, GATE, why)
                continue
            support = self._support(m, cand, facts)
            if not support:
                put(mid, REJECTED, NO_SUPPORTING_FACTS,
                    "no verified fact or uploaded evidence supports it")
                continue
            evidence = self._evidence(m, uploaded)
            missing = self._required_evidence_missing(m, uploaded)
            if missing:
                put(mid, UNRESOLVED, EVIDENCE_REQUIRED,
                    "evidence required before it can be argued: " + ", ".join(missing),
                    support, evidence + [{"kind": k, "uploaded": False} for k in missing])
                continue
            put(mid, SUPPORTED, SELECTED,
                "selected by Case Intelligence; gate holds, nothing blocks it, "
                "supported by verified facts", support, evidence)

        # 1b. P6.2: a ground licensed by a VERIFIED legal finding is argued on
        # the calculation. Case Intelligence adds judgment grounds; it cannot
        # subtract a statutory defect the deterministic engine proved.
        for m in kept:
            mid = m.module_id
            if mid in decided:
                continue
            lic = legal_findings.referenced_findings(m) & verified_findings
            if not lic:
                continue
            cand = match.candidates.get(mid)
            if cand is not None and cand.status == "BLOCKED":
                continue                      # step 3 records the block
            support = self._support(m, cand, facts)
            if not support:
                continue
            missing = self._required_evidence_missing(m, uploaded)
            evidence = self._evidence(m, uploaded)
            if missing:
                put(mid, UNRESOLVED, EVIDENCE_REQUIRED,
                    "evidence required before it can be argued: " + ", ".join(missing),
                    support, evidence + [{"kind": k, "uploaded": False} for k in missing])
                continue
            put(mid, SUPPORTED, VERIFIED_FINDING,
                "licensed by verified legal finding " + "/".join(sorted(lic)) +
                "; a calculated statutory defect is argued independently of selection",
                support, evidence)

        # 1c. P6.2: grounds are cumulative across plan versions. A ground the
        # latest LOCKED plan supported stays supported while its module is
        # active, its gate holds, nothing blocks it and its support stands;
        # a new customer fact can add grounds, never silently remove one.
        previous = latest_locked(case)
        for item in (previous.supported if previous is not None else []):
            mid = item.module_id
            m = self.kg.modules.get(mid)
            if mid in decided or m is None or m.status != "ACTIVE":
                continue
            cand = match.candidates.get(mid)
            if cand is not None and cand.status == "BLOCKED":
                continue                      # step 3 records the block
            if mid not in kept_ids:
                put(mid, REJECTED, GATE,
                    f"supported in plan v{previous.version} but the gate no longer holds "
                    "on the current facts: " + gate_why.get(mid, "reasoning gate"))
                continue
            refusal = legal_findings.rejection(m, facts, verified_findings)
            if refusal:
                put(mid, REJECTED, NO_VERIFIED_FINDING, refusal)
                continue
            support = self._support(m, cand, facts)
            if not support:
                put(mid, REJECTED, NO_SUPPORTING_FACTS,
                    f"supported in plan v{previous.version} but no verified fact or "
                    "uploaded evidence supports it any more")
                continue
            missing = self._required_evidence_missing(m, uploaded)
            evidence = self._evidence(m, uploaded)
            if missing:
                put(mid, UNRESOLVED, EVIDENCE_REQUIRED,
                    "evidence required before it can be argued: " + ", ".join(missing),
                    support, evidence + [{"kind": k, "uploaded": False} for k in missing])
                continue
            put(mid, SUPPORTED, CARRIED_FORWARD,
                f"supported in plan v{previous.version}; gate and supporting facts "
                "still hold", support, evidence)

        # 2. Proposals the analysis-stage veto already refused.
        for mid, why in proposals["vetoed"].items():
            cand = match.candidates.get(mid)
            if cand is not None and cand.status == "BLOCKED":
                put(mid, REJECTED, BLOCKED, cand.reason or why)
            else:
                put(mid, REJECTED, VETOED, why)

        # 3. Blocked knowledge, proposed or not: the trace must say what blocked it.
        for cand in match.blocked:
            put(cand.module_id, REJECTED, BLOCKED, cand.reason or "blocked by relationship")

        # 4. What was offered and not chosen.
        offered = sorted(set(proposals["candidates"]) | set(proposals["omitted"])
                         | {c.module_id for c in match.supported})
        for mid in offered:
            cand = match.candidates.get(mid)
            if cand is None:
                continue
            if cand.status == "SUPPORTED":
                put(mid, REJECTED, NOT_SELECTED,
                    "gate holds on the facts but Case Intelligence did not select it",
                    self._support(self.kg.modules[mid], cand, facts))
            elif cand.status == "RELEVANT" and cand.missing:
                put(mid, UNRESOLVED, MISSING_FACTS,
                    "could apply; not established: " + ", ".join(cand.missing))
            else:
                put(mid, REJECTED, GATE, "offered, not argued: " + (cand.reason or
                                                                 "gate does not hold"))

        # Priority: KB-GOV-07 drafting order over the supported claims only.
        order = [m.module_id for m in self.reasoning._drafting_priority(
            [self.kg.modules[mid] for mid, d in decided.items() if d["status"] == SUPPORTED])]
        rank = {mid: n for n, mid in enumerate(order, 1)}
        status_order = {SUPPORTED: 0, UNRESOLVED: 1, REJECTED: 2}

        trust = dict(trust or {})
        trust.update({
            "kb_version": {"release_id": getattr(self.kg, "release_id", None),
                           "digest": kb_digest(self.kg),
                           "relations_version": getattr(self.kg.relations, "version", None)},
            "practice_code_version": code_version,
            "builder_version": BUILDER_VERSION,
        })
        plan = FinalClaimPlan(claim_plan_id=plan_id, case_id=case.case_id,
                              analysis_run_id=run_uuid(case.case_id, case.run_id),
                              run_number=case.run_id, version=version, inputs_digest=digest,
                              trust={}, material_fact_accounting=proposals[
                                  "material_fact_accounting"])
        used_facts: set[str] = set()
        relationships: list[dict] = []
        for mid, d in sorted(decided.items(),
                             key=lambda kv: (status_order[kv[1]["status"]],
                                             rank.get(kv[0], 0), kv[0])):
            rels = self._relationships(mid, match, facts)
            relationships += [dict(r, module_id=mid) for r in rels]
            for f in d["support"]:
                if f.get("fact"):
                    used_facts.add(f["fact"])
                for dep in f.get("because_of") or []:
                    used_facts.add(dep["fact"])
            plan.add_item(ClaimPlanItem(
                item_id=str(uuid.uuid5(uuid.UUID(plan_id), mid)), knowledge_id=knowledge_id(mid),
                module_id=mid, claim_type=d["claim_type"], status=d["status"],
                decision=d["decision"], reason=d["reason"],
                supporting_facts=_freeze(d["support"]), evidence_refs=_freeze(d["evidence"]),
                relationships=_freeze(rels), priority=rank.get(mid), topic=d["topic"]))
        trust["facts_used"] = self._facts_used(case, used_facts)
        trust["relationships_used"] = relationships
        trust["signals"] = {k: v["value"] for k, v in match.signals.items()}
        trust["proposals"] = {k: v for k, v in proposals.items()
                              if k != "material_fact_accounting"}
        plan.trust = _freeze(trust)
        return plan

    # ------------------------------------------------------------- decide
    def decide(self, case: CaseFile, *, trust: Optional[dict] = None) -> FinalClaimPlan:
        """The case's LOCKED plan for its current state.

        Unchanged inputs re-use the locked plan (the same facts give the same
        plan). Changed inputs build version n+1, confirm and lock it, and mark
        version n SUPERSEDED - its content is never touched.
        """
        current = latest_locked(case)
        proposals = self.proposals(case)
        draft = self.build(case, version=(current.version + 1) if current else 1,
                           trust=trust, proposals=proposals)
        if current is not None and current.inputs_digest == draft.inputs_digest:
            case.audit.append({"event": "claim_plan_reused",
                               "claim_plan_id": current.claim_plan_id,
                               "version": current.version})
            return current
        draft.confirm().lock()
        if current is not None:
            current.supersede(draft)
        case.claim_plans.append(draft)
        case.audit.append({"event": "claim_plan_locked", "claim_plan_id": draft.claim_plan_id,
                           "version": draft.version, "approved": draft.supported_ids,
                           "supersedes": current.claim_plan_id if current else None,
                           "trace": draft.trace(),
                           "changes": diff(current, draft) if current else None})
        return draft

    # ------------------------------------------------------------ details
    @staticmethod
    def _support(module, cand, facts: dict) -> list[dict]:
        """The verified facts / evidence that make the gate hold, from the
        relationship match; else the gate's own facts that are present."""
        rows = [dict(r) for r in (cand.selected_because if cand is not None else [])]
        if rows:
            return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))
        out = []
        for name in sorted(referenced_facts(module.use_when)):
            v = facts.get(name)
            if v not in (None, "", [], False):
                out.append({"condition": f"{name}={json.dumps(_plain(v), default=str)}",
                            "fact": name, "value": _plain(v)})
        return out

    def _evidence(self, module, uploaded: dict) -> list[dict]:
        kinds = set(module.evidence_helpful or [])
        for b in module.building_blocks or []:
            blk = self.kg.blocks.get(b)
            if blk is not None and blk.status == "ACTIVE":
                kinds |= set(blk.requires_evidence or [])
        return [{"kind": k, "evidence_id": uploaded[k], "uploaded": True}
                for k in sorted(kinds) if k in uploaded]

    def _required_evidence_missing(self, module, uploaded: dict) -> list[str]:
        """Evidence the claim cannot be argued without: every approved block of
        the module needs an enclosure (R-08), and none of them is uploaded. With
        such a block set the pack would carry no wording for the claim at all."""
        blocks = [self.kg.blocks[b] for b in module.building_blocks or []
                  if b in self.kg.blocks and self.kg.blocks[b].status == "ACTIVE"]
        if not blocks or not all(b.requires_evidence for b in blocks):
            return []
        needed = sorted({k for b in blocks for k in b.requires_evidence})
        if any(k in uploaded for k in needed):
            return []
        return needed

    def _relationships(self, mid: str, match, facts: dict) -> list[dict]:
        """The relationship edges that put this claim in or kept it out."""
        out = []
        cand = match.candidates.get(mid)
        for e in self.kg.relations.edges_of(mid):
            if e.target_id != mid:
                continue
            cond = e.metadata.get("condition")
            if e.relationship_type in ("SUPPORTS", "EVIDENCE_SUPPORTS") and cond is not None:
                try:
                    holds = bool(evaluate(cond, facts))
                except PredicateError:
                    holds = False
                if holds:
                    out.append({"edge_id": e.edge_id, "relationship": e.relationship_type,
                                "source": f"{e.source_type}:{e.source_id}"})
        for b in (cand.blocked_by if cand is not None else []):
            out.append({"edge_id": b.get("edge_id"), "relationship": "BLOCKS",
                        "source": b.get("signal") or "do_not_use_when",
                        "reason": b.get("reason")})
        return sorted(out, key=lambda r: (r["relationship"], str(r.get("edge_id")), r["source"]))

    @staticmethod
    def _facts_used(case: CaseFile, names: Iterable[str]) -> dict:
        out = {}
        for name in sorted(set(names)):
            f = case.facts.get(name)
            if f is None:
                continue
            out[name] = {"value": _plain(f.value), "status": f.status.value,
                         "source_kind": f.source.kind.value, "source_ref": f.source.ref,
                         "fact_id": case.facts.node_id(name)}
        return out


__all__ = ["FinalClaimPlan", "ClaimPlanItem", "ClaimPlanBuilder", "ClaimPlanLockedError",
           "ClaimPlanIntegrityError", "diff", "latest_locked", "claim_label", "run_uuid",
           "DRAFT", "CONFIRMED", "LOCKED", "SUPERSEDED", "SUPPORTED", "REJECTED", "UNRESOLVED"]
