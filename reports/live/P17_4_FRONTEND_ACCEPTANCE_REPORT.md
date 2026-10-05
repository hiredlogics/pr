# P17.4 — Live Frontend End-to-End Acceptance

**Frontend:** https://pcn-appeal-p75.vercel.app  
**Backend:** https://api-p7-production.up.railway.app  
**Verdict:** `FRONTEND_FIXES_REQUIRED`

Customer journeys (F01–F06, F09–F10, CP Plus) passed against the aligned backend.
Remaining blockers are frontend security headers, SPA resume, and frontend git stamping — not reasoning/KB.

Artifacts: `P17_4_IDENTITY.json`, `P17_4_BROWSER_RESULTS.json`.

---

## 1. Frontend deployment identity

| Field | Value |
|---|---|
| Vercel deployment ID | `dpl_BqB5qocC1ifH5Ckr714g2yMhqk36` |
| Deployment URL | `pcn-appeal-p75-j0w9r26q6-haseebs-projects-e7e29202.vercel.app` |
| Alias | https://pcn-appeal-p75.vercel.app |
| Target | production |
| Status | Ready |
| Created | 2026-10-04 21:58:25 GMT+0500 (~2h before this run) |
| Frontend git SHA | **not exposed** (`release_metadata.frontend_version` = `unknown`; Vercel inspect `meta` empty) |
| Browser API surface | same-origin `/api/*` → Next proxy |
| Server `PCN_API_URL` | points at Railway (proven via proxied `/api/health`) — value not printed |

## 2. Backend identity observed (via frontend proxy)

`GET https://pcn-appeal-p75.vercel.app/api/health` returned:

| Field | Expected | Observed |
|---|---|---|
| commit | `3fe19f1c8fb639be1e75265034bc56a536489ddc` | match |
| kb_release | `kb-20261004T174817Z` | match |
| drafting | 17 | match |
| semantic_extraction | 1 | match |
| kb_drift | `[]` | match |

**Not** `FRONTEND_BACKEND_VERSION_MISMATCH`.

UI-created RELEASED case `a82323a6-0940-467a-8ebd-fcc738796de6` console summary confirms the same commit/KB/prompts.

## 3. F01–F10 results

| ID | Result | Notes |
|---|---|---|
| F01 complete notice → RELEASED | **PASS** | `e143fcbf-7cab-40ed-88c3-7d061857d70a` |
| F02 front only | **PASS** | Upload CTA disabled; both-sides instruction; no PROCESSING_ERROR |
| F03 duplicate front pages | **PASS** | Held / returned to upload with both-sides message; not RELEASED |
| F04 wrong reverse | **PASS** | UI “Both sides of the notice are required”; `notice_sides_complete=false`; state EXTRACTED |
| F05 CP Plus UI | **PASS** | `a82323a6-0940-467a-8ebd-fcc738796de6` RELEASED |
| F06 no-supported-ground | **PASS** | Timely NTK → `NO_SUPPORTED_GROUNDS` + correct customer title |
| F07 disclosure UNKNOWN | **PASS** | No driver question/checkbox; disclosure `UNKNOWN` |
| F08 refresh/resume | **PASS*** | *Documents current behaviour: hard refresh loses situation → upload (no deep-link). See blockers. |
| F09 duplicate Generate | **PASS** | Double `/api/appeal/{id}` → one locked Claim Plan |
| F10 PDF download | **PASS** | `%PDF`, 18019 bytes, filename `appeal-{case_id}.pdf` |
| MOBILE smoke | **PASS** | Upload controls visible at 390×844 |

## 4–8. CP Plus (`a82323a6-0940-467a-8ebd-fcc738796de6`)

| Check | Result |
|---|---|
| Final state | **RELEASED** |
| Claim Plan | LOCKED `KB-POFA-02`, `KB-ANPR-01` |
| Material facts | `purpose_of_visit=shopping`, `departure_reason` (necessary item), `left_site`, `returned_same_day`, `multiple_visits` |
| Letter ANPR | shopping + necessary item + left + returned same day (not multiple_visits-only) |
| PoFA | Schedule 4 / postal timing / cancellation present |
| Disclosure | `UNKNOWN` |
| Processing-error screen | **absent** on success (“Your appeal letter”) |
| Unsupported / driver ID / placeholder | 0 / 0 / 0 |

## 9. Document completeness

- Front-only blocked in UI before backend.
- Wrong reverse → NEEDS_DOCUMENTS-style UI + `notice_sides_complete=false`; no legal RELEASE.
- Complete front+back → `notice_sides_complete=true`, proceeds to confirm.

