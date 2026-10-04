"""Evaluator-side labels for sealed p10_3 holdout (opened at P10.4 scoring only).

Holdout JSON expected blocks remain sealed / untouched. Labels live here so
implementation never required editing holdout goldens.
"""
from __future__ import annotations

HOLDOUT_LABELS: dict[str, dict] = {
    "HOLDOUT_A": {
        "family": "mechanical_issue",
        "semantic_concepts_any": ["BROKEN_DOWN", "IMMOBILISED"],
        "facts_any": {"vehicle_immobilised": True},
        "must_not_include_grounds": ["KB-POFA-01", "KB-POFA-05"],
    },
    "HOLDOUT_B": {
        "family": "payment_keying",
        "semantic_concepts_any": ["PAYMENT_MADE", "KEYING_ERROR"],
        "facts_any": {"payment_made": True},
        "substantive_grounds_any": ["KB-PAY-01", "KB-KEY-01"],
        "must_not_include_grounds": ["KB-POFA-01", "KB-POFA-05"],
    },
    "HOLDOUT_C": {
        "family": "loading_collection",
        "semantic_concepts_any": ["COLLECTION", "PICK_UP"],
        "must_not_affirm_concepts": ["BROKEN_DOWN", "PAYMENT_MADE"],
        "must_not_include_grounds": ["KB-POFA-01", "KB-POFA-05"],
    },
    "HOLDOUT_D": {
        "family": "uncertainty_permit_site",
        "must_not_promote_facts": ["permit_held", "left_site"],
        "must_not_affirm_concepts": ["PERMIT_DISPLAYED"],
        # "possibly left site" / "not certain whether a permit" → uncertain
        "uncertain_or_negated_ok": True,
    },
}
