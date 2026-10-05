"""Generic narrative atoms / event observations (no phrase-specific extractors).

Material customer meaning that does not map to a controlled ontology field must
still survive as atoms/events with provenance. Categories are labels only —
nothing here matches named people, businesses, objects, or test-case wording.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Frame event kinds from engines.narrative → atom categories.
_FRAME_KIND_CATEGORY = {
    "VISIT": "visit_activity",
    "LEAVE": "departure_event",
    "RETURN": "return_event",
    "MULTI": "multiple_attendance",
}

# Generic causal / purpose / condition cues (category detectors, not case phrases).
_REASON_CUE = re.compile(
    r"\b(because|so that|in order to|so I|so we|after|before|when|while|"
    r"due to|owing to|as a result|which meant|which caused)\b",
    re.I,
)
_UNCERTAIN_CUE = re.compile(
    r"\b(maybe|perhaps|might|may have|not sure|unsure|think|possibly|"
    r"not certain|I think)\b",
    re.I,
)
_NEGATION_CUE = re.compile(
    r"\b(no|not|never|didn't|did not|wasn't|was not|without)\b",
    re.I,
)


def _polarity_of(text: str, *, negated: bool = False) -> str:
    if negated or _NEGATION_CUE.search(text or ""):
        # Negation in-clause is handled by caller for frames; keep uncertain first.
        if _UNCERTAIN_CUE.search(text or ""):
            return "UNCERTAIN"
        if negated:
            return "NEGATED"
    if _UNCERTAIN_CUE.search(text or ""):
        return "UNCERTAIN"
    return "AFFIRMED"


def atoms_from_frames(frames: Iterable[Any], *, source_text: str = "") -> list[dict]:
    """Turn narrative clause frames into event atoms (category + provenance)."""
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for fr in frames or []:
        clause = str(getattr(fr, "text", "") or "")[:240]
        events = list(getattr(fr, "events", None) or [])
        for kind, _pos, neg in events:
            cat = _FRAME_KIND_CATEGORY.get(kind)
            if not cat:
                continue
            pol = "NEGATED" if neg else _polarity_of(clause)
            key = (cat, pol, clause[:80])
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "atom_id": f"NA-{cat}-{len(out)}",
                "name": cat,
                "category": cat,
                "proposition": None,
                "source_text": clause,
                "source_excerpt": clause or (source_text or "")[:240],
                "attribution": "CUSTOMER",
                "polarity": pol,
                "confidence": 0.75 if pol == "AFFIRMED" else 0.55,
                "mapped_to_ontology": False,
                "material_to": ["timeline", "allegation", "supporting_context"],
            })
        # Unmapped reason/purpose clause: keep the clause even without ontology hit.
        if clause and _REASON_CUE.search(clause) and not any(
                a.get("source_text") == clause and a.get("category") == "unmapped_reason"
                for a in out):
            out.append({
                "atom_id": f"NA-unmapped-reason-{len(out)}",
                "name": "unmapped_reason",
                "category": "unmapped_reason",
                "proposition": None,
                "source_text": clause,
                "source_excerpt": clause,
                "attribution": "CUSTOMER",
                "polarity": _polarity_of(clause),
                "confidence": 0.6,
                "mapped_to_ontology": False,
                "material_to": ["timeline", "substantive_rebuttal", "supporting_context"],
            })
    return out


def atoms_from_unpromoted_concepts(concepts: Iterable[Any]) -> list[dict]:
    """Preserve NEGATED / UNCERTAIN / below-threshold concepts as atoms."""
    from .ontology import CONCEPT_TO_FACTS
    from .ontology import PROMOTE_MIN_CONFIDENCE

    out: list[dict] = []
    for c in concepts or []:
        polarity = getattr(c, "polarity", None) or "AFFIRMED"
        conf = float(getattr(c, "confidence", 0) or 0)
        concept = getattr(c, "concept", None)
        if not concept:
            continue
        # Affirmed + above threshold + mappable → FactManager path; skip duplicate atom.
        if (polarity == "AFFIRMED" and conf >= PROMOTE_MIN_CONFIDENCE
                and CONCEPT_TO_FACTS.get(concept)
                and getattr(c, "attribution", "CUSTOMER") != "THIRD_PARTY"):
            continue
        out.append({
            "atom_id": f"NA-concept-{concept}-{polarity}",
            "name": f"concept_{concept.lower()}",
            "category": "semantic_concept",
            "concept": concept,
            "proposition": None,
            "source_text": (getattr(c, "source_text", None) or "")[:240],
            "source_excerpt": (getattr(c, "source_text", None) or "")[:240],
            "attribution": getattr(c, "attribution", "CUSTOMER") or "CUSTOMER",
            "polarity": polarity,
            "confidence": conf,
            "mapped_to_ontology": bool(CONCEPT_TO_FACTS.get(concept)),
            "material_to": ["allegation", "timeline", "supporting_context"],
        })
    return out


def merge_atoms(*groups: Iterable[dict]) -> list[dict]:
    """Deduplicate atoms by (category/name, polarity, excerpt prefix)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for group in groups:
        for a in group or []:
            if not isinstance(a, dict):
                continue
            key = (
                a.get("category") or a.get("name"),
                a.get("polarity"),
                (a.get("source_excerpt") or a.get("source_text") or "")[:80],
                a.get("concept"),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(a)
    return out


def atoms_from_text_reasons(texts: Iterable[str]) -> list[dict]:
    """Preserve causal/purpose clauses as unmapped atoms (whole-text scan)."""
    out: list[dict] = []
    for raw in texts or []:
        text = str(raw or "").strip()
        if len(text) < 4 or not _REASON_CUE.search(text):
            continue
        # Prefer the sentence/clause containing the cue.
        for part in re.split(r"[.;\n]+", text):
            clause = part.strip()
            if len(clause) < 8 or not _REASON_CUE.search(clause):
                continue
            out.append({
                "atom_id": f"NA-unmapped-reason-{len(out)}",
                "name": "unmapped_reason",
                "category": "unmapped_reason",
                "proposition": None,
                "source_text": clause[:240],
                "source_excerpt": clause[:240],
                "attribution": "CUSTOMER",
                "polarity": _polarity_of(clause),
                "confidence": 0.6,
                "mapped_to_ontology": False,
                "material_to": [
                    "timeline", "substantive_rebuttal", "supporting_context",
                ],
            })
    return out


def collect_narrative_atoms(
    texts: list[str],
    concepts: Optional[list[Any]] = None,
    *,
    existing: Optional[list[dict]] = None,
) -> list[dict]:
    """Full generic atom set for SemanticCaseState (no phrase-specific rules)."""
    from ..engines.narrative import extract_departure_reason, frames

    frame_atoms: list[dict] = []
    dep_atoms: list[dict] = []
    for raw in texts or []:
        text = str(raw or "").strip()
        if len(text) < 4:
            continue
        frame_atoms.extend(atoms_from_frames(frames(text), source_text=text))
        dep = extract_departure_reason(text)
        if dep:
            dep["category"] = "departure_reason"
            dep["mapped_to_ontology"] = False
            dep["material_to"] = ["timeline", "allegation", "substantive_rebuttal"]
            dep_atoms.append(dep)
    return merge_atoms(
        existing or [],
        dep_atoms,
        frame_atoms,
        atoms_from_text_reasons(texts or []),
        atoms_from_unpromoted_concepts(concepts or []),
    )
