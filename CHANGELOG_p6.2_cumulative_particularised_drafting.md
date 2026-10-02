# P6.2 — Cumulative grounds and particularised drafting

Branch `feature/p6-drafting-validation-intelligence`, built on P6.1 (`6686c8d`). Not pushed.
Driven by Alpha's 2026-10-02 live test (overstay NTK, event 17/07/2026, issued 30/07/2026,
forgotten-purse / left-and-returned account): the letter argued multiple visits and generic
keeper liability but the calculated PoFA timing defect had vanished, and nothing anywhere
required a timing defect to be argued on its dates.

## WHAT

**1. Cumulative grounds (claim plan).** `ClaimPlanBuilder.build` no longer treats Case
Intelligence's selection as the only route to SUPPORTED. Two new licensed routes, each with
its own decision code and full re-checks (gate, relations, supporting facts, evidence):

| Decision | Meaning |
|---|---|
| `VERIFIED_FINDING` | the module's gate holds and a VERIFIED legal finding its gate references licenses it — a calculated statutory defect is argued on the calculation, independently of model selection |
| `CARRIED_FORWARD` | the module was SUPPORTED in the previous locked plan and its gate and supporting facts still hold; the reason names the previous plan version |

A carried ground that lapses is REJECTED with the reason ("supported in plan vN but the gate
no longer holds on the current facts" / no verified fact / finding no longer verified) —
cumulative never means zombie. Unlicensed grounds still never reach SUPPORTED.

**2. Particularised timing defects (validation + drafting).**
- `legal/findings.py`: `FindingSpec.timed` marks the defect families whose proof is the date
  calculation itself (`POFA_POSTAL_LATE`, `POFA_NTD_NTK_LATE`, `POFA_NTD_NTK_TOO_EARLY`);
  `particulars(finding)` returns the calculation's dates and day count (empty for content
  defects); `date_stated` / `days_stated` accept the customary renderings ("31 July 2026",
  "31st July 2026", "July 31, 2026", "31/07/2026", ISO; "3 days" / "three days");
  `render_date` / `particularised_sentence` produce the compliant wording the demo drafters
  use; `for_pack` now carries `legal_module_id`.
- `VAL-PARTICULARS` (letter-level, BLOCK): when a VERIFIED timed finding licenses an approved
  ground, the letter must state every date in its calculation and the day count; the issue
  message carries the actual values, so a retrying drafter can comply. Letter-level issues
  cannot be trimmed, so a stubborn drafter goes to MANUAL_REVIEW, never to the customer.
- `VAL-COVERAGE` (letter-level, BLOCK): every approved Claim Plan item needs at least one
  GROUNDED sentence — an established ground can no longer silently drop out of the letter.
- Drafting prompt v12 → v13: the five-particular rule (event date, issue date, statutory
  deadline, deemed delivery date, exact days outside, then the transfer-failure conclusion),
  the coverage rule, and the account-sequence rule for records-based grounds (set out the
  keeper's sequence, explain why first-entry/final-exit captures do not establish one
  continuous stay, request the complete capture records).
- Demo drafters (`pcn_appeal/llm.py`, `tests/support.py`) emit the particularised sentence
  per licensing finding, grounded to its module with the date facts in `fact_refs`.

**3. Alpha's test, reproduced end-to-end (synthetic data).** The released letter now reads:

> On the operator's own documents the parking event took place on 17 July 2026, the Notice
> to Keeper was issued on 30 July 2026; the statutory period for delivery of the notice ended
> on 31 July 2026 and it is deemed delivered on 3 August 2026, 3 days outside that period, so
> the notice was not delivered within the statutory period and keeper liability does not
> transfer.

with the multiple-visit ground kept alongside (KB-POFA-01/02/05 + KB-ANPR-01 all SUPPORTED).

## The Master Case Object question

**The equivalent single authoritative object already exists; no restructure.** The state
passed into Draft Generation is one object built in one place (`DraftContext.to_payload`,
from `CaseFile` + the locked plan + `RetrievalPack`), mapping onto Alpha's seven components:

| Alpha's component | Where it lives in V2 |
|---|---|
| Notice facts / customer facts | Fact Graph on `CaseFile` (every fact carries source: DOCUMENT / ANSWER / narrative), serialized as `verified_facts` + `fact_refs` + `fact_basis` |
| Calculated facts | `legal_findings` (P6.1): persisted per-case findings with `calculation_result`; VERIFIED ones travel as `verified_legal_findings` |
| Applicable knowledge | the locked Claim Plan's items (`case_context.claim_plan`, `module_ids`) and their retrieved wording (`context_chunks`) |
| Missing facts | plan items UNRESOLVED / MISSING_FACTS; questions come only from these (P3 Question Authority) |
| Supported grounds, cumulative | plan SUPPORTED items — now with the two new licensed routes, so a ground cannot vanish with the model's selection (the P6.2 fix) |
| Draft requirements per ground | `verified_legal_findings` calculations + prompt v13 rules |
| Validation compares the draft back | DV engine per-sentence grounding + VAL-LEGAL-FINDING + **VAL-PARTICULARS** + **VAL-COVERAGE** (the P6.2 fixes), failing before the customer |

