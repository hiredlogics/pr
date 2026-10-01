# Change log: Package A, seven safety defects

These are the defects from the client's issue list where wrong or unsafe output could reach a customer. Builds on `2a3f811`. All fixes are generic: no operator-, notice- or case-specific rule.

## 1. An analysis failure was told to the customer as "no supported grounds" (#41, #15)

| | |
|---|---|
| **What** | The case-analysis call is retried once. Each attempt records `case_analysis_completed` or `case_analysis_error`. When the latest attempt failed and nothing is selected, the case is held as **PROCESSING_ERROR** ("not a judgment on the strength of your case", can continue), not NO_SUPPORTED_GROUNDS. `classify_hold` checks this before any no-supported-grounds path. |
| **Root cause** | `analysis.py` caught the exception and returned an empty selection. The orchestrator could not tell "found nothing" from "never ran", so it recorded `analysis_complete_no_supported_grounds`. |
| **Blast radius** | `engines/analysis.py` (`_call`, `CaseAnalysis.analysis_failed`), `orchestrator.py` (empty-selection branch), `engines/outcome.py` (`analysis_failed`). A genuine empty analysis is still NO_SUPPORTED_GROUNDS. |
| **Found while testing** | `test_outcome_boundary.GenerateShortCircuit` passed the KnowledgeGraph where the LLM goes, so analysis always crashed. The test asserted "no supported grounds", which means it encoded this bug. It is corrected and gains a failure counterpart. |

## 2. PoFA was SATISFIED with nothing seen (#20, #21)

| | |
|---|---|
| **What** | `pofa_9_2_e_status` is SATISFIED only when the pass-to-driver invitation was positively found, in the text or by an explicit extraction flag. If it was not seen, the status is UNRESOLVED and the reverse side is requested. |
| **Root cause** | `recovery.py` computed `defect = has_pass is False`. When only the name-driver flag was set and no text was readable, `has_pass` was None, so `defect` was False and the status was SATISFIED. |
| **Blast radius** | `_assess_ntk_schedule4_content` only. DEFECT_IDENTIFIED still needs both sides. |

## 3. Weak PCN and VRM integrity check (#25)

| | |
|---|---|
| **What** | The PCN must appear as a whole token. Any token one or two edits away from it is blocked as an altered PCN. Any capitalised plate-like token one or two edits from the VRM is blocked, whatever its shape, spaced or unspaced. The old current-format check is kept. |
| **Root cause** | The PCN check was a substring test, so "1234567" passed inside "12345678". The VRM check only looked at `AB12CDE`-shaped tokens, so a misread such as `RX7V5FP` for `RX7V5PP` was never compared. |
| **Blast radius** | `validation.py` document-level checks. Tokens must contain a digit (plus a letter if the identifier has one), and plates must be in capitals, so words, dates, times and Code section numbers don't match. |

## 4. An attempted payment became "a payment was made" (#28)

| | |
|---|---|
| **What** | `payment_made` now means a completed payment only. "I was paying" no longer sets it, and a negated account ("I never paid") is ignored. The proposition reads "a parking payment was made for the visit". The question vocabulary reads "Was a parking payment completed for this visit?" Attempts and failures stay with the existing `payment_attempt_failed` fact and its PP-PAY-003/004/005 wording. |
| **Root cause** | One fact meant both "made" and "attempted", but PP-PAY-001 asserts the payment was made. |
| **Blast radius** | The `payment_made` free-text rule, its proposition and its question text. No KB module changes. |

## 5. Any written answer became True (#8)

| | |
|---|---|
| **What** | How a written answer opens decides its value. "No, ..." gives False and "Yes, ..." gives True. An uncertain answer ("not sure", "don't know", "can't remember", "maybe") sets no fact, and the raw wording is kept for the account engine. On a yes/no question, "maybe" is no longer recorded as "No". A written answer with no stated yes or no still records that an account was given, as before. |
| **Root cause** | `questioning.py` stored True for any prose answer, so "No, the children were not in the car" became True. |
| **Blast radius** | `QuestionEngine.record_answer`. `test_uploads.BoolAnswers` documented the "maybe is No" behaviour. That test now uses a non-answer, and a new test covers "maybe". |

## 6. The bay template inferred an "interval" from the operator's record (#23, #26)

| | |
|---|---|
| **What** | PP-BAY-001 and the KB-BAY-01 core proposition are now a neutral request to prove the breach. They state the recorded times, say those times don't show how the bay was used, and ask for every photo and record relied on, with timestamps. KB-BAY-01 moves to v1.4. |
| **Root cause** | The approved wording said "a record spanning only that interval does not of itself establish the breach". The letter therefore inferred what the operator observed, which the notice doesn't show. It was seen in the last live letter. |
| **Not changed** | The selection gate (`observation_window_min ≤ 5`), and KB-BAY-02. |
| **Needs approval** | **The new wording is pending client approval.** |

## 7. Two photos of the front counted as both sides (#2)

| | |
|---|---|
| **What** | The classifier (prompt v2) labels every page FRONT, REVERSE, CONTINUATION, BLANK or UNKNOWN. When pages are labelled, two distinct photos that are both FRONT are refused at upload (`only_front_pages`), and any non-front page counts as the other side. A blank back is accepted. If nothing is labelled, the old rule applies unchanged. The text fallback now needs two different kinds of reverse wording, so a front citing "Protection of Freedoms Act 2012, Schedule 4" no longer passes as a reverse. |
| **Root cause** | Completeness counted distinct image hashes. A second photo of the face has different bytes. |
| **Blast radius** | `intake/classifier.py` (`pages`, normalised and range-checked), `notice_completeness.py` (`notice_pages_sufficient`, `_page_sides`), the private-parking upload gate, and the classification prompt (v1 to v2). The charge-certificate route is unchanged. |
| **Risk** | If the model labels a genuine reverse as FRONT, the customer is asked for the other side. The cost is one more upload, not a wrong letter. |

## Tests

- **New:** `tests/test_package_a_safety.py`, 26 tests covering all seven defects. Updated: `test_outcome_boundary.py` (+1) and `test_uploads.py` (+1).
- **Against the old code** (HEAD exported to a scratch folder): 25 failures and 1 error across all seven defect classes. The new failure counterpart in `test_outcome_boundary` also fails there.
- **Full suite:** 486 run, 23 failures, 6 errors, 7 skipped. The failure set is identical to the baseline (the same 29 stale tests), compared by test id.
- **Live** (real OpenAI, the original WebP photos, nothing case-specific, store reloaded before every step):
  - **Front and back.** The classifier labelled the pages FRONT and REVERSE. The case went to RELEASED with grounds BAY and POFA, and the letter uses the new put-to-proof wording. There is no interval, instant or EV wording. Every reload was identical, and letter.pdf returned 200 before and after the reload.
  - **Front twice** (the same face re-encoded and cropped, so the bytes differ). Refused at upload with HTTP 422, `NOTICE_SIDES_REQUIRED` / `only_front_pages`, and the both-sides message. The old code accepted this.
  - **Not exercised live:** an analysis provider failure (covered by unit tests), the payment and answer paths (this notice asked no questions), and identifier alteration (the drafted letter was correct).

## Deployment

Not deployed. In addition to the earlier checklist (`store init`, rebuild with Pillow):

- **KB sync.** KB-BAY-01 is now v1.4 and the classification prompt v2, so the release drift check refuses to start until the release is re-synced (`python -m pcn_appeal.store sync`).
- **Rollback.** Redeploy the previous build and re-sync the previous release. There are no schema changes in this package.
