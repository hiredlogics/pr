# P3: Question Authority and materiality layer

Branch `feature/p3-question-authority`, built on P2 (`a28a40a`). Not merged, not pushed, not deployed.

**Unchanged by design:**
- the knowledge modules (`kb_modules`, gates, strengths);
- the drafting engine;
- Claim Plan finalisation;
- legal rules (PoFA calculator, Code resolver, validators);
- service routing (`routes.yaml`);
- the fact model (P1) and the hypothesis model (P2).

## Approval flow

```
Fact Graph + hypotheses
        │
        ▼
case analysis (model)  ──► grounds ──► Claim Plan (unchanged)
        │
        ├── model questions ─────────┐
        ├── KB-gate questions ───────┤   pre-checks (existing, in analysis.py):
        │   (ground suppressed at    │   topic clusters, store-contact, ANPR shape,
        │    use_when)               │   choice options. Each drop is now a trace row.
        ▼                            ▼
P1 conflicts / confirmations ──► CANDIDATES ◄── P2 hypotheses, postcode / trade body
                                     │
                                     ▼
                   engines/question_authority.py  (every candidate)
                     dedupe → R4 → R3 → R1 → R5
                                     │
               APPROVED (ranked) ────┴──── REJECTED (reason)
                     │                          │
       ONE shown; the rest wait for the         │
       next round, which re-decides             │
                     │                          │
                     ▼                          ▼
   customer: fact / text / type / options    admin trace: audit `question_review`,
   (customer_safe.customer_question)         /cases/{id}/trace, /cases/{id}/facts
```

Zero questions is a valid outcome: when nothing is approved, nothing is shown, and the case goes on to drafting or its hold with what is known.

## Question Authority design (`pcn_appeal/engines/question_authority.py`)

A candidate is APPROVED only if every check passes. They are applied in this order, and the first failure is the recorded reason.

| Check | Rejects when |
|---|---|
| **Dedupe** | <ul><li>the fact is already held (under any alias, e.g. `children_present` → `child_occupant_present`);</li><li>the customer already answered it;</li><li>it was already asked;</li><li>the same wording was shown before under another name;</li><li>another question for the fact was approved this round;</li><li>a narrative fact already says it (`COVERED_BY`: `visited_premises=true` covers `genuine_customer`);</li><li>a hypothesis owns the fact (its wording wins).</li></ul> |
| **R4 answerable** | <ul><li>it asks for a legal interpretation (PoFA, relevant land, lawful, enforceable, Code of Practice and so on);</li><li>it asks for something only the operator holds;</li><li>it asks about driver identity.</li></ul> |
| **R3 documents** | <ul><li>the fact is a notice field (except the site postcode and trade body, which a customer can read off the notice);</li><li>a supporting document shows it;</li><li>the engines derive it;</li><li>recovery already recovered it;</li><li>it is operator-requestable.</li></ul> |
| **R1 supported issue** | <ul><li>no in-force KB module gates on or requires the fact;</li><li>the module the candidate names does not depend on it;</li><li>every module that does is on a route the allegation rules out (see below).</li></ul>Case-integrity sources (document conflict, P1 confirmation) are supported issues in their own right. The postcode and trade-body sources must name in-force modules their deterministic check found they would unlock. |
| **R5 material** | No possible answer changes the related module's result. |

**R5 in detail.** The gate is evaluated three-valued (`tri()`):
- an unknown fact leaves a conjunct open;
- `do_not_use_when` is evaluated as the reasoning gate R-03 does, on what is known.

An answer is material only if one of these holds:
- it could **open** the module, moving it from ruled out to possible or from possible to applies;
- for a module already selected, it could **rule that module out**.

A fact that a module only uses to exclude itself (for example `not payment_made` on the consideration-period grounds) is never asked on that module's account. A fact the module requires but does not gate on is material only while the module can still apply.

**Allegation fit (R1).** `ALLEGATION_CLASSES` and `ROUTES_IRRELEVANT_TO` live in the authority layer. They are not in the KB or routing, and they hold generic allegation shapes only. One class is defined:
- PROHIBITION (no parking, no stopping, prohibited, yellow or hatched lines, keep clear and similar);
- it rules out the PAYMENT, KEYING and GRACE routes, because no payment, tariff keying or end-of-period grace can permit parking where it is prohibited;
- an unclassified allegation rules out nothing.

**Ranking.** One question is shown at a time. Approved questions are ordered by:
1. case integrity first;
2. then impact: a decisive answer (one answer applies the module and another rules it out) ahead of a possible one;
3. then the strongest related module;
4. then least effort (bool, then choice, then number, then text).

**Question object** (internal): `question_id` (stable per case, fact and wording), `text`, `target_fact`, `related_module`, `material_reason`, `impact_if_yes`, `impact_if_no`, plus `issue`, `source` and `priority`.

