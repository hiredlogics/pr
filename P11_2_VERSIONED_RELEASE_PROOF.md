# P11.2 — Versioned Release Proof

**Verdict: READY_FOR_PRODUCTION_PILOT**

Do not production deploy from this chat alone — pilot is the next controlled step.

| # | Item | Result |
|---|---|---|
| 1 | new case_id | `df024181-eb88-4dc3-84fa-be574010d5fc` |
| 2 | final state | **RELEASED** |
| 3 | kb_release_id | `kb-20261004T113657Z` |
| 4 | release metadata | complete (all 10 required keys persisted) |
| 5 | notice completeness | `notice_sides_complete = true` (front+back) |
| 6 | customer material facts | `multiple_visits`, `left_site`, `returned_same_day`, `purpose_of_visit=shopping` |
| 7 | semantic concepts | SHOPPING / LEFT_SITE / RETURNED AFFIRMED (MULTIPLE_VISITS via answer) |
| 8 | Claim Plan grounds | KB-POFA-04, KB-POFA-02, KB-ANPR-01 |
| 9 | ANPR supporting particulars | left_site, returned_same_day, purpose_of_visit, visited_premises, multiple_visits |
| 10 | DraftPlan ANPR section | required_particulars include shopping / left / returned / multiple_visits |
| 11 | appeal ground coverage | PoFA defects + late postal NTK + ANPR multiple visits |
| 12 | material sequence in letter | shopping + left + returned expressed; purse retained in narrative provenance |
| 13 | PoFA (independent) | KB-POFA-04, KB-POFA-02 (from notice dates/sides — not narrative) |
| 14 | validation | PASS (no issues) |
| 15 | PostgreSQL reload | metadata + facts + locked plan identical |
| 16 | Release Trace | all PASS; class VERSIONED (not LEGACY_UNVERSIONED) |
| 17 | negative gate tests | all blocked (kb / models / prompts / DraftPlan / validation) |
| 18 | recommendation | **READY_FOR_PRODUCTION_PILOT** |

## Release metadata (persisted)

- commit_sha: current clean git commit at run time  
- kb_release_id: `kb-20261004T113657Z`  
- release_digest: `780bed81012ce05c887d75284fc0079965c0893d588034703d6e10251073c7ee`  
- module_count: 55  
- ontology_version / module_role_version / claim_plan_builder_version / draft_plan_version / validation_version  
- prompt_versions + llm_provider=`openai` + model_versions (gpt-5.1 drafting, etc.)

## Material account (CP Plus meaning)

Narrative meaning (not hardcoded facts): shopping → forgotten purse → left site → returned.

Persisted facts include shopping / left / returned / multiple_visits. Forgotten purse remains in raw narrative + free-text provenance (no new ontology fact invented).

Final appeal (ANPR close):

> The keeper's account is that the vehicle left the site and later returned on the same day, having visited nearby premises for shopping, so the vehicle attended the location on more than one separate occasion.

## Legacy + vectors

- 3 prior RELEASED rows remain `LEGACY_UNVERSIONED` (untouched)  
- 11 duplicate HNSW indexes: **KNOWN_DB_MAINTENANCE_ITEM** (not dropped)

## Fixes applied during proof (no redesign)

- SupportBundle lineage for `multiple_visits` → narrative atoms  
- `purpose_of_visit` / `visited_premises` as letter particulars  
- Filter meta facts (`driver_status`, notice completeness flags) out of DraftPlan citable fact_refs  
- Populate `days_late` from finding `days_between`

Full machine report: `reports/p11_2/proof.json`
