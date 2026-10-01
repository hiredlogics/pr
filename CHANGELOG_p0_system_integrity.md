# P0: system safety, version control and decision integrity

Branch `feature/p0-system-integrity` (from `563f6e9` + review doc `9a14419`). Not merged, not pushed, not deployed.

**Unchanged by design:**
- Claim Plan logic, the question engine, drafting strategy, PoFA rules, the knowledge modules, service routing, and the OFR/CCJ/Bailiff services.
- `claim_plan.py` only swaps two string literals for the enum, which compares equal.

**Mapping to the architecture review:** D1 is fixed in P0.2 and D3 in P0.1. D11 is partly addressed by P0.5 (manifests persisted).

## P0.1 Customer output security

| | |
|---|---|
| **What** | <ul><li>`customer_safe.py` adds:<ul><li>`customer_question`, which keeps only `fact`, `text`, `type` and `options`;</li><li>`scrub`, which drops internal keys at any depth and redacts internal-ID shapes (`KB-…`, `PP-/AI-XXX-NNN`, `VAL-…`, `RULE-…`, `MODULE-…`, `SCOP-…`) from strings;</li><li>`leaks`, used by the tests.</li></ul></li><li>A middleware runs `scrub` over every JSON body on the customer routes, including errors. Any removal is logged as a leak.</li><li>The endpoints themselves now build clean payloads:<ul><li>Questions go through `customer_question`.</li><li>Ingest, upload and confirmation flags go through `_customer_flags`.</li><li>`/confirmation` no longer returns `facts` (confidence and source references). That moved to the new admin route `GET /cases/{id}/facts`, and the console uses it.</li><li>The 422 upload refusal drops `reason`, which is routing logic.</li><li>The PDF 503 drops the exception text, which goes to the audit instead.</li><li>The postcode question drops `material_because`; its `unlocks` stay in the audit.</li></ul></li><li>VAL-LEAK also refuses any `INTERNAL_ID` shape in a letter (or PDF) sentence. The validator version moves to VAL-2.</li></ul> |
| **Why / root cause** | <ul><li>The orchestrator's postcode question bypassed the analysis sanitiser, so it carried "needed to apply KB-POFA-02" (D3).</li><li>`/confirmation` returned confidence and source references to the public proxy.</li><li>Raw flags were returned on two paths.</li></ul> |
| **Blast radius** | <ul><li>`api.py` and `console.html`.</li><li>`validation.py`: one rule widened.</li><li>`orchestrator.py`: one key removed.</li></ul> |
| **Not changing** | <ul><li>Every field the frontend reads, including `rejected[].reason`, `route`, `detail.message` and `code`.</li><li>`/health`. It is an ops endpoint and still shows versions (see risks).</li></ul> |

## P0.2 Route registry

| | |
|---|---|
| **What** | <ul><li>`routes.py` adds `Route(str, Enum)`:<ul><li>the 9 service routes, with `Route.CCJ` as an alias of `CCJ_REMOVAL`;</li><li>the 21 KB ground routes;</li><li>`SERVICE_ROUTES`, `GROUND_ROUTES` and `GENERAL_GROUND_ROUTES = {POFA, LANDOWNER}`.</li></ul></li><li>`KnowledgeGraph._build` calls `validate_ground_routes`. A module route, or a `routes.yaml` key, that is not registered raises `UnknownRouteError`, so the process does not start. This applies to both the YAML and Postgres-release sources, and `ALLOW_KB_DRIFT` cannot override it.</li><li>The router's constants are defined from the enum and validated at import.</li><li>Every raw route literal comparison has been replaced, in `analysis`, `recovery`, `reasoning`, `drafter`, `claim_plan`, `validation` and `services/validators`. A test fails if one is reintroduced.</li></ul> |
| **Root cause (D1)** | <ul><li>`("POFA", "LAND")` never matched LANDOWNER, so `_fact_specific_path_open` treated the always-on landowner module as a fact-specific ground.</li><li>The `KB-LAND` id-prefix backstop hid this in two other places.</li></ul> |
| **Behaviour change** | <ul><li>With only POFA or LANDOWNER grounds selected, analysis's own `operator_ata` and `site_postcode` questions now go through their materiality gates (`_ata_would_unlock` and `postcode_unlocks`) instead of being dropped.</li><li>The suite is unchanged, and the live runs are unchanged (see below).</li></ul> |