The investigation showed the dates were **not** lost between Case Intelligence and drafting:
since P6.1 the payload carried the full calculation (deadline, presumed delivery, days). Two
gaps caused Alpha's letter: (a) the plan builder dropped the timing ground when CI stopped
proposing it, and (b) nothing required the calculation to be *stated*, so the generic KB
sentence (PP-POFA-003) satisfied validation. Both are now closed at the engine level, not by
any operator- or case-specific rule. The reproduced payload for Alpha's test is attached to
the review (synthetic reproduction — the deployed case store needs an admin token we don't
hold in this session).

## WHY / ROOT CAUSE

1. `claim_plan_authority.build` step 1 read "CI's selection — the only route to SUPPORTED".
   Reproduced: with the VERIFIED finding present but KB-POFA-02/05 omitted from proposals,
   both came back `REJECTED NOT_SELECTED | gate holds on the facts but Case Intelligence did
   not select it`. One non-deterministic model omission = a vanished statutory ground.
2. The generic wording came from the KB block itself being sufficient: no rule demanded the
   calculation's dates in the letter, so drafters (demo and model alike) stopped at
   "not delivered within the applicable statutory period".

## BLAST RADIUS

- Letters change **only** where a verified timed finding licenses an approved ground: the 4
  PoFA journeys gain the particularised paragraph (goldens regenerated, +1 paragraph each);
  the 6 reference scenarios (no verified timing findings) are byte-identical to P6.1.
- Plans gain items only via the two licensed routes; both run the same gate / relation /
  support / evidence checks as selection. `REG_no_notice` and `REG_notice_plus_payment`
  states unchanged (incl. the deliberate PROCESSING_ERROR journey).
- Validation is stricter (two new letter-level BLOCK rules). On the demo path retry resolves
  them; on a real model they may raise retry rate (flagged for the staging run).
- Prompt v13; `plan_digest` unchanged in shape (decisions/reasons are not in the digest).

## NOT CHANGING

- The PoFA calculator, finding evaluation and statuses (P6.1) are untouched; UNRESOLVED still
  yields no claim, no letter wording, no particulars duty.
- Content defects (`NTK_CONTENT_DEFECT`, invitation) need no timing particulars — `timed`
  marks defect *families*, not operators or cases.
- Question Authority, routing, driver disclosure (tri-state; narrative never sets it),
  PROCESSING_ERROR vs NO_SUPPORTED_GROUNDS.
- No restructure around a new "master case object": the audit above shows the existing
  CaseFile → locked plan → DraftContext chain is that object.

## TEST PLAN

| Check | Result |
|---|---|
| `tests/test_cumulative_drafting.py` (new: cumulative routes, lapse, Alpha scenario end-to-end, particulars block/pass, coverage, stubborn drafter → MANUAL_REVIEW, date renderings) | 14 / 14 OK |
| `test_verified_legal_findings.py`, `test_claim_plan_authority.py` (34, incl. rewritten authority invariant: *unlicensed* grounds are never added; established grounds survive losing selection), `test_draft_intelligence.py` (44), `test_prompts.py` | OK |
| Full suite | same 29 known baseline failures, none new |
| Before/after, 6 reference scenarios vs P6.1 | identical letters and states |
| Repeat 5 × 6 scenarios | 1 distinct result each |
| 5 journeys vs regenerated goldens | all PASS; diff per PoFA journey = the one added paragraph |
| Alpha reproduction (synthetic) | RELEASED; letter states 17 July 2026 / 30 July 2026 / 31 July 2026 / 3 August 2026 / 3 days / "does not transfer"; KB-ANPR-01 kept |
| Mutations (finding licence off: 1 fail; carry-forward off: 3 fails; VAL-PARTICULARS off: 1 fail; VAL-COVERAGE off: 2 fails) | all caught, originals restored, suite OK |

## DEPLOYMENT

1. No migration (P6.2 uses the P6.1 `legal_findings` table as is).
2. Deploy; prompt bump to drafting v13 ships with it.
3. Staging smoke: run the journeys against `journeys/golden`; then a late-notice case and
   confirm the letter's PoFA paragraph carries the five particulars.
4. Not run, needs you: migrations 0006–0009 on PostgreSQL staging; a real-model staging run —
   v13 + VAL-PARTICULARS/VAL-COVERAGE may raise the retry rate on PoFA cases, watch
   MANUAL_REVIEW volume.
