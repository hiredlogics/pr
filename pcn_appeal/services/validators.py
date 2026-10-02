"""Which validators may run on which route's output.

Validation is split into CORE rules, which apply to anything the platform
produces, and ROUTE rules, which encode one service's law and must never run
on another's output. For example VAL-STAGE bans "county court" in an initial
private-parking appeal; an Order for Recovery form has to name the court.

Today every rule is still executed by the private-parking ValidationEngine
(engines/validation.py), unchanged. This module is the boundary that stops that
engine being applied to any other route: a route with no validators registered
cannot have its output released (fail closed). Lifting the CORE rules into
their own engine is part of the first non-private service (Phase 3).
"""
from __future__ import annotations

from ..engines.validation import ValidationEngine
from ..routes import Route
from .base import ServiceNotAvailable

# Route-agnostic: provenance, identifiers, dates, enclosed evidence, internal
# leakage. VAL-SIGNATURE and VAL-DOCUMENT are required by the statutory-form
# services and have no implementation yet.
CORE_RULES = ("VAL-GROUND", "VAL-FACT", "VAL-EVIDENCE", "VAL-CONFLICT", "VAL-LEAK",
              "VAL-REPEAT", "VAL-SIGNATURE", "VAL-DOCUMENT")

# Private parking law and drafting policy (KB V2 section 17 and later additions).
PRIVATE_PARKING_RULES = ("VAL-DRIVER", "VAL-MODULE", "VAL-POFA", "VAL-CODE", "VAL-RES",
                         "VAL-BREAK", "VAL-EQ", "VAL-ANPR", "VAL-STAGE", "VAL-OBSOLETE",
                         "VAL-EVIDENCE-CONTRADICTION", "VAL-SUBSTANCE", "VAL-CUSTOMER-COPY",
                         "VAL-ACCOUNT-COVERAGE", "VAL-REPEAT-POINT")

ROUTE_RULES: dict[str, tuple[str, ...]] = {
    "PRIVATE_PARKING": PRIVATE_PARKING_RULES,
}


def rules_for(route: str) -> tuple[str, ...]:
    """Every rule that applies to a route's output. CORE plus the route's own;
    never another route's."""
    return CORE_RULES + ROUTE_RULES.get(route, ())


def engine_for(route: str, judge=None) -> ValidationEngine:
    """The validator that may check this route's output. Raises for a route
    whose validators do not exist yet, so nothing it produces can be released."""
    if route == Route.PRIVATE_PARKING:
        return ValidationEngine(judge)
    raise ServiceNotAvailable(f"{route}: no validators registered; output cannot be released")
