# P10.4 Sealed Holdout Report

Run once after validation. Holdout JSON expected blocks were not edited; labels applied from `pcn_appeal/eval/p10_4/holdout_labels.py`.

- Git commit: `be577e4111cefe0e9d22ad562aa1bf89d82542f2`
- E2E pass rate: 25.0%
- Semantic P/R: 100.0% / 33.3%
- Ground P/R: N/A / 0.0%
- LEGAL_CONCLUSION independent: 0
- SUPPORTING incorrectly in plan: 0

| case_id | family | e2e | first_fail | grounds |
| --- | --- | --- | --- | --- |
| HOLDOUT_A | mechanical_issue | FAIL | SEMANTIC | `[]` |
| HOLDOUT_B | payment_keying | FAIL | SEMANTIC | `[]` |
| HOLDOUT_C | loading_collection | PASS | — | `['KB-ACT-01']` |
| HOLDOUT_D | uncertainty_permit_site | FAIL | SEMANTIC | `['KB-REC-01']` |
