"""Meaning-bridge semantic cues for offline / reference-model evaluation (P10.5).

This is NOT production LLM understanding and NOT exact holdout phrase patches.
It approximates meaning-level synonymy so ReferenceAnalysisLLM can exercise the
LLM-primary path when no live model is available.

Cues describe semantic classes (restart failure, keyed mismatch, departed site)
rather than copying sealed-holdout sentences.
"""
from __future__ import annotations

import re
from typing import Iterable

from dataclasses import asdict, dataclass

from .ontology import CONCEPTS

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


@dataclass
class _Cue:
    concept: str
    polarity: str = "AFFIRMED"
    attribution: str = "CUSTOMER"
    source_text: str = ""
    confidence: float = 0.8
    provenance: str = "meaning_bridge_reference"

    def as_dict(self) -> dict:
        return asdict(self)


def _window_negated(text: str, start: int, end: int) -> bool:
    """Polarity negation is only material in the same clause before the cue."""
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
    r"\b((did not|didn't|do not|don't|never)\s+pay|not paid|"
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

# "the vehicle departed" as a synonym class, written once. Movement verbs take
# an optional adverb before the direction ("drove STRAIGHT back out", "went
# right back out"), which is how people actually write it.
_ADV = r"(?:\s(?:straight|right|immediately|just|back|then))*"
_LEAVE = (
    r"left|exit\w*|turned" + _ADV + r"\s(?:around|back)|"
    r"(?:drove|driv\w+|went|pulled|headed)" + _ADV +
    r"\s(?:off|out|away|back\sout|elsewhere|home)"
)

