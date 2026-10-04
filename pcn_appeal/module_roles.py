"""P10.5 — explicit KB module roles for Claim Plan eligibility.

Roles are metadata only: legal wording (topic / core_proposition / blocks)
is unchanged. A module being retrieved or having use_when=true is not enough
to enter the Claim Plan as a ground.

P10.5 governance:
  EVIDENCE_REQUIREMENT defaults can_lead_letter=False.
  Only explicit controlled KB metadata may set can_lead_letter=True.
"""
from __future__ import annotations

from typing import Optional

SUBSTANTIVE_GROUND = "SUBSTANTIVE_GROUND"
SUPPORTING_PROPOSITION = "SUPPORTING_PROPOSITION"
LEGAL_CONCLUSION = "LEGAL_CONCLUSION"
EVIDENCE_REQUIREMENT = "EVIDENCE_REQUIREMENT"
STRUCTURAL = "STRUCTURAL"

MODULE_ROLES = (
    SUBSTANTIVE_GROUND,
    SUPPORTING_PROPOSITION,
    LEGAL_CONCLUSION,
    EVIDENCE_REQUIREMENT,
    STRUCTURAL,
)

# May appear as an approved Claim Plan ground.
CLAIM_GROUND_ROLES = frozenset({SUBSTANTIVE_GROUND, EVIDENCE_REQUIREMENT})

# May accompany a substantive ground in drafting context, never alone.
SUPPORT_ONLY_ROLES = frozenset({SUPPORTING_PROPOSITION, LEGAL_CONCLUSION})

MODULE_ROLE_VERSION = "p10_5_roles_v1"

# Default classification for the shipped KB. YAML `module_role` overrides.
ROLE_BY_MODULE: dict[str, str] = {
    "KB-POFA-01": LEGAL_CONCLUSION,
    "KB-POFA-02": SUBSTANTIVE_GROUND,
    "KB-POFA-03": SUBSTANTIVE_GROUND,
    "KB-POFA-04": SUBSTANTIVE_GROUND,
    "KB-POFA-05": LEGAL_CONCLUSION,
    "KB-POFA-06": SUBSTANTIVE_GROUND,     # REVIEW — lead disabled via metadata
    "KB-PAY-01": SUBSTANTIVE_GROUND,
    "KB-PAY-02": SUBSTANTIVE_GROUND,
    "KB-PAY-03": SUBSTANTIVE_GROUND,
    "KB-KEY-01": SUBSTANTIVE_GROUND,
    "KB-KEY-02": SUBSTANTIVE_GROUND,
    "KB-BREAK-01": SUBSTANTIVE_GROUND,
    "KB-BREAK-02": SUBSTANTIVE_GROUND,
    "KB-BREAK-03": SUBSTANTIVE_GROUND,
    "KB-RES-01": SUBSTANTIVE_GROUND,
    "KB-RES-02": SUBSTANTIVE_GROUND,
    "KB-RES-03": SUBSTANTIVE_GROUND,
    "KB-RES-04": SUBSTANTIVE_GROUND,
    "KB-RES-05": SUPPORTING_PROPOSITION,
    "KB-RES-06": SUBSTANTIVE_GROUND,
    "KB-RES-07": SUBSTANTIVE_GROUND,
    "KB-CON-01": SUBSTANTIVE_GROUND,
    "KB-CON-02": SUBSTANTIVE_GROUND,
    "KB-GRACE-01": SUBSTANTIVE_GROUND,
    "KB-GRACE-02": SUBSTANTIVE_GROUND,
    "KB-ANPR-01": SUBSTANTIVE_GROUND,
    "KB-ANPR-02": EVIDENCE_REQUIREMENT,
    "KB-ANPR-03": EVIDENCE_REQUIREMENT,
    "KB-EV-01": EVIDENCE_REQUIREMENT,
    "KB-BAY-01": SUBSTANTIVE_GROUND,
    "KB-BAY-02": SUBSTANTIVE_GROUND,
    "KB-TIME-01": EVIDENCE_REQUIREMENT,
    "KB-AUTH-01": SUBSTANTIVE_GROUND,
    "KB-AUTH-02": SUBSTANTIVE_GROUND,
    "KB-AUTH-03": SUBSTANTIVE_GROUND,
    "KB-CUST-01": SUBSTANTIVE_GROUND,
    "KB-SIGN-01": SUPPORTING_PROPOSITION,
    "KB-SIGN-02": SUPPORTING_PROPOSITION,
    "KB-SIGN-03": SUPPORTING_PROPOSITION,
    "KB-SIGN-04": SUPPORTING_PROPOSITION,
    "KB-EQ-01": SUBSTANTIVE_GROUND,
    "KB-EQ-02": SUBSTANTIVE_GROUND,
    "KB-EQ-03": SUBSTANTIVE_GROUND,
    "KB-HOSP-01": SUBSTANTIVE_GROUND,
    "KB-HOSP-02": SUBSTANTIVE_GROUND,
    "KB-HOSP-03": SUPPORTING_PROPOSITION,
    "KB-ACT-01": SUBSTANTIVE_GROUND,
    "KB-ACT-02": SUBSTANTIVE_GROUND,
    "KB-ACT-03": SUBSTANTIVE_GROUND,
    "KB-EVCH-01": SUBSTANTIVE_GROUND,
    "KB-INFRA-01": SUBSTANTIVE_GROUND,
    "KB-LAND-01": SUPPORTING_PROPOSITION,
    "KB-LAND-02": SUPPORTING_PROPOSITION,
    "KB-LAND-03": SUPPORTING_PROPOSITION,
    "KB-REC-01": SUBSTANTIVE_GROUND,
}

