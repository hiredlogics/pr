# P6.1 — Verified Legal Defect Gate (legal findings)

Branch `feature/p6-drafting-validation-intelligence`, built on P6 (`c4cbecf`). Not pushed.

## WHAT

A Legal Claim Evidence Gate. A specific legal defect now travels one road only:

```
document facts -> legal calculation engine -> LEGAL FINDING (VERIFIED | NOT_SUPPORTED | UNRESOLVED)
    -> Claim Plan (defect ground requires a VERIFIED finding, else REJECTED "Legal defect not verified")
    -> drafting (the model sees VERIFIED findings only, with their calculations)
    -> validation (VAL-LEGAL-FINDING: a defect sentence requires its own VERIFIED finding)
```

| Spec item | Where |
|---|---|
| 1. Legal finding model | `pcn_appeal/legal/findings.py`: `FindingSpec` registry (type, description, assertion pattern, facts), `evaluate(case, pofa_result)`, one record per (case, type) on `CaseFile.legal_findings`. |
| 2. Migration | `infra/migrations/0009_legal_findings.sql` (+ `postgres_schema.sql`, sqlite test DDL): finding_id uuid PK, case_id, run_id, finding_type, status CHECK, supporting_facts, calculation_result, legal_module_id, created_at, updated_at; UNIQUE(case_id, finding_type); trigger makes the identity immutable while the assessment may move. |
| 3. Claim Plan | `claim_plan.py` and `claim_plan_authority.py`: a ground whose gate passed because of `pofa_finding` is REJECTED (`NO_VERIFIED_FINDING`, "Legal defect not verified") unless that code is a VERIFIED finding; a gate-failed ground that names findings gets the same reason instead of a bare "gate does not hold". |
| 4. Drafting | `RetrievalPack.legal_findings` carries VERIFIED records only (`for_pack`); `DraftContext` re-filters and adds payload key `verified_legal_findings` (type, description, calculation); drafting prompt v12 states the rule. UNRESOLVED and NOT_SUPPORTED never reach the model. |
| 5. Validation | `VAL-LEGAL-FINDING` in `draft_validation_engine.py`: a sentence asserting a registered defect (late postal NTK, para 8 window, missing invitation, missing content) must map to a VERIFIED finding; "appears non-compliant" is refused when nothing is verified; putting the operator to proof stays allowed. `_conclusion_authorised` now requires the *matching* finding, not any finding. |
| 6. Generalised | The registry is the extension point: a new defect family (signage, payment, authority) is a new `FindingSpec` plus its deterministic calculator. Module requirements are read from each module's own gate (`referenced_findings`), never a hard-coded list. No operator- or PCN-specific rule anywhere. |
| 7. Trace | Execution trace gains `legal_findings` (type, status, facts, calculation, licensed module); case report gains "## Legal findings"; new integrity check `LEGAL_DEFECTS_VERIFIED`; `/cases/{id}/appeal` returns the pack's verified findings; rejection reasons appear on plan items as everywhere else. |
| 8. Tests | `tests/test_verified_legal_findings.py`, 27 tests. |

Finding types registered now: `POFA_POSTAL_LATE` (the spec's POFA_LATE_NOTICE, keeping the code the KB already uses), `POFA_NTD_NTK_LATE`, `POFA_NTD_NTK_TOO_EARLY`, `POFA_NTK_INVITATION_DEFECT`, `NTK_CONTENT_DEFECT`.

## WHY / ROOT CAUSE

The deterministic PoFA calculator existed (P0's `legal/pofa.py`), but its result was held as one scalar fact plus an unpersisted code list, and three gates trusted it loosely:

1. The claim-plan belt accepted **any** finding for **every** PoFA-timing ground.
2. Validation (VAL-POFA, DV-LEGAL) allowed **any** PoFA-flavoured defect sentence as long as **some** finding existed. A case with only a content defect could state a late-delivery defect.
3. Nothing persisted the calculation, so a reviewer could not see what proved a drafted defect.

The production letter that triggered this phase stated a late Notice to Keeper. That calculation was in fact verified (issued 13 days after the event, presumed delivery 3 days past the deadline), but the system could not prove it per sentence. Now it can, and the cross-defect hole is closed: on the P6 commit, a late-delivery sentence on a case whose only verified defect was the invitation defect passed validation; on P6.1 it is blocked by VAL-LEGAL-FINDING.

## BLAST RADIUS

- Decision paths unchanged on honest runs: the hard belt fires only when the gate fact and the calculation diverge. Before/after on the 6 reference scenarios: identical letters and states. The 5 golden journeys: PASS, no diffs.
- Validation is stricter: a defect sentence now needs the matching finding. Legacy packs that only carry `pofa_findings` codes are still honoured (those codes are the verified types).
- Prompt: drafting v11 -> v12 (one new rule block). Claim-plan exclusion reason "PoFA finding required" became "Legal defect not verified...".
- New table only; `save()` upserts findings, `load()` reads them. One new audit event (`legal_findings`), new trace key, new report section, new check.

## NOT CHANGING

- The PoFA calculator itself (dates, margins, UNRESOLVED boundary behaviour) is untouched.
- Fact Graph, Question Authority, routing, document classification, driver disclosure (tri-state; narrative never sets it).
- UNRESOLVED stays distinct from NOT_SUPPORTED, and `PROCESSING_ERROR` from `NO_SUPPORTED_GROUNDS`.
- Checks observe; the new check does not gate release.

## TEST PLAN

| Check | Result |
|---|---|
| `test_verified_legal_findings.py` (spec tests 1–5 + persistence, trace, generality) | 27 / 27 OK |
| Full suite | 820 tests, same 29 known failures as baseline, none new |
| Before/after vs P6 `c4cbecf`, 6 reference scenarios | identical letter hashes and states |
| Repeat 5 × 6 scenarios | 1 distinct result each |
| 5 journeys vs golden snapshots | all PASS, no diffs |
| Cross-defect leak demo (content defect verified, timing compliant, late-delivery sentence) | P6: no rule fires. P6.1: VAL-LEGAL-FINDING blocks |
| Mutations (gate disabled: 1 failure; assertion scan disabled: 3; persistence removed: 2+1 error) | all caught, originals restored, suite OK |

Spec mapping: (1) late notice -> VERIFIED, KB-POFA-02 argued, released; (2) compliant -> NOT_SUPPORTED, injected ground rejected "Legal defect not verified"; (3) missing date -> UNRESOLVED, nothing drafted, nothing in the payload; (4) injected defect sentence -> VAL-LEGAL-FINDING, end-to-end the letter never carries it; (5) the production postal shape -> VERIFIED with days_between=3 and the defect allowed, and the one-day presumed-delivery boundary stays UNRESOLVED and silent.

## DEPLOYMENT

1. Apply `infra/migrations/0009_legal_findings.sql` (additive, after 0008). Rollback: drop `legal_findings` and its trigger.
2. Deploy. No config change. Prompt bump to drafting v12 is in the release.
3. Staging smoke: run the journeys with `--golden journeys/golden`; then `GET /admin/cases/{id}/audit` on a PoFA case and confirm the Legal findings section shows the calculation.
4. Not run, needs you: migrations 0006–0009 on PostgreSQL staging; a real-model run (the v12 rule plus VAL-LEGAL-FINDING may raise retry rate on PoFA cases).
