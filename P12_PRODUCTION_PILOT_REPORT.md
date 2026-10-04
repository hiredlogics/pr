# P12 — Controlled Production Pilot

Frozen, staging-approved architecture. No redesign, fine-tune, pgvector,
silent prompt/KB edits, or automatic rollout broadening.

## 1. Pilot release versions

```json
{
  "evaluation_release": "P12_PILOT_2026-10-04",
  "git_commit": "ba41e052df09659f9b073b04774be53e65390da5",
  "git_branch": "feature/p8-architecture-hardening",
  "working_tree_clean": true,
  "deployment_version": "version_2",
  "build_id": "unknown",
  "environment": "development",
  "kb_release_id": "yaml-feabf179f9097464",
  "kb_release_digest": "feabf179f90974644404262d1a637f47f8fe68acbe20463150690183dc7d5ef5",
  "kb_release_null_forbidden": true,
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
    "reason": ""
  },
  "database_migration_version": 12,
  "database_migration_numbers": [
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
  "commit_short": "ba41e052df09",
  "change_control": {
    "prompt_edits_during_pilot": "FORBIDDEN",
    "kb_edits_during_pilot": "FORBIDDEN",
    "ontology_edits_during_pilot": "FORBIDDEN",
    "module_role_edits_during_pilot": "FORBIDDEN",
    "claim_plan_rule_edits_during_pilot": "FORBIDDEN",
    "requires_new_release_for_behavioural_change": true
  }
}
```

Working tree clean: **True**
KB release id: `yaml-feabf179f9097464`
KB release digest: `feabf179f90974644404262d1a637f47f8fe68acbe20463150690183dc7d5ef5`

## 0 / Pre-pilot blockers

| Blocker | Result |
| --- | --- |
| NO_SUPPORTED_GROUNDS state consistency | PASS |
| KB release pinned (non-null) | PASS |
| Secrets scan clean | PASS |
| Physical-photo smoke safe | PASS |
| Rollback documented | PASS |

```json
{
  "passed": true,
  "blockers": {
    "nsg_state_consistent": true,
    "kb_release_pinned": true,
    "secrets_scan_clean": true,
    "photo_smoke_safe": true,
    "rollback_documented": true
  },
  "nsg": {
    "passed": true,
    "enum_present": true,
    "pipeline_state": "NO_SUPPORTED_GROUNDS",
    "case_state": "NO_SUPPORTED_GROUNDS",
    "outcome": "NO_SUPPORTED_GROUNDS",
    "not_manual_review": true
  },
  "kb_pin": {
    "passed": true,
    "kb_release_id": "yaml-feabf179f9097464",
    "kb_release_digest": "feabf179f90974644404262d1a637f47f8fe68acbe20463150690183dc7d5ef5",
    "null_forbidden": true
  },
  "secrets": {
    "passed": true,
    "files_scanned": 176,
    "critical_count": 0,
    "note": "Rotate/revoke any historically exposed production/provider credentials before pilot traffic. This scan only covers the repo.",
    "operator_action_required": true
  },
  "rollback": {
    "passed": true,
    "migration_count": 12,
    "with_rollback_section": 12,
    "procedure": "1) Redeploy previous git commit / deployment_version. 2) Keep DATABASE_URL pointing at compatible schema (migrations append-only; do not DROP history tables). 3) Pin previous prompts via published KB release or prior yaml-{digest} pin. 4) Do not UPDATE/DELETE append-only fact_history / claim_plans / draft_versions (triggers block). 5) Verify /health commit + kb_release match the prior freeze."
  },
  "photo_smoke": {
    "passed": true,
    "ran": true,
    "legal_critical_accuracy_mean": 0.6797619047619048,
    "n_passed": 13,
    "n_rows": 15
  }
}
```

## 2. Number of real cases

**0** (cohort cap 20; auto-scale forbidden)

## 3. Case outcome distribution

```json
{}
```

## 4. Extraction metrics

Photo smoke:
```json
{
  "ran": true,
  "passed": true,
  "physical_photos_present": true,
  "n_rows": 15,
  "n_passed": 13,
  "n_base_photos": 3,
  "legal_critical_accuracy_mean": 0.6797619047619048,
  "normal_all_passed": true,
  "modes": [
    "normal",
    "angled",
    "low_light",
    "glare",
    "blur",
    "front_back_pair_on_normal"
  ],
  "note": "Real phone/scan PNGs from pcn_finetune_v1; degraded variants simulate angle/low-light/glare/blur. Labels used only for scoring."
}
```

Cohort legal-critical accuracy: None

## 5. Semantic metrics

Precision: None · Recall: None

## 6. Ground precision / recall

Precision: None · Recall: None

## 7. Drafting coverage

Ground: None · Material: None · Particulars: None · Unsupported: None

## 8. Validator results

Validation failure rate: None · Processing error rate: None · NO_SUPPORTED_GROUNDS rate: None

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

## 9. Operational failures

DB failures: 0 · Provider failures: 0 · 429 rate: None · 5xx rate: None

## 10. Latency

P50: None · P95: None

## 11. Cost / case

Calls/case: None · In tokens: None · Out tokens: None · Cost/case: None

## 12. Safety incidents

```json
[]
```

Safety-stop catalogue:
```json
[
  "incorrect_deterministic_legal_calculation",
  "unsupported_substantive_legal_ground",
  "invented_material_fact_released",
  "driver_identity_improperly_inferred",
  "claim_plan_ground_silently_lost",
  "validation_block_but_draft_released",
  "processing_error_shown_as_released",
  "cross_customer_data_exposure",
  "duplicate_appeal_submission",
  "corrupted_authoritative_state"
]
```

## 13. Rollback events

```json
[]
```

Rollback procedure:
```json
"1) Redeploy previous git commit / deployment_version. 2) Keep DATABASE_URL pointing at compatible schema (migrations append-only; do not DROP history tables). 3) Pin previous prompts via published KB release or prior yaml-{digest} pin. 4) Do not UPDATE/DELETE append-only fact_history / claim_plans / draft_versions (triggers block). 5) Verify /health commit + kb_release match the prior freeze."
```

## 14. Unresolved issues

- Real-customer cohort not yet executed (n=0). Append case records to `reports/p12_pilot/cohort_registry.json` during the pilot; do not auto-scale past the cap.
- Operator must rotate/revoke any historically exposed production/provider credentials before live traffic.
- Pilot must serve this exact freeze; behavioural changes require a new release.
- Case-quality review for the initial cohort is pilot QA only — not a permanent human-review dependency.

## Customer path (no bypass)

upload_front_back → classification_extraction → customer_confirmation → semantic_extraction → questions_where_needed → legal_findings → kb_eligibility → claim_plan → draft_plan → live_llm_drafting → validation → customer_outcome

## 15. Recommendation

**PILOT_EXTENSION_REQUIRED**

Do not automatically expand rollout. STOP after pilot report.