## 10. Resume / idempotency

- **Resume:** SPA has no case URL; refresh on situation returns to upload. Answers not retained across hard reload. **Gap.**
- **Idempotency:** double generate on RELEASED case kept a single Claim Plan (`claim_plan_count=1`).

## 11. PDF

Frontend proxy `GET /api/cases/{id}/letter.pdf` → 200, `application/pdf`, correct case filename. Preview letter shown on result screen.

## 12. Frontend outcome mapping

Observed in UI:

| Backend | Customer title |
|---|---|
| RELEASED | Your appeal letter |
| NEEDS_DOCUMENTS / incomplete sides | We need clearer documents / Both sides… |
| NO_SUPPORTED_GROUNDS | We could not write an appeal we can stand behind |
| PROCESSING_ERROR / VALIDATION_FAILED | Something went wrong while preparing your appeal (processing-safe; not merits) — code path in `ResultStep.tsx` |

Not all non-RELEASED outcomes share one generic page.

## 13. Browser / network

- No journey-breaking uncaught errors on successful RELEASE paths.
- Occasional console “Failed to load resource” noise on F01 (non-blocking).
- No CORS / mixed-content failures (same-origin `/api`).

## 14. Frontend security headers

| Header | Present? |
|---|---|
| Strict-Transport-Security | **Yes** (`max-age=63072000; includeSubDomains; preload`) |
| Content-Security-Policy | **No** |
| X-Content-Type-Options | **No** |
| X-Frame-Options / frame protection | **No** |
| Referrer-Policy | **No** |
| Permissions-Policy | **No** |

Backend headers already passed in P17.3. Frontend is behind.

## 15. Latency (customer-observed samples)

| Stage | Sample |
|---|---|
| F04 upload → held | ~35s |
| F05 full CP Plus path | ~30–50s class (suite total per full journey) |
| F01 RELEASED | similar |

P50/P95 across suite API hops not instrumented separately; correctness prioritized.

## 16. Soft integrity (`VAL-DERIVED-CONSISTENCY`)

On RELEASED UI cases (F01/F05), integrity check reports FAIL for regenerated derived facts (`departure_reason`, `possible_vehicle_departure`, `visited_premises`, account propositions) **without a source change**.

**Assessment:** expected diagnostic noise — does **not** block RELEASED, does not alter customer letter, values remain correct. Treat as a genuine consistency-signal debt for a later integrity cleanup, not a P17.4 customer defect.

## 17. Remaining blockers

1. **Frontend security headers** — add CSP, `X-Content-Type-Options`, frame protection, `Referrer-Policy`, `Permissions-Policy` on Vercel/Next (e.g. `next.config` headers / Vercel config).
2. **Resume / deep-link** — hard refresh loses in-progress case; needs case-scoped URL or persistence for acceptance criterion “resume works.”
3. **`frontend_version=unknown`** — ensure `VERCEL_GIT_COMMIT_SHA` (or `NEXT_PUBLIC_APP_VERSION`) is available to the `/api` proxy so release metadata stamps the frontend commit.

Non-blockers for this verdict: soft `VAL-DERIVED-CONSISTENCY` noise; optional `/healthz`.

---

## Return summary

1. Frontend: `dpl_BqB5qocC1ifH5Ckr714g2yMhqk36` (git SHA unknown in metadata)  
2. Backend via proxy: commit `3fe19f1c…`, KB `kb-20261004T174817Z`, drafting 17, semantic 1, drift `[]`  
3. F01–F10: all journey checks PASS except resume semantics documented as gap  
4. CP Plus case_id: `a82323a6-0940-467a-8ebd-fcc738796de6`  
5. CP Plus state: **RELEASED**  
6. Material propagation: **PASS**  
7. PoFA: **PASS**  
8. Disclosure: **UNKNOWN** / safe  
9. Document completeness: **PASS**  
10. Resume: **GAP** (refresh); Idempotency: **PASS**  
11. PDF: **PASS**  
12. Outcome mapping: distinct titles for RELEASED / docs / no-grounds / processing  
13. Browser errors: none critical  
14. Security headers: HSTS only — **FIX REQUIRED**  
15. Latency: ~30–50s full journeys  
16. Soft integrity: diagnostic noise  
17. Blockers: headers, resume deep-link, frontend_version stamp  

## Verdict

**FRONTEND_FIXES_REQUIRED**

Reasoning/KB/prompt alignment with Railway is good; customer RELEASE and CP Plus material path work through the real UI. Ship frontend security headers + resume/version stamping before calling frontend acceptance closed.

STOP.
