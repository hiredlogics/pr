# VAL_multiple_visits

- Family: `multiple_visits`
- Split: `VALIDATION`
- State: `RELEASED`
- Outcome: `None`
- E2E: PASS (first fail: —)
- Supported grounds: `['KB-ANPR-01']`
- Retrieved: `['KB-ANPR-01', 'KB-POFA-01']`

## Concepts
```json
[
  {
    "concept": "RETURNED",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "Drove out of the car park mid-morning, then came back later for a second visit.",
    "confidence": 0.85,
    "provenance": "deterministic_pattern"
  },
  {
    "concept": "MULTIPLE_VISITS",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "Drove out of the car park mid-morning, then came back later for a second visit.",
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
      "MULTIPLE_VISITS",
      "RETURNED"
    ],
    "concept_fp": [],
    "concept_fn": [],
    "concept_misses_partial": [
      "LEFT_SITE"
    ],
    "concept_unexpected_affirmed": [],
    "narrative_fact_precision": 1.0,
    "narrative_fact_recall": 1.0,
    "fact_tp": [
      "multiple_visits"
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
`['facts_any', 'invariants', 'kb_candidates_any', 'must_not_affirm_concepts', 'must_not_include_grounds', 'semantic_concepts_any']`
