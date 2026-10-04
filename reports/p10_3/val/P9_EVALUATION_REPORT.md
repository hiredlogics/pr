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

- Total cases: 4
- COMPLETE: 2
- NOTICE_ONLY: 2
- Blind holdout scored: 0 (not used for tuning)

## End-to-end

- Passed cases: 4
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
- Legal finding accuracy: N/A
- KG retrieval precision: 66.7%
- KG retrieval recall: 100.0%
- Ground precision: 100.0%
- Ground recall: 100.0%
- Claim Plan coverage: 100.0%
- SupportBundle completeness: 100.0%
- DraftContext coverage: N/A
- Draft ground coverage: 100.0%
- Draft material-fact coverage: 100.0%
- Unsupported assertion rate: 0.0%
- Validator detection rate: N/A
- Validator false-positive rate: N/A
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
  "run_a": [],
  "run_b": [],
  "dropped_without_check": [],
  "added": [],
  "passed": true,
  "detail": "all still-valid A grounds remain in B",
  "note": "This baseline cannot inspect InvalidationRecord if the ground is absent; any drop is treated as a silent remove."
}
```

## Validator mutation report

```json
{
  "status": "N/A",
  "reason": "no released draft/pack available"
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
| REG_no_notice | VALIDATION | COMPLETE | PASS | — | — | NO_APPEAL_RIGHT |
| REG_payment_keying | VALIDATION | COMPLETE | PASS | — | KB-PAY-01, KB-KEY-01 | RELEASED |
| REF_lu3440789 | VALIDATION | NOTICE_ONLY | PASS | — | — | MANUAL_REVIEW |
| REF_lu3489734 | VALIDATION | NOTICE_ONLY | PASS | — | — | MANUAL_REVIEW |

## Remaining defects (ranked)

No ranked defects on scored layers.

## Fine-tuning decision

Fine-tuning is **not** warranted from this baseline. Ground-truth volume is small (10 COMPLETE, none client-signed for final wording). Architecture and rules must finish passing on an approved set before any model task (narrative → controlled facts, or locked plan → polished drafting) is a candidate. Do not fine-tune unrestricted ground selection.

## Confirmations

- No case-specific application fixes were made during this baseline.
- Blind holdout was not used for prompt, rule, KB, or parameter tuning.
- Architecture was not redesigned.
- No production deploy.
