# P17 — Live Backend Full System Test

**Target:** `https://api-p7-production.up.railway.app`
**Recommendation:** `LIVE_BACKEND_PASS`

Dedicated `LIVE_TEST_` cases only. No production customer data modified.
No deploys, no destructive SQL, no secret values in this report.

## 1. Backend availability

- Root/docs/openapi reachable via discovery probes
- Environment: `staging`
- App version: `p7-staging`
- Build: `2fd356e5-ba98-4107-835a-f686a3be182e`
- Commit: `3fe19f1c8fb639be1e75265034bc56a536489ddc`

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
  "kb_release": "kb-20261004T174817Z",
  "kb_release_digest": "780bed81012ce05c887d75284fc0079965c0893d588034703d6e10251073c7ee",
  "kb_source": "postgres-release",
  "modules": 55,
  "vision": true,
  "prompt_versions": {
    "drafting": 17,
    "extraction": 10,
    "validation": 3,
    "case_analysis": 8,
    "classification": 2,
    "page_references": 2,
    "semantic_extraction": 1
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
  "kb_drift": []
}
```

## 4. Auth result

See AUTH_* rows in results. Admin token configured locally: **True**

## 5. Test cases PASS/FAIL

- PASS: 49  FAIL: 0  SKIP: 6  TOTAL: 55

| ID | Result | Layer |
| --- | --- | --- |
| HEALTH | PASS |  |
| OPENAPI_DISCOVERY | PASS |  |
| PROBE_healthz | SKIP |  |
| PROBE_ready | SKIP |  |
| BACKEND_AVAILABLE | PASS |  |
| AUTH_ADMIN_UNAUTH | PASS |  |
| AUTH_ADMIN_INVALID | PASS |  |
| AUTH_ADMIN_VALID | PASS |  |
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
| T07_CP_PLUS_ACCEPTANCE | PASS |  |
| T07_CLAIM_PLAN | PASS |  |
| T07_CONSOLE | PASS |  |
| T07_TRACE | PASS |  |
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
| T25_ADMIN_DB_STATUS | PASS |  |
| T25_PGVECTOR_STATUS | PASS |  |
| T25_PGVECTOR_SEARCH | PASS |  |
| T25_DB_CHECKS | PASS |  |
| T21_RELEASE_METADATA | PASS |  |
| SECURITY_ERROR_LEAKAGE | PASS |  |
| SECURITY_BASICS | PASS |  |
| T24_PLACEHOLDER_VALIDATION | SKIP |  |
| T25_INVENTED_FACT_VALIDATION | SKIP |  |
| T18_SUPPORT_ONLY | SKIP |  |
| T19_ADDITIVE_POFA | SKIP |  |

## 6–18. Metrics & subsystem results

Per-test details are in `P17_LIVE_BACKEND_RESULTS.json`.

- Cases created (indexed): 22
- CP Plus: `{"case_id": "b4f22847-e76d-4ee5-aa06-cb86ac335ef3", "state": "RELEASED", "coverage": {"shopping": true, "reason": true, "left": true, "returned": true, "cancel": true, "driver_id": false, "placeholder": false}, "letter_excerpt": "I am appealing this Parking Charge Notice as the registered keeper of the vehicle LT12EST.\n\nThe Notice to Keeper for PCN LIVE_TEST_PCN_CPPLUS states that the parking event at LIVE_TEST Retail Park, M1 1AA took place on 1 June 2026, and that the notice was issued on 20 June 2026. On the operator\u2019s own dates, this was a postal Notice to Keeper in England and Wales, so the Schedule 4 provisions for the postal route apply. On that basis, the last date by which a compliant Notice to Keeper could be delivered was 15 June 2026, whereas the presumed date of delivery for a notice issued on 20 June 2026 is 23 June 2026, which is 8 days after the statutory deadline. The Notice to Keeper was therefore not delivered within the applicable statutory period for the relevant postal-notice route. The operator has not established the identity of the driver and, as the applicable Schedule 4 timing requirements have not been satisfied, liability cannot be transferred to the registered keeper under Schedule 4. For these verified reasons, keeper liability under Schedule 4 of the Protection of Freedoms Act 2012 has not been established in respect of this PCN.\n\nThe keeper\u2019s account is that the vehicle atte"}`

## 19. pgvector health

Read-only admin searches (if token available) are under `meta.pgvector_searches` in JSON.

## 20–21. Integrity / security

- Security headers sample: `{"access-control-allow-origin": null, "x-content-type-options": "nosniff", "x-frame-options": "DENY", "strict-transport-security": "max-age=31536000; includeSubDomains", "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"}`

## 22. Latency

- API P50: 11496 ms
- API P95: 38184 ms
- Requests: 60

## 23. Failures + first defective layer

```json
[]
```

## 24. Critical blockers

```json
[]
```

## 25. Recommendation

**LIVE_BACKEND_PASS**

Do not deploy. STOP after this report.
