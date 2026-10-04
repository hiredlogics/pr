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
    CONCEPT_DEFINITIONS, CONCEPT_TO_FACTS, CONCEPTS,
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
        r"(did not|didn't|do not|don't) pay|not paid|no payment (was )?made)\b", re.I)),
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
        r"drove away (and|then)|exited (the )?(site|car park))\b", re.I)),
    ("RETURNED", re.compile(
        r"\b(came back|returned (to|later)|went back (to|in))\b", re.I)),
    ("MULTIPLE_VISITS", re.compile(
        r"\b(two visits|more than one visit|visited twice|second (visit|entry)|"
        r"left and (then )?returned|came back later)\b", re.I)),
    ("SHOPPING", re.compile(r"\b(shopping|bought|purchases?|supermarket)\b", re.I)),
    ("DROP_OFF", re.compile(r"\b(drop[ -]?off|dropped (off|someone))\b", re.I)),
    ("PICK_UP", re.compile(r"\b(pick[ -]?up|picked up|collect(ing|ed) (a )?(passenger|friend))\b", re.I)),
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
    for c in concepts:
        if c.polarity != "AFFIRMED":
            continue
        if c.attribution in ("THIRD_PARTY",):
            # Preserve attribution; do not auto-promote third-party claims.
            continue
        if float(c.confidence or 0) < PROMOTE_MIN_CONFIDENCE:
            continue
        mapping = CONCEPT_TO_FACTS.get(c.concept)
        if not mapping:
            continue
        fact_name, value = mapping
        intended[fact_name] = value
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


def _llm_extract(texts: list[str], llm, *, confirmed_facts: Optional[dict] = None) -> list[SemanticConcept]:
    from .. import prompts
    payload = {
        "ontology_version": ONTOLOGY_VERSION,
        "allowed_concepts": sorted(CONCEPTS),
        "concept_definitions": {
            k: CONCEPT_DEFINITIONS[k] for k in sorted(CONCEPT_DEFINITIONS)
        },
        "confirmed_facts": {
            k: v for k, v in (confirmed_facts or {}).items()
            if v is not None
        },
        "customer_texts": [str(t)[:800] for t in texts if str(t).strip()],
        "output_schema": {
            "concepts": [{
                "concept": "ONTOLOGY_ID",
                "polarity": "AFFIRMED|NEGATED|UNCERTAIN",
                "attribution": "CUSTOMER|DOCUMENT|DERIVED|THIRD_PARTY",
                "source_text": "verbatim span",
                "confidence": 0.0,
            }]
        },
        "rules": [
            "Reason from concept_definitions (meaning), not keyword lists.",
            "Use only allowed_concepts.",
            "Emit every distinct concept supported by the texts (multi-concept OK).",
            "NEGATED when the customer denies the meaning; UNCERTAIN when unsure.",
            "THIRD_PARTY when the claim is attributed to someone else.",
            "Never emit KB module ids, legal grounds, legal conclusions, or outcomes.",
        ],
    }
    out = llm.complete_json(
        task="semantic_extraction",
        system=prompts.system("semantic_extraction"),
        user=json.dumps(payload),
    )
    return validate_concepts(out.get("concepts") or [])


def extract_concepts(texts: list[str], llm=None,
                     confirmed_facts: Optional[dict] = None) -> list[SemanticConcept]:
    """LLM-primary meaning extraction; deterministic helpers merge in."""
    helpers = extract_concepts_deterministic(texts)
    llm_concepts: list[SemanticConcept] = []
    used_llm = False
    if llm is not None:
        try:
            llm_concepts = _llm_extract(texts, llm, confirmed_facts=confirmed_facts)
            used_llm = True
        except Exception:
            # Fall through to meaning-bridge / helpers when LLM path unavailable.
            used_llm = False
    if not used_llm:
        try:
            from .meaning_bridge import extract_concepts_meaning_bridge
            llm_concepts = validate_concepts(extract_concepts_meaning_bridge(texts))
            for c in llm_concepts:
                c.provenance = c.provenance or "meaning_bridge_reference"
        except Exception:
            llm_concepts = []
    return _merge_concepts(llm_concepts, helpers)


def extract_and_promote(case, texts: Optional[list[str]] = None, llm=None) -> dict:
    """Run extraction and write affirmed concepts into FactManager before questions."""
    from ..engines.account import apply_fact_delta
    from ..models import Fact, FactSource, FactStatus, SourceKind

    if texts is None:
        from ..engines.account import _collect_customer_texts
        texts = _collect_customer_texts(case)
    confirmed = {}
    for name, node in (getattr(case, "facts", None) or {}).items():
        if getattr(node, "usable", False):
            confirmed[name] = node.value
    concepts = extract_concepts(list(texts or []), llm=llm, confirmed_facts=confirmed)
    intended = concepts_to_intended_facts(concepts)
    delta = {}
    if intended:
        delta = apply_fact_delta(case, intended)
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
    case.raw_answers["_semantic_concepts"] = json.dumps(
        [c.as_dict() for c in concepts])[:8000]
    case.audit.append({
        "event": "semantic_concepts",
        "ontology_version": ONTOLOGY_VERSION,
        "concepts": [c.as_dict() for c in concepts],
        "promoted_facts": sorted(intended),
        "delta": delta,
        "llm_passed": llm is not None,
    })
    return {"concepts": concepts, "intended": intended, "delta": delta}
