# Change log — repair/decouple-material-account-claim-plan

## Proven cause
Material customer facts (e.g. children present) only reached the letter through
KB-BAY-01 → PP-BAY-002, and KB-BAY-01 requires `observation_window_min <= 5`.
Downstream stages also auto-seeded REC and stripped LAND, silently changing
Case Intelligence’s finalized claims. TemplateDrafter could release a different
substantive letter after AI drafting failure.

## Exact changes
| Component | Change |
|---|---|
| `engines/claim_plan.py` | **New** — finalize claims from CI proposals; account material facts; list omissions for reassessment only (no strength≥50 auto-insert; no LAND auto-delete) |
| `engines/analysis.py` | Replace `_veto` seed/drop with `_finalize_claims`; one bounded reassessment; pin gate-satisfied modules into candidate visibility |
| `engines/reasoning.py` | Stop empty-selection REC seed; always expose `factual_rebuttal` + `timing_argument` + `claim_plan` in `case_context` |
| `engines/account.py` | Minimal negation guard for occupancy/eligibility facts (proven by failing test) |
| `data/kb_modules.yaml` | **KB-BAY-01** timing-only; **KB-BAY-02** account rebuttal (no observation gate); **KB-LAND-01** `use_when: authority_challenge_proportionate` (not always-true) |
| `data/building_blocks.yaml` | PP-BAY-001 timing; PP-BAY-002 bound to KB-BAY-02 |
| `orchestrator.py` | No TemplateDrafter substantive fallback on AI failure → MANUAL_REVIEW |
| `drafting/drafter.py` | Docstring: template is layout/unit-test helper, not AI-failure release path |
| `data/prompts.yaml` | drafting v8 / case_analysis v6 — factual rebuttal independent of BAY timing |
| `tests/support.py` | ReferenceAnalysisLLM drafts from pack; does not auto-select always-on LAND |
| `tests/test_account_decouple.py` | **New** — window 0/45/missing; children variants; claim-plan ownership; model failure |
| `tests/test_prompts.py` / `test_case_intelligence_fixes.py` | Version bump; thin-pack asks via CI `ask=` |

## Before / after trace (Parent & Child + children confirmed)

| | Before | After |
|---|---|---|
| Account fact | Lost unless KB-BAY-01 selected | Always in `case_context.factual_rebuttal` |
| Window 45 | No BAY → no account paragraph | KB-BAY-02 can still select; timing KB-BAY-01 off |
| LAND | always-true + multi-stage strip | CI-selected only (`authority_challenge_proportionate`) |
| AI draft fail | TemplateDrafter substantive letter | MANUAL_REVIEW |

## Tests run (OK)
- `tests.test_account_decouple`
- `tests.test_prompts.Registry`
- `tests.test_material_account`
- `tests.test_case_intelligence_fixes.LeadingGroundTests`
- `tests.test_private_parking_v2`
- `tests.test_question_authority`

## Environment (local ≠ prod — not deployed)
| | Branch | Production |
|---|---|---|
| Git | `repair/decouple-material-account-claim-plan` | ≈ `3998e83` |
| KB | local YAML (BAY-02 + LAND policy) | `kb-20260930T005927Z` |
| Prompts | case_analysis **6**, drafting **8** | case_analysis **5**, drafting **7** |
| Provider | test doubles | openai gpt-5.1 / gpt-5-mini |

DemoLLM tests are not live drafting acceptance. No staging/client retest requested yet.

## Remaining uncertainties
- Production still has KB-LAND-01 always-true until KB sync after review
- Duplicate ACTIVE/REVIEW rows for some modules in prod DB
- Railway image has no recorded git SHA

## Rollback
```bash
git checkout main
# discard branch work if needed:
# git branch -D repair/decouple-material-account-claim-plan
```
Production is untouched; no KB sync or Railway deploy was performed.
