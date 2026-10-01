# P1: Fact Graph layer

Branch `feature/p1-fact-graph`, built on `feature/p0-system-integrity` (`9f3cc39`). Not merged, not pushed, not deployed.

**Unchanged by design:**
- legal rules and KB content;
- Claim Plan logic;
- the analysis question generator;
- drafting strategy;
- service routing;
- the extraction field loop and prompts (every prompt version is unchanged).

## Architecture

**Before (P0)**

`DOCUMENT → EXTRACTION → case.facts (a plain dict) → engines`.
- Each engine read the dict, and any engine could write it.
- Extraction set `.status` on fact objects directly.
- Tests and the store assigned and popped keys.
- `CaseFile.put` checked ownership, but an explicit customer act always applied, even over a confident document reading.
- The store kept an append-only `facts` table, with `superseded` rows standing in for history.

**After (P1)**

`DOCUMENT → EXTRACTION → FactManager → Fact Graph → reasoning / analysis / drafting / validation`.
- `case.facts` is a read-only `FactGraph`. Each fact is a node with a stable UUID and created/updated times.
- `__setitem__`, `pop`, `update`, `clear` and `del` raise `FactWriteError`, and `Fact` is frozen.
- The only writers are `FactManager.update_fact`, `set_status`, `retract`, `resolve_conflict` and `hydrate` (load only).
- `case.put`, `case.set_status` and `case.retract` delegate to FactManager.
- Engines still read through `case.get`, `case.has` and `case.fact_view()`. No engine keeps its own copy.
- Drafting reads `fact_view()` and the pack's `verified_facts`. Both exclude disputed and UNCERTAIN facts, so drafting reads verified facts only.

## What changed

| | |
|---|---|
| **Fact model** | <ul><li>`Fact` is frozen and gains `disputed`.</li><li>`usable` is now: not UNCERTAIN and not disputed.</li><li>The graph view maps the engine vocabulary to the spec's:<ul><li>`SourceType`: DOCUMENT, CUSTOMER_ANSWER, CUSTOMER_FREE_TEXT, EVIDENCE, CALCULATION, SYSTEM_DERIVED;</li><li>`GraphStatus`: CONFIRMED, EXTRACTED, DERIVED, UNKNOWN, DISPUTED, CONFLICT.</li></ul></li><li>The engine enums are kept, because renaming them would have meant editing every engine.</li><li>Aliases: `children_present` reads `child_occupant_present`, and `driver_disclosure` reads `driver_disclosure_to_operator`. The other aliases are in `fact_graph.ALIASES`.</li></ul> |
| **FactManager** (`fact_graph.py`) | <ul><li>`update_fact` checks, in order:<ol><li>resolve an open conflict;</li><li>same value: keep the stronger status, never demote;</li><li>a placeholder over a customer value: IGNORED;</li><li>ownership: may this source override the held one?</li><li>otherwise apply.</li></ol></li><li>Every write records history (previous, new, source type, changed_by, reason, outcome, run, time) and a source row.</li></ul> |
| **Conflict states** | <ul><li>**NEEDS_CONFIRMATION**: a customer contradicts a confident reading of a document-owned fact. The reading is kept and marked disputed, the customer is asked, and the case cannot be drafted.</li><li>**KEPT_EXISTING**, in any of these cases:<ul><li>a machine reading against a customer-settled value;</li><li>free text against evidence or a document;</li><li>a document reading against a customer-owned value.</li></ul></li><li>**RESOLVED**: the customer chose one of the two values, or an admin did.</li></ul> |
| **Orchestrator** | <ul><li>`_reanalyse` puts the confirmation questions before the analysis questions. Each is a choice between exactly the two values, modelled on the existing PCN-conflict question.</li><li>`generate` holds an unresolved NEEDS_CONFIRMATION as MANUAL_REVIEW, with the questions pending and an audit entry `held_needs_fact_confirmation`.</li><li>`classify_hold` maps that hold to NEEDS_FACTS, never PROCESSING_ERROR.</li></ul> |
| **Account engine** | <ul><li>Adds `customer_described_event` (DERIVED, CUSTOMER_FREE_TEXT) when the account gives a first-person description of the event, such as "I parked" or "we stopped", and the description isn't negated.</li><li>It is withheld from the drafter and from analysis questions (`withheld_from_drafter`, `INTERNAL_FACTS`), so drafting inputs are unchanged.</li><li>Free text never sets disclosure or identification. `driver_disclosure_to_operator` stays absent, which parses as UNKNOWN, and `keeper_route_blocked` stays false.</li></ul> |
| **Extraction** | The four direct `.status = UNCERTAIN` mutations are now `case.set_status(..., reason=...)`. |
| **Store** | <ul><li>`facts` has one row per node, upserted on `(case_id, fact_name)`. A retracted fact is `active = false` and keeps its id. Nothing is deleted.</li><li>`fact_history` is append-only and gains `fact_id`, `changed_by` and `source_type`.</li><li>`fact_sources` records every reading, accepted or not.</li><li>`fact_conflicts` is upserted by `conflict_id`.</li><li>`load` hydrates node ids, times and `disputed`, and revives date values.</li></ul> |
| **Fact API** (admin only, not customer routes) | <ul><li>`GET /cases/{id}/facts`: the existing `facts` list, plus `nodes`, `fact_sources`, `fact_conflicts` and `needs_confirmation`.</li><li>`POST /cases/{id}/facts`: either `{fact_name, value, reason}` through `update_fact` (`changed_by = admin`, with the same ownership rules) or `{conflict_id, value, reason}` through `resolve_conflict`. `reason` is required.</li><li>`GET /facts/{fact_id}/history`.</li></ul> |

