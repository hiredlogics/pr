"""What the notice asserts, as typed propositions, and how a case fact relates
to them.

Before this module the allegation existed only as raw text plus a category
boolean (`restricted_bay_alleged`). There was nothing for a confirmed customer
fact to be matched AGAINST, so a contradiction could only be recomputed ad hoc
by whichever engine happened to care - which is how a confirmed "children were
present" ended up expressed as a generic evidential complaint that the operator
had not proved its case.

An operator's allegation is a factual claim. "Parked in a Parent and Child bay
without being accompanied by a child" asserts `child_occupant_present = False`.
Once that is written down as a proposition, the relationship between it and a
usable fact is one generic comparison, and the same comparison serves a permit,
payment or continuity allegation without a second code path.

The mapping from allegation wording to proposition is deliberately a small,
explicit table rather than anything inferred at runtime: a proposition drives a
ground that goes into a letter, so which words mean which proposition must be
reviewable, versioned and testable. It is keyed on the kind of contravention,
never on an operator or a site.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import Fact, FactStatus, SourceKind

# ---------------------------------------------------------------- relations
CONTRADICTS = "CONTRADICTS"
SUPPORTS = "SUPPORTS"
PARTIALLY_ADDRESSES = "PARTIALLY_ADDRESSES"
UNRELATED = "UNRELATED"

RELATIONSHIPS = (CONTRADICTS, SUPPORTS, PARTIALLY_ADDRESSES, UNRELATED)


@dataclass(frozen=True)
class AllegationProposition:
    """One material factual claim the notice makes.

    `subject` is the fact name the claim is about, so a proposition and a fact
    are directly comparable. `asserted_value` is what the operator says that
    fact is - usually False, because an allegation is typically the absence of
    an entitlement ("without a valid permit" asserts `permit_held = False`).
    """
    proposition_id: str
    allegation_type: str
    subject: str
    predicate: str
    asserted_value: Any
    source_document_ref: str
    evidence_refs: list[str] = field(default_factory=list)
    # The wording in the notice this was read from, for the audit trail.
    source_text: str = ""

    def as_dict(self) -> dict:
        return {
            "proposition_id": self.proposition_id,
            "allegation_type": self.allegation_type,
            "subject": self.subject,
            "predicate": self.predicate,
            "asserted_value": self.asserted_value,
            "source_document_ref": self.source_document_ref,
            "evidence_refs": list(self.evidence_refs),
            "source_text": self.source_text[:200],
        }


@dataclass(frozen=True)
class AllegationRule:
    """One row of the controlled mapping: wording -> proposition."""
    allegation_type: str
    pattern: re.Pattern[str]
    subject: str
    asserted_value: Any
    predicate: str = "is"
    # Wording that means this rule does NOT apply even though the pattern hit.
    unless: Optional[re.Pattern[str]] = None


def _r(p: str) -> re.Pattern[str]:
    return re.compile(p, re.I)


# The controlled table. Each row says: when the contravention is described this
# way, the operator is asserting this fact has this value.
#
# Rows are about kinds of contravention, not operators or sites. Adding one is
# a reviewable KB-style change, and every row is covered by a test asserting
# both that it fires on its own wording and that it does not fire on wording it
# should leave alone.
_RULES: tuple[AllegationRule, ...] = (
    # --- eligibility bays: the allegation is that the required occupant or
    # entitlement was absent, not that the stay was too long.
    AllegationRule(
        "RESTRICTED_BAY_OCCUPANT",
        _r(r"\b(parent|family)\b[^.]{0,40}\b(and|&)?\s*child\b|\bchild\s*(and|&)\s*parent\b"),
        "child_occupant_present", False),
    AllegationRule(
        "RESTRICTED_BAY_DISABLED",
        _r(r"\b(disabled|accessible|blue\s*badge)\b[^.]{0,30}\b(bay|space|parking)\b|"
           r"\b(bay|space)\b[^.]{0,30}\bdisabled\b"),
        "blue_badge_displayed", False),
    AllegationRule(
        "RESTRICTED_BAY_EV",
        _r(r"\b(ev|electric\s*vehicle|charging)\b[^.]{0,30}\b(bay|space|point)\b"),
        "ev_charging_session", False),
    # --- permits
    AllegationRule(
        "PERMIT_NOT_VALID",
        _r(r"\b(no|without|invalid|not\s+display\w*|failed\s+to\s+display)\b[^.]{0,30}"
           r"\b(permit|season\s*ticket|resident'?s?\s*permit)\b|"
           r"\bpermit\b[^.]{0,30}\b(not\s+(valid|displayed)|invalid|expired)\b"),
        "permit_held", False),
    # --- payment
    AllegationRule(
        "PAYMENT_NOT_MADE",
        _r(r"\b(without\s+(making|paying)|no\s+payment|not\s+pa(y|id)|failed\s+to\s+pay|"
           r"non[\s-]?payment|unpaid\s+(tariff|charge\s+for\s+parking))\b"),
        "payment_made", False,
        # An overstay allegation accepts that payment was made; what it disputes
        # is the duration. Reading it as "no payment" would rebut a claim the
        # operator never made.
        unless=_r(r"\b(overstay\w*|exceeded|longer\s+than|beyond\s+the\s+(paid|permitted))\b")),
    # --- ANPR continuity: a single continuous stay is asserted by stating one
    # entry, one exit and a total duration.
    AllegationRule(
        "CONTINUOUS_STAY",
        _r(r"\b(remained|stayed|parked)\b[^.]{0,30}\bcontinuous(ly)?\b|"
           r"\bcontinuous(ly)?\b[^.]{0,30}\b(parked|present|stay)\b|"
           r"\btotal\s+(stay|duration|time)\s+of\b"),
        "multiple_visits", False),
)


def _pid(allegation_type: str, subject: str, ref: str) -> str:
    """A stable id for the same proposition on the same document.

    Deterministic so a re-run of the same case produces the same plan digest:
    a random id would make every locked plan differ from the last.
    """
    h = hashlib.sha256(f"{allegation_type}|{subject}|{ref}".encode()).hexdigest()
    return f"ALG-{h[:12]}"


def derive_allegation_propositions(
        breach: Optional[str], facts: dict[str, Any], *,
        source_ref: str = "notice:alleged_breach",
        evidence_refs: Optional[list[str]] = None) -> list[AllegationProposition]:
    """The typed propositions the notice's own words assert.

    Returns [] for wording no rule recognises. That is deliberate: an
    unrecognised allegation must not be given an invented proposition, because
    a proposition is what a rebuttal ground is built on.
    """
    text = (breach or "").strip()
    if not text:
        return []
    out: list[AllegationProposition] = []
    seen: set[str] = set()
    for rule in _RULES:
        if not rule.pattern.search(text):
            continue
        if rule.unless is not None and rule.unless.search(text):
            continue
        if rule.subject in seen:
            continue
        seen.add(rule.subject)
        out.append(AllegationProposition(
            proposition_id=_pid(rule.allegation_type, rule.subject, source_ref),
            allegation_type=rule.allegation_type,
            subject=rule.subject,
            predicate=rule.predicate,
            asserted_value=rule.asserted_value,
            source_document_ref=source_ref,
            evidence_refs=list(evidence_refs or []),
            source_text=text,
        ))
    return out


# ------------------------------------------------------------- relationships
# A fact may only create a DIRECT factual rebuttal when the customer or a
# document put it forward. These are the statuses that count as established.
_VERIFIED_STATUSES = (
    FactStatus.CONFIRMED, FactStatus.CORRECTED, FactStatus.DERIVED,
    FactStatus.EXTRACTED,
)
# Sources that speak for themselves. CUSTOMER_FREE_TEXT is absent on purpose:
# prose qualifies only where the account engine judged it an explicit
# assertion, which it reports through `customer_asserted`.
_VERIFIED_SOURCES = (
    SourceKind.ANSWER, SourceKind.DOCUMENT, SourceKind.CALCULATION,
)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    if a is None or b is None:
        return a is b
    return str(a).strip().lower() == str(b).strip().lower()


def may_rebut(fact: Fact, *, customer_asserted: bool = False) -> bool:
    """Whether this fact is established enough to contradict an allegation.

    Condition D of the contract. A hypothesis or an unconfirmed reading of
    prose may not: it can still support other grounds, but it cannot put a
    factual proposition into the keeper's mouth.
    """
    if fact is None or not fact.usable:
        return False
    if fact.value in (None, "", "UNKNOWN", []):
        return False
    if fact.source.kind == SourceKind.CUSTOMER_FREE_TEXT:
        return bool(customer_asserted)
    return (fact.source.kind in _VERIFIED_SOURCES
            and fact.status in _VERIFIED_STATUSES)


def relate(prop: AllegationProposition, fact: Fact, *,
           customer_asserted: bool = False) -> str:
    """How one fact stands to one allegation proposition.

    One comparison for every allegation type. CONTRADICTS is reserved for a
    fact that is both established (`may_rebut`) and opposite in value; a fact
    about the same subject that is not established yet is
    PARTIALLY_ADDRESSES, which keeps it visible without letting it assert.
    """
    if fact is None or prop.subject != fact.name:
        return UNRELATED
    if _same(fact.value, prop.asserted_value):
        return SUPPORTS
    if not may_rebut(fact, customer_asserted=customer_asserted):
        return PARTIALLY_ADDRESSES
    return CONTRADICTS
