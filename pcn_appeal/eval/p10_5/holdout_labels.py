"""Evaluator-side labels for NEW p10_5 sealed holdout (opened only at scoring).

Holdout JSON expected blocks remain sealed. Labels are applied at eval time.
Do not use these during implementation tuning.
"""
from __future__ import annotations

HOLDOUT_LABELS: dict[str, dict] = {
    "HOLDOUT_E": {
        "family": "mechanical_issue",
        "semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"],
        "must_not_include_grounds": ["KB-POFA-01", "KB-POFA-05"],
    },
    "HOLDOUT_F": {
        "family": "payment_keying",
        "semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"],
        "must_not_include_grounds": ["KB-POFA-01", "KB-POFA-05"],
    },
    "HOLDOUT_G": {
        "family": "collection",
        "semantic_concepts_any": ["COLLECTION", "PICK_UP"],
        "must_not_affirm_concepts": ["BROKEN_DOWN", "PAYMENT_MADE"],
    },
    "HOLDOUT_H": {
        "family": "uncertainty",
        "must_not_promote_facts": ["left_site", "permit_held"],
        "uncertain_or_negated_ok": True,
    },
}
