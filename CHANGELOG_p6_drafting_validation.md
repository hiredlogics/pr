# P6 — Enterprise Drafting and Validation Intelligence

Branch `feature/p6-drafting-validation-intelligence`, built on P5.5 (`2682d0b`). Not pushed.

## WHAT

The drafter is now a controlled translator: it turns an approved, LOCKED Claim Plan into customer language and nothing else.

Pipeline: Claim Plan → `DraftContext` → LLM draft → sentence grounding → validation (existing VAL-* plus new DV-*) → release.

| Spec item | Where |
|---|---|
| 1. DraftContext | `pcn_appeal/drafting/context.py`. Frozen dataclass built only by `DraftContext.from_pack`. `drafting_payload` delegates to it. |
| 2. Pipeline | `orchestrator.py`: `draft_context` audit event, draft loop, `_validate`, `_record_version`. |
| 3. Sentence grounding | `engines/draft_validation_engine.py`. Per sentence: `GROUNDED` / `STRUCTURAL` / `UNGROUNDED` with claim_plan_items, facts, evidence. Ungrounded sentences go back to the drafter as feedback, then are trimmed. |
| 4. Customer language | Drafting prompt v11 plus `fact_basis` and `driver_rule` in the payload. Customer-account facts must be attributed ("The keeper's account is that…"). |
| 5. Validation engine | `DraftValidationEngine` (DV-1): DV-FACT, DV-CLAIM, DV-GROUND, DV-EVIDENCE, DV-DRIVER, DV-LEGAL, DV-STRUCTURE, DV-LEAK, DV-ACCOUNT. Merged with the existing engine's result. |
| 6. Shadow judge | `drafting/shadow_judge.py`. Off by default (`SHADOW_JUDGE=1`). Returns PASS / WARNING / ERROR, never blocks, never raises. |
| 7. `draft_versions` | Migration `0008_draft_versions.sql`, mirrored in `postgres_schema.sql`; `drafting/versions.py`; store save/load. |
| 8. Regression | `integrity/journeys.py` `snapshot` / `compare` / `regressed`; CLI `--golden DIR`, `--update-golden`; `journeys/golden/*.json`. |
| 9. Tests | `tests/test_draft_intelligence.py`, 44 tests. |

### DraftContext

| Allowed | Withheld (stripped at any depth) |
|---|---|
| LOCKED claim plan, approved claims only | full knowledge base |
| verified facts, each with `fact_basis` DOCUMENT / CUSTOMER_ACCOUNT / DERIVED | rejected, blocked and candidate modules |
| approved evidence and evidence index | customer narrative, free text, `customer_source_texts` |
| guidance chunks for approved modules plus structural wording | internal trace, audit, recovery |
| case metadata allowlist | confidence and scores |
| driver status and `driver_rule` | |

`audit()` records ids and a SHA-256 of the payload, never text. Payload size on the reference case: 11099 → 10975 bytes.

### Draft versions

`draft_id` is a uuid5 of case, plan and content hash, so regenerating the same draft yields the same row. `version` is per case. Content, hash, plan, model, prompt version and created_at are immutable in the database (trigger). `released` can be set once and never reverted. Columns also carry validation status, issues, grounding and shadow-judge result.

## WHY

P5 fixed which claims may be argued. It did not control how the drafter turns them into words. Wording could still blur "the keeper says" into fact, state a driver's actions, claim payment from an attempt, or assert a legal conclusion with no basis. There was also no durable record of which text was produced under which plan.

## ROOT CAUSE

1. The drafter received a pack that carried more than it needed, including free-text provenance.
2. Validation checked claims by module reference but not sentence-level grounding, attribution or legal conclusions.
3. Drafts were not persisted as versions. Reloading a case could not prove it was the same draft.
4. Validator feedback echoed rejected module ids back to the drafter, a small leak of withheld material.

## BLAST RADIUS

