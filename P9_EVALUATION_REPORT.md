# P9 Evaluation Report — BASELINE

This is an honest baseline. Application code, prompts, KB content, and
golden expectations were not changed to make the current output pass.

## Scope and limits

- Live vision extraction was **not** available. Document fields were
  injected from labeled gold via `ReferenceAnalysisLLM`. Extraction
  metrics therefore measure fact-graph landing, not OCR.
- COMPLETE journey cases do **not** treat snapshot `approved` lists as
  client-approved grounds. Those layers are N/A unless independently
  approved (late NTK finding; scenario-suite payment/keying).
- NOTICE_ONLY cases are not complete ground truth. They do not establish
  unknown customer circumstances, reverse-page defects, or approved grounds.
- Blind holdout was scored and **not** used for tuning.

## Versions

- git commit: `be577e4111cefe0e9d22ad562aa1bf89d82542f2`
- KB release: `None`
- prompt versions: `{'extraction': 10, 'drafting': 16, 'case_analysis': 8, 'validation': 3, 'page_references': 2, 'classification': 2}`
- model/provider: `ReferenceAnalysisLLM`
- Claim Plan builder: `3`
- Master Case Object: `1`

## Dataset

- Total cases: 19
- COMPLETE: 10
- NOTICE_ONLY: 9
- Blind holdout scored: 4 (not used for tuning)

## End-to-end

- Passed cases: 7
- Failed cases: 12
- End-to-end pass rate: 36.8%
- COMPLETE pass rate: 50.0%
- NOTICE_ONLY pass rate: 22.2%
- BLIND_HOLDOUT pass rate: 25.0%

## Aggregate metrics

- Extraction accuracy (injected landing): 93.1%
- Legal-critical extraction accuracy: 100.0%
- Fact precision: 100.0%
- Fact recall: 91.6%
- Narrative fact precision: 66.7%
- Narrative fact recall: 66.7%
- Question precision: N/A
- Question recall: N/A
- Legal finding accuracy: 100.0%
- KG retrieval precision: 23.3%
- KG retrieval recall: 83.3%
- Ground precision: 30.0%
- Ground recall: 83.3%
- Claim Plan coverage: 83.3%
- SupportBundle completeness: 83.3%
- DraftContext coverage: 100.0%
- Draft ground coverage: 9.3%
- Draft material-fact coverage: 63.6%
- Unsupported assertion rate: 0.0%
- Validator detection rate: 55.6%
- Validator false-positive rate: 0.0%
- Outcome consistency (scored outcome layer): 50.0%
- Repeatability (semantic stability): 100.0%

## Architecture invariants

- Additive-ground pair (['REG_late_ntk', 'REG_late_ntk_anpr']): PASS — all still-valid A grounds remain in B
- Claim Plan persist/reload: see per-case reports (Master Case + SQLite store).
- PROCESSING_ERROR appeared on REG_payment_keying and REG_residential, but both already failed draft/validation. The hard invariant (Claim Plan PASS + Draft PASS + Validation PASS + no exception ⇒ PROCESSING_ERROR impossible) was **not** falsified.
- Repeatability of authoritative semantic state: 100.0%

## First defective layer

- `EXTRACTION_ERROR`: 7
- `GROUND_SELECTION_ERROR`: 2
- `NARRATIVE_FACT_ERROR`: 1
- `KNOWLEDGE_RETRIEVAL_ERROR`: 1
- `DRAFT_ERROR`: 1

## Additive-ground invariant

```json
{
  "pair": [
    "REG_late_ntk",
    "REG_late_ntk_anpr"
  ],
  "run_a": [
    "KB-POFA-01",
    "KB-POFA-02",
    "KB-POFA-04",
    "KB-POFA-05"
  ],
  "run_b": [
    "KB-ANPR-01",
    "KB-POFA-01",
    "KB-POFA-02",
    "KB-POFA-04",
    "KB-POFA-05"
  ],
  "dropped_without_check": [],
  "added": [
    "KB-ANPR-01"
  ],
  "passed": true,
  "detail": "all still-valid A grounds remain in B",
  "note": "This baseline cannot inspect InvalidationRecord if the ground is absent; any drop is treated as a silent remove."
}
```

