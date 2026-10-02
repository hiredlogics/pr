# P5.5 — AI System Integrity Audit Layer

Branch `feature/p5.5-system-integrity-audit`, built on P5 (`baaf8fd`). Not pushed.

## WHAT

An inspection layer over the whole pipeline. It adds no intelligence and
changes no decision; it makes every decision visible and checks the
architecture's promises on every run. New package `pcn_appeal/integrity/`:

| Spec item | Where | Shape |
|---|---|---|
| 1. `case_execution_trace` | `trace.execution_trace(case, run_id)`; table `case_execution_trace` | `{case_id, run_id, execution_id, versions{code, kb, prompts, models, provider, validator, claim_plan_version}, final_state, state_history, steps[{stage, status, duration_ms, counts…}], ai_calls, claim_plan, audit_events}` over 11 stages INTAKE → OUTCOME |
| 2. `fact_write_audit` | existing `fact_history` (P0.4) joined into the trace and report | fact, previous, new, changed_by, source_kind/ref, reason, outcome, run_id, at |
| 3. Knowledge decision trace | existing `knowledge_match` audit (P4) in the report: SUPPORTS via facts / BLOCKS with reason | per module |
| 4. `question_trace` | existing `question_review` rows (P3), one per question with `decision` and `reason`; check `QUESTIONS_HAVE_REASONS` | APPROVED / REJECTED + reason |
| 5. `ai_execution_logs` | `ai_log.AuditedLLM` wraps the client in `AppealPipeline.__init__`; bound to the case by `CaseFile.ensure_run`; `case.ai_calls`; audit event `ai_call`; table `ai_execution_logs` | task, provider, model, prompt_version, prompt_sha256, input_sha256, input_chars, input_sources (top-level JSON keys only), images, output_sha256, output_chars, output_keys, duration_ms, status, error, at. **No prompt, payload or response text is stored.** |
| 6. `case_state_history` | `CaseFile.__setattr__` records every `state` change with `audit_index`; `trace.state_history` resolves the reason from the audit event recorded at the transition; table `case_state_history` | from, to, run_id, reason, preceded_by, at |
| 7. Integrity checks | `checks.check_case` (in memory, 9 checks) and `checks.check_store` (SQL, 7 invariants) | PASS / FAIL with evidence |
| 8. Journey harness | `journeys/*.yaml` (input only), `integrity/journeys.py`, CLI `python -m pcn_appeal.integrity journeys journeys/ --out reports/` (in-process or `--base-url` against a deployed API) | Case input → full customer journey over the real routes → admin audit → PASS / FAIL |
| 9. `CASE_REPORT.md` | `report.case_report`; on `AppealOutput.integrity["report"]`, `case_execution_trace.report`, `GET /admin/cases/{id}/audit/report.md`, and `reports/<case>_CASE_REPORT.md` from the harness | Result, Versions, Timeline, State history, Facts (PII redacted), Knowledge, Questions, Claim Plan, AI calls, Validation, Integrity checks |
| 10. `tests/test_system_integrity.py` | 24 tests | see TEST PLAN |

Checks (`check_case`):

- `DRAFT_REQUIRES_LOCKED_PLAN` — a draft exists only when this run decided a plan and exactly one plan is LOCKED.
- `NO_CLAIM_OUTSIDE_PLAN` — every sentence's module refs ⊆ approved ∪ STRUCTURAL, plus the VAL-PLAN wording check.
- `FACTS_HAVE_SOURCES` — every fact has a source kind + ref and a `fact_history` write record.
- `NO_CUSTOMER_LEAKAGE` — no internal ids (KB-/VAL-/PP-…), placeholders, trace vocabulary or system-prompt text in the letter, questions or outcome copy.
- `STATE_MACHINE_CONSISTENT` — transitions chain and end at the case's state.
- `AI_CALLS_LOGGED` — every model call has model, prompt version and input hash.
- `QUESTIONS_HAVE_REASONS` — every approved/rejected question has a reason.
- `MANIFEST_RECORDED` — a generated run has its execution manifest.
- `DRIVER_NOT_IDENTIFIED` — an unidentified-driver letter never says "I parked / I was driving".

`check_store` (SQL, `GET /admin/integrity/db-checks`, `python -m pcn_appeal.integrity db-checks`): `DRAFT_REQUIRES_LOCKED_PLAN`, `ONE_LOCKED_PLAN_PER_CASE`, `SUPPORTED_ITEMS_HAVE_SUPPORT`, `FACTS_HAVE_SOURCES`, `FACTS_HAVE_WRITE_RECORDS`, `NO_CUSTOMER_LEAKAGE`.

API: `GET /admin/cases/{id}/audit` (checks + trace + plan + report; what the harness reads), `GET /admin/cases/{id}/audit/report.md`, `GET /admin/cases/{id}/execution-trace?run_id=`, `GET /admin/integrity/db-checks?case_id=`; `GET /cases/{id}/trace` now also carries `execution_trace` and `integrity`. All admin-gated.

