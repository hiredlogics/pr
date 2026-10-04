"""P10.3 ground eligibility pipeline (before Claim Plan support).

candidate module
  → role check
  → required facts
  → required semantic concepts (optional)
  → blocking facts
  → applicable allegation / legal route (existing gates)
  → support strength
  → Claim Plan

use_when=true or retrieval alone is not sufficient.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..module_roles import can_be_claim_ground, role_of
from ..rules.dsl import evaluate
from ..semantics.ontology import CONCEPT_TO_FACTS

# Optional concept requirements beyond required_facts (generic, not PCN-specific).
REQUIRED_CONCEPTS: dict[str, frozenset[str]] = {
    "KB-KEY-01": frozenset({"KEYING_ERROR", "REGISTRATION_MISMATCH"}),
    "KB-BREAK-01": frozenset({"BROKEN_DOWN", "IMMOBILISED"}),
    "KB-PAY-01": frozenset({"PAYMENT_MADE"}),
}


@dataclass
class EligibilityResult:
    module_id: str
    eligible: bool
    role: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"module_id": self.module_id, "eligible": self.eligible,
                "role": self.role, "reasons": list(self.reasons)}


def _concepts_present(case) -> set[str]:
    import json
    raw = (case.raw_answers or {}).get("_semantic_concepts")
    if not raw:
        return set()
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    return {str(r.get("concept")) for r in rows
            if str(r.get("polarity") or "").upper() == "AFFIRMED"}


def _facts_cover_concepts(facts: dict, concepts: frozenset[str]) -> bool:
    """True when FactManager already holds a promoted fact for any required concept."""
    for c in concepts:
        mapping = CONCEPT_TO_FACTS.get(c)
        if not mapping:
            continue
        name, value = mapping
        if facts.get(name) == value or (value is True and facts.get(name)):
            return True
    return False


def check_module(module, case, facts: Optional[dict] = None,
                 concepts: Optional[set[str]] = None) -> EligibilityResult:
    """Full eligibility check for one module (diagnostics + Claim Plan belt)."""
    facts = facts if facts is not None else case.fact_view()
    concepts = concepts if concepts is not None else _concepts_present(case)
    mid = module.module_id
    role = role_of(module)
    reasons: list[str] = []

    if not can_be_claim_ground(module):
        return EligibilityResult(mid, False, role,
                                 [f"role {role} cannot independently create a ground"])

    if getattr(module, "status", "ACTIVE") != "ACTIVE":
        return EligibilityResult(mid, False, role, ["module not ACTIVE"])

    try:
        if not evaluate(module.use_when, facts):
            reasons.append("use_when not satisfied")
        if module.do_not_use_when and evaluate(module.do_not_use_when, facts):
            reasons.append("do_not_use_when satisfied (blocked)")
    except Exception as exc:
        reasons.append(f"gate error: {exc}")

    missing = [f for f in (module.required_facts or []) if not facts.get(f)
               and f not in referenced_facts(module.use_when)]
    # required_facts that are also gate facts may be absent when optional — keep soft.
    soft_missing = [f for f in (module.required_facts or []) if case.get(f) in (None, "")]
    if soft_missing and mid in REQUIRED_CONCEPTS:
        # Prefer semantic evidence of the circumstance over unanswered questions.
        need = REQUIRED_CONCEPTS[mid]
        if not (need & concepts) and not _facts_cover_concepts(facts, need):
            reasons.append("required semantic concepts absent: " + ", ".join(sorted(need)))

    if reasons:
        return EligibilityResult(mid, False, role, reasons)
    return EligibilityResult(mid, True, role, ["eligible"])


def filter_claim_candidates(modules, case, facts: Optional[dict] = None) -> list:
    """Keep only modules that pass role + semantic eligibility."""
    facts = facts if facts is not None else case.fact_view()
    concepts = _concepts_present(case)
    return [m for m in modules
            if check_module(m, case, facts, concepts).eligible]
