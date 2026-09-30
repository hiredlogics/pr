# Change log — release control and case provenance

Consolidated fix plan §5 (issue 32) and §8 (issues 13, 33). Scope is release
control only: no legal content, no KB module or block wording changed.

## Proven cause

Four separate ways the running system could not say what it was:

1. `store/cases.py` wrote `model=None` and the literal `"VAL-1"` for every case
   ever validated. `drafts.prompt_version` existed in the schema and was never
   written at all. A stored case could name its KB release and still not say
   what wrote the letter or which rules cleared it.
2. `prompts.use_release()` was never called anywhere in production. A KB release
   pins a prompt version per task and `kb_source.load_release()` dutifully
   fetched them — then nothing applied them, so the app ran the YAML prompts
   while stamping every case with a release that named different ones.
3. `api._load_kg()` caught **any** exception loading the published release and
   silently served the authored YAML with a printed warning. Production could
   argue different law from the release its cases were recorded against.
4. Nothing in the running process reported the deployed commit. During the
   earlier outcome-boundary incident the deployed commit was reconstructed by
   eye (`CHANGELOG_outcome_boundary.md`: "no SHA in meta") — which is the
   mechanical reason the client kept retesting versions nobody could identify.

## Exact changes

| Component | Change |
|---|---|
| `version.py` | **New** — deployed commit from `GIT_COMMIT` / Railway / Vercel / GitHub env, else the working tree, else `"unknown"`. Never raises |
| `engines/validation.py` | **New** `VERSION` constant — the validator owns its own version instead of the store hardcoding one |
| `models.py` | `Draft` carries `model` and `prompt_version` — the letter and what produced it are only auditable together |
| `drafting/drafter.py` | `LLMDrafter` records the resolved drafting model, falling back to the client class name so a demo stand-in's letter is never read back as a real provider's |
| `orchestrator.py` | `_without_failing_sentences` carries provenance onto the trimmed draft (it is the one that gets released) |
| `store/cases.py` | `save_output` writes real `model` / `prompt_version` / `validator_version`; `new_case` records `commit_sha` + `llm_provider` |
| `store/kb_source.py` | **New** `release_differs_from_yaml()` — compares served module and prompt versions against the authored YAML |
| `api.py` | `_load_kg()` refuses to boot on a release that exists but cannot load, or that drifts from the YAML; applies the release's pinned prompts; `ALLOW_KB_DRIFT=1` is the explicit, logged override |
| `api.py` `/health` | Adds `commit`, `kb_source`, `kb_source_note`, `kb_drift`, `prompt_versions`, `validator_version` |
| `infra/postgres_schema.sql` | `cases.commit_sha`, `cases.llm_provider` + `ALTER TABLE ... IF NOT EXISTS` so populated databases get them (`init_schema()` skips an existing CREATE TABLE) |
| `tests/test_release_provenance.py` | **New** — 16 tests |

## Before / after

| | Before | After |
|---|---|---|
| Stored model | `None`, always | The model that drafted, or the stand-in's name |
| Stored prompt version | never written | `prompts.version("drafting")` |
| Stored validator version | literal `"VAL-1"` | `validation.VERSION` |
| Released prompts | fetched, then ignored | applied via `use_release()` |
| Release fails to load | silently serves YAML | refuses to boot |
| Release drifts from YAML | undetected | refuses to boot, naming each mismatch |
| "Is my fix deployed?" | unanswerable | `GET /health` → `commit` |

## Blast radius

- **Behavioural change at boot**: with `DATABASE_URL` set, an unreachable
  release table or a drifted release now **stops the API** instead of serving
  YAML. Deliberate, and the reason this work exists. `ALLOW_KB_DRIFT=1`
  restores the old behaviour explicitly and logs that it did.
- **Prompts now actually follow the release.** If the published release pins an
  older prompt than the deployed YAML, boot fails until it is republished —
  previously the app ran the YAML prompt regardless. Republish before deploying:
  `python -m pcn_appeal.store sync --publish`.
- **Schema**: two nullable columns. Existing rows read `NULL`, which is truthful
  (those cases genuinely did not record a build).
- No customer-facing surface changed. No KB module, block or legal wording
  changed. No drafting behaviour changed.

## Known gap this does NOT close

No block in `building_blocks.yaml` declares a `version`, so `kb_sync` pins every
block at `"1.0"` and a wording change re-syncs over the same row. Block text
drift is therefore invisible both to `release_differs_from_yaml()` and to replay:
a "pinned" release cannot reproduce the block wording it was published with.
Fixing it means versioning ~74 blocks and changing sync semantics — KB content
work, tracked separately, and deliberately not smuggled in here.

## Tests run

- `tests.test_release_provenance` — 16 tests, OK. Both `StoredProvenance` tests
  verified to FAIL against the reverted hardcoded values, so they are real
  regression cover rather than tests that happen to pass.
- Full suite: 385 tests, 23 failures / 6 errors / 7 skipped — identical to the
  pre-change baseline of 369 tests with the same 23 / 6 / 7. All pre-existing
  and unrelated (upload notice-sides fixtures, web-flow, and an `extraction`
  `EXPECTED_VERSIONS` mismatch that predates this branch).

## Deployment version

Set `GIT_COMMIT` (or rely on `RAILWAY_GIT_COMMIT_SHA` / `VERCEL_GIT_COMMIT_SHA`)
in the deploy environment, then verify against `GET /health`:

    commit            == the sha you deployed
    kb_source         == "postgres-release"   (not "yaml")
    kb_drift          == []
    prompt_versions   == the versions you expect
    provider          == "openai"             (not "demo")

`kb_source: "yaml"` with `DATABASE_URL` set now means only one thing: no release
has been published yet.
