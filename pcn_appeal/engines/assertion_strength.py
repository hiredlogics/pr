"""VAL-STRENGTH: a sentence may not assert more than the case state establishes.

The drafter is a renderer. Its recurring failure is not inventing a ground (VAL-PLAN
stops that) but turning a weaker fact into a stronger one while paraphrasing:

    driver unidentified      -> "the customer refuses to identify the driver"
    payment attempted        -> "payment was made"
    receipt exists           -> "parking was authorised"
    permit mentioned         -> "held a valid permit"
    retail park              -> "a customers-only site"
    approved: "X not shown"  -> "the charge is unlawful"

None of these is about one operator, site, ground or wording. Each is a CLAIM KIND:
a way of speaking that is only true when a particular fact is established, or when
the approved wording for the ground itself says it. This module is that table. A
guard fires when a sentence ASSERTS its claim kind (a negated or put-to-proof
sentence - "does not establish that the vehicle was authorised" - is not an
assertion) and the case state does not license it. Add a guard here, or call
`register`, when a new ground introduces a new claim kind; nothing else changes.

Two ways a claim kind can be licensed, and a guard may use either or both:
  facts      at least one of these verified facts is truthy in pack.verified_facts
  wording    the approved wording in the pack (module propositions and building
             blocks the Claim Plan retrieved) itself uses the term. The client's
             knowledge base is the legal authority: a legal conclusion it states may
             be paraphrased, one it does not state may not be added.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

I = re.IGNORECASE


@dataclass(frozen=True)
class Guard:
    id: str
    pattern: re.Pattern
    message: str
    facts: tuple[str, ...] = ()       # any one truthy verified fact licenses it
    wording: bool = False             # the pack's approved wording may license it
    # Group in `pattern` whose text the approved wording must contain, when
    # `wording` is set. Group 0 (the whole match) when None.
    term_group: Optional[int] = None


# A negator shortly before the match turns an assertion into a denial or a put-to-proof
# ("does not establish that", "no evidence that", "has not been shown to").
_NEGATION = re.compile(
    r"\b(?:not|no|never|neither|nor|cannot|can't|without|fails?|failed|failure|"
    r"unable|n't|nothing|none)\b|n['’]t\b", I)
_NEG_WINDOW = 70


def _negated(text: str, start: int) -> bool:
    return bool(_NEGATION.search(text[max(0, start - _NEG_WINDOW):start]))


# Which term the sentence relies on for a wording-licensed guard, normalised so
# "unlawful" and "unlawfully" share a stem.
def _stem(term: str) -> str:
    t = re.sub(r"[^a-z ]", "", term.lower()).strip()
    return re.sub(r"(?:ly|ed|ing|s)$", "", t) if len(t) > 5 else t


GUARDS: list[Guard] = [
    # The keeper's silence about who drove is not a refusal. The system never asks
    # who was driving, so no fact can establish that anyone declined to say.
    Guard(
        "driver-refusal",
        re.compile(
            r"\b(?:refus\w+|declin\w+|unwilling)\b"
            r"[^.;:]{0,40}\b(?:identify|name\s+the\s+driver|identity\s+of\s+the\s+driver|"
            r"who\s+(?:was|is)\s+driving|driver['’]?s\s+(?:name|identity))", I),
        "States that someone refused or declined to identify the driver; an "
        "unidentified driver is not a refusal",
        facts=("driver_refused_to_identify", "driver_identity_refused"),
    ),
    # Payment: an attempt, an app failure or a receipt is not a completed payment.
    Guard(
        "payment-completed",
        re.compile(
            r"\b(?:payment|fee|tariff|charge for parking)\s+(?:was|had been|has been)\s+"
            r"(?:successfully\s+|duly\s+|correctly\s+)?(?:made|paid|completed|taken|"
            r"processed|received|accepted)\b"
            r"|\b(?:the\s+)?(?:vehicle|parking)\s+was\s+(?:duly\s+)?paid\s+for\b", I),
        "States that payment was completed without a verified payment fact",
        facts=("payment_made",),
    ),
    # Authorisation: evidence that a purchase or permit exists does not by itself
    # prove the vehicle was authorised to park.
    Guard(
        "authorisation-established",
        re.compile(
            r"\b(?:was|were|had been|has been|is)\s+(?:duly\s+|properly\s+|fully\s+)?"
            r"(?:authori[sz]ed|permitted|entitled)\s+to\s+(?:park|remain|be\s+(?:there|on))\b"
            r"|\b(?:authori[sz]ation|permission)\s+(?:existed|was\s+(?:in\s+place|held|granted))\b", I),
        "States that the vehicle was authorised to park without an authorisation fact",
        facts=("authorisation", "permit_held", "authorisation_source"),
    ),
    Guard(
        "valid-permit",
        re.compile(
            r"\b(?:held|holds|had|displayed|displaying|possess\w*)\s+(?:a\s+|the\s+)?"
            r"(?:valid|current|in-date|paid-up)\s+(?:permit|licen[cs]e|pass|ticket|badge)\b", I),
        "States that a valid permit was held or displayed without a verified permit fact",
        facts=("permit_held",),
    ),
    # Site status: a type of place is not a rule about who may park there.
    Guard(
        "customer-only-site",
        re.compile(
            r"\b(?:customers?[- ]only|for\s+customers?\s+only|reserved\s+(?:exclusively\s+)?for\s+"
            r"customers?|exclusively\s+for\s+customers?)\b", I),
        "Describes the site as customer-only without that being established",
        facts=("customer_only_site",),
    ),
    # The allegation is the operator's; the letter may not adopt it as proven.
    Guard(
        "allegation-adopted",
        re.compile(
            r"\b(?:the\s+)?(?:overstay|breach|contravention|alleged\s+breach)\s+"
            r"(?:is|was|has\s+been|had\s+been)\s+(?:proven|proved|established|confirmed|admitted)\b", I),
        "Treats the operator's allegation as proven or admitted",
    ),
    # Approved wording may say a requirement "has not been established". It may not
    # be widened into "the whole charge is unlawful" unless the KB says so itself.
    Guard(
        "conclusion-widened",
        re.compile(
            r"\b(?:parking\s+charge(?:\s+notice)?|PCN|charge|notice|demand)\s+"
            r"(?:is|was|has\s+been|must\s+be)\s+(?:therefore\s+|accordingly\s+|also\s+)?"
            r"(unlawful|invalid|void|unenforceable|illegal|a\s+penalty)\b"
            r"|\b(?:unlawfully|illegally)\s+(?:issued|demanded|charged)\b", I),
        "Widens the approved legal conclusion into a finding the approved wording "
        "does not make",
        wording=True, term_group=1,
    ),
    # Motive and state of mind are never in the case state.
    Guard(
        "intention-attributed",
        re.compile(
            r"\b(deliberately|intentionally|knowingly|wilfully|willfully|on\s+purpose|"
            r"in\s+bad\s+faith|dishonestly|cynically)\b", I),
        "Attributes an intention or motive that no source establishes",
        wording=True, term_group=1,
    ),
]


def register(guard: Guard) -> None:
    """Add a claim-kind guard. Ids are unique; re-registering an id replaces it."""
    for i, g in enumerate(GUARDS):
        if g.id == guard.id:
            GUARDS[i] = guard
            return
    GUARDS.append(guard)


def _approved_wording(pack) -> str:
    """The pack's approved legal wording, lower-cased."""
    parts = []
    for c in getattr(pack, "context_chunks", None) or []:
        if isinstance(c, dict) and c.get("kind") in (None, "module", "block"):
            parts.append(str(c.get("text") or ""))
    return " ".join(parts).lower()


def _truthy(v) -> bool:
    return v not in (None, "", False, 0, "UNKNOWN", "NONE", [], {})


def violations(text: str, pack) -> list[Guard]:
    """Guards this sentence breaks given the pack's verified facts and approved wording."""
    facts = getattr(pack, "verified_facts", None) or {}
    out: list[Guard] = []
    wording: Optional[str] = None
    for g in GUARDS:
        for m in g.pattern.finditer(text or ""):
            if _negated(text, m.start()):
                continue
            if any(_truthy(facts.get(f)) for f in g.facts):
                break
            if g.wording:
                if wording is None:
                    wording = _approved_wording(pack)
                term = m.group(g.term_group) if g.term_group else m.group(0)
                if _stem(term) and _stem(term) in wording:
                    break
            out.append(g)
            break
    return out


def describe(guards: Iterable[Guard]) -> str:
    return "; ".join(g.message for g in guards)
