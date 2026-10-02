# Response to the P0–P6.1 architecture review — point by point

Prepared against HEAD `55de019` (P6.2). Every claim below was verified in code this session;
citations are file:line. One note up front: the review covers P0–P6.1, and **P6.2 is already
committed** — it closed the cumulative-grounds defect (a supported ground can no longer vanish
with the model's selection) and the particularised-timing rule (VAL-PARTICULARS / VAL-COVERAGE),
so part of what the review asks under "verified findings" and "coverage" is now in place.

Format per point: **(1) implemented, (2) partial, (3) needs changing, (4) proposal,
(5) blocking before final regression?**

---

## 1. Legal-critical extraction hardening

**(1) Implemented.**
- Confidence gate: below 0.85 a field becomes UNCERTAIN and unusable (`engines/extraction.py:28,380`); `case.get`/`fact_view` serve usable facts only (`models.py:91-92,397-399`).
- Unparseable dates/times force confidence 0 → UNCERTAIN (`extraction.py:184-194,352-355,263-267`).
- One chronology check: `notice_issue_date < parking_event_date` → UNCERTAIN + flag (`extraction.py:447-451`).
- PCN number is genuinely cross-checked (labelled regex across documents + the classifier's independent per-document reading; mismatch → UNCERTAIN + conflict question + **hard hold**, `extraction.py:399-438`, `orchestrator.py:463-482`).
- Customer corrections to confident document facts are refused pending a closed confirmation question, and the case holds in MANUAL_REVIEW while open (`fact_graph.py:272-274`, `orchestrator.py:436-457`).
- The PoFA calculator refuses to conclude on missing dates and on the ±1-day presumed-delivery boundary (UNRESOLVED, `legal/pofa.py:81-113`).
- One-click auto-appeal promotes only EXTRACTED facts; UNCERTAIN never auto-confirms (verified by running the test: the uncertain-date assertion passes today).

**(2) Partial / (3) needs changing — the review's concern is confirmed. The specific holes:**
1. **The step-by-step confirm screen promotes UNCERTAIN → CONFIRMED** (`extraction.py:565-567`) and the UI sends every non-empty displayed field as confirmed (`frontend/app/page.tsx:166`) — a chronology-flagged issue date left unedited becomes CONFIRMED and usable, and a pending PCN conflict is cleared without the customer choosing (`extraction.py:569-571`).
2. **One LLM pass, self-reported confidence, no second reading.** Deterministic labelled-regex readers exist for the key fields but run only as *gap-filling recovery*, never as a cross-check against the LLM's value (`recovery.py:209-211`); two distinct regex candidates are noted in the trace only; a recovered value (DERIVED, fixed 0.9) can replace a chronology-flagged date without re-checking chronology, after the confirm step has already passed (`recovery.py:284-292`, `orchestrator.py:276`).
3. **No plausibility suite**: no future-date check, no NTD ≤ event ≤ issue cross-chronology, no received ≥ issue, no exit-before-entry flag (duration silently dropped, `extraction.py:276,454-457`). Corrected dates are not validated (unparseable → silently None) and chronology is not re-run after correction.
4. **Notice route** is derived from the LLM's `doc_types` labels only (`extraction.py:542-547`): a windscreen notice mislabelled "PCN" becomes POSTAL; route, entry/exit times, allegation and notice stage are **not on the confirmation screen** (`api.py:800-813`); the classifier's independent `document_date` is stored but never compared with `notice_issue_date`.
5. **An UNCERTAIN legal-critical date generates no question and no review**: Question Authority rejects questions about document-owned fields ("the documents answer it", `question_authority.py:310-311`), so the case proceeds with the ground silently lost — conservative, but exactly the "silently continue" the review refuses.
6. **The answers endpoint accepts facts that were never asked** (`engines/questioning.py:99-105` falls back to the question bank, then free text) — unsolicited facts can enter the Fact Graph as ANSWERED.
7. A finding can be VERIFIED from EXTRACTED (unconfirmed) dates (`legal/findings.py` does not require confirmation of supporting facts).
8. The chronology check has no test.

**(4) Proposal — P7 "Legal-Critical Input Gate" (generic, no operator rules):**
- a) A `LEGAL_CRITICAL` field registry: event date, issue date, ntd_date, received date, entry/exit times, notice route, notice stage, allegation, operator.
- b) **Deterministic second reading as a cross-check, not a fallback**: run the labelled-regex readers always; LLM value vs regex value disagree → UNCERTAIN + a closed confirmation question (the P1 conflict machinery already exists for exactly this).
- c) Chronology/plausibility suite: future dates, NTD ≤ event ≤ issue ≤ received, entry < exit or flagged, corrected values re-parsed and chronology re-run.
- d) Confirm step: an UNCERTAIN legal-critical field requires an explicit customer entry (never default-confirmed); `pcn_conflict` cleared only by an explicit choice.
- e) Route shown on (or confirmed through) the journey when derived route is UNKNOWN or conflicts with document keywords.
- f) The answers endpoint refuses facts not asked.
- g) `findings.evaluate` requires its supporting legal-critical facts to be document-sourced and customer-confirmed (or deterministically cross-checked) before VERIFIED.

