"""P10.6 — structured DraftPlan between LOCKED Claim Plan and LLM rendering.

Every SUPPORTED Claim Plan ground owns exactly one DraftSection (or an
explicit permitted merge). The LLM renders sections; it does not choose
grounds.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from ..module_roles import (
    EVIDENCE_REQUIREMENT, LEGAL_CONCLUSION, STRUCTURAL,
    SUBSTANTIVE_GROUND, SUPPORTING_PROPOSITION, role_of,
)
from .support_contract import DraftRequirement, SupportBundle

DRAFT_PLAN_VERSION = "p10_6_draft_plan_v1"

# Compatible grounds that may share one section (explicit merge).
PERMITTED_MERGES: tuple[frozenset[str], ...] = (
    frozenset({"PAYMENT", "KEYING"}),
)

# Gating / meta facts that must not become letter required_particulars.
NON_LETTER_PARTICULARS = frozenset({
    "jurisdiction", "relevant_land", "notice_route", "pofa_route", "pofa_finding",
    "operator_ata", "code_version", "driver_status", "delivery_date_proven",
    "hire_firm", "keeper_liability_asserted", "windshield_notice",
    # Completeness / defect flags feed the Claim Plan; the letter expresses the
    # Schedule 4 point via approved modules, not by citing these meta facts.
    "notice_sides_complete", "ntk_defect_document_confirmed",
    "ntk_defect_keeper_warning", "ntk_invites_name_driver",
    "ntk_invites_pass_to_driver", "ntd_date", "notice_received_date",
})

# Letter-facing particulars the renderer / validator may require when valued.
LETTER_PARTICULARS = frozenset({
    "payment_made", "payment_method", "payment_amount", "keying_error_type",
    "vehicle_immobilised", "breakdown_severity", "left_site", "returned_same_day",
    "multiple_visits", "loading_activity", "activity_type", "permit_held",
    "permit_type", "parking_event_date", "notice_issue_date", "ntd_date",
    "notice_received_date", "days_late", "deadline", "presumed_delivery",
    "allocated_bay", "account_contradicts_allegation", "material_account_proposition",
    "children_present", "observation_start", "observation_end",
    # Purpose of visit (shopping / drop-off / …) is a controlled ontology fact
    # and must travel through SupportBundle → DraftPlan when valued.
    "purpose_of_visit", "visited_premises",
    # Generic narrative-atom particular: professional reason for leaving.
    "departure_reason",
    # Passenger activity facts (KB-ACT-02 / ANPR sequence particulars).
    "dropoff_activity", "pickup_activity",
})

# Soft semantic cues that a ground's topic was expressed (not exact wording).
_TOPIC_CUES: dict[str, re.Pattern] = {
    "PAYMENT": re.compile(r"\b(pay|paid|payment|tariff)\b", re.I),
    "KEYING": re.compile(r"\b(key|mistyp|registr|plate|vrm|character)\b", re.I),
    "BREAKDOWN": re.compile(r"\b(break|mechanical|immobil|stall|fault|restart|failure)\b", re.I),
    "POFA": re.compile(r"\b(schedule 4|keeper liability|notice to keeper|statutory|days)\b", re.I),
    "RESIDENTIAL": re.compile(r"\b(permit|resident|lease|tenan|bay)\b", re.I),
    "ANPR": re.compile(
        r"\b(anpr|visit|entry|exit|capture|camera|left|return|depart|"
        r"drop.?off|pick.?up|separate)\b", re.I),
    "AUTHORISATION": re.compile(r"\b(authoris|permit|consent|whitelist)\b", re.I),
    "EVIDENCE": re.compile(r"\b(evidence|record|proof|contradict|receipt|validation)\b", re.I),
    "BAY": re.compile(r"\b(bay|restriction|occup)\b", re.I),
    "ACTIVITY": re.compile(
        r"\b(load|deliver|collect|unload|parcel|courier|drop.?off|pick.?up|"
        r"passenger|set down)\b", re.I),
    "GRACE": re.compile(r"\b(grace|consideration|period)\b", re.I),
    "LAND": re.compile(r"\b(landowner|authority)\b", re.I),
    "SIGN": re.compile(r"\b(sign|signage)\b", re.I),
}


@dataclass
class DraftSection:
    section_id: str
    ground_id: str                          # primary owner
    ground_ids: list[str]                   # includes merges
    purpose: str
    role: str = SUBSTANTIVE_GROUND
    supporting_fact_ids: list[str] = field(default_factory=list)
    supporting_fact_names: list[str] = field(default_factory=list)
    derived_fact_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    legal_finding_ids: list[str] = field(default_factory=list)
    required_particulars: list[str] = field(default_factory=list)
    particular_values: dict = field(default_factory=dict)
    prohibited_claims: list[str] = field(default_factory=list)
    required_outcome: str = "express_ground"
    support_module_ids: list[str] = field(default_factory=list)
    context_chunk_ids: list[str] = field(default_factory=list)
    narrative_atoms: list[dict] = field(default_factory=list)
    merged: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DraftPlan:
    case_id: str
    claim_plan_id: Optional[str]
    version: str
    sections: list[DraftSection]
    introduction_requirements: list[str]
    closing_requirements: list[str]
    leading_ground_ids: list[str]
    support_only_ids: list[str]

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "claim_plan_id": self.claim_plan_id,
            "draft_plan_version": self.version,
            "sections": [s.as_dict() for s in self.sections],
            "introduction_requirements": list(self.introduction_requirements),
            "closing_requirements": list(self.closing_requirements),
            "leading_ground_ids": list(self.leading_ground_ids),
            "support_only_ids": list(self.support_only_ids),
        }

    def owned_grounds(self) -> set[str]:
        out: set[str] = set()
        for s in self.sections:
            out.update(s.ground_ids)
        return out


def _route_family(module_id: str) -> str:
    mid = str(module_id or "")
    if mid.startswith("KB-PAY-"):
        return "PAYMENT"
    if mid.startswith("KB-KEY-"):
        return "KEYING"
    if mid.startswith("KB-BREAK-"):
        return "BREAKDOWN"
    if mid.startswith("KB-POFA-"):
        return "POFA"
    if mid.startswith("KB-RES-"):
        return "RESIDENTIAL"
    if mid.startswith("KB-ANPR-"):
        return "ANPR"
    if mid.startswith("KB-AUTH-"):
        return "AUTHORISATION"
    if mid.startswith("KB-EV-") or mid.startswith("KB-REC-") or mid.startswith("KB-TIME-"):
        return "EVIDENCE"
    if mid.startswith("KB-BAY-"):
        return "BAY"
    if mid.startswith("KB-ACT-"):
        return "ACTIVITY"
    if mid.startswith("KB-GRACE-"):
        return "GRACE"
    if mid.startswith("KB-LAND-"):
        return "LAND"
    if mid.startswith("KB-SIGN-"):
        return "SIGN"
    return "OTHER"


def _merge_group(module_ids: list[str]) -> list[list[str]]:
    """Partition leading grounds into sections, applying permitted merges."""
    remaining = list(module_ids)
    groups: list[list[str]] = []
    # PAYMENT + KEYING merge
    pay = [m for m in remaining if m.startswith("KB-PAY-")]
    key = [m for m in remaining if m.startswith("KB-KEY-")]
    if pay and key:
        group = pay + key
        groups.append(group)
        remaining = [m for m in remaining if m not in group]
    for mid in remaining:
        groups.append([mid])
    return groups


def build_draft_plan(pack, case_id: str = "") -> DraftPlan:
    """Transform LOCKED Claim Plan + SupportBundles into a DraftPlan."""
    plan = {}
    if isinstance(getattr(pack, "claim_plan", None), dict):
        plan = dict(pack.claim_plan)
    ctx_plan = (getattr(pack, "case_context", None) or {}).get("claim_plan") or {}
    if isinstance(ctx_plan, dict):
        for k, v in ctx_plan.items():
            plan.setdefault(k, v)

    approved = list(plan.get("approved") or plan.get("module_ids")
                    or getattr(pack, "module_ids", None) or [])
    bundles = dict(plan.get("support_bundles") or {})
    reqs = dict(plan.get("draft_requirements") or {})
    labels = dict(plan.get("labels") or {})

    leading, support_only = [], []
    for mid in approved:
        role = role_of(mid)
        if role in (SUPPORTING_PROPOSITION, LEGAL_CONCLUSION):
            support_only.append(mid)
        elif role == STRUCTURAL:
            continue
        else:
            # SUBSTANTIVE + EVIDENCE may own a section; evidence still secondary
            leading.append(mid)

    # Attach support-only modules to the first leading section when present
    groups = _merge_group(leading)
    sections: list[DraftSection] = []
    chunks = list(getattr(pack, "context_chunks", None) or [])
    findings = list(getattr(pack, "legal_findings", None) or [])
    prohibited = list(getattr(pack, "prohibited_claims", None) or [])

    for i, group in enumerate(groups, 1):
        primary = group[0]
        bundle = SupportBundle.from_dict(bundles.get(primary))
        # Merge bundles for merged grounds
        for mid in group[1:]:
            other = SupportBundle.from_dict(bundles.get(mid))
            bundle = SupportBundle(
                source_fact_ids=tuple(dict.fromkeys(
                    list(bundle.source_fact_ids) + list(other.source_fact_ids))),
                derived_fact_ids=tuple(dict.fromkeys(
                    list(bundle.derived_fact_ids) + list(other.derived_fact_ids))),
                evidence_ids=tuple(dict.fromkeys(
                    list(bundle.evidence_ids) + list(other.evidence_ids))),
                legal_finding_ids=tuple(dict.fromkeys(
                    list(bundle.legal_finding_ids) + list(other.legal_finding_ids))),
                relationship_ids=tuple(dict.fromkeys(
                    list(bundle.relationship_ids) + list(other.relationship_ids))),
                source_fact_names=tuple(dict.fromkeys(
                    list(bundle.source_fact_names) + list(other.source_fact_names))),
                derived_fact_names=tuple(dict.fromkeys(
                    list(bundle.derived_fact_names) + list(other.derived_fact_names))),
                values={**bundle.values, **other.values},
            )
        particulars: list[str] = []
        values: dict = dict(bundle.values or {})
        for mid in group:
            req = DraftRequirement.from_dict(reqs.get(mid))
            for name in req.required_particulars:
                if name and name not in NON_LETTER_PARTICULARS and name not in particulars:
                    particulars.append(name)
        for name in bundle.source_fact_names:
            if (name and name not in NON_LETTER_PARTICULARS
                    and name in LETTER_PARTICULARS and name not in particulars):
                particulars.append(name)
        # Drop particulars with no value in pack facts / bundle
        facts = getattr(pack, "verified_facts", None) or {}
        kept = []
        for name in particulars:
            if name in NON_LETTER_PARTICULARS:
                continue
            val = values.get(name, facts.get(name))
            if val in (None, "", [], False) and name not in (
                    "days_late", "days"):
                continue
            if name not in LETTER_PARTICULARS and name not in (
                    "days_late", "deadline", "presumed_delivery"):
                continue
            kept.append(name)
            if name not in values and val not in (None, ""):
                values[name] = val
        # Finding dates as particulars (letter must state the calculation)
        for f in findings:
            if f.get("legal_module_id") not in group:
                continue
            calc = f.get("calculation") or {}
            for key in ("parking_event_date", "notice_issue_date", "deadline",
                        "presumed_delivery", "days", "days_between", "days_late"):
                if calc.get(key) is not None:
                    pname = ("days_late" if key in ("days", "days_between", "days_late")
                             else key)
                    if pname not in kept:
                        kept.append(pname)
                    values.setdefault(pname, calc.get(key))
            # Particulars helper may expose days separately from calculation blob.
            try:
                from ..legal.findings import particulars as finding_particulars
                p = finding_particulars(f)
                if p.get("days") is not None:
                    if "days_late" not in kept:
                        kept.append("days_late")
                    values.setdefault("days_late", p.get("days"))
                for key, val in (p.get("dates") or {}).items():
                    if val is not None:
                        if key not in kept and key in LETTER_PARTICULARS:
                            kept.append(key)
                        values.setdefault(key, val)
            except Exception:
                pass

        chunk_ids = [
            c.get("id") for c in chunks
            if c.get("module_id") in group and c.get("id")
        ]
        topic = labels.get(primary) or primary
        purpose = (
            f"Express supported ground(s) {', '.join(group)}: {topic}. "
            f"Include required particulars; do not invent facts."
        )
        narrative_atoms: list[dict] = []
        if any(str(m).startswith("KB-ANPR") for m in group):
            dep = values.get("departure_reason")
            ctx = getattr(pack, "case_context", None) or {}
            if not dep:
                for a in (ctx.get("narrative_atoms") or []):
                    if a.get("name") == "departure_reason" and a.get("proposition"):
                        dep = a["proposition"]
                        values["departure_reason"] = dep
                        break
            if dep:
                purpose += (
                    " Explain WHY the vehicle left and returned using the "
                    f"departure_reason particular ({dep}); professionally rewrite, "
                    "do not paste customer wording, do not identify the driver."
                )
                atoms_src = list(ctx.get("narrative_atoms") or [])
                matched = [a for a in atoms_src
                           if (a.get("name") == "departure_reason"
                               or a.get("proposition") == dep)]
                narrative_atoms = matched or [{
                    "atom_id": "NA-departure_reason",
                    "name": "departure_reason",
                    "proposition": dep,
                    "attribution": "CUSTOMER",
                    "polarity": "AFFIRMED",
                }]
                if "departure_reason" not in kept:
                    kept.append("departure_reason")
            # Preserve passenger activity sequence when present (generic meaning).
            drop = values.get("dropoff_activity") or facts.get("dropoff_activity")
            pick = values.get("pickup_activity") or facts.get("pickup_activity")
            if drop or pick:
                purpose += (
                    " Express the customer activity sequence using valued "
                    "particulars (drop-off / leave / return / pick-up as present); "
                    "professionally rewrite; do not collapse into generic ANPR "
                    "timestamp wording alone; do not identify the driver."
                )
                for name, val in (("dropoff_activity", drop), ("pickup_activity", pick)):
                    if val not in (None, "", False) and name not in kept:
                        kept.append(name)
                        values.setdefault(name, val)
            # Attach material narrative atoms (including unmapped) for this ground.
            ctx_atoms = list((getattr(pack, "case_context", None) or {})
                             .get("narrative_atoms") or [])
            material_atoms = [
                a for a in ctx_atoms
                if isinstance(a, dict)
                and a.get("polarity") in (None, "AFFIRMED", "NEGATED", "UNCERTAIN")
                and (
                    a.get("mapped_to_ontology") is False
                    or a.get("category") in (
                        "departure_reason", "departure_event", "return_event",
                        "visit_activity", "multiple_attendance", "unmapped_reason",
                    )
                    or a.get("name") in (
                        "departure_reason", "unmapped_reason",
                    )
                )
            ]
            if material_atoms and not narrative_atoms:
                narrative_atoms = material_atoms[:8]
            elif material_atoms:
                seen_ids = {a.get("atom_id") for a in narrative_atoms}
                for a in material_atoms:
                    if a.get("atom_id") not in seen_ids:
                        narrative_atoms.append(a)
                narrative_atoms = narrative_atoms[:12]
        # Meta / gating facts stay in the SupportBundle for audit but must not
        # be offered to the drafter as citable fact_refs (VAL-FACT).
        letter_names = [
            n for n in bundle.source_fact_names
            if n and n not in NON_LETTER_PARTICULARS
        ]
        name_by_id = {}
        for fid, name in zip(bundle.source_fact_ids, bundle.source_fact_names):
            name_by_id[str(fid)] = name
        letter_ids = [
            fid for fid in bundle.source_fact_ids
            if name_by_id.get(str(fid), str(fid)) not in NON_LETTER_PARTICULARS
            and str(fid) not in NON_LETTER_PARTICULARS
        ]
        sections.append(DraftSection(
            section_id=f"S{i:02d}",
            ground_id=primary,
            ground_ids=list(group),
            purpose=purpose,
            role=role_of(primary),
            supporting_fact_ids=letter_ids,
            supporting_fact_names=letter_names,
            derived_fact_ids=list(bundle.derived_fact_ids),
            evidence_ids=list(bundle.evidence_ids),
            legal_finding_ids=list(bundle.legal_finding_ids),
            required_particulars=kept,
            particular_values=values,
            prohibited_claims=list(prohibited),
            required_outcome="express_ground",
            support_module_ids=list(support_only) if i == 1 else [],
            context_chunk_ids=chunk_ids,
            narrative_atoms=narrative_atoms,
            merged=len(group) > 1,
        ))

    intro = [
        "Identify as registered keeper",
        "Name PCN / VRM when present",
        "Do not argue grounds in the introduction",
    ]
    # One closing requirement — avoid repeating keeper-liability / cancel twice.
    closing = [
        "One concise cancellation request; mention Schedule 4 keeper-liability "
        "only once if pofa_findings are non-empty (do not repeat the same "
        "conclusion)",
    ]
    return DraftPlan(
        case_id=case_id or "",
        claim_plan_id=plan.get("claim_plan_id"),
        version=DRAFT_PLAN_VERSION,
        sections=sections,
        introduction_requirements=intro,
        closing_requirements=closing,
        leading_ground_ids=leading,
        support_only_ids=support_only,
    )


def section_expresses_ground(text: str, section) -> bool:
    """Semantic-equivalence check: topic cue or a required particular value."""
    if not (text or "").strip():
        return False
    ground_ids = list(getattr(section, "ground_ids", None) or [])
    values = dict(getattr(section, "particular_values", None) or {})
    required = list(getattr(section, "required_particulars", None) or [])
    for mid in ground_ids:
        fam = _route_family(mid)
        cue = _TOPIC_CUES.get(fam)
        if cue and cue.search(text):
            return True
    # Particulars (semantic aliases) — any required or valued particular counts
    names = list(dict.fromkeys(required + list(values.keys())))
    for name in names:
        value = values.get(name)
        if particular_expressed(text, name, value):
            return True
    for name, value in values.items():
        if value in (None, "", False, True):
            continue
        token = str(value).strip()
        if len(token) >= 3 and token.lower() in text.lower():
            return True
    for mid in ground_ids:
        frag = mid.split("-")[-1].lower()
        if frag and frag in text.lower():
            return True
    return False


def particular_expressed(text: str, name: str, value: Any = None) -> bool:
    """Whether a required particular appears in section text."""
    low = (text or "").lower()
    if name.replace("_", " ") in low:
        return True
    if value not in (None, "", False):
        token = str(value).strip()
        if token and token.lower() in low:
            return True
        # date-ish loose match
        digits = re.sub(r"\D", "", token)
        if len(digits) >= 6 and digits[:6] in re.sub(r"\D", "", text or ""):
            return True
    # known aliases
    aliases = {
        "payment_made": (r"\bpaid\b", r"\bpayment\b"),
        "keying_error_type": (r"\bmistyp", r"\bkey", r"\bregistr", r"\bplate\b"),
        "vehicle_immobilised": (r"\bimmobil", r"\bmechanical", r"\bbreak"),
        "left_site": (r"\bleft\b", r"\bdepart", r"\bdrove away"),
        "returned_same_day": (r"\breturn", r"\bcame back"),
        "multiple_visits": (r"\bvisit", r"\bentry", r"\bsecond"),
        "loading_activity": (r"\bload", r"\bdeliver", r"\bcollect", r"\bparcel"),
        "permit_held": (r"\bpermit\b",),
        "days_late": (r"\bday", r"\blate\b"),
        "parking_event_date": (r"\b20\d{2}\b", r"\bparking event\b"),
        "notice_issue_date": (r"\b20\d{2}\b", r"\bnotice to keeper was issued\b"),
        "deadline": (r"\bstatutory period\b", r"\bdeadline\b", r"\bended on\b"),
        "presumed_delivery": (r"\bdeemed delivered\b", r"\bpresumed delivery\b"),
        "account_contradicts_allegation": (
            r"\bcontradict", r"\binconsistent with\b", r"\binconsistency\b"),
        "material_account_proposition": (r"\bkeeper'?s account\b", r"\baccount is that\b"),
        "purpose_of_visit": (
            r"\bshop", r"\bpurchas", r"\bretail",
            r"\bdrop.?off\b", r"\bpick.?up\b", r"\bpassenger\b",
        ),
        "dropoff_activity": (
            r"\bdrop.?off\b", r"\bdropped\b", r"\bset down\b", r"\bpassenger\b",
        ),
        "pickup_activity": (
            r"\bpick.?up\b", r"\bcollect", r"\bpassenger\b",
        ),
        "departure_reason": (
            r"\bforgot", r"\bforgotten\b", r"\bretriev", r"\bcollect",
            r"\bwallet\b", r"\bpurse\b", r"\bnecessary item\b", r"\bat home\b",
            r"\bleft elsewhere\b", r"\bprompting the departure\b",
        ),
        "visited_premises": (r"\bpremis", r"\bnearby\b", r"\battended\b"),
    }
    for pat in aliases.get(name, ()):
        if re.search(pat, text or "", re.I):
            return True
    return False


def coverage_trace(plan: DraftPlan, draft, pack=None) -> list[dict]:
    """GROUND → section → rendered text → coverage verdict."""
    letter_by_module: dict[str, str] = {}
    if draft is not None:
        for s in draft.sentences():
            text = s.text or ""
            for mid in s.module_refs or []:
                letter_by_module[mid] = (letter_by_module.get(mid) or "") + " " + text
    rows = []
    for section in plan.sections:
        text = " ".join(letter_by_module.get(m, "") for m in section.ground_ids).strip()
        if not text and draft is not None:
            # Also scan full letter for semantic expression
            text = draft.plain_text() if hasattr(draft, "plain_text") else ""
        expressed = section_expresses_ground(text, section)
        missing = [
            n for n in section.required_particulars
            if not particular_expressed(text, n, section.particular_values.get(n))
        ]
        rows.append({
            "ground_ids": list(section.ground_ids),
            "section_id": section.section_id,
            "support_bundle_complete": bool(
                section.supporting_fact_ids or section.legal_finding_ids
                or section.evidence_ids),
            "draft_requirement_particulars": list(section.required_particulars),
            "rendered_text_present": bool(text.strip()),
            "semantic_expression": expressed,
            "missing_particulars": missing,
            "verdict": (
                "OK" if expressed and not missing else
                "DRAFT_RENDERING_ERROR"
            ),
        })
    return rows