# concept → meaning cues (regex). Broad synonym classes; not case-specific.
_MEANING_CUES: tuple[tuple[str, re.Pattern], ...] = (
    ("BROKEN_DOWN", re.compile(
        r"\b(would not restart|wouldn't restart|failed to restart|"
        r"refused to (fire|start|restart)|would not fire|"
        r"could not (get (it|the car) (to )?start|restart)|"
        r"stopped (working|functioning)|ceased (to )?(work|function)|"
        r"ceased normal operation|developed a (fault|problem)|vehicle failure|"
        r"mechanical (problem|trouble)|car (failed|fault)|"
        r"engine (cut out|failed|died|refused)|wouldn't start|would not start|"
        r"mechanical (fault|failure|issue)|lost power|stalled|"
        r"broke down|breakdown)\b", re.I)),
    ("IMMOBILISED", re.compile(
        r"\b(could not (move|drive|leave)|unable to (move|drive|leave)|"
        r"prevented (normal )?departure|stuck (on site|in the (bay|car park)|until)|"
        r"immobilised|immobilized|waited for (roadside|recovery|assistance)|"
        r"roadside (help|assistance|recovery)|assistance was arranged|"
        r"recovery was (called|arranged)|needed (help|assistance|recovery))\b", re.I)),
    ("PAYMENT_MADE", re.compile(
        r"\b((completed|settled|finished|made) (the )?payment|"
        r"payment (was )?(made|completed|taken|settled)|"
        r"tariff paid|parking (was )?paid|paid through|"
        r"I paid|we paid|paid (for|via|using|with|on)|"
        r"paid (the|my) parking|"
        r"(did not|didn't|do not|don't) pay|not paid|no payment (was )?made|"
        r"never paid|settled (the )?tariff|tariff (was )?settled)\b",
        re.I)),
    ("KEYING_ERROR", re.compile(
        r"\b(keyed|keying|mis-?key|mistyped|typ(o|ed)|"
        r"one character (wrong|incorrect|off|different|differed)|"
        r"character (differed|wrong|incorrect)|"
        r"(vrm|plate|registration).{0,40}(off by|wrong by|differed by) one|"
        r"off by one (letter|character)|"
        r"entered .{0,24}(incorrect|wrong|mistyped)|"
        r"wrong (reg|registration|plate)|"
        r"(reg|registration|plate|vrm).{0,40}(wrong|incorrect|error|mistake|"
        r"differ|mismatch|off by))\b", re.I)),
    ("REGISTRATION_MISMATCH", re.compile(
        r"\b(registration (did not|didn't|does not|doesn't) "
        r"(exactly )?match|different (reg|plate)|"
        r"incorrect registration|mismatched (reg|plate|registration))\b", re.I)),
    ("LEFT_SITE", re.compile(
        r"\b(left (the )?(site|car park|retail park|location)|"
        r"did not leave|didn't leave|never left|"
        r"departed (the )?(site|location|car park)|"
        r"went (off site|elsewhere|away)|drove (away|out)|"
        r"drove away before|exited (the )?(site|car park)|was no longer on site|"
        r"not on site during|went off-site|off-site|"
        r"later left|then left|left after\b|"
        r"left,?\s+and\s+(then\s+)?(returned|came|come)|"
        r"left and (then )?(came|come) back|left and (then )?returned)\b", re.I)),
    # KB-CON-02: the terms were considered and declined, and the vehicle left.
    # The semantic class is "decision against the terms, then departure" - the
    # production path reads CONCEPT_DEFINITIONS["TERMS_REJECTED_LEFT"] for the
    # meaning; these cues only let the offline reference model reach the same
    # concept. Requires BOTH a rejection of the terms and a departure, so
    # "the sign was unclear" or a plain "I left" alone does not reach it.
    ("TERMS_REJECTED_LEFT", re.compile(
        # One vocabulary for each half of the meaning, shared by every branch,
        # so a phrasing that works in "declined THEN left" order also works in
        # "left BECAUSE declined" order. Keeping three separate copies of the
        # rejection list let "decided against it" match one order only.
        # DECLINE: a decision against the terms/charge. LEAVE: a departure.
        # The concept needs BOTH, in either order, so a bare "too expensive" or
        # a bare "I left" never reaches it. The only standalone form is an
        # explicit statement that no parking happened.
        r"(?:"
        r"(?:(?P<d1>did\s?n[o']?t\s(?:agree|accept|want)|does\s?n[o']?t\sagree"
        r"|did\s?n[o']?t\slike|not\s(?:agree|accept|prepared|willing)"
        r"|too\s(?:expensive|much|dear|steep)|refus\w*|declin\w*"
        r"|decided\s(?:against|not)|chang\w*\smy\smind"
        r"|would\s?n[o']?t\spay|unwilling)"
        r"[^.!?]{0,90}"
        r"(?P<l1>" + _LEAVE + r"))"
        r"|"
        r"(?:(?P<l2>" + _LEAVE + r")"
        r"[^.!?]{0,90}"
        r"(?P<d2>did\s?n[o']?t\s(?:agree|accept|want)|does\s?n[o']?t\sagree"
        r"|did\s?n[o']?t\slike|not\s(?:agree|accept|prepared|willing)"
        r"|too\s(?:expensive|much|dear|steep)|refus\w*|declin\w*"
        r"|decided\s(?:against|not)|chang\w*\smy\smind"
        r"|would\s?n[o']?t\spay|unwilling))"
        r"|(?P<nopark>(?:never|did\s?n[o']?t)\s+park(?:ed)?\b"
        r"|no parking took place)"
        r")",
        re.I)),
    ("RETURNED", re.compile(
        r"\b(came back|come back|returned(?:\s+(to|later))?|went back (to|in)|"
        r"re-?entered|came back (later|afterwards)|"
        r"before (coming|returning) back|coming back)\b", re.I)),
    ("MULTIPLE_VISITS", re.compile(
        r"\b(two visits|more than one visit|visited twice|second (visit|entry|stay)|"
        r"another stay|left and (then )?(returned|came back|come back)|came back later|"
        r"more than one (entry|stay)|two stays|second short stay)\b", re.I)),
    ("LOADING", re.compile(
        r"\b(loading|unloading|loaded|unloaded|"
        r"load(ing)? (stock|goods)|goods were being load)\b", re.I)),
    ("DELIVERY", re.compile(r"\b(deliver(y|ing|ed)|courier|stock drop)\b", re.I)),
    ("COLLECTION", re.compile(
        r"\b(collection|collecting (goods|a parcel|an order|stock)|"
        r"take a parcel|parcel (from|order)|collection point)\b", re.I)),
    ("PICK_UP", re.compile(
        r"\b(pick[ -]?up|picking up|"
        r"pick(?:ed|ing)?\b.{0,24}\bup\b|"
        r"collect(?:ing|ed)?\b.{0,24}\b(them|him|her|a passenger|the passenger|"
        r"a friend))\b", re.I)),
    ("DROP_OFF", re.compile(
        r"\b(drop[ -]?off|dropping off|"
        r"drop(?:ped|ping)?\b.{0,40}\boff\b|"
        r"set(?:ting)? down (a )?(passenger|rider))\b", re.I)),
    ("SHOPPING", re.compile(r"\b(shopping|bought|purchases?|supermarket)\b", re.I)),
    ("PERMIT_HELD", re.compile(
        r"\b(have a permit|hold a permit|resident('s)? permit|permit holder|"
        r"authoris(ed|ation) (to park|permit))\b", re.I)),
    ("PERMIT_DISPLAYED", re.compile(
        r"\b(permit (was )?(displayed|shown)|displayed .{0,12}permit|"
        r"whether a permit was shown)\b", re.I)),
    ("PAYMENT_ATTEMPTED", re.compile(
        r"\b(tried to pay|attempted (to )?pay|went to pay)\b", re.I)),
    ("PAYMENT_FAILED", re.compile(
        r"\b(payment (failed|declined|didn't go through|did not go through)|"
        r"machine (rejected|failed|would not accept))\b", re.I)),
    ("CHILD_PRESENT", re.compile(
        r"\b(child|children|kids?|toddler|infant|baby).{0,30}"
        r"(in (the )?(car|vehicle)|with (me|us)|on board)\b", re.I)),
    ("DISABLED_PASSENGER", re.compile(
        r"\b(blue badge|disabled (passenger|bay|driver)|disability)\b", re.I)),
    ("PASSENGER_PRESENT", re.compile(
        r"\b(passenger|someone (else )?in (the )?(car|vehicle))\b", re.I)),
)


