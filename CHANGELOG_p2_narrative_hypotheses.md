# P2: Narrative hypothesis and fact reconciliation layer

Branch `feature/p2-narrative-hypotheses`, built on P1 (`b57b9a4`). Not merged, not pushed, not deployed.

**Unchanged by design:**
- the Knowledge Base, including KB-ANPR-01 (`use_when: {is: multiple_visits}`);
- Claim Plan;
- drafting;
- validation;
- service routing;
- every prompt and the extraction engine.

The ground is gated exactly as before, by the existing reasoning gate (R-03) on the verified fact view. What changes is that free text can no longer put `multiple_visits` into that view.

## Fact flow

```
customer narrative / free-text answers
        │
        ▼
engines/narrative.py      clauses → event frames (visit, leave, return, explicit 2nd visit;
                          negation scope; on foot vs vehicle; site vs building; same day?)
        │
        ├──► ATOMIC FACTS ──► FactManager ──► Fact Graph
        │    visited_premises, purpose_of_visit, left_site, returned_same_day,
        │    possible_vehicle_departure, returned_to_vehicle
        │    (DERIVED, CUSTOMER_FREE_TEXT; provenance only: withheld from the drafter,
        │     never asked)
        │
        └──► HYPOTHESES ──► case.fact_hypotheses (NOT the Fact Graph)
             possible_multiple_visits = true, UNCONFIRMED
                    │  only if an in-force KB module gates on / requires the fact
                    ▼
             one question, asked once:
             "Did the vehicle leave the car park and return later that day?"
                    │
          YES ──────┼────── NO ────────────── no answer
           ▼        │        ▼                    ▼
  multiple_visits=true   multiple_visits=false   hypothesis stays UNCONFIRMED,
  (ANSWER, via           (ANSWER, via            never used
   FactManager)           FactManager)
  hypothesis CONFIRMED   hypothesis REJECTED
           ▼
  reasoning gate R-03 → KB-ANPR-01 eligible → claim reasoning → drafting
```

## Files changed

| File | Change |
|---|---|
| `pcn_appeal/hypotheses.py` (new) | <ul><li>The hypothesis model and `Hypotheses`, with these methods:<ul><li>`propose`: idempotent and keyed by fact and value;</li><li>`withdraw_unsupported`;</li><li>`settle`: called by FactManager;</li><li>`questions`;</li><li>`trace`.</li></ul></li><li>`KINDS` holds the question, reason, impact and proposition for each fact.</li></ul> |
| `pcn_appeal/engines/narrative.py` (new) | <ul><li>Clause and frame reading, plus `understand(case, texts)`.</li><li>Deterministic and generic.</li></ul> |
| `pcn_appeal/engines/account.py` | <ul><li>Removes the `multiple_visits` regex rule.</li><li>Calls `narrative.understand`.</li><li>Adds the drafting proposition back only once the hypothesis is CONFIRMED (`_confirmed_hypotheses`).</li></ul> |
| `pcn_appeal/fact_graph.py` | FactManager calls `Hypotheses.settle` after every applied write, every same-value write and every conflict resolution. |
| `pcn_appeal/models.py` | Adds `CaseFile.fact_hypotheses`. |
| `pcn_appeal/orchestrator.py` | `_reanalyse` adds hypothesis questions after the confirmation questions, using `_could_change_a_ground`, which checks the KG's `gating_facts` / `required_facts`. They replace any analysis question for the same fact. |
| `pcn_appeal/engines/reasoning.py`, `engines/analysis.py` | `NARRATIVE_FACTS` added to `withheld_from_drafter` and `INTERNAL_FACTS`. |
| `pcn_appeal/fact_ownership.py` | Narrative facts are CUSTOMER-owned. |
| `pcn_appeal/customer_safe.py` | `possible_impact`, `hypothesis_id`, `hypotheses`, `fact_hypotheses` and `hypothesis_trace` are added to the internal keys. |
| `pcn_appeal/api.py` | `/trace` and `GET /cases/{id}/facts` (both admin only) show the hypothesis trace. |
| `pcn_appeal/store/cases.py`, `tests/sqlite_store.py` | Save and load `fact_hypotheses`. |
| `infra/migrations/0003_fact_hypotheses.sql`, `infra/postgres_schema.sql` | The new table. |

## Hypothesis model

`fact_hypotheses` fields:

| Group | Fields |
|---|---|
| Spec fields | `hypothesis_id`, `case_id`, `fact_name`, `possible_value`, `source_text`, `confidence`, `required_confirmation_question`, `status` |
| Added | `signals` (why it was read that way), `rule`, `reason`, `possible_impact`, `asked_at`, `answer`, `resolved_fact_id`, `resolved_by`, `run_id`, `created_at`, `updated_at`, `resolved_at` |

Statuses:

| Status | Meaning |
|---|---|
| UNCONFIRMED | proposed from the account |
| CONFIRMED | the answer matched |
| REJECTED | the answer contradicted it |
| SUPERSEDED | a confirmed, document or evidence value already exists, so it is never asked |
| WITHDRAWN | the account no longer supports it |

**Priority:** a hypothesis is never written to the Fact Graph, so it is below every fact and cannot override one. Among facts, P1's ownership rules still apply unchanged, including NEEDS_CONFIRMATION for contradicted document-owned readings.

