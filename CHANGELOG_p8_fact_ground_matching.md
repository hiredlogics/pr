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
- ~~The KB has no module arguing "land under byelaws is not relevant land".~~
  Done: KB-POFA-07, approved by the client on 7 Oct 2026 (see below).
- KB-CUST-01 needs an uploaded receipt; the customer should be asked to upload
  one, which is an evidence request the question flow does not make yet.

## Client instructions, 7 Oct 2026

1. **KB-POFA-07 (airport / statutory control)**, ACTIVE, strength 95, leads like
   a PoFA timing failure. Client wording verbatim (PP-POFA-009) plus a
   strict-proof fallback (PP-POFA-010) for an operator that says the zone is
   outside the controlled land. `pofa.assess` emits a verified
   `POFA_NOT_RELEVANT_LAND` finding; PP-POFA-006 is not appended to it.
2. **Location-level activation.** `statutory_control_sites` in
   `derivation_rules.yaml`: only CONFIRMED locations set `relevant_land`
   (Luton and Stansted pick-up/drop-off zones); other airports and other
   Luton/Stansted locations are PENDING and change nothing. Each location
   supplies its jurisdiction (the Luton notice prints no postcode). Railway
   land stays out (2025 amendment order).
3. **Default keeper appeal (KB-KEEPER-01).** When no ground can lead and the
   keeper route is open, the orchestrator switches it on instead of ending
   with no letter. It never stacks on a stronger ground, and a question that
   could unlock a ground (site postcode) or an account not yet understood
   still comes first. Wording is jurisdiction-neutral.
4. **Document stage.** Classifier prompt v3 adds DRIVER_LETTER; text patterns
   back it up. A reminder or driver letter is never timed as the first NTK:
   the original is linked from an uploaded INITIAL_NOTICE or a date printed
   in the letter, else the customer is asked once for it. Particulars say
   "On the dates available" when the date came from the customer.
   PP-DRIVER-001 (driver-letter strict proof) drafted at REVIEW.
5. **Late appeals.** `appeal_period_expired` after 28 days from issue: the
   letter carries PP-LATE-001 and the API returns a customer notice. Never
   stopped.
6. **PP-POFA-007**: "verified" removed.

Also fixed while testing:
- VAL-GROUND-COVERAGE had no topic cue for CON, CUST, HOSP, EQ, EVCH, INFRA
  modules, so those grounds could never pass validation.
- R-08c: a paragraph that asserts a customer-owned fact ("time was required to
  consider the terms") needs the customer's own answer, not a derived value.
- Fixed-form grounds (KB-POFA-07, KB-KEEPER-01) always carry all their
  approved paragraphs, in order.

Tests: `tests/test_p8_client_instructions.py` (15). Full suite: no new
failures against the original baseline. Tests that expected "no supported
grounds" were updated to the default keeper appeal; three run-bookkeeping
tests switch the default off to keep the no-grounds terminal covered.

Wording drafted by the developer, to confirm with the client: PP-POFA-010,
PP-KEEPER-001/002/003, PP-LATE-001, PP-DRIVER-001 (REVIEW), and the customer
notice text for a late appeal.

## Follow-ups, 8 Oct 2026

### Remaining development items

- **Postcode skip loop.** "Skip the rest" on the site-postcode question held the
  case for the postcode, which showed the same question again. Skipped facts are
  now recorded (`raw_answers["_declined"]`), never asked again and never a reason
  to hold; the jurisdiction-neutral keeper appeal goes ahead instead.
- **Postcode / jurisdiction from the location.** A postcode printed in the site
  wording is used ("Quayside Shopping Centre (M50 3AH)"); otherwise a place name
  (`data/place_jurisdictions.yaml`), then the model's own reading of the site
  (`site_country`, extraction v12). Two jurisdictions named -> still asked.
- **Back page from another operator.** The classifier's issuer name is compared
  across notice pages (`notice_completeness.same_operator`: shared word, run
  together, or initials such as ECP / UKPC). A mismatched page is set aside and
  the customer is told why in `rejected`.
- **Letter quality.** The operator's recorded entry/exit times are stated; a
  sentence the letter already said is dropped; the closing no longer opens with
  "For the reasons set out above" twice. New `letter_document` / `letter_full`
  in the API (sender, operator address, date, reference, salutation, sign-off),
  used by the on-screen preview, the copy button and the PDF. Extraction v12
  reads `operator_address`.
- **Receipt request.** When a ground lacks only a document the customer may hold
  (KB-CUST-01: receipt or bank statement), the case asks for an upload once
  (`type: upload`, `POST /cases/{id}/evidence` and `/evidence-blobs`). The
  Question Authority counts such a document as obtainable, so "were you a
  customer?" is now asked. KB-CUST-01 had no usable wording (PP-AUTH-001 needs
  `authorisation_source`); PP-CUST-001 drafted for approval.
- **Notices 7 and 8** (ParkMaven Quayside, Euro Car Parks Morrisons Keighley) run
  in the harness; wording and store-name variants covered
  (`tests/test_p8_followups.py`).
- **Upload button.** Not reproduced: one click uploads on desktop and mobile
  emulation (Playwright, mocked API). Needs the device/browser where it happens.

### Client review, 8 Oct 2026

- **Lists are aids, not gates.** Breach, site and airport lists improve
  extraction, retrieval and verification; they do not limit which approved
  grounds the analysis can identify.
- **Drop-off zones.** No blanket "no permitted period". Overstay wording is
  matched first, so an overstay in a drop-off zone is an overstay. A permitted
  period is taken as not ended only when no overstay is alleged and the whole
  stay is inside the Code consideration period.
- **Consideration and grace unchanged, grace extended.** KB-CON-01 / KB-GRACE-01
  are as before. New: the actual overstay is calculated (`permitted_period` or
  `paid_until_time` on the notice vs the operator's times) and compared with the
  Code version's grace period; within it, PP-GRACE-005 (KB wording) is used.
- **Retail park / shopping centre** prompt the genuine-customer questions
  (`retail_site`, `points_at`); they never establish a customer-only site.
- **Appeal period.** A deadline printed on the notice first; else 28 days from
  the FIRST notice. A reminder or driver letter whose original cannot be dated is
  `MAY_HAVE_EXPIRED`, never a fresh period (PP-LATE-002, for approval).
- **AI-led statutory control.** Extraction reads `statutory_land_indicator`
  (AIRPORT / PORT / RAILWAY / BYELAWS) from the notice. Confirmed material ->
  KB-POFA-07 leads, as before. Any other airport land (listed, pending or never
  seen) -> new KB-POFA-08 puts the operator to strict proof that the land is
  relevant land (PP-POFA-011, for approval; strength 45, never leads alone).
  Railway land stays out.
- **PP-DRIVER-001** stays off.

Full suite: no new failures against the baseline (112 failures / 18 errors,
all pre-existing). Frontend: `tsc` clean, vitest 20/20. Note: `npm ci` fails
because `package-lock.json` is out of sync with `package.json` (pre-existing).
