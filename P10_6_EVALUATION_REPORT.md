# P10.6 — Draft Fidelity + Claim Plan Rendering Contract

Make the drafting layer faithfully render every supported Claim Plan ground
and every required material particular. No P8 redesign, no ground-selection
change, no semantic ontology change, no fine-tune, no pgvector, no deploy.

## 1. First-defective-layer analysis

On clean-upstream DEVELOPMENT cases, when Claim Plan / SupportBundle /
DraftRequirement / DraftContext are complete but prose misses a ground, the
defect is classified **DRAFT_RENDERING_ERROR**.

DEVELOPMENT layer counts:
```json
{
  "CLAIM_PLAN": 3,
  "NONE": 8
}
```

Trace shape: GROUND → SupportBundle → DraftRequirement → DraftContext →
DraftSection → Generated paragraph → Final sentence link.

## 2. DraftPlan schema

Version: `p10_6_draft_plan_v1`

```
DraftPlan {
  case_id, claim_plan_id, version,
  sections: [DraftSection...],
  introduction_requirements[], closing_requirements[],
  leading_ground_ids[], support_only_ids[]
}
DraftSection {
  section_id, ground_id, ground_ids[], purpose, role,
  supporting_fact_ids[], supporting_fact_names[],
  derived_fact_ids[], evidence_ids[], legal_finding_ids[],
  required_particulars[], particular_values{},
  prohibited_claims[], required_outcome,
  support_module_ids[], context_chunk_ids[], merged
}
```

## 3. Claim Plan → DraftPlan transformation

`pcn_appeal.drafting.plan.build_draft_plan(pack)`:
- One DraftSection per leading / substantive(+evidence) ground
- Explicit permitted merge: PAYMENT + KEYING → single section (`merged=true`)
- SUPPORTING_PROPOSITION / LEGAL_CONCLUSION attach as `support_module_ids`, not standalone paragraphs
- Particulars taken from DraftRequirement + SupportBundle values present in the locked plan

## 4. LLM structured-output contract

Prompt drafting **v17**. Preferred output:

```json
{"opening": "...", "sections": [{"section_id","ground_ids","text","fact_ids_used","finding_ids_used"}], "closing": "..."}
```

Legacy `paragraphs` still accepted. Assembler: `assemble_structured_draft`.

## 5. Section / ground ownership

Every substantive sentence carries `module_refs` = section `ground_ids`.
`Draft.section_ownership` records `merged_ground_ids` when applicable.
Merged grounds must both be semantically expressed.

## 6. Validators changed

- **VAL-GROUND-COVERAGE** (new / strengthened): DraftSection exists + rendered text + semantic expression; filler does not count; BLOCK
- **VAL-COVERAGE**: retained; also flags DraftPlan grounds without linked text
- **VAL-DRAFT-PARTICULARS**: reports `ground_id`, `section_id`, missing particular, supporting fact ids
- **VAL-INVENTED** / unsupported assertion path unchanged (target 0%)

## 7. Targeted regeneration

On coverage/particulars failure, orchestrator calls `LLMDrafter.regenerate_sections`
for failed section_ids only (max 2 section retries). Claim Plan is not modified.

## 8–12. DEVELOPMENT before/after draft metrics

| Metric | Before (P10.5) | After (P10.6) |
| --- | --- | --- |
| Clean-upstream ground coverage | 20% | 100.0% |
| Material-fact coverage | — | 100.0% |
| Required-particular coverage | — | 100.0% |
| Unsupported assertion rate | — | 0.0% |
| Validator pass rate (clean) | — | 100.0% |
| No-ground hold OK | — | 100.0% |

Clean-upstream DEVELOPMENT cases: 8/11 (3 correctly held with empty Claim Plan:
support-only, no-ground, mechanical with no selectable lead).

First defective layer on clean-upstream releases: **NONE** (was DRAFT_RENDERING_ERROR).

## 13. Real-model probe

```json
{
  "provider": "demo",
  "models": {},
  "reason": "no OPENAI_API_KEY set",
  "prompt_version": 17,
  "temperature": null,
  "structured_output_valid": null,
  "ground_coverage": null,
  "latency_ms": null,
  "cost": null,
  "ran": false,
  "note": "No live OpenAI credentials in this environment; DemoLLM/reference only."
}
```

## 14. P8/P10 invariant results

```json
{
  "passed": true
}
```

## 15. Fresh sealed drafting-holdout result

| Metric | HOLDOUT |
| --- | --- |
| Ground coverage (clean) | 100.0% |
| Material-fact coverage | 100.0% |
| Required-particular coverage | 100.0% |
| Unsupported assertion rate | 0.0% |
| Cases | 4 |

## Frozen semantic observations (unchanged)

```json
{
  "DEV_semantic_recall": 0.794,
  "VAL_semantic_recall": 0.733,
  "sealed_holdout_semantic_recall": 1.0,
  "note": "Existing P10.5 observations \u2014 not changed in P10.6"
}
```

## Recommendation

**READY_FOR_STAGING**

Do not fine-tune. Do not add pgvector. Do not production deploy. STOP after evaluation.
