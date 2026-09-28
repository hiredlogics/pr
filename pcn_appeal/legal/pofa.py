"""Deterministic PoFA 2012 Schedule 4 checks.

The LLM NEVER decides whether a PoFA defect exists. This module does, and the
result is passed to the drafter as a verified finding code (or nothing).

Design choices (legal team must sign off before go-live):
* Schedule 4 applies to England & Wales only. Scotland / NI -> NOT_APPLICABLE.
* Land under statutory control (byelaws: many airports, railway stations,
  ports) is not "relevant land" -> NOT_APPLICABLE for keeper liability.
* Para 9 (no Notice to Driver): NTK must be *given* within the relevant period
  of 14 days beginning with the day after the parking period ended. A posted
  notice is presumed given on the 2nd working day after posting.
* Para 8 (Notice to Driver first): NTK given no earlier than the end of 28 days
  and no later than the end of 56 days, each beginning the day after the NtD.
* We do not know the true posting date - only the printed issue date. So near
  a boundary we return UNRESOLVED (-> human review) rather than allege a defect.
* Working days exclude weekends and England & Wales bank holidays. In
  production load https://www.gov.uk/bank-holidays.json on a schedule; the
  table below is a fallback only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

EW_BANK_HOLIDAYS = {
    # 2025
    date(2025, 1, 1), date(2025, 4, 18), date(2025, 4, 21), date(2025, 5, 5),
    date(2025, 5, 26), date(2025, 8, 25), date(2025, 12, 25), date(2025, 12, 26),
    # 2026
    date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4),
    date(2026, 5, 25), date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28),
    # 2027
    date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29), date(2027, 5, 3),
    date(2027, 5, 31), date(2027, 8, 30), date(2027, 12, 27), date(2027, 12, 28),
}

BOUNDARY_MARGIN_DAYS = 1   # admin-configurable safety margin


def is_working_day(d: date, holidays=EW_BANK_HOLIDAYS) -> bool:
    return d.weekday() < 5 and d not in holidays


def add_working_days(d: date, n: int, holidays=EW_BANK_HOLIDAYS) -> date:
    cur = d
    while n > 0:
        cur += timedelta(days=1)
        if is_working_day(cur, holidays):
            n -= 1
    return cur


@dataclass
class PofaResult:
    route: str                  # POSTAL / WINDSCREEN / NOT_APPLICABLE / UNRESOLVED
    findings: list[str]         # e.g. ["POFA_POSTAL_LATE"] - verified defects only
    notes: list[str]
    presumed_delivery: Optional[date] = None
    deadline: Optional[date] = None


def assess(*, jurisdiction: str, relevant_land: Optional[bool], notice_route: str,
           parking_event_date: Optional[date], notice_issue_date: Optional[date],
           ntd_date: Optional[date] = None, actual_delivery_date: Optional[date] = None,
           operator_relies_on_pofa: Optional[bool] = None,
           driver_identified: bool = False) -> PofaResult:
    notes: list[str] = []

    if driver_identified:
        return PofaResult("NOT_APPLICABLE", [], ["Driver formally identified - keeper route not used."])
    if jurisdiction != "ENGLAND_WALES":
        return PofaResult("NOT_APPLICABLE", [], [f"Schedule 4 does not apply in {jurisdiction}."])
    if relevant_land is False:
        return PofaResult("NOT_APPLICABLE", [], ["Land under statutory control - not relevant land."])
    if relevant_land is None:
        notes.append("Relevant-land status unconfirmed.")

    if notice_route == "POSTAL":
        if not (parking_event_date and notice_issue_date):
            return PofaResult("UNRESOLVED", [], notes + ["Missing event or issue date."])
        deadline = parking_event_date + timedelta(days=14)
        if actual_delivery_date:
            delivered, basis = actual_delivery_date, "actual"
        else:
            delivered, basis = add_working_days(notice_issue_date, 2), "presumed (issue date used as posting date)"
        notes.append(f"Deadline {deadline.isoformat()}; delivery {delivered.isoformat()} [{basis}].")
        if delivered <= deadline:
            return PofaResult("POSTAL", [], notes + ["NTK timing appears compliant."], delivered, deadline)
        late_by = (delivered - deadline).days
        if basis.startswith("presumed") and late_by <= BOUNDARY_MARGIN_DAYS:
            return PofaResult("UNRESOLVED", [], notes + [f"Late by {late_by} day(s) on a presumed date - human check."],
                              delivered, deadline)
        return PofaResult("POSTAL", ["POFA_POSTAL_LATE"], notes + [f"Late by {late_by} day(s)."], delivered, deadline)

    if notice_route == "WINDSCREEN":
        if not (ntd_date and notice_issue_date):
            return PofaResult("UNRESOLVED", [], notes + ["Missing Notice to Driver or NTK date."])
        delivered = actual_delivery_date or add_working_days(notice_issue_date, 2)
        earliest, latest = ntd_date + timedelta(days=28), ntd_date + timedelta(days=56)
        notes.append(f"Window {earliest.isoformat()}..{latest.isoformat()}; delivery {delivered.isoformat()}.")
        if earliest <= delivered <= latest:
            return PofaResult("WINDSCREEN", [], notes + ["NTK within window."], delivered, latest)
        off = (earliest - delivered).days if delivered < earliest else (delivered - latest).days
        if actual_delivery_date is None and off <= BOUNDARY_MARGIN_DAYS:
            return PofaResult("UNRESOLVED", [], notes + ["Boundary case on a presumed date - human check."],
                              delivered, latest)
        code = "POFA_NTD_NTK_TOO_EARLY" if delivered < earliest else "POFA_NTD_NTK_LATE"
        return PofaResult("WINDSCREEN", [code], notes, delivered, latest)

    return PofaResult("UNRESOLVED", [], notes + ["Notice route unknown."])
