"""External driver-disclosure status — separate from narrative and identity.

Product rule
------------
The fact \"operator has received the driver's name and a serviceable address\"
is not inferred from first-person wording, names on the notice, or an unchecked
control. Only an explicit confirmation may set CONFIRMED_YES.

States: CONFIRMED_YES | CONFIRMED_NO | UNKNOWN

driver_status remains the legacy gate used by PoFA timing:
  CONFIRMED_YES -> FORMALLY_IDENTIFIED
  anything else -> UNIDENTIFIED (keeper route stays open)
"""
from __future__ import annotations

from typing import Any, Optional

from .models import CaseFile, DriverStatus, Fact, FactSource, FactStatus, SourceKind

DISCLOSURE_YES = "CONFIRMED_YES"
DISCLOSURE_NO = "CONFIRMED_NO"
DISCLOSURE_UNKNOWN = "UNKNOWN"
DISCLOSURE_FACT = "driver_disclosure_to_operator"

_YES = frozenset({"1", "true", "yes", "on", "confirmed_yes"})
_NO = frozenset({"0", "false", "no", "off", "confirmed_no"})
_UNKNOWN = frozenset({"", "null", "none", "unknown", "unset", "n/a", "na"})


def parse_disclosure(value: Any) -> str:
    """Strict parse. Never uses generic truthiness (non-empty string != yes)."""
    if value is None:
        return DISCLOSURE_UNKNOWN
    if value is True or value == 1:
        return DISCLOSURE_YES
    if value is False or value == 0:
        return DISCLOSURE_NO
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _UNKNOWN:
            return DISCLOSURE_UNKNOWN
        if s in _YES:
            return DISCLOSURE_YES
        if s in _NO:
            return DISCLOSURE_NO
        return DISCLOSURE_UNKNOWN
    return DISCLOSURE_UNKNOWN


def apply_disclosure(
    case: CaseFile,
    raw_value: Any,
    *,
    source: str,
    actor: str = "customer",
) -> str:
    """Record disclosure fact, sync driver_status, append auditable event.

    Returns the normalised status. Does not invent CONFIRMED_YES.
    """
    status = parse_disclosure(raw_value)
    previous = case.driver_status.value
    previous_fact = case.get(DISCLOSURE_FACT)

    case.put(Fact(
        f"F-{DISCLOSURE_FACT}", DISCLOSURE_FACT, status,
        FactStatus.ANSWERED if status != DISCLOSURE_UNKNOWN else FactStatus.DERIVED,
        FactSource(SourceKind.ANSWER, f"disclosure:{source}"),
    ))

    if status == DISCLOSURE_YES:
        case.driver_status = DriverStatus.FORMALLY_IDENTIFIED
    else:
        # UNKNOWN and CONFIRMED_NO both keep the keeper Schedule 4 path open.
        case.driver_status = DriverStatus.UNIDENTIFIED

    case.audit.append({
        "event": "driver_disclosure_set",
        "actor": actor,
        "source": source,
        "raw_submitted": raw_value if not isinstance(raw_value, (bytes, bytearray)) else "<binary>",
        "raw_type": type(raw_value).__name__,
        "disclosure_status": status,
        "previous_disclosure": previous_fact,
        "previous_driver_status": previous,
        "next_driver_status": case.driver_status.value,
    })
    return status


def correct_disclosure(
    case: CaseFile,
    new_status: str,
    *,
    reason: str,
    actor: str = "admin",
) -> str:
    """Auditable correction of an erroneous disclosure record. Not a blanket reset."""
    normalised = parse_disclosure(new_status)
    if new_status in (DISCLOSURE_YES, DISCLOSURE_NO, DISCLOSURE_UNKNOWN):
        normalised = new_status
    previous = case.driver_status.value
    previous_fact = case.get(DISCLOSURE_FACT)
    case.put(Fact(
        f"F-{DISCLOSURE_FACT}", DISCLOSURE_FACT, normalised,
        FactStatus.CORRECTED,
        FactSource(SourceKind.ANSWER, f"disclosure_correction:{actor}"),
    ))
    case.driver_status = (
        DriverStatus.FORMALLY_IDENTIFIED if normalised == DISCLOSURE_YES
        else DriverStatus.UNIDENTIFIED
    )
    case.audit.append({
        "event": "driver_disclosure_corrected",
        "actor": actor,
        "reason": reason,
        "previous_disclosure": previous_fact,
        "previous_driver_status": previous,
        "disclosure_status": normalised,
        "next_driver_status": case.driver_status.value,
    })
    return normalised


def keeper_route_blocked(case: CaseFile) -> bool:
    """True only when external disclosure is explicitly confirmed."""
    status = case.get(DISCLOSURE_FACT)
    if status == DISCLOSURE_YES:
        return True
    if status in (DISCLOSURE_NO, DISCLOSURE_UNKNOWN):
        return False
    # Legacy cases: fall back to driver_status only when no disclosure fact exists.
    return case.driver_status.value == "FORMALLY_IDENTIFIED"
