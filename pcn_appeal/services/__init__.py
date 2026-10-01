"""Service engines, one per route.

    intake (classify -> resolve route) -> engine_for(case.route) -> that engine

Layout: one package per route. Each package will own its own knowledge/,
prompts/, rules/, validators/ and outputs/ as the service is built. Only
private_parking has an engine today; every other route is a RedirectService
that identifies the document and stops - no legal rules are authored for a
route before its specification and content are approved.
"""
from __future__ import annotations

from typing import Optional

from ..models import CaseFile
from ..rules import scope
from ..rules.scope import ScopeStop
from . import (bailiff, ccj_removal, charge_certificate, claims, council_pcn, debt_recovery,
               order_for_recovery, unsupported_review)
from .base import ServiceEngine, ServiceNotAvailable
from .private_parking import PrivateParkingService

_REDIRECTS: dict[str, ServiceEngine] = {
    m.ROUTE: m.SERVICE for m in (debt_recovery, order_for_recovery, charge_certificate,
                                 council_pcn, claims, bailiff, ccj_removal, unsupported_review)
}


def engine_for(route: str, pipeline=None) -> ServiceEngine:
    """The engine for a route. `pipeline` is the AppealPipeline the private
    parking engine delegates to; other routes ignore it."""
    if route == PrivateParkingService.route:
        return PrivateParkingService(pipeline)
    try:
        return _REDIRECTS[route]
    except KeyError:
        raise KeyError(f"no service registered for route {route!r}") from None


def routes() -> tuple[str, ...]:
    return (PrivateParkingService.route, *_REDIRECTS)


def intake_stop(case: CaseFile) -> Optional[ScopeStop]:
    """The stop the intake router's decision implies, or None to proceed.

    Read from the persisted route and stage, so it survives rehydration and is
    re-derived identically on every call.
    """
    if case.route is None:
        return None
    return engine_for(case.route).outcome(case)


def stop_by_code(code: str) -> Optional[ScopeStop]:
    """Customer wording for a stop code, whichever layer issued it."""
    from .private_parking import STAGE_STOPS
    for stop in (*(s.stop for s in _REDIRECTS.values()), *STAGE_STOPS.values()):
        if stop.code == code:
            return stop
    return scope.STOPS.get(code)


__all__ = ["ServiceEngine", "ServiceNotAvailable", "engine_for", "routes", "intake_stop",
           "stop_by_code"]