What is pending and returned is `{fact, text, type, options}` only. The object lives in the `question_review` audit rows.

**Admin trace.** There is one row per candidate, per round, approved or rejected. The analysis pre-check drops are included.

Fields: `round`, `stage`, `question_id`, `candidate_question`, `target_fact`, `related_module`, `source`, `decision`, `reason`, `material_reason`, `impact_if_yes`, `impact_if_no`, `shown`, `priority`.

Example:
- candidate "Was the visit connected with genuine use of the premises at this location?";
- target `genuine_customer`;
- REJECTED;
- reason "already established by what the customer said (visited_premises=true)".

## Files changed

| File | Change |
|---|---|
| `pcn_appeal/engines/question_authority.py` (new) | The authority, `tri()`, `trace()`. |
| `pcn_appeal/orchestrator.py` | <ul><li>`_reanalyse` passes every source to the authority as candidates and shows one question.</li><li>Only the shown question is marked asked.</li><li>The P1 confirmation hold also goes through the authority.</li><li>Pending questions are customer-shaped.</li></ul> |
| `pcn_appeal/engines/analysis.py` | <ul><li>Removes `SITUATION_FALLBACK` and `_ensure_situation_questions`.</li><li>Pre-check drops are recorded (`question_rejections`).</li><li>Candidates are tagged with `source`, `related_module` (KB gates), `unlocks` (postcode and trade body) and `material_reason`.</li><li>Adds `_ata_unlocks`.</li></ul> |
| `pcn_appeal/hypotheses.py` | `questions()` only proposes; the new `mark_asked()` records `asked_at` when the question is actually shown. |
| `pcn_appeal/data/prompts.yaml` | `case_analysis` v6 → v7 (below). |
| `pcn_appeal/customer_safe.py` | Adds `question_id`, `related_module`, `impact_if_yes`, `impact_if_no`, `question_trace`, `priority` and `kb_gated` to the internal keys. |
| `pcn_appeal/api.py` | <ul><li>`/cases/{id}/trace` and `/cases/{id}/facts` (both admin) gain `question_trace`.</li><li>`POST /cases/{id}/answers` returns customer-shaped questions explicitly.</li></ul> |
| `tests/test_question_authority.py` | 33 new tests, appended. The existing harness and its 13 tests are unchanged. |
| `tests/test_p2_narrative_hypotheses.py` | One test now reads the internal fields from the candidate, not the returned question. |
| `tests/test_prompts.py` | `EXPECTED_VERSIONS` case_analysis set to 7. That test was already a baseline failure, for the extraction version. |

No database migration. The trace uses the existing `audit_log`.

## Removed prompt rules (`case_analysis` v7)

**Removed:**
> EXCEPTION — thin packs: if the only supportable candidates are weak / support-only … and no strength≥50 ground is available yet, you MUST ask 1–3 plain situation questions … Do not end the case with zero questions and only landowner authority.

The cross-reference "— or when the thin-pack exception above applies" is also removed.

**Added:**
- "Zero questions is a valid outcome, including when the only supportable ground is weak: never ask because a pack is thin, a field is empty or a candidate exists. A question whose yes and no would lead to the same result must not be asked."
- Each question names `related_module` (internal).

**Code:** the unused `SITUATION_FALLBACK` bank (payment, genuine customer, permit, signage, short presence, breakdown) and its hook are deleted.

## Tests

`tests/test_question_authority.py` has 33 new tests across these classes:

| Class | Covers |
|---|---|
| `P3SpecTests` | Spec tests 1–5, plus an overstay control for test 3 and three prohibition wordings. |
| `DuplicatePrevention` | <ul><li>previous question;</li><li>previous answer;</li><li>same wording under two names;</li><li>repeated fact within a round;</li><li>wording shown in an earlier round;</li><li>a hypothesis owns its fact.</li></ul> |
| `ModuleRequirement` | <ul><li>a named module must depend on the fact;</li><li>the related module is recorded.</li></ul> |
| `Materiality` | <ul><li>yes and no give the same result (genuine customer without a receipt);</li><li>the same question is material once the receipt exists;</li><li>a settled fact closes the gate (keying error after "no payment");</li><li>impacts are stated.</li></ul> |
| `CustomerCanAnswer` | <ul><li>legal interpretation;</li><li>operator-held information;</li><li>driver identity;</li><li>notice field;</li><li>engine-derived fact.</li></ul> |
| `OneQuestionAtATime` | <ul><li>one is shown and the rest wait, then the next is shown after the answer;</li><li>least-effort tie-break;</li><li>integrity first.</li></ul> |
| `ZeroQuestions` | <ul><li>a thin pack asks nothing;</li><li>the prompt no longer carries the rule;</li><li>no situation bank remains in the code.</li></ul> |
| `CustomerSafeOutput` | <ul><li>the full internal object;</li><li>the returned and pending questions are customer-shaped with no leaks;</li><li>scrub as a backstop.</li></ul> |
| `AdminTrace` | <ul><li>every generated question has a row, including pre-check drops;</li><li>the trace is admin-only over the API.</li></ul> |

