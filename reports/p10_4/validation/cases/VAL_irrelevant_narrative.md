# VAL_irrelevant_narrative

- Family: `irrelevant`
- Split: `VALIDATION`
- State: `MANUAL_REVIEW`
- Outcome: `NO_SUPPORTED_GROUNDS`
- E2E: PASS (first fail: —)
- Supported grounds: `[]`
- Retrieved: `['KB-POFA-01']`

## Concepts
```json
[
  {
    "concept": "SHOPPING",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "The weather was pleasant and I like this shopping centre's coffee.",
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
    "semantic_concept_precision": "N/A",
    "semantic_concept_recall": "N/A",
    "concept_tp": [],
    "concept_fp": [],
    "concept_fn": [],
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
    "passed": true
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
    "status": "N/A",
    "reason": "upstream incomplete or not released",
    "clean_upstream": false,
    "passed": null
  }
}
```

## Expected keys
`['orphan_support', 'semantic_concepts_affirmed']`