**(5) Blocking: YES** — agreed this is the top priority; every downstream safeguard reasons faithfully from whatever enters here.

---

## 2. Narrative hypotheses beyond multiple visits

**(1) Implemented.** The full loop exists for `multiple_visits` only (`hypotheses.py:56-65`; proposal `narrative.py:242-247`; question precedence `question_authority.py:292-293`; drafting proposition only when CONFIRMED `account.py:430-445`). Six scene-setting facts (`visited_premises`, `left_site`, …) are DERIVED, withheld from the drafter and never asked — context only.

**(2) Partial.** Fourteen account-rule facts are written **directly as ANSWERED** from free text (`account.py:52-242,324-332`), six with negation guards. `account_contradicts_allegation` / the material propositions are then computed from those unconfirmed extractions (`account.py:343-378`) — a regex match alone can set a gate-satisfying fact.

**(4) Proposal — a classification rule, then apply it:** a narrative fact may be written directly only when it is the customer's first-person report of an observable circumstance *and* its truth asserts nothing stronger than what the customer said. Anything asserting a **completed legal act or a vehicle/legal state** gets a hypothesis + one precise question:

| Hypothesis-gate (ambiguous, legally material) | Direct write stays (self-evident report) |
|---|---|
| `payment_made` ("I paid" vs "the machine failed") | `payment_attempt_failed` (an attempt is only an attempt) |
| `vehicle_immobilised`, `immobilisation_prevented_departure` ("strange noise" ≠ "could not be moved") | `child_occupant_present`, `blue_badge_displayed` |
| `loading_activity`, `ev_charging_session`, `disability_extra_time` | `seeking_parking_space`, the six scene facts |
| `permit_held`, `resident_connection_stated`, `bay_conditions_met_accounted` | `customer_described_event` |

**(5) Blocking: partially.** `payment_made` and the two breakdown facts gate real grounds — those move to the hypothesis path **before** regression; the rest follow in the same pattern without blocking.

---

## 3. The three-question limit

**(1) Implemented.** Per-question materiality (R1/R5, `question_authority.py:344-438`), one question at a time, each round re-decided from scratch; conflict/confirmation/hypothesis questions are **not** capped.

**(2)/(3) Confirmed problems.**
- `max_question_rounds: 3` (`data/questions.yaml:116`) counts persisted `analysis_round` audit events — **including the internal ground-recovery rounds inside generate()** — so the customer's budget is consumed by rounds they never saw (`analysis.py:219-228`, `orchestrator.py:679-701`).
- Beyond the cap, material questions are **silently dropped**: an in-memory trace line only, no audit event (`analysis.py:221-222`).
- Worse, a question "asked" during an internal recovery round is appended to `asked_questions` although the customer never saw it (`orchestrator.py:208-213,692-698`).

**(4) Proposal.** Adopt the review's stopping condition as the rule: **stop when no approved candidate remains that is material to an available ground** — which is what Question Authority already computes; the arbitrary round cap becomes a high circuit-breaker only (e.g. 10), audit-logged when hit. Internal recovery rounds stop consuming the budget and stop marking questions as asked. Every dropped question becomes a durable audit event.

