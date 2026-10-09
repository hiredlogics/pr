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
PRODUCERS_PATH = Path(__file__).resolve().parent.parent / "data" / "fact_producers.yaml"

# Statuses that represent the customer or a document speaking; derivation never
# replaces them (D-02).
_PROTECTED = (FactStatus.CONFIRMED, FactStatus.CORRECTED, FactStatus.ANSWERED,
              FactStatus.EXTRACTED)

# Facts this layer may write. The fact-producer registry lists the same names
# with source DERIVED; tests/test_fact_producers.py keeps the two in step.
DERIVED_FACTS = (
    "alleged_breach_type", "permitted_period_ended", "short_presence_before_acceptance",
    "customer_only_site", "dropoff_site", "hospital_site", "relevant_land",
    "statutory_control_site", "notice_stage", "original_notice_issue_date",
    "appeal_period_expired", "appeal_period_status", "retail_site",
    "statutory_control_possible", "overstay_min", "within_grace_period",
)

# Days the operator's appeal period runs from the notice's issue date (the
# period printed on the notices tested). Admin-editable in derivation_rules.yaml.
DEFAULT_APPEAL_PERIOD_DAYS = 28

# Stages at which the uploaded letter is not the first Notice to Keeper, so its
# own date must never be used for the Schedule 4 timing calculation.
LATER_STAGES = ("REMINDER", "DRIVER_LETTER")


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
        "notice_stages": [
            (stage, [re.compile(p, re.I) for p in (row or {}).get("patterns") or []])
            for stage, row in (data.get("notice_stages") or {}).items()
        ],
        "original_notice_date": [re.compile(p, re.I) for p in
                                 (data.get("original_notice_date") or {}).get("patterns") or []],
        "appeal_period_days": int((data.get("appeal_period") or {}).get("days")
                                  or DEFAULT_APPEAL_PERIOD_DAYS),
        "points_at": {k: list(v or []) for k, v in (data.get("points_at") or {}).items()},
        "permitted_period": {k: re.compile(v, re.I)
                             for k, v in (data.get("permitted_period") or {}).items()},
        "statutory_land_indicators": {
            kind: [re.compile(p, re.I) for p in pats or []]
            for kind, pats in (data.get("statutory_land_indicators") or {}).items()},
        "statutory_control_sites": [
            {"id": row["id"], "name": row.get("name", row["id"]),
             "status": str(row.get("status", "PENDING")).upper(),
             "jurisdiction": row.get("jurisdiction"),
             "all_of": [[re.compile(p, re.I) for p in group]
                        for group in row.get("all_of") or []]}
            for row in data.get("statutory_control_sites") or []
        ],
    }
    return compiled


@lru_cache(maxsize=1)
def establishable_by(path: str = str(PRODUCERS_PATH)) -> dict[str, tuple[str, ...]]:
    """DERIVED gate fact -> the QUESTIONs whose answers can establish it instead.

    A calculation cannot always settle a derived fact: `within_grace_period`
    needs a permitted period AND a duration on the notice, and many notices
    carry neither. The question named here is then the only thing that can
    decide it, so the gap-finder is allowed to ask for it - but only while the
    derived fact is still unknown. Once derivation settles the fact from the
    operator's own figures, the answer could no longer change the appeal, and
    the client rule is to not ask.
    """
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {fact: tuple(qs or ())
            for fact, qs in (data.get("establishable_by") or {}).items()}


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


