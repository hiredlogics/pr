# P10 — Evidence-based defect remediation

Authority: P9 baseline (`reports/p9/P9_EVALUATION_REPORT.md`). Goldens and
blind expected outputs were not edited. No production deploy.

P10 evaluation artefacts: `reports/p10/dev`, `reports/p10/val`, `reports/p10/blind`.
Defect register: `P10_DEFECT_REGISTER.md`.
Model decision: `P10_MODEL_IMPROVEMENT_DECISION.md`.

---

## 1. Defect register

See `P10_DEFECT_REGISTER.md`. Nine records (D10-01 … D10-09). Grouped by first
defective layer from the P9 first-fail table.

## 2. Ranked defect list

| Rank | ID | Layer | Priority | Action |
| --- | --- | --- | --- | --- |
| 1 | D10-03 | VALIDATION_ERROR | P1 | Implemented — `{TOKEN}` / `[PLACEHOLDER]` |
| 2 | D10-04 | VALIDATION_ERROR | P1 | Implemented — VAL-INVENTED |
| 3 | D10-05 | VALIDATION_ERROR | P1 | Implemented — VAL-CALC |
| 4 | D10-01 | EXTRACTION_ERROR | P2 | Implemented — time seconds |
| 5 | D10-06 | VALIDATION_ERROR | P2 | Implemented — honest missing-ground mutation; DV VAL-COVERAGE kept |
| 6 | D10-07 | NARRATIVE_FACT → retrieval | P2 | Implemented after DEV — keying CircumstanceRule |
| 7 | D10-02 | GROUND_SELECTION_ERROR | scored P1 | **No code.** GOLDEN_REVIEW_REQUIRED |
| 8 | D10-08 | NARRATIVE_FACT_ERROR | — | Holdout. Recorded only |
| 9 | D10-09 | DRAFT / OUTCOME | — | Holdout. Recorded only |

Supporting generic contracts (not case patches): DraftContext placeholder fill;
LLM-path one-argument merge for PAYMENT+KEYING; lease-clause particularisation
from DraftContext.

## 3. Root cause per defect class

| ID | First correct state | First incorrect state | Root cause |
| --- | --- | --- | --- |
| D10-01 | Injected `12:59:17` | Fact Graph `12:59` | `_hhmm` dropped seconds |
| D10-02 | `KB-POFA-02` + published companion modules | Scored as extra grounds | Closed golden `{KB-POFA-02}` vs published `use_when` |
| D10-03 | `{OPERATOR_NAME}` in letter | Validator pass | LEAK only matched `{{` / `[image N]` |
| D10-04 | Invented permit/bay id | Validator pass | No identifier-vs-fact check |
| D10-05 | Contradictory day-count | Validator pass | VAL-PARTICULARS required the right count; did not refuse a wrong one |
| D10-06 | Missing approved ground | Validator pass | Mutation deleted one sentence of a multi-sentence ground |
| D10-07 | Narrative “typo in reg” | `keying_error_type` absent | CircumstanceRule covered payment, not keying |
| D10-08 | Holdout breakdown facts | Narrative miss | Not remediated (blind) |
| D10-09 | Holdout residential draft/outcome | Draft then outcome miss | Not remediated against blind |

## 4. Components changed

- `pcn_appeal/engines/extraction.py` — `_hhmm` / `_time_parts` / `_minutes`
- `pcn_appeal/engines/validation.py` — VAL-4: VAL-INVENTED, VAL-CALC, wider LEAK
- `pcn_appeal/engines/draft_validation_engine.py` — `[PLACEHOLDER\|TODO\|TBD]`, case-sensitive `{TOKEN}`
- `pcn_appeal/engines/account.py` — generic `keying_error_type=MINOR` CircumstanceRule
- `pcn_appeal/drafting/context.py` — fill `{{placeholders}}` from verified facts
- `pcn_appeal/drafting/drafter.py` — PAYMENT+KEYING one-argument merge; lease particulars from the locked pack
- `pcn_appeal/eval/p9/mutations.py` — missing-ground removes every sentence of one module; wrong-calc injects a timing sentence
- `pcn_appeal/eval/p9/__main__.py` — `--split` / `--out` / `--no-p9-root` (does not overwrite P9 baseline)
- `tests/test_p10_remediation.py`