## Validator mutation report

```json
{
  "status": "SCORED",
  "clean_draft_flagged": false,
  "clean_rules": [],
  "rows": [
    {
      "mutation": "missing_ground",
      "detected": false,
      "rules": [],
      "draft_validation_issues": [],
      "validation_issues": []
    },
    {
      "mutation": "missing_material_fact",
      "detected": true,
      "rules": [
        "VAL-DRAFT-PARTICULARS",
        "VAL-PARTICULARS"
      ],
      "draft_validation_issues": [
        "VAL-PARTICULARS",
        "VAL-DRAFT-PARTICULARS",
        "VAL-DRAFT-PARTICULARS"
      ],
      "validation_issues": []
    },
    {
      "mutation": "wrong_calculation",
      "detected": false,
      "rules": [],
      "draft_validation_issues": [],
      "validation_issues": []
    },
    {
      "mutation": "unsupported_legal_proposition",
      "detected": true,
      "rules": [
        "VAL-OBSOLETE"
      ],
      "draft_validation_issues": [],
      "validation_issues": [
        "VAL-OBSOLETE"
      ]
    },
    {
      "mutation": "invented_fact",
      "detected": false,
      "rules": [],
      "draft_validation_issues": [],
      "validation_issues": []
    },
    {
      "mutation": "driver_disclosure",
      "detected": true,
      "rules": [
        "DV-DRIVER",
        "VAL-DRIVER"
      ],
      "draft_validation_issues": [
        "DV-DRIVER"
      ],
      "validation_issues": [
        "VAL-DRIVER"
      ]
    },
    {
      "mutation": "unresolved_placeholder",
      "detected": false,
      "rules": [],
      "draft_validation_issues": [],
      "validation_issues": []
    },
    {
      "mutation": "missing_cancellation_request",
      "detected": true,
      "rules": [
        "DV-STRUCTURE",
        "VAL-COVERAGE"
      ],
      "draft_validation_issues": [
        "DV-STRUCTURE",
        "VAL-COVERAGE"
      ],
      "validation_issues": []
    },
    {
      "mutation": "source_lineage_failure",
      "detected": true,
      "rules": [
        "DV-FACT",
        "VAL-COVERAGE",
        "VAL-FACT"
      ],
      "draft_validation_issues": [
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "DV-FACT",
        "VAL-COVERAGE",
        "VAL-COVERAGE",
        "VAL-COVERAGE",
        "VAL-COVERAGE"
      ],
      "validation_issues": [
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT",
        "VAL-FACT"
      ]
    }
  ],
  "detection_rate": 0.5555555555555556,
  "false_positive_rate": 0.0,
  "detected": 5,
  "total": 9
}
```

## Repeatability

```json
{
  "runs_per_case": 5,
  "cases": [
    "REG_breakdown",
    "REG_late_ntk",
    "REG_late_ntk_anpr",
    "REG_overstay_skip_all",
    "REG_payment_keying"
  ],
  "stable_cases": 5,
  "stability_rate": 1.0,
  "per_case": {
    "REG_breakdown": {
      "runs": 5,
      "unique_semantic_states": 1,
      "stable": true
    },
    "REG_late_ntk": {
      "runs": 5,
      "unique_semantic_states": 1,
      "stable": true
    },
    "REG_late_ntk_anpr": {
      "runs": 5,
      "unique_semantic_states": 1,
      "stable": true
    },
    "REG_overstay_skip_all": {
      "runs": 5,
      "unique_semantic_states": 1,
      "stable": true
    },
    "REG_payment_keying": {
      "runs": 5,
      "unique_semantic_states": 1,
      "stable": true
    }
  },
  "note": "Exact wording may vary; this compares facts, findings, grounds, plan digest, and outcome only."
}
```

## Per-case results

