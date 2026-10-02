# P7 — Confirmed fact → ground propagation (Scope B)

Branch `feature/p7-kb-amendments`. Generic fix to the contract by which an
established customer fact becomes an appeal ground in its own right, rather
than being replaced by the evidential put-to-proof argument that merely
supports it.

Nothing here is specific to parent-and-child bays, to any operator, or to any
PCN. The bay case is the reproduction; the mechanism is typed and general.

---

## WHAT

| # | Change | File |
|---|--------|------|
| 1 | New allegation proposition layer: the notice's assertions become typed propositions (subject / predicate / asserted value) via a controlled rule table. Unrecognised wording yields no propositions. | `pcn_appeal/allegation.py` (new) |
| 2 | One reusable fact ↔ allegation relationship function returning `CONTRADICTS` / `SUPPORTS` / `PARTIALLY_ADDRESSES` / `UNRELATED`. Not four condition branches. | `pcn_appeal/allegation.py` |
| 3 | New `FACTUAL_REBUTTAL` ground: a usable fact that contradicts a proposition, carrying `required_particulars` (`STATE_FACT`, `CONNECT_FACT_TO_ALLEGATION`). | `pcn_appeal/rebuttal.py` (new) |
| 4 | Narrative provenance made four-tier: `STATED` / `CONFIRMED` / `ASSERTED` / `INFERRED`. A `CircumstanceRule.asserts` pattern distinguishes wording that states a fact outright from wording a fact can merely be read out of. | `pcn_appeal/engines/account.py` |
| 5 | Locked claim plan carries `factual_rebuttals`; `grounds()` orders rebuttals before KB modules; `content_digest()` covers them. Immutability and versioning unchanged. | `pcn_appeal/engines/claim_plan_authority.py` |
| 6 | `CaseFile.factual_rebuttals`. | `pcn_appeal/models.py` |
| 7 | DraftContext emits each rebuttal as REQUIRED content with `role: "PRIMARY"`. The drafter is told the contradiction, never asked to rediscover it. | `pcn_appeal/drafting/context.py` |
| 8 | New `VAL-MATERIAL-FACT-COVERAGE`: a required particular absent from the letter is an issue. Matching is semantic (fact-language tokens), not exact-string. | `pcn_appeal/engines/validation.py` |
| 9 | New `VAL-POFA-AUTHORITY`: substantive Schedule 4 wording requires an authorised PoFA ground. Neutral keeper-status wording is not blocked. | `pcn_appeal/engines/validation.py` |
| 10 | KB-POFA-01 → v1.1 (client directed): the gate additionally requires a specific authorised PoFA issue. Proposition and building blocks unchanged. | `pcn_appeal/data/kb_modules.yaml` |
| 11 | Finding → module attribution decided by gate specificity, not `kb_modules.yaml` order. | `pcn_appeal/legal/findings.py` |

## WHY

The regression: a confirmed material customer fact could exist in the Fact
Graph, be relevant to the allegation, and still be downgraded or lost before
the final claim and draft. The letter argued only "the operator has not proved
the contrary" and never stated the customer's own answer.

Required output semantics, now enforced:

- PRIMARY — the allegation is factually disputed, because the customer's
  established fact says otherwise.
- SECONDARY — the operator's evidence does not establish the contrary.

The customer fact is the ground. The evidence challenge supports it and may
never replace it.

## ROOT CAUSE

The six-stage trace (Fact Graph → allegation representation → Case
Intelligence → Claim Plan → DraftContext → draft validation) located the first
defective boundary at **stage 2: there was no allegation representation at
all.** The allegation existed only as free prose in `alleged_breach`. With
nothing typed to contradict, no stage downstream could carry a factual
rebuttal, so the only thing that could ever reach the plan was the evidential
module — which is why the support arrived and the answer did not. The
proximate loss point on the inferred path was
`_account_contradicts_allegation` (`account.py:609`).

Two further defects found and fixed in the same area:

- A released letter could carry a substantive Schedule 4 paragraph with
  `pofa_finding: None` and zero validation issues. `POFA_DEFECT` only caught
  specific defect assertions, so generic Schedule 4 wording passed freely —
  hence a new validator rather than a widened regex.
