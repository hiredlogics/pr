# P8: fact-to-ground matching

## Problem (live test, 7 Oct 2026)

Six real notices were run with a thin customer account ("I am the registered
keeper. I do not accept this charge"). Only notices with a late Notice to Keeper
produced a letter. Airport drop-off, supermarket overstay and no-valid-session
notices were asked nothing and ended with no supported ground.

Root cause: KB gates are written in judgement facts (`customer_only_site`,
`permitted_period_ended`, `short_presence_before_acceptance` ...). Extraction
produces raw facts (location, contravention wording, entry/exit times). Nothing
converted one into the other, so the only route to a gate fact was a customer
answer, and the question path skipped thin accounts.

## Changes

1. **Derivation layer** (`engines/derivation.py`, `data/derivation_rules.yaml`).
   Runs inside fact recovery, before calculations. Deterministic rules turn
   document facts into gate facts: `alleged_breach_type`,
   `permitted_period_ended`, `short_presence_before_acceptance` (duration within
   the Code consideration period of the resolved Code version),
   `customer_only_site`, `dropoff_site`, `hospital_site`, `relevant_land`
   (byelaw sites). Never overwrites a customer or document value; no match
   writes nothing.
2. **Byelaw land stays NOT_APPLICABLE.** Three places forced `pofa_route` back
   to POSTAL whenever it came out NOT_APPLICABLE; they now respect
   `relevant_land is False`.
3. **Fact producer registry** (`data/fact_producers.yaml`) and
   `tests/test_fact_producers.py`: every fact a gate reads must name a
   producer (DOCUMENT / EVIDENCE / DERIVED / SYSTEM / QUESTION / OPERATOR), or
   the build fails.
4. **Questions follow the gates** (`engines/analysis.py`):
   - "thin account" is judged by the facts the account established, not by
     `len(narrative) < 40` (a 79-character empty account used to skip every
     question);
   - new `document_pointed_gaps`: a claim ground whose gate is already partly
     TRUE on document/derived facts gets its remaining customer facts asked;
   - `payment_made` / `multiple_visits` are always offered on a thin account
     (the authority still rejects immaterial ones);
   - unlocking questions are ordered: customer-raised, then document-pointed,
     then generic, so the 4-question cap never cuts the customer's own topic.
5. **Question Authority materiality fix** (`engines/question_authority.py`): a
   number/free-text answer to an `exists`/`is` condition has one possible truth
   value, so "answers differ" could never hold and such questions (e.g.
   `exit_delay_min` for KB-GRACE-01) were always rejected. Now material when
   that answer moves the module off UNRESOLVED.

## Result on the test notices (thin account)

| Notice | Before | After |
|---|---|---|
| APCOA Luton drop-off, 3 min | asked nothing, no ground | asks payment, drop-off; KB-CON-01 |
| Euro Car Parks Sainsbury's overstay | asked nothing, no ground | asks exit delay; KB-GRACE-01 |
| ParkMaven no valid session | asked nothing, no ground | asks payment; no ground invented on "no" |

## Tests

20 new tests (`test_derivation`, `test_fact_producers`,
`test_fact_ground_matching`). Full suite: no new failures against the branch
baseline (113 failures / 18 errors pre-existing); one pre-existing failure now
passes. Four tests were updated because the notice now settles a fact they
assumed unknown; each keeps its original intent (see inline P8 comments).

## Needs client / legal sign-off

- Breach classes, `permitted_period_ended` mapping, site lists and byelaw site
  list in `derivation_rules.yaml`.
- The KB has no module arguing "land under byelaws is not relevant land". With
  this change airport notices no longer get a PoFA timing argument, but they do
  not get a byelaw ground either until the client adds one.
- KB-CUST-01 needs an uploaded receipt; the customer should be asked to upload
  one, which is an evidence request the question flow does not make yet.