| Case | Split | Completeness | E2E | First fail | Grounds | State |
| --- | --- | --- | --- | --- | --- | --- |
| REG_breakdown | BLIND_HOLDOUT | COMPLETE | FAIL | NARRATIVE_FACT_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05 | RELEASED |
| REG_late_ntk | DEVELOPMENT | COMPLETE | FAIL | GROUND_SELECTION_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-POFA-02 | RELEASED |
| REG_late_ntk_anpr | DEVELOPMENT | COMPLETE | FAIL | GROUND_SELECTION_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-POFA-02, KB-ANPR-01 | RELEASED |
| REG_no_notice | VALIDATION | COMPLETE | PASS | — | — | NO_APPEAL_RIGHT |
| REG_notice_plus_payment | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-POFA-02, KB-PAY-01, KB-REC-01 | RELEASED |
| REG_overstay_dont_know | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-POFA-02, KB-REC-01 | RELEASED |
| REG_overstay_skip_all | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-POFA-02, KB-REC-01 | RELEASED |
| REG_overstay_yes_all | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-POFA-02, KB-REC-01 | RELEASED |
| REG_payment_keying | VALIDATION | COMPLETE | FAIL | KNOWLEDGE_RETRIEVAL_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-PAY-01 | MANUAL_REVIEW |
| REG_residential | BLIND_HOLDOUT | COMPLETE | FAIL | DRAFT_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-RES-03, KB-REC-01 | MANUAL_REVIEW |
| REF_00347261100014 | DEVELOPMENT | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | KB-POFA-01, KB-POFA-05, KB-POFA-02 | RELEASED |
| REF_00347261120013 | DEVELOPMENT | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | KB-POFA-01, KB-POFA-05, KB-POFA-02 | RELEASED |
| REF_70377302 | DEVELOPMENT | NOTICE_ONLY | PASS | — | KB-POFA-01 | MANUAL_REVIEW |
| REF_88811908015 | DEVELOPMENT | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | — | MANUAL_REVIEW |
| REF_lu3440789 | VALIDATION | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | — | MANUAL_REVIEW |
| REF_lu3489734 | VALIDATION | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | — | MANUAL_REVIEW |
| REF_new1936102153896 | BLIND_HOLDOUT | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | KB-POFA-01 | MANUAL_REVIEW |
| REF_sp62712518 | BLIND_HOLDOUT | NOTICE_ONLY | PASS | — | KB-POFA-01, KB-POFA-05, KB-POFA-02 | RELEASED |
| REF_stn1947529 | DEVELOPMENT | NOTICE_ONLY | FAIL | EXTRACTION_ERROR | KB-POFA-01 | MANUAL_REVIEW |

## Remaining defects (ranked)

1. **P1** — GROUND_SELECTION_ERROR: 2 case(s): REG_late_ntk, REG_late_ntk_anpr
2. **P1** — DRAFT_ERROR: 1 case(s): REG_residential
3. **P1** — VALIDATOR_MISS: undetected mutations: missing_ground, wrong_calculation, invented_fact, unresolved_placeholder
4. **P2** — EXTRACTION_ERROR: 7 case(s): REF_00347261100014, REF_00347261120013, REF_88811908015, REF_lu3440789, REF_lu3489734, REF_new1936102153896, REF_stn1947529
5. **P2** — NARRATIVE_FACT_ERROR: 1 case(s): REG_breakdown
6. **P2** — KNOWLEDGE_RETRIEVAL_ERROR: 1 case(s): REG_payment_keying

## Fine-tuning decision

Fine-tuning is **not** warranted from this baseline. Ground-truth volume is small (10 COMPLETE, none client-signed for final wording). Architecture and rules must finish passing on an approved set before any model task (narrative → controlled facts, or locked plan → polished drafting) is a candidate. Do not fine-tune unrestricted ground selection.

## Confirmations

- No case-specific application fixes were made during this baseline.
- Blind holdout was not used for prompt, rule, KB, or parameter tuning.
- Architecture was not redesigned.
- No production deploy.
