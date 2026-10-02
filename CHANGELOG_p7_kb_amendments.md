# P7 — client-directed KB amendments and a false-assertion fix

Implements the client decisions of 2026-10-02 on KB-BAY-01, KB-BAY-02,
KB-REC-01 and the `payment_attempt_failed` false positive.

Full suite: 964 run / 0 failed / 7 skipped. Journeys: 5/5 PASS, no golden diffs.

KB-POFA-06 is deliberately NOT included: the amended wording is drafted and
awaiting client approval, and the module remains `status: REVIEW`.

---

## 1. `payment_attempt_failed` — one sentence could contaminate another

**WHAT.** Free-text circumstance rules matched across sentence boundaries. All
nine `.{0,N}` gaps in `pcn_appeal/engines/account.py` are replaced with
`_gap(n)` = `[^.!?\n]{0,n}`, which cannot run past the end of a sentence.

**ROOT CAUSE.** `.` matches a full stop. "I paid at the machine when I arrived.
I did not realise the time had run out." matched `machine when I arrived. I did
not` and asserted a FAILED payment for a customer who had in fact paid.

**A SECOND, MORE SERIOUS DEFECT, found while fixing the first.**
`disability_extra_time` matched the bare word "disabled" with no gap at all, so
"My father is disabled" pleaded an Equality Act ground (KB-EQ-01, strength 65,
activates on that fact alone). A gap bound could not fix this; the pattern was
rewritten to require first-person adjacency to the condition, or a disability
reason attached to the time taken.

**BLAST RADIUS.** A shipped journey golden contained the fabricated assertion.
Re-recorded; letter content hash unchanged, so no released wording moved.

**TEST PLAN.** `tests/test_sentence_boundaries.py` (25 tests), written before
the fix per client instruction. Includes the reproductions, recall tests
proving the rules still read what the customer did say, and property tests
asserting no rule reintroduces a raw `.{0,N}`.

## 2. KB-BAY-01 v1.5 — principle approved, trigger amended

**WHAT.** The ≤5-minute observation threshold is removed. The module now
activates on a new derived fact `bay_eligibility_unevidenced` (EX-19).

**WHY.** No minute figure carries independent legal significance, and the
figure was not from approved source material. An eligibility bay is enforced
on who or what was using it: a timestamp records presence, not entitlement.

**NOT CHANGING.** The evidential put-to-proof principle, which the client
approved, is retained in full.

`prohibited_claims` gains "asserting the observation period was too short to be
lawful" and "treating any particular observation duration as legally
insufficient". `tests/test_kb_amendments_v2.py` sweeps every module and asserts
that NO module anywhere thresholds the observation window.

## 3. KB-BAY-02 v1.1 — approved with the provenance tightening

**WHAT.** `FreeTextExtraction` gains `provenance` (INFERRED / STATED /
CONFIRMED) and `customer_asserted`. A material fact may be restated to the
operator as the keeper's own assertion only where the customer explicitly
stated or confirmed it. `use_when` additionally requires
`material_account_proposition` to exist.

**WHY (client).** "I do not want an important factual proposition asserted in
the customer's name merely because the system inferred it."

**KNOWN LIMITATION, flagged for the client.** `child_occupant_present` has no
closed-form question, unlike the other eligibility facts
(`blue_badge_displayed`, `permit_held`, `loading_activity`,
`ev_charging_session`, `disability_extra_time`). An inferred-only
parent-and-child account therefore yields no KB-BAY-02 paragraph and no
question. This is the safe direction — nothing is asserted — but the ground is
unargued. Adding the question requires a change to the question machinery's
"a fact exists ⇒ do not ask" invariant and is NOT included here.

## 4. KB-REC-01 v1.2 — keyword activation removed

**WHAT.** `use_when` now requires `validation_mechanism_material`, not merely
`validation_mechanism_alleged`. EX-20 sets the materiality fact only where a
confirmed or extracted case fact establishes the mechanism is genuinely in
issue, and audits the named basis either way.

**WHY (client).** "A module should activate because the actual case facts make
it relevant, not because a keyword appeared."

**BLAST RADIUS.** REC-01 dropped from all four REG_* goldens.
`REG_notice_plus_payment` improved from MANUAL_REVIEW / PROCESSING_ERROR /
FAILED with `['VAL-REPEAT','VAL-SUBSTANCE']` to RELEASED / PASSED with no
issues. All five approved grounds remain GROUNDED.

## 5. Two generic improvements kept from the investigation

Question materiality now follows the `depends_on` derivation edges authored in
`kb_relations.yaml`, in both `orchestrator._could_change_a_ground` and
`question_authority._module_materiality` (depth 4, cycle-safe). Judged on
direct gates alone, a confirming question for a fact that only feeds a derived
fact was rejected as immaterial.

## 6. Not addressed (flagged, not fixed)

`HIRE_KEEPER` in `pcn_appeal/engines/extraction.py` still contains two raw
`.{0,40}` gaps. It matches notice text rather than customer prose, so it
cannot fabricate a customer assertion; left for the POFA-06 work.
