# P10.4 — Fresh Validation + Sealed Holdout Evaluation

Evaluation of the **frozen** P10.3 semantic-layer + ground-role architecture.
No behavioural code changes, prompt edits, KB edits, golden edits, pgvector,
fine-tuning, or holdout-tuned patches were applied during scoring.

## 1. Frozen versions

```json
{
  "evaluation_release": "P10_4_FROZEN_2026-10-04",
  "git_commit": "be577e4111cefe0e9d22ad562aa1bf89d82542f2",
  "git_branch": "feature/p8-architecture-hardening",
  "working_tree_dirty": true,
  "kb_release": null,
  "module_count": 55,
  "semantic_ontology_version": "p10_3_ontology_v1",
  "semantic_ontology_digest": "a23fb0172da385d5",
  "semantic_concept_count": 21,
  "module_role_version": "p10_3_roles_v1",
  "module_role_digest": "b777ffd2a9758a44",
  "claim_plan_builder_version": "3",
  "master_case_schema_version": 1,
  "validation_engine_version": "VAL-5",
  "draft_validation_version": "DV-1",
  "prompt_versions": {
    "classification": 2,
    "extraction": 10,
    "case_analysis": 8,
    "drafting": 16,
    "validation": 3
  },
  "model_provider": "ReferenceAnalysisLLM"
}
```

_Note: P10.4 evaluation freeze. Application behaviour must not change during scoring. Working tree may contain uncommitted P10.3 work._

## 2. Role-governance audit

P10.4 governance: EVIDENCE_REQUIREMENT must not imply can_lead_letter without explicit metadata. Frozen runtime still allows lead via CLAIM_GROUND_ROLES; flagged modules require ROLE_REVIEW_REQUIRED.

- ROLE_REVIEW_REQUIRED count: **4**
- Flagged modules: `['KB-ANPR-02', 'KB-ANPR-03', 'KB-EV-01', 'KB-POFA-06']`
- By role: `{'SUBSTANTIVE_GROUND': 40, 'EVIDENCE_REQUIREMENT': 4, 'SUPPORTING_PROPOSITION': 9, 'LEGAL_CONCLUSION': 2}`

Full table: `ROLE_GOVERNANCE_AUDIT.md`.

## 3. Fresh validation dataset manifest

- `p10_4_v1` — 10 cases
- Families: `{'VAL_multiple_visits': 'multiple_visits', 'VAL_payment_keying': 'payment_keying', 'VAL_loading_delivery': 'loading_delivery', 'VAL_mechanical': 'mechanical_issue', 'VAL_permit': 'permit_issue', 'VAL_negation': 'negation', 'VAL_uncertainty': 'uncertainty', 'VAL_irrelevant': 'irrelevant_narrative', 'VAL_legal_finding_only': 'legal_finding_only', 'VAL_support_only': 'support_only_candidate'}`

## 4. Validation metrics

- E2E: 100.0%
- Semantic P/R: 100.0% / 100.0%
- Narrative fact P/R: 100.0% / 100.0%
- Negation / uncertainty / attribution: 100.0% / 100.0% / 100.0%
- Fact-promotion / lineage: 100.0% / N/A

## 5. Semantic concept confusion table

| concept | tp | fp | fn |
| --- | --- | --- | --- |
| BROKEN_DOWN | 1 | 0 | 0 |
| DELIVERY | 1 | 0 | 0 |
| IMMOBILISED | 1 | 0 | 0 |
| KEYING_ERROR | 1 | 0 | 0 |
| LEFT_SITE | 0 | 0 | 1 |
| LOADING | 1 | 0 | 0 |
| MULTIPLE_VISITS | 1 | 0 | 0 |
| PAYMENT_MADE | 1 | 0 | 0 |
| PERMIT_HELD | 1 | 0 | 0 |
| RETURNED | 1 | 0 | 0 |

## 6. KB candidate metrics

- Precision: 100.0%
- Recall: 100.0%
- Role-filter P/R: 100.0% / 100.0%

## 7. Ground precision / recall

- Precision: 100.0%
- Recall: 100.0%

## 8. Orphan-support results

- Orphan-support letters: 0
- SUPPORTING incorrectly reached Claim Plan: **0** (expected 0)
- LEGAL_CONCLUSION independent grounds: **0** (expected 0)

## 9. Drafting metrics (clean upstream subset)

- Clean cases: 7
- Ground coverage: 71.4%
- Material-fact coverage: 100.0%
- Required-particular coverage: N/A
- Unsupported assertion rate: 0.0%

## 10. Validator mutation results

- Detection: 100.0% (P9 classes 100.0%; extras 100.0%)
- False positives: 0.0%
- Passed: **True**

## 11. P8/P10 invariant results

- Passed: **True**
- `{'Master Case': True, 'FactManager / P8 architecture': True, 'P10.3 semantic + roles': True, 'P10 remediation': True}`

## 12. Sealed holdout results

- E2E: 25.0%
- Semantic P/R: 100.0% / 33.3%
- Ground P/R: N/A / 0.0%
- LEGAL_CONCLUSION independent: 0
- SUPPORTING incorrectly in plan: 0

| case_id | e2e | first_fail | grounds |
| --- | --- | --- | --- |
| HOLDOUT_A | FAIL | SEMANTIC | `[]` |
| HOLDOUT_B | FAIL | SEMANTIC | `[]` |
| HOLDOUT_C | PASS | — | `['KB-ACT-01']` |
| HOLDOUT_D | FAIL | SEMANTIC | `['KB-REC-01']` |

## 13. Remaining first-defective layers

- HOLDOUT_A: SEMANTIC
- HOLDOUT_B: SEMANTIC
- HOLDOUT_D: SEMANTIC
- Frequency: SEMANTIC×3

## 14. Recommendation

**MORE_SEMANTIC_WORK**

- ADD_VECTOR_RETRIEVAL: `False`
- Production deploy: **forbidden** (`do_not_production_deploy=True`)
- Fine-tune: **forbidden** (`do_not_fine_tune=True`)

Reasons:
- sealed holdout E2E 25% - wording/pattern generalisation gap
- sealed holdout semantic recall 33%
- 4 modules ROLE_REVIEW_REQUIRED (evidence lead inheritance)

---

STOP after report. No post-evaluation code changes in this release.
