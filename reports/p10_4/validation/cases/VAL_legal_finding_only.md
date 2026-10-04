# VAL_legal_finding_only

- Family: `legal_finding_only`
- Split: `VALIDATION`
- State: `RELEASED`
- Outcome: `None`
- E2E: PASS (first fail: —)
- Supported grounds: `['KB-POFA-02']`
- Retrieved: `['KB-POFA-02', 'KB-POFA-05', 'KB-POFA-01']`

## Concepts
```json
[]
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
    "concept_misses_partial": [],
    "concept_unexpected_affirmed": [],
    "narrative_fact_precision": "N/A",
    "narrative_fact_recall": "N/A",
    "fact_tp": [],
    "fact_fp": [],
    "fact_fn": [],
    "negation_accuracy": 1.0,
    "uncertainty_accuracy": 1.0,
    "attribution_accuracy": 1.0,
    "fact_promotion_accuracy": "N/A",
    "derived_lineage_completeness": "N/A",
    "passed": true
  },
  "kb_ground": {
    "status": "SCORED",
    "kb_candidate_precision": 1.0,
    "kb_candidate_recall": 1.0,
    "candidate_tp": [
      "KB-POFA-02"
    ],
    "candidate_fp": [],
    "candidate_fn": [],
    "role_filter_precision": 1.0,
    "role_filter_recall": 1.0,
    "ground_precision": 1.0,
    "ground_recall": 1.0,
    "ground_tp": [
      "KB-POFA-02"
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
    "material_fact_coverage": "N/A",
    "required_particular_coverage": "N/A",
    "unsupported_assertion_rate": 0.0,
    "validation_passed": true,
    "passed": true
  }
}
```

## Expected keys
`['findings_any_types', 'invariants', 'kb_candidates_any', 'legal_conclusion_independent_count', 'must_not_include_grounds', 'substantive_grounds_any']`