- Being the registered keeper with the driver unidentified was authorising
  substantive PoFA wording on its own. Keeper status is the circumstance in
  which Schedule 4 may matter, not a ground.

## BLAST RADIUS

Full suite **1004 tests, 0 failures** (38 new). Journeys **5/5 PASS**.

Across all five journey goldens exactly one recorded field changes:
`plan_digest`, because the digest now covers `factual_rebuttals` by design.
Every drafted letter (`content_hash`), approved module list, paragraph
structure, validation outcome and fact set is byte-identical to the
pre-change baseline. No existing letter changed.

Item 11 is worth calling out. KB-POFA-01's v1.1 gate names the PoFA finding
codes as preconditions, which made it the first module in file order to
mention them. `licenses.setdefault` therefore moved `POFA_POSTAL_LATE` from
KB-POFA-02 (topic "Postal NTK timing", strength 95, gated on that one finding)
to KB-POFA-01 (general keeper-liability threshold, strength 40). That is not
cosmetic: `legal_module_id` decides which paragraph argues the finding's dates
and day count (`llm.py`) and `VAL-PARTICULARS` polices them there, so the
postal timing calculation would have been attached to the generic threshold
sentence. Attribution is now by specificity — fewest finding codes named, then
strength, then module id for determinism — which restores every pre-change
attribution without hard-coding any module.

## NOT CHANGING

- No PDF layout change.
- No redesign of P1–P7 components.
- No operator-specific or PCN-specific behaviour.
- No Parent & Child wording patch, no canned BAY paragraph, no BAY-specific
  drafting rule.
- No new generic fallback questions. In particular no Parent & Child
  confirmation question was created to manufacture confirmation.
- Claim plan immutability (`_MUTABLE_WHEN_LOCKED`), lifecycle and versioning
  guarantees untouched; rebuttals are set while the plan is DRAFT.
- KB-POFA-01's core proposition and building blocks unchanged; only its gate.
- KB-POFA-06 remains REVIEW pending client approval of the drafted wording.

## TEST PLAN

New, written RED first:

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_factual_rebuttal.py` | 26 | explicit assertion is not an inference; typed propositions; generic relationship model; a confirmed fact becomes a ground; the factual rebuttal leads the evidential point; the mechanism is not bay-specific (permit / payment / continuity); ranking orders but does not delete |
| `tests/test_pofa_authority.py` | 7 | keeper status alone is not a ground; a specific verified defect is allowed; `VAL-POFA-AUTHORITY`; neutral keeper wording not blocked |
| `tests/test_material_fact_coverage.py` | 5 | coverage of a required particular; coverage is semantic, not exact-string |

Amended because their premises were superseded by the client's provenance
correction (both previously relied on explicit prose being treated as
inferred):

- `tests/test_kb_amendments_v2.py` — `test_an_inferred_read_does_not_establish_the_contradiction`
  now uses genuinely oblique wording ("I was travelling with family that
  afternoon"); new `test_an_explicit_narrative_assertion_does_establish_it`
  pins the client's own example as ASSERTED.
- `tests/test_p4_knowledge_relations.py`, `tests/test_keying_error.py` — same
  distinction.

Amended because they had silently relied on KB-POFA-01 being approved on
keeper status alone — the exact spurious ground this work removes:
`tests/test_claim_plan_authority.py`, `tests/test_draft_intelligence.py`,
`tests/test_assertion_only_findings.py`.

Commands:

```
DATABASE_URL= LLM_PROVIDER= APP_ENV= PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests
DATABASE_URL= LLM_PROVIDER= APP_ENV= PYTHONPATH=. .venv/bin/python -m pcn_appeal.integrity journeys journeys/ --golden journeys/golden --out /tmp/jr
```

## DEPLOYMENT

No migration required. `factual_rebuttals` lives on the claim plan payload and
the case object; no schema column was added and `legal_findings` is unchanged.

`plan_digest` values change by design, so any stored digest from before this
change will not match a recomputed one. Digests are integrity checks within a
run, not cross-version identifiers, and no persisted plan is invalidated.

Not deployed. No merge to main.