Not changed: KB modules, prompts, operator maps used as gates, P9 goldens,
blind expected outputs, Claim Plan builder version.

## 5. Generic fixes implemented

1. **Time ontology** — optional seconds kept; HH:MM-only notices do not invent seconds.
2. **Validator invariants** — unresolved tokens, invented permit/bay/ticket identifiers, contradictory day-counts.
3. **Narrative → keying fact** — typo / mis-key / wrong-reg language writes `keying_error_type=MINOR`.
4. **DraftContext fill** — `{{bay_reference}}` etc. resolved from verified facts / placeholder_map; unresolved tokens stay.
5. **One case theory** — when PAY and KEY are both drafted, restated payment sentences are dropped and paragraphs merge.
6. **Lease particulars** — verbatim lease clause and regulations confrontation from `lease_clauses` already on the pack.

Rejected: Engine-4 VAL-COVERAGE against route labels (residential/payment regenerate regressions). DV VAL-COVERAGE remains.

## 6. Tests added

`tests/test_p10_remediation.py` — failing case + near-miss + negative control per defect:

- `P10TimePrecision` / `P10ExtractionLanding`
- `P10ValidatorPlaceholder` / `InventedFact` / `WrongCalculation` / `MissingGround`
- `P10NarrativeKeying`
- `P10DraftContextPlaceholders`
- `P10OneArgumentMerge`

Existing `tests.test_scenarios` (A–D), `tests.test_keying_error`,
`tests.test_verified_legal_findings`, `tests.test_p8_architecture`,
`tests.test_draft_intelligence` golden snapshots, `tests.test_case_console` —
green after the one-argument gate (merge only when both PAY and KEY are present).

## 7. Development before / after

P9 DEV subset: 5/11 E2E (45.5%). P10 DEV: 8/11 (72.7%).

| Metric | P9 overall* | P10 DEV | Delta |
| --- | --- | --- | --- |
| E2E pass rate (DEV cases) | 45.5% | 72.7% | +27.2 pp |
| COMPLETE pass (DEV) | 4/6 (66.7%) | 4/6 (66.7%) | 0 |
| NOTICE_ONLY pass (DEV) | 1/5 (20%) | 4/5 (80%) | +3 cases |
| Extraction landing | 93.1% overall | 100.0% | + |
| Legal-critical extraction | 100% | 100% | 0 |
| Fact precision / recall | 100% / 91.6% | 100% / 100% | recall + |
| Narrative fact P/R | 66.7% / 66.7% | 100% / 100% | + (no holdout breakdown in DEV) |
| Ground precision / recall | 30.0% / 83.3% | 32.5% / 100% | recall +; precision still extra-PoFA |
| Claim Plan coverage | 83.3% | 100% | + |
| SupportBundle completeness | 83.3% | 100% | + |
| DraftContext coverage | 100% | 100% | 0 |
| Unsupported assertion rate | 0% | 0% | 0 |
| Validator detection | 55.6% (5/9) | **100% (9/9)** | +44.4 pp |
| Validator false positives | 0% | 0% | 0 |
| Outcome consistency | 50% | 100% | + |
| Additive-ground pair | PASS | PASS | 0 |

\* P9 published some metrics only in aggregate. DEV E2E is counted from the P9 case table.

DEV case movement:

| Case | P9 first fail | P10 |
| --- | --- | --- |
| REG_late_ntk / _anpr | GROUND_SELECTION | unchanged — GOLDEN_REVIEW |
| REG_overstay_* (3) | PASS | PASS |
| REG_notice_plus_payment | PASS | PASS |
| REF_00347261100014 | EXTRACTION | **PASS** |
| REF_00347261120013 | EXTRACTION | **PASS** |
| REF_88811908015 | EXTRACTION | **PASS** |
| REF_70377302 | PASS | PASS |
| REF_stn1947529 | EXTRACTION | DRAFT (layer advanced; support-only `KB-POFA-01`, no leading ground) |

