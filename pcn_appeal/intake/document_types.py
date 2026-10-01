"""Canonical document classifications.

One vocabulary for every service. The classifier labels each uploaded document
with exactly one of these, before any service-specific reading happens, so no
service's assumptions leak into deciding which service a case belongs to.

Two kinds of label:

  * ROUTING types - the document that says what stage a charge is at and so
    which service may act (a parking notice, a debt letter, an Order for
    Recovery ...). The router picks the route from these.
  * SUPPORTING_EVIDENCE - a receipt, a photo, a lease. It never decides a
    route; it attaches to whichever route the routing documents select.

UNKNOWN is a routing type on purpose: a case whose only document could not be
placed goes to review, never to a guessed service.
"""
from __future__ import annotations

PRIVATE_PARKING_NOTICE = "PRIVATE_PARKING_NOTICE"
PRIVATE_PARKING_APPEAL_RESPONSE = "PRIVATE_PARKING_APPEAL_RESPONSE"
DEBT_RECOVERY = "DEBT_RECOVERY"
COUNCIL_PCN = "COUNCIL_PCN"
CHARGE_CERTIFICATE = "CHARGE_CERTIFICATE"
ORDER_FOR_RECOVERY = "ORDER_FOR_RECOVERY"
LETTER_BEFORE_CLAIM = "LETTER_BEFORE_CLAIM"
COUNTY_CLAIM = "COUNTY_CLAIM"
BAILIFF_ENFORCEMENT = "BAILIFF_ENFORCEMENT"
CCJ = "CCJ"
UNKNOWN = "UNKNOWN"
SUPPORTING_EVIDENCE = "SUPPORTING_EVIDENCE"

# Most advanced stage first. When several routing documents are uploaded
# together the furthest-progressed one decides: a claim form beside the original
# notice means the appeal stage has passed, however appealable the notice looks.
ROUTING_PRECEDENCE: tuple[str, ...] = (
    CCJ,
    BAILIFF_ENFORCEMENT,
    COUNTY_CLAIM,
    LETTER_BEFORE_CLAIM,
    ORDER_FOR_RECOVERY,
    CHARGE_CERTIFICATE,
    COUNCIL_PCN,
    DEBT_RECOVERY,
    PRIVATE_PARKING_APPEAL_RESPONSE,
    PRIVATE_PARKING_NOTICE,
    UNKNOWN,
)
ROUTING_TYPES = frozenset(ROUTING_PRECEDENCE)
ALL_TYPES = ROUTING_TYPES | {SUPPORTING_EVIDENCE}

# The regime the underlying charge belongs to. Separate from the document type:
# a county claim or a CCJ can follow either a private parking charge or
# something else, and the claims route will need to know which.
PRIVATE_PARKING = "PRIVATE_PARKING"
COUNCIL_STATUTORY = "COUNCIL_STATUTORY"
OTHER = "OTHER"
FAMILIES = frozenset({PRIVATE_PARKING, COUNCIL_STATUTORY, OTHER, UNKNOWN})

# Types whose regime is fixed by what they are. A classifier that labels one of
# these with a different family is contradicting itself, which is ambiguity -
# the case goes to review rather than to either reading.
FIXED_FAMILY: dict[str, str] = {
    PRIVATE_PARKING_NOTICE: PRIVATE_PARKING,
    PRIVATE_PARKING_APPEAL_RESPONSE: PRIVATE_PARKING,
    DEBT_RECOVERY: PRIVATE_PARKING,
    COUNCIL_PCN: COUNCIL_STATUTORY,
    CHARGE_CERTIFICATE: COUNCIL_STATUTORY,
    ORDER_FOR_RECOVERY: COUNCIL_STATUTORY,
}

# Stages each type may report, default first. A stage outside the list falls
# back to the default and is noted in the audit, so a free-text stage can never
# open or close a route by itself.
STAGES: dict[str, tuple[str, ...]] = {
    PRIVATE_PARKING_NOTICE: ("INITIAL_NOTICE", "REMINDER", "APPEAL_WINDOW_CLOSED"),
    PRIVATE_PARKING_APPEAL_RESPONSE: ("OPERATOR_RESPONSE",),
    DEBT_RECOVERY: ("DEBT_RECOVERY",),
    COUNCIL_PCN: ("PENALTY_CHARGE_NOTICE", "NOTICE_TO_OWNER"),
    CHARGE_CERTIFICATE: ("CHARGE_CERTIFICATE",),
    ORDER_FOR_RECOVERY: ("ORDER_FOR_RECOVERY",),
    LETTER_BEFORE_CLAIM: ("PRE_ACTION",),
    COUNTY_CLAIM: ("CLAIM_ISSUED",),
    BAILIFF_ENFORCEMENT: ("ENFORCEMENT",),
    CCJ: ("JUDGMENT",),
    UNKNOWN: ("UNKNOWN",),
    SUPPORTING_EVIDENCE: ("NOT_APPLICABLE",),
}

# Within one type, a later stage outranks an earlier one (same reason as
# ROUTING_PRECEDENCE). Index in STAGES is the order.


def stage_rank(document_type: str, stage: str) -> int:
    stages = STAGES.get(document_type, ())
    return stages.index(stage) if stage in stages else 0


def default_stage(document_type: str) -> str:
    return STAGES.get(document_type, ("UNKNOWN",))[0]


# The private-parking extractor has its own, older label set (prompt
# `extraction`). It is still produced inside the private-parking service, and
# test doubles that only script an extraction response use it to stand in for
# the classifier. This is the one mapping between the two vocabularies.
LEGACY_LABELS: dict[str, tuple[str, str]] = {
    # label -> (canonical type, stage)
    "PCN": (PRIVATE_PARKING_NOTICE, "INITIAL_NOTICE"),
    "NTK": (PRIVATE_PARKING_NOTICE, "INITIAL_NOTICE"),
    "NTD": (PRIVATE_PARKING_NOTICE, "INITIAL_NOTICE"),
    "OUT_OF_STAGE": (PRIVATE_PARKING_NOTICE, "APPEAL_WINDOW_CLOSED"),
    "DEBT_RECOVERY": (DEBT_RECOVERY, "DEBT_RECOVERY"),
    "COUNCIL_PCN": (COUNCIL_PCN, "PENALTY_CHARGE_NOTICE"),
    "COURT_CLAIM": (COUNTY_CLAIM, "CLAIM_ISSUED"),
    "OTHER": (UNKNOWN, "UNKNOWN"),
}


def from_legacy_label(label: str) -> tuple[str, str]:
    """Canonical (type, stage) for a private-parking extraction label. Every
    label not listed is a supporting-evidence kind (RECEIPT, LEASE, PHOTO ...)."""
    if label in LEGACY_LABELS:
        return LEGACY_LABELS[label]
    return SUPPORTING_EVIDENCE, "NOT_APPLICABLE"
