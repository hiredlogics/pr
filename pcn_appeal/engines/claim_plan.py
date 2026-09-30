"""Finalize one claim plan from Case Intelligence proposals + material facts.

Retrieval offers candidates. The model proposes. Deterministic veto still applies.
This module does not invent grounds by strength threshold and does not delete
LANDOWNER merely because another ground exists.

It does:
  * account for every material customer fact (used / irrelevant / unresolved / excluded);
  * record how each fact relates to each proposed claim;
  * list gate-satisfied candidates the model omitted (for one bounded reassessment);
  * mark a landowner-authority request as proportionate only when Case
    Intelligence actually selected it (never as always-on filler).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..kg.graph import KnowledgeGraph
from ..models import CaseFile, Fact, FactSource, FactStatus, KBModule, SourceKind
from ..rules.dsl import evaluate


@dataclass
class ClaimPlan:
    """Internal plan the pack and drafter consume."""
    module_ids: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    material_fact_accounting: list[dict] = field(default_factory=list)
    omitted_gate_satisfied: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "module_ids": list(self.module_ids),
            "claims": list(self.claims),
            "material_fact_accounting": list(self.material_fact_accounting),
            "omitted_gate_satisfied": list(self.omitted_gate_satisfied),
            "trace": list(self.trace),
        }


def _is_always_on(module: KBModule) -> bool:
    uw = module.use_when
    return isinstance(uw, dict) and uw.get("always") is True


def _material_fact_names(case: CaseFile) -> list[str]:
    """Structured facts produced from free text / material account assessment."""
    names: list[str] = []
    for row in (getattr(case, "free_text_provenance", None) or []):
        name = row.get("fact_name")
        if name and name not in names:
            names.append(name)
    for name in (
        "account_contradicts_allegation",
        "material_account_proposition",
        "material_account_propositions",
        "bay_child_occupant_accounted",
    ):
        if case.has(name) and name not in names:
            names.append(name)
    return names


def _fact_supports_module(fact_name: str, module: KBModule, facts: dict[str, Any]) -> str:
    """How one material fact relates to one module: supports / contradicts / neutral."""
    required = set(module.required_facts or [])
    if fact_name in required:
        return "supports"
    if fact_name in (
        "account_contradicts_allegation", "material_account_proposition",
        "material_account_propositions", "child_occupant_present",
        "bay_child_occupant_accounted",
    ):
        if module.module_id.startswith("KB-BAY") or module.route == "BAY":
            if facts.get("account_contradicts_allegation"):
                return "supports"
            return "neutral"
    return "neutral"


def _module_gate_satisfied(module: KBModule, facts: dict[str, Any]) -> bool:
    if _is_always_on(module):
        return False
    if module.route == "LANDOWNER":
        return False
    if evaluate(module.do_not_use_when, facts):
        return False
    return bool(evaluate(module.use_when, facts))


def build_claim_plan(
        case: CaseFile,
        kg: KnowledgeGraph,
        proposed_ids: list[str],
        candidate_ids: list[str],
        facts: dict[str, Any],
        *,
        findings: Optional[list] = None,
        code_version: Optional[str] = None,
        needs_pofa_finding=None,
        needs_code_version=None,
) -> ClaimPlan:
    """Build the finalized claim plan and material-fact accounting.

    Does NOT auto-add strength≥50 modules. Gate-satisfied omissions are listed
    for a bounded Case Intelligence reassessment, not silently inserted.
    """
    plan = ClaimPlan()
    findings = list(findings or [])
    proposed_set = [mid for mid in (proposed_ids or []) if mid]

    # Landowner authority is never filler: only when CI selected it.
    if "KB-LAND-01" in proposed_set:
        case.put(Fact(
            "F-authority_challenge_proportionate",
            "authority_challenge_proportionate",
            True,
            FactStatus.DERIVED,
            FactSource(SourceKind.CALCULATION, "claim_plan"),
        ))
        facts = {**facts, "authority_challenge_proportionate": True}
        plan.trace.append(
            "authority_challenge_proportionate: Case Intelligence selected KB-LAND-01")

    kept: list[str] = []
    for mid in proposed_set:
        module = kg.modules.get(mid)
        if module is None or module.status != "ACTIVE":
            plan.claims.append({
                "module_id": mid, "status": "excluded",
                "reason": "not an active ground",
            })
            continue
        if evaluate(module.do_not_use_when, facts):
            plan.claims.append({
                "module_id": mid, "status": "excluded",
                "reason": "do_not_use_when",
            })
            continue
        if not evaluate(module.use_when, facts):
            plan.claims.append({
                "module_id": mid, "status": "excluded",
                "reason": "use_when not satisfied",
            })
            continue
        if needs_pofa_finding and needs_pofa_finding(module) and not findings:
            plan.claims.append({
                "module_id": mid, "status": "excluded",
                "reason": "PoFA finding required",
            })
            continue
        if mid == "KB-POFA-04" and facts.get("notice_sides_complete") is False:
            plan.claims.append({
                "module_id": mid, "status": "excluded",
                "reason": "notice sides incomplete",
            })
            continue
        if needs_code_version and needs_code_version(module) and not code_version:
            plan.claims.append({
                "module_id": mid, "status": "excluded",
                "reason": "Code version unresolved",
            })
            continue
        kept.append(mid)
        plan.claims.append({
            "module_id": mid, "status": "selected",
            "reason": "proposed by Case Intelligence and gates passed",
            "route": module.route, "topic": module.topic,
        })

    # List — do not auto-insert — gate-satisfied candidates the model omitted.
    have = set(kept)
    for mid in candidate_ids or []:
        if mid in have:
            continue
        module = kg.modules.get(mid)
        if module is None or module.status != "ACTIVE":
            continue
        if not _module_gate_satisfied(module, facts):
            continue
        if needs_pofa_finding and needs_pofa_finding(module) and not findings:
            continue
        if mid == "KB-POFA-04" and facts.get("notice_sides_complete") is False:
            continue
        if needs_code_version and needs_code_version(module) and not code_version:
            continue
        plan.omitted_gate_satisfied.append(mid)
        plan.claims.append({
            "module_id": mid, "status": "omitted_gate_satisfied",
            "reason": "use_when satisfied on facts but not proposed; awaiting reassessment",
            "route": module.route, "topic": module.topic,
        })
        plan.trace.append(f"omitted_gate_satisfied {mid}")

    selected_modules = [kg.modules[mid] for mid in kept if mid in kg.modules]
    for fact_name in _material_fact_names(case):
        relations = []
        disposition = "irrelevant"
        reason = "no selected claim engages this fact"
        for module in selected_modules:
            rel = _fact_supports_module(fact_name, module, facts)
            relations.append({"module_id": module.module_id, "relation": rel})
            if rel == "supports":
                disposition = "used"
                reason = f"supports {module.module_id}"
            elif rel == "contradicts" and disposition != "used":
                disposition = "excluded"
                reason = f"conflicts with {module.module_id}"
        if disposition == "irrelevant" and fact_name == "child_occupant_present":
            if not facts.get("restricted_bay_alleged"):
                reason = "child occupancy noted; allegation is not a restricted-bay claim"
            elif not any(m.module_id.startswith("KB-BAY") for m in selected_modules):
                disposition = "unresolved"
                reason = "child occupancy material but no bay claim finalized"
        plan.material_fact_accounting.append({
            "fact_name": fact_name,
            "disposition": disposition,
            "reason": reason,
            "relations": relations,
            "value_present": case.get(fact_name) not in (None, "", []),
        })

    plan.module_ids = kept
    plan.trace.append(f"finalized claims: {kept}")
    case.audit.append({"event": "claim_plan", **plan.as_dict()})
    return plan
