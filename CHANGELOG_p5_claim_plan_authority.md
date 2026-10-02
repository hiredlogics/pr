# P5 — Claim Plan Authority and Decision Freeze

Branch `feature/p5-claim-plan-authority`, built on P4b (`82be840`). Not pushed.

## WHAT

There is now one decision object per case version: the **Final Claim Plan**.
It is the only source of truth for:

- which claims are argued (SUPPORTED, with a priority);
- which are not (REJECTED, with the reason);
- what is still open (UNRESOLVED: missing facts or required evidence).

Once built, it is confirmed and **LOCKED**.

- Drafting receives only the locked plan, the verified facts and the uploaded
  evidence.
- Validation refuses any argument that is not in the plan.
- Upstream engines still run, but they only *propose*.

## WHY

Before P5, the claims could change at six points between analysis and the
letter:

1. Case analysis.
2. Its bounded reassessment.
3. The analysis-stage veto.
4. The pack-time reasoning gate and conflict resolution.
5. Orchestrator ground recovery.
6. Orchestrator widening.

None of these recorded a single answer to "what does this letter argue, and
why". The drafter was also given more than the decision: the analysis-stage
claim plan (including excluded and omitted modules) and the customer's raw
wording.

## ROOT CAUSE

"Which claims" was an emergent property of a chain of engines, each of which
could filter or reorder. It was never a stored object, so it could not be:

- locked;
- versioned;
- compared between runs;
- checked against the draft.

## BEFORE / AFTER ARCHITECTURE

```
BEFORE
  facts -> knowledge match -> case analysis (+ reassessment) -> analysis veto
        -> case.analysis_module_ids
        -> orchestrator ground recovery (may re-analyse and change the ids)
        -> reasoning.analyse: re-gate (R-03 / R-01) + conflicts (R-04) + order
           -> pack (could silently drop a selected ground)
        -> drafter (pack + raw customer texts + old claim plan incl. excluded modules)
           -> on no_ground: reasoning.analyse(..., widen=True) again (re-gated)
        -> validation (module_refs ⊆ pack.module_ids)

AFTER
  facts + knowledge relationships + evidence
        -> CASE INTELLIGENCE ............ proposes (analysis, reassessment, veto, ground recovery)
        -> CLAIM PLAN BUILDER ........... decides (engines/claim_plan_authority.py)
        -> FINAL CLAIM PLAN -> confirm -> LOCK   (persisted, versioned, immutable)
        -> reasoning.pack_for(plan) ..... approved wording for the plan's claims only
        -> DRAFTING ..................... locked plan + verified facts + uploaded evidence
           -> on no_ground: pack_for(plan, widen=True), same claims
        -> VALIDATION ................... VAL-PLAN: every argument must be in the plan
```

## DELIVERABLES

### 1. DB migration: `infra/migrations/0006_claim_plan_authority.sql`

The migration is mirrored in `infra/postgres_schema.sql`.

**`claim_plans` table**
- Identity: `claim_plan_id` (uuid PK), `case_id` (NOT NULL, FK cases), `analysis_run_id` (uuid NOT NULL), `run_number`, `version` (UNIQUE per case).
- Status: one of DRAFT, CONFIRMED, LOCKED, SUPERSEDED.
- Timestamps and supersession: `created_at`, `confirmed_at`, `locked_at`, `superseded_at`, `superseded_by`.
- Digests: `inputs_digest`, `plan_digest`.
- Trust columns: `code_version`, `kb_version`, `prompt_version`, `model_version`, `facts_used`, `relationships_used`, `trust`, `material_fact_accounting`.
- A partial unique index allows at most one LOCKED plan per case.

**`claim_plan_items` table**
- `item_id`, `claim_plan_id`, `ordinal`, `knowledge_id`, `module_id`, `claim_type`.
- `status`: one of SUPPORTED, REJECTED, UNRESOLVED.
- `decision`, `reason` (NOT NULL), `priority`, `topic`.
- `supporting_facts`, `evidence_refs`, `relationships` (all jsonb).

**The lock in the database** (plpgsql triggers)
- Items are never updated.
- No item can be inserted into a LOCKED or SUPERSEDED plan.
- A locked plan's row allows only LOCKED → SUPERSEDED (with `superseded_at` / `superseded_by`).
- `tests/sqlite_store.py` mirrors the tables, the triggers and the foreign keys.

**Rollback** is in the file header.

### 2. Claim Plan model: `pcn_appeal/engines/claim_plan_authority.py`

