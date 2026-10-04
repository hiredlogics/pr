# HOLDOUT_C

- Family: `loading_collection`
- Split: `HOLDOUT`
- State: `RELEASED`
- Outcome: `None`
- E2E: FAIL (first fail: SEMANTIC)
- Supported grounds: `['KB-ACT-01']`
- Retrieved: `['KB-ACT-01']`

## Concepts
```json
[
  {
    "concept": "COLLECTION",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "Collecting a parcel from the unit; vehicle stationary only for the collection.",
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
    "semantic_concept_recall": 0.5,
    "concept_tp": [
      "COLLECTION"
    ],
    "concept_fp": [],
    "concept_fn": [
      "PICK_UP"
    ],
    "concept_unexpected_affirmed": [],
    "narrative_fact_precision": "N/A",
    "narrative_fact_recall": "N/A",
    "fact_tp": [],
    "fact_fp": [],
    "fact_fn": [],
    "negation_accuracy": 1.0,
    "uncertainty_accuracy": 1.0,
    "attribution_accuracy": 1.0,
    "fact_promotion_accuracy": 1.0,
    "derived_lineage_completeness": "N/A",
    "passed": false
  },
  "kb_ground": {
    "status": "SCORED",
    "kb_candidate_precision": "N/A",
    "kb_candidate_recall": "N/A",
    "candidate_tp": [],
    "candidate_fp": [],
    "candidate_fn": [],
    "role_filter_precision": 1.0,
    "role_filter_recall": 1.0,
    "ground_precision": "N/A",
    "ground_recall": "N/A",
    "ground_tp": [],
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
      "no_support_orphan_letter": true,
      "no_structural_lead": true,
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
    "draft_ground_coverage": 0.0,
    "material_fact_coverage": "N/A",
    "required_particular_coverage": "N/A",
    "unsupported_assertion_rate": 0.0,
    "validation_passed": true,
    "passed": true
  }
}
```

## Expected keys
`['sealed']`
