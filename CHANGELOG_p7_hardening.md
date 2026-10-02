# P7 — Pre-release hardening: blockers B1–B7

Seven release blockers from the architecture review, closed in order, each
with its own proving test module. Final regression: **910 tests ran, 0
failures, 0 errors, 7 skipped** (`test_pg_integration`, needs a live
pgvector Postgres). Journey suite **5/5 against goldens**.

Everything below is generic system behaviour: no operator-specific or
PCN-specific rules, nothing keyed to a test fixture.

## B1 — Legal-critical input gate (`tests/test_legal_critical_gate.py`, 26 tests)

A registry of legal-critical fields (`engines/extraction.py: LEGAL_CRITICAL`)
with four always-on protections:

- **Deterministic cross-check (EX-18)**: every legal-critical model reading is
  re-derived from document text by `FIELD_PATTERNS`. Agreement produces a
  `cross_checked_fields` DERIVED fact; mismatch demotes the field to
  UNCERTAIN, raises a `conflict:<field>` flag, and audits
  `cross_check_mismatch` with both values. Prose fields agree on any-reading
  containment.
- **Chronology validation** (`validate_chronology(case, *, stage)`): future
  dates, issue-before-event, NTD/NTK ordering, received-before-issue,
  exit-before-entry — each demotes to UNCERTAIN regardless of prior status,
  including CORRECTED, at every stage it is re-run.
- **Confirmation discipline**: confirm never promotes an UNCERTAIN field
  (audit `uncertain_not_auto_confirmed`); unparseable date/time corrections
  raise instead of storing garbage; `pcn_conflict` clears only on an explicit
  PCN-number correction. Fact-graph rule 5: a confident DOCUMENT reading is
  never silently overwritten by a customer correction — it opens a
  NEEDS_CONFIRMATION conflict; only UNCERTAIN fields are the customer's to
  correct.
- **Answer provenance**: `answer()` refuses facts that were never asked
  (audit `answer_refused_unasked`); notice route is asked, not guessed
  (`_route_question`); findings with `unconfirmed_support` demote
  VERIFIED → UNRESOLVED unless every supporting fact is
  CONFIRMED/CORRECTED/ANSWERED/DERIVED or cross-checked.

Closing B1 healed three baseline reds by fixing a genuine wiring defect:
claim-plan exclusions now map onto `UNLOCKABLE`, so gate-half questions are
generated for plan-excluded modules again.

## B2 — Hypothesis gate (`tests/test_account_hypotheses.py`)

Narrative phrases are hypotheses, never facts. A claim in the customer's
account ("I paid") generates the confirming question; only the answer sets
the fact. Driver identification is never set from narrative.

## B3 — Materiality stopping rule (`tests/test_question_budget.py`)

One question at a time, ranked; a question is asked only if its answer could
change a module's result (R5 materiality), and only SHOWN questions are
marked asked (Q-07). Derived facts are never asked (R3).

## B4 — Critical integrity checks gate release (`tests/test_integrity_gate.py`)

Critical integrity-check failures hold the case in MANUAL_REVIEW; release
requires a clean critical set.

## B5 — KB release discipline (`tests/test_kb_release_discipline.py`, 17 tests)

- `kb_digest` now covers the whole KB: modules, blocks, **and relation edge
  ids** (UUIDv5, deterministic).
- Release manifests pin `block_texts` (sha256 per block) and the curated
  `relations`; `KnowledgeGraph.from_release` serves the release's own
  relations. `release_differs_from_yaml` names drift per block/relation and
  says when a release predates the pins ("republish").
- `kb_releases` is insert-only: code pre-check plus a SQL trigger
  (`infra/migrations/0010_kb_releases_insert_only.sql`, also in
  `postgres_schema.sql`).
- `POST /admin/kb/releases` (admin-gated, 503 without DB) runs the journey
  suite in-process as a release gate — 409 unless green and the release id is
  free — then reloads the serving graph. Gate cases are ephemeral
  (`C-GATE-*`, never persisted; the ephemeral header requires the admin
  token). Note: the gate runs synchronously (minutes) and needs
  httpx/TestClient plus `journeys/` + goldens shipped in the image.
- Production never serves YAML: `_load_kg` refuses on every path, including
  `ALLOW_KB_DRIFT`.

(The module-content decision on KB-BAY-01/BAY-02/POFA-06/REC-01 is with
Alpha; dossier delivered separately.)

## B6 — Assertion-only finding specs (`tests/test_assertion_only_findings.py`)

Finding specifications assert; they do not compute. Calculations live in the
engines that own them.

## B7 — Baseline reds to zero

All 29 baseline reds dispositioned (29 → 0; full record in the session
dispositions file). Production defects found and fixed via red tests:

1. **UNLOCKABLE wiring** (B1, above).
2. **PDF page join** — `ingest._pdf_text` joined pages with `"\n"`, hiding
   the page boundary from `notice_completeness.upload_pages_sufficient`;
   genuine two-page notices were wrongly blocked with
   NOTICE_SIDES_REQUIRED. Pages now join with `"\f"`.
3. **Burned questions** — with answers in hand, `auto_appeal` ran a
   pre-answer reanalysis that SHOWED (and so consumed, Q-07) a held question
   the customer never saw. That reanalysis is skipped when answers are
   present; `answer()` runs it itself.
4. **Placeholder leak (R-08c)** — `{{placeholder}}` tokens in approved block
   wording are now filled deterministically from usable facts in
   `reasoning._pack`; a block whose placeholder has no usable fact is
   withheld with a trace line, never sent to drafting.
5. **Shared-wording coverage** — VAL-COVERAGE treated approved wording shared
   between sibling modules of one theory as "never argued" for all but one
   sibling; it now accepts a grounded sentence whose text appears in a
   sibling's approved wording.

Fixture-side closures: two-sided (and where the scenario demands it,
PoFA-compliant) notice fixtures under the notice-sides rule; prompt version
pins reviewed and re-pinned; `UNSUPPORTED_REVIEW` stop-code rename; the
choice-422 test rewritten around the B1 asked-facts contract; the OpenAI
pipeline stub now drafts via the reference drafter. Journey goldens
re-recorded for three documented benign effects (`cross_checked_fields`
fact, `pcn_conflict` as flag, `payment_made` asked under B2).

## Known quirk, deliberately not fixed

`payment_attempt_failed` narrative regex can false-positive across sentence
boundaries. Pre-existing, off the B1–B7 path; flagged for review.
