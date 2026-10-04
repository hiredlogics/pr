# P10 Defect Register

Authority: P9 baseline (`reports/p9/P9_EVALUATION_REPORT.md`, per-case reports,
dataset `p9_v1`). Split and goldens are immutable. Blind holdout is not used
to choose or tune fixes.

Priority = SEVERITY × FREQUENCY × LEGAL_RISK.

---

## D10-01 EXTRACTION_TIME_SECONDS

```
defect_id: D10-01
layer: EXTRACTION_ERROR
affected_case_ids (DEVELOPMENT): REF_stn1947529, REF_88811908015,
  REF_00347261120013, REF_00347261100014
affected_also (not used to tune): REF_lu3440789, REF_lu3489734,
  REF_new1936102153896
frequency: 7 / 19 (4 development)
severity: P2
reproducibility: 100% (deterministic `_hhmm`)
root_cause: TIME_FIELDS normalisation captures only HH:MM and drops seconds.
  Injected ANPR times `12:59:17` land as `12:59`. Legal-critical fields were
  accurate; ordinary time fields were not.
architecture_component: engines/extraction.py `_hhmm` / `_minutes`
first_correct_state: extractor/injection supplies HH:MM:SS
first_incorrect_state: Fact Graph stores HH:MM
proposed_fix: generic time parser preserves optional seconds; duration uses
  full precision. No PCN/operator conditions.
regression_tests: test_p10_remediation.P10TimePrecision
```

**Trace:** INPUT `12:59:17` → extraction `_hhmm` → Fact `12:59` (incorrect).

---

## D10-02 GROUND_SELECTION_EXTRA_POFA

```
defect_id: D10-02
layer: GROUND_SELECTION_ERROR
affected_case_ids (DEVELOPMENT): REG_late_ntk, REG_late_ntk_anpr
frequency: 2
severity: scored P1 (incorrect ground) — after root-cause: GOLDEN_REVIEW_REQUIRED
reproducibility: 100%
root_cause: P9 expected a closed set {KB-POFA-02} only. The pipeline also
  selected KB-POFA-01 (unidentified keeper threshold), KB-POFA-05 (conclusion
  after a verified PoFA finding), and KB-POFA-04 (document-confirmed content
  gate). Those modules fire from published use_when, not from a LATE-specific
  leak. KB-POFA-02 recall is 100%. Suppressing them would be golden-matching.
architecture_component: KB use_when (kb_modules.yaml) + Claim Plan authority
proposed_fix: NO CODE CHANGE. Flag GOLDEN_REVIEW_REQUIRED for the LATE
  expected.supported_grounds closed set.
regression_tests: none (no behavioural change)
```

**Trace:** INPUT late postal dates → FIRST CORRECT: `POFA_POSTAL_LATE` VERIFIED,
`KB-POFA-02` supported → FIRST “INCORRECT” only vs a narrow golden, not vs KB
gates.

---

## D10-03 VALIDATOR_MISS_PLACEHOLDER

```
defect_id: D10-03
layer: VALIDATION_ERROR
affected_case_ids: P9 mutation `unresolved_placeholder` on REG_late_ntk draft
frequency: 1 / 9 mutation classes
severity: P1 (unresolved placeholder must fail)
reproducibility: 100%
root_cause: VAL-LEAK / DV-LEAK match `{{` / `}}` and `[image N]` only.
  `{OPERATOR_NAME}` and `[PLACEHOLDER]` are not matched.
architecture_component: engines/validation.py LEAK;
  engines/draft_validation_engine.py PLACEHOLDER
proposed_fix: generic unresolved-placeholder patterns (single-brace tokens,
  [PLACEHOLDER|TODO|TBD]).
regression_tests: test_p10_remediation.P10ValidatorPlaceholder
```

---

## D10-04 VALIDATOR_MISS_INVENTED_FACT

```
defect_id: D10-04
layer: VALIDATION_ERROR
affected_case_ids: P9 mutation `invented_fact` on REG_late_ntk draft
frequency: 1 / 9 mutation classes
severity: P1 (unsupported assertion)
reproducibility: 100%
root_cause: no deterministic check that a sentence asserting a permit/bay/
  ticket identifier must match a verified fact value.
architecture_component: engines/validation.py
proposed_fix: VAL-INVENTED — identifier assertions require a matching
  verified-fact value. Generic ontology (permit/bay/ticket/voucher/reference).
regression_tests: test_p10_remediation.P10ValidatorInventedFact
```

---

## D10-05 VALIDATOR_MISS_WRONG_CALCULATION

