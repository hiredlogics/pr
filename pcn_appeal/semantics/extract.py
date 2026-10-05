"""Narrative → controlled semantic concepts → FactManager (P10.5).

Order (required):
  narrative → semantic extraction → ontology validation → FactManager
  → gap/conflict detection → questions

P10.5: LLM meaning extraction is primary. Deterministic patterns remain as
high-confidence helpers / safety checks. A meaning-bridge reference path
supports offline evaluation when no live model is configured.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from .ontology import (
    CONCEPT_DEFINITIONS, CONCEPT_EXTRA_FACTS, CONCEPT_TO_FACTS, CONCEPTS,
    ONTOLOGY_VERSION, PROMOTE_MIN_CONFIDENCE,
)

_NEGATION = re.compile(
    r"\b(no|not|never|didn't|did not|wasn't|was not|weren't|were not|"
    r"without|neither|nor)\b",
    re.I,
)
_UNCERTAIN = re.compile(
    r"\b(maybe|perhaps|might|may have|not sure|unsure|don't know|"
    r"do not know|can't remember|cannot remember|think so|possibly|"
    r"not certain)\b",
    re.I,
)

# High-confidence helpers only — not the primary meaning path.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("BROKEN_DOWN", re.compile(
        r"\b(engine (cut out|failed|died)|wouldn't start|would not start|"
        r"mechanical (fault|failure|issue)|lost power|stalled|broke down|breakdown)\b", re.I)),
    ("IMMOBILISED", re.compile(
        r"\b(could not (move|drive|leave)|unable to (move|drive|leave)|"
        r"stuck (on site|in the (bay|car park))|immobilised|immobilized)\b", re.I)),
    ("PAYMENT_MADE", re.compile(
        r"\b(I paid|we paid|payment (was )?(made|completed|taken)|"
        r"paid (for|via|using|with|on)|paid (the|my) parking|"
        r"(did not|didn't|do not|don't) pay|not paid|no payment (was )?made)\b"
        r"|(?<![A-Za-z])Paid(?![A-Za-z])|(?<![A-Za-z])paid(?![A-Za-z])", re.I)),
    ("PAYMENT_ATTEMPTED", re.compile(
        r"\b(tried to pay|attempted (to )?pay|went to pay)\b", re.I)),
    ("PAYMENT_FAILED", re.compile(
        r"\b(payment (failed|declined|didn't go through|did not go through)|"
        r"machine (rejected|failed|would not accept))\b", re.I)),
    ("KEYING_ERROR", re.compile(
        r"\b(typ(o|ed)|keying|mis-?key|mistyped|wrong (reg|registration|plate)|"
        r"(reg|registration|plate).{0,20}(wrong|incorrect|error|mistake))\b", re.I)),
    ("REGISTRATION_MISMATCH", re.compile(
        r"\b(registration (did not|didn't) match|different (reg|plate)|"
        r"entered .{0,12}(wrong|incorrect) (reg|registration|plate))\b", re.I)),
    ("LEFT_SITE", re.compile(
        r"\b(left (the )?(site|car park|retail park)|went (off site|elsewhere)|"
        r"drove away (and|then)|exited (the )?(site|car park)|"
        r"left after\b|left,?\s+and\s+(then\s+)?(returned|came|come)|"
        r"left and (then )?(came|come) back|left and (then )?returned)\b", re.I)),
    ("RETURNED", re.compile(
        r"\b(came back|come back|returned(?:\s+(to|later))?|went back (to|in))\b",
        re.I)),
    ("MULTIPLE_VISITS", re.compile(
        r"\b(two(\s+\w+)?\s+visits|more than one visit|visited twice|"
        r"second (visit|entry)|separate visits|"
        r"left and (then )?(returned|came back|come back)|came back later)\b",
        re.I)),
    ("SHOPPING", re.compile(r"\b(shopping|bought|purchases?|supermarket)\b", re.I)),
    # Passenger set-down / collection meaning (structural; not person-name cues).
    ("DROP_OFF", re.compile(
        r"\b(drop[ -]?off|dropping off|"
        r"drop(?:ped|ping)?\b.{0,40}\boff\b|"
        r"set(?:ting)? down (a )?(passenger|rider))\b", re.I)),
    ("PICK_UP", re.compile(
        r"\b(pick[ -]?up|picking up|"
        r"pick(?:ed|ing)?\b.{0,24}\bup\b|"
        r"collect(?:ing|ed)?\b.{0,24}\b(them|him|her|a passenger|the passenger))\b",
        re.I)),
    ("LOADING", re.compile(r"\b(loading|unloading)\b", re.I)),
    ("DELIVERY", re.compile(r"\b(deliver(y|ing|ed)|courier)\b", re.I)),
    ("COLLECTION", re.compile(r"\b(collection|collecting (goods|a parcel|an order))\b", re.I)),
    ("PERMIT_HELD", re.compile(
        r"\b(have a permit|hold a permit|resident('s)? permit|permit holder)\b", re.I)),
    ("PERMIT_DISPLAYED", re.compile(
        r"\b(permit (was )?(displayed|shown)|displayed .{0,12}permit)\b", re.I)),
    ("CHILD_PRESENT", re.compile(
        r"\b(child|children|kids?|toddler|infant|baby).{0,30}"
        r"(in (the )?(car|vehicle)|with (me|us)|on board)\b", re.I)),
    ("DISABLED_PASSENGER", re.compile(
        r"\b(blue badge|disabled (passenger|bay|driver)|disability)\b", re.I)),
    ("PASSENGER_PRESENT", re.compile(
        r"\b(passenger|someone (else )?in (the )?(car|vehicle))\b", re.I)),
)


@dataclass
class SemanticConcept:
    concept: str
    polarity: str = "AFFIRMED"          # AFFIRMED / NEGATED / UNCERTAIN
    attribution: str = "CUSTOMER"       # CUSTOMER / DOCUMENT / DERIVED / THIRD_PARTY
    source_text: str = ""
    confidence: float = 0.8
    provenance: str = "deterministic_pattern"

    def as_dict(self) -> dict:
        return asdict(self)


def _window_negated(text: str, start: int, end: int) -> bool:
    """Polarity negation looks in the same clause *before* the match only.

    Words like ``would not restart`` / ``could not move`` encode the concept
    itself (in-span ``not`` is ignored). Prior-clause negations do not apply.
    """
    clause_start = max(
        text.rfind(".", 0, start),
        text.rfind(";", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind("\n", 0, start),
    ) + 1
    window = text[max(clause_start, start - 48):start]
    return bool(_NEGATION.search(window))


_NEGATIVE_PAYMENT = re.compile(
    r"\b((did not|didn't|do not|don't|never)\s+pay|not paid|never paid|"
    r"no payment(\s+was)?\s+made|never\s+a\s+payment)\b",
    re.I,
)
_NEGATIVE_BREAKDOWN = re.compile(
    r"\b((no|never\s+a)\s+(mechanical\s+)?(fault|failure|breakdown|issue)|"
    r"(did not|didn't)\s+break\s+down|no\s+breakdown)\b",
    re.I,
)
_NEGATIVE_LEFT = re.compile(
    r"\b((did not|didn't|do not|don't|never)\s+leave|"
    r"did not leave the site|never left)\b",
    re.I,
)


def _uncertain(text: str) -> bool:
    return bool(_UNCERTAIN.search(text or ""))


def extract_concepts_deterministic(texts: list[str]) -> list[SemanticConcept]:
    """High-confidence phrase helpers (safety / supplement). Not primary meaning."""
    out: list[SemanticConcept] = []
    seen: set[tuple[str, str]] = set()
    for raw in texts:
        text = str(raw or "").strip()
        if len(text) < 4:
            continue
        uncertain = _uncertain(text)
        for concept, pattern in _PATTERNS:
            if concept not in CONCEPTS:
                continue
            m = pattern.search(text)
            if not m:
                continue
            polarity = "NEGATED" if _window_negated(text, m.start(), m.end()) else "AFFIRMED"
            if concept == "PAYMENT_MADE" and _NEGATIVE_PAYMENT.search(text):
                polarity = "NEGATED"
            if concept == "BROKEN_DOWN" and _NEGATIVE_BREAKDOWN.search(text):
                polarity = "NEGATED"
            if concept == "LEFT_SITE" and _NEGATIVE_LEFT.search(text):
                polarity = "NEGATED"
            if uncertain and polarity == "AFFIRMED":
                polarity = "UNCERTAIN"
            # "not certain whether a permit was shown" is uncertainty, not denial
            if uncertain and concept in ("PERMIT_DISPLAYED", "PERMIT_HELD",
                                        "LEFT_SITE", "RETURNED"):
                polarity = "UNCERTAIN"
            key = (concept, polarity)
            if key in seen:
                continue
            seen.add(key)
            out.append(SemanticConcept(
                concept=concept, polarity=polarity, attribution="CUSTOMER",
                source_text=text[:240], confidence=0.9 if polarity == "AFFIRMED" else 0.6,
                provenance="deterministic_helper",
            ))
    return out


def validate_concepts(raw: list[Any]) -> list[SemanticConcept]:
    """Keep only controlled ontology ids; drop legal / module leakage."""
    out: list[SemanticConcept] = []
    banned = re.compile(
        r"\b(KB-[A-Z]+-\d+|ground|appeal outcome|PoFA|Schedule 4|"
        r"keeper liability|cancel the (pcn|notice))\b",
        re.I,
    )
    for item in raw or []:
        if isinstance(item, SemanticConcept):
            c = item
        elif isinstance(item, dict):
            name = str(item.get("concept") or "").strip().upper()
            if name not in CONCEPTS:
                continue
            src = str(item.get("source_text") or "")
            if banned.search(src) and name not in CONCEPTS:
                continue
            # Reject if source_text is only a module id
            if re.fullmatch(r"\s*KB-[A-Z]+-\d+\s*", src or ""):
                continue
            try:
                conf = float(item.get("confidence") or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            c = SemanticConcept(
                concept=name,
                polarity=str(item.get("polarity") or "AFFIRMED").upper(),
                attribution=str(item.get("attribution") or "CUSTOMER").upper(),
                source_text=src[:240],
                confidence=conf,
                provenance=str(item.get("provenance") or "llm"),
            )
        else:
            continue
        if c.concept not in CONCEPTS:
            continue
        if c.polarity not in ("AFFIRMED", "NEGATED", "UNCERTAIN"):
            c.polarity = "AFFIRMED"
        if c.attribution not in ("CUSTOMER", "DOCUMENT", "DERIVED", "THIRD_PARTY"):
            c.attribution = "CUSTOMER"
        out.append(c)
    return out


def concepts_to_intended_facts(concepts: list[SemanticConcept]) -> dict[str, Any]:
    """Affirmed, attributable, above-threshold concepts → FactManager values."""
    intended: dict[str, Any] = {}
    affirmed = {
        c.concept for c in concepts
        if c.polarity == "AFFIRMED"
        and c.attribution not in ("THIRD_PARTY",)
        and float(c.confidence or 0) >= PROMOTE_MIN_CONFIDENCE
    }
    for c in concepts:
        if c.concept not in affirmed:
            continue
        mapping = CONCEPT_TO_FACTS.get(c.concept)
        if not mapping:
            continue
        fact_name, value = mapping
        intended[fact_name] = value
        for extra_name, extra_val in CONCEPT_EXTRA_FACTS.get(c.concept, ()):
            # When both drop-off and pick-up are affirmed, keep drop_off as the
            # purpose label; both activity facts still promote independently.
            if (extra_name == "purpose_of_visit" and c.concept == "PICK_UP"
                    and "DROP_OFF" in affirmed):
                continue
            intended.setdefault(extra_name, extra_val)
    return intended


def _merge_concepts(*groups: list[SemanticConcept]) -> list[SemanticConcept]:
    """Merge by concept: prefer AFFIRMED > NEGATED > UNCERTAIN; keep highest conf."""
    rank = {"AFFIRMED": 3, "NEGATED": 2, "UNCERTAIN": 1}
    best: dict[str, SemanticConcept] = {}
    for group in groups:
        for c in group:
            prev = best.get(c.concept)
            if prev is None:
                best[c.concept] = c
                continue
            if rank.get(c.polarity, 0) > rank.get(prev.polarity, 0):
                best[c.concept] = c
            elif c.polarity == prev.polarity and c.confidence > prev.confidence:
                best[c.concept] = c
    return list(best.values())


_ALLOWED_RELS = frozenset({
    "PRECEDES", "FOLLOWS", "CAUSES", "SUPPORTS", "CONTRADICTS",
    "SEPARATE_FROM", "OCCURS_DURING",
})
_ALLOWED_RELEVANCE = frozenset({
    "ALLEGATION", "TIMELINE", "GROUND_ELIGIBILITY", "SUBSTANTIVE_REBUTTAL",
    "EVIDENCE", "CONTEXT",
})

# P17.10 — a stable taxonomy for the unmapped-meaning channels.
#
# These labels CLASSIFY meaning; they never carry it. The meaning itself stays
# in `description` / `proposition`, which is what lets an unseen situation
# survive under a known label instead of growing the ontology (or drifting into
# a new label per paraphrase). A label outside these sets is not rejected - the
# row is kept and relabelled to the fallback, so material content is never
# dropped for a taxonomy miss.
ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "ARRIVAL", "DEPARTURE", "RETURN", "PAYMENT", "DELAY", "ACCESS_ISSUE",
    "AUTHORISATION", "EVIDENCE", "ACTIVITY", "MECHANICAL", "KEYING",
    "COMMUNICATION", "OTHER",
})
EVENT_TYPE_FALLBACK = "OTHER"

# Lowercase: the deterministic atom categories (semantics/atoms.py) and the
# candidate-discovery hints (engines/knowledge_matcher.py) are both lowercase.
ALLOWED_ATOM_CATEGORIES: frozenset[str] = frozenset({
    "visit_activity", "visit_purpose", "departure_event", "departure_reason",
    "return_event", "multiple_attendance", "payment", "payment_attempt",
    "mechanical", "access", "access_issue", "authorisation", "keying",
    "timing", "evidence", "person_present", "semantic_concept",
    "unmapped_reason", "unmapped_material",
})
ATOM_CATEGORY_FALLBACK = "unmapped_material"


def _note(errors: Optional[list], code: str, detail: Any) -> None:
    """Record a contract violation explicitly instead of discarding in silence."""
    if errors is not None:
        errors.append({"code": code, "detail": str(detail)[:200]})


def _sanitize_events(raw: list, errors: Optional[list] = None) -> list[dict]:
    out = []
    for i, row in enumerate(raw or []):
        if not isinstance(row, dict):
            _note(errors, "event_not_an_object", type(row).__name__)
            continue
        desc = str(row.get("description") or row.get("proposition") or "").strip()
        src = str(row.get("source_text") or "")[:240]
        if not desc and not src:
            _note(errors, "event_has_no_meaning", sorted(row))
            continue
        pol = str(row.get("polarity") or "AFFIRMED").upper()
        if pol not in ("AFFIRMED", "NEGATED", "UNCERTAIN"):
            pol = "UNCERTAIN"
        attr = str(row.get("attribution") or "CUSTOMER").upper()
        if attr not in ("CUSTOMER", "DOCUMENT", "THIRD_PARTY", "DERIVED"):
            attr = "CUSTOMER"
        mid = str(row.get("event_id") or f"EV-LLM-{i+1}")
        # Never accept legal/KB ids as event types.
        etype = str(row.get("event_type") or row.get("kind") or "OTHER").upper()
        if etype.startswith("KB-") or "POFA" in etype:
            etype = EVENT_TYPE_FALLBACK
        # Taxonomy, not meaning: an unknown label is relabelled, never dropped,
        # because `description` still carries what the customer said.
        if etype not in ALLOWED_EVENT_TYPES:
            _note(errors, "event_type_outside_taxonomy", etype)
            etype = EVENT_TYPE_FALLBACK
        try:
            conf = float(row.get("confidence") or 0.7)
        except Exception:
            conf = 0.7
        out.append({
            "event_id": mid,
            "event_type": etype,
            "kind": etype.lower(),
            "description": desc[:400],
            "proposition": desc[:400],
            "polarity": pol,
            "attribution": attr,
            "source_text": src,
            "confidence": conf,
            "mapped_to_ontology": False,
        })
    return out


def _sanitize_atoms(raw: list, errors: Optional[list] = None) -> list[dict]:
    out = []
    for i, row in enumerate(raw or []):
        if not isinstance(row, dict):
            _note(errors, "atom_not_an_object", type(row).__name__)
            continue
        prop = str(row.get("proposition") or row.get("description") or "").strip()
        src = str(row.get("source_text") or row.get("source_excerpt") or "")[:240]
        if not prop and not src:
            _note(errors, "atom_has_no_meaning", sorted(row))
            continue
        # Reject atoms that smuggle module ids / legal conclusions.
        blob = f"{prop} {src}".upper()
        if "KB-" in blob or "CLAIM PLAN" in blob or "CANCEL THE" in blob:
            _note(errors, "atom_carried_a_legal_conclusion", prop[:80])
            continue
        pol = str(row.get("polarity") or "AFFIRMED").upper()
        if pol not in ("AFFIRMED", "NEGATED", "UNCERTAIN"):
            pol = "UNCERTAIN"
        attr = str(row.get("attribution") or "CUSTOMER").upper()
        if attr not in ("CUSTOMER", "DOCUMENT", "THIRD_PARTY", "DERIVED"):
            attr = "CUSTOMER"
        cat = str(row.get("category") or row.get("name") or "unmapped_reason").lower()
        if cat.startswith("kb-"):
            cat = ATOM_CATEGORY_FALLBACK
        if cat not in ALLOWED_ATOM_CATEGORIES:
            # The specific meaning is in `proposition` and survives untouched;
            # only the classification falls back (P17.10 §2).
            _note(errors, "atom_category_outside_taxonomy", cat)
            cat = ATOM_CATEGORY_FALLBACK
        try:
            conf = float(row.get("confidence") or 0.7)
        except Exception:
            conf = 0.7
        out.append({
            "atom_id": str(row.get("atom_id") or f"NA-LLM-{i+1}"),
            "category": cat,
            "name": cat,
            "proposition": prop[:400] or src[:400],
            "polarity": pol,
            "attribution": attr,
            "source_text": src,
            "source_excerpt": src,
            "confidence": conf,
            "mapped_to_ontology": False,
            "material_to": ["substantive_rebuttal", "timeline", "supporting_context"],
        })
    return out


def _sanitize_relationships(raw: list, errors: Optional[list] = None,
                            known_ids: Optional[set[str]] = None) -> list[dict]:
    """event_id / atom_id are local to one response, so a relationship may only
    join two ids that response actually emitted (P17.10 §3). A dangling edge
    would otherwise point at nothing once the application assigns real ids."""
    out = []
    for row in raw or []:
        if not isinstance(row, dict):
            _note(errors, "relationship_not_an_object", type(row).__name__)
            continue
        rel = str(row.get("relationship") or "").upper()
        if rel not in _ALLOWED_RELS:
            _note(errors, "relationship_outside_vocabulary", rel)
            continue
        src = str(row.get("source_id") or "").strip()
        tgt = str(row.get("target_id") or "").strip()
        if not src or not tgt or src.startswith("KB-") or tgt.startswith("KB-"):
            _note(errors, "relationship_endpoint_invalid", f"{src}->{tgt}")
            continue
        if known_ids is not None and not ({src, tgt} <= known_ids):
            _note(errors, "relationship_references_unemitted_id", f"{src}->{tgt}")
            continue
        out.append({"source_id": src, "relationship": rel, "target_id": tgt})
    return out


def _sanitize_relevance(raw: list, errors: Optional[list] = None) -> list[dict]:
    out = []
    for row in raw or []:
        if not isinstance(row, dict):
            _note(errors, "relevance_not_an_object", type(row).__name__)
            continue
        sid = str(row.get("source_id") or "").strip()
        if not sid or sid.startswith("KB-"):
            _note(errors, "relevance_source_invalid", sid)
            continue
        tags = [
            str(t).upper() for t in (row.get("relevant_to") or [])
            if str(t).upper() in _ALLOWED_RELEVANCE
        ]
        if not tags:
            continue
        try:
            conf = float(row.get("confidence") or 0.7)
        except Exception:
            conf = 0.7
        out.append({"source_id": sid, "relevant_to": tags, "confidence": conf})
    return out


def _llm_extract_product(texts: list[str], llm, *,
                         confirmed_facts: Optional[dict] = None) -> dict:
    """Full semantic_extraction product (concepts + unmapped meaning channels)."""
    from .. import prompts
    from .state import _json_safe
    # Fact values are not all JSON primitives: a notice date arrives here as a
    # datetime.date once extraction has parsed it. Serializing the payload raw
    # raised TypeError for every case holding one, and the caller's except
    # branch then fell back to the offline reference bridge - so the model's
    # semantic reading never ran in production. Normalize before serializing.
    payload = {
        "ontology_version": ONTOLOGY_VERSION,
        "allowed_concepts": sorted(CONCEPTS),
        "concept_definitions": {
            k: CONCEPT_DEFINITIONS[k] for k in sorted(CONCEPT_DEFINITIONS)
        },
        # P17.10 §2: a closed label set for the two unmapped-meaning channels,
        # with an explicit home for meaning that fits none of them. Supplying
        # them is what stops the taxonomy growing one label per paraphrase.
        "allowed_event_types": sorted(ALLOWED_EVENT_TYPES),
        "allowed_atom_categories": sorted(ALLOWED_ATOM_CATEGORIES),
        "event_type_fallback": EVENT_TYPE_FALLBACK,
        "atom_category_fallback": ATOM_CATEGORY_FALLBACK.upper(),
        "confirmed_facts": _json_safe({
            k: v for k, v in (confirmed_facts or {}).items()
            if v is not None
        }),
        "customer_texts": [str(t)[:800] for t in texts if str(t).strip()],
        "output_schema": {
            "concepts": [{
                "concept": "ONTOLOGY_ID",
                "polarity": "AFFIRMED|NEGATED|UNCERTAIN",
                "attribution": "CUSTOMER|DOCUMENT|DERIVED|THIRD_PARTY",
                "source_text": "verbatim span",
                "confidence": 0.0,
            }],
            "events": [{
                "event_id": "E1", "event_type": "GENERIC",
                "description": "normalized meaning",
                "polarity": "AFFIRMED|NEGATED|UNCERTAIN",
                "attribution": "CUSTOMER|DOCUMENT|THIRD_PARTY",
                "source_text": "verbatim span", "confidence": 0.0,
            }],
            "narrative_atoms": [{
                "atom_id": "A1", "category": "GENERIC",
                "proposition": "professional normalized proposition",
                "polarity": "AFFIRMED|NEGATED|UNCERTAIN",
                "attribution": "CUSTOMER|DOCUMENT|THIRD_PARTY",
                "source_text": "verbatim span", "confidence": 0.0,
            }],
            "relationships": [{
                "source_id": "E1", "relationship": "PRECEDES|FOLLOWS|CAUSES|SUPPORTS|CONTRADICTS|SEPARATE_FROM|OCCURS_DURING",
                "target_id": "E2",
            }],
            "material_relevance": [{
                "source_id": "A1",
                "relevant_to": ["ALLEGATION", "TIMELINE", "GROUND_ELIGIBILITY",
                                "SUBSTANTIVE_REBUTTAL", "EVIDENCE", "CONTEXT"],
                "confidence": 0.0,
            }],
        },
        "rules": [
            "Reason from concept_definitions (meaning), not keyword lists.",
            "concepts[]: use only allowed_concepts.",
            "Material meaning without an ontology id MUST be preserved as "
            "events[] / narrative_atoms[] — never discard it.",
            "Preserve the most specific source-supported material detail: "
            "normalization is professional wording, not abstraction. A broad "
            "concept may coexist with a specific atom/event.",
            "Use only allowed_event_types / allowed_atom_categories; fall back "
            "to the stated fallback label and keep the meaning in "
            "description / proposition.",
            "Return all five arrays; use [] where nothing applies.",
            "event_id / atom_id are local to this response; relationships may "
            "reference only ids emitted here.",
            "Never emit KB module ids, legal grounds, legal conclusions, or outcomes.",
        ],
    }
    out = llm.complete_json(
        task="semantic_extraction",
        system=prompts.system("semantic_extraction"),
        user=json.dumps(payload, default=str),
    ) or {}
    return _normalize_product(out)


# Every top-level channel the contract promises. A response that omits one (an
# older persisted reply replayed, or a model that answered only with concepts)
# must still produce a complete product: downstream reads these channels
# positionally and a missing key used to surface as a KeyError / None iteration
# far from the cause.
PRODUCT_CHANNELS = (
    "concepts", "events", "narrative_atoms", "relationships", "material_relevance",
)


def _normalize_product(out: Any) -> dict:
    """Validate one semantic_extraction response into the full product shape.

    Malformed rows are recorded in `schema_errors` rather than dropped in
    silence, so a contract breach is visible in the audit trail instead of
    looking like "the model found nothing" (P17.10 §3).
    """
    errors: list[dict] = []
    if not isinstance(out, dict):
        _note(errors, "response_not_an_object", type(out).__name__)
        out = {}
    for key in PRODUCT_CHANNELS:
        value = out.get(key)
        if value is None:
            _note(errors, "channel_absent_normalized_to_empty", key)
        elif not isinstance(value, list):
            _note(errors, "channel_not_a_list", f"{key}={type(value).__name__}")
    def _rows(key: str) -> list:
        value = out.get(key)
        return value if isinstance(value, list) else []

    events = _sanitize_events(_rows("events"), errors)
    atoms = _sanitize_atoms(_rows("narrative_atoms"), errors)
    local_ids = {str(e.get("event_id")) for e in events}
    local_ids |= {str(a.get("atom_id")) for a in atoms}
    return {
        "concepts": validate_concepts(_rows("concepts")),
        "events": events,
        "narrative_atoms": atoms,
        "relationships": _sanitize_relationships(
            _rows("relationships"), errors, known_ids=local_ids),
        "material_relevance": _sanitize_relevance(_rows("material_relevance"), errors),
        "schema_errors": errors,
    }


def _llm_extract(texts: list[str], llm, *, confirmed_facts: Optional[dict] = None) -> list[SemanticConcept]:
    """Backward-compatible concepts-only wrapper."""
    return list(_llm_extract_product(texts, llm, confirmed_facts=confirmed_facts).get("concepts") or [])


def extract_concepts(texts: list[str], llm=None,
                     confirmed_facts: Optional[dict] = None) -> list[SemanticConcept]:
    """LLM-primary meaning extraction; deterministic helpers merge in."""
    product = extract_semantic_product(texts, llm=llm, confirmed_facts=confirmed_facts)
    return list(product.get("concepts") or [])


def extract_semantic_product(texts: list[str], llm=None,
                             confirmed_facts: Optional[dict] = None) -> dict:
    """Full semantic product: controlled concepts + unmapped meaning channels."""
    helpers = extract_concepts_deterministic(texts)
    llm_product: dict = {
        "concepts": [], "events": [], "narrative_atoms": [],
        "relationships": [], "material_relevance": [], "schema_errors": [],
    }
    used_llm = False
    degraded = ""
    if llm is not None:
        try:
            llm_product = _llm_extract_product(
                texts, llm, confirmed_facts=confirmed_facts)
            used_llm = True
        except Exception as exc:
            # Falling back to the offline bridge is a real loss of semantic
            # quality, so it is never silent: a serialization or provider
            # fault here used to look identical to "the model found nothing".
            used_llm = False
            degraded = f"{type(exc).__name__}: {exc}"[:300]
            from ..llm import redact
            print(f"[semantic] model extraction unavailable, using the "
                  f"reference bridge: {redact(degraded)}")
    if not used_llm:
        try:
            from .meaning_bridge import extract_concepts_meaning_bridge
            bridge = validate_concepts(extract_concepts_meaning_bridge(texts))
            for c in bridge:
                c.provenance = c.provenance or "meaning_bridge_reference"
            llm_product["concepts"] = bridge
        except Exception:
            llm_product["concepts"] = []
    concepts = _merge_concepts(list(llm_product.get("concepts") or []), helpers)
    return {
        "concepts": concepts,
        "events": list(llm_product.get("events") or []),
        "narrative_atoms": list(llm_product.get("narrative_atoms") or []),
        "relationships": list(llm_product.get("relationships") or []),
        "material_relevance": list(llm_product.get("material_relevance") or []),
        "llm_passed": used_llm,
        "degraded_reason": degraded,
        "schema_errors": list(llm_product.get("schema_errors") or []),
    }


def derive_multiple_visits_concept(concepts: list[SemanticConcept]) -> list[SemanticConcept]:
    """If LEFT_SITE + RETURNED are AFFIRMED, affirm MULTIPLE_VISITS when absent.

    Does not override an explicit NEGATED MULTIPLE_VISITS (conflict path).
    """
    by = {c.concept: c for c in concepts}
    left, ret, multi = by.get("LEFT_SITE"), by.get("RETURNED"), by.get("MULTIPLE_VISITS")
    if not (left and left.polarity == "AFFIRMED" and ret and ret.polarity == "AFFIRMED"):
        return concepts
    if multi is not None and multi.polarity in ("NEGATED", "AFFIRMED"):
        return concepts
    return list(concepts) + [SemanticConcept(
        concept="MULTIPLE_VISITS", polarity="AFFIRMED", attribution="DERIVED",
        source_text=(left.source_text or ret.source_text or "")[:240],
        confidence=min(float(left.confidence or 0.8), float(ret.confidence or 0.8)),
        provenance="derived_from_left_and_returned",
    )]


def extract_and_promote(case, texts: Optional[list[str]] = None, llm=None,
                        narrative_atoms: Optional[list[dict]] = None) -> dict:
    """Run extraction and write affirmed concepts into FactManager before questions.

    Sole free-text → fact promotion path for ontology-owned facts. Builds
    SemanticCaseState, runs material consistency, stamps the fact revision for
    knowledge / Claim Plan handoff.
    """
    from ..engines.account import apply_fact_delta
    from ..models import Fact, FactSource, FactStatus, SourceKind
    from .state import (
        attach_semantic_state, build_semantic_case_state, record_material_conflicts,
    )

    if texts is None:
        from ..engines.account import _collect_customer_texts
        texts = _collect_customer_texts(case)
    confirmed = {}
    for name, node in (getattr(case, "facts", None) or {}).items():
        if getattr(node, "usable", False):
            confirmed[name] = node.value
    from .state import SEMANTIC_OWNED_FACTS

    product = extract_semantic_product(
        list(texts or []), llm=llm, confirmed_facts=confirmed)
    concepts = derive_multiple_visits_concept(list(product.get("concepts") or []))
    # Preserve unmapped / uncertain / frame-level meaning as narrative atoms.
    # LLM atoms (no ontology id) merge with heuristic atoms — never discarded.
    from .atoms import collect_narrative_atoms, merge_atoms
    narrative_atoms = collect_narrative_atoms(
        list(texts or []), concepts,
        existing=merge_atoms(
            list(narrative_atoms or []),
            list(product.get("narrative_atoms") or []),
        ))
    intended = concepts_to_intended_facts(concepts)
    # Preserve non-ontology free-text facts (e.g. departure_reason) across delta.
    delta_intended = dict(intended)
    for name, node in list((getattr(case, "facts", None) or {}).items()):
        if name in SEMANTIC_OWNED_FACTS:
            continue
        if getattr(node, "value", None) in (None, "", []):
            continue
        src = getattr(getattr(node, "source", None), "kind", None)
        if src in (SourceKind.CUSTOMER_FREE_TEXT, SourceKind.ANSWER):
            delta_intended.setdefault(name, node.value)
    delta = apply_fact_delta(case, delta_intended) if delta_intended else {}
    for name, value in intended.items():
        existing = case.facts.get(name)
        if existing and existing.usable and existing.source.kind in (
                SourceKind.DOCUMENT, SourceKind.CALCULATION):
            continue
        case.put(Fact(
            f"F-{name}", name, value, FactStatus.ANSWERED,
            FactSource(SourceKind.CUSTOMER_FREE_TEXT,
                       f"semantic:{name}",
                       excerpt=next(
                           (c.source_text for c in concepts
                            if CONCEPT_TO_FACTS.get(c.concept, (None,))[0] == name),
                           "")[:240]),
        ), reason="semantic_concept")
    # Material consistency after FactManager writes (no last-write-wins).
    conflicts = record_material_conflicts(case)
    revision = int((case.raw_answers or {}).get("_semantic_revision") or 0) + 1
    state = build_semantic_case_state(
        case, concepts, texts=list(texts or []),
        narrative_atoms=list(narrative_atoms or []),
        llm_events=list(product.get("events") or []),
        llm_relationships=list(product.get("relationships") or []),
        llm_material_relevance=list(product.get("material_relevance") or []),
        revision=revision, ontology_version=ONTOLOGY_VERSION,
    )
    # Refresh contradictions after conflict recording.
    state.contradictions = [
        {"code": c.get("code"), "facts": c.get("facts"), "detail": c.get("detail"),
         "status": c.get("status")}
        for c in (case.fact_conflicts or [])
        if c.get("rule") == "material_consistency" and c.get("status") != "RESOLVED"
    ] or state.contradictions
    attach_semantic_state(case, state)
    case.raw_answers["_semantic_concepts"] = json.dumps(
        [c.as_dict() for c in concepts])[:8000]
    case.raw_answers["_semantic_fact_revision"] = str(
        len(getattr(case, "fact_history", None) or []))
    case.audit.append({
        "event": "semantic_concepts",
        "ontology_version": ONTOLOGY_VERSION,
        "concepts": [c.as_dict() for c in concepts],
        "promoted_facts": sorted(intended),
        "delta": delta,
        "material_conflicts": conflicts,
        "semantic_revision": revision,
        # Whether the model's reading actually succeeded - not merely whether a
        # client was supplied, which is what this used to report.
        "llm_passed": bool(product.get("llm_passed")),
        "llm_degraded_reason": product.get("degraded_reason") or "",
        # A contract breach in the model's reply is recorded, not hidden: an
        # omitted channel or a malformed row is a different failure from "the
        # account contained nothing material".
        "semantic_schema_errors": list(product.get("schema_errors") or [])[:12],
    })
    return {
        "concepts": concepts, "intended": intended, "delta": delta,
        "state": state, "conflicts": conflicts,
    }
