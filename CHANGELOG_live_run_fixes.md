# Change log: fixes from the live run over the customer uploads

All of these were found by running the client's real uploads through the live pipeline: real OpenAI calls, the store reloaded before every step, and the same neutral account and answer policy for every case. Builds on Package A (`ec4a54e`). Every fix is generic: no operator-, notice- or case-specific rule.

## 1. The front of one notice and the back of another became one letter

| | |
|---|---|
| **What** | The upload gate refuses pages that print a different registration or charge number (`different_notices`), with its own message. Each notice page's references are read in a separate one-document call (`page_references` prompt v2). |
| **Root cause** | Nothing compared the pages. The joint classification call was also seen copying one page's registration onto the other. |
| **Tolerances** (set from repeated live reads) | <ul><li>Two readings count as different only when more than 60% of their characters differ. On one blurred notice, the two sides read up to 4 of 7 characters apart; two different notices differed in all 7.</li><li>A charge-number reading is compared only if it is mostly digits and within 2 characters of the other reading's length. This excludes footer form codes and long barcode numbers, both of which refused genuine pairs live.</li></ul> |
| **Blast radius** | `notice_completeness.py` (`different_notices`, `notice_pages_sufficient`, `assess_notice_sides`), `intake/classifier.py`, `prompts.yaml`, `llm.py` (model preference), and `api.py`: the rejection message, plus an audit record of which field differed. |
| **Risk** | <ul><li>A false refusal costs the customer a re-upload.</li><li>A smaller misread is accepted and shown on the confirmation screen.</li><li>If the per-document read fails, the joint reading is kept.</li></ul> |

## 2. Two identical photos were refused with the wrong message

Identical image bytes are refused first, as `duplicate_front_images`, with a message saying the same photo was uploaded twice. Before this, the side labels ran first and gave "only front pages".

## 3. A PCN misread from a photo went unchallenged

If the classifier's reading of the PCN disagrees with the extractor's, the PCN is marked UNCERTAIN for the customer to check on the confirmation screen, and the audit records `pcn_read_disagreement`. This deliberately does **not** set `pcn_conflict`: that hold asks for both sides, which would be the wrong instruction here.

## 4. The customer was asked about internally derived facts

Questions about computed facts (`account_contradicts_allegation`, `pofa_*`, `ntk_*`, the duration and window facts, `jurisdiction` and others) are dropped.

## 5. Letters printed ISO dates

The letter and the PDF render dates as "19 September 2026". The "Grounds cited" footer is removed from the PDF (issue #5).

## 6. Para 9(2)(f) keeper-liability warning

| | |
|---|---|
| **What** | `pofa.scan_keeper_warning` tests for both statutory parts: the driver's name and address are not known, **and** there is a right to recover from the keeper. When the notice's **text** (a PDF text layer or OCR) lacks them and both sides are present, the case gets `ntk_defect_keeper_warning`. That feeds the already-approved KB-POFA-05 and PP-POFA-005B. |
| **Not relied on** | The extractor's yes/no reading of a **photo**. Live, on a blurred reverse, the model returned statutory wording that the page does not print. That reading is recorded in the trace and never pleaded. |

## 7. Some AI letters ended without asking for anything

If an AI letter has no cancel request, the approved closing (PP-END-001 and PP-END-002, already ACTIVE) is appended before validation, so it is validated like the rest. The audit records `closing_added`. A mention of cancelling that asks for nothing ("not an automatic cancellation ground") doesn't count as a request.

## 8. Quoting the notice's own allegation held a sound letter

VAL-RES accepts a quotation that reproduces a verified fact, such as the allegation as printed. Before this, three attempts were refused and the case ended in PROCESSING_ERROR.

## Considered and not changed

- **Asking for the site postcode when it can't be read.** This would conflict with the deliberate, tested rule (`test_question_authority.JurisdictionIsNeverAskedAutomatically`) that unknown jurisdiction withholds the PoFA grounds instead of asking. That is a decision for the client.
- **`detail: "high"` on images.** Tried, and repeated reads were no better on these photos. Reverted.

## Tests

- `tests/test_live_findings.py`: 22 tests.
- **Full suite:** 506 run. 23 failures and 6 errors, the same 29 tests as the baseline, compared by test id.
- **Live:** see the report given to the client.

## Deployment

Not deployed. In addition to the Package A checklist:
- `store sync` for prompt versions: extraction v10, classification v2, page_references v2.
- No schema change.
- Rollback: redeploy the previous build and re-sync the previous release.
