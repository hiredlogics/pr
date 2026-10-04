# P11-STAGING — Production-like End-to-End Validation

Validate frozen P10.6 architecture in a production-like staging environment.
No architecture redesign, fine-tune, pgvector, golden modification, or production deploy.

## 1. Staging release versions

| Field | Value |
| --- | --- |
| Release | `P11_STAGING_2026-10-04` |
| Branch | `feature/p8-architecture-hardening` |
| Freeze commit | `6766e2ee024dc6fb14cacfe5205a231081a00f00` |
| Working tree clean at freeze | **True** |
| Ontology | `p10_5_ontology_v1` |
| Module roles | `p10_5_roles_v1` |
| Claim Plan builder | `3` |
| DraftPlan | `p10_6_draft_plan_v1` |
| Validation | `VAL-5` / draft `DV-1` |
| Prompts | classification 2, extraction 10, case_analysis 8, drafting **17**, validation 3, semantic_extraction 1 |
| Live models | drafting/semantic/extraction `gpt-5.1`, validation `gpt-5-mini` |

Freeze commit chain also includes: `7561fae` (architecture freeze), `8fc92fe` (probe harden), `6766e2e` (`.env` BOM fix).

## 2. PostgreSQL result

**PASS** on isolated Neon database `pcn_appeal_p11` (original `neondb` app schema left untouched).

| Step | Result |
| --- | --- |
| create / ingest / save / reload | PASS |
| reassess / draft | RELEASED |
| reload after draft | PASS |
| Claim Plan id match | PASS |
| Master digest match | PASS |
| DB facts / claim_plans / draft_versions | 24 / 1 / 1 |

SQLite was **not** used. Credentials live only in gitignored `.env.staging.local`.

## 3. Migration result

| Check | Result |
| --- | --- |
| Static chain 0001–0012 ordered, no duplicates | **PASS** |
| Rollback comments documented | **PASS** |
| Live apply / table / trigger probe | **PASS** |

Added `0012_fact_history_ignored_duplicate.sql` so `IGNORED_DUPLICATE` history rows persist (same-value stability).

Rollback/recovery: greenfield `python -m pcn_appeal.store init`; existing DB apply `infra/migrations/0001`…`0012` in order; restore from staging PITR for corruption; append-only triggers block UPDATE/DELETE on audit tables.

## 4. Live extraction metrics

**NOT RUN.** No representative PDF/phone-photo corpus wired for automated P11 extraction. Golden field injection forbidden.

## 5. Live semantic-model metrics

| Metric | Result |
| --- | --- |
| Provider / model | openai / `gpt-5.1` |
| Prompt version | 1 |
| Latency | 6129 ms |
| Structured-output valid | **True** |
| Concepts returned | 3 |
| KB ground authority in output | **None** |
| Probe passed | **PASS** |

Observation: live model emitted `ONT::*` labels rather than the frozen ontology concept ids — structured validity OK; map/normalize before production pilot.

## 6. Live drafting metrics

| Metric | Result |
| --- | --- |
| Provider / model | openai / `gpt-5.1` |
| Prompt / DraftPlan | v17 / `p10_6_draft_plan_v1` |
| Latency | 4128 ms (re-probe) |
| Structured sections | **True** |
| Ground coverage | **100%** |
| Material-fact coverage | **100%** |
| Required-particular coverage | **100%** |
| Unsupported assertion rate | **0%** |
| Cancel request | present |
| Probe passed | **PASS** |

## 7. Provider-failure results

**PASS.** Modes: timeout, 429, 500, invalid JSON, empty, partial, unavailable.
Released count: **0**. All resolved to `MANUAL_REVIEW` (not `RELEASED`).

## 8. Idempotency / concurrency results

**PASS.** Double `generate`: same Claim Plan id + digest; single plan object; both RELEASED on paid/keying path.

## 9. Outcome-state results

| Check | Result |
| --- | --- |
| No-ground does not RELEASE | **PASS** |
| Front/back gate (front-only, duplicate, two pages, multipage) | **PASS** |
| Expected hold classes recognized | documented |

Unresolved: one no-ground case returned `outcome_code=null` with `MANUAL_REVIEW` (hold is correct; customer code should be explicit `NO_SUPPORTED_GROUNDS`).

## 10. Security findings

| Check | Result |
| --- | --- |
| Files scanned | 557 |
| Critical live keys in repo | **0** |
| `.env` gitignored | yes |
| Admin token gate present | yes |

Fixed during P11: `.env` UTF-8 BOM prevented `OPENAI_API_KEY` from loading (`pcn_appeal/config.py`).

## 11. Latency / cost metrics

| Metric | Value |
| --- | --- |
| Regression P50 | 1962 ms |
| Regression P95 | 2095 ms |
| Live semantic | 6129 ms |
| Live drafting | 4128 ms |
| Cost / appeal | not metered (use provider dashboard) |

## 12. Staging E2E / regression

| Metric | Value |
| --- | --- |
| Cases | 11 |
| E2E OK (incl. expected holds) | **11/11** |
| Clean-upstream draft ground coverage | **100%** |
| P8/P10 invariants | **PASS** (112 tests) |

## Release gates

| Gate | Result |
| --- | --- |
| P8/P10 invariants | PASS |
| Real PostgreSQL | **PASS** (`pcn_appeal_p11`) |
| Migrations static | PASS |
| Migrations live | **PASS** |
| Draft coverage 100% (clean) | PASS |
| Provider failures | PASS |
| Idempotency | PASS |
| Outcome consistency | PASS |
| Front/back | PASS |
| Security (no critical) | PASS |
| Working tree clean | PASS |
| Live semantic | PASS |
| Live drafting | PASS |
| Live extraction | **FAIL** (not run) |

## 13. Unresolved issues

1. Wire representative PDF/photo corpus for live extraction (no golden injection).
2. Normalize live semantic concept ids to frozen ontology.
3. Ensure `classify_hold` always emits `NO_SUPPORTED_GROUNDS` (not null) for empty-plan holds.
4. **Rotate secrets** that were pasted into chat (Neon password, R2 keys, admin password, SMTP app password, session password). Do not commit `.env.staging.local`.

## 14. Recommendation

**EXTRACTION_WORK_REQUIRED**

Postgres + migrations + live drafting/semantic + invariants now pass. Remaining release blocker: real document extraction corpus on staging.

Do not production deploy. STOP after staging review.

---

Harness: `python -m pcn_appeal.eval.p11_staging`  
Artefacts: `reports/p11_staging/`
