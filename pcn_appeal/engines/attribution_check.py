"""VAL-ATTRIBUTION: only what the notice prints may be credited to the notice.

Live, a letter said "the notice itself records ... that this overstay falls within the
applicable grace period". The notice records no such thing: within_grace_period is a
fact WE calculated from the notice's times (basis DERIVED). Crediting our calculation to
the notice puts words in the operator's mouth, and a keeper who repeats them is
asserting something the operator can truthfully deny.

The rule is about the fact's BASIS (`DraftContext.fact_basis`):

    DOCUMENT   printed on the notice            "the notice shows / records / states ..."
    DERIVED    calculated or inferred by us     "the calculation shows ..." or stated plainly
    CUSTOMER   the keeper's account             "I understand that ..."

A sentence that credits the notice (the notice / the PCN / the Notice to Keeper, with
shows / records / states / says / confirms / notes) with a claim that is one of this
case's DERIVED facts is refused. The claim is recognised by the fact's own name (the
words of `within_grace_period` give "grace"; `overstay_min` gives "overstay"), so a new
derived fact is covered without adding a phrase. A sentence that already says it is a
calculation ("calculation", "on the times shown", "worked out") is fine.

Two further tests apply to every sentence that credits the notice, whether or not it cites
a derived fact, because a calculation can be smuggled in as a figure or a judgement:

  * every TIME, DURATION or AMOUNT in it must appear in what the notice itself printed
    (its extracted fields). "9 minutes" passes only if the notice prints 9 minutes; the
    entry and exit times pass because the notice prints them.
  * wording no notice prints - a vague size ("only a few minutes beyond") or a conclusion
    ("falls within the grace period") - fails unless the notice's own text contains it.
"""
from __future__ import annotations

import re
from typing import Optional

from ..drafting.context import ACCOUNT_DERIVED_FACTS

CREDITS_THE_NOTICE = re.compile(
    r"\b(?:the|this)\s+(?:(?:parking\s+charge\s+|penalty\s+charge\s+)?notice|PCN|"
    r"notice\s+to\s+(?:keeper|driver))(?:\s+itself)?\s+(?:also\s+|further\s+|clearly\s+)?"
    r"(?:records?|shows?|states?|says|confirms?|notes?|certif(?:y|ies)|establishes)\b", re.I)

#: A sentence that says what it is: a calculation, or stated from the notice's own figures.
SAYS_IT_IS_CALCULATED = re.compile(
    r"\b(?:calculat\w*|worked\s+out|on\s+the\s+(?:times|dates|figures)\s+(?:shown|printed|recorded)|"
    r"from\s+the\s+(?:times|dates|figures)|the\s+difference\s+between)\b", re.I)

# Words of a fact's name that say nothing about its meaning.
_STOP = {"within", "period", "ended", "total", "recorded", "count", "flag", "status",
         "value", "found", "derived", "minutes", "min", "time", "date", "name",
         # the allegation's own vocabulary: notices do print "overstayed by 9 minutes"
         "overstay", "overstayed", "exceeded", "alleged"}


def _tokens(fact_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", fact_name.lower())
            if len(t) >= 5 and t not in _STOP]


def derived_facts(pack) -> set[str]:
    """Names of the pack's facts whose basis is DERIVED (not printed, not the keeper's)."""
    ctx = getattr(pack, "case_context", None) or {}
    reported = set(ctx.get("customer_reported_facts") or []) | ACCOUNT_DERIVED_FACTS
    established = set(ctx.get("document_established_facts") or []) - ACCOUNT_DERIVED_FACTS
    return {n for n in (getattr(pack, "verified_facts", None) or {})
            if n not in reported and n not in established}


def credited_to_notice(sentence_text: str, fact_ids, pack) -> Optional[str]:
    """The derived claim this sentence credits to the notice, or None."""
    text = sentence_text or ""
    if not CREDITS_THE_NOTICE.search(text) or SAYS_IT_IS_CALCULATED.search(text):
        return None
    by_id = {fid: name for name, fid in (getattr(pack, "fact_refs", None) or {}).items()}
    cited = {by_id.get(f) for f in (fact_ids or [])} - {None}
    derived = derived_facts(pack)
    low = text.lower()
    for name in sorted(cited & derived):
        tokens = _tokens(name)
        if tokens and all(t in low for t in tokens):
            return " ".join(tokens)
    return None


