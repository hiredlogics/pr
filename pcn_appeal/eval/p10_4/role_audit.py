"""Module-role governance audit (report-only; does not change KB wording)."""
from __future__ import annotations

import json
from pathlib import Path

from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.module_roles import (
    CLAIM_GROUND_ROLES, EVIDENCE_REQUIREMENT, LEGAL_CONCLUSION,
    ROLE_BY_MODULE, STRUCTURAL, SUBSTANTIVE_GROUND, SUPPORTING_PROPOSITION,
    can_be_claim_ground, can_lead_letter, role_of,
)

# P10.4 policy: EVIDENCE_REQUIREMENT must not imply can_lead unless explicit.
# Frozen runtime currently maps EVIDENCE_REQUIREMENT → can_lead via CLAIM_GROUND_ROLES.
EXPLICIT_CAN_LEAD: dict[str, bool] = {
    # No EVIDENCE_REQUIREMENT module has explicit can_lead_letter=true metadata yet.
}


def audit_rows() -> list[dict]:
    kg = KnowledgeGraph()
    rows = []
    for mid in sorted(kg.modules):
        mod = kg.modules[mid]
        role = role_of(mod)
        source = ("yaml_module_role" if getattr(mod, "module_role", None)
                  and mid not in ROLE_BY_MODULE else
                  "ROLE_BY_MODULE" if mid in ROLE_BY_MODULE else "default_SUBSTANTIVE")
        runtime_claim = can_be_claim_ground(mod)
        runtime_lead = can_lead_letter(mod) and mod.strength >= 50
        policy_lead = runtime_lead
        flags = []
        if role == EVIDENCE_REQUIREMENT:
            explicit = EXPLICIT_CAN_LEAD.get(mid)
            if explicit is None and runtime_lead:
                flags.append("ROLE_REVIEW_REQUIRED")
                flags.append("EVIDENCE_REQUIREMENT_INHERITS_LEAD_WITHOUT_EXPLICIT_FLAG")
                policy_lead = False  # governance view
            elif explicit is True:
                policy_lead = True
            else:
                policy_lead = False
        if role == LEGAL_CONCLUSION and runtime_claim:
            flags.append("ROLE_REVIEW_REQUIRED")
        if role == SUPPORTING_PROPOSITION and runtime_claim:
            flags.append("ROLE_REVIEW_REQUIRED")
        if role == STRUCTURAL and (runtime_claim or runtime_lead):
            flags.append("ROLE_REVIEW_REQUIRED")
        if mid.startswith("KB-POFA-0") and role == SUBSTANTIVE_GROUND and mid in (
                "KB-POFA-06",):
            if getattr(mod, "status", "") == "REVIEW":
                flags.append("ROLE_REVIEW_REQUIRED")
        reason = {
            SUBSTANTIVE_GROUND: "fact-specific / dispositive ground",
            SUPPORTING_PROPOSITION: "support / signage / landowner framing; strength or role bars lead",
            LEGAL_CONCLUSION: "companion conclusion after a verified finding; not independent",
            EVIDENCE_REQUIREMENT: "puts operator to proof / evidence challenge",
            STRUCTURAL: "letter structure only",
        }.get(role, "unclassified")
        rows.append({
            "module_id": mid,
            "module_role": role,
            "status": getattr(mod, "status", None),
            "strength": mod.strength,
            "route": mod.route,
            "can_enter_claim_plan": runtime_claim,
            "can_lead_letter_runtime": runtime_lead,
            "can_lead_letter_policy": policy_lead,
            "explicit_can_lead_letter": EXPLICIT_CAN_LEAD.get(mid),
            "classification_source": source,
            "reason": reason,
            "flags": flags,
        })
    return rows


def write_audit(dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    rows = audit_rows()
    flagged = [r for r in rows if r["flags"]]
    by_role = {}
    for r in rows:
        by_role.setdefault(r["module_role"], 0)
        by_role[r["module_role"]] += 1
    summary = {
        "module_count": len(rows),
        "by_role": by_role,
        "role_review_required_count": len(flagged),
        "role_review_required": [r["module_id"] for r in flagged],
        "policy_note": (
            "P10.4 governance: EVIDENCE_REQUIREMENT must not imply can_lead_letter "
            "without explicit metadata. Frozen runtime still allows lead via "
            "CLAIM_GROUND_ROLES; flagged modules require ROLE_REVIEW_REQUIRED."
        ),
        "rows": rows,
    }
    (dest / "ROLE_GOVERNANCE_AUDIT.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    md = [
        "# P10.4 Role governance audit",
        "",
        summary["policy_note"],
        "",
        f"- Modules: {summary['module_count']}",
        f"- By role: `{summary['by_role']}`",
        f"- ROLE_REVIEW_REQUIRED: {summary['role_review_required_count']}",
        "",
        "| module_id | role | claim? | lead runtime? | lead policy? | flags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        md.append(
            f"| {r['module_id']} | {r['module_role']} | {r['can_enter_claim_plan']} | "
            f"{r['can_lead_letter_runtime']} | {r['can_lead_letter_policy']} | "
            f"{','.join(r['flags']) or '—'} |"
        )
    (dest / "ROLE_GOVERNANCE_AUDIT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return summary