def match_statutory_site(location: Any, breach: Any,
                         rules: Optional[dict] = None) -> Optional[dict]:
    """First listed statutory-control location whose every pattern group
    matches the notice's location + contravention text; None when none does."""
    rules = rules or load_rules()
    blob = " ".join(str(t) for t in (location, breach) if t)
    if not blob:
        return None
    for site in rules["statutory_control_sites"]:
        groups = site["all_of"]
        if groups and all(any(p.search(blob) for p in group) for group in groups):
            return site
    return None


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
        # Client 2026-10-08: no blanket "drop-off means no permitted period";
        # the allegation decides. When the operator does not allege an overstay
        # and its own times put the whole stay inside the consideration period,
        # no permitted period can have ended before the vehicle left.
        if kind and kind != "OVERSTAY":
            _write(case, "permitted_period_ended", False,
                   f"no_overstay_alleged_and_duration<={limit}min", written)

    # D-grace (client 2026-10-08): the actual overstay from the operator's own
    # times against the permitted period printed on the notice, compared with
    # the grace period of the resolved Code version.
    over = overstay_minutes(case, rules)
    if over is not None:
        _write(case, "overstay_min", over, "overstay_calc", written)
        if over <= 0:
            # The operator's own times show the vehicle left at or before the
            # permitted period ended, so no permitted period ended before
            # departure - whatever the allegation wording says. D-period sets
            # this flag from the contravention text alone; leaving both live
            # would assert a duration contradiction the validator forbids
            # (VAL-CONFLICT), and would gate grace/overstay grounds on a
            # premise the operator's own figures disprove.
            _write(case, "permitted_period_ended", False,
                   f"operator_times_show_no_overstay:{over}min", written)
        grace = grace_minutes(case)
        if grace is not None and over > 0:
            _write(case, "within_grace_period", over <= grace,
                   f"overstay<={grace}min", written)
    else:
        # The notice does not give both a permitted period and a duration, so the
        # overstay cannot be calculated from the operator's own figures. The
        # customer's own account of the delay then genuinely decides the grace
        # question, which is why exit_delay_min is worth asking HERE - and only
        # here. Once the notice settles it, asking adds nothing.
        delay = case.get("exit_delay_min")
        grace = grace_minutes(case)
        if isinstance(delay, (int, float)) and grace is not None:
            _write(case, "within_grace_period", 0 < float(delay) <= grace,
                   f"customer_exit_delay<={grace}min", written)

    # D-site: site character from the location wording (and breach wording,
    # which for drop-off zones names the zone).
    for name, patterns in rules["site_types"].items():
        if _matches(patterns, location, breach if name == "dropoff_site" else None):
            _write(case, name, True, f"site:{name}", written)

    # D-land: land under statutory control is not relevant land (PoFA Sch 4
    # para 3). Only a location the client has CONFIRMED is covered for parking
    # or waiting changes anything; a PENDING match is recorded for review.
    site = match_statutory_site(location, breach, rules)
    if site is not None:
        if site["status"] == "CONFIRMED":
            _write(case, "relevant_land", False, f"statutory_control:{site['id']}", written)
            _write(case, "statutory_control_site", site["id"],
                   f"statutory_control:{site['id']}", written)
        else:
            case.audit.append({"event": "statutory_control_site_pending",
                               "site": site["id"], "name": site["name"],
                               "note": "location not yet confirmed; relevant_land left unknown"})
    # Client 2026-10-08: the list is an aid, not a whitelist. Land the notice
    # shows is at an airport, and that no confirmed entry covers, is flagged
    # for the strict-proof point (KB-POFA-08) whether or not it is listed.
    if case.get("relevant_land") is not False:
        kind = statutory_land_kind(case, rules)
        if kind == "AIRPORT":
            _write(case, "statutory_control_possible", True, "statutory_land:AIRPORT", written)

    # D-stage: what stage the uploaded letter is at, and the original notice's
    # date when it can be linked (client instruction 2026-10-07).
    stage, original = notice_stage(case, rules)
    if stage:
        _write(case, "notice_stage", stage, "notice_stage", written)
    if original is not None:
        _write(case, "original_notice_issue_date", original, "original_notice_link", written)

    # D-late: the normal appeal period may have expired (client instruction
    # 2026-10-07: flag it and adapt the wording; never stop the appeal).
    status, end = appeal_period(case, rules)
    if status is not None:
        _write(case, "appeal_period_status", status, f"appeal_period_end:{end}", written)
        _write(case, "appeal_period_expired", status != "IN_TIME", f"appeal_period_end:{end}",
               written)

    if written:
        case.audit.append({"event": "derivation", "derived": written})
    return written


def today():
    """The date the appeal is being prepared. Patched in tests."""
    from datetime import date
    return date.today()


def _as_date(value):
    from .extraction import parse_uk_date
    if value is None or hasattr(value, "year"):
        return value
    return parse_uk_date(str(value))


def appeal_period(case: CaseFile, rules: Optional[dict] = None):
    """(status, period_end). status: IN_TIME / EXPIRED / MAY_HAVE_EXPIRED, or
    (None, None) when there is nothing to count from.

    Client 2026-10-08: the deadline printed on the notice first; else the
    period from the FIRST notice's date. A reminder or driver letter never
    starts a fresh period from its own date: with no original date it is
    MAY_HAVE_EXPIRED."""
    from datetime import timedelta
    rules = rules or load_rules()
    printed = _as_date(case.get("appeal_deadline_date"))
    later = case.get("notice_stage") in LATER_STAGES
    if hasattr(printed, "year") and not later:
        return ("EXPIRED" if today() > printed else "IN_TIME"), printed
    issued = _as_date(case.get("original_notice_issue_date")) if later \
        else _as_date(case.get("notice_issue_date"))
    if not hasattr(issued, "year"):
        return ("MAY_HAVE_EXPIRED", None) if later else (None, None)
    end = issued + timedelta(days=int(rules.get("appeal_period_days") or DEFAULT_APPEAL_PERIOD_DAYS))
    return ("EXPIRED" if today() > end else "IN_TIME"), end


def appeal_period_status(case: CaseFile, rules: Optional[dict] = None):
    """(expired, period_end); kept for callers of the 2026-10-07 version."""
    status, end = appeal_period(case, rules)
    return (None if status is None else status != "IN_TIME"), end


def grace_minutes(case: CaseFile) -> Optional[int]:
    """End-of-parking grace period from the resolved Code version."""
    version, _status = code_versions.resolve(
        case.get("parking_event_date"), case.get("operator_ata"),
        case.get("operator_transitioned"))
    value = (getattr(version, "provisions", None) or {}).get("grace_period_min_minutes") \
        if version is not None else None
    return int(value) if value is not None else None


