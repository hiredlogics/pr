"""Allegation normalisation: what KIND of term the notice says was broken.

One classifier, used by every layer that needs the answer, so that two notices that
allege the same thing in different words are treated as the same thing everywhere.

It exists because that was not true. Three places each classified the allegation on
their own, from raw substrings of its wording:

  * the relations graph's `allegation_class` signal (kb_relations.yaml)
  * the records-request gate (KB-REC-01, kb_modules.yaml)
  * the derived `alleged_breach_type` (derivation_rules.yaml)

"Exceeded the maximum stay" was an overstay in all three; "Stayed beyond the permitted
period" was an overstay in none - and because "permit" is a substring of "permitted",
it was classed as a PERMIT allegation and pulled in a records request about permits,
while the equivalent wording did not. Same meaning, different Claim Plan.

A class here is a MEANING, written as a small set of patterns over word boundaries,
not a phrase list: an overstay is excess against a time allowance ("beyond", "past",
"in excess of", "longer than", "exceeded" ... a period, stay, limit, allowance), not
a particular sentence. A permit is the noun, not the participle of "to permit".

Add a pattern here when a new way of saying a class turns up; every rule that reads
the class (`allegation_class` in the fact view) inherits it.
"""
from __future__ import annotations

import re
from typing import Any, Optional

UNCLASSIFIED = "UNCLASSIFIED"

# Up to three modifying words between a preposition of excess and what is exceeded:
# "beyond the permitted period", "past their free parking time".
_MODIFIERS = r"(?:\w+\s+){0,3}?"
_ALLOWANCE = r"(?:period|time|stay|limit|allowance|allowed|duration|session|tariff|maximum)"


def _c(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


# Order is the precedence: the first class whose meaning the wording carries is the
# class. It is the order the relations graph has always applied.
CLASSES: list[tuple[str, list[re.Pattern]]] = [
    ("PROHIBITION", _c(
        r"no[\s-]+parking", r"no\s+stopping", r"no\s+waiting", r"\bprohibited\b",
        r"yellow\s+line", r"\bhatched\b", r"keep\s+clear", r"fire\s+lane",
        r"emergency\s+access", r"\bfootway\b", r"\bpavement\b", r"\bobstruct\w*",
        r"not\s+a\s+parking", r"not\s+a\s+designated")),
    ("RESTRICTED_BAY", _c(
        r"parent\s*(?:and|&)\s*child", r"disabled\s+bay", r"blue\s+badge", r"\bev\s+bay\b",
        r"electric\s+vehicle", r"\breserved\b", r"loading\s+bay")),
    ("OVERSTAY", _c(
        r"\boverst\w*", r"\boutstay\w*", r"\bexceed\w*",
        r"\bmax(?:imum)?\s+(?:stay|time|duration)\b", r"\blonger\s+than\b",
        r"\btime\s+limit\b",
        # Excess against an allowance, however it is phrased.
        rf"\b(?:beyond|past|in\s+excess\s+of)\s+(?:the\s+|their\s+|its\s+|a\s+|an\s+)?"
        rf"{_MODIFIERS}{_ALLOWANCE}\b",
        rf"\bafter\s+(?:the\s+)?(?:end|expiry|expiration)\s+of\s+(?:the\s+|their\s+)?"
        rf"(?:\w+\s+){{0,2}}?(?:period|time|stay|session|tariff)\b",
        r"\b(?:stay\w*|remain\w*|park\w*)\b[^.;]{0,40}\b(?:too\s+long|for\s+longer)\b")),
    # The NOUN: "no valid permit", "residents only". Not "permitted period", where
    # "permitted" is the participle of a time allowance.
    ("PERMIT", _c(r"\bpermits?\b", r"authori[sz]", r"\bresidents?\s+only\b")),
    ("PAYMENT", _c(r"\bpay\w*", r"\bticket", r"\btariff", r"validat")),
]


def concepts(text: Any) -> list[str]:
    """Every class the wording carries, in precedence order (it can carry several)."""
    s = str(text or "")
    return [name for name, pats in CLASSES if any(p.search(s) for p in pats)] if s.strip() else []


def classify(text: Any, restricted_bay: bool = False) -> Optional[str]:
    """The allegation's class: the first meaning it carries, UNCLASSIFIED when it
    carries none, None when there is no allegation text to read.

    `restricted_bay` is the extraction's own EX-12 finding; a bay allegation it
    recognised outranks every class below RESTRICTED_BAY, as it always did.
    """
    s = str(text or "").strip()
    if not s:
        return None
    found = concepts(s)
    if restricted_bay and "RESTRICTED_BAY" not in found:
        found = [c for c in found if c == "PROHIBITION"] + ["RESTRICTED_BAY"] + [
            c for c in found if c != "PROHIBITION"]
    return found[0] if found else UNCLASSIFIED


def has(text: Any, concept: str) -> bool:
    return concept in concepts(text)
