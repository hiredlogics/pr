"""P10.6 — structured DraftPlan between LOCKED Claim Plan and LLM rendering.

Every SUPPORTED Claim Plan ground owns exactly one DraftSection (or an
explicit permitted merge). The LLM renders sections; it does not choose
grounds.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
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
    # P8: these module families had no cue, so VAL-GROUND-COVERAGE could never
    # pass for them however the letter argued the ground.
    "CONSIDERATION": re.compile(r"\b(consider\w*|accept\w*|contract|terms)\b", re.I),
    "CUSTOMER": re.compile(r"\b(customer|premises|purchase|receipt|shop\w*)\b", re.I),
    "HOSPITAL": re.compile(r"\b(hospital|medical|clinic\w*|treatment|appointment|patient)\b", re.I),
    "EQUALITY": re.compile(r"\b(disab\w*|equality|adjustment|blue badge)\b", re.I),
    "EV_CHARGING": re.compile(r"\b(charg\w*|electric)\b", re.I),
    "INFRASTRUCTURE": re.compile(r"\b(barrier|gate|access|fault\w*|malfunction\w*)\b", re.I),
    "KEEPER": re.compile(r"\b(strict proof|keeper liability|registered keeper)\b", re.I),
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
    # P17.10: the material meaning the section must express, under the names the
    # contract uses. `material_atoms` is the canonical alias of narrative_atoms;
    # `supporting_events` carries the event sequence, which the SupportBundle
    # already held but which stopped at this boundary - so a letter could keep a
    # ground's facts and lose the order they happened in.
    material_atoms: list[dict] = field(default_factory=list)
    supporting_events: list[dict] = field(default_factory=list)
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
    for prefix, family in (("KB-CON-", "CONSIDERATION"), ("KB-CUST-", "CUSTOMER"),
                           ("KB-HOSP-", "HOSPITAL"), ("KB-EQ-", "EQUALITY"),
                           ("KB-EVCH-", "EV_CHARGING"), ("KB-INFRA-", "INFRASTRUCTURE"),
                           ("KB-KEEPER-", "KEEPER")):
        if mid.startswith(prefix):
            return family
    return "OTHER"


def _same_argument(bundles: dict, a: str, b: str) -> bool:
    """Whether two grounds would restate the same argument in the letter.

    True when they are in one route family and their sections would carry the
    same particulars and the same material meaning - three breakdown grounds
    on one flat battery, for instance. Then they belong in one paragraph: a
    letter that argues the same conclusion three times is weaker than one that
    argues it once, and the renderer can only avoid repeating itself if the
    plan stops asking for it.
    """
    if _route_family(a) != _route_family(b) or _route_family(a) == "OTHER":
        return False
    one, two = SupportBundle.from_dict(bundles.get(a)), SupportBundle.from_dict(bundles.get(b))
    if set(one.required_particulars) != set(two.required_particulars):
        return False

    def meaning(bundle) -> set[str]:
        return {str(r.get("proposition") or r.get("description") or "")
                for r in _rows(bundle.material_narrative_atoms)
                + _rows(bundle.supporting_events)}
    return meaning(one) == meaning(two)


def _merge_group(module_ids: list[str],
                 bundles: Optional[dict] = None) -> list[list[str]]:
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
        prior = next((g for g in groups
                      if _same_argument(bundles or {}, g[0], mid)), None)
        if prior is not None:
            prior.append(mid)
            continue
        groups.append([mid])
    return groups


def _rows(seq: Any) -> list[dict]:
    """Semantic rows as plain dicts.

    A locked claim plan is frozen, so its bundles arrive as read-only Mappings
    rather than dicts. Every `isinstance(row, dict)` guard below then dropped
    the ground's own atoms and events on the floor, which is how a bundle
    carrying eight particulars produced a section carrying none.
    """
    out: list[dict] = []
    for row in seq or ():
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, Mapping):
            out.append(dict(row))
    return out


def _dedupe_rows(rows: Any, key: str) -> list[dict]:
    """Semantic rows in order, one per id (falling back to the text itself)."""
    seen: set[str] = set()
    out: list[dict] = []
    for row in _rows(rows):
        ident = str(row.get(key) or row.get("proposition")
                    or row.get("description") or "")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(row)
    return out


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
    groups = _merge_group(leading, bundles)
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
                # Merged grounds share one paragraph, so they share its
                # particulars: rebuilding the bundle without these dropped the
                # material meaning of every ground after the first.
                material_narrative_atoms=tuple(_dedupe_rows(
                    list(bundle.material_narrative_atoms)
                    + list(other.material_narrative_atoms), "atom_id")),
                supporting_events=tuple(_dedupe_rows(
                    list(bundle.supporting_events)
                    + list(other.supporting_events), "event_id")),
                required_particulars=tuple(dict.fromkeys(
                    list(bundle.required_particulars)
                    + list(other.required_particulars))),
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
        # P17.9: material semantic particulars for EVERY substantive ground
        # (not ANPR-only). Drafter renders; does not rediscover meaning.
        narrative_atoms: list[dict] = []
        ctx = getattr(pack, "case_context", None) or {}
        bundle_atoms = _rows(getattr(bundle, "material_narrative_atoms", None))
        if not bundle_atoms:
            bundle_atoms = _rows((bundle.as_dict() if hasattr(bundle, "as_dict") else {})
                                 .get("material_narrative_atoms"))
        # The bundle's atoms belong to THIS ground. Case-level atoms stand in
        # only when the bundle carries none and there is a single substantive
        # section, for the same reason as the events below: a section answers
        # for the meaning of its own ground, and a statutory-timing paragraph
        # is not where the keeper's errand belongs. A ground licensed by a
        # verified legal finding is a calculated defect, argued independently
        # of the account, so no case-level stand-in reaches it either.
        # The same holds for the default keeper appeal: it is written only because no
        # ground resting on the account was established, and puts the operator to
        # proof instead, so the account has no section here to be expressed in.
        calculated_ground = (any(f.get("legal_module_id") in group for f in findings)
                             or bool(facts.get("default_keeper_appeal")))
        ctx_atoms = list(bundle_atoms) + (
            _rows(ctx.get("narrative_atoms"))
            if not bundle_atoms and len(groups) == 1 and not calculated_ground
            else [])
        dep = values.get("departure_reason")
        if not dep:
            for a in ctx_atoms:
                if isinstance(a, dict) and (
                        a.get("name") == "departure_reason"
                        or a.get("category") == "departure_reason") and a.get("proposition"):
                    dep = a["proposition"]
                    values["departure_reason"] = dep
                    break
        if dep:
            purpose += (
                " Explain WHY the vehicle left and returned using the "
                f"departure_reason particular ({dep}); professionally rewrite, "
                "do not paste customer wording, do not identify the driver."
            )
            matched = [a for a in ctx_atoms if isinstance(a, dict) and (
                a.get("name") == "departure_reason"
                or a.get("category") == "departure_reason"
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
        material_atoms = [
            a for a in ctx_atoms
            if isinstance(a, dict)
            and a.get("polarity") in (None, "AFFIRMED", "NEGATED", "UNCERTAIN")
            and (
                a.get("mapped_to_ontology") is False
                or a.get("category")
                or a.get("proposition")
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
        if narrative_atoms:
            purpose += (
                " Express material narrative particulars from material_atoms "
                "(professional paraphrase; preserve negation/uncertainty; "
                "do not paste customer wording; do not identify the driver)."
            )
        # The event sequence travels with the ground. Generic: any material
        # event the bundle or the semantic state carries, in the order recorded,
        # for every substantive ground - not one route's special case.
        bundle_events = _rows(getattr(bundle, "supporting_events", None))
        if not bundle_events:
            bundle_events = _rows((bundle.as_dict() if hasattr(bundle, "as_dict") else {})
                                  .get("supporting_events"))
        # The bundle's own events belong to this ground. Case-level events are
        # attributed only when there is a single substantive section, because a
        # section is answerable for the meaning of ITS ground: making every
        # section express every event would demand the account sequence inside a
        # statutory-timing paragraph it has nothing to do with.
        ctx_events: list[dict] = []
        if not bundle_events and len(groups) == 1 and not calculated_ground:
            ctx_events = (
                _rows(ctx.get("supporting_events"))
                or _rows(ctx.get("material_events"))
                or _rows(ctx.get("customer_reported_events"))
            )
        supporting_events: list[dict] = []
        seen_events: set[str] = set()
        for ev in list(bundle_events) + list(ctx_events):
            if not isinstance(ev, dict):
                continue
            key = str(ev.get("event_id") or ev.get("description")
                      or ev.get("proposition") or "")
            if not key or key in seen_events:
                continue
            seen_events.add(key)
            supporting_events.append(ev)
        supporting_events = supporting_events[:12]
        if supporting_events:
            purpose += (
                " Express the material event sequence from supporting_events as "
                "keeper-attributed facts in the order recorded (professional "
                "paraphrase; preserve purpose, reason and temporal order; do "
                "not paste customer wording; do not identify the driver)."
            )
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
            material_atoms=list(narrative_atoms),
            supporting_events=supporting_events,
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


_PARAPHRASE_STOPWORDS = frozenset({
    "that", "this", "with", "from", "have", "been", "were", "their", "there",
    "which", "them", "they", "then", "than", "when", "into", "onto", "upon",
    "about", "would", "could", "should", "while", "where", "whose", "being",
    "having", "because",
})


def _value_meaning_expressed(text: str, value: Any) -> bool:
    """Whether a multi-word particular's MEANING survives a paraphrase.

    A proposition-shaped particular ("their purse had been forgotten, prompting
    the departure") is never reproduced verbatim in a professionally written
    letter, so a substring test can only be passed by copying - exactly what
    VAL-CUSTOMER-COPY forbids. Content-word overlap accepts the paraphrase and
    still fails on abstraction, which is what the coverage rule is for. Generic:
    it reads the value, so it needs no list of the things a customer might name.
    """
    token = str(value or "").strip()
    words = [w for w in re.findall(r"[a-z][a-z'-]{2,}", token.lower())
             if w not in _PARAPHRASE_STOPWORDS]
    if len(words) < 2:
        return False
    low = (text or "").lower()
    hits = sum(1 for w in dict.fromkeys(words) if w in low)
    return hits >= 2


def particular_expressed(text: str, name: str, value: Any = None) -> bool:
    """Whether a required particular appears in section text."""
    low = (text or "").lower()
    if name.replace("_", " ") in low:
        return True
    if value not in (None, "", False):
        token = str(value).strip()
        if token and token.lower() in low:
            return True
        if _value_meaning_expressed(text, token):
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
        # Category cues only. The thing the customer actually named is matched
        # from the particular's own value (see _value_meaning_expressed), never
        # from a list of objects - a list is a phrase rule and would silently
        # fail for the next unseen item.
        "departure_reason": (
            r"\bforgot", r"\bforgotten\b", r"\bretriev", r"\bcollect",
            r"\bleft elsewhere\b", r"\bprompting the departure\b",
            r"\breason for (?:the )?depart", r"\bwent back\b",
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