**Full suite:** 629 run (596 + 33), 23 failures and 6 errors. These are the same 29 tests as the baseline, compared by test ID, and there are no new failures.

**Conflict probe:** unchanged, with the one intended postcode conflict.

## Before and after

Offline: the same harness and the same stand-in model questions, on P2 `a28a40a` vs P3. "Shown" is what the customer is asked.

| # | Scenario | P2 shown | P3 shown | P3 reason (trace) |
|---|---|---|---|---|
| 1 | "I went shopping, forgot my purse, left and came back." + model asks genuine visit | multiple_visits, genuine_customer | multiple_visits | genuine visit rejected: already established (visited_premises=true) |
| 2 | "I went to the supermarket to shop." + receipt + model asks genuine visit | genuine_customer | none | same |
| 3 | "Parked in a No Parking Area" + model asks payment | payment_made | none | R1: prohibition cannot be answered by PAYMENT/KEYING; R5: no other module's result changes |
| 4 | children_present=true confirmed + model asks children | none | none | fact already confirmed |
| 5 | model asks "Did you see the signs?" (no module) | saw_the_signs | none | R1: no in-force module depends on this fact |
| 6 | overstay + model asks permit and payment | permit_held, payment_made | payment_made (permit waits) | ranked: payment 85 > permit 70 |
| 7 | model asks genuine visit, no receipt | genuine_customer | none | R5: every possible answer leads to the same result |

**Live** (OpenAI, the shopping narrative, the runner answering "no" to bool questions; store = SQLite shim):

| Case | Asked | Outcome |
|---|---|---|
| Bay | Only the leave-and-return question, approved via KB-ANPR-01 and answered "no" (REJECTED); 0 leaks. | RELEASED (BAY + POFA), as in P2. |
| Overstay | Only that question; the model proposed no other question. | NO_SUPPORTED_GROUNDS: only KB-POFA-01 survived, `pofa_findings=[]`. |

**Overstay paired run.** The same images on P2 released with POFA-02 and POFA-05.

The difference is extraction, not questioning:
- the P3 run read `notice_issue_date` as the event date;
- the P2 run read it as 14 days later, which gives POFA_POSTAL_LATE.

The P2 run also asked only the hypothesis question. This is the same date-extraction variance seen in P1 and P2, and it is outside P3.

## Remaining risks

1. **Fewer questions overall.** Thin packs now end without situation questions. A case that needed a "did you pay?" prompt to find its only strong ground now relies on the model judging it material, and R5 then confirming it. This is the spec's intent, but it is a product change. Watch the NO_SUPPORTED_GROUNDS rate after deploy.
2. **One question at a time × `max_question_rounds: 3`.** Analysis questions now arrive one per round, so a case needing four separate facts gets three. Integrity and hypothesis questions are not capped by the round limit. Raising the limit is config (`questions.yaml`), not code.
3. **Allegation fit is a small regex table** in the authority, not KB metadata. Only PROHIBITION is defined. An oddly worded prohibition is not classified and excludes nothing, which is the safe direction. Moving allegation applicability into KB metadata would be cleaner, but is a KB change and needs legal review.
4. **`COVERED_BY` has one entry** (`visited_premises` → `genuine_customer`). It suppresses the question; it does not set `genuine_customer`. So KB-CUST-01 cannot open from the narrative alone; it still needs the answer from elsewhere plus a receipt and a customer-only site. Promoting narrative facts to KB facts is a product and legal decision (P2 deliberately kept them provenance-only).
5. **R5 is only as good as the gates.** A module whose `use_when` under-specifies its own conditions makes a question look more material than it is. Unknown facts make R5 lenient ("may apply"); it rejects only when the outcome is already decided.
6. **Overlapping pre-checks.** The older drops in analysis (topic clusters, store-contact, ANPR shape and so on) still run before the authority. Each one is now a trace row, but the reasons are worded in two styles. They could later be folded into the authority.
7. **Two holds not changed:** the postcode hold in `_hold_without_a_leading_ground` still re-presents the postcode question directly. It is a re-display of an already-material question, not a new ask.

## Deployment (when approved)

1. Merge P0, then P1, then P2, then P3. Not done; this needs approval. There is no migration for P3.
2. On staging:
   - run the shopping narrative and check one question and its `question_trace` in `/trace`;
   - run a No Parking Area notice and check that no payment question appears;
   - check that `prompt_versions.case_analysis` is 7 in the manifest.
3. **Rollback:** redeploy P2. There is no data to undo; the trace rows are audit entries.