**`ClaimPlanItem`** is a frozen dataclass. Its facts, evidence and relationships are read-only mappings and tuples. Each item records:
- `module_id` and `knowledge_id` (the same id as P4/P4b);
- `claim_type` (the route);
- `status`;
- `decision`: one of SELECTED, BLOCKED, NOT_ACTIVE, GATE, VETOED, NO_SUPPORTING_FACTS, EVIDENCE_REQUIRED, NOT_SELECTED, MISSING_FACTS;
- `reason`: why it was selected or rejected;
- `supporting_facts`: what supports it, including any derived-from facts;
- `evidence_refs`, `relationships` (edge ids) and `priority`.

**`FinalClaimPlan`** provides:
- views: `supported`, `supported_ids`, `rejected`, `unresolved`, `required_evidence()`;
- `for_drafting()`: approved claims only;
- `for_validation()`;
- `trace()`: admin only;
- `explain(module_id)`;
- `as_dict()` and `from_dict()`. `from_dict()` re-verifies `plan_digest`, so a tampered stored plan is refused.

**`ClaimPlanBuilder`** is the single builder.
- **Input:** Case Intelligence's proposals as recorded on the case (`analysis_module_ids` plus the latest `case_analysis` audit), the P4 knowledge match, the verified facts and the uploaded evidence.
- **A claim is SUPPORTED only if all of these hold:**
  - Case Intelligence selected it. The plan never adds a ground.
  - It is in force and not BLOCKED.
  - The reasoning gate keeps it. This is `ReasoningEngine.eligibility`, the same R-03 / R-01 / R-04 function the pack used.
  - At least one verified fact or uploaded evidence supports it.
  - Evidence it cannot be argued without has been uploaded. Otherwise it is UNRESOLVED.
- **Everything else** is recorded with its reason: vetoed proposals, every blocked module, and offered candidates that were not chosen.
- **Priority** comes from the existing KB-GOV-07 ordering (`_drafting_priority`).

`diff(a, b)` answers "what changed between runs": claims added, removed or changed; facts added, removed or changed; and version changes.

### 3. Lock mechanism

- The lifecycle is DRAFT → `confirm()` → CONFIRMED → `lock()` → LOCKED → `supersede()` → SUPERSEDED.
  - `confirm()` checks that every item has a reason, that supported items have supporting facts, and that priorities run 1..n.
- After `lock()`, any attribute change raises `ClaimPlanLockedError`, except the LOCKED → SUPERSEDED transition. This covers items, trust, digests, adding an item, and moving status back.
- `ClaimPlanBuilder.decide(case)` handles versions:
  - If the inputs digest is unchanged, the locked plan is reused (`claim_plan_reused`). The same facts give the same plan.
  - Otherwise it builds version n+1, confirms and locks it, and marks version n SUPERSEDED. Version n's content is never touched.
  - The audit `claim_plan_locked` event carries the trace and the diff.
- `store/cases.py` handles persistence: `_save_claim_plans` / `_load_claim_plans`.
  - Each plan is written CONFIRMED with its items, then superseded or locked in version order. This order keeps the foreign key and the one-LOCKED index valid.

### 4. Removed decision points

"They may propose. Only Claim Plan decides."

| Location | Before | After |
|---|---|---|
| `analysis.py` `_finalize_claims` + bounded reassessment | its selection was what got drafted | proposal; also records `candidates` in the audit so the plan can account for them |
| `engines/claim_plan.py` `build_claim_plan` | "finalized claims" | analysis-stage veto; its exclusions become REJECTED / VETOED items |
| `reasoning.analyse` gate + conflicts | silently dropped selected grounds at pack time | extracted to `eligibility()`, applied once by the builder and recorded as REJECTED / GATE; `pack_for(plan)` never re-gates |
| `reasoning._drafting_priority` | ordered the pack | sets the plan's `priority`; the pack uses the plan's order |
| orchestrator `_analyse_until_a_ground_can_lead` / ground recovery | changed the selection right before drafting | proposal phase, completed before the lock |
| orchestrator widen on `no_ground_reason` | `reasoning.analyse(case.analysis_module_ids, widen=True)` re-gated whatever the ids were by then | `pack_for(plan, widen=True)`: more approved wording, same locked claims |
| `case_context.claim_plan` | analysis-stage plan, including excluded and `omitted_gate_satisfied` modules | `plan.for_drafting()`: approved claims only |
| `_without_failing_sentences`, `_with_closing`, TemplateDrafter | remove sentences / add STRUCTURAL closing / substantive fallback already disabled | unchanged: none can add a claim, and the trimmed draft is re-validated against the plan |