# Explicit lead overrides when YAML omits can_lead_letter.
# EVIDENCE_REQUIREMENT → False unless listed True here or in YAML.
EXPLICIT_CAN_LEAD: dict[str, bool] = {
    "KB-ANPR-02": False,
    "KB-ANPR-03": False,
    "KB-EV-01": False,
    "KB-TIME-01": False,
    "KB-POFA-06": False,   # REVIEW status — must not lead until legally activated
}


def normalize_role(raw: Optional[str], *, default: str = SUBSTANTIVE_GROUND) -> str:
    role = str(raw or "").strip().upper()
    if role in MODULE_ROLES:
        return role
    return default


def role_of(module_or_id, module_role: Optional[str] = None) -> str:
    """Resolve role: explicit attribute / arg, then shipped map, else SUBSTANTIVE."""
    if module_role:
        return normalize_role(module_role)
    mid = getattr(module_or_id, "module_id", None) or str(module_or_id or "")
    attr = getattr(module_or_id, "module_role", None)
    if attr:
        return normalize_role(attr)
    if mid in ROLE_BY_MODULE:
        return ROLE_BY_MODULE[mid]
    return SUBSTANTIVE_GROUND


def can_be_claim_ground(module_or_id, module_role: Optional[str] = None) -> bool:
    return role_of(module_or_id, module_role) in CLAIM_GROUND_ROLES


def _explicit_lead(module_or_id) -> Optional[bool]:
    """YAML / object metadata first, then EXPLICIT_CAN_LEAD map."""
    flag = getattr(module_or_id, "can_lead_letter", None)
    if flag is not None:
        return bool(flag)
    mid = getattr(module_or_id, "module_id", None) or str(module_or_id or "")
    if mid in EXPLICIT_CAN_LEAD:
        return EXPLICIT_CAN_LEAD[mid]
    return None


def can_lead_letter(module_or_id, module_role: Optional[str] = None) -> bool:
    """Leading status is not inferred from role alone for EVIDENCE_REQUIREMENT.

    - SUBSTANTIVE_GROUND: lead allowed unless explicit can_lead_letter=false
    - EVIDENCE_REQUIREMENT: lead forbidden unless explicit can_lead_letter=true
    - SUPPORTING / LEGAL_CONCLUSION / STRUCTURAL: never lead
    """
    role = role_of(module_or_id, module_role)
    explicit = _explicit_lead(module_or_id)
    if role == EVIDENCE_REQUIREMENT:
        return explicit is True
    if role == SUBSTANTIVE_GROUND:
        if explicit is False:
            return False
        return True
    return False


def is_support_only(module_or_id, module_role: Optional[str] = None) -> bool:
    return role_of(module_or_id, module_role) in SUPPORT_ONLY_ROLES


def classify_pack(module_ids, kg=None) -> dict:
    """Partition module ids by role for eligibility / orphan checks."""
    substantive, support, other = [], [], []
    for mid in module_ids or []:
        role = role_of(kg.modules.get(mid) if kg and hasattr(kg, "modules") else mid)
        if role in CLAIM_GROUND_ROLES:
            substantive.append(mid)
        elif role in SUPPORT_ONLY_ROLES:
            support.append(mid)
        else:
            other.append(mid)
    return {
        "substantive": substantive,
        "support_only": support,
        "other": other,
        "orphan_support": bool(support) and not substantive,
    }


def governance_basis(module_or_id) -> dict:
    """Report-only: why a module may/may not lead."""
    mid = getattr(module_or_id, "module_id", None) or str(module_or_id or "")
    role = role_of(module_or_id)
    explicit = _explicit_lead(module_or_id)
    lead = can_lead_letter(module_or_id)
    claim = can_be_claim_ground(module_or_id)
    if role == EVIDENCE_REQUIREMENT:
        basis = (
            "EVIDENCE_REQUIREMENT defaults can_lead_letter=false; "
            + ("explicit can_lead_letter=true overrides" if explicit is True
               else "no explicit true override")
        )
    elif mid == "KB-POFA-06":
        basis = "SUBSTANTIVE but REVIEW; explicit can_lead_letter=false until activation"
    elif explicit is False:
        basis = "explicit can_lead_letter=false metadata"
    elif role == SUBSTANTIVE_GROUND:
        basis = "SUBSTANTIVE_GROUND default lead=true"
    else:
        basis = f"role {role} cannot lead"
    return {
        "module_id": mid,
        "module_role": role,
        "can_enter_claim_plan": claim,
        "can_lead_letter": lead,
        "explicit_can_lead_letter": explicit,
        "governance_basis": basis,
    }
