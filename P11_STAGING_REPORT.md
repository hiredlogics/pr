# P11-STAGING — Production-like End-to-End Validation

Validate frozen P10.6 architecture in a production-like staging environment.
No architecture redesign, fine-tune, pgvector, golden modification, or production deploy.

## 1. Staging release versions

```json
{
  "evaluation_release": "P11_STAGING_2026-10-04",
  "git_commit": "cad11dab1bf5d700134d652b433a36184b79db53",
  "git_branch": "feature/p8-architecture-hardening",
  "working_tree_clean": true,
  "working_tree_dirty": false,
  "working_tree_ignored_for_freeze": [
    "reports/p11_staging/**",
    "P11_STAGING_REPORT.md"
  ],
  "kb_release": null,
  "module_count": 55,
  "ontology_version": "p10_5_ontology_v1",
  "ontology_digest": "a23fb0172da385d5",
  "module_role_version": "p10_5_roles_v1",
  "module_role_digest": "b777ffd2a9758a44",
  "claim_plan_builder_version": "3",
  "draft_plan_version": "p10_6_draft_plan_v1",
  "master_case_schema_version": 1,
  "validation_engine_version": "VAL-5",
  "draft_validation_version": "DV-1",
  "prompt_versions": {
    "classification": 2,
    "extraction": 10,
    "case_analysis": 8,
    "drafting": 17,
    "validation": 3
  },
  "model_provider_probe": {
    "provider": "unavailable",
    "models": {},
    "reason": "Missing credentials. Please pass an `api_key`, `workload_identity`, `admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable."
  }
}
```

Working tree clean at freeze: **True**

## 2. PostgreSQL result

```json
{
  "passed": true,
  "ran": true,
  "case_id": "f86e5100-8998-43aa-a1ff-7d6c6a894229",
  "steps": {
    "create_ingest": "EXTRACTED",
    "save_1": true,
    "reload_1": true,
    "reassess": true,
    "draft_state": "RELEASED",
    "reload_after_draft": true,
    "plan_ids_match": true,
    "master_digest_match": true,
    "db_facts": 24,
    "db_claim_plans": 1,
    "db_draft_versions": 1
  }
}
```

## 3. Migration result

Static:
```json
{
  "passed": true,
  "migration_count": 12,
  "numbers": [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12
  ],
  "duplicates": [],
  "missing_numbers": [],
  "ordered": true,
  "rollback_docs": "Each migration file documents a Rollback: section. Greenfield: python -m pcn_appeal.store init (postgres_schema.sql). Existing DB: apply infra/migrations/0001..0011 in order via psql. Recovery: restore from staging backup / point-in-time recovery; do not UPDATE/DELETE append-only audit tables (triggers block)."
}
```

Live:
```json
{
  "passed": true,
  "ran": true,
  "checks": {
    "select_1": true,
    "table:cases": true,
    "table:facts": true,
    "table:fact_history": true,
    "table:claim_plans": true,
    "table:draft_versions": true,
    "table:legal_findings": true,
    "table:master_case_state": true,
    "table:document_baselines": true,
    "append_only_triggers_present": true
  }
}
```

## 4. Live extraction metrics

```json
{
  "ran": true,
  "passed": true,
  "provider": "openai",
  "models": {
    "classification": "gpt-5.1",
    "extraction": "gpt-5.1",
    "page_references": "gpt-5.1",
    "semantic_extraction": "gpt-5.1",
    "case_analysis": "gpt-5.1",
    "drafting": "gpt-5.1",
    "validation": "gpt-5-mini"
  },
  "model": "gpt-5.1",
  "n_cases": 6,
  "n_passed": 6,
  "field_accuracy_mean": 1.0,
  "legal_critical_accuracy_mean": 1.0,
  "latency_p50_ms": 7694,
  "latency_mean_ms": 7104,
  "note": "Real notice transcripts from p9 notice_only; labels used only for scoring. No extraction_fields injected into the pipeline."
}
```

## 5. Live semantic-model metrics

```json
{
  "ran": true,
  "provider": "openai",
  "models": {
    "classification": "gpt-5.1",
    "extraction": "gpt-5.1",
    "page_references": "gpt-5.1",
    "semantic_extraction": "gpt-5.1",
    "case_analysis": "gpt-5.1",
    "drafting": "gpt-5.1",
    "validation": "gpt-5-mini"
  },
  "prompt_version": 1,
  "is_demo": false,
  "latency_ms": 4812,
  "structured_output_valid": true,
  "concept_count": 4,
  "affirmed_count": 3,
  "forbidden_kb_authority": false,
  "model": "gpt-5.1",
  "passed": true
}
```

## 6. Live drafting metrics

```json
{
  "ran": true,
  "provider": "openai",
  "models": {
    "classification": "gpt-5.1",
    "extraction": "gpt-5.1",
    "page_references": "gpt-5.1",
    "semantic_extraction": "gpt-5.1",
    "case_analysis": "gpt-5.1",
    "drafting": "gpt-5.1",
    "validation": "gpt-5-mini"
  },
  "prompt_version": 17,
  "draft_plan_version": "p10_6_draft_plan_v1",
  "is_demo": false,
  "latency_ms": 3857,
  "structured_output_valid": true,
  "used_sections_shape": true,
  "ground_coverage": 1.0,
  "required_particular_coverage": 1.0,
  "material_fact_coverage": 1.0,
  "unsupported_assertion_rate": 0.0,
  "cancel_request_present": true,
  "model": "gpt-5.1",
  "passed": true
}
```

## 7. Provider-failure results

```json
{
  "passed": true,
  "ran": true,
  "released_count": 0
}
```

## 8. Idempotency / concurrency results

```json
{
  "passed": true,
  "ran": true,
  "plan_id_1": "5d818ef5-70ea-526a-901e-05057b64350d",
  "plan_id_2": "5d818ef5-70ea-526a-901e-05057b64350d",
  "digest_match": true,
  "state_1": "RELEASED",
  "state_2": "RELEASED",
  "claim_plan_objects": 1
}
```

## 9. Outcome-state results

```json
{
  "passed": true,
  "ran": true,
  "cases": [
    {
      "case": "no_ground",
      "state": "MANUAL_REVIEW",
      "outcome_code": "NO_SUPPORTED_GROUNDS",
      "passed": true,
      "class": "NO_SUPPORTED_GROUNDS",
      "trace_ui_agree": true
    },
    {
      "case": "successful_appeal",
      "state": "RELEASED",
      "outcome_code": null,
      "passed": true,
      "class": "RELEASED",
      "trace_ui_agree": true
    }
  ],
  "expected_hold_classes": [
    "RELEASED",
    "NO_SUPPORTED_GROUNDS",
    "NEEDS_CUSTOMER_INPUT",
    "NEEDS_DOCUMENTS",
    "NEEDS_FACTS",
    "STOP_UNSUPPORTED_ROUTE",
    "PROCESSING_ERROR"
  ]
}
```

Front/back:
```json
{
  "passed": true,
  "ran": true,
  "cases": [
    {
      "name": "front_only",
      "ok": false,
      "reason": "front_only_or_single_page",
      "passed": true
    },
    {
      "name": "duplicate_page",
      "ok": false,
      "reason": "duplicate_front_images",
      "passed": true
    },
    {
      "name": "two_correct_pages",
      "ok": true,
      "reason": "distinct_pages_or_multipage",
      "passed": true
    },
    {
      "name": "multipage_pdf_text",
      "ok": true,
      "reason": "distinct_pages_or_multipage",
      "passed": true
    }
  ],
  "note": "Does not invent reverse-page content; gate blocks incomplete uploads"
}
```

## 10. Security findings

```json
{
  "passed": true,
  "files_scanned": 563,
  "critical_count": 0,
  "note": "Rotate/revoke any historically exposed live key before staging sign-off. No secret values are printed.",
  "findings": []
}
```

## 11. Latency / cost metrics

```json
{
  "regression_p50_ms": 2660,
  "regression_p95_ms": 3213,
  "semantic_latency_ms": 4812,
  "drafting_latency_ms": 3857
}
```

Estimated cost per completed appeal: not metered in this harness (provider dashboard).

## 12. Staging E2E / regression

```json
{
  "passed": true,
  "ran": true,
  "n_cases": 11,
  "n_e2e_ok": 11,
  "n_clean_upstream": 6,
  "clean_ground_coverage": 1.0,
  "latency_p50_ms": 2660,
  "latency_p95_ms": 3213
}
```

Invariants:
```json
{
  "passed": true,
  "tests_run": 112,
  "checks": {
    "Master Case": true,
    "FactManager / P8 architecture": true,
    "P10.3 semantic + roles": true,
    "P10 remediation": true
  }
}
```

## Release gates

| Gate | Result |
| --- | --- |
| P8/P10 invariants | PASS |
| Real PostgreSQL | PASS |
| Migrations static | PASS |
| Migrations live | PASS |
| Draft coverage 100% (clean) | PASS |
| Provider failures | PASS |
| Idempotency | PASS |
| Outcome consistency | PASS |
| Front/back | PASS |
| Security (no critical) | PASS |
| Working tree clean | PASS |
| Live semantic | PASS |
| Live drafting | PASS |
| Live extraction | PASS |

## 13. Unresolved issues

- Staging `DATABASE_URL` is loaded from gitignored `.env.staging.local` (isolated `pcn_appeal_p11`).
- Live extraction uses p9 notice_only transcripts rendered to JPEG (vision path); not physical phone photos.
- Estimated cost per appeal is not metered in-harness (use provider dashboard).
- Working tree must be clean at freeze for the release gate (staging release commit).

## 14. Recommendation

**READY_FOR_PRODUCTION_PILOT**

Do not production deploy. STOP after staging review.
