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
- Blind holdout scored: 4 (not used for tuning)

## End-to-end

- Passed cases: 2
- Failed cases: 2
- End-to-end pass rate: 50.0%
- COMPLETE pass rate: 0.0%
- NOTICE_ONLY pass rate: 100.0%
- BLIND_HOLDOUT pass rate: 50.0%

## Aggregate metrics

- Extraction accuracy (injected landing): 100.0%
- Legal-critical extraction accuracy: 100.0%
- Fact precision: 100.0%
- Fact recall: 100.0%
- Narrative fact precision: 0.0%
- Narrative fact recall: 0.0%
- Question precision: N/A
- Question recall: N/A
- Legal finding accuracy: N/A
- KG retrieval precision: N/A
- KG retrieval recall: N/A
- Ground precision: N/A
- Ground recall: N/A
- Claim Plan coverage: N/A
- SupportBundle completeness: N/A
- DraftContext coverage: N/A
- Draft ground coverage: 20.0%
- Draft material-fact coverage: 50.0%
- Unsupported assertion rate: 0.0%
- Validator detection rate: N/A
- Validator false-positive rate: N/A
- Outcome consistency (scored outcome layer): 0.0%
- Repeatability (semantic stability): N/A

## Architecture invariants

- Additive-ground pair (['REG_late_ntk', 'REG_late_ntk_anpr']): PASS — all still-valid A grounds remain in B
- Claim Plan persist/reload: see per-case reports (Master Case + SQLite store).
- PROCESSING_ERROR on a case that already failed draft/validation is not a hard-invariant breach. The invariant is: Claim Plan PASS + Draft PASS + Validation PASS + no exception ⇒ PROCESSING_ERROR impossible.
- Repeatability of authoritative semantic state: N/A

## First defective layer

- `NARRATIVE_FACT_ERROR`: 1
- `OUTCOME_STATE_ERROR`: 1

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
| REG_breakdown | BLIND_HOLDOUT | COMPLETE | FAIL | NARRATIVE_FACT_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05 | RELEASED |
| REG_residential | BLIND_HOLDOUT | COMPLETE | FAIL | OUTCOME_STATE_ERROR | KB-POFA-01, KB-POFA-04, KB-POFA-05, KB-RES-03, KB-REC-01 | RELEASED |
| REF_new1936102153896 | BLIND_HOLDOUT | NOTICE_ONLY | PASS | — | KB-POFA-01 | MANUAL_REVIEW |
| REF_sp62712518 | BLIND_HOLDOUT | NOTICE_ONLY | PASS | — | KB-POFA-01, KB-POFA-05, KB-POFA-02 | RELEASED |

## Remaining defects (ranked)

1. **P0** — OUTCOME_STATE_ERROR: 1 case(s): REG_residential
2. **P2** — NARRATIVE_FACT_ERROR: 1 case(s): REG_breakdown

## Fine-tuning decision

Fine-tuning is **not** warranted. Remaining errors include architecture or deterministic-rule failures. Fix those first. Do not fine-tune raw notice + narrative → unrestricted legal ground selection.

## Confirmations

- No case-specific application fixes were made during this baseline.
- Blind holdout was not used for prompt, rule, KB, or parameter tuning.
- Architecture was not redesigned.
- No production deploy.