**Questions** carry `target_fact`, `reason` and `possible_impact` internally. The customer sees only `fact`, `text` and `type` (`customer_safe.customer_question`). None of the wording contains a KB id or module name, and a test checks this.

## Database

`0003_fact_hypotheses.sql`:
- Additive and idempotent. Apply it after `0002`.
- `UNIQUE(case_id, fact_name, possible_value)`, plus a partial index on UNCONFIRMED.
- Rollback: `DROP TABLE fact_hypotheses`.

## Tests

**`tests/test_p2_narrative_hypotheses.py`** has 25 tests:

| Spec test | Result |
|---|---|
| 1. "I left and came back." | `possible_multiple_visits=true` UNCONFIRMED, the question is generated, there is no `multiple_visits` fact, KB-ANPR-01 is not eligible and not in the pack. |
| 2. YES | `multiple_visits=true` (ANSWER), hypothesis CONFIRMED, KB-ANPR-01 eligible and in the pack. |
| 3. NO | `multiple_visits=false`, REJECTED, no KB-ANPR-01. No answer: it stays UNCONFIRMED and is unused. |
| 4. "I walked back to the car to get my purse." | No hypothesis. Three more pedestrian or building-only variants give the same result. |
| 5. Same narrative repeated | The account is re-read 3 times and the same text appears in two channels: one hypothesis, same id, same contents, same facts. |

The file also covers:
- the spec's atomic facts;
- generic names, with no retailer names in the code;
- negation: 5 denials, including "did not leave and come back" and "never left the car park";
- negation does not reach back into an earlier clause;
- a next-day return is not a second visit;
- a hypothesis is not in the fact view, the pack or the letter;
- it never overrides a confirmed fact, and a document value supersedes it;
- free text never settles a hypothesis;
- rewriting the account withdraws it;
- it is asked once, and only when a ground depends on it;
- the question's internal fields are stripped for customers;
- the admin trace shows the full chain and is admin only;
- hypotheses survive a reload.

**Updated tests:** `test_material_account.test_multiple_visits` and `test_customer_input_not_copy.test_multiple_visits` asserted the old behaviour (free text → `multiple_visits=true` plus the drafting proposition). They now assert a hypothesis, no fact, and the proposition only after a confirmed answer.

**Full suite:** 596 run (571 + 25), 23 failures and 6 errors. These are the same 29 tests as the pre-existing baseline, compared by test ID, and there are no new failures.

**Conflict probe:** unchanged from P1, with one intended postcode conflict.

**Before and after** (offline, same harness, P1 `b57b9a4` vs P2):

| Narrative | P1 | P2 |
|---|---|---|
| "I went shopping at the supermarket, forgot my purse, left and came back." | `multiple_visits=true`, KB-ANPR-01 selected, no question | hypothesis, question asked, no KB-ANPR-01 |
| "I went back again later to the car to get my purse." | `multiple_visits=true`, KB-ANPR-01 | nothing (on foot, back to the car) |
| "I left and came back the next day." | `multiple_visits=true`, KB-ANPR-01 | nothing (another day) |
| "I walked back to the car to get my purse." | nothing | nothing |
| "I did not leave and come back." | nothing* | nothing |

\* On P1 this sentence did not fire only because the old regex matched "left and came back" and not "leave and come back". The rule had no negation check: "We never left and came back" would have fired.

**Live** (OpenAI, the shopping narrative, the runner's neutral policy answering "no" to bool questions):
- Both cases created the hypothesis, asked the question and recorded REJECTED, giving `multiple_visits=false`.
- No KB-ANPR-01, no multiple-visit wording in either letter, 0 conflicts, 0 leaks.
- Bay was RELEASED (BAY + POFA, as before).
- Overstay was held for the unread site postcode. This is the known extraction variance from P1, not P2.

## Remaining risks

1. **Fewer automatic ANPR letters.** A case that used to get a multiple-visit argument from the narrative alone now needs a "yes". Skipping drops that ground. This is intended, but it is a product change.
2. **The negation and mode reading is rule-based, at clause and frame level, with no model call.** This was chosen for determinism (spec test 5) and offline testing. It covers coordinated verbs, earlier-clause scope, on-foot movement, return-to-vehicle, building-only departures and other-day returns. Unusual phrasing ("I nipped off in the motor for a bit") may be missed. A miss produces no hypothesis, which is the safe direction: no ground. A model-based reader could sit behind the same `read()` interface later.
3. **Only `multiple_visits` uses the hypothesis path.** Other free-text rules (children present, payment made, breakdown and so on) still write ANSWERED facts directly. The same ambiguity could exist there. Moving them is a KINDS entry plus a rule change each, and needs a product decision.
4. **Asked once.** If the customer skips the question, it is not asked again, so the ground stays closed for that case.
5. **Atomic facts are provenance only.** Nothing in the KB uses them yet, by design. They exist for the trace and for later work.
6. **Migration order:** 0001, then 0002, then 0003, before deploying.

## Deployment (when approved)

1. Merge P0, then P1, then P2. Not done; this needs approval.
2. On staging, apply `0003` and check the `fact_hypotheses` table.
3. Deploy, then run a case with "left and came back" and check:
   - the question appears;
   - "yes" gives KB-ANPR-01 in `/trace`;
   - "no" or skip gives none;
   - `/trace` shows the hypothesis chain.
4. **Rollback:** redeploy P1. The table can stay, or be dropped.
