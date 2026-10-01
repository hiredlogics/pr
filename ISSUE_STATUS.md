# Private Parking automation: status of the 45 client issues

Audited on 2026-10-01 against branch `phase-2-intake-router` at `2a3f811`. **Package A update:** the seven high-risk defects below are FIXED-LOCAL (see `CHANGELOG_package_a.md`).

- Read-only code audit of the backend, frontend, KB YAML and prompts.
- Spot claims marked ✔ were re-verified by hand.
- **`origin/main` contains only Phase 0 (`1e7b103`).** Phase 2 and the EV, persistence and WebP fixes are local only.
- Nothing has been deployed since the last client test. Deployed behaviour is unverified.

**Statuses**

| Status | Meaning |
|---|---|
| FIXED | Done in the code |
| FIXED-LOCAL | Done in code that hasn't been pushed or deployed yet |
| PARTIAL | Some of it is fixed; the gap is stated |
| OPEN | Not addressed |

## Summary

| Status | Count | Issues |
|---|---|---|
| FIXED / FIXED-LOCAL | 11 | 1, 10, 14, 16, 17, 18, 27, 35, 36, 37, 43 |
| PARTIAL | 21 | 2, 3, 5, 7, 8, 9, 11, 12, 15, 19, 20, 21, 24, 25, 26, 32, 34, 38, 39, 40, 41 |
| OPEN | 13 | 4, 6, 13, 22, 23, 28, 29, 30, 31, 33, 42, 44*, 45 |

