"""Who owns a fact: which source may set it, and which may not overwrite it.

  DOCUMENT - what the notice prints. Read by extraction, confirmed or corrected
             by the customer on the confirmation screen. The customer's account
             (free text) never rewrites it.
  CUSTOMER - what happened: the circumstances only the customer knows. Set by
             their answers and their account. A document reading never
             rewrites it.
  EVIDENCE - what a supporting document shows (receipt, photos, lease,
             payment records). Set from that document; the customer's account
             never rewrites it.

Facts not named here are owned by whoever set them first: a customer-sourced
value is treated as CUSTOMER, a document-sourced one as DOCUMENT. The rules
that enforce this live in CaseFile.put (models.py).
"""
from __future__ import annotations

DOCUMENT = "DOCUMENT"
CUSTOMER = "CUSTOMER"
EVIDENCE = "EVIDENCE"

DOCUMENT_OWNED = frozenset({
    "pcn_number", "vrm", "operator_name", "operator_ata",
    "parking_event_date", "notice_issue_date", "ntd_date", "ntk_date",
    "contravention_date", "entry_time", "exit_time",
    "charge_amount", "reduced_amount", "alleged_breach",
    "parking_location", "site_postcode", "keeper_name", "keeper_address",
})

CUSTOMER_OWNED = frozenset({
    # circumstances
    "child_occupant_present", "children_present", "customer_described_event",
    # narrative atomic facts (engines/narrative.py)
    "visited_premises", "purpose_of_visit", "left_site", "returned_same_day",
    "possible_vehicle_departure", "returned_to_vehicle",
    "vehicle_immobilised", "immobilisation_cause", "immobilisation_prevented_departure",
    "recovery_attended",
    # payment attempt (what the customer did, not what a record shows)
    "payment_made", "payment_method", "payment_attempt_failed", "payment_failure_detail",
    "vrm_entered", "vrm_entered_is_known_vehicle",
    # reason for the visit
    "genuine_customer", "visitor_authorised", "authorisation_source", "resident_status",
    "hospital_attendance", "clinical_delay", "loading_activity", "dropoff_activity",
    "ev_charging_session", "multiple_visits", "disability_extra_time",
    "disability_time_relates_to_breach", "blue_badge_displayed", "permit_held",
    "no_parking_took_place", "time_spent_finding_space", "exit_congestion",
    "collection_or_checkin_delay", "short_presence_before_acceptance",
})

EVIDENCE_OWNED = frozenset({
    "payment_recorded_in_document", "receipt_supplied",
    "lease_clauses", "lease_parking_clause_found", "lease_has_regulations_clause",
    "hire_docs_supplied", "photos_supplied",
})


def owner_of(name: str) -> str | None:
    if name in DOCUMENT_OWNED:
        return DOCUMENT
    if name in CUSTOMER_OWNED:
        return CUSTOMER
    if name in EVIDENCE_OWNED:
        return EVIDENCE
    return None
