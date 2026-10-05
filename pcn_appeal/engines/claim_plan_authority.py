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

A claim is SUPPORTED when a higher-priority source licenses it and no
GroundInvalidation removes it. Case Intelligence is a proposer only: it may
add candidates, support an existing ground, or propose an invalidation. It
cannot emit a final ground list, and a weaker selection cannot subtract a
verified finding or a still-valid carried ground.

Authority order (a lower source cannot remove a higher one):
  1. VERIFIED_FINDING   calculated statutory / document defect
  2. CARRIED_FORWARD    supported in the previous locked plan, still valid
  3. SELECTED           Case Intelligence proposal that passes the gate
  4. CANDIDATE          knowledge-match offer, not argued

A ground leaves the supported set only with a GroundInvalidation (fact,
finding, reason, plan versions). Omission, ranking, narrative, and module
order are not invalidations.

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

BUILDER_VERSION = "3"  # P8.2: union of VF∪CF∪SELECTED∪CANDIDATE; drop only via invalidation

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
# P10.3: module role is SUPPORTING_PROPOSITION / LEGAL_CONCLUSION / STRUCTURAL —
# retrieval or use_when=true is not enough to create a Claim Plan ground.
ROLE_INELIGIBLE = "ROLE_INELIGIBLE"
# P6.2: supported grounds are cumulative.
VERIFIED_FINDING = "VERIFIED_FINDING"       # a calculated statutory defect: argued on the
                                            # calculation, independently of model selection
CARRIED_FORWARD = "CARRIED_FORWARD"         # supported in the previous locked plan and the
                                            # gate + supporting facts still hold
CANDIDATE = "CANDIDATE"                     # knowledge-match offer; not a supported ground

# Lower index wins. A later source may add; it may not overwrite a better origin.
ORIGIN_PRIORITY = (VERIFIED_FINDING, CARRIED_FORWARD, SELECTED, CANDIDATE)

_NS = uuid.UUID("6b1f4c1e-5f0a-4d8e-9c55-0d7a5c1a9e05")
_MUTABLE_WHEN_LOCKED = frozenset({"status", "superseded_at", "superseded_by"})


class ClaimPlanLockedError(RuntimeError):
    """A LOCKED (or SUPERSEDED) plan was asked to change."""


class ClaimPlanIntegrityError(RuntimeError):
    """A stored plan's content no longer matches the digest it was locked with."""


class SilentGroundDropError(RuntimeError):
    """A supported or verified-licensed ground left the plan with no invalidation."""


@dataclass(frozen=True)
class GroundInvalidation:
    """The only record that may remove a ground from the supported set."""
    ground_id: str
    reason: str
    invalidated_by_fact_id: Optional[str] = None
    invalidated_by_finding_id: Optional[str] = None
    timestamp: str = ""
    previous_plan_version: Optional[int] = None
    new_plan_version: Optional[int] = None
    decision: str = GATE

    def as_dict(self) -> dict:
        return {"ground_id": self.ground_id, "reason": self.reason,
                "invalidated_by_fact_id": self.invalidated_by_fact_id,
                "invalidated_by_finding_id": self.invalidated_by_finding_id,
                "timestamp": self.timestamp, "previous_plan_version": self.previous_plan_version,
                "new_plan_version": self.new_plan_version, "decision": self.decision}

    @classmethod
    def from_dict(cls, d: dict) -> "GroundInvalidation":
        return cls(d["ground_id"], d.get("reason") or "",
                   d.get("invalidated_by_fact_id"), d.get("invalidated_by_finding_id"),
                   d.get("timestamp") or "", d.get("previous_plan_version"),
                   d.get("new_plan_version"), d.get("decision") or GATE)


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