**(5) Blocking: YES** — cheap, and it changes which cases the regression pack exercises.

---

## 4. One authoritative KB publication path

**Direct answers to the three questions:**
- *Single authoritative source for a live case*: with `DATABASE_URL` set, the latest published Postgres `kb_releases` row (`api.py:204-250`, `store/kb_source.py:63-139`), with a **drift refusal** against the YAML unless `ALLOW_KB_DRIFT=1`. Without a database or published release, the YAML files. **Except**: relations always come from `kb_relations.yaml` even under a DB release, and block text is pinned "1.0" so block drift is invisible to the check.
- *How a release is approved*: today only the CLI `python -m pcn_appeal.store sync --publish`. `POST /admin/kb/releases` is a deliberate 501 ("must run the scenario suite and publish only if green", `api.py:1400-1411`).
- *Which release a given appeal used*: `cases.kb_release_id` (set at creation), the locked plan's `trust.kb_version` `{release_id, digest, relations_version}` (`claim_plan_authority.py:647-651`), and the per-run execution manifest with per-module versions — this part is already deterministic and auditable.

**(3) Needs changing.**
- Relations and block texts are outside the release payload and digest.
- `kb_releases` is upserted although documented immutable (`kb_sync.py:142-144`).
- **KB-BAY-01, KB-BAY-02, KB-POFA-06, KB-REC-01 are live in YAML but absent from the controlled document** — all `legal_basis_origin: NOT_IN_SOURCE`, BAY-01 marked "PENDING CLIENT APPROVAL". The ingestion store built from the docx is explicitly not live.
- YAML fallback is permitted even in production when no release is published.

**(4) Proposal.** (a) Fold relations + block texts into the release payload and digest; (b) make `kb_releases` insert-only; (c) implement the publish endpoint gated on the journey/golden suite being green; (d) production refuses the YAML fallback — a published release is mandatory; (e) **the four NOT_IN_SOURCE modules need your legal decision**: add them to the controlled document or retire them. That decision is yours, not code.

**(5) Blocking:** (d) and the four-module decision, yes. (a)–(c) should land in the same phase; they are small.

---

## 5. Critical integrity failures must block release

**(1) Implemented — more than the changelog wording suggests.** Five of the six classes you list are already **hard-gated at their source**, before or at draft time (any BLOCK → bounded retry → MANUAL_REVIEW, never released):

| Failure class | Hard gate today |
|---|---|
| Claim outside locked plan | VAL-PLAN (incl. "plan not LOCKED", "pack ≠ plan") + DV-CLAIM/DV-GROUND |
| Internal reasoning in the letter | VAL-LEAK + DV-LEAK, plus the `customer_safe` scrub middleware on every customer response |
| Driver improperly identified | VAL-DRIVER + DV-DRIVER |
| Authorised claim without verified finding | Claim-plan NO_VERIFIED_FINDING + VAL-LEGAL-FINDING |
| Specific state inconsistencies | needs-confirmation hold, PCN-conflict hold (MANUAL_REVIEW) |

**(2)/(3) The gap:** the `check_case` audit (11 checks) runs **after** the final state is set and "never fails the run" (`integrity/__init__.py:22-35`, `orchestrator.py:409-415`). Case-level FACTS_HAVE_SOURCES, STATE_MACHINE_CONSISTENT, NO_CUSTOMER_LEAKAGE beyond the letter, SUPPORTED_ITEMS_HAVE_SUPPORT and ONE_LOCKED_PLAN_PER_CASE are observe-only (the last two CLI-only).

**(4) Proposal.** Split checks into CRITICAL and WARN. CRITICAL (NO_CLAIM_OUTSIDE_PLAN, FACTS_HAVE_SOURCES, NO_CUSTOMER_LEAKAGE, DRIVER_NOT_IDENTIFIED, LEGAL_DEFECTS_VERIFIED, STATE_MACHINE_CONSISTENT, SUPPORTED_ITEMS_HAVE_SUPPORT) run **inside** `generate()` before the state is finalised: any FAIL → MANUAL_REVIEW with the failing check in the audit. Mostly redundant with the inline gates — which is exactly what a belt-and-braces hard gate should be. WARN stays observational.