**Root cause:**
- Facts had no single owner. Any engine, the store or a test could assign or mutate a fact without a record.
- A customer correction could silently replace a confident document reading. A machine placeholder could replace a customer's answer (spec test 2 failed under P0).

## Behaviour change (UX)

A customer correcting a value the system read confidently from the notice is no longer applied silently. This covers the VRM, PCN number, dates, amount, operator, location and allegation.
- The customer is asked one question: "We read X on your notice, but you gave Y. Which one is printed on the notice?"
- Until they answer, the case holds as NEEDS_FACTS.
- A reading the system itself marked UNCERTAIN can still be corrected directly.

This deliberately reverses P0's "an explicit customer act always applies". Two P0 tests were updated to the new rule.

## Database migration

The migration is `infra/migrations/0002_fact_graph.sql`, and its body is also in `postgres_schema.sql`. `db._split` now keeps `$$` DO blocks whole.

1. `fact_history` gains `fact_id`, `changed_by` and `source_type`. The outcome CHECK now allows APPLIED, CONFLICT, IGNORED and RETRACTED.
2. The old append-only `facts` (the table that has a `superseded` column) is renamed to `facts_v1`. It is kept, not dropped.
3. New tables are created: `facts`, `fact_sources` and `fact_conflicts`.
4. Backfill:
   - the current row of each `facts_v1` fact becomes a `facts` node;
   - every `facts_v1` row becomes a `fact_history` entry with reason `backfill_facts_v1`.

The rollback is in the file header.

**Run order:** apply the migration, then deploy. Code from this branch cannot read the old `facts` table, and P0 code cannot read the new one.

## Tests

**`tests/test_p1_fact_graph.py`** has 26 tests:

| Spec test | Result |
|---|---|
| 1. Document VRM vs customer VRM | A NEEDS_CONFIRMATION conflict. The held value is kept and disputed, and it is excluded from `fact_view`. |
| 2. Customer `children_present` vs an AI "unknown" | Kept and recorded as IGNORED. A contrary reading is KEPT_EXISTING. |
| 3. "I parked and my children were with me" | `customer_described_event=true`, `children_present=true`, disclosure UNKNOWN, no `driver_identified`, keeper route not blocked. A negated account sets nothing. |
| 4. Update → history | One node id across writes. The history has previous, new, source type, changed_by, reason, outcome, run and time. |
| 5. Reload | Identical Fact objects, nodes, history, sources and conflicts, including a retracted node's id and a typed date. Saving again writes nothing twice. |