## P0.3 Run isolation

| | |
|---|---|
| **What** | <ul><li>`CaseFile.run_id` and `run_status` (NONE, OPEN or COMPLETED).</li><li>`case.audit` is a `RunAudit` list that stamps `run_id` on every entry, however the list was assigned.</li><li>A run starts (`ensure_run`) at intake, ingest, confirm, answer, auto_appeal and generate, if none is open. A paused run (one waiting for answers) is continued, not restarted.</li><li>`_with_outcome` closes the run with its outcome, for both released and held results.</li><li>`classify_hold` reads only the current run's audit.</li><li>A case with no run, from before this change, is treated as a single run, so its outcome is unchanged.</li></ul> |
| **Root cause** | `classify_hold` built its events from the whole audit. A `draft_error` or `ground_recovery` wipe in an earlier run decided every later outcome: PROCESSING_ERROR for ever. |
| **Unchanged** | `analysis_failed` still reads the most recent analysis attempt across runs. The module IDs a later `/generate` reuses come from that attempt. |

## P0.4 Fact ownership and provenance

| | |
|---|---|
| **What** | <ul><li>`CaseFile.put` records every write in `fact_history`: fact, previous value, new value, previous status, new status, source kind and ref, run, UTC time, reason, and outcome (APPLIED, CONFLICT or RETRACTED).</li><li>A write is refused, and recorded in `fact_conflicts` plus a `fact_conflict` audit entry, when either of these holds:<ul><li>**A.** A machine reading (EXTRACTED, DERIVED, UNCERTAIN, or an inference from free text) would replace a different value the customer confirmed, corrected or answered.</li><li>**B.** Ownership (`fact_ownership.py`) forbids it:<ul><li>the customer's account cannot rewrite a DOCUMENT- or EVIDENCE-owned value read from a document;</li><li>a document reading cannot rewrite a CUSTOMER-owned value the customer gave.</li></ul></li></ul></li><li>An explicit customer act always applies, so a customer can still correct a misreading.</li><li>Placeholders (`None`, `""`, `UNKNOWN`, `[]`) are never conflicts.</li><li>A same-value write at a lower status is not applied, so a confirmation is never demoted.</li><li>The confirmation screen's status promotion (`set_status`) and the account engine's deletions (`retract`) are recorded too.</li></ul> |
| **Evidence it does not block existing flows** | <ul><li>Instrumented full suite: 0 conflicts fired.</li><li>Live: 0 conflicts on both cases.</li></ul> |

## P0.5 Version recording

| | |
|---|---|
| **What** | <ul><li>`manifest.py` builds a manifest for every completed run (released or held) with:<ul><li>case and run;</li><li>backend commit, build ID, environment, frontend version;</li><li>KB release ID, a digest of the modules and blocks loaded, and each argued module's version;</li><li>every prompt version;</li><li>provider and model per task;</li><li>the drafter's class, model, prompt version and attempt;</li><li>the validator version and judge;</li><li>digests of the facts and evidence;</li><li>the state, outcome, module IDs and `letter_sha256`.</li></ul></li><li>It is attached in `generate` and stored on `AppealOutput.manifest`, in the audit (`execution_manifest`) and in `drafts.manifest`.</li><li>It is visible on `/trace` and the admin `/appeal`, and scrubbed from customer output.</li><li>The Next proxy sets `X-Frontend-Version` from `VERCEL_GIT_COMMIT_SHA` or `NEXT_PUBLIC_APP_VERSION`, and strips any value the browser sent.</li><li>The backend sanitises the value to 64 characters and stores it as `cases.frontend_version`.</li></ul> |
| **Reproducibility** | The manifest pins code, KB, prompts and models. `drafts.retrieval_pack` already holds the exact drafting context. `letter_sha256` shows whether a replay produced the same letter. |

