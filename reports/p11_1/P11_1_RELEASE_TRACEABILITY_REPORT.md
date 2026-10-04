# P11.1 — Release Traceability + Live Case Verification

**Date:** 2026-10-04  
**Scope:** Staging Neon `pcn_appeal_p11` — no production deploy, no reasoning/KB/prompt/pgvector behaviour changes.

---

## 1. Released case traced

**Case:** `f86e5100-8998-43aa-a1ff-7d6c6a894229` (RELEASED, created 2026-10-04T12:01:44Z)

| Stage | Count / status |
|---|---|
| CASE | present (`commit_sha` set, `llm_provider=openai`) |
| DOCUMENT BASELINE | 1 |
| FACTS | 24 |
| FACT SOURCES | 62 |
| DERIVED FACTS | 13 |
| LEGAL FINDINGS | 5 |
| CLAIM PLAN | 1 (+ 28 plan items / ground decisions) |
| DRAFT PLAN | audit/grounding path (no dedicated table) |
| DRAFT VERSION | 1 |
| VALIDATION | 0 rows in `validations` table; pass stored on draft version path |
| FINAL OUTCOME | RELEASED |
| KB release on case | **NULL** → `LEGACY_UNVERSIONED` |

Join graph for facts → sources → findings → claim plan → draft version is intact for this case.

---

## 2. Missing joins found

| Issue | Classification |
|---|---|
| `kb_release_id` NULL on all 3 RELEASED staging rows | `LEGACY_UNVERSIONED` — not backfilled |
| `validations` table empty | Validation results live on draft versions / audit, not a separate table join |
| `route` / `document_type` / `stage` NULL | Staging harness called `pipe.ingest()` without API intake; values never set (not a silent drop). Persistence via `routing_columns` already works when intake runs. |
| `frontend_version` NULL | OPTIONAL — client header not sent in staging runs |
| `appeal_deadline` NULL | OPTIONAL — engine not wired |

No invented backfill applied.

---

## 3. Release metadata fields

Required before RELEASED (new cases), persisted on `cases.release_metadata` (migration `0013` + schema):

- `commit_sha`
- `kb_release_id`
- `ontology_version`
- `module_role_version`
- `claim_plan_builder_version`
- `draft_plan_version`
- `validation_version`
- `prompt_versions`
- `llm_provider`
- `model_versions`

**Gate:** `gate_before_release` in `pcn_appeal/release_trace.py`, wired in `orchestrator._release_gate` at both RELEASED assignment sites.

If incomplete → state `MANUAL_REVIEW`, internal audit `RELEASE_METADATA_INCOMPLETE`, customer outcome maps to `PROCESSING_ERROR` (no version IDs in customer UI).

---

## 4. Legacy-row handling

All current staging RELEASED rows (`3/3`) classify as **`LEGACY_UNVERSIONED`**.

- Visible in Live Data Explorer Release Trace panel
- Not rewritten / not backfilled
- New-case RELEASED invariants skipped for this class only
- New cases must never take this exception (gate enforces)

---

## 5. KB release verification

Pinned release **`kb-20261004T113657Z`** resolves:

| Field | Value |
|---|---|
| release_id | `kb-20261004T113657Z` |
| release_digest | `780bed81012ce05c887d75284fc0079965c0893d588034703d6e10251073c7ee` |
| module_count | 55 |
| module_versions (sample) | KB-EQ-01@1.0 … KB-BAY-01@1.4 (55 total) |
| created_at | (see live_audit.json / KB source row) |

Every production-pilot case must point at an immutable KB release via `kb_release_id` + `release_metadata`.

---

## 6. Vector / index audit (read-only)

| Check | Result |
|---|---|
| pgvector | installed |
| table | `kb_embeddings` |
| dimensions | 1024 |
| rows | 129 |
| embedding model | `hashing-v1` |
| null embeddings | 0 |
| duplicate entity embeddings | none |
| orphan embeddings | none |
| flags | `[]` |

**HNSW indexes:** 11 indexes on the same `(embedding vector_cosine_ops)` column.

- Normalized bodies: **1 distinct**
- Verdict: **`ACCIDENTAL_DUPLICATES`**
- Indexes not dropped in this change (cleanup deferred)

---

## 7. Tests

`tests/test_p11_1_release_trace.py` — **17 passed**

- complete meta → allow
- missing `kb_release_id` → BLOCK
- missing Claim Plan / validation / DraftPlan signals → BLOCK (invariants / trace)
- legacy staging row → visible, not rewritten
- save/reload → release trace unchanged
- orchestrator gate → MANUAL_REVIEW + `RELEASE_METADATA`

---

## 8. Case-column null audit

| Field | Class | Note |
|---|---|---|
| route | REQUIRED_FOR_RELEASE | Set by API intake; persisted. Staging nulls = harness skip. |
| document_type | REQUIRED_FOR_RELEASE | Same as route |
| stage | OPTIONAL | May be unset for some notices |
| appeal_deadline | OPTIONAL | Not computed yet |
| frontend_version | OPTIONAL | Client header |
| kb_release_id | REQUIRED_FOR_RELEASE | Gate + stamp at release |
| commit_sha | REQUIRED_FOR_RELEASE | Set at case create |
| llm_provider | REQUIRED_FOR_RELEASE | Set at case create |

---

## 9. Live Data Explorer

Admin Cases view now includes a **Release Trace** panel:

Code version · KB release · Ontology · Facts · Legal findings · Claim Plan · DraftPlan · Draft · Validation · Final outcome — with ✓/✗ and missing-link highlight. Legacy classification shown.

---

## Final verdict

### TRACEABILITY_FIX_REQUIRED

**Why not READY_FOR_PRODUCTION_PILOT yet**

1. Staging has **zero** VERSIONED RELEASED cases — all 3 RELEASED rows are `LEGACY_UNVERSIONED` (`kb_release_id` null).
2. A **new** case has not yet been proven end-to-end through the gate to RELEASED with complete `release_metadata` on this database.
3. Gate + persistence + UI + tests are in place; pilot readiness requires one green VERSIONED RELEASED case on staging after this change.

**Do not production deploy.** Next step: run one staging case through the normal API path (intake → … → generate) and confirm Release Trace is all ✓ with `kb-20261004T113657Z` (or current pin) stamped.
