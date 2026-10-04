# P17 — Live Backend Full System Test

**Target:** `https://api-p7-production.up.railway.app`
**Recommendation:** `LIVE_FIXES_REQUIRED`

Dedicated `LIVE_TEST_` cases only. No production customer data modified.
No deploys, no destructive SQL, no secret values in this report.

## 1. Backend availability

- Root/docs/openapi reachable via discovery probes
- Environment: `staging`
- App version: `p7-staging`
- Build: `df006b99-a85f-4dca-b624-38d415b12e7e`
- Commit: `unknown`

## 2. API endpoints discovered

- OpenAPI paths: **59**

```
/admin/api/db/cases
/admin/api/db/cases/{case_id}
/admin/api/db/diagnostics
/admin/api/db/diagnostics/select
/admin/api/db/diagnostics/{query_id}
/admin/api/db/knowledge
/admin/api/db/status
/admin/api/db/tables
/admin/api/db/tables/{schema}/{table}
/admin/api/db/tables/{schema}/{table}/rows
/admin/api/db/vector/columns
/admin/api/db/vector/embeddings
/admin/api/db/vector/health
/admin/api/db/vector/indexes
/admin/api/db/vector/search
/admin/api/db/vector/status
/admin/cases/{case_id}/audit
/admin/cases/{case_id}/audit/report.md
/admin/cases/{case_id}/claim-plans
/admin/cases/{case_id}/claim-plans/compare
/admin/cases/{case_id}/claim-plans/explain
/admin/cases/{case_id}/console
/admin/cases/{case_id}/console/compare
/admin/cases/{case_id}/console/report.txt
/admin/cases/{case_id}/execution-trace
/admin/integrity/db-checks
/admin/kb/modules/{module_id}
/admin/kb/releases
/admin/knowledge/changes
/admin/knowledge/edges
/admin/knowledge/edges/{edge_id}/remove
/admin/knowledge/graph/facts
/admin/knowledge/import
/admin/knowledge/modules
/admin/knowledge/modules/{module_id}
/admin/knowledge/modules/{module_id}/activate
/admin/knowledge/modules/{module_id}/disable
/admin/knowledge/relationship-changes
/admin/knowledge/releases
/admin/knowledge/releases/compare
/appeal
/appeal/files
/appeal/{case_id}
/cases
/cases/{case_id}
/cases/{case_id}/answers
/cases/{case_id}/appeal
/cases/{case_id}/blobs
/cases/{case_id}/confirm
/cases/{case_id}/confirmation
/cases/{case_id}/disclosure
/cases/{case_id}/documents
/cases/{case_id}/facts
/cases/{case_id}/files
/cases/{case_id}/generate
/cases/{case_id}/letter.pdf
/cases/{case_id}/trace
/facts/{fact_id}/history
/health
```

## 3. Health result

```json
{
  "status": "ok",
  "store": "postgres",
  "provider": "openai",
  "kb_release": "kb-20261003T172126Z",
  "kb_release_digest": "320dccd14a5ff93b6bf2971a9bc514f367ab98bb701424906ff281331ab8e226",
  "kb_source": "postgres-release",
  "modules": 55,
  "vision": true,
  "prompt_versions": {
    "drafting": 15,
    "extraction": 10,
    "validation": 3,
    "case_analysis": 8,
    "classification": 2,
    "page_references": 2
  },
  "models": {
    "classification": "gpt-5.1",
    "extraction": "gpt-5.1",
    "page_references": "gpt-5.1",
    "semantic_extraction": "gpt-5.1",
    "case_analysis": "gpt-5.1",
    "drafting": "gpt-5.1",
    "validation": "gpt-5-mini"
  },
  "kb_drift": [
    "module KB-BAY-01: release serves 1.5, YAML has 1.4",
    "module KB-BAY-02: release serves 1.1, YAML has 1.0",
    "module KB-POFA-01: release serves 1.1, YAML has 1.0",
    "module KB-REC-01: release serves 1.2, YAML has 1.1",
    "prompt drafting: release serves 15, YAML has 17",
    "prompt semantic_extraction: release serves None, YAML has 1"
  ]
}
```

## 4. Auth result

See AUTH_* rows in results. Admin token configured locally: **False**

## 5. Test cases PASS/FAIL

- PASS: 39  FAIL: 1  SKIP: 13  TOTAL: 53