## 8. Validation before / after

P9 VAL: 1/4 (25%). P10 VAL: 3/4 (75%).

| Metric | P9 VAL cases | P10 VAL | Delta |
| --- | --- | --- | --- |
| E2E pass | 1/4 | 3/4 | +2 |
| Extraction landing | 2 EXTRACTION fails | 100% | + |
| Fact P/R | — | 100% / 100% | |
| Narrative fact P/R | — | 100% / 100% | |
| Ground precision / recall | KEY missing | 40% / 100% | KEY recall restored; extra PoFA FPs |
| Claim Plan / SupportBundle | KEY incomplete | 100% / 100% | + |
| Unsupported assertion rate | 0% | 0% | 0 |

VAL case movement:

| Case | P9 | P10 |
| --- | --- | --- |
| REG_no_notice | PASS | PASS |
| REF_lu3440789 | EXTRACTION | **PASS** |
| REF_lu3489734 | EXTRACTION | **PASS** |
| REG_payment_keying | KNOWLEDGE_RETRIEVAL (no KEY) | GROUND_SELECTION — KEY+PAY present; extra PoFA vs closed golden |

## 9. Blind holdout (run once after freeze)

**Not used to choose or tune any fix.**

| Metric | P9 blind | P10 blind |
| --- | --- | --- |
| E2E pass | 1/4 (25%) | 2/4 (50%) |
| COMPLETE pass | 0/2 | 0/2 |
| NOTICE_ONLY pass | 1/2 | 2/2 |
| Extraction landing | 1 EXTRACTION fail | 100% |
| Fact precision / recall | — | 100% / 100% |
| Narrative fact P/R | — | 0% / 0% |
| Ground precision / recall | — | N/A (COMPLETE goldens N/A or failed earlier) |
| Claim Plan / SupportBundle / DraftContext | — | N/A |
| Draft ground coverage | — | 20% |
| Draft material coverage | — | 50% |
| Unsupported assertion rate | — | 0% |
| Outcome consistency | — | 0% |
| End-to-end pass rate | 25% | 50% |

| Case | P9 | P10 (recorded only) |
| --- | --- | --- |
| REG_breakdown | NARRATIVE_FACT | NARRATIVE_FACT (unchanged class) |
| REG_residential | DRAFT | OUTCOME_STATE (layer advanced; letter now RELEASED) |
| REF_new1936102153896 | EXTRACTION | PASS (time-precision side-effect) |
| REF_sp62712518 | PASS | PASS |

New holdout observations for the next evaluation cycle (not fixed here):
D10-08 narrative facts on breakdown; D10-09 residential outcome vs golden.

## 10. Regressions introduced

None on DEVELOPMENT cases that already passed P9.

- LATE pair first-fail unchanged (not a code regression).
- `REG_notice_plus_payment` draft-intelligence snapshot still matches after
  gating one-argument merge to PAY+KEY together.
- Scenario A–D pass.

## 11. Unresolved defects

- **D10-02 / payment_keying extra PoFA** — GOLDEN_REVIEW_REQUIRED. Published
  `KB-POFA-01/04/05` gates fire; suppressing them would be golden-matching.
- **REF_stn1947529 DRAFT** — NOTICE_ONLY, support-only `KB-POFA-01`, no leading
  ground, empty letter. Expected after extraction was repaired.
- **D10-08 / D10-09** — blind holdout; next cycle.

## 12–13. Model improvement / fine-tune

See `P10_MODEL_IMPROVEMENT_DECISION.md`.

**Fine-tuning recommendation: NOT YET**

---

## Confirmations

- No operator-specific patches.
- No PCN-specific patches.
- No blind-set tuning (blind run once after freeze).
- No golden expectations changed to obtain passing results.
- No architecture redesign. Claim Plan remains final authority.
- No production deploy.

**STOP after P10.**
