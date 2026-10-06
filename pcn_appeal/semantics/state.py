"""Normalized SemanticCaseState between understanding and FactManager (handoff).

Document / narrative / answer → LLM (or helper) concepts → SemanticCaseState
→ FactManager (sole fact authority) → conflict check → knowledge matching.

This module does not change KB modules, Claim Plan builders, or drafting.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .ontology import CONCEPT_EXTRA_FACTS, CONCEPT_TO_FACTS

# Material consistency conflicts block Claim Plan lock (not last-write-wins).
FACT_CONFLICT = "FACT_CONFLICT"

# Facts promoted only via semantic → FactManager (not CircumstanceRule / narrative).
SEMANTIC_OWNED_FACTS = frozenset(
    name for name, _ in CONCEPT_TO_FACTS.values()
) | frozenset(
    name for extras in CONCEPT_EXTRA_FACTS.values() for name, _ in extras
)

# Visit-sequence facts used for consistency and timeline.
_VISIT_FACTS = frozenset({"left_site", "returned_same_day", "multiple_visits"})

# Allegation-family → material fact names (relevance tagging only).
_MATERIAL_BY_ALLEGATION: dict[str, frozenset[str]] = {
    "overstay": frozenset({
        "entry_time", "exit_time", "total_recorded_duration_min",
        "permitted_duration_min", "left_site", "returned_same_day",
        "multiple_visits", "payment_made", "purpose_of_visit",
        "departure_reason", "dropoff_activity", "pickup_activity",
        "anpr_sequence_incomplete", "grace_period_min",
    }),
    "payment": frozenset({
        "payment_made", "payment_attempt_failed", "keying_error_type",
        "payment_recorded_in_document", "entry_time", "exit_time",
    }),
    "bay": frozenset({
        "child_occupant_present", "permit_held", "blue_badge_displayed",
        "disability_extra_time", "loading_activity", "restricted_bay_alleged",
    }),
}


@dataclass
class SemanticCaseState:
    document_entities: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    concepts: list[dict] = field(default_factory=list)
    narrative_atoms: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    uncertainties: list[dict] = field(default_factory=list)
    missing_information: list[dict] = field(default_factory=list)
    evidence_links: list[dict] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    confidence: list[dict] = field(default_factory=list)
    material_relevance: list[dict] = field(default_factory=list)
    operator_observed_events: list[dict] = field(default_factory=list)
    customer_reported_events: list[dict] = field(default_factory=list)
    revision: int = 0
    ontology_version: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _allegation_family(breach: str) -> str:
    b = (breach or "").lower()
    if any(t in b for t in ("pay", "payment", "ticket", "permit", "vrm", "reg")):
        return "payment"
    if any(t in b for t in ("bay", "child", "disabled", "blue badge", "parent")):
        return "bay"
    return "overstay"


def material_tags_for(fact_name: str, allegation: str) -> list[str]:
    """Which reasoning slices this fact is material to (not a legal conclusion)."""
    tags: list[str] = []
    family = _allegation_family(allegation)
    material = _MATERIAL_BY_ALLEGATION.get(family, frozenset())
    if fact_name in material or fact_name in _VISIT_FACTS:
        tags.append("allegation")
    if fact_name in _VISIT_FACTS or fact_name in (
            "entry_time", "exit_time", "parking_event_date", "departure_reason"):
        tags.append("timeline")
    if fact_name in ("pcn_number", "vrm", "operator_name", "notice_route"):
        tags.append("document_identity")
    if fact_name in material:
        tags.append("legal_analysis")
    if fact_name in ("payment_made", "keying_error_type", "departure_reason",
                     "purpose_of_visit"):
        tags.append("evidence")
    if not tags:
        tags.append("supporting_context")
    return tags


def _doc_entities(case) -> list[dict]:
    names = (
        "pcn_number", "vrm", "operator_name", "parking_location",
        "parking_event_date", "entry_time", "exit_time", "alleged_breach",
        "notice_route", "charge_amount",
    )
    out = []
    for name in names:
        node = (getattr(case, "facts", None) or {}).get(name)
        if node is None or getattr(node, "value", None) in (None, "", []):
            continue
        out.append({
            "name": name,
            "value": node.value,
            "source": "DOCUMENT",
            "source_type": getattr(getattr(node, "source", None), "kind", None)
            and node.source.kind.value,
            "attribution": "OPERATOR" if name in (
                "entry_time", "exit_time", "alleged_breach") else "DOCUMENT",
            "confidence": float(getattr(node, "confidence", 0.9) or 0.9),
            "material_to": material_tags_for(name, str(case.get("alleged_breach") or "")),
        })
    return out


def _concept_events(concepts: list[Any], texts: list[str]) -> list[dict]:
    """Operator vs customer event frames from concepts (not legal conclusions)."""
    events: list[dict] = []
    by = {getattr(c, "concept", None): c for c in concepts}
    excerpt = (texts[0][:240] if texts else "")

    def _ev(eid: str, kind: str, concept: str, *, attribution: str,
            polarity: str = "AFFIRMED") -> None:
        c = by.get(concept)
        events.append({
            "event_id": eid,
            "kind": kind,
            "concept": concept,
            "polarity": polarity if c is None else c.polarity,
            "attribution": attribution,
            "source": "CUSTOMER_FREE_TEXT" if attribution == "CUSTOMER" else attribution,
            "source_type": "CUSTOMER_FREE_TEXT",
            "source_excerpt": (c.source_text if c is not None else excerpt)[:240],
            "confidence": float(c.confidence) if c is not None else 0.7,
        })

    if by.get("SHOPPING") and by["SHOPPING"].polarity == "AFFIRMED":
        _ev("E-visit-1", "VISIT", "SHOPPING", attribution="CUSTOMER")
    if by.get("DROP_OFF") and by["DROP_OFF"].polarity == "AFFIRMED":
        _ev("E-dropoff", "DROP_OFF", "DROP_OFF", attribution="CUSTOMER")
    if by.get("LEFT_SITE") and by["LEFT_SITE"].polarity == "AFFIRMED":
        _ev("E-depart", "DEPART_SITE", "LEFT_SITE", attribution="CUSTOMER")
    if by.get("RETURNED") and by["RETURNED"].polarity == "AFFIRMED":
        _ev("E-return", "RETURN_SITE", "RETURNED", attribution="CUSTOMER")
    if by.get("PICK_UP") and by["PICK_UP"].polarity == "AFFIRMED":
        _ev("E-pickup", "PICK_UP", "PICK_UP", attribution="CUSTOMER")
    if by.get("MULTIPLE_VISITS") and by["MULTIPLE_VISITS"].polarity == "AFFIRMED":
        if "DROP_OFF" not in by and "SHOPPING" not in by:
            _ev("E-visit-2", "VISIT", "MULTIPLE_VISITS", attribution="CUSTOMER")
    return events


def _timeline_from_events(events: list[dict], case) -> list[dict]:
    """Ordered customer visit sequence + operator observation anchors."""
    # DROP_OFF → DEPART_SITE → RETURN_SITE → PICK_UP (activity sequence).
    preferred = (
        "E-visit-1", "E-dropoff", "E-depart", "E-return", "E-pickup", "E-visit-2",
    )
    rank = {eid: i for i, eid in enumerate(preferred)}
    cust = sorted(
        [e for e in events if e.get("attribution") == "CUSTOMER"],
        key=lambda e: (rank.get(e.get("event_id"), 50), e.get("event_id") or ""),
    )
    timeline: list[dict] = []
    seq = 0
    for e in cust:
        seq += 1
        label = {
            "E-visit-1": "visit",
            "E-dropoff": "drop_off",
            "E-depart": "depart_site",
            "E-return": "return_site",
            "E-pickup": "pick_up",
            "E-visit-2": "second_visit",
        }.get(e["event_id"], e["kind"].lower())
        timeline.append({
            "seq": seq,
            "event_id": e["event_id"],
            "kind": e["kind"],
            "label": label,
            "attribution": "CUSTOMER_ACCOUNT",
            "source_excerpt": e.get("source_excerpt"),
            "polarity": e.get("polarity"),
            "confidence": e.get("confidence"),
        })
    # Operator-observed span (allegation), distinct from continuous parking.
    entry = case.get("entry_time")
    exit_t = case.get("exit_time")
    event_date = case.get("parking_event_date")
    if entry or exit_t:
        timeline.append({
            "seq": 0,
            "event_id": "E-operator-span",
            "kind": "OPERATOR_OBSERVED_SPAN",
            "label": "operator_observed_entry_exit",
            "attribution": "OPERATOR_ALLEGATION",
            "operator_observed_entry": entry,
            "operator_observed_exit": exit_t,
            "event_date": event_date,
            "note": "Not interpreted as one continuous customer visit without review",
        })
    return timeline


def _relationships(concepts: list[Any], atoms: list[dict]) -> list[dict]:
    """Causal / temporal links — never legal conclusions."""
    by = {c.concept: c for c in concepts}
    rel: list[dict] = []
    affirmed = {c.concept for c in concepts if c.polarity == "AFFIRMED"}

    dep = next((a for a in atoms if a.get("fact_name") == "departure_reason"
                or a.get("name") == "departure_reason"
                or a.get("kind") == "departure_reason"
                or a.get("atom_id") == "NA-departure_reason"), None)
    if dep and "LEFT_SITE" in affirmed:
        rel.append({
            "subject": "forgotten_item_or_departure_reason",
            "predicate": "CAUSES",
            "object": "departure_from_site",
            "source_excerpt": (dep.get("source_text") or dep.get("source_excerpt") or "")[:240],
            "confidence": float(dep.get("confidence") or 0.8),
        })
    if "DROP_OFF" in affirmed and "LEFT_SITE" in affirmed:
        rel.append({
            "subject": "DROP_OFF",
            "predicate": "PRECEDES",
            "object": "DEPART_SITE",
            "confidence": 0.85,
        })
    if "LEFT_SITE" in affirmed and "RETURNED" in affirmed:
        rel.append({
            "subject": "DEPART_SITE",
            "predicate": "PRECEDES",
            "object": "RETURN_SITE",
            "confidence": 0.85,
        })
        rel.append({
            "subject": "left_site+returned_same_day",
            "predicate": "SUPPORTS",
            "object": "multiple_visits",
            "confidence": 0.85,
        })
    if "RETURNED" in affirmed and "PICK_UP" in affirmed:
        rel.append({
            "subject": "RETURN_SITE",
            "predicate": "PRECEDES",
            "object": "PICK_UP",
            "confidence": 0.85,
        })
    if "MULTIPLE_VISITS" in affirmed:
        rel.append({
            "subject": "customer_account",
            "predicate": "SUPPORTS",
            "object": "multiple_visits",
            "confidence": float(by["MULTIPLE_VISITS"].confidence),
        })
    return rel


def _missing(concepts: list[Any], case) -> list[dict]:
    out = []
    by = {c.concept: c for c in concepts}
    if by.get("LEFT_SITE") and by["LEFT_SITE"].polarity == "AFFIRMED":
        if "RETURNED" not in by and case.get("returned_same_day") is None:
            out.append({"topic": "return_timing", "detail": "left_site affirmed; return not stated"})
        if case.get("departure_reason") in (None, ""):
            out.append({"topic": "departure_reason",
                        "detail": "left site without stated reason"})
    if case.get("entry_time") and case.get("exit_time") and case.get("multiple_visits") is None:
        if by.get("LEFT_SITE") and by["LEFT_SITE"].polarity == "AFFIRMED":
            out.append({"topic": "visit_count",
                        "detail": "operator span present; visit count not confirmed"})
    return out


def build_semantic_case_state(
    case,
    concepts: list[Any],
    *,
    texts: Optional[list[str]] = None,
    narrative_atoms: Optional[list[dict]] = None,
    llm_events: Optional[list[dict]] = None,
    llm_relationships: Optional[list[dict]] = None,
    llm_material_relevance: Optional[list[dict]] = None,
    revision: int = 0,
    ontology_version: str = "",
) -> SemanticCaseState:
    texts = list(texts or [])
    atoms = list(narrative_atoms or [])
    allegation = str(case.get("alleged_breach") or "")
    events = _concept_events(concepts, texts)
    # Merge LLM events that have no ontology concept (material unmapped meaning).
    seen_ev = {e.get("event_id") for e in events}
    for e in llm_events or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("event_id") or f"EV-LLM-{len(events)+1}"
        if eid in seen_ev:
            continue
        row = dict(e)
        row["event_id"] = eid
        events.append(row)
        seen_ev.add(eid)
    timeline = _timeline_from_events(events, case)

    fact_rows = []
    for c in concepts:
        mapping = CONCEPT_TO_FACTS.get(c.concept)
        if not mapping:
            continue
        name, value = mapping
        fact_rows.append({
            "name": name,
            "intended_value": value if c.polarity == "AFFIRMED" else None,
            "polarity": c.polarity,
            "attribution": c.attribution,
            "source": "CUSTOMER_FREE_TEXT",
            "source_type": "CUSTOMER_FREE_TEXT",
            "source_excerpt": (c.source_text or "")[:240],
            "confidence": float(c.confidence or 0),
            "concept": c.concept,
            "material_to": material_tags_for(name, allegation),
        })

    provenance = [{
        "kind": "semantic_concept",
        "concept": c.concept,
        "polarity": c.polarity,
        "provenance": c.provenance,
        "source_excerpt": (c.source_text or "")[:240],
        "confidence": float(c.confidence or 0),
    } for c in concepts]
    for a in atoms:
        provenance.append({
            "kind": "narrative_atom",
            "fact_name": a.get("fact_name") or "departure_reason",
            "source_excerpt": (a.get("source_excerpt") or a.get("source_text") or "")[:240],
            "proposition": a.get("proposition"),
            "confidence": float(a.get("confidence") or 0.8),
        })

    conf = [{
        "concept": c.concept,
        "confidence": float(c.confidence or 0),
        "polarity": c.polarity,
    } for c in concepts]

    evidence_links = []
    for eid, item in (getattr(case, "evidence", None) or {}).items():
        evidence_links.append({
            "evidence_id": eid,
            "kind": getattr(item, "kind", None),
            "material_to": ["evidence"],
        })

    contradictions = list_material_contradictions(case)

    customer_events = [e for e in events if e.get("attribution") == "CUSTOMER"]
    operator_events = [
        t for t in timeline if t.get("attribution") == "OPERATOR_ALLEGATION"
    ]
    material_relevance = []
    for row in fact_rows:
        material_relevance.append({
            "kind": "fact",
            "name": row.get("name"),
            "material_to": list(row.get("material_to") or []),
            "polarity": row.get("polarity"),
        })
    for a in atoms:
        material_relevance.append({
            "kind": "narrative_atom",
            "name": a.get("name") or a.get("category"),
            "material_to": list(a.get("material_to") or ["supporting_context"]),
            "polarity": a.get("polarity"),
            "mapped_to_ontology": bool(a.get("mapped_to_ontology")),
        })
    for e in customer_events:
        material_relevance.append({
            "kind": "event",
            "name": e.get("kind") or e.get("event_id"),
            "material_to": ["timeline", "allegation"],
            "polarity": e.get("polarity"),
        })
    # LLM-declared relevance (already sanitized upstream).
    for row in llm_material_relevance or []:
        if isinstance(row, dict) and row.get("source_id"):
            material_relevance.append({
                "kind": "llm_relevance",
                "name": row.get("source_id"),
                "material_to": [t.lower() for t in (row.get("relevant_to") or [])],
                "confidence": row.get("confidence"),
            })

    uncertainties = []
    for c in concepts:
        if getattr(c, "polarity", None) == "UNCERTAIN":
            uncertainties.append({
                "kind": "concept", "id": c.concept,
                "source_text": (c.source_text or "")[:240],
            })
    for a in atoms:
        if a.get("polarity") == "UNCERTAIN":
            uncertainties.append({
                "kind": "narrative_atom",
                "id": a.get("atom_id") or a.get("category"),
                "proposition": (a.get("proposition") or "")[:240],
            })
    for e in events:
        if e.get("polarity") == "UNCERTAIN":
            uncertainties.append({
                "kind": "event",
                "id": e.get("event_id") or e.get("kind"),
                "description": (e.get("description") or e.get("proposition") or "")[:240],
            })

    rels = _relationships(concepts, atoms)
    seen_rel = {(r.get("source_id"), r.get("relationship"), r.get("target_id")) for r in rels}
    for r in llm_relationships or []:
        key = (r.get("source_id"), r.get("relationship"), r.get("target_id"))
        if key in seen_rel:
            continue
        rels.append(dict(r))
        seen_rel.add(key)

    return SemanticCaseState(
        document_entities=_doc_entities(case),
        facts=fact_rows,
        concepts=[c.as_dict() if hasattr(c, "as_dict") else dict(c) for c in concepts],
        narrative_atoms=atoms,
        events=events,
        timeline=timeline,
        relationships=rels,
        contradictions=contradictions,
        uncertainties=uncertainties,
        missing_information=_missing(concepts, case),
        evidence_links=evidence_links,
        provenance=provenance,
        confidence=conf,
        material_relevance=material_relevance,
        operator_observed_events=operator_events,
        customer_reported_events=customer_events,
        revision=revision,
        ontology_version=ontology_version,
    )


def list_material_contradictions(case) -> list[dict]:
    """Detect material visit-sequence inconsistencies (no silent overwrite)."""
    left = case.get("left_site") is True
    returned = case.get("returned_same_day") is True
    multi = case.get("multiple_visits")
    out = []
    if left and returned and multi is False:
        out.append({
            "code": "VISIT_SEQUENCE_CONFLICT",
            "facts": ["left_site", "returned_same_day", "multiple_visits"],
            "detail": (
                "left_site and returned_same_day are affirmed but "
                "multiple_visits is false"
            ),
            "status": FACT_CONFLICT,
        })
    return out


def record_material_conflicts(case) -> list[dict]:
    """Write FACT_CONFLICT rows onto the case; do not last-write-wins."""
    from ..fact_graph import now

    found = list_material_contradictions(case)
    recorded = []
    for row in found:
        # Idempotent: skip if an open FACT_CONFLICT already covers these facts.
        key = tuple(sorted(row["facts"]))
        already = any(
            c.get("status") == FACT_CONFLICT
            and c.get("rule") == "material_consistency"
            and tuple(sorted(c.get("facts") or [])) == key
            for c in (case.fact_conflicts or [])
        )
        if already:
            continue
        conflict = {
            "conflict_id": f"fc-material-{'-'.join(key)}",
            "fact": "multiple_visits",
            "fact_name": "multiple_visits",
            "facts": list(row["facts"]),
            "held_value": False,
            "proposed_value": True,
            "rule": "material_consistency",
            "status": FACT_CONFLICT,
            "resolution_status": FACT_CONFLICT,
            "resolution": None,
            "detail": row["detail"],
            "code": row["code"],
            "at": now(),
            "run_id": getattr(case, "run_id", 0),
        }
        case.fact_conflicts.append(conflict)
        case.audit.append({"event": "fact_conflict", **conflict})
        recorded.append(conflict)
    return recorded


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "value") and not isinstance(obj, (str, int, float, bool)):
        try:
            return obj.value
        except Exception:
            return str(obj)
    return obj


STATE_BUDGET = 24000
ATOMS_BUDGET = 8000
ROW_CAP = 200                  # per channel, before the byte-budget shrink
# Shed in this order when the payload does not fit: audit/context channels
# first. The material meaning channels are never in this list - they are
# trimmed round-robin afterwards so no single one is wiped out.
_SHED_ORDER = (
    "provenance", "confidence", "evidence_links", "document_entities",
    "timeline", "missing_information", "uncertainties",
    "operator_observed_events", "customer_reported_events",
    "material_relevance", "relationships", "contradictions",
)
_MATERIAL = ("narrative_atoms", "events", "concepts", "facts")
_EXCERPT_KEYS = ("source_text", "source_excerpt", "proposition", "description")


def _fit_json(payload: dict, budget: int) -> tuple[str, list[dict]]:
    """Serialize `payload` within `budget`, shrinking the STRUCTURE only.

    Slicing a JSON string yields a blob every reader's json.loads rejects, so
    the whole semantic state would be silently discarded (the material meaning
    with it). Instead trim excerpts, then drop whole rows from the least
    material channel first, and always return parseable JSON plus a record of
    what was shed so the loss is auditable rather than silent.
    """
    shed: list[dict] = []
    blob = json.dumps(payload, default=str)
    if len(blob) <= budget:
        return blob, shed
    before = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    # Bound the shrink loop: re-serializing per popped row is fine for tens of
    # rows, not for thousands.
    for key in _SHED_ORDER + _MATERIAL:
        rows = payload.get(key)
        if isinstance(rows, list) and len(rows) > ROW_CAP:
            del rows[ROW_CAP:]

    for row in (r for k in _SHED_ORDER + _MATERIAL
                for r in (payload.get(k) or []) if isinstance(r, dict)):
        for ek in _EXCERPT_KEYS:
            if isinstance(row.get(ek), str) and len(row[ek]) > 180:
                row[ek] = row[ek][:180]
    blob = json.dumps(payload, default=str)

    for key in _SHED_ORDER:
        rows = payload.get(key)
        if len(blob) <= budget:
            break
        if not isinstance(rows, list) or not rows:
            continue
        while rows and len(blob) > budget:
            rows.pop()
            blob = json.dumps(payload, default=str)
        shed.append({"channel": key, "kept": len(rows)})

    # Material meaning: trim the longest channel one row at a time so every
    # channel keeps a share rather than the first one being emptied.
    while len(blob) > budget:
        live = [k for k in _MATERIAL if payload.get(k)]
        if not live:
            break
        payload[max(live, key=lambda k: len(payload[k]))].pop()
        blob = json.dumps(payload, default=str)
    for key in _MATERIAL:
        rows = payload.get(key)
        if isinstance(rows, list) and len(rows) < before.get(key, len(rows)):
            shed.append({"channel": key, "kept": len(rows)})
    return blob, shed


def attach_semantic_state(case, state: SemanticCaseState, held: bool = False) -> None:
    """Persist SemanticCaseState for knowledge handoff + claim-plan gate.

    `held`: the customer-account stream is not ready (understanding.is_ready), so
    the state is kept for the record under `*_held` keys and nothing that reads
    the live keys - knowledge retrieval, analysis, the pack, drafting context -
    can see it. Nothing is deleted; it is simply not consumed.
    """
    payload = _json_safe(state.as_dict())
    blob, shed = _fit_json(payload, STATE_BUDGET)
    live, held_suffix = ("", "_held")
    suffix = held_suffix if held else live
    other = live if held else held_suffix
    case.raw_answers["_semantic_case_state" + suffix] = blob
    case.raw_answers.pop("_semantic_case_state" + other, None)
    case.raw_answers["_semantic_revision"] = str(state.revision)
    # Compact atom list for pack/DraftPlan, fitted the same way.
    atoms = list(payload.get("narrative_atoms") or [])
    atoms_blob, atoms_shed = _fit_json({"narrative_atoms": atoms}, ATOMS_BUDGET)
    case.raw_answers["_semantic_narrative_atoms" + suffix] = json.dumps(
        json.loads(atoms_blob).get("narrative_atoms") or [])
    case.raw_answers.pop("_semantic_narrative_atoms" + other, None)
    # Lightweight attribute for in-process consumers (not a redesign of CaseFile).
    setattr(case, "semantic_case_state", None if held else payload)
    entry = {
        "event": "semantic_case_state",
        "held": held,
        "revision": state.revision,
        "concepts": len(state.concepts),
        "events": len(state.events),
        "timeline": len(state.timeline),
        "relationships": len(state.relationships),
        "contradictions": len(state.contradictions),
    }
    if shed or atoms_shed:
        entry["truncated"] = shed + atoms_shed
    case.audit.append(entry)


def open_material_fact_conflicts(case) -> list[dict]:
    return [
        c for c in (case.fact_conflicts or [])
        if c.get("status") == FACT_CONFLICT
        and c.get("rule") == "material_consistency"
    ]


def handoff_ready(case, *, texts: Optional[list[str]] = None) -> tuple[bool, list[str]]:
    """Pre-Claim-Plan checks for the semantic → fact → knowledge handoff."""
    reasons: list[str] = []
    if open_material_fact_conflicts(case):
        reasons.append("unresolved_material_fact_conflict")
    has_text = bool(texts)
    if texts is None:
        from ..engines.account import _collect_customer_texts
        has_text = bool(_collect_customer_texts(case))
    # A held customer stream is deliberately absent from the live keys; its
    # absence is not a failed handoff, and it must not stop the independent
    # document and legal analysis. The hold is recorded, and reported, elsewhere.
    from . import understanding
    if has_text and understanding.customer_stream_blocked(case) is None:
        if not (case.raw_answers or {}).get("_semantic_case_state"):
            reasons.append("semantic_state_missing")
        if not (case.raw_answers or {}).get("_semantic_concepts"):
            reasons.append("semantic_concepts_missing")
        # Material visit facts that concepts intended must have reached FactManager.
        try:
            concepts = json.loads((case.raw_answers or {}).get("_semantic_concepts") or "[]")
        except (TypeError, ValueError):
            concepts = []
        for c in concepts:
            if c.get("polarity") != "AFFIRMED":
                continue
            mapping = CONCEPT_TO_FACTS.get(c.get("concept") or "")
            if not mapping:
                continue
            name, value = mapping
            if name in SEMANTIC_OWNED_FACTS and case.get(name) != value:
                # May be blocked by document conflict — still material if visit facts.
                if name in _VISIT_FACTS:
                    reasons.append(f"material_fact_not_in_factmanager:{name}")
    stamp = (case.raw_answers or {}).get("_semantic_fact_revision")
    if stamp is not None:
        current = str(len(getattr(case, "fact_history", None) or []))
        # Allow growth after semantic (derived props); block if semantic stamp absent after texts.
        if has_text and not stamp:
            reasons.append("semantic_fact_revision_missing")
    return (not reasons), reasons


def handoff_blocks_claim_plan(case) -> Optional[str]:
    ok, reasons = handoff_ready(case)
    if ok:
        return None
    return ";".join(reasons)
