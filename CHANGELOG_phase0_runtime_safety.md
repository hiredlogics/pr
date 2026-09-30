# Change log — Phase 0: environment truth and runtime safety

Scope: runtime/deployment safety only. **No change** to ground selection, the
Claim Plan, question generation, drafting, validation rules, PoFA logic or KB
modules/blocks. Builds on `5c4267f` (release provenance).

## Observed production state (2026-09-30, read-only probes)

| Item | Finding | How established |
|---|---|---|
| Frontend | `https://pcn-appeal.vercel.app` (Vercel), Next build id `4dh_f_0rs-_ubJMPVXik_` | HTTP headers / page payload |
| Frontend source commit | UNVERIFIED | not exposed; no Vercel CLI access here |
| Backend host | **Railway**, reached via the Next proxy (`PCN_API_URL`) | `x-railway-edge`, `x-railway-request-id` response headers |
| Backend URL | UNVERIFIED | hidden behind the proxy by design |
| Backend commit | UNVERIFIED — live `/health` has no `commit` field, so the running build predates `5c4267f` | live `/health` |
| Provider / models | `openai`; extraction, case_analysis, drafting `gpt-5.1`; validation `gpt-5-mini` | live `/health` |
| `LLM_PROVIDER` value | UNVERIFIED (provider resolved to openai; whether the variable is set explicitly is not observable) | — |
| Database | `store: postgres` → `DATABASE_URL` set | live `/health` |
| KB release | `kb-20260930T231446Z` | live `/health` |
| Prompt versions | UNVERIFIED — not reported by the deployed build | — |
| `ADMIN_TRACE_TOKEN` | **Not set** — `/cases/{id}/trace` answered past the auth check | live probe returned "unknown case", not 401 |
| `/console` | **Publicly served** through the site | live probe: 200 text/html |
| Blob uploads | **Not configured** (`/api/blob/upload` → 501), so uploads use proxy mode | live probe |
| Vercel project `pcn-appeal-api` | **Live but broken** — every request `FUNCTION_INVOCATION_FAILED` | live probe |
| Fly (`pcn-appeal-api.fly.dev`) | Not resolving — not deployed | live probe |

## Proven cause (per change)

1. **Silent demo fallback.** `llm.default_client()` returned `DemoLLM` whenever
   `LLM_PROVIDER` was unset and OpenAI failed to initialise. DemoLLM's
   validation always passes, so a demo letter could be RELEASED in production.
2. **Operator routes public.** `/trace` authenticated only when
   `ADMIN_TRACE_TOKEN` was set (it is not in production). `/console`, `/generate`,
   `GET /appeal`, `/answers`, `/documents` had no guard. The Next proxy forwarded
   every path, including `Authorization`, so all were reachable from the site.
3. **No environment/build identity** on `/health`, and the raw provider error
   (which can quote a masked key fragment) was shown as `provider_note`.

## Exact changes

| File | Change | Reason |
|---|---|---|
| `pcn_appeal/runtime.py` | **New.** `environment()` from `APP_ENV` → `RAILWAY_ENVIRONMENT_NAME` → `VERCEL_ENV` (default `development`); `is_production()`; `build_id()` from `BUILD_ID` → `RAILWAY_DEPLOYMENT_ID` → `VERCEL_DEPLOYMENT_ID` | One definition of "production" and "which deployment" |
| `pcn_appeal/llm.py` | `default_client()`: in production, anything but `LLM_PROVIDER=openai` raises `ProviderPolicyError`; with `openai`, init errors propagate (never DemoLLM). `probe()` reports `provider: "unavailable"` instead of raising. Key fragments `sk-…` redacted from notes | Cause 1, 3 |
| `pcn_appeal/api.py` | Startup (lifespan) builds the client once in production and refuses to start if it fails | Bad deploy is not promoted |
| `pcn_appeal/api.py` | `_pipeline()`: case creation/rehydration on an unusable provider → HTTP 503 `{"code":"PROCESSING_ERROR", ...}` with customer-safe wording; no case row created | Processing failure is never a demo case or a merits outcome |
| `pcn_appeal/api.py` | `/health` adds `environment`, `build_id`; in production a non-openai provider returns **503** `status: "unhealthy"`; `vision` derived from the probe (no second client) | Cause 3; container HEALTHCHECK fails on it |
| `pcn_appeal/api.py` | `_require_admin()` on `/console`, `/cases/{id}/trace`, `/generate`, `GET /appeal`, `/answers`, `/documents`, `/disclosure`. Token (`ADMIN_TRACE_TOKEN` or `ADMIN_TOKEN`) accepted as `Authorization: Bearer` or `X-Admin-Token`, constant-time compare. **No token + production = 403**; no token + development = open (unchanged) | Cause 2 |
| `frontend/app/api/[...path]/route.ts` | Allowlist of the customer journey's routes only (health, appeal/files, appeal/{id}, cases, cases/{id}/files\|blobs\|confirm, confirmation, letter.pdf); everything else 404. Strips `Authorization` / `X-Admin-Token` | Cause 2 — proxy cannot bypass |
| `tests/test_runtime_safety.py` | **New**, 18 tests | Regression cover |

