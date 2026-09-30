# Change log — outcome boundary (live “could not write an appeal”)

## Proven cause (PCN 88812207965 / case `3cb620c9-5362-4344-b4e9-f6bf8e086f6c`)

| Layer | Value |
|---|---|
| Deployed commit (prod ≈) | `3998e83` (Railway image; no SHA in meta) |
| KB release | `kb-20260930T005927Z` |
| Prompts in release | extraction **8**, case_analysis **5**, drafting **7**, validation **2** |
| Provider / models | openai · gpt-5.1 (extract/CI/draft), gpt-5-mini (validate) |

**UI path:** `frontend/components/ResultStep.tsx` showed *“Nothing in what you sent us…”* for **every** `MANUAL_REVIEW` / held state — no backend outcome code.

**Actual pipeline outcome (audit):**
1. Case Intelligence finalized **`KB-LAND-01`** after answers (`payment_made`, `genuine_customer`, `signage_issue_raised` = true; narrative `"nothing"`).
2. `generate()` **ground_recovery** wiped selection `was=[KB-LAND-01] → now=[]`.
3. Empty pack → `no_ground` → STRUCTURAL shell → **`VAL-SUBSTANCE`** → `MANUAL_REVIEW`.
4. Customer message treated that **processing/empty-pack failure** as a **merits** finding.

**Classification:** model/validation + retrieval failure after wipe — **not** genuine “complete analysis with no supported grounds”, not missing docs, not out of scope.

## Fixes (no deploy until review)

| Path | Change |
|---|---|
| `pcn_appeal/engines/outcome.py` | Customer outcome codes; `classify_hold` separates PROCESSING_ERROR / NO_SUPPORTED_GROUNDS / NEEDS_* |
| `pcn_appeal/orchestrator.py` | Preserve selection on wipe; short-circuit empty analysis; attach outcome on all holds |
| `pcn_appeal/api.py` | Expose `outcome*` on held customer payloads; gate `/trace` with `ADMIN_TRACE_TOKEN` when set |
| `pcn_appeal/llm.py` | DemoLLM drafts pack-faithful letters (not TemplateDrafter-after-AI-failure) |
| `frontend/components/ResultStep.tsx` | Map by `outcome`; remove “What went into this”; continue same case |
| `frontend/app/page.tsx` / `lib/types.ts` | `onContinue` + outcome fields |
| `tests/test_outcome_boundary.py` | Boundary regressions for the live wipe path |
| `tests/test_scenarios.py` | Bad LLM draft → PROCESSING_ERROR (no template RELEASE) |

## Intentionally unchanged

- No PCN-specific production rule for `88812207965`.
- No inventing grounds / restoring substantive TemplateDrafter on AI failure.
- Case answers remain; Continue re-runs `/appeal/{id}` without restart.