**(5) Blocking: YES** — small change, high assurance value.

---

## 6. The 29 baseline failures — full disposition

Every failure was re-run or read from the full-suite log this session. **None can release a wrong
letter to a customer**: 25 are stale tests of pre-rebuild contracts, 4 are fail-closed
(the pipeline correctly refuses to release and routes to MANUAL_REVIEW).

| # | Test(s) | Why it fails | Still relevant? | Disposition | Live-case risk |
|---|---|---|---|---|---|
| 1–5 | `test_blob_uploads` ×2, `test_evidence_storage` ×1, `test_uploads.one_bad_file`, `test_uploads.pdf_upload_runs_the_whole_journey` | Fixtures upload a single notice side; the deliberate NOTICE_SIDES_REQUIRED rule (both sides mandatory) now returns 422 | The *rule* is intended (verified on the live app) | Update fixtures to front+back; keep the rule | None — behaviour is the intended one |
| 6–16 | `test_uploads` ×5 (`evidence_ids` KeyError, `uploaded_case_can_be_continued` KeyError, `pdf_is_offered` KeyError, `pdf_404s` EXTRACTED≠CONFIRMED, `a_photo_alone`, `an_unreadable_photo`), `test_web_flow` ×4, `test_store.answering_the_paused_questions`, `test_store.finishes_in_one_call` / `pauses_only` (notice_reverse_pages question) | All encode the **pre-rebuild journey contract** (auto-confirm, no sides rule, old response shapes) | The behaviours they protected (no internal ids, plain-English grounds) now live in `customer_safe` tests + DV-LEAK | Rewrite against the V2 contract, porting the protective assertions | None |
| 17–19 | `test_recovery_ladder` ×3 (signage gating questions not asked) | Question Authority (P3) no longer asks gate facts of suppressed grounds the model did not select | **Open product question** — this is your point 3: under the materiality stopping rule these questions may become askable again | Decide with point 3; until then they document the gap, keep them red or skip-with-reason | A legitimate signage ground can go unasked silently — covered by the point-3 fix |
| 20–22 | `test_scenarios.ScenarioB_Resident` ×2 (lease) | KB block wording contains literal `{{bay_reference}}` / `{{lease_or_tenancy}}` placeholders the demo drafter copies; VAL-LEAK correctly **blocks** → MANUAL_REVIEW (second test then trips a test-bug TypeError on letter=None) | Yes — lease letters can't release on the demo path | Fill placeholders from facts at block-render time; fix the test's None handling | Fail-closed: MANUAL_REVIEW, never a leaked placeholder |
| 23–24 | `test_scenarios.ScenarioC_PaymentKeying` ×2 | Demo drafter emits the payment point 3× across blocks; VAL-REPEAT-POINT **blocks** → MANUAL_REVIEW | Yes | De-duplicate payment propositions across module blocks | Fail-closed |
| 25 | `test_llm_clients.FullPipelineOverOpenAI` | Integration test needs the real provider; demo env → VAL-DRAFT block | Yes — belongs to the staging run (point 7) | Mark skip-unless-key locally; run in staging | n/a locally |
| 26 | `test_prompts.EXPECTED_VERSIONS` | Prompts were bumped (extraction→10; classification, page_references added) without updating the pin — **unrecorded prompt drift**, itself a finding | Yes | Review those prompt diffs, then update the pin; keep the pin test | Prompt changes went unreviewed — process gap, not a letter defect |
| 27 | `test_uploads.a_choice_answer_outside_its_options` | The guard exists (`questioning.py:125-128` raises on out-of-options) but the fixture's question is no longer asked, so the stray answer is ignored with 200 | Yes | Update fixture; and close the related real gap — unsolicited answers for *unasked* facts are accepted (point 1f) | The 1f gap is real; the option guard itself works |
| 28 | `test_store.uncertain_facts_are_never_auto_confirmed` | **The safeguard passes** (uncertain date withheld); only the stale expectation that confident facts auto-promote fails — auto-promotion was deliberately removed | The protective half, yes | Split the test: keep the uncertain-half, drop the promotion-half | None — stricter than the test expected |
| 29 | `test_recovery_ladder` (third of the trio) | counted in 17–19 | | | |

