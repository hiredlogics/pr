"""Service route resolution: exactly one route per case.

The classifier says what each document is; this table says which service owns
a case holding those documents. It is code, not model judgement, and it is the
only place a document type is mapped to a service.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from . import document_types as T
from .classifier import DocumentClassification

# Routes. One per service engine in pcn_appeal/services/.
PRIVATE_PARKING = "PRIVATE_PARKING"
DEBT_RECOVERY = "DEBT_RECOVERY"
ORDER_FOR_RECOVERY = "ORDER_FOR_RECOVERY"
CHARGE_CERTIFICATE = "CHARGE_CERTIFICATE"
COUNCIL_PCN = "COUNCIL_PCN"
CLAIMS = "CLAIMS"
BAILIFF = "BAILIFF"
CCJ_REMOVAL = "CCJ_REMOVAL"
UNSUPPORTED_REVIEW = "UNSUPPORTED_REVIEW"

ROUTES = (PRIVATE_PARKING, DEBT_RECOVERY, ORDER_FOR_RECOVERY, CHARGE_CERTIFICATE,
          COUNCIL_PCN, CLAIMS, BAILIFF, CCJ_REMOVAL, UNSUPPORTED_REVIEW)

# Below this the router does not trust the label it would route on. The case
# goes to review rather than to the service the label names.
MIN_CONFIDENCE = 0.6


class ServiceRouteRegistry:
    """document type -> route. Every routing type has exactly one entry."""

    TABLE: dict[str, str] = {
        T.PRIVATE_PARKING_NOTICE: PRIVATE_PARKING,
        # The operator has already answered an appeal. Still the private parking
        # service's case; that service refuses the stage (services/private_parking).
        T.PRIVATE_PARKING_APPEAL_RESPONSE: PRIVATE_PARKING,
        T.DEBT_RECOVERY: DEBT_RECOVERY,
        T.ORDER_FOR_RECOVERY: ORDER_FOR_RECOVERY,
        T.CHARGE_CERTIFICATE: CHARGE_CERTIFICATE,
        T.COUNCIL_PCN: COUNCIL_PCN,
        T.LETTER_BEFORE_CLAIM: CLAIMS,
        T.COUNTY_CLAIM: CLAIMS,
        T.BAILIFF_ENFORCEMENT: BAILIFF,
        T.CCJ: CCJ_REMOVAL,
        T.UNKNOWN: UNSUPPORTED_REVIEW,
    }

    @classmethod
    def route_for(cls, document_type: str) -> str:
        return cls.TABLE[document_type]


@dataclass
class RouteDecision:
    route: str
    document_type: str
    stage: str
    # The document the route was decided on; None when nothing could be routed.
    decided_by: Optional[str]
    reason: str
    # Every routing document seen, most advanced first, for the audit.
    candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _review(reason: str, document_type: str = T.UNKNOWN, decided_by: Optional[str] = None,
            candidates: Optional[list] = None) -> RouteDecision:
    return RouteDecision(UNSUPPORTED_REVIEW, document_type, T.default_stage(T.UNKNOWN),
                         decided_by, reason, candidates or [])


def resolve(classifications: dict[str, DocumentClassification]) -> RouteDecision:
    """The one route for a case holding these documents.

    Precedence is by stage reached (document_types.ROUTING_PRECEDENCE), so the
    most advanced document decides. The winner must also be trustworthy: low
    confidence, or a family that contradicts its own type, sends the case to
    review instead of to either reading.
    """
    routing = [c for c in classifications.values()
               if c.document_type in T.ROUTING_TYPES and c.document_type != T.UNKNOWN]
    unknown = [c for c in classifications.values() if c.document_type == T.UNKNOWN]

    if not routing:
        why = ("no uploaded document could be identified" if unknown
               else "only supporting evidence was uploaded - no notice or letter to route on")
        return _review(why, candidates=[c.evidence_id for c in unknown])

    order = {t: i for i, t in enumerate(T.ROUTING_PRECEDENCE)}
    routing.sort(key=lambda c: (order[c.document_type], -T.stage_rank(c.document_type, c.stage),
                                c.evidence_id))
    winner = routing[0]
    candidates = [{"evidence_id": c.evidence_id, "document_type": c.document_type,
                   "stage": c.stage, "confidence": c.confidence} for c in routing]

    if winner.confidence < MIN_CONFIDENCE:
        return _review(f"{winner.document_type} at confidence {winner.confidence:.2f}"
                       + (f": {winner.ambiguity_reason}" if winner.ambiguity_reason else ""),
                       winner.document_type, winner.evidence_id, candidates)
    fixed = T.FIXED_FAMILY.get(winner.document_type)
    if fixed and winner.service_family not in (fixed, T.UNKNOWN):
        return _review(f"{winner.document_type} labelled with family {winner.service_family}",
                       winner.document_type, winner.evidence_id, candidates)

    route = ServiceRouteRegistry.route_for(winner.document_type)
    others = {ServiceRouteRegistry.route_for(c.document_type) for c in routing[1:]} - {route}
    reason = f"{winner.document_type} ({winner.stage}) on {winner.evidence_id}"
    if others:
        reason += f"; outranks documents for {', '.join(sorted(others))}"
    return RouteDecision(route, winner.document_type, winner.stage, winner.evidence_id,
                         reason, candidates)
