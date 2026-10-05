"""Narrative understanding: the customer's account as atomic facts and
hypotheses (P2).

The account engine used to map a phrase straight to a legal fact ("left and
came back" -> `multiple_visits = True`), which then selected a ground and
reached the letter. A phrase does not say who or what moved: the customer may
have walked back to the car, left the shop and not the car park, or driven
away and returned. So this module reads the account in three steps:

  1. clauses   the text is split into clauses; each clause gets an event frame
  2. frames    per clause: the events (visit, leave, return, explicit second
               visit), whether each is negated (negation scopes over the verbs
               that follow it in the clause, so "I did not leave and come back"
               negates both), how the person travelled (vehicle / on foot), what
               was left (the site, or only a building) and when they came back
  3. reading   atomic facts are what the customer plainly said (visited, the
               purpose, left the site, came back the same day). Anything that
               would need an inference about the VEHICLE is a hypothesis
               (hypotheses.py), never a fact, and is asked about

Deterministic: the same text always gives the same frames, facts and
hypotheses (no model call, so a re-read of an unchanged account changes
nothing). Generic: no operator, site or retailer names; "the supermarket" is
`visited_premises = True, purpose_of_visit = shopping`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..hypotheses import Hypotheses, _key
from ..models import CaseFile, Fact, FactSource, FactStatus, SourceKind

# Atomic facts this module writes. Provenance only: withheld from the drafter
# (reasoning.withheld_from_drafter) and never asked (analysis.INTERNAL_FACTS).
# Narrative atoms written by this module.
NARRATIVE_FACTS = frozenset({
    "visited_premises", "purpose_of_visit", "left_site", "returned_same_day",
    "possible_vehicle_departure", "returned_to_vehicle",
    # Generic professional reason for leaving (not a semantic ontology concept).
    "departure_reason",
})
# Internal-only atoms: never letter content / never citable fact_refs.
NARRATIVE_INTERNAL = frozenset({
    "possible_vehicle_departure", "returned_to_vehicle",
})
# Letter-facing narrative particulars (SupportBundle → DraftPlan → letter).
NARRATIVE_LETTER_FACTS = frozenset(NARRATIVE_FACTS - NARRATIVE_INTERNAL)

_CLAUSES = re.compile(
    r"[.;!?\n]+|,\s*|\s+-\s+|"
    r"\s+(?:but|then|so|because|although|though|however|afterwards|after that|whereas)\s+",
    re.I)
_NEGATOR = re.compile(
    r"\b(?:not|never|no longer|without|didn'?t|did\s+not|hadn'?t|had\s+not|haven'?t|"
    r"wasn'?t|weren'?t|isn'?t|don'?t|do\s+not|cannot|can'?t|couldn'?t)\b", re.I)

_VISIT = re.compile(
    r"\b(?:went (?:to|into|shopping)|visited|visiting|was at|were at|popped (?:in|into)|"
    r"nipped (?:in|into)|shopping (?:at|in)|went in)\b", re.I)
_LEAVE = re.compile(
    r"\b(?:left|leave|leaving|went out|exited|exit(?:ed)? the|departed|drove (?:off|away|out|home)|"
    r"pulled out|moved (?:the|my|our) (?:car|vehicle))\b", re.I)
_RETURN = re.compile(
    r"\b(?:came back|come back|coming back|returned|return(?:ing)?|went back|go(?:ing)? back|"
    r"drove back|re-?entered|back again|back later)\b", re.I)
_EXPLICIT_MULTI = re.compile(
    r"\b(?:two (?:sep[ae]rate )?visits|more than one visit|visited .{0,20}twice|sep[ae]rate visits|"
    r"second visit|(?:entered|came in|drove in) twice)\b", re.I)
_VEHICLE = re.compile(
    r"\b(?:drove|drive|driving|driven|by car|in (?:the|my|our) (?:car|vehicle)|"
    r"(?:the )?car ?park|moved (?:the|my|our) (?:car|vehicle)|pulled (?:out|in)|re-?entered|"
    r"barrier)\b", re.I)
_ON_FOOT = re.compile(r"\b(?:walked|walk(?:ing)?|on foot|ran|running)\b", re.I)
_TO_VEHICLE = re.compile(
    r"\b(?:back to|to|went to|returned to|walked to|out to) (?:the|my|our) (?:car|vehicle|van)\b",
    re.I)
_BUILDING_LEAVE = re.compile(
    r"\b(?:left|leave|leaving|exited|went out of|came out of|walked out of) (?:the |a )?"
    r"(?:store|shop|supermarket|building|restaurant|cafe|café|gym|office|premises|centre|"
    r"center|mall|hospital|surgery|pharmacy|bank)\b", re.I)
_LATER = re.compile(r"\b(?:later|that day|same day|afterwards|again|a second time|an hour)\b", re.I)
_OTHER_DAY = re.compile(r"\b(?:next day|following day|another day|the day after|tomorrow)\b",
                        re.I)

_PURPOSES = (
    ("shopping", re.compile(r"\b(?:shop(?:ping)?|groceries|supermarket|store|retail)\b", re.I)),
    ("dining", re.compile(r"\b(?:eat|ate|lunch|dinner|breakfast|restaurant|cafe|café|coffee)\b",
                          re.I)),
    ("medical", re.compile(r"\b(?:doctor|gp|hospital|appointment|surgery|clinic|pharmacy)\b",
                           re.I)),
    ("leisure", re.compile(r"\b(?:gym|cinema|swim(?:ming)?|leisure)\b", re.I)),
    ("work", re.compile(r"\b(?:work|shift|my job|office)\b", re.I)),
)

# Generic departure-reason cues (item forgotten / collect item / realised at home).
# Not phrase-specific and not an ontology concept — yields one professional
# proposition with customer-source provenance.
_DEPARTURE_REASON = re.compile(
    r"(?P<span>"
    r"(?:forgot(?:ten)?|left behind)\s+(?:my |the |our |his |her |a |an )?"
    r"(?:\w[\w-]{1,24}['’]s\s+)*\w[\w-]{1,24}(?:\s+\w[\w-]{1,24})?"
    r"|"
    r"(?:left|went|drove|departed)\b[^.]{0,40}\b(?:to |in order to )?(?:collect|get|fetch|retrieve|pick up)\s+"
    r"(?:my |the |our |a |an )?(?:\w[\w-]{1,24}['’]s\s+)*"
    r"\w[\w-]{1,24}(?:\s+\w[\w-]{1,24})?"
    r"|"
    r"realis(?:e|ed|ing)\b[^.]{0,60}\b(?:forgot(?:ten)?|at home|left (?:behind|at home)|"
    r"necessary (?:item|thing)|had been forgotten)"
    r")",
    re.I,
)


# The thing the customer actually named, taken from their own span. Captured
# as a word class, not a list of items, so any noun is preserved.
_REASON_OBJECT = re.compile(
    r"\b(?:forgot(?:ten)?|left behind|left|collect|get|fetch|retrieve|pick up)\s+"
    r"(?P<poss>my |our |his |her |their )?(?:the |a |an )?"
    # A possessive chain ("my daughter's inhaler") describes the owner, not the
    # thing; skip past it so the object the customer named is the one kept.
    r"(?:[a-z][\w-]{1,24}['’]s\s+)*"
    r"(?P<obj>[a-z][\w-]{2,24}(?:\s+[a-z][\w-]{2,24})?)\b",
    re.I,
)
# A compound object stops at the next clause: "my purse and drove home" names
# the purse, not "purse and". Grammar words, never nouns being described.
_OBJECT_STOP = frozenset({
    "and", "but", "so", "then", "before", "after", "because", "which", "that",
    "from", "with", "at", "in", "on", "to", "for", "was", "were", "had", "has",
    "is", "it", "the", "a", "an", "back", "again", "home", "there", "later",
    "while", "when", "as", "of", "out", "off", "up", "down", "over",
})
# Words that are grammar rather than the object being described.
_NOT_AN_OBJECT = frozenset({
    "home", "there", "back", "again", "it", "them", "him", "her", "us", "me",
    "that", "this", "those", "these", "something", "anything", "one", "some",
    "behind", "from", "with", "into", "onto", "about", "around", "the", "and",
    "car", "vehicle", "park", "site", "to", "at", "in", "on", "later", "then",
})


def departure_reason_object(span: str) -> tuple[str, bool]:
    """The specific thing the customer named, and whether they owned it.

    Returns ("", False) when the span names nothing specific.
    """
    for m in _REASON_OBJECT.finditer(span or ""):
        words = (m.group("obj") or "").strip().lower().split()
        while len(words) > 1 and words[-1] in _OBJECT_STOP:
            words.pop()
        obj = " ".join(words)
        if obj and words[0] not in _NOT_AN_OBJECT:
            return obj, bool(m.group("poss"))
    return "", False


def departure_reason_proposition(span: str) -> str:
    """Professional proposition for a departure-reason span.

    The customer's own object is kept. Substituting "a necessary item" for
    whatever they actually named discarded the particular that makes the
    account specific, and no downstream layer could recover it. The generic
    wording survives only as the fallback for a span naming nothing.
    """
    low = (span or "").lower()
    obj, owned = departure_reason_object(span)
    named = f"their {obj}" if owned else (f"the {obj}" if obj
                                          else "a necessary item")
    if re.search(r"\b(collect|fetch|retrieve|pick up)\b", low):
        return f"the departure was to collect {named}"
    if re.search(r"\brealis", low):
        return (f"{named} was realised to have been left elsewhere, "
                f"prompting the departure")
    return f"{named} had been forgotten, prompting the departure"


def extract_departure_reason(text: str) -> Optional[dict[str, Any]]:
    """Return narrative atom for the reason for leaving, or None."""
    m = _DEPARTURE_REASON.search(text or "")
    if not m:
        return None
    span = (m.group("span") or m.group(0) or "").strip()
    if len(span) < 4:
        return None
    proposition = departure_reason_proposition(span)
    return {
        "atom_id": "NA-departure_reason",
        "name": "departure_reason",
        "proposition": proposition,
        "source_text": span,
        "source_excerpt": (text or "")[:240],
        "attribution": "CUSTOMER",
        "polarity": "AFFIRMED",
        "confidence": 0.85,
    }


@dataclass
class Frame:
    """One clause, as events and their circumstances."""
    text: str
    events: list[tuple[str, int, bool]] = field(default_factory=list)   # (kind, pos, negated)
    vehicle: bool = False
    on_foot: bool = False
    to_vehicle: bool = False
    building_leave: bool = False
    later: bool = False
    other_day: bool = False

    def positive(self, kind: str) -> bool:
        return any(k == kind and not neg for k, _, neg in self.events)

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "events": [[k, neg] for k, _, neg in self.events],
                "vehicle": self.vehicle, "on_foot": self.on_foot, "to_vehicle": self.to_vehicle,
                "building_leave": self.building_leave}


def frames(text: str) -> list[Frame]:
    out = []
    for raw in _CLAUSES.split(text or ""):
        clause = (raw or "").strip()
        if len(clause) < 2:
            continue
        f = Frame(clause)
        neg = _NEGATOR.search(clause)
        neg_at = neg.start() if neg else None
        for kind, rx in (("VISIT", _VISIT), ("LEAVE", _LEAVE), ("RETURN", _RETURN),
                         ("MULTI", _EXPLICIT_MULTI)):
            for m in rx.finditer(clause):
                # Negation scopes forward over the clause: "did not leave and
                # come back" negates both verbs; "left, not realising" does not
                # reach back (it is a separate clause).
                f.events.append((kind, m.start(), neg_at is not None and neg_at < m.start()))
        f.events.sort(key=lambda e: e[1])
        f.vehicle = bool(_VEHICLE.search(clause))
        f.on_foot = bool(_ON_FOOT.search(clause))
        f.to_vehicle = bool(_TO_VEHICLE.search(clause))
        f.building_leave = bool(_BUILDING_LEAVE.search(clause))
        f.later = bool(_LATER.search(clause))
        f.other_day = bool(_OTHER_DAY.search(clause))
        out.append(f)
    return out


@dataclass
class Reading:
    facts: dict[str, Any]
    hypothesis: dict[str, Any] | None           # {value, confidence, signals}
    frames: list[Frame]


def read(text: str) -> Reading:
    """Atomic facts and the multiple-visit hypothesis for one text."""
    fs = frames(text)
    facts: dict[str, Any] = {}
    signals: list[str] = []

    if any(f.positive("VISIT") for f in fs):
        facts["visited_premises"] = True
    for purpose, rx in _PURPOSES:
        if rx.search(text or ""):
            facts["purpose_of_visit"] = purpose
            break

    # A departure of the VEHICLE is possible when someone left the site (not
    # only a building) and nothing says they were on foot.
    departure_at = None
    for i, f in enumerate(fs):
        if not f.positive("LEAVE"):
            continue
        if f.on_foot and not f.vehicle:
            signals.append("left_on_foot")
            continue
        if f.building_leave and not f.vehicle:
            signals.append("left_building_only")
            continue
        departure_at = i if departure_at is None else departure_at
        signals.append("left_vehicle" if f.vehicle else "left_unspecified")

    returned, vehicle_return = False, False
    for i, f in enumerate(fs):
        if f.to_vehicle and f.on_foot and not f.vehicle:
            facts["returned_to_vehicle"] = True        # "walked back to the car"
        if not f.positive("RETURN"):
            continue
        if f.to_vehicle or (f.on_foot and not f.vehicle):
            # Going back TO the vehicle, or on foot: the person moved, the car did not.
            if f.to_vehicle:
                facts["returned_to_vehicle"] = True
            signals.append("returned_on_foot")
            continue
        if departure_at is not None and i >= departure_at:
            returned = True
        elif departure_at is None and f.later and not f.building_leave:
            # "I returned later": a return implies a departure before it.
            returned = True
            signals.append("return_implies_departure")
        else:
            continue
        vehicle_return = vehicle_return or f.vehicle
        facts["returned_same_day"] = not any(x.other_day for x in fs)

    explicit = any(f.positive("MULTI") for f in fs)
    if departure_at is not None:
        facts["left_site"] = True

    # Material reason for leaving — narrative atom, not a semantic ontology concept.
    atom = extract_departure_reason(text or "")
    if atom and (
        departure_at is not None
        or facts.get("left_site")
        or returned
        or any(f.positive("LEAVE") for f in fs)
        or any(f.positive("RETURN") for f in fs)
    ):
        facts["departure_reason"] = atom["proposition"]

    hypothesis = None
    # A return on another day is a separate event, not a second visit within
    # the period the charge is about.
    if returned and facts.get("returned_same_day") is False and not explicit:
        signals.append("returned_another_day")
        returned = False
    if explicit or returned:
        left_by_vehicle = departure_at is not None and fs[departure_at].vehicle
        if explicit:
            confidence = 0.8
            signals.append("explicit_second_visit")
        elif vehicle_return or left_by_vehicle:
            confidence = 0.8
        elif departure_at is None:
            confidence = 0.4
        else:
            confidence = 0.5
        facts["possible_vehicle_departure"] = True
        hypothesis = {"value": True, "confidence": confidence,
                      "signals": list(dict.fromkeys(signals))}
    facts = {k: v for k, v in facts.items() if v is not None}
    return Reading(facts, hypothesis, fs)


# Ontology-owned facts are promoted only via semantics → FactManager.
_ONTOLOGY_FACT_NAMES: Optional[frozenset] = None


def _ontology_fact_names() -> frozenset:
    global _ONTOLOGY_FACT_NAMES
    if _ONTOLOGY_FACT_NAMES is None:
        from ..semantics.ontology import CONCEPT_TO_FACTS
        _ONTOLOGY_FACT_NAMES = frozenset(name for name, _ in CONCEPT_TO_FACTS.values())
    return _ONTOLOGY_FACT_NAMES


def understand(case: CaseFile, texts: list[str], *,
               write_ontology_facts: bool = False) -> dict[str, Any]:
    """Read every customer text: write atomic facts, propose hypotheses,
    withdraw hypotheses the account no longer supports.

    Ontology-mapped facts (left_site, returned_same_day, purpose_of_visit, …)
    default to *not* writing here — they must pass semantic → FactManager.
    Non-ontology narrative atoms (departure_reason, possible_vehicle_departure)
    still write through FactManager.
    """
    written: dict[str, Any] = {}
    skipped_ontology: dict[str, Any] = {}
    supported: set[str] = set()
    atoms: list[dict[str, Any]] = []
    owned = _ontology_fact_names()
    for raw in texts:
        text = str(raw or "").strip()
        if len(text) < 4:
            continue
        r = read(text)
        atom = extract_departure_reason(text)
        for name, value in r.facts.items():
            if name in written or name in skipped_ontology:
                continue
            excerpt = text[:240]
            if name == "departure_reason" and atom:
                excerpt = atom.get("source_text") or excerpt
                atoms.append(atom)
            if not write_ontology_facts and name in owned:
                skipped_ontology[name] = value
                continue
            written[name] = value
            case.put(Fact(f"F-{name}", name, value, FactStatus.DERIVED,
                          FactSource(SourceKind.CUSTOMER_FREE_TEXT, f"free_text:narrative:{name}",
                                     excerpt=excerpt)),
                     reason="narrative_understanding")
        if r.hypothesis is not None:
            Hypotheses.propose(case, "multiple_visits", r.hypothesis["value"],
                                   source_text=text, confidence=r.hypothesis["confidence"],
                                   signals=r.hypothesis["signals"], rule="narrative.read")
            supported.add(_key("multiple_visits", r.hypothesis["value"]))
    Hypotheses.withdraw_unsupported(case, supported)
    if atoms:
        case.audit.append({"event": "narrative_atom", "atoms": atoms})
        # Provenance rows for traceability (source text ↔ proposition).
        # material_account_propositions are merged later in assess_material_account.
        prov = list(getattr(case, "free_text_provenance", None) or [])
        for a in atoms:
            prov.append({
                "source": "CUSTOMER_FREE_TEXT",
                "original": a.get("source_excerpt") or a.get("source_text"),
                "fact_name": "departure_reason",
                "normalized_value": a.get("proposition"),
                "drafting_proposition": a.get("proposition"),
                "relevant_to_allegation": True,
                "answer_id": "narrative_atom:departure_reason",
                "text_span": a.get("source_text"),
                "attribution": a.get("attribution"),
                "polarity": a.get("polarity"),
                "confidence": a.get("confidence"),
                "extractor_version": "narrative_v1",
            })
        case.free_text_provenance = prov
    case.audit.append({"event": "narrative_understanding", "facts": written,
                       "skipped_ontology_facts": skipped_ontology,
                       "hypotheses": sorted(supported),
                       "narrative_atoms": atoms})
    return {"facts": written, "skipped_ontology_facts": skipped_ontology,
            "hypotheses": sorted(supported), "narrative_atoms": atoms}