Proposed rule going forward: **zero unexplained red**. Each test is fixed, rewritten, or retired with a recorded reason — done as part of P7 so the regression cycle starts from green.

---

## 7. Production-like end-to-end staging run

**(1) Nothing of this has run yet** — and it cannot run from this environment. It needs from you: staging PostgreSQL access (migrations 0006–0009 are written but untested on Postgres), the production OpenAI configuration, and a deploy. **(4) Proposed run order:** apply migrations → publish a KB release (so `kb_release_id` is real, not YAML-fallback) → run the journey suite and the point-9 regression pack against the real model → measure drafting retry rate and MANUAL_REVIEW volume (v13 + VAL-PARTICULARS/VAL-COVERAGE/VAL-LEGAL-FINDING may raise both). **(5)** This *is* the final gate; it runs after the blocking items close.

---

## 8. Verified findings beyond PoFA

**(1) Implemented:** the architecture is already the extension point you describe — `FindingSpec` registry (assertion pattern + facts + calculator), modules reference findings through their own gates, `timed` marks date-proved families; nothing is PoFA-hard-coded.

**(4) Proposal in two steps:**
- **Cheap and immediate (recommend blocking):** register *assertion-only* specs for signage, payment and authority — a pattern with **no calculator** can never be VERIFIED, so VAL-LEGAL-FINDING immediately refuses "the signage was inadequate" / "payment was made and the record proves it" / "the operator has no authority" as naked assertions, leaving put-to-proof as the only route. Today those sentences are constrained only by block wording.
- **Roadmap (non-blocking):** real calculators per family as evidence allows — e.g. SIGNAGE from uploaded photo facts, PAYMENT_RECORD from a bank/app record document class, ANPR_SEQUENCE_INCOMPLETE from capture-set facts. Each is a new FindingSpec + deterministic calculator; no prompt or operator rule.

---

## 9. The real-scenario regression pack

**(1) Implemented:** the harness exists (`python -m pcn_appeal.integrity journeys`) and its per-case report already shows the exact chain you list — extracted facts + provenance, hypotheses, questions generated/suppressed with reasons, legal findings with calculations, every claim-plan item with its decision and reason, the draft with per-sentence grounding, validation and the integrity table. Five journeys with goldens exist.

**(4) Proposal:** build your ~19 scenarios as journeys with goldens (late postal PoFA; weekend/bank-holiday service — the calculator already carries E&W holidays 2025–27; missing invitation wording; NTD→NTK timing; ANPR overstay; genuine double-dip; leaving-and-returning narrative; payment; permit; parent-and-child; loading; breakdown; residential/primacy; hire/lease missing statutory documents; debt-recovery document; council PCN; front-only; duplicate front as both sides; no-ground). Run deterministically on the demo path for regression, and once on staging with the real model (point 7). Deliverable per case: the CASE_REPORT chain, not just the letter.

**(5)** This is the final cycle itself — built now, run after the blockers close.

---

## Blocking list (my recommendation before the final regression cycle)

| # | Item | Point | Size |
|---|---|---|---|
| B1 | Legal-Critical Input Gate (cross-check second reading, chronology suite, confirm-step fix, unasked-answer refusal, confirmed-facts-for-findings) | 1 | the main phase |
| B2 | Hypothesis-gate `payment_made` + breakdown facts | 2 | small |
| B3 | Materiality stopping rule; internal rounds stop consuming the budget; dropped questions audited | 3 | small |
| B4 | Critical integrity checks gate release | 5 | small |
| B5 | Production requires a published KB release; relations+blocks in the digest; **your legal decision on BAY-01/BAY-02/POFA-06/REC-01** | 4 | small + your decision |
| B6 | Assertion-only finding specs for signage/payment/authority | 8 | small |
| B7 | Baseline tests to zero-red with recorded dispositions | 6 | medium |
| Then | Staging E2E (your infra) + the 19-scenario regression pack | 7, 9 | the cycle |

Not blocking: the remaining hypothesis migrations (point 2 tail), finding calculators for new families (point 8 tail), KB publish-endpoint polish beyond the gate.