def _jsonable(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


_CONTENT_DROP = frozenset({"fact_id", "source", "finding_id"})


def _content_facts(rows) -> list:
    """Identity fields (including nested because_of fact_ids) must not enter
    the plan digest: two cases on the same facts would otherwise differ."""
    def strip(v):
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if k not in _CONTENT_DROP}
        if isinstance(v, (list, tuple)):
            return [strip(x) for x in v]
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v
    return [strip(_thaw(f)) for f in rows]


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
    support_bundle: Any = None
    draft_requirement: Any = None

    def content(self) -> dict:
        """What the plan decided about this claim - no ids, no timestamps -
        so two cases on the same facts compare equal."""
        return {"module_id": self.module_id, "claim_type": self.claim_type,
                "status": self.status, "decision": self.decision, "reason": self.reason,
                "supporting_facts": _content_facts(self.supporting_facts),
                "evidence_refs": sorted({_thaw(e).get("kind") for e in self.evidence_refs}),
                "priority": self.priority}

    def as_dict(self) -> dict:
        from ..drafting.support_contract import build_bundle, build_requirement
        # Prefer the locked SupportBundle / DraftRequirement stamped at plan
        # time so material source particulars survive reload without a live case.
        if self.support_bundle is not None:
            bundle_dict = _thaw(self.support_bundle)
        else:
            bundle_dict = build_bundle(
                self.supporting_facts, self.evidence_refs, self.relationships,
            ).as_dict()
        if self.draft_requirement is not None:
            req_dict = _thaw(self.draft_requirement)
        else:
            req_dict = build_requirement(
                build_bundle(self.supporting_facts, self.evidence_refs,
                             self.relationships),
            ).as_dict()
        return {"item_id": self.item_id, "knowledge_id": self.knowledge_id,
                "module_id": self.module_id, "claim_type": self.claim_type,
                "status": self.status, "decision": self.decision, "reason": self.reason,
                "supporting_facts": _jsonable(_thaw(self.supporting_facts)),
                "evidence_refs": _thaw(self.evidence_refs),
                "relationships": _thaw(self.relationships),
                "priority": self.priority, "topic": self.topic,
                "support_bundle": bundle_dict, "draft_requirement": req_dict}

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimPlanItem":
        return cls(d["item_id"], d["knowledge_id"], d["module_id"], d.get("claim_type") or "",
                   d["status"], d.get("decision") or "", d.get("reason") or "",
                   _freeze(d.get("supporting_facts") or []), _freeze(d.get("evidence_refs") or []),
                   _freeze(d.get("relationships") or []), d.get("priority"),
                   d.get("topic") or "",
                   _freeze(d.get("support_bundle")) if d.get("support_bundle") else None,
                   _freeze(d.get("draft_requirement")) if d.get("draft_requirement") else None)


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
        from ..drafting.support_contract import contract_views
        bundles, reqs = contract_views(self)
        return {
            "claim_plan_id": self.claim_plan_id, "version": self.version,
            "status": self.status,
            "module_ids": self.supported_ids,
            "claims": [{"module_id": i.module_id, "claim_type": i.claim_type,
                        "priority": i.priority,
                        "supported_by": [_thaw(f).get("condition") for f in i.supporting_facts]}
                       for i in self.supported],
            "support_bundles": bundles,
            "draft_requirements": reqs,
            "material_fact_accounting": accounting,
        }

    def for_validation(self) -> dict:
        def _fact_names(item):
            names = []
            for row in item.supporting_facts or ():
                r = dict(row) if not isinstance(row, dict) else dict(row)
                n = r.get("fact") or r.get("condition")
                if isinstance(n, dict):
                    n = n.get("fact") or n.get("name")
                if n:
                    names.append(str(n))
                for dep in r.get("because_of") or ():
                    d = dep.get("fact") if isinstance(dep, dict) else dep
                    if isinstance(d, dict):
                        d = d.get("fact") or d.get("name")
                    if d:
                        names.append(str(d))
            return names

        from ..drafting.support_contract import contract_views
        bundles, reqs = contract_views(self)
        return {"claim_plan_id": self.claim_plan_id, "version": self.version,
                "status": self.status, "plan_digest": self.plan_digest,
                "approved": self.supported_ids,
                "labels": {i.module_id: claim_label(i.module_id) for i in self.items},
                # P8.5 / P8.6: lineage + join diagnostics
                "decisions": {i.module_id: i.decision for i in self.supported},
                "support_facts": {i.module_id: _fact_names(i) for i in self.supported},
                "support_bundles": bundles,
                "draft_requirements": reqs,
                "rejected": [{"module_id": i.module_id, "decision": i.decision,
                              "reason": i.reason}
                             for i in self.items if i.status == REJECTED]}

    def source_trace(self) -> list[str]:
        """P8.2: why each supported ground exists, against CI omission."""
        selected = set((_thaw(self.trust).get("proposals") or {}).get("selected") or [])
        vf = [i for i in self.supported if i.decision == VERIFIED_FINDING]
        out = ["GROUND SOURCES", "Verified findings:"]
        if vf:
            out.extend(f"  ✓ {i.module_id}" for i in vf)
        else:
            out.append("  (none)")
        out.append("Case Intelligence:")
        omitted = [i.module_id for i in vf if i.module_id not in selected]
        if omitted:
            out.extend(f"  not selected {mid}" for mid in omitted)
        elif selected:
            out.append("  selected " + ", ".join(selected))
        else:
            out.append("  none")
        out.append("Final Claim Plan:")
        if self.supported_ids:
            out.extend(f"  ✓ {mid}" for mid in self.supported_ids)
        else:
            out.append("  (none)")
        for mid in omitted:
            out.append(f"Reason: Verified finding authority overrides omission ({mid}).")
        return out

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
        reloaded case decides from what was recorded, not from memory.

        The selection is read from the case and only from the case: an empty
        selection means nothing was selected. Rebuilding it from the audit here
        would be a second authority over the same decision - the store does that
        once, on load (store/cases.py), where "never recorded" can still be told
        apart from "selected nothing".
        """
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
        selected = [m for m in (case.analysis_module_ids or []) if m]
        previous = latest_locked(case)
        prev_ids = set(previous.supported_ids) if previous is not None else set()
        # Live selection is the proposer's current output. Audit add/support
        # lists document that call; they must not resurrect a cleared selection.
        add = [m for m in selected if m not in prev_ids]
        support_existing = [m for m in selected if m in prev_ids]
        proposed_inv = event.get("proposed_invalidations")
        if proposed_inv is None:
            proposed_inv = [{"ground_id": mid, "reason": why} for mid, why in vetoed.items()]
        return {"selected": selected,
                "proposed": [m for m in (event.get("proposed") or []) if m],
                "vetoed": dict(sorted(vetoed.items())),
                "omitted": sorted(cp.get("omitted_gate_satisfied") or []),
                "candidates": sorted(event.get("candidates") or []),
                "material_fact_accounting": list(cp.get("material_fact_accounting") or []),
                # P8.2 CI contract: proposer outputs only. There is no final_ground_list.
                "add_ground_candidates": [m for m in add if m],
                "support_existing_ground": [m for m in support_existing if m],
                "proposed_invalidations": list(proposed_inv or [])}

    def inputs_digest(self, case: CaseFile, facts: dict, proposals: dict,
                      code_version: Optional[str]) -> str:
        from ..manifest import kb_digest
        return _sha({"facts": {k: _plain(v) for k, v in sorted(facts.items())},
                     "evidence": sorted((e.kind, e.evidence_id) for e in case.evidence.values()
                                        if e.uploaded),
                     "proposals": {k: v for k, v in proposals.items()
                                   if k not in ("material_fact_accounting",
                                                "add_ground_candidates",
                                                "support_existing_ground",
                                                "proposed_invalidations")},
                     "kb": kb_digest(self.kg),
                     "relations": getattr(self.kg.relations, "version", None),
                     "practice_code_version": code_version,
                     "builder": BUILDER_VERSION})

    # -------------------------------------------------------------- build
    def _origin_rank(self, decision: str) -> int:
        if decision == VERIFIED_FINDING:
            return 0
        if decision == CARRIED_FORWARD:
            return 1
        if decision == SELECTED:
            return 2
        return 3  # CANDIDATE / NOT_SELECTED / veto / gate

    def _row(self, mid: str, status: str, decision: str, reason: str,
             support=(), evidence=()) -> dict:
        m = self.kg.modules.get(mid)
        return dict(status=status, decision=decision, reason=reason,
                    support=list(support), evidence=list(evidence),
                    claim_type=str(getattr(getattr(m, "route", None), "value",
                                           getattr(m, "route", "")) or ""),
                    topic=getattr(m, "topic", "") or "")

    def _merge(self, decided: dict, mid: str, row: dict) -> None:
        """Union with priority. A lower-origin row cannot overwrite a better one."""
        held = decided.get(mid)
        if held is None:
            decided[mid] = row
            return
        if row["status"] == SUPPORTED:
            if held["status"] != SUPPORTED:
                decided[mid] = row
                return
            if self._origin_rank(row["decision"]) < self._origin_rank(held["decision"]):
                decided[mid] = row
            return
        # Rejects / unresolved never subtract a supported higher-authority ground.

    def _invalidation(self, case: CaseFile, mid: str, *, reason: str, decision: str,
                      previous, version: int, fact: Optional[str] = None,
                      finding: Optional[str] = None) -> GroundInvalidation:
        return GroundInvalidation(
            ground_id=mid, reason=reason, decision=decision,
            invalidated_by_fact_id=case.facts.node_id(fact) if fact else None,
            invalidated_by_finding_id=finding,
            timestamp=_now(),
            previous_plan_version=getattr(previous, "version", None),
            new_plan_version=version)

    def build_verified_ground_set(self, case, facts, verified_findings, match,
                                  kept, uploaded, previous, version) -> tuple[dict, list]:
        """Highest authority: modules licensed by a VERIFIED legal finding."""
        from ..module_roles import can_be_claim_ground, role_of

        rows, invalidations = {}, []
        for m in kept:
            mid = m.module_id
            lic = legal_findings.referenced_findings(m) & verified_findings
            if not lic:
                continue
            # Verified findings license substantive / evidence-requirement grounds
            # only — not framing conclusions or support-only companions.
            if not can_be_claim_ground(m):
                reason = (f"module role {role_of(m)} cannot be a Claim Plan ground "
                          f"(licensed by { '/'.join(sorted(lic)) })")
                rows[mid] = self._row(mid, REJECTED, ROLE_INELIGIBLE, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=ROLE_INELIGIBLE,
                    previous=previous, version=version,
                    finding=next(iter(sorted(lic)), None)))
                continue
            cand = match.candidates.get(mid)
            codes = "/".join(sorted(lic))
            if cand is not None and cand.status == "BLOCKED":
                reason = cand.reason or "blocked by relationship"
                rows[mid] = self._row(mid, REJECTED, BLOCKED, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=BLOCKED, previous=previous,
                    version=version, finding=next(iter(sorted(lic)), None)))
                continue
            support = self._support(m, cand, facts, case)
            if not support:
                reason = (f"verified finding {codes} licenses this ground but no "
                          "verified fact or uploaded evidence supports it")
                rows[mid] = self._row(mid, REJECTED, NO_SUPPORTING_FACTS, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=NO_SUPPORTING_FACTS,
                    previous=previous, version=version,
                    finding=next(iter(sorted(lic)), None)))
                continue
            for code in sorted(lic):
                rec = next((r for r in (case.legal_findings or [])
                            if r.get("finding_type") == code), None)
                support.append({"condition": code,
                                "finding_id": (rec or {}).get("finding_id") or code,
                                "finding_type": code})
            missing = self._required_evidence_missing(m, uploaded)
            evidence = self._evidence(m, uploaded)
            if missing:
                reason = "evidence required before it can be argued: " + ", ".join(missing)
                rows[mid] = self._row(
                    mid, UNRESOLVED, EVIDENCE_REQUIRED, reason, support,
                    evidence + [{"kind": k, "uploaded": False} for k in missing])
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=EVIDENCE_REQUIRED,
                    previous=previous, version=version,
                    finding=next(iter(sorted(lic)), None)))
                continue
            rows[mid] = self._row(
                mid, SUPPORTED, VERIFIED_FINDING,
                f"licensed by verified legal finding {codes}; a calculated "
                "statutory defect is argued independently of selection",
                support, evidence)
        return rows, invalidations

    def build_carried_forward_ground_set(self, case, facts, verified_findings, match,
                                         kept_ids, gate_why, uploaded, previous,
                                         version) -> tuple[dict, list]:
        """Previously approved grounds that remain valid on the current facts."""
        from ..module_roles import can_be_claim_ground, role_of

        rows, invalidations = {}, []
        if previous is None:
            return rows, invalidations
        for item in previous.supported:
            mid = item.module_id
            m = self.kg.modules.get(mid)
            if m is None or m.status != "ACTIVE":
                reason = "not an in-force knowledge module"
                rows[mid] = self._row(mid, REJECTED, NOT_ACTIVE, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=NOT_ACTIVE,
                    previous=previous, version=version))
                continue
            if not can_be_claim_ground(m):
                reason = f"module role {role_of(m)} cannot remain a Claim Plan ground"
                rows[mid] = self._row(mid, REJECTED, ROLE_INELIGIBLE, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=ROLE_INELIGIBLE,
                    previous=previous, version=version))
                continue
            cand = match.candidates.get(mid)
            if cand is not None and cand.status == "BLOCKED":
                reason = cand.reason or "blocked by relationship"
                rows[mid] = self._row(mid, REJECTED, BLOCKED, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=BLOCKED,
                    previous=previous, version=version))
                continue
            if mid not in kept_ids:
                fact = next(iter(sorted(referenced_facts(m.use_when) | referenced_facts(
                    m.do_not_use_when))), None)
                reason = (f"supported in plan v{previous.version} but the gate no longer "
                          "holds on the current facts: " + gate_why.get(mid, "reasoning gate"))
                rows[mid] = self._row(mid, REJECTED, GATE, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=GATE, previous=previous,
                    version=version, fact=fact))
                continue
            refusal = legal_findings.rejection(m, facts, verified_findings)
            if refusal:
                rows[mid] = self._row(mid, REJECTED, NO_VERIFIED_FINDING, refusal)
                invalidations.append(self._invalidation(
                    case, mid, reason=refusal, decision=NO_VERIFIED_FINDING,
                    previous=previous, version=version))
                continue
            support = self._support(m, cand, facts, case)
            if not support:
                reason = (f"supported in plan v{previous.version} but no verified fact "
                          "or uploaded evidence supports it any more")
                rows[mid] = self._row(mid, REJECTED, NO_SUPPORTING_FACTS, reason)
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=NO_SUPPORTING_FACTS,
                    previous=previous, version=version))
                continue
            missing = self._required_evidence_missing(m, uploaded)
            evidence = self._evidence(m, uploaded)
            if missing:
                reason = "evidence required before it can be argued: " + ", ".join(missing)
                rows[mid] = self._row(
                    mid, UNRESOLVED, EVIDENCE_REQUIRED, reason, support,
                    evidence + [{"kind": k, "uploaded": False} for k in missing])
                invalidations.append(self._invalidation(
                    case, mid, reason=reason, decision=EVIDENCE_REQUIRED,
                    previous=previous, version=version))
                continue
            rows[mid] = self._row(
                mid, SUPPORTED, CARRIED_FORWARD,
                f"supported in plan v{previous.version}; gate and supporting facts "
                "still hold", support, evidence)
        return rows, invalidations

    def build_selected_ground_set(self, case, facts, verified_findings, match,
                                  kept_ids, gate_why, uploaded, proposals) -> dict:
        """Case Intelligence proposals. Additive only; never subtracts VF/CF."""
        from ..module_roles import can_be_claim_ground, role_of

        rows = {}
        selected = list(dict.fromkeys(
            list(proposals.get("support_existing_ground") or [])
            + list(proposals.get("add_ground_candidates") or [])
            + list(proposals.get("selected") or [])))
        for mid in selected:
            m = self.kg.modules.get(mid)
            cand = match.candidates.get(mid)
            if m is None or m.status != "ACTIVE":
                rows[mid] = self._row(mid, REJECTED, NOT_ACTIVE,
                                      "not an in-force knowledge module")
                continue
            if not can_be_claim_ground(m):
                rows[mid] = self._row(
                    mid, REJECTED, ROLE_INELIGIBLE,
                    f"module role {role_of(m)} cannot independently create a "
                    "Claim Plan ground (use_when/retrieval is not enough)")
                continue
            if cand is not None and cand.status == "BLOCKED":
                rows[mid] = self._row(mid, REJECTED, BLOCKED,
                                      cand.reason or "blocked by relationship")
                continue
            finding_refusal = legal_findings.rejection(m, facts, verified_findings)
            if finding_refusal:
                rows[mid] = self._row(mid, REJECTED, NO_VERIFIED_FINDING, finding_refusal)
                continue
            if mid not in kept_ids:
                why = gate_why.get(mid, "reasoning gate does not keep it")
                if legal_findings.referenced_findings(m) and not (
                        legal_findings.referenced_findings(m) & verified_findings):
                    why = (f"{legal_findings.REJECTION_REASON}: no verified legal finding "
                           "licenses this ground")
                rows[mid] = self._row(mid, REJECTED, GATE, why)
                continue
            support = self._support(m, cand, facts, case)
            if not support:
                rows[mid] = self._row(mid, REJECTED, NO_SUPPORTING_FACTS,
                                      "no verified fact or uploaded evidence supports it")
                continue
            evidence = self._evidence(m, uploaded)
            missing = self._required_evidence_missing(m, uploaded)
            if missing:
                rows[mid] = self._row(
                    mid, UNRESOLVED, EVIDENCE_REQUIRED,
                    "evidence required before it can be argued: " + ", ".join(missing),
                    support, evidence + [{"kind": k, "uploaded": False} for k in missing])
                continue
            rows[mid] = self._row(
                mid, SUPPORTED, SELECTED,
                "selected by Case Intelligence; gate holds, nothing blocks it, "
                "supported by verified facts", support, evidence)
        return rows

    def apply_invalidations(self, case, decided, proposals, verified_ids,
                            previous, version, pending: list) -> tuple[list, list]:
        """Apply only explicit invalidations. CI proposals against VF/CF are refused.

        Returns (accepted, refused). A previous supported or verified-licensed
        ground that is not SUPPORTED must already have an accepted record;
        otherwise the drop is a builder error, not an omission.
        """
        accepted = list(pending)
        refused = []
        protected = {
            mid for mid, row in decided.items()
            if row["status"] == SUPPORTED and row["decision"] in (VERIFIED_FINDING, CARRIED_FORWARD)
        }
        for raw in proposals.get("proposed_invalidations") or []:
            mid = (raw or {}).get("ground_id") or (raw or {}).get("module_id")
            if not mid:
                continue
            if mid in protected:
                refused.append({
                    "ground_id": mid,
                    "reason": (raw.get("reason") or "proposed by Case Intelligence"),
                    "refused": "verified or carried ground cannot be invalidated by selection",
                })
                continue
            if mid in decided and decided[mid]["status"] == SUPPORTED:
                # CI may propose dropping its own SELECTED ground only with a record.
                reason = raw.get("reason") or "proposed by Case Intelligence"
                decided[mid] = self._row(mid, REJECTED, VETOED, reason)
                accepted.append(self._invalidation(
                    case, mid, reason=reason, decision=VETOED,
                    previous=previous, version=version))
        must_explain = set(verified_ids)
        if previous is not None:
            must_explain |= set(previous.supported_ids)
        explained = {inv.ground_id for inv in accepted}
        for mid in sorted(must_explain):
            row = decided.get(mid)
            if row is not None and row["status"] == SUPPORTED:
                continue
            if mid in explained:
                continue
            raise SilentGroundDropError(
                f"{mid} left the supported set with no GroundInvalidation "
                f"(previous=v{getattr(previous, 'version', None)} new=v{version})")
        return accepted, refused

    def build(self, case: CaseFile, *, version: int = 1, trust: Optional[dict] = None,
              proposals: Optional[dict] = None) -> FinalClaimPlan:
        """Union VF ∪ CF ∪ SELECTED ∪ CANDIDATE, then drop only via invalidation."""
        from ..knowledge_ingestion.graph import knowledge_id
        from ..manifest import kb_digest
        from .module_resolver import KnowledgeModuleResolver

        code, _pofa = self.reasoning.applicability(case)
        code_version = getattr(code, "version_id", None)
        calc_codes = list(getattr(_pofa, "findings", []) or [])
        facts = legal_findings.gate_facts(case.fact_view(), calc_codes)
        verified_findings = legal_findings.verified_types(
            case.legal_findings, calc_codes)
        proposals = proposals if proposals is not None else self.proposals(case)
        # P17.9: one KnowledgeModuleResolver pass (candidate ≠ eligibility).
        resolved = KnowledgeModuleResolver(
            self.kg, reasoning=self.reasoning,
        ).resolve(case, fact_view=facts, code_version=code)
        match = resolved.match
        kept = [self.kg.modules[mid] for mid in resolved.eligible_ids
                if mid in self.kg.modules]
        gate_why = dict(resolved.gate_why or {})
        kept_ids = {m.module_id for m in kept}
        uploaded = {e.kind: e.evidence_id for e in sorted(case.evidence.values(),
                                                           key=lambda e: e.evidence_id)
                    if e.uploaded}
        digest = self.inputs_digest(case, facts, proposals, code_version)
        plan_id = str(uuid.uuid5(_NS, f"{case.case_id}:claim-plan:v{version}:{digest}"))
        previous = latest_locked(case)

        decided: dict[str, dict] = {}
        vf, inv_vf = self.build_verified_ground_set(
            case, facts, verified_findings, match, kept, uploaded, previous, version)
        cf, inv_cf = self.build_carried_forward_ground_set(
            case, facts, verified_findings, match, kept_ids, gate_why, uploaded,
            previous, version)
        selected = self.build_selected_ground_set(
            case, facts, verified_findings, match, kept_ids, gate_why, uploaded, proposals)
        for mid, row in vf.items():
            self._merge(decided, mid, row)
        for mid, row in cf.items():
            self._merge(decided, mid, row)
        for mid, row in selected.items():
            self._merge(decided, mid, row)

        verified_ids = set(vf)
        invalidations, refused = self.apply_invalidations(
            case, decided, proposals, verified_ids, previous, version, inv_vf + inv_cf)

        # Veto / blocked / offered candidates fill the trace; they cannot subtract SUPPORTED.
        for mid, why in proposals["vetoed"].items():
            if mid in decided and decided[mid].get("status") == SUPPORTED:
                continue
            cand = match.candidates.get(mid)
            if cand is not None and cand.status == "BLOCKED":
                self._merge(decided, mid, self._row(mid, REJECTED, BLOCKED,
                                                    cand.reason or why))
            else:
                self._merge(decided, mid, self._row(mid, REJECTED, VETOED, why))
        for cand in match.blocked:
            self._merge(decided, cand.module_id, self._row(
                cand.module_id, REJECTED, BLOCKED, cand.reason or "blocked by relationship"))
        offered = sorted(set(proposals["candidates"]) | set(proposals["omitted"])
                         | {c.module_id for c in match.supported})
        for mid in offered:
            cand = match.candidates.get(mid)
            if cand is None:
                continue
            if cand.status == "SUPPORTED":
                self._merge(decided, mid, self._row(
                    mid, REJECTED, NOT_SELECTED,
                    "gate holds on the facts but Case Intelligence did not select it",
                    self._support(self.kg.modules[mid], cand, facts, case)))
            elif cand.status == "RELEVANT" and cand.missing:
                self._merge(decided, mid, self._row(
                    mid, UNRESOLVED, MISSING_FACTS,
                    "could apply; not established: " + ", ".join(cand.missing)))
            else:
                self._merge(decided, mid, self._row(
                    mid, REJECTED, GATE,
                    "offered, not argued: " + (cand.reason or "gate does not hold")))

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
            "invalidations": [i.as_dict() for i in invalidations],
            "refused_invalidations": refused,
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
                    dname = dep.get("fact") if isinstance(dep, dict) else None
                    if dname:
                        used_facts.add(dname)
            from ..drafting.support_contract import build_bundle, build_requirement
            findings = [r for r in d["support"] if r.get("finding_id") or r.get("finding_type")]
            # P17.9: attach material semantic events/atoms into SupportBundle.
            row = resolved.rows.get(mid)
            atoms = list(row.narrative_atoms)[:8] if row is not None else ()
            events = list(row.supporting_events)[:8] if row is not None else ()
            parts = list(row.required_particulars) if row is not None else ()
            bundle = build_bundle(
                d["support"], d["evidence"], rels,
                finding_rows=findings, case=case,
                material_narrative_atoms=atoms,
                supporting_events=events,
                required_particulars=parts,
            )
            req = build_requirement(
                bundle,
                prohibited=list(getattr(self.kg.modules.get(mid), "prohibited_claims", None) or []),
                findings=[r for r in (case.legal_findings or [])
                          if r.get("legal_module_id") == mid
                          or r.get("finding_type") in {f.get("finding_type") for f in findings}],
                claim_type=d["claim_type"],
            )
            plan.add_item(ClaimPlanItem(
                item_id=str(uuid.uuid5(uuid.UUID(plan_id), mid)), knowledge_id=knowledge_id(mid),
                module_id=mid, claim_type=d["claim_type"], status=d["status"],
                decision=d["decision"], reason=d["reason"],
                supporting_facts=_freeze(d["support"]), evidence_refs=_freeze(d["evidence"]),
                relationships=_freeze(rels), priority=rank.get(mid), topic=d["topic"],
                support_bundle=_freeze(bundle_dict),
                draft_requirement=_freeze(req.as_dict())))
        trust["facts_used"] = self._facts_used(case, used_facts)
        trust["relationships_used"] = relationships
        trust["signals"] = {
            k: v["value"] for k, v in ((match.signals if match else {}) or {}).items()
            if isinstance(v, dict) and "value" in v
        }
        trust["module_resolver"] = {
            "eligible_ids": list(resolved.eligible_ids),
            "unresolved_ids": list(resolved.unresolved_ids),
            "candidates": list(resolved.candidates),
            "invariant_candidate_ne_eligibility": True,
        }
        trust["proposals"] = {k: v for k, v in proposals.items()
                              if k != "material_fact_accounting"}
        plan.trust = _freeze(trust)
        return plan

    def record_final_claim_plan(self, case: CaseFile, draft: FinalClaimPlan,
                                current: Optional[FinalClaimPlan]) -> FinalClaimPlan:
        """Lock the decided plan and take its grounds. The only write of record."""
        if current is not None and current.inputs_digest == draft.inputs_digest:
            case.audit.append({"event": "claim_plan_reused",
                               "claim_plan_id": current.claim_plan_id,
                               "version": current.version})
            case.master.record_grounds(current)
            return current
        draft.confirm().lock()
        if current is not None:
            current.supersede(draft)
        case.claim_plans.append(draft)
        case.master.record_grounds(draft)
        case.audit.append({"event": "claim_plan_locked", "claim_plan_id": draft.claim_plan_id,
                           "version": draft.version, "approved": draft.supported_ids,
                           "supersedes": current.claim_plan_id if current else None,
                           "trace": draft.trace(),
                           "source_trace": draft.source_trace(),
                           "invalidations": _thaw(draft.trust).get("invalidations") or [],
                           "changes": diff(current, draft) if current else None})
        return draft

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
        return self.record_final_claim_plan(case, draft, current)

    # ------------------------------------------------------------ details
    @staticmethod
    def _support(module, cand, facts: dict, case=None) -> list[dict]:
        """The verified facts / evidence that make the gate hold, from the
        relationship match; else the gate's own facts that are present.
        Derived rows carry their source facts (P8.5)."""
        from ..drafting.support_contract import enrich_support_rows
        rows = [dict(r) for r in (cand.selected_because if cand is not None else [])]
        if not rows:
            for name in sorted(referenced_facts(module.use_when)):
                v = facts.get(name)
                if v not in (None, "", [], False):
                    rows.append({"condition": f"{name}={json.dumps(_plain(v), default=str)}",
                                 "fact": name, "value": _plain(v)})
        rows = enrich_support_rows(rows, case=case, module=module, facts=facts)
        return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))

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
           "ClaimPlanIntegrityError", "GroundInvalidation", "SilentGroundDropError",
           "diff", "latest_locked", "claim_label", "run_uuid",
           "DRAFT", "CONFIRMED", "LOCKED", "SUPERSEDED", "SUPPORTED", "REJECTED", "UNRESOLVED",
           "VERIFIED_FINDING", "CARRIED_FORWARD", "SELECTED", "CANDIDATE", "ORIGIN_PRIORITY"]