def permitted_minutes(text: Any, rules: Optional[dict] = None) -> Optional[int]:
    """"Max stay 3 hours" -> 180; "1 hour 30 minutes" -> 90; None if unreadable."""
    if text in (None, ""):
        return None
    if isinstance(text, (int, float)):
        return int(text)
    rules = rules or load_rules()
    pats = rules.get("permitted_period") or {}
    total, found = 0.0, False
    if "hours" in pats:
        m = pats["hours"].search(str(text))
        if m:
            total += float(m.group(1)) * 60
            found = True
    if "minutes" in pats:
        m = pats["minutes"].search(str(text))
        if m:
            total += int(m.group(1))
            found = True
    return int(round(total)) if found and total > 0 else None


def overstay_minutes(case: CaseFile, rules: Optional[dict] = None) -> Optional[int]:
    """Minutes the operator's own times run past the permitted period printed
    on the notice. Paid-until time first (exit - paid_until), else a printed
    period (duration - period). None when the notice gives neither."""
    from .extraction import _hhmm
    exit_t, paid = _hhmm(case.get("exit_time")), _hhmm(case.get("paid_until_time"))
    if exit_t and paid:
        eh, em = map(int, exit_t.split(":"))
        ph, pm = map(int, paid.split(":"))
        diff = (eh * 60 + em) - (ph * 60 + pm)
        if diff < -12 * 60:
            diff += 24 * 60                  # paid until before midnight, left after
        return diff
    period = permitted_minutes(case.get("permitted_period"), rules)
    duration = case.get("total_recorded_duration_min")
    if period is None or not isinstance(duration, (int, float)):
        return None
    return int(duration) - period


def statutory_land_kind(case: CaseFile, rules: Optional[dict] = None) -> Optional[str]:
    """What statutory-control land the notice shows: the extraction model's
    reading first (it sees the whole notice), the indicator patterns over the
    location and allegation as backup. Railway land is never returned."""
    rules = rules or load_rules()
    read = str(case.get("statutory_land_indicator") or "").upper()
    if read and read not in ("NONE", "RAILWAY", "NULL"):
        return read
    blob = " ".join(str(case.get(n) or "") for n in ("parking_location", "alleged_breach",
                                                      "statutory_land_evidence"))
    for kind, pats in (rules.get("statutory_land_indicators") or {}).items():
        if any(p.search(blob) for p in pats):
            return kind
    return None


def notice_stage(case: CaseFile, rules: Optional[dict] = None):
    """(stage, original_notice_date) for the case's private parking notice.

    Stage comes from the classifier's per-document labels, the latest stage
    winning; text patterns are the backup when no label is later than
    INITIAL_NOTICE. The original notice is linked from an INITIAL_NOTICE upload
    carrying a date (same PCN, or none printed), else from a date the later
    letter itself prints for the earlier notice. Unlinked -> None.
    """
    from .extraction import parse_uk_date
    rules = rules or load_rules()
    order = ("INITIAL_NOTICE", "REMINDER", "DRIVER_LETTER")
    pcn = str(case.get("pcn_number") or "").replace(" ", "").upper()
    labelled, initial_dates = [], []
    for row in (getattr(case, "classifications", None) or {}).values():
        if row.get("document_type") != "PRIVATE_PARKING_NOTICE":
            continue
        st = row.get("stage")
        if st in order:
            labelled.append(st)
        refs = row.get("references") or {}
        same = not refs.get("pcn_number") or not pcn or \
            str(refs["pcn_number"]).replace(" ", "").upper() == pcn
        if st == "INITIAL_NOTICE" and same and row.get("document_date"):
            d = parse_uk_date(row["document_date"])
            if d is not None:
                initial_dates.append(d)
    stage = max(labelled, key=order.index) if labelled else None
    if stage in (None, "INITIAL_NOTICE"):
        text = "\n".join((e.text or "") for e in case.evidence.values())
        for st, patterns in rules["notice_stages"]:
            if any(p.search(text) for p in patterns):
                stage = st
                break
    original = min(initial_dates) if initial_dates and stage in LATER_STAGES else None
    if original is None and stage in LATER_STAGES:
        text = "\n".join((e.text or "") for e in case.evidence.values())
        for p in rules["original_notice_date"]:
            m = p.search(text)
            if m:
                original = parse_uk_date(m.group(1))
                if original is not None:
                    break
    return stage, original


def timing_issue_date(case: CaseFile):
    """The date the Schedule 4 timing check must use.

    The first notice's own date normally. For a reminder or driver letter the
    letter's date is not the Notice to Keeper's, so only a linked original date
    (or one the customer gave) may be used; none -> None, and the calculator
    returns UNRESOLVED instead of a "62 days late" error."""
    from .extraction import parse_uk_date
    if case.get("notice_stage") in LATER_STAGES:
        value = case.get("original_notice_issue_date")
        if value is None:
            return None
        return value if hasattr(value, "year") else parse_uk_date(str(value))
    return case.get("notice_issue_date")