Hooks in existing code (small): `RunAudit._stamp` adds `at`; `CaseFile.state_history`, `CaseFile.ai_calls`, state-transition recording in `__setattr__`, `ai_log.bind` in `ensure_run`; `AppealPipeline.__init__` wraps the LLM; `generate()` calls `integrity.record` after the manifest; `manifest.provider_of` unwraps the wrapper; `_persist` writes the execution trace; store save/load for the two new append-only tables.

## WHY

P0–P5 made the pipeline deterministic and the claim plan authoritative. What was missing was proof *per case*: today a reviewer can see a letter and a trace, but not "who changed this fact", "why was this question rejected", "which model and prompt wrote this", "did the state machine do what it should", or run a customer's whole journey and get a verdict. P5.5 answers each of those from the data the engines already record, and fails loudly when any invariant breaks.

## ROOT CAUSE

Observability was spread across six audit shapes (audit_log events, fact_history, fact_sources, hypotheses, question_review, knowledge_match, claim plan trust) with no timestamps on audit events, no record of state transitions, and no record of model calls. Nothing assembled them, and nothing checked them.

## BLAST RADIUS

- No decision path changes. Before/after on the 6 reference scenarios (`ba_generate.py`, P5 commit vs this branch): identical states, outcomes, packs and letter hashes.
- `complete_json` now goes through `AuditedLLM`. It proxies every other attribute (`models`, `calls`, `SUPPORTS_IMAGES`…), re-raises provider errors unchanged and records the failure.
- Every audit entry gains `at`; every `CaseFile` gains two lists. Stored audit JSON grows by one key per entry.
- `generate()` does extra work at the end (checks + trace + report render); wrapped in try/except, never fails the run (`integrity_check_failed` audit event instead).
- New tables only (migration 0007). `save()` appends new state transitions and AI calls; `save_execution_trace` replaces the row for (case, run).

## NOT CHANGING

- No new rules, grounds, prompts or model behaviour. No operator- or PCN-specific logic anywhere, including the journeys (synthetic notices only).
- Checks observe; they do not block a release. Wiring a FAIL to MANUAL_REVIEW is a later, explicit decision.
- `PROCESSING_ERROR` and `NO_SUPPORTED_GROUNDS` remain distinct and are reported as recorded.
- Driver disclosure stays tri-state; the layer reads it, never sets it.
- The 29 known baseline failures are untouched (they include `test_web_flow`, which uploads a single-page notice and is held by the both-sides gate; the journeys supply a reverse page instead).
- The notice-sides completeness gate: a journey that omits the reverse page is reported as "the system asked for documents the journey does not supply" rather than looped.

## TEST PLAN

`tests/test_system_integrity.py` — 24 tests, all pass:

- Fact audit: every fact has a source and a write record; a changed fact keeps previous/new/writer/source/time; the report lists facts and redacts personal ones.
- Question audit: approved and rejected questions carry reasons; a reason-less row fails the check.
- Claim protection: the locked plan rejects add_item, items reassignment, item mutation and status change.
- Draft safety: an argument outside the plan is caught (`NO_CLAIM_OUTSIDE_PLAN`), a draft without a locked plan is caught, a clean run passes all 9 checks and records `integrity_check`.
- Leakage positive controls: internal id, placeholder, trace vocabulary, prompt text, driver identification — each FAILs.
- AI call log: every call logged with SHA-256 hashes, model, prompt version, timestamp; no prompt/response text; the narrative appears nowhere; a failing call is logged as ERROR and re-raised.
- State history: transitions chain from CREATED to the final state with reasons; a broken chain fails the check.
- Versions / trace shape: kb, prompts, models, claim plan version present; 11 stages in order; the generated stages SUCCESS; counts and decision recorded.
- Reload (SQLite store with FK + lock trigger): `case_state_history`, `ai_execution_logs`, `case_execution_trace` rows match memory; the trace of the reloaded case equals the original (execution id, versions, stages, transitions with reasons, call hashes); DB checks pass on a clean case, the DB refuses to unlock a plan, and a deleted write record is caught.
- Journey harness: all 5 shipped journeys PASS through the real routes (3 RELEASED, 1 MANUAL_REVIEW/PROCESSING_ERROR under the demo drafter, 1 NO_APPEAL_RIGHT); an unmet expectation fails the journey; admin audit routes gated and consistent with `/cases/{id}/trace`.

Full suite: 749 tests (725 before), the same 29 known failures, none new.
Determinism: `repeat5.py` 5 runs × 6 scenarios → 1 distinct result each; `overstay_skip_all` and `overstay_answers_yes` journeys run with `repeat: 2` and compare plan digests.

Not run (needs you): migration 0007 on PostgreSQL staging; the harness against a deployed API with the real model (`--base-url … --admin-token …`), which is where the `PROCESSING_ERROR` seen under the demo drafter should disappear.

## DEPLOYMENT

1. Apply `infra/migrations/0007_system_integrity_audit.sql` (additive, idempotent; after 0006). Rollback: drop the three tables.
2. Deploy. No config change; admin routes use the existing `ADMIN_TOKEN` / `ADMIN_TRACE_TOKEN`.
3. Smoke on staging: `python -m pcn_appeal.integrity journeys journeys/ --base-url https://<staging> --admin-token … --out reports/`, then `GET /admin/integrity/db-checks`.
4. Later (explicit decision): gate release on `integrity.passed`.