`reasoning.analyse(selected_ids)` is kept for direct engine use and for
existing tests. Its output is identical to before.

### 5. Drafting integration

- `drafter.drafting_payload(pack)` builds the drafter's whole input.
- `case_context.customer_source_texts` (the customer's raw narrative and answers) is withheld.
  - It stays in the pack for VAL-CUSTOMER-COPY.
- `module_ids`, `context_chunks` and `prohibited_claims` come from the plan's SUPPORTED claims only.
- The drafting prompt is unchanged (v10).

### 6. Validation integration

`ValidationEngine(..., kg=)` adds **VAL-PLAN** (validator version `VAL-3`). It blocks:

- a `module_ref` outside the plan: "`ANPR not approved in Claim Plan`";
- approved wording of a module outside the plan, whatever the sentence cites. This uses 8-word windows of block text, and wording shared with plan modules is ignored;
- a pack whose modules are not the plan's;
- a plan that is not LOCKED.

Messages name the claim family, never the rejected module id, because they are
fed back to the drafter.

### 7. Tests: `tests/test_claim_plan_authority.py` (33 tests)

| Spec | Test |
|---|---|
| 1 supported module → plan item | `SupportedModuleCreatesPlanItem` (KB-ANPR-01 SUPPORTED, `multiple_visits=true`; every item says why; evidence-required → UNRESOLVED, then SUPPORTED in v2 once a PHOTO is uploaded) |
| 2 unsupported claim → validation fail | `UnsupportedClaimFailsValidation` (ANPR ref fails with the exact message; ANPR wording under a POFA ref fails; POFA in plan passes; a rogue drafter's ANPR paragraph never ships) |
| 3 locked plan cannot mutate | `LockedPlanCannotMutate` + DB triggers in `PlanSurvivesDatabaseReload` |
| 4 new customer fact → v2 | `NewCustomerFactCreatesVersion2` (V1 POFA → payment answer → V2 + PAY-01; V1 SUPERSEDED, items byte-identical; diff names the fact) |
| 5 same facts → same plan | `SameFactsSamePlan` (two cases: equal digests and trace; regeneration reuses the plan, same letter) |
| 6 rejected module never reaches drafting | `RejectedModuleNeverReachesDrafting` (drafting payload contains no rejected module id, no raw narrative, no `customer_source_texts`) |
| 7 plan survives DB reload | `PlanSurvivesDatabaseReload` (round-trip; v1+v2 supersession; first-time save of both versions; DB refuses edits; tampered plan refused on load) |
| client trust | `ClientTrust` (code / KB / prompt / model versions, facts used with source, relationship edge ids, explain in/out, blocked trace) |
| authority | `OnlyThePlanDecides` (never adds a ground; drafting refuses an unlocked plan; widen keeps claims) |
| admin | `AdminRoutes` |
| review: shared boilerplate | `SharedBoilerplateIsNotAnArgument` (generic requests and every structural block pass under six plan shapes; wording shared with an approved module passes; positive control: the same shared wording fails when neither module is approved) |

**Mutation checks**

Each mutant was run against the test file and then reverted. Each was caught:

| Mutant | Tests that failed |
|---|---|
| VAL-PLAN disabled | 4 |
| raw text sent to the drafter | 1 |
| evidence rule removed | 1 |
| reuse disabled | 1 |

### 8. Admin trace

Routes, all admin-only:
- `GET /admin/cases/{id}/claim-plans`: every version, with its trace.
- `GET /admin/cases/{id}/claim-plans/explain?module_id=`: why it was included or excluded.
- `GET /admin/cases/{id}/claim-plans/compare?a=&b=`: what changed between runs.
- `/cases/{id}/trace` and `/cases/{id}/facts` gain a `claim_plan` key.

Trace lines read like this:

```
Selected KB-BAY-02 (priority 1), because fact restricted_bay_alleged=true (...), fact account_contradicts_allegation=true (from child_occupant_present=true), relationship SUPPORTS
Blocked KB-ANPR-01, reason: wrong evidence type (the notice relies on an attendant or photo observation, not ANPR)
Blocked KB-PAY-01, reason: payment allegation absent (a prohibition cannot be answered by payment or grace)
Unresolved KB-SIGN-02, reason: evidence required before it can be argued: PHOTO
```

### 9. Change log

This file.

## BLAST RADIUS

**Changed:**
- `orchestrator._generate`: plan → lock → `pack_for`, and the widen path.
- `reasoning.py`: `eligibility` extracted, `pack_for` added, `case_context.claim_plan`.
- `drafter.py`: payload builder.
- `validation.py`: VAL-PLAN; version VAL-3.
- `models.py`: `CaseFile.claim_plans`, `RetrievalPack.claim_plan`.
- `store/cases.py`: plans are saved and loaded with the case.
- `api.py`: admin routes.
- `analysis.py`: one audit key.

**Intended behaviour changes:**
- A claim whose every approved paragraph needs an enclosure is not argued until that evidence is uploaded. In practice this is KB-SIGN-02 without a PHOTO. It used to reach the drafter with no approved wording.
- A selected claim whose gate holds without any present fact is rejected (NO_SUPPORTING_FACTS). No current module or scenario triggers this.
- The drafter no longer receives `customer_source_texts`. The prompt's rule about them is now vacuous. The prompt wording is deliberately unchanged.

## NOT CHANGING

- Fact Graph and FactManager.
- Knowledge Graph and relations (`kg/`, `kb_relations.yaml`) and the P4b ingestion layer.
- Question Authority.
- Legal modules (`legal/pofa.py`, `code_versions`).
- Drafting prompt wording: prompts are unchanged, so their versions are unchanged.
- Service routing and intake.
- PROCESSING_ERROR vs NO_SUPPORTED_GROUNDS: the outcome logic is unchanged and the plan is built in both cases.
- Driver disclosure (tri-state).

## TEST PLAN AND EVIDENCE

**Full suite**

```bash
env -u OPENAI_API_KEY DATABASE_URL= LLM_PROVIDER= APP_ENV= .venv/bin/python -m unittest discover -s tests
```

- Result: 725 tests (after the review follow-up).
- Failures: the same 29 as the baseline (23 F + 6 E). No new failures, none fixed.
- `tests/test_claim_plan_authority.py`: 33/33 pass.
- Repeated runs (review): each of the six scenarios run 5 times in fresh pipelines gives one distinct result (plan digest, inputs digest, approved claims, state, letter hash). Local deterministic stand-in only; the real model was not run.

**Before/after through `generate()`**

Six scenarios, comparing 82be840 with this branch:

- The pack, the state and the outcome are identical in all six.
- The letter SHA-256 is identical in all six.

| Scenario | Plan | Outcome |
|---|---|---|
| 1 parent & child + child answer | BAY-02 + POFA-01 | RELEASED |
| 2 photo notice + multiple visits | POFA-01 | NO_SUPPORTED_GROUNDS (ANPR blocked) |
| 3 payment made | PAY-01 + POFA-01 | RELEASED |
| 4 no parking + payment made | POFA-01 | NO_SUPPORTED_GROUNDS (PAY blocked) |
| 5 overstay, nothing said | POFA-01 | NO_SUPPORTED_GROUNDS |
| 6 ANPR overstay + multiple visits | ANPR-01 + POFA-01 | RELEASED |

**P1 conflict probe:** unchanged (1 known).

## RISKS / OPEN

- **Not run against real Postgres.** The plpgsql triggers, the partial unique index and the FK ordering are exercised only through the SQLite mirror.
- **Deploy order matters.** The migration must be applied before the code. With a database enabled, `store.save` writes `claim_plans`, so without the tables every case save fails.
- **The VAL-PLAN wording check is a heuristic.** It flags a sentence when 8-word windows match a non-plan module's approved wording and the plan's own modules don't share them. No suite regressions so far. Watch live for false positives on closely paraphrased shared boilerplate.
- **`analysis_module_ids` is not persisted** (pre-existing). A reloaded case re-analyses at generate, and the plan is then built from those fresh proposals. A new plan version is minted only if the inputs differ.
- **Traces are verbose.** They list every offered or blocked candidate, so the console may want filtering by status.
- **Extra retrieval.** One retrieval pass runs in the proposal phase and another for the plan pack. Cost only.

## DEPLOYMENT

1. Apply `infra/migrations/0006_claim_plan_authority.sql`. It is additive and idempotent.
2. Deploy the code.
3. No data backfill is needed. Existing cases get v1 on their next generate.

Nothing is pushed or merged: that needs explicit confirmation. Stopping for
P5 review, with no drafting improvements until Claim Plan Authority is verified.
