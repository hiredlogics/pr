"""The interface every service engine implements, and the redirect engine used
by routes that do not have a service yet.

A route that is not built says so: `RedirectService` returns the customer-safe
stop for its route and raises ServiceNotAvailable for every step it does not
have. Nothing here pretends to analyse or draft for a service that does not exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..models import CaseFile
from ..rules.scope import ScopeStop


class ServiceNotAvailable(RuntimeError):
    """A step was called on a route whose service has not been built."""


@dataclass(frozen=True)
class CompletenessPolicy:
    """What a route needs uploaded before its engine may read anything."""
    name: str
    description: str
    # (ok, reason). None means the policy has no check of its own yet.
    check: Optional[Callable[[CaseFile], tuple[bool, str]]] = None


@dataclass(frozen=True)
class DocumentCheck:
    ok: bool
    policy: str
    reason: str
    enforced: bool


NO_REQUIREMENT = CompletenessPolicy(
    "NONE", "No upload requirement: this route only explains where the case belongs.")


class ServiceEngine:
    """One service's engine. `live` is False until the service can take a case
    past document validation."""

    route: str = ""
    live: bool = False
    completeness: CompletenessPolicy = NO_REQUIREMENT

    def validate_documents(self, case: CaseFile) -> DocumentCheck:
        """Route-specific completeness. Enforced only by a live service: asking
        for more pages before telling a customer the service does not exist yet
        would only delay the answer."""
        policy = self.completeness
        if not self.live or policy.check is None:
            return DocumentCheck(True, policy.name, "not enforced", enforced=False)
        ok, reason = policy.check(case)
        return DocumentCheck(ok, policy.name, reason, enforced=True)

    def outcome(self, case: CaseFile) -> Optional[ScopeStop]:
        """The stop or redirect for this case, or None to proceed into the engine."""
        raise NotImplementedError

    # Engine steps. A service implements the ones it has.
    def extract_service_facts(self, case: CaseFile):
        raise ServiceNotAvailable(f"{self.route}: extraction is not available")

    def analyse(self, case: CaseFile):
        raise ServiceNotAvailable(f"{self.route}: analysis is not available")

    def get_questions(self, case: CaseFile):
        raise ServiceNotAvailable(f"{self.route}: questions are not available")

    def generate(self, case: CaseFile):
        raise ServiceNotAvailable(f"{self.route}: output generation is not available")

    def validate_output(self, case: CaseFile, output):
        raise ServiceNotAvailable(f"{self.route}: output validation is not available")


class RedirectService(ServiceEngine):
    """A route with no engine yet: it names where the case belongs and stops.

    `completeness` is declared now so the policy the service will enforce is on
    record, but it is not enforced while `live` is False.
    """

    live = False

    def __init__(self, route: str, stop: ScopeStop,
                 completeness: CompletenessPolicy = NO_REQUIREMENT):
        self.route = route
        self.stop = stop
        self.completeness = completeness

    def outcome(self, case: CaseFile) -> ScopeStop:
        return self.stop
