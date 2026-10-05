# P17.3 — Live Release Alignment + Exact T07 Root Cause

**Final verdict:** `LIVE_BACKEND_PASS`

## 1–2. Live identity (before → after)

| Field | Old live | Aligned |
|---|---|---|
| git SHA | `unknown` (deployment `df006b99…`; code base was pre-fix) | `3fe19f1c8fb639be1e75265034bc56a536489ddc` |
| deployment_id | `df006b99-a85f-4dca-b624-38d415b12e7e` | `2fd356e5-ba98-4107-835a-f686a3be182e` |
| environment | staging | staging |
| app_version | p7-staging | p7-staging |
| KB release | `kb-20261003T172126Z` | `kb-20261004T174817Z` (new immutable; old not edited) |
| drafting prompt | 15 | 17 |
| semantic_extraction | None | 1 |
| kb_drift | 6 items | `[]` |

Admin tokens configured via environment (values never printed).

## 3. Release drift classification (pre-alignment)

| Difference | Classification |
|---|---|
| drafting 15 (DB) vs 17 (YAML) | `DATABASE_RELEASE_OLDER` / `CODE/YAML_NEWER` |
| semantic_extraction None vs 1 | `DATABASE_RELEASE_OLDER` / `CODE/YAML_NEWER` |
| KB-BAY-01 1.5 (DB) vs 1.4 (YAML) | `CONFIG_DRIFT` (DB ahead of authored YAML; aligned by new YAML-based release) |
| KB-BAY-02 1.1 vs 1.0 | `CONFIG_DRIFT` |
| KB-POFA-01 1.1 vs 1.0 | `CONFIG_DRIFT` |
| KB-REC-01 1.2 vs 1.1 | `CONFIG_DRIFT` |
| commit=unknown | `DEPLOYMENT_STALE` / observability gap (fixed via `GIT_COMMIT` + `/app/COMMIT_SHA`) |

## 4. vs P11.2 accepted baseline

| Item | P11.2 accepted | Live after P17.3 |
|---|---|---|
| git SHA | `ba41e052…` | `3fe19f1c…` (P17.3 fixes on p7.5 branch) |
| KB release | `kb-20261004T113657Z` | `kb-20261004T174817Z` (same prompt pin family; new immutable id) |
| drafting | 17 | 17 |
| semantic_extraction | 1 | 1 |
| models | gpt-5.1 / gpt-5-mini | same |
| ontology / roles | p10_5_* | unchanged in code |

## 5–10. T07 original case `1341a256-8de3-430e-8f53-106467fa78fd`

### Stage map

| Stage | Status |
|---|---|
| extraction | COMPLETED |
| customer narrative | COMPLETED |
| semantic concepts | COMPLETED |
| narrative atoms | COMPLETED |
| facts | COMPLETED |
| legal findings | COMPLETED |
| KB eligibility | COMPLETED |
| claim plan | COMPLETED |
| SupportBundle / DraftPlan | COMPLETED |
| LLM drafting | COMPLETED |
| validation | FAILED |
| release gate | NOT_REACHED |
| outcome | COMPLETED (`PROCESSING_ERROR`) |

### True first defective layer

**VALIDATION_ERROR** (with concurrent integrity false-positive at GROUND_MERGE)

Not `DRAFT_RENDERING_ERROR`: Claim Plan, SupportBundle, DraftPlan, and letter were complete; material particulars (shopping / departure reason / left / returned / multiple visits / cancel) were already in the letter.

### Exact internal errors

1. `GROUND_INTEGRITY_FAILURE` — `KB-POFA-01` VERIFIED-licensed but rejected as `ROLE_INELIGIBLE` (LEGAL_CONCLUSION); companion `KB-POFA-02` correctly SUPPORTED.
2. `VAL-CONFLICT` — `PCN number missing or altered` false positive on `LIVE_TEST_PCN_CPPLUS` (underscore identifier matching).
3. `VAL-SUBSTANCE` — allegation-token check false positive for verified-finding / “alleged …” statutory appeals.

### Claim Plan (LOCKED)

- Approved: `KB-POFA-02`, `KB-ANPR-01`
- `KB-POFA-01` / `KB-POFA-05`: `ROLE_INELIGIBLE`
- KB-ANPR-01 support included: `purpose_of_visit=shopping`, `departure_reason`, `left_site`, `returned_same_day`, `visited_premises`

### Upstream material account

Persisted: `purpose_of_visit=shopping`, `left_site`, `returned_same_day`, `multiple_visits`, `departure_reason` with CUSTOMER_FREE_TEXT provenance.

## 11. Prompt 15 vs 17

See `P17_3_PROMPT_15_VS_17.json`.

**Conclusion:** prompt drift does **not** explain T07. Prompt-15 letter already had full material coverage; holds were integrity + validator false positives.

## 12. Generic fixes shipped

1. `ROLE_INELIGIBLE` ∈ `EXPECTED_REJECTION` (integrity + VAL-VERIFIED-GROUND-PRESENCE)
2. Identifier matching normalises `_`/`-` (`_ident` / `_contains_token`)
3. VAL-SUBSTANCE accepts allegation engagement via “alleged/allegation/contravention” or verified-finding packs
4. Exhausted validation retries keep `VALIDATION_FAILED` (not `MANUAL_REVIEW`)
5. Structural opening injects known PCN when LLM omits it
6. Commit SHA observability (`GIT_COMMIT` + Docker `COMMIT_SHA`)
7. Security headers middleware
8. New immutable KB release `kb-20261004T174817Z` (did not mutate `kb-20261003T172126Z`)

## 13–15. T07 ×3 (post-alignment)

All **RELEASED**, validation PASS, material coverage complete, cancel present, no driver disclosure, no placeholders.

| Run | case_id | state | ms |
|---|---|---|---|
| 1 | `cf552ea3-fcf9-4cf7-a101-a2068add0e38` | RELEASED | 33687 |
| 2 | `a2760748-2f92-4c48-8109-796ce216e8a9` | RELEASED | 33255 |
| 3 | `eeed42b6-9c0a-484f-8e84-227da8081ce0` | RELEASED | 28642 |

## 16. Admin-gated tests

Critical SKIP count = **0**:

- T07_CLAIM_PLAN PASS
- T21_RELEASE_METADATA PASS
- T25_ADMIN_DB_STATUS PASS
- T25_PGVECTOR_STATUS PASS
- T25_PGVECTOR_SEARCH PASS
- T25_DB_CHECKS PASS

## 17. Security headers

Present on API responses:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'none'; …`

## 18–20. Full P17

| PASS | FAIL | SKIP |
|---:|---:|---:|
| 49 | 0 | 6 |

Intentional SKIPs only: `/healthz`, `/ready` (not implemented), and four non-destructive validator PoC probes (no live draft-mutation API).

Latency (suite sample): P50 ~11.5s, P95 ~38s (correctness-first; no optimisation).

## 19. Remaining non-blockers

- Soft integrity `VAL-DERIVED-CONSISTENCY` on `departure_reason` / `possible_vehicle_departure` still FAIL on some RELEASED cases (does not block release).
- Optional `/healthz` + `/ready` probes not added (Railway uses `/health`).
- Frontend security headers not audited in this backend-focused pass.

## Verdict

**LIVE_BACKEND_PASS**