def extract_concepts_meaning_bridge(texts: Iterable[str]) -> list[dict]:
    """Reference-model meaning cues — dicts validated by extract.validate_concepts."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in texts:
        text = str(raw or "").strip()
        if len(text) < 4:
            continue
        uncertain = _uncertain(text)
        for concept, pattern in _MEANING_CUES:
            if concept not in CONCEPTS:
                continue
            m = pattern.search(text)
            if not m:
                continue
            polarity = "NEGATED" if _window_negated(text, m.start(), m.end()) else "AFFIRMED"
            if concept == "PAYMENT_MADE" and (
                    _NEGATIVE_PAYMENT.search(text)
                    or re.search(r"\bnever paid\b", text, re.I)):
                polarity = "NEGATED"
            # Third-party attribution — do not affirm as customer fact
            if re.search(r"\b(they told me|someone said|i was told)\b", text, re.I):
                if polarity == "AFFIRMED" and concept in (
                        "BROKEN_DOWN", "IMMOBILISED", "PAYMENT_MADE"):
                    continue  # skip; attribution not customer-affirmed
            if concept == "BROKEN_DOWN" and _NEGATIVE_BREAKDOWN.search(text):
                polarity = "NEGATED"
            if concept == "LEFT_SITE" and _NEGATIVE_LEFT.search(text):
                polarity = "NEGATED"
            if uncertain and polarity == "AFFIRMED":
                polarity = "UNCERTAIN"
            if uncertain and polarity == "NEGATED" and concept in (
                    "PERMIT_DISPLAYED", "PERMIT_HELD", "LEFT_SITE", "RETURNED"):
                polarity = "UNCERTAIN"
            key = (concept, polarity)
            if key in seen:
                continue
            seen.add(key)
            out.append(_Cue(
                concept=concept, polarity=polarity, attribution="CUSTOMER",
                source_text=text[:240],
                confidence=0.8 if polarity == "AFFIRMED" else 0.6,
            ).as_dict())
    return out
