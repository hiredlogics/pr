"""Derivation layer: notice facts -> KB gate facts (P8 fact-to-ground matching).

Position: extraction -> recovery (documents) -> **derivation** -> calculations
-> gates -> questions.

Why it exists
-------------
Extraction produces raw facts (parking_location, alleged_breach, entry/exit
times). KB gates are written in judgement facts (customer_only_site,
permitted_period_ended, short_presence_before_acceptance ...). Without a layer
between them the only route to a gate fact was a customer answer, so on a thin
account almost every module stayed UNKNOWN and only the PoFA timing ground
(whose input is a calculation) could ever fire.

Rules
-----
D-01 Derivation is deterministic: regex over document facts, arithmetic on
     derived durations, thresholds from the resolved Code version.
D-02 A derived value never overwrites a fact the customer confirmed, corrected
     or answered, nor a usable document value.
D-03 No match -> nothing written. Unknown stays unknown, so the question bank
     can still ask; a derivation never asserts a negative it was not shown.
D-04 Every write is DERIVED with a CALCULATION source naming the rule, so the
     case trace shows which rule produced a gate fact.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from ..legal import code_versions
from ..models import CaseFile, Fact, FactSource, FactStatus, SourceKind

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "derivation_rules.yaml"

# Statuses that represent the customer or a document speaking; derivation never
# replaces them (D-02).
_PROTECTED = (FactStatus.CONFIRMED, FactStatus.CORRECTED, FactStatus.ANSWERED,
              FactStatus.EXTRACTED)

# Facts this layer may write. The fact-producer registry lists the same names
# with source DERIVED; tests/test_fact_producers.py keeps the two in step.
DERIVED_FACTS = (
    "alleged_breach_type", "permitted_period_ended", "short_presence_before_acceptance",
    "customer_only_site", "dropoff_site", "hospital_site", "relevant_land",
)


@lru_cache(maxsize=1)
def load_rules(path: str = str(RULES_PATH)) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    compiled = {
        "breach_types": [
            (row["type"], [re.compile(p, re.I) for p in row.get("patterns") or []])
            for row in data.get("breach_types") or []
        ],
        "permitted_period_ended": dict(data.get("permitted_period_ended") or {}),
        "site_types": {
            name: [re.compile(p, re.I) for p in (row or {}).get("patterns") or []]
            for name, row in (data.get("site_types") or {}).items()
        },
        "byelaw_sites": [re.compile(p, re.I)
                         for p in (data.get("byelaw_sites") or {}).get("patterns") or []],
    }
    return compiled


def classify_breach(text: Any, rules: Optional[dict] = None) -> Optional[str]:
    """alleged_breach wording -> breach class, or None when nothing matches."""
    if not text:
        return None
    rules = rules or load_rules()
    for kind, patterns in rules["breach_types"]:
        if any(p.search(str(text)) for p in patterns):
            return kind
    return None


def _matches(patterns, *texts: Any) -> bool:
    blob = " ".join(str(t) for t in texts if t)
    return bool(blob) and any(p.search(blob) for p in patterns)


def _write(case: CaseFile, name: str, value: Any, rule: str, written: dict) -> None:
    existing = case.facts.get(name)
    if existing is not None and existing.usable and existing.value not in (None, "", []):
        if existing.status in _PROTECTED or existing.source.kind != SourceKind.CALCULATION:
            return  # D-02
        if existing.value == value:
            return
    applied = case.put(Fact(
        f"F-{name}", name, value, FactStatus.DERIVED,
        FactSource(SourceKind.CALCULATION, f"derive:{rule}"),
    ))
    if applied is not False:
        written[name] = {"value": value, "rule": rule}


def consideration_minutes(case: CaseFile) -> Optional[int]:
    """Consideration period from the resolved Code version (never hard-coded)."""
    version, _status = code_versions.resolve(
        case.get("parking_event_date"), case.get("operator_ata"),
        case.get("operator_transitioned"))
    if version is None:
        return None
    provisions = getattr(version, "provisions", None) or {}
    value = provisions.get("consideration_period_min_minutes")
    return int(value) if value is not None else None


def derive(case: CaseFile, rules: Optional[dict] = None) -> dict[str, dict]:
    """Apply every derivation rule; return {fact: {value, rule}} for what was written."""
    rules = rules or load_rules()
    written: dict[str, dict] = {}
    location = case.get("parking_location")
    breach = case.get("alleged_breach")

    # D-breach: classify the contravention wording.
    kind = classify_breach(breach, rules)
    if kind:
        _write(case, "alleged_breach_type", kind, "breach_type", written)
    kind = case.get("alleged_breach_type") or kind

    # D-period: a breach class that settles whether a permitted period existed.
    if kind in rules["permitted_period_ended"]:
        _write(case, "permitted_period_ended", bool(rules["permitted_period_ended"][kind]),
               f"permitted_period:{kind}", written)

    # D-short: total ANPR presence within the Code consideration period. Only
    # the positive is derived; a longer stay does not prove time was not spent
    # considering terms (D-03).
    mins = case.get("total_recorded_duration_min")
    limit = consideration_minutes(case)
    if isinstance(mins, (int, float)) and limit is not None and 0 <= mins <= limit:
        _write(case, "short_presence_before_acceptance", True,
               f"duration<={limit}min", written)

    # D-site: site character from the location wording (and breach wording,
    # which for drop-off zones names the zone).
    for name, patterns in rules["site_types"].items():
        if _matches(patterns, location, breach if name == "dropoff_site" else None):
            _write(case, name, True, f"site:{name}", written)

    # D-land: land under statutory control is not relevant land (PoFA Sch 4).
    if _matches(rules["byelaw_sites"], location):
        _write(case, "relevant_land", False, "byelaw_site", written)

    if written:
        case.audit.append({"event": "derivation", "derived": written})
    return written