```
defect_id: D10-05
layer: VALIDATION_ERROR
affected_case_ids: P9 mutation `wrong_calculation` on REG_late_ntk draft
frequency: 1 / 9 mutation classes
severity: P1 (incorrect legal calculation)
reproducibility: 100%
root_cause: VAL-PARTICULARS requires the correct day count to be present; it
  does not refuse a contradictory day count. The P9 mutation (14→3) was also
  a no-op when the letter particularised by dates only.
architecture_component: engines/validation.py
proposed_fix: VAL-CALC — a stated day-count that contradicts a verified
  finding's calculation is a BLOCK.
regression_tests: test_p10_remediation.P10ValidatorWrongCalculation
```

---

## D10-06 VALIDATOR_MISS_MISSING_GROUND

```
defect_id: D10-06
layer: VALIDATION_ERROR
affected_case_ids: P9 mutation `missing_ground` on REG_late_ntk draft
frequency: 1 / 9 mutation classes
severity: P2 (missed relevant ground in letter)
reproducibility: 100% against the weak mutation
root_cause: DraftValidationEngine VAL-COVERAGE already requires one grounded
  sentence per approved module. The P9 mutation dropped a single sentence of
  a multi-sentence ground, so coverage still held.
architecture_component: eval/p9/mutations.py; DraftValidationEngine VAL-COVERAGE
  (already present). Engine-4 VAL-COVERAGE against route labels was tried and
  rejected — it caused residential/payment regenerate regressions.
proposed_fix: honest missing-ground mutation (remove every sentence of the
  first non-STRUCTURAL module). Do not duplicate coverage in Engine 4.
regression_tests: test_p10_remediation.P10ValidatorMissingGround
```

---

## D10-07 KEYING_FACT_NOT_PROMOTED (validation; fix after development)

```
defect_id: D10-07
layer: KNOWLEDGE_RETRIEVAL_ERROR (first fail) — root layer is NARRATIVE_FACT
affected_case_ids: REG_payment_keying (VALIDATION)
frequency: 1
severity: P2
reproducibility: 100% on P9 harness
root_cause: "I paid on the app but typo in reg" sets payment_made via
  CircumstanceRule, but no ontology rule writes keying_error_type. Question
  Authority did not ask, so the pre-supplied answer never landed. KB-KEY-01
  use_when requires payment_made AND keying_error_type=MINOR.
architecture_component: engines/account.py CircumstanceRule
proposed_fix: generic narrative → keying_error_type MINOR for registration
  typo/keying language. Not a PCN/operator rule. Implement after development
  time/validator fixes.
regression_tests: test_p10_remediation.P10NarrativeKeying
status: IMPLEMENTED. P10 validation re-run: KB-KEY-01 and KB-PAY-01 both
  selected (recall 100%). First fail advanced to GROUND_SELECTION_ERROR
  because extra KB-POFA-01/04/05 fire from published use_when — same class
  as D10-02. Flag GOLDEN_REVIEW_REQUIRED for the closed {PAY, KEY} set.
  Do not suppress PoFA modules.
```

---

## Held (not fixed in P10)

| ID | Layer | Cases | Reason |
| --- | --- | --- | --- |
| D10-08 | NARRATIVE_FACT_ERROR | REG_breakdown | BLIND HOLDOUT — record only |
| D10-09 | DRAFT_ERROR | REG_residential | BLIND HOLDOUT — record only |
| D10-02 | GROUND_SELECTION | LATE pair | GOLDEN_REVIEW_REQUIRED |

---

## Implementation status

| ID | Status |
| --- | --- |
| D10-01 | Done — DEV NOTICE_ONLY extraction 4/5 now pass E2E (stn advances to DRAFT) |
| D10-02 | GOLDEN_REVIEW_REQUIRED — no code |
| D10-03 | Done — mutation detected |
| D10-04 | Done — mutation detected |
| D10-05 | Done — mutation detected |
| D10-06 | Done — honest mutation + existing DV VAL-COVERAGE |
| D10-07 | Done — KEY retrieved on VAL; extra PoFA → GOLDEN_REVIEW |
| D10-08 | Held (blind) |
| D10-09 | Held (blind; first-fail moved to OUTCOME_STATE on re-score) |

Full before/after: `P10_REMEDIATION_REPORT.md`.

## Ranking used for implementation

1. D10-03 / D10-04 / D10-05 — P1 validator legal-safety
2. D10-01 — P2 systemic extraction (4 development cases)
3. D10-06 — P2 validator coverage
4. D10-07 — P2 validation-split narrative ontology (after 1–3)
5. D10-02 — no code; golden review
6. D10-08 / D10-09 — holdout, next cycle
