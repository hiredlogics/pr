"""The route registry: every route name the system compares against, in one enum.

Two kinds of route share the name, and both live here so neither can drift:

  * service routes - which service owns a case (intake/router.py);
  * ground routes  - which argument family a knowledge module belongs to
    (kb_modules.yaml `route`, routes.yaml).

Code compares against `Route.X`, never a string literal. A literal can be
misspelt and still run: `m.route in ("POFA", "LAND")` matched no module, because
the KB names the landowner route LANDOWNER, and it silently changed which
questions were asked. `validate_ground_routes` runs when a knowledge graph is
built, so a KB route the code does not know stops the load instead.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Route(str, Enum):
    # Service routes (intake/router.py).
    PRIVATE_PARKING = "PRIVATE_PARKING"
    DEBT_RECOVERY = "DEBT_RECOVERY"
    ORDER_FOR_RECOVERY = "ORDER_FOR_RECOVERY"
    CHARGE_CERTIFICATE = "CHARGE_CERTIFICATE"
    COUNCIL_PCN = "COUNCIL_PCN"
    CLAIMS = "CLAIMS"
    BAILIFF = "BAILIFF"
    CCJ_REMOVAL = "CCJ_REMOVAL"
    CCJ = "CCJ_REMOVAL"                      # alias: the same service
    UNSUPPORTED_REVIEW = "UNSUPPORTED_REVIEW"

    # Ground routes (kb_modules.yaml, routes.yaml).
    ACTIVITY = "ACTIVITY"
    ANPR = "ANPR"
    ANPR_EVIDENCE = "ANPR_EVIDENCE"
    AUTHORISATION = "AUTHORISATION"
    BAY = "BAY"
    BREAKDOWN = "BREAKDOWN"
    CONSIDERATION = "CONSIDERATION"
    CUSTOMER = "CUSTOMER"
    EQUALITY = "EQUALITY"
    EVIDENCE = "EVIDENCE"
    EV_CHARGING = "EV_CHARGING"
    GRACE = "GRACE"
    HOSPITAL = "HOSPITAL"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    KEYING = "KEYING"
    LANDOWNER = "LANDOWNER"
    PAYMENT = "PAYMENT"
    POFA = "POFA"
    RECORDS = "RECORDS"
    RESIDENTIAL = "RESIDENTIAL"
    SIGNAGE = "SIGNAGE"

    def __str__(self) -> str:                # f"{Route.POFA}" -> "POFA"
        return self.value


SERVICE_ROUTES = frozenset({
    Route.PRIVATE_PARKING, Route.DEBT_RECOVERY, Route.ORDER_FOR_RECOVERY,
    Route.CHARGE_CERTIFICATE, Route.COUNCIL_PCN, Route.CLAIMS, Route.BAILIFF,
    Route.CCJ_REMOVAL, Route.UNSUPPORTED_REVIEW,
})
GROUND_ROUTES = frozenset(r for r in Route if r not in SERVICE_ROUTES)

# Ground routes that are not fact-specific: the Schedule 4 framing and
# landowner authority apply to almost every notice, so their presence never
# means the customer's own facts have opened a ground.
GENERAL_GROUND_ROUTES = frozenset({Route.POFA, Route.LANDOWNER})


class UnknownRouteError(ValueError):
    """A KB or router route that is not in `Route`."""


def validate_ground_routes(module_routes: Iterable[tuple[str, str]],
                           declared_routes: Iterable[str]) -> None:
    """Refuse a knowledge base whose routes the code does not know.

    `module_routes` is (module_id, route) pairs; `declared_routes` is the
    routes.yaml keys. Every one must be a registered ground route, and every
    module's route must be declared."""
    known = {r.value for r in GROUND_ROUTES}
    declared = set(declared_routes)
    problems = [f"routes.yaml declares unregistered route {r!r}" for r in sorted(declared - known)]
    for mid, route in module_routes:
        if route not in known:
            problems.append(f"{mid}: route {route!r} is not a registered ground route")
        elif route not in declared:
            problems.append(f"{mid}: route {route!r} is not declared in routes.yaml")
    if problems:
        raise UnknownRouteError("knowledge base routes do not match the route registry: "
                                + "; ".join(problems))


def validate_service_routes(routes: Iterable[str]) -> None:
    known = {r.value for r in SERVICE_ROUTES}
    unknown = sorted(set(routes) - known)
    missing = sorted(known - set(routes))
    if unknown or missing:
        raise UnknownRouteError(f"service routes do not match the route registry: "
                                f"unregistered {unknown}, unrouted {missing}")