- Drafter input loses `free_text_provenance` and `recovery`, and gains `fact_basis` and `driver_rule`. Prompt versions: drafting 10 → 11, validation 2 → 3.
- Validation is stricter. DV-ACCOUNT and DV-GROUND can reject drafts the earlier engine accepted. The retry, feedback and trim loop absorbs this; the real model has not been run (see below).
- Validator feedback now says "an unapproved claim" instead of a KB id.
- New table only. `save()` upserts draft versions; `load()` reads them.
- Audit and trace gain `draft_context`, `draft_validation`, `draft_version`, `shadow_judge` events; new integrity check `DRAFT_VERSION_RECORDED`; report gains a Drafts section; `/admin/cases/{id}/audit` gains `regression`.

## NOT CHANGING

- Fact Graph, Knowledge Graph, Question Authority, Claim Plan selection, routing, document classification.
- `PROCESSING_ERROR` stays distinct from `NO_SUPPORTED_GROUNDS`.
- Driver disclosure stays tri-state. The spec's CONFIRMED_DISCLOSED is the code's `FORMALLY_IDENTIFIED`. The narrative never sets identification.
- No operator- or PCN-specific wording or rules.
- Shadow judge does not gate release.

## TEST PLAN

Evidence:

| Check | Result |
|---|---|
| `test_draft_intelligence.py` | 44 / 44 OK |
| Full suite | 793 tests, the same 29 known failures as baseline, none new, none fixed |
| Before/after, 6 reference scenarios vs P5 (`baaf8fd`) | identical states and letter hashes |
| Repeat, 5 runs × 6 scenarios | 1 distinct result each |
| 5 shipped journeys vs golden snapshots | all PASS |
| Mutation checks | DV checks emptied: 17 failures + 1 error. DV not merged: 2. Context strips nothing: 2. Versions not recorded: 7 + 6 errors. All caught; originals restored. |

Spec test cases:

1. Parent/child includes the children fact and does not identify the driver: `ParentChildCase`.
2. Payment attempt does not say payment completed: `PaymentAttempt` (DV-FACT).
3. ANPR multiple visits only after a confirmed fact: `AnprMultipleVisits`.
4. Injected unsupported argument fails validation and the feedback carries no KB id: `UnsupportedArgumentIsRefused`.
5. Reload gives the same draft version; regenerate is idempotent; a new plan gets a new version; the database refuses edits: `DraftVersions`.

## BEFORE / AFTER

Drafter payload on the same case (top-level keys only differ):

| | P5 | P6 |
|---|---|---|
| case_context | includes `free_text_provenance`, `recovery` | both removed |
| added keys | none | `fact_basis`, `driver_rule` |
| chunk modules | KB-BAY-02, KB-POFA-01 | KB-BAY-02, KB-POFA-01 |

Validation of candidate sentences against the same plan (children fact present, ANPR claim not approved):

| Candidate sentence | Before (P5 engine) | After (P6) |
|---|---|---|
| "The keeper's account is that children were present in the vehicle." | passes | passes, GROUNDED |
| "Children were present in the vehicle." | passes | DV-ACCOUNT |
| "I parked and my children were with me." | passes | DV-ACCOUNT, DV-DRIVER |
| ANPR "two separate visits" sentence | caught by claim check only | DV-CLAIM, UNGROUNDED |
| "The notice is unlawful and unenforceable." | not checked | DV-LEGAL |
| "The tariff was paid in full." | not checked | DV-FACT |
| "Relying on KB-BAY-02." | caught by leak check | DV-LEAK |

"Before" for rows 1 to 3 and 6 reflects the rules that existed in P5; the P6 column was measured by running the new engine on each sentence.

## DEPLOYMENT

1. Apply `infra/migrations/0008_draft_versions.sql` (additive, after 0006 and 0007). Rollback: drop `draft_versions` and its trigger.
2. Deploy. No config change. Optional: `SHADOW_JUDGE=1` to run the judge on released drafts.
3. Staging smoke with the real model:
   `python -m pcn_appeal.integrity journeys journeys/ --golden journeys/golden --base-url https://<staging> --admin-token … --out reports/`
   Expect `TEXT_ONLY` draft differences (fine) and no `STRUCTURE` differences.
4. Not run, needs you: migrations 0006, 0007, 0008 on PostgreSQL staging; the 50 golden cases (only the 5 shipped journeys are snapshotted); a real-model run to see whether DV-ACCOUNT and DV-GROUND raise the retry rate.