*44 is a working rule rather than a code item. One allegation-substring gate remains (see #3).

## Highest risk: wrong or unsafe output could reach a customer (Package A: all FIXED-LOCAL)

| # | Defect | Evidence |
|---|---|---|
| 41/15 | An AI analysis failure is told to the customer as "no supported ground" ✔ | `analysis.py:193,204` log `case_analysis_error`; `outcome.py` never reads it; `orchestrator.py:390` sets `analysis_complete_no_supported_grounds` |
| 20/21 | PoFA marked SATISFIED with nothing seen ✔ | `recovery.py:419-422`: `has_pass=None` → `defect = None is False` → False → SATISFIED |
| 25 | VRM integrity check misses misreads ✔ | `validation.py:403` only matches `AB12CDE`-shaped tokens, so RX7V5PP is never compared. The PCN check is a substring test. |
| 28 | "Attempted" payment becomes "A payment was made" | `account.py:119`, `questions.yaml:17` and `analysis.py:65` store "made or attempted" as `payment_made`; PP-PAY-001 asserts the payment was made |
| 8 | Any prose answer sets the fact to True | `questioning.py:103-111`: "No, the children were not in the car" → True |
| 23/26 | The generic "instant" inference is in approved wording (seen in today's live letter) ✔ | Building block PP-BAY-001 (`building_blocks.yaml:125`) and the KB-BAY-01 core proposition |
| 2 | Two photos of the front count as complete | `notice_completeness.py:146` counts distinct images, and `:157-161` accepts front-side "Protection of Freedoms" text as the reverse |
| 33 | EV-charging false match is live on main | Fix `4ffca8e` is local only |

## Full register

### 1. Routing and completeness

| # | Status | Finding |
|---|---|---|
| 1 | FIXED-LOCAL | Phase 2 neutral classifier and router. Debt, council, claims, bailiff, CCJ, unknown and operator-response documents stop before any engine runs. Debt recovery returns the specified wording and template CTA. Verified live: 11/11 synthetic types, plus today's real NTK. |
| 2 | PARTIAL | Front + back is enforced, and identical images are rejected. Gaps: two *different* front photos pass; front text mentioning "Protection of Freedoms" passes as a reverse; any text containing a form feed passes; the result screen's "Add the other side" button only re-posts `/appeal/{id}` and offers no upload (`page.tsx:195-202`). Needs a per-page side label (FRONT / REVERSE / CONTINUATION) from the classifier. |
| 20 | PARTIAL | Reverse missing → UNRESOLVED (`recovery.py:480-500`). But see the SATISFIED-with-nothing-seen bug above. |

### 2. Question authority

| # | Status | Finding |
|---|---|---|
| 3 | PARTIAL | No ground → question decision tree remains in live code. `_unlocking_questions` and `SITUATION_FALLBACK` are dead code (`analysis.py:487-536`, `64-82`). Left: KB-REC-01 gates on an allegation substring; gate-satisfied modules are pinned into the candidate list and force a second "reassessment" model call. |
| 4 | OPEN | Up to 3 questions per round, all rendered together (`QuestionsStep.tsx:100`). The prompt's thin-pack rule says "you MUST ask 1–3… (payment made, permit held, genuine customer visit, signage, breakdown…)" whatever the narrative says (`prompts.yaml:393-397`). Empty material fields are fed to the model as `ask_if_customer_knows` (`recovery.py:553-560`). |
| 7 | PARTIAL | "Kids in the car" sets only child facts, and no KB gate uses them. The thin-pack MUST-ask rule can still produce a permission question. `permit_held` fires on a bare "authorised" (`account.py:184`). |
| 37 | FIXED | `questions.yaml` is vocabulary and answer types only. Its question text is reachable only from dead code. |
| 38 | PARTIAL | `FactStatus` has no not-askable, irrelevant or operator-held states. `do_not_ask` is merged into "known" (`analysis.py:593`; `prompts.yaml:357`). "UNKNOWN" is stored as a value, so `has()` treats it as known (`recovery.py:313`). |
| 39 | PARTIAL | The postcode copy is dead (`flags.ts:56`), but the model may still ask for `site_postcode` whenever jurisdiction is unknown, without checking that it is material (`analysis.py:716-719`). |
| 42 | OPEN | A "Skip the rest of the questions" button exists (`QuestionsStep.tsx:183`), and `AnswersIn.skip` is in the API. |

### 3. Ground authority

| # | Status | Finding |
|---|---|---|
| 11 | PARTIAL | KB-LAND-01's gate is circular: `claim_plan.py:115-125` sets `authority_challenge_proportionate=True` whenever the model proposes it, and never clears it. A "simple letter" path still releases support-only packs (`orchestrator.py:404`). The route name is `LANDOWNER`, but code tests `"LAND"` (`analysis.py:735`, `recovery.py:641`). |
| 12 | PARTIAL | There is no locked Claim Plan. Nine layers can change the selection after Case Intelligence: candidate window, claim-plan veto, reassessment call, three re-analysis rounds, reasoning conflict resolution over *all* eligible modules, block gates, drafter `no_ground_reason`, and validation sentence-trimming (which can delete a whole ground). Nothing checks that every finalised claim appears in the letter. |
| 27 | FIXED | Every ANPR module needs its own trigger fact. Caveat: `multiple_visits` can come from a narrative regex. |
| 6 | OPEN | No evidence-method fact (ANPR vs on-site photo). Only question filtering uses entry/exit times. |
| 31 | OPEN | KB-EV-01 (any photo, receipt or dashcam evidence) maps only to PP-ANPR-004, whose wording says the vehicle "was not continuously present… ANPR sequence". |
| 30 | OPEN | Module and block mismatches give zero approved wording: INFRA-01, GRACE-01/02, CUST-01, ACT-03, AUTH-01/02/03, CON-01. R-08b uses truthiness, so `exit_delay_min=0` withholds wording. |
| 29 | OPEN | AI-HOSP-001 requires `disability_extra_time`, so hospital cases without a disability fact get no wording. KB-HOSP-03 has no blocks. |
| 40 | PARTIAL | NO_SUPPORTED_GROUNDS exists. It is also reached on analysis failure (#41), on a selection emptied downstream, and on a support-only hold. `generate()` doesn't re-check completeness. |

### 4. Fact integrity

| # | Status | Finding |
|---|---|---|
| 9 | PARTIAL | Propositions travel in the pack independently of modules. A structured "yes" answer creates no proposition (`account.py:485`). Claim-plan coverage counts only module-linked facts. |
| 10 | FIXED | The children rebuttal (KB-BAY-02 / PP-BAY-002) is independent of the ≤5-minute gate. |
| 17/18 | FIXED | FORMALLY_IDENTIFIED is set only by strict external disclosure (`disclosure.py:72,110`). Narrative cannot set it. |
| 24 | PARTIAL | The prompt has a system-wide "direct fact contradiction" rule. But `account_contradicts_allegation` is only set for bay-type allegations (`account.py:421-437`), and no validator enforces the order. |
| 26 | PARTIAL | Entry, exit, observation and event times are separate. There is no `photo_time`. The PP-BAY-001 wording asserts the "instant" inference. |
| 28 | OPEN | See the high-risk table. |
| 8 | PARTIAL | VAL-CUSTOMER-COPY blocks any copied 6-word phrase. The first-person rewrite has about 7 patterns. The prose → True defect is above. |

### 5. PoFA

| # | Status | Finding |
|---|---|---|
| 19 | PARTIAL | Deterministic. The name-driver and pass-to-driver invitations are scanned separately, but only a missing *pass-on* is ever flagged. A missing name-driver invitation never is. Other paragraph 9 elements aren't scanned. |
| 21 | PARTIAL | Deterministic (`legal/pofa.py`). Has the bug above. |
| 22 | OPEN | Findings reach the pack as codes only. Dates and day counts never reach the draft, and PP-POFA-003/004 have no date placeholders. |

### 6. Drafting

| # | Status | Finding |
|---|---|---|
| 23 | OPEN | Generic block wording is still released (today's letter). There is no ground → fact → rule → application structure in the prompt (`prompts.yaml`). |
| 25 | PARTIAL | See the high-risk table. |

### 7. Customer UI and outcomes

| # | Status | Finding |
|---|---|---|
| 5 | PARTIAL | The Next screens are clean. Leaks remain: the PDF prints "Grounds cited: Keeper liability (PoFA Schedule 4)…" (`pdf.py:215`); the legacy page at `GET /` lists grounds; the masthead shows the provider and "N legal modules" (`page.tsx:217`); `/cases/{id}/confirmation` returns confidence and source (data, not rendered). |
| 14 | FIXED | Template fallback is gone; validation regenerates three times, then trims. Caveat: every hold is written to `review_queue` as "validation failed after max attempts". The legacy page says "A case handler will review it". |
| 15/41 | PARTIAL | Outcome codes exist (PROCESSING_ERROR, NEEDS_DOCUMENTS, NEEDS_FACTS, NO_SUPPORTED_GROUNDS…). The old merits string is gone. The analysis failure is mislabelled (above). A retrieval exception gives an HTTP 500. |
| 16 | FIXED | "What went into this" is gone. Trace is admin-only. |
| 35/36 | FIXED | TemplateDrafter can't release, so the failures it used to hide now surface. Fixing them is the upstream items above. |

### 8. Release and change control

| # | Status | Finding |
|---|---|---|
| 13 | OPEN | No temperature or seed, no dated model snapshots, and the returned `model` / `system_fingerprint` are not recorded (`llm.py:116`). Live runs will vary. |
| 32 | PARTIAL | The release-vs-YAML drift check refuses startup, and `/health` reports provenance. Missing: per-case model and prompt versions for extraction, classification, analysis and validation; retrieval index version; Code version. `validator_version` is the constant "VAL-1". YAML has 55 modules and the JSON 58 (KB-BAY-01/02, POFA-06, REC-01 vs GOV-01..07), though the JSON is not used at runtime. |
| 33 | OPEN | Main has only Phase 0; four commits are local. Retest only after the deploy checklist (push, deploy backend and frontend, `store init`, KB sync, `/health` commit check). |
| 34 | PARTIAL | Unit tests are separate from live runs. There is no real-provider test layer, no CI, and no browser/PDF or production smoke tests. Live runs exist only as scratch scripts. |
| 43 | FIXED | A change log for every change (6 files at the repo root). |
| 44 | Rule | Today's fixes are generic. KB-REC-01's allegation substring gate is the remaining borderline. |
| 45 | OPEN | 16 scenario tests, no T01-T20 numbering, no anonymised real-case pack, and no live harness. **29 tests already fail on a stale expectation** (old single-side fixtures, the reverse-page question, prompt versions, template removal). That baseline hides new regressions and should be fixed first. |