| ID | Result | Layer |
| --- | --- | --- |
| HEALTH | PASS |  |
| OPENAPI_DISCOVERY | PASS |  |
| PROBE_healthz | SKIP |  |
| PROBE_ready | SKIP |  |
| BACKEND_AVAILABLE | PASS |  |
| AUTH_ADMIN_UNAUTH | PASS |  |
| AUTH_ADMIN_INVALID | PASS |  |
| AUTH_ADMIN_VALID | SKIP |  |
| AUTH_CUSTOMER_CREATE | PASS |  |
| T02_FRONT_ONLY | PASS |  |
| T01_COMPLETE_CLEAN | PASS |  |
| T03_DUPLICATE_FRONT | PASS |  |
| T_IMG_ROTATED | PASS |  |
| T_IMG_LOW_LIGHT | PASS |  |
| T_IMG_BLUR | PASS |  |
| T_IMG_UNREADABLE | PASS |  |
| SEM_S1 | PASS |  |
| SEM_S2 | PASS |  |
| SEM_S3 | PASS |  |
| SEM_S4 | PASS |  |
| SEM_S5 | PASS |  |
| SEM_S6 | PASS |  |
| SEM_S7 | PASS |  |
| SEM_S8 | PASS |  |
| SEM_S9 | PASS |  |
| SEM_S10 | PASS |  |
| SEM_S11 | PASS |  |
| T07_CP_PLUS_ACCEPTANCE | FAIL | DRAFT_RENDERING_ERROR |
| T07_CLAIM_PLAN | SKIP |  |
| T04_LATE_NTK | PASS |  |
| T05_TIMELY_NTK | PASS |  |
| T06_CONTENT_DEFECT | PASS |  |
| T08_PAYMENT | PASS |  |
| T09_KEYING | PASS |  |
| T10_PAY_KEY | PASS |  |
| T11_BREAKDOWN | PASS |  |
| T12_LOADING | PASS |  |
| T13_PERMIT | PASS |  |
| T17_NO_SUPPORTED_GROUND | PASS |  |
| T23_DRIVER_DISCLOSURE | PASS |  |
| T20_IDEMPOTENCY | PASS |  |
| T22_PERSIST_RELOAD | PASS |  |
| T25_ADMIN_DB_STATUS | SKIP |  |
| T25_PGVECTOR_STATUS | SKIP |  |
| T25_PGVECTOR_SEARCH | SKIP |  |
| T25_DB_CHECKS | SKIP |  |
| T21_RELEASE_METADATA | SKIP |  |
| SECURITY_ERROR_LEAKAGE | PASS |  |
| SECURITY_BASICS | PASS |  |
| T24_PLACEHOLDER_VALIDATION | SKIP |  |
| T25_INVENTED_FACT_VALIDATION | SKIP |  |
| T18_SUPPORT_ONLY | SKIP |  |
| T19_ADDITIVE_POFA | SKIP |  |

## 6–18. Metrics & subsystem results

Per-test details are in `P17_LIVE_BACKEND_RESULTS.json`.

- Cases created (indexed): 22
- CP Plus: `{"case_id": "1341a256-8de3-430e-8f53-106467fa78fd", "state": "MANUAL_REVIEW", "coverage": {"shopping": false, "reason": false, "left": false, "returned": false, "cancel": false, "driver_id": false, "placeholder": false}, "letter_excerpt": ""}`

## 19. pgvector health

Read-only admin searches (if token available) are under `meta.pgvector_searches` in JSON.

## 20–21. Integrity / security

- Security headers sample: `{"access-control-allow-origin": null, "x-content-type-options": null, "x-frame-options": null, "strict-transport-security": null, "content-security-policy": null}`

## 22. Latency

- API P50: 29909 ms
- API P95: 56866 ms
- Requests: 47

## 23. Failures + first defective layer

```json
[
  {
    "id": "T07_CP_PLUS_ACCEPTANCE",
    "layer": "DRAFT_RENDERING_ERROR",
    "detail": {
      "case_id": "1341a256-8de3-430e-8f53-106467fa78fd",
      "state": "MANUAL_REVIEW",
      "outcome": "PROCESSING_ERROR",
      "coverage": {
        "shopping": false,
        "reason": false,
        "left": false,
        "returned": false,
        "cancel": false,
        "driver_id": false,
        "placeholder": false
      },
      "note": "not RELEASED \u2014 material propagation cannot be fully scored"
    }
  }
]
```

## 24. Critical blockers

```json
[
  {
    "id": "T07_CP_PLUS_ACCEPTANCE",
    "result": "FAIL",
    "detail": {
      "case_id": "1341a256-8de3-430e-8f53-106467fa78fd",
      "state": "MANUAL_REVIEW",
      "outcome": "PROCESSING_ERROR",
      "coverage": {
        "shopping": false,
        "reason": false,
        "left": false,
        "returned": false,
        "cancel": false,
        "driver_id": false,
        "placeholder": false
      },
      "note": "not RELEASED \u2014 material propagation cannot be fully scored"
    },
    "first_defective_layer": "DRAFT_RENDERING_ERROR"
  }
]
```

## 25. Recommendation

**LIVE_FIXES_REQUIRED**

Do not deploy. STOP after this report.
