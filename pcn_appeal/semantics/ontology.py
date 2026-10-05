"""Controlled semantic ontology between narrative and FactManager (P10.5).

Concepts are factual meaning labels. They are not KB modules and not legal
grounds. Promotion into typed facts uses CONCEPT_TO_FACTS.

P10.5: CONCEPT_DEFINITIONS state meaning (not keywords) for LLM extraction.
"""
from __future__ import annotations

# Concept id → short human label (docs / metrics).
CONCEPTS: dict[str, str] = {
    # MOVEMENT
    "LEFT_SITE": "Customer left the parking site during the visit",
    "RETURNED": "Customer returned to the site or vehicle",
    "MULTIPLE_VISITS": "More than one visit / entry is described",
    # PAYMENT
    "PAYMENT_MADE": "A parking payment was made",
    "PAYMENT_ATTEMPTED": "A payment was attempted",
    "PAYMENT_FAILED": "A payment attempt failed",
    "KEYING_ERROR": "Registration was mistyped or mis-keyed at payment",
    # ACTIVITY
    "SHOPPING": "Shopping / retail purpose",
    "DROP_OFF": "Dropping a passenger or goods",
    "PICK_UP": "Collecting a passenger or goods",
    "LOADING": "Loading activity",
    "DELIVERY": "Delivery activity",
    "COLLECTION": "Collection activity",
    # VEHICLE
    "BROKEN_DOWN": "Vehicle suffered a mechanical failure",
    "IMMOBILISED": "Vehicle could not be moved as intended",
    # PERMIT
    "PERMIT_HELD": "A permit / authorisation is held",
    "PERMIT_DISPLAYED": "A permit was displayed",
    "REGISTRATION_MISMATCH": "Entered registration differs from the vehicle",
    # PERSON
    "PASSENGER_PRESENT": "A passenger was present",
    "CHILD_PRESENT": "A child was present in the vehicle",
    "DISABLED_PASSENGER": "A disabled passenger / blue-badge context",
}

# Meaning-first definitions for LLM semantic extraction (P10.5).
# Extractors reason from these meanings, not keyword lists.
CONCEPT_DEFINITIONS: dict[str, str] = {
    "LEFT_SITE": (
        "The vehicle or person relevant to the account departed the parking "
        "or site area during the material sequence (including temporary departure)."
    ),
    "RETURNED": (
        "The vehicle or person subsequently came back to the site or vehicle "
        "during the relevant period after having been away."
    ),
    "MULTIPLE_VISITS": (
        "More than one distinct visit, entry, or stay at the site is described "
        "for the same day or material period."
    ),
    "PAYMENT_MADE": (
        "A parking payment was completed or settled (app, machine, phone, or "
        "other channel), not merely intended."
    ),
    "PAYMENT_ATTEMPTED": (
        "A payment was tried or initiated but completion is not established."
    ),
    "PAYMENT_FAILED": (
        "A payment attempt failed, was declined, or did not go through."
    ),
    "KEYING_ERROR": (
        "Information entered into a payment or registration system was "
        "materially mistyped or mismatched relative to the vehicle."
    ),
    "REGISTRATION_MISMATCH": (
        "The registration entered or recorded does not match the vehicle's "
        "actual registration."
    ),
    "SHOPPING": "The purpose of presence involved shopping or retail purchases.",
    "DROP_OFF": "Someone or something was dropped off at or near the site.",
    "PICK_UP": "Someone was collected or picked up at or near the site.",
    "LOADING": (
        "Goods were being loaded or unloaded as the reason for presence."
    ),
    "DELIVERY": (
        "Presence related to delivering goods or a courier delivery."
    ),
    "COLLECTION": (
        "Presence related to collecting goods, a parcel, or an order."
    ),
    "BROKEN_DOWN": (
        "A mechanical or electrical fault materially affected normal vehicle "
        "operation (including failure to restart, stall, loss of power)."
    ),
    "IMMOBILISED": (
        "The vehicle could not practically be moved or driven during the "
        "relevant period as intended."
    ),
    "PERMIT_HELD": (
        "The customer holds or is entitled to a permit or similar authorisation."
    ),
    "PERMIT_DISPLAYED": (
        "A permit was shown or displayed in connection with the parking event."
    ),
    "PASSENGER_PRESENT": "A passenger was present in the vehicle.",
    "CHILD_PRESENT": "A child was present in the vehicle.",
    "DISABLED_PASSENGER": (
        "A disabled passenger or blue-badge context is described."
    ),
}

# Concept → FactManager fact name + value (typed promotion).
# Negated / uncertain concepts are not promoted as affirmative facts.
# DROP_OFF / PICK_UP promote activity facts (KB-ACT-02 gates on dropoff_activity),
# not only purpose_of_visit labels.
CONCEPT_TO_FACTS: dict[str, tuple[str, object]] = {
    "LEFT_SITE": ("left_site", True),
    "RETURNED": ("returned_same_day", True),
    "MULTIPLE_VISITS": ("multiple_visits", True),
    "PAYMENT_MADE": ("payment_made", True),
    "PAYMENT_ATTEMPTED": ("payment_attempt_failed", True),
    "PAYMENT_FAILED": ("payment_attempt_failed", True),
    "KEYING_ERROR": ("keying_error_type", "MINOR"),
    "SHOPPING": ("purpose_of_visit", "shopping"),
    "DROP_OFF": ("dropoff_activity", True),
    "PICK_UP": ("pickup_activity", True),
    "LOADING": ("loading_activity", True),
    "DELIVERY": ("loading_activity", True),
    "COLLECTION": ("loading_activity", True),
    "BROKEN_DOWN": ("vehicle_immobilised", True),
    "IMMOBILISED": ("vehicle_immobilised", True),
    "PERMIT_HELD": ("permit_held", True),
    "PERMIT_DISPLAYED": ("permit_held", True),
    "REGISTRATION_MISMATCH": ("keying_error_type", "MINOR"),
    "CHILD_PRESENT": ("child_occupant_present", True),
    "DISABLED_PASSENGER": ("disability_extra_time", True),
}

# Additional FactManager writes when a concept is AFFIRMED (same provenance).
# Used so DROP_OFF/PICK_UP also label purpose_of_visit when not already set.
CONCEPT_EXTRA_FACTS: dict[str, tuple[tuple[str, object], ...]] = {
    "DROP_OFF": (("purpose_of_visit", "drop_off"),),
    "PICK_UP": (("purpose_of_visit", "pick_up"),),
}

ONTOLOGY_VERSION = "p10_5_ontology_v1"
PROMOTE_MIN_CONFIDENCE = 0.55