The file also covers:
- direct writes refused;
- node-id stability;
- correcting a reading twice still needs confirmation;
- the confirmation question offers only the two values, with dates shown as "1 June 2026";
- the answer resolves the conflict as CONFIRMED or CORRECTED;
- the pipeline asks, holds as NEEDS_FACTS if the question goes unanswered, and proceeds once answered;
- the Fact API: GET, POST through FactManager, resolve, reason required, history, admin only, not a customer route.

**Updated tests:**
- `test_reload_persistence`: 3 tests now expect one row plus history.
- `test_pg_integration`: the supersede test is rewritten to expect one node, history and NEEDS_CONFIRMATION. It runs only where Postgres is available.
- `test_p0_system_integrity`: 4 tests updated for the new conflict keys and the NEEDS_CONFIRMATION rule.
- `test_keying_error`: `pop` becomes `retract`.

**Full suite:** 571 run (545 + 26), 23 failures and 6 errors. These are the same 29 tests as the pre-existing baseline, compared by test ID, and there are no new failures.

**Conflict probe** (every `put` across the existing suite, excluding the P0/P1 files):
- One conflict fired: `test_a_corrected_jurisdiction_is_not_overwritten_by_the_postcode`. The customer answers a postcode different from the confident reading, so it is NEEDS_CONFIRMATION, as intended. The test's assertion still holds.
- 0 IGNORED.

**Live** (OpenAI, SQLite store with a reload before every step, the same neutral answer policy as P0):

| Case | Result |
|---|---|
| Bay | RELEASED, BAY + POFA, as in P0. 0 conflicts, 0 leaks, manifest present, reloads identical. |
| Overstay | Varies from run to run, on P0 code as well as P1 (see below). 0 conflicts and 0 leaks in every run. |

The overstay result depends on whether the model reads the site postcode. A paired run of the same images at the same time, with the extraction response probed:
- P0 code: no postcode returned, so jurisdiction UNKNOWN and NEEDS_FACTS (the existing `held_needs_site_postcode` hold).
- P1 code: postcode returned, so RELEASED with POFA + LANDOWNER.

Earlier P1 runs missed the postcode, and an earlier P0 run read it. P1 does not change the extraction prompt, call or field loop.

## Remaining risks

1. **Migration ordering.** Deploying before the migration fails every case save. A rollback after new cases are written needs the rollback SQL, because `facts_v1` does not receive new writes.
2. **Live postcode read is unstable** (shown above). This was there before P1, and it decides overstay outcomes. It is out of P1 scope and is a candidate extraction item.
3. **UX for the confirmation question.** The frontend renders it as an ordinary choice question, with no new component. It has not been checked in the browser.
4. **Ownership lists are explicit** (`fact_ownership.py`). A new document-printed fact name has to be added there, or a customer correction of it applies directly.
5. **History volume.** The account engine retracts and re-adds its free-text facts on every analysis round. That is now visible as RETRACTED/APPLIED pairs in `fact_history`, a few rows per round. Correct, but noisy.
6. **Admin writes are customer-strength.** A `POST /facts` value counts as an answer. It cannot overwrite a confident document reading without resolving the conflict, which is intentional.
7. **Manifest commit.** Locally it still reports HEAD (`9f3cc39`) while the tree is uncommitted (P0 risk 5).
8. **The Knowledge Graph work has not been started**, as instructed.

## Deployment (when approved)

1. Review `feature/p1-fact-graph`. It depends on P0, so merge P0 first.
2. On staging:
   - take a backup;
   - apply `0001`, then `0002`;
   - check that `facts` row counts equal the distinct `(case_id, name)` counts of the current rows in `facts_v1`;
   - check that the backfilled history count equals the `facts_v1` row count.
3. Deploy the backend, then repeat the bay and overstay checks, plus a VRM-correction case to see the confirmation question.
4. **Rollback:** use the SQL in the `0002` header, then redeploy P0.