## Behaviour before → after

| | Before | After |
|---|---|---|
| Production, `LLM_PROVIDER` unset | OpenAI if key works, else **DemoLLM silently** | Refuses to start; `/health` 503; new case 503 PROCESSING_ERROR |
| Production, `LLM_PROVIDER=openai`, bad key | 500 on case creation | Refuses to start; case creation 503 PROCESSING_ERROR |
| Development, no key | DemoLLM | DemoLLM (unchanged) |
| `/trace` in production, no token | **Public** | 403 |
| `/console` through the site | **Public** | 404 at proxy; 403/401 at backend |
| `/health` | no environment/build id; raw provider error | `environment`, `build_id`; key fragments redacted |
| `/disclosure` wrong token | 403 | 401 (no token configured in prod: 403) |

## Not changed (explicit)

Ground selection, `claim_plan.py`, `analysis.py`, `reasoning.py`, question
generation, `drafter.py`, `validation.py` rules, `outcome.py`, `pofa.py`,
`kb_modules.yaml`, `building_blocks.yaml`, prompts. Customer routes' request and
response shapes are unchanged. `POST /appeal` (JSON) is no longer reachable
*through the proxy* (the UI never called it); it is unchanged on the backend.

## Uploaded documents

Not publicly accessible in current production: blob mode is not configured, so
files travel multipart through the proxy; the backend stores extracted text in
Postgres and holds images in memory only. **Latent risk, not changed:**
`frontend/lib/upload.ts` blob mode uses `access: "public"`. Do not enable
`NEXT_PUBLIC_UPLOAD_MODE=blob` until it is switched to private access
(`@vercel/blob` 2.8.0 supports it) with an authenticated backend fetch.

## Tests

- New: `tests/test_runtime_safety.py`, 18 tests, OK. Against the pre-change
  `api.py`/`llm.py` the provider, health and operator-route tests fail.
- Full suite (`python -m unittest discover -s tests`, no secrets in env, DemoLLM /
  fakes only, no real provider): **403 run, 23 failures, 6 errors, 7 skipped** —
  the identical failing set to the `5c4267f` baseline (385 run, 23/6/7). All
  pre-existing; see the deliverable for the list.
- Frontend: `tsc --noEmit` clean; proxy allowlist checked against 25 route cases.
- Note: `discover -s tests -t .` fails to import 12 modules (`import support`);
  the suite must be run with `-s tests` as above.

## Deployment prerequisites (Railway backend)

Before deploying this build, set on the Railway **production** environment:

1. `LLM_PROVIDER=openai` — **required**, or the service refuses to start.
2. `ADMIN_TRACE_TOKEN=<random secret>` — otherwise operator routes are 403 for
   everyone (safe default, but the console becomes unusable).
3. Confirm `RAILWAY_ENVIRONMENT_NAME` is `production` there, or set
   `APP_ENV=production` explicitly.
4. Configure Railway's healthcheck path to `/health` so a 503 blocks promotion.

Then redeploy the Vercel frontend (proxy allowlist). Verify:
`/health` → `environment: production`, `commit` = deployed sha,
`provider: openai`, `status: ok`; `/api/console` → 404;
`/api/cases/x/trace` → 404.

## Rollback

`git revert <phase0 commit>` and redeploy backend and frontend. Or, backend
only: redeploy the previous Railway deployment from its dashboard. The change is
stateless (no schema or KB change), so rollback has no data step.
