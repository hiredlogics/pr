# P10.4 Validation Report

Frozen P10.3 semantic-layer + ground-role evaluation. **No code / prompt / KB / golden changes during scoring.**

## 1. Frozen versions

- Evaluation release: `P10_4_FROZEN_2026-10-04`
- Git commit: `be577e4111cefe0e9d22ad562aa1bf89d82542f2`
- Branch: `feature/p8-architecture-hardening`
- Dirty tree: `True`
- KB release: `None`
- Semantic ontology: `p10_3_ontology_v1` (a23fb0172da385d5)
- Module roles: `p10_3_roles_v1` (b777ffd2a9758a44)
- Claim Plan builder: `3`
- Model/provider: `ReferenceAnalysisLLM`
- Prompt versions: `{'classification': 2, 'extraction': 10, 'case_analysis': 8, 'drafting': 16, 'validation': 3}`

## 2. Role governance audit

P10.4 governance: EVIDENCE_REQUIREMENT must not imply can_lead_letter without explicit metadata. Frozen runtime still allows lead via CLAIM_GROUND_ROLES; flagged modules require ROLE_REVIEW_REQUIRED.

- Modules: 55
- By role: `{'SUBSTANTIVE_GROUND': 40, 'EVIDENCE_REQUIREMENT': 4, 'SUPPORTING_PROPOSITION': 9, 'LEGAL_CONCLUSION': 2}`
- ROLE_REVIEW_REQUIRED: **4**
- Flagged: `['KB-ANPR-02', 'KB-ANPR-03', 'KB-EV-01', 'KB-POFA-06']`

See `ROLE_GOVERNANCE_AUDIT.md`.

## 3. Fresh validation dataset

- Dataset: `p10_4_v1`
- Cases: 10
- IDs: `['VAL_multiple_visits', 'VAL_payment_keying', 'VAL_loading_delivery', 'VAL_mechanical', 'VAL_permit', 'VAL_negation', 'VAL_uncertainty', 'VAL_irrelevant', 'VAL_legal_finding_only', 'VAL_support_only']`

## 4–9. Validation metrics

| Metric | Value |
| --- | --- |
| E2E pass rate | 100.0% |
| Semantic concept P / R | 100.0% / 100.0% |
| Narrative fact P / R | 100.0% / 100.0% |
| Negation accuracy | 100.0% |
| Uncertainty accuracy | 100.0% |
| Attribution accuracy | 100.0% |
| Fact-promotion accuracy | 100.0% |
| Derived-lineage completeness | N/A |
| KB candidate P / R | 100.0% / 100.0% |
| Role-filter P / R | 100.0% / 100.0% |
| Ground P / R | 100.0% / 100.0% |
| Orphan-support letters | 0 |
| SUPPORTING incorrectly in Claim Plan | 0 (expected 0) |
| LEGAL_CONCLUSION independent grounds | 0 (expected 0) |
| Clean-upstream draft cases | 7 |
| Draft ground coverage | 71.4% |
| Material-fact coverage | 100.0% |
| Required-particular coverage | N/A |
| Unsupported assertion rate | 0.0% |

## 5. Semantic concept confusion

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

## 10. Validator mutation results

- Status: `SCORED`
- Detection rate: 100.0% (expected 100%)
- False-positive rate: 0.0% (expected 0%)
- Passed: **True**

## 11. P8/P10 invariant results

- Passed: **True**
- Tests run: 112
- Checks: `{'Master Case': True, 'FactManager / P8 architecture': True, 'P10.3 semantic + roles': True, 'P10 remediation': True}`

## Per-case first-fail

| case_id | family | e2e | first_fail | grounds |
| --- | --- | --- | --- | --- |
| VAL_irrelevant | irrelevant_narrative | PASS | — | `[]` |
| VAL_legal_finding_only | legal_finding_only | PASS | — | `['KB-POFA-02']` |
| VAL_loading_delivery | loading_delivery | PASS | — | `['KB-ACT-01', 'KB-BAY-02']` |
| VAL_mechanical | mechanical_issue | PASS | — | `['KB-BREAK-01']` |
| VAL_multiple_visits | multiple_visits | PASS | — | `['KB-ANPR-01']` |
| VAL_negation | negation | PASS | — | `[]` |
| VAL_payment_keying | payment_keying | PASS | — | `['KB-PAY-01', 'KB-KEY-01']` |
| VAL_permit | permit_issue | PASS | — | `['KB-AUTH-02', 'KB-REC-01']` |
| VAL_support_only | support_only_candidate | PASS | — | `['KB-AUTH-02']` |
| VAL_uncertainty | uncertainty | PASS | — | `[]` |