# --------------------------------------------------------------- what the notice itself prints
_NOTICE_FIELDS = ("alleged_breach", "permitted_period", "paid_until_time", "entry_time", "exit_time",
                  "observation_time", "event_time", "charge_amount", "reduced_amount")

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
                 "thirty": 30, "forty": 40, "sixty": 60}
_UNITS = {"minute": "min", "minutes": "min", "min": "min", "mins": "min",
          "hour": "hr", "hours": "hr", "hr": "hr", "hrs": "hr", "day": "day", "days": "day"}

_TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_DURATION = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")[\s-]*(minutes?|mins?|hours?|hrs?|days?)\b", re.I)
_MONEY = re.compile(r"[£$]\s?(\d[\d,]*(?:\.\d{2})?)")

#: A size or a verdict that no notice prints: "only a few minutes beyond", "falls within the grace period".
VAGUE_OR_CONCLUDED = re.compile(
    r"\b(?:a\s+few|a\s+couple\s+of|several|only\s+(?:a\s+)?(?:few|small|slight|brief|short|marginal)|"
    r"a\s+short|brief|marginal|slight)\b[^.;]{0,30}\b(?:minutes?|time|excess|period|duration|delay|overstay)\b"
    r"|\b(?:fall|falls|fell|falling|is|was|are|were|lies|lay)\s+within\b[^.;]{0,60}\bgrace\b"
    r"|\bgrace\s+(?:period|allowance|time)\b", re.I)


def notice_corpus(pack) -> str:
    """What the notice itself printed, as lower-case text: the extracted fields that have
    DOCUMENT basis, plus the notice's own times and amounts. Empty when the pack carries
    no such facts (then nothing here can be verified and the check stays silent)."""
    ctx = getattr(pack, "case_context", None) or {}
    facts = getattr(pack, "verified_facts", None) or {}
    names = (set(ctx.get("document_established_facts") or []) - ACCOUNT_DERIVED_FACTS) | set(_NOTICE_FIELDS)
    return " ".join(str(facts[n]) for n in sorted(names) if n in facts and facts[n] not in (None, "")).lower()


def _quantities(text: str) -> set[str]:
    """The times, durations and amounts a sentence states, in a comparable form."""
    out: set[str] = set()
    for m in _TIME.finditer(text):
        out.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    for m in _DURATION.finditer(text):
        n = m.group(1).lower()
        n = str(_NUMBER_WORDS.get(n, n))
        out.add(f"{float(n):g} {_UNITS[m.group(2).lower()]}")
    for m in _MONEY.finditer(text):
        out.add("£" + m.group(1).replace(",", ""))
    return out


def _quantities_printed(corpus: str) -> set[str]:
    printed = _quantities(corpus)
    # money is printed as "GBP 85.00" or "85.00"; accept the bare figure
    for m in re.finditer(r"(?<![\d.])(\d[\d,]*\.\d{2})(?![\d])", corpus):
        printed.add("£" + m.group(1).replace(",", ""))
    return printed


def unprinted_claim(sentence_text: str, pack) -> Optional[str]:
    """What this notice-crediting sentence says that the notice does not contain, or None."""
    text = sentence_text or ""
    if not CREDITS_THE_NOTICE.search(text):
        return None
    corpus = notice_corpus(pack)
    if not corpus:
        return None
    printed = _quantities_printed(corpus)
    missing = sorted(q for q in _quantities(text) if q not in printed)
    if missing:
        return "the figure " + ", ".join(missing)
    m = VAGUE_OR_CONCLUDED.search(text)
    if m and m.group(0).lower() not in corpus and not (
            "grace" in m.group(0).lower() and "grace" in corpus):
        return f"'{m.group(0).strip()}'"
    return None
