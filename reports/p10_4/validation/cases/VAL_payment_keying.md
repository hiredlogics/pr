# VAL_payment_keying

- Family: `payment_keying`
- Split: `VALIDATION`
- State: `RELEASED`
- Outcome: `None`
- E2E: PASS (first fail: —)
- Supported grounds: `['KB-PAY-01', 'KB-KEY-01']`
- Retrieved: `['KB-KEY-01', 'KB-PAY-01', 'KB-POFA-01']`

## Concepts
```json
[
  {
    "concept": "PAYMENT_MADE",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "I paid using the parking app, but the registration I typed was wrong.",
    "confidence": 0.85,
    "provenance": "deterministic_pattern"
  },
  {
    "concept": "KEYING_ERROR",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "I paid using the parking app, but the registration I typed was wrong.",
    "confidence": 0.85,
    "provenance": "deterministic_pattern"
  }
]
```

## Scores
```json
{
  "semantic": {
    "status": "SCORED",
    "semantic_concept_precision": 1.0,
    "semantic_concept_recall": 1.0,
    "concept_tp": [
      "KEYING_ERROR",
      "PAYMENT_MADE"
    ],
    "concept_fp": [],
    "concept_fn": [],
    "concept_misses_partial": [],
    "concept_unexpected_affirmed": [],
    "narrative_fact_precision": 1.0,
    "narrative_fact_recall": 1.0,
    "fact_tp": [
      "payment_made",
      "keying_error_type"
    ],
    "fact_fp": [],
    "fact_fn": [],
    "negation_accuracy": 1.0,
    "uncertainty_accuracy": 1.0,
    "attribution_accuracy": 1.0,
    "fact_promotion_accuracy": 1.0,
    "derived_lineage_completeness": "N/A",
    "passed": true
  },
  "kb_ground": {
    "status": "SCORED",
    "kb_candidate_precision": 1.0,
    "kb_candidate_recall": 1.0,
    "candidate_tp": [
      "KB-KEY-01",
      "KB-PAY-01"
    ],
    "candidate_fp": [],
    "candidate_fn": [],
    "role_filter_precision": 1.0,
    "role_filter_recall": 1.0,
    "ground_precision": 1.0,
    "ground_recall": 1.0,
    "ground_tp": [
      "KB-KEY-01",
      "KB-PAY-01"
    ],
    "ground_fp": [],
    "ground_fn": [],
    "orphan_support": false,
    "orphan_support_letter": false,
    "unsupported_leading_ground_rate": 0.0,
    "supporting_incorrectly_in_claim_plan": 0,
    "legal_conclusion_independent_grounds": 0,
    "legal_in_plan": [],
    "support_in_plan": [],
    "structural_in_plan": [],
    "evidence_in_plan": [],
    "primary_route_ok": true,
    "passed": true
  },
  "invariants": {
    "status": "SCORED",
    "checks": {
      "support_cannot_lead_alone": true,
      "no_legal_conclusion_independent": true
    },
    "legal_conclusion_in_plan": [],
    "supporting_in_plan": [],
    "passed": true
  },
  "draft": {
    "status": "SCORED",
    "clean_upstream": true,
    "draft_ground_coverage": 1.0,
    "material_fact_coverage": 1.0,
    "required_particular_coverage": "N/A",
    "unsupported_assertion_rate": 0.0,
    "validation_passed": true,
    "passed": true
  }
}
```

## Expected keys
`['facts_any', 'invariants', 'kb_candidates_any', 'must_not_include_grounds', 'primary_route_any', 'semantic_concepts_any', 'substantive_grounds_any']`