## Database migration

The migration is `infra/migrations/0001_p0_system_integrity.sql`. The same statements are appended to `infra/postgres_schema.sql`.

Everything is additive and idempotent (IF NOT EXISTS), and the rollback SQL is in the file header:
- `cases`: `current_run_id`, `run_status` and `frontend_version`;
- `audit_log.run_id`, plus an index;
- `drafts`: `run_id` and `manifest`;
- a new `fact_history` table, append-only, with a CHECK on `outcome`.

`tests/sqlite_store.py` mirrors all of it.

**The migration must run before this build serves traffic**, because `save()` writes the new columns. An older build runs unchanged against the migrated schema.

## Tests

`tests/test_p0_system_integrity.py` has 32 tests:

| Area | Tests |
|---|---|
| P0.1 | 11 |
| P0.2 | 5 |
| P0.3 | 4 |
| P0.4 | 8 |
| P0.5 | 4 |

The P0.6 required set is all in that file:
1. An internal KB reason is generated (by the pipeline and by a mocked pipeline), and the API output is clean.
2. LANDOWNER matches `Route.LANDOWNER`, and an unknown route stops the load.
3. Run 1 gives NO_SUPPORTED_GROUNDS and run 2 hits an API failure, giving PROCESSING_ERROR. The reverse also holds: an old failure does not decide a new completed run, and combining the runs reproduces the old bug.
4. Overwriting a customer fact records a CONFLICT and keeps the value, and this survives a reload.
5. A generated letter, and a hold, carry a manifest that is persisted with the draft.

**Full suite:** 545 run, 23 failures and 6 errors. These are the same 29 tests as the pre-existing baseline, compared by test ID, and no new failures.

**Live** (OpenAI, SQLite store with a reload before every step, the same neutral answer policy as before):

| Case | Outcome | Grounds | Run | Conflicts | Leaks | Manifest | Reloads |
|---|---|---|---|---|---|---|---|
| Bay notice | RELEASED | BAY + POFA (as before) | 1, COMPLETED | 0 | 0 | full | identical |
| Overstay notice | NO_SUPPORTED_GROUNDS | as before | 1, COMPLETED | 0 | 0 | full | identical |

## Remaining risks

1. **`/health` is public through the proxy** and reports the commit, prompt versions and validator version. That is system metadata, not case output. Before production, decide whether the proxy should reduce it to `{provider, vision, modules, status}`.
2. **Unknown shapes.** The ID-redaction patterns cover the ID shapes in use today. An internal ID in a new shape would pass the scrub, although the key denylist and the endpoint-level construction still apply.
3. **Conflicts are not shown to the customer.** They are recorded and visible to admins, and the held value wins. Showing the conflict on the confirmation screen is a UX decision.
4. **Ownership lists are explicit** (`fact_ownership.py`). A new fact name falls back to origin-based ownership plus rule A, and needs adding to the right list.
5. **The manifest commit reads `git rev-parse HEAD`** when no platform variable is set. A dirty local tree reports HEAD, not the working tree. Railway and Vercel set the variable.
6. **The validation judge is still not wired** (D2). The manifest records `judge: null`, which is accurate.
7. **The store's `save()` needs the migration.** Deploying the code before the migration fails case saves.

## Deployment (when approved)

1. Review and merge `feature/p0-system-integrity`. This needs the user's approval and is not done.
2. On staging, apply `infra/migrations/0001_p0_system_integrity.sql` with `python -m pcn_appeal.store init` or `psql -f`. Check the new columns and `fact_history`.
3. Run `python -m pcn_appeal.store sync`. The prompt versions are unchanged since `563f6e9`, and VAL-2 is a code constant.
4. Deploy the backend to Railway and the frontend to Vercel.
   - `/health` shows `validator_version: VAL-2` and the new commit.
   - A test case's `/trace` shows a `manifest` with the Vercel `frontend_version`.
5. Repeat the bay and overstay checks on staging, then production.
6. **Rollback:** redeploy the previous build. The schema is additive, so it can stay.
