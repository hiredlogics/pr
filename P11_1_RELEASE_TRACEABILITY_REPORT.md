# P11.1 — Release Traceability + Live Case Verification

See full report: [`reports/p11_1/P11_1_RELEASE_TRACEABILITY_REPORT.md`](reports/p11_1/P11_1_RELEASE_TRACEABILITY_REPORT.md)

## Verdict: TRACEABILITY_FIX_REQUIRED

Gate, persistence (`release_metadata`), Release Trace panel, and 17 unit tests are in place. Staging still has only `LEGACY_UNVERSIONED` RELEASED rows (3/3 `kb_release_id` null). No VERSIONED RELEASED case has been proven end-to-end yet — do not production deploy.

### Trace sample
- Case `f86e5100-8998-43aa-a1ff-7d6c6a894229`: facts/sources/findings/claim plan/draft join OK; `kb_release_id` null → LEGACY_UNVERSIONED

### KB pin
- `kb-20261004T113657Z` → digest `780bed81…`, 55 modules

### Vectors
- 129 embeddings @ 1024, model `hashing-v1`, no orphans/nulls/dupes
- 11 HNSW indexes = ACCIDENTAL_DUPLICATES (not dropped)

### Required release metadata (new cases)
`commit_sha`, `kb_release_id`, `ontology_version`, `module_role_version`, `claim_plan_builder_version`, `draft_plan_version`, `validation_version`, `prompt_versions`, `llm_provider`, `model_versions` — missing any → block with internal `RELEASE_METADATA_INCOMPLETE`
