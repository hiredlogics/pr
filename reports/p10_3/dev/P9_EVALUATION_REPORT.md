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

- Total cases: 11
- COMPLETE: 6
- NOTICE_ONLY: 5
- Blind holdout scored: 0 (not used for tuning)

## End-to-end

- Passed cases: 11
- Failed cases: 0
- End-to-end pass rate: 100.0%
- COMPLETE pass rate: 100.0%
- NOTICE_ONLY pass rate: 100.0%
- BLIND_HOLDOUT pass rate: N/A

## Aggregate metrics

- Extraction accuracy (injected landing): 100.0%
- Legal-critical extraction accuracy: 100.0%
- Fact precision: 100.0%
- Fact recall: 100.0%
- Narrative fact precision: 100.0%
- Narrative fact recall: 100.0%
- Question precision: N/A
- Question recall: N/A
- Legal finding accuracy: 100.0%
- KG retrieval precision: 29.2%
- KG retrieval recall: 100.0%
- Ground precision: 100.0%
- Ground recall: 100.0%
- Claim Plan coverage: 100.0%
- SupportBundle completeness: 100.0%
- DraftContext coverage: 100.0%
- Draft ground coverage: 36.1%
- Draft material-fact coverage: 100.0%
- Unsupported assertion rate: 0.0%
- Validator detection rate: 100.0%
- Validator false-positive rate: 0.0%
- Outcome consistency (scored outcome layer): 100.0%
- Repeatability (semantic stability): N/A

## Architecture invariants

- Additive-ground pair (['REG_late_ntk', 'REG_late_ntk_anpr']): PASS — all still-valid A grounds remain in B
- Claim Plan persist/reload: see per-case reports (Master Case + SQLite store).
- PROCESSING_ERROR on a case that already failed draft/validation is not a hard-invariant breach. The invariant is: Claim Plan PASS + Draft PASS + Validation PASS + no exception ⇒ PROCESSING_ERROR impossible.
- Repeatability of authoritative semantic state: N/A

## First defective layer

No failed cases.

## Additive-ground invariant

```json
{
  "pair": [
    "REG_late_ntk",
    "REG_late_ntk_anpr"
  ],
  "run_a": [
    "KB-POFA-02"
  ],
  "run_b": [
    "KB-ANPR-01",
    "KB-POFA-02"
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
      "detected": true,
      "rules": [
        "VAL-COVERAGE",
        "VAL-DRAFT-PARTICULARS",
        "VAL-PARTICULARS",
        "VAL-SUBSTANCE"
      ],
      "draft_validation_issues": [
        "VAL-PARTICULARS",
        "VAL-COVERAGE",
        "VAL-DRAFT-PARTICULARS"
      ],
      "validation_issues": [
        "VAL-SUBSTANCE"
      ]
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
        "VAL-DRAFT-PARTICULARS"
      ],
      "validation_issues": []
    },
    {
      "mutation": "wrong_calculation",
      "detected": true,
      "rules": [
        "VAL-CALC"
      ],
      "draft_validation_issues": [],
      "validation_issues": [
        "VAL-CALC"
      ]
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
      "detected": true,
      "rules": [
        "VAL-INVENTED"
      ],
      "draft_validation_issues": [],
      "validation_issues": [
        "VAL-INVENTED"
      ]
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
      "detected": true,
      "rules": [
        "DV-LEAK",
        "VAL-LEAK"
      ],
      "draft_validation_issues": [
        "DV-LEAK"
      ],
      "validation_issues": [
        "VAL-LEAK"
      ]
    },
    {
      "mutation": "missing_cancellation_request",
      "detected": true,
      "rules": [
        "DV-STRUCTURE"
      ],
      "draft_validation_issues": [
        "DV-STRUCTURE"
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
        "DV-FACT",
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
        "VAL-FACT",
        "VAL-FACT"
      ]
    }
  ],
  "detection_rate": 1.0,
  "false_positive_rate": 0.0,
  "detected": 9,
  "total": 9
}
```

## Repeatability

```json
{
  "runs_per_case": 0,
  "stability_rate": "N/A",
  "note": "skipped"
}
```

## Per-case results

| Case | Split | Completeness | E2E | First fail | Grounds | State |
| --- | --- | --- | --- | --- | --- | --- |
| REG_late_ntk | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-02 | RELEASED |
| REG_late_ntk_anpr | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-02, KB-ANPR-01 | RELEASED |
| REG_notice_plus_payment | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-02, KB-PAY-01, KB-REC-01 | RELEASED |
| REG_overstay_dont_know | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-02, KB-REC-01 | RELEASED |
| REG_overstay_skip_all | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-02, KB-REC-01 | RELEASED |
| REG_overstay_yes_all | DEVELOPMENT | COMPLETE | PASS | — | KB-POFA-02, KB-REC-01 | RELEASED |
| REF_00347261100014 | DEVELOPMENT | NOTICE_ONLY | PASS | — | KB-POFA-02 | RELEASED |
| REF_00347261120013 | DEVELOPMENT | NOTICE_ONLY | PASS | — | KB-POFA-02 | RELEASED |
| REF_70377302 | DEVELOPMENT | NOTICE_ONLY | PASS | — | — | MANUAL_REVIEW |
| REF_88811908015 | DEVELOPMENT | NOTICE_ONLY | PASS | — | — | MANUAL_REVIEW |
| REF_stn1947529 | DEVELOPMENT | NOTICE_ONLY | PASS | — | — | MANUAL_REVIEW |

## Remaining defects (ranked)

No ranked defects on scored layers.

## Fine-tuning decision

Fine-tuning is **not** warranted from this baseline. Ground-truth volume is small (10 COMPLETE, none client-signed for final wording). Architecture and rules must finish passing on an approved set before any model task (narrative → controlled facts, or locked plan → polished drafting) is a candidate. Do not fine-tune unrestricted ground selection.

## Confirmations

- No case-specific application fixes were made during this baseline.
- Blind holdout was not used for prompt, rule, KB, or parameter tuning.
- Architecture was not redesigned.
- No production deploy.
