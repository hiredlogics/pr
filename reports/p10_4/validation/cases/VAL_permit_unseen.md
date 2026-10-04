# VAL_permit_unseen

- Family: `permit`
- Split: `VALIDATION`
- State: `RELEASED`
- Outcome: `None`
- E2E: PASS (first fail: —)
- Supported grounds: `['KB-AUTH-02', 'KB-REC-01']`
- Retrieved: `['KB-AUTH-02', 'KB-REC-01', 'KB-POFA-01']`

## Concepts
```json
[
  {
    "concept": "PERMIT_HELD",
    "polarity": "AFFIRMED",
    "attribution": "CUSTOMER",
    "source_text": "I am a permit holder for that block; the windscreen disc was on display.",
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
    "status": "SCORED",
    "clean_upstream": true,
    "draft_ground_coverage": 0.5,
    "material_fact_coverage": "N/A",
    "required_particular_coverage": "N/A",
    "unsupported_assertion_rate": 0.0,
    "validation_passed": true,
    "passed": true
  }
}
```

## Expected keys
`['orphan_support', 'promoted_facts', 'semantic_concepts_affirmed_any']`
