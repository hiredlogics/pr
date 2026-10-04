# P10.3 — Case semantic layer + ground role / eligibility

STOP after development review. No production deploy. Old P9 blind holdout
was **not** run for tuning. Fresh holdout under `datasets/p10_3_v1/holdout/`
is sealed.

---

## 1. Module-role taxonomy

| Role | May be Claim Plan ground? | May lead letter? |
| --- | --- | --- |
| `SUBSTANTIVE_GROUND` | Yes | Yes (if strength ≥ 50) |
| `EVIDENCE_REQUIREMENT` | Yes | Yes (if strength ≥ 50) |
| `SUPPORTING_PROPOSITION` | No | No |
| `LEGAL_CONCLUSION` | No | No |
| `STRUCTURAL` | No | No |

Runtime: `pcn_appeal/module_roles.py` + `KBModule.module_role`.
YAML may override; defaults loaded in `KnowledgeGraph._build`.

## 2. Modules classified by role

| Role | Count | Modules |
| --- | --- | --- |
| SUBSTANTIVE_GROUND | 40 | PAY/KEY/BREAK/RES(−05)/CON/GRACE/ANPR-01/BAY/AUTH/CUST/EQ/HOSP(−03)/ACT/EVCH/INFRA/POFA-02..04,06/REC-01… |
| SUPPORTING_PROPOSITION | 9 | LAND-01..03, SIGN-01..04, RES-05, HOSP-03 |
| LEGAL_CONCLUSION | 2 | **KB-POFA-01**, **KB-POFA-05** |
| EVIDENCE_REQUIREMENT | 4 | ANPR-02, ANPR-03, EV-01, **TIME-01** |

## 3. Semantic ontology

Controlled concepts in `pcn_appeal/semantics/ontology.py`:

- MOVEMENT: LEFT_SITE, RETURNED, MULTIPLE_VISITS  
- PAYMENT: PAYMENT_MADE, PAYMENT_ATTEMPTED, PAYMENT_FAILED, KEYING_ERROR  
- ACTIVITY: SHOPPING, DROP_OFF, PICK_UP, LOADING, DELIVERY, COLLECTION  
- VEHICLE: BROKEN_DOWN, IMMOBILISED  
- PERMIT: PERMIT_HELD, PERMIT_DISPLAYED, REGISTRATION_MISMATCH  
- PERSON: PASSENGER_PRESENT, CHILD_PRESENT, DISABLED_PASSENGER  

Each concept carries polarity (AFFIRMED / NEGATED / UNCERTAIN), attribution,
source text, confidence, provenance. Affirmed concepts promote via
`CONCEPT_TO_FACTS` into FactManager **before** Question Authority.

## 4. LLM semantic extraction schema

```json
{
  "concepts": [
    {
      "concept": "<ONTOLOGY_ID>",
      "polarity": "AFFIRMED|NEGATED|UNCERTAIN",
      "attribution": "CUSTOMER",
      "source_text": "<excerpt>",
      "confidence": 0.0
    }
  ]
}
```

Hard rules: only ontology ids; never KB module ids, legal grounds, legal
conclusions, or appeal outcomes (`validate_concepts` strips leakage).

Deterministic patterns always run; LLM path is optional and fails open to
deterministic results (no prompt registry change required for DEV).

## 5. Ground eligibility changes

Pipeline (`engines/ground_eligibility.py` + Claim Plan Authority):

candidate → **role check** → required facts → required semantic concepts
→ blocking / use_when → support → Claim Plan

New decision: `ROLE_INELIGIBLE` rejects SUPPORTING / LEGAL_CONCLUSION from
VF, CF, and SELECTED sets (with GroundInvalidation — no silent drop).

`leading_grounds` also requires `can_lead_letter` (claim-ground role).

## 6. Orphan-support handling

- Invariant `VAL-ORPHAN-SUPPORT` (ValidationEngine VAL-5): pack with only
  support/conclusion modules BLOCKS.
- Orchestrator already holds when `leading_grounds` is empty →
  `NO_SUPPORTED_GROUNDS` (no invented ground, no empty letter).

## 7. Generic development tests

- `tests/test_p10_3_semantic_layer.py` — roles, ontology, eligibility,
  orphan, DEV metric smoke, sealed holdout presence.
- Dataset: `datasets/p10_3_v1/` — 8 DEVELOPMENT cases (distinct wording from
  old blind) + 4 sealed HOLDOUT cases (expectations not used in this phase).

## 8. DEVELOPMENT before / after metrics

### Full P9 DEV / VAL after P10.3 + keeper-warning paraphrase fix

| Split | P9 baseline | P10 | P10.3 final |
| --- | --- | --- | --- |
| DEVELOPMENT E2E | 45.5% | 72.7% | **100% (11/11)** |
| VALIDATION E2E | 25% | 75% | **100% (4/4)** |
| DEV ground precision | ~30% | 32.5% → 58% | **100%** |
| DEV ground recall | — | 100% | **100%** |

Loop found and fixed (not fine-tuning): `scan_keeper_warning` required
near-verbatim “do not know name and address” text, so common Schedule 4
paraphrases (“seeking recovery from the keeper … if the driver is not
identified”) were treated as missing → false `KB-POFA-04`. Broadened to
generic limbs only. Harness: empty `NO_SUPPORTED_GROUNDS` letters no longer
fail draft `must_express` on NOTICE_ONLY.

| Case | P10 (before roles) | P10.3 final |
| --- | --- | --- |
| Late postal NTK grounds | POFA-01,02,04,05 | **POFA-02** |
| Payment + keying grounds | PoFA companions + PAY + KEY | **PAY-01 + KEY-01** |
| Companion FP (POFA-01/05) | common | **0** |
| False POFA-04 on compliant NTK | common | **0** |

Old P9 blind was **not** used for tuning. Fresh `p10_3_v1` holdout remains sealed.

## 9. P8 invariant results

| Invariant | Result |
| --- | --- |
| Master Case / Claim Plan authority | PASS (`tests.test_p8_architecture`) |
| FactManager authority | PASS |
| Verified legal findings | PASS (`tests.test_verified_legal_findings`) |
| Additive grounds / invalidation | PASS (ROLE_INELIGIBLE recorded) |
| SupportBundle / DraftContext | unchanged contracts |
| Validator safety | VAL-5; scenarios A–D PASS |
| Scenario / P10 remediation | PASS |
| KB-TIME-01 duration dispute | PASS (role = EVIDENCE_REQUIREMENT) |

Old P9 blind **not** re-run for tuning.

## 10. Recommendation

**SEMANTIC_LAYER_SUFFICIENT** (for this cycle)

- Controlled concepts + typed eligibility already remove the dominant
  ground-precision failure (companion LEGAL_CONCLUSION modules) and
  promote narrative facts without question-gated invention.
- Candidate discovery recall was not shown to be the bottleneck (ground
  recall on substantive classes remains high).
- **Do not add pgvector yet.** Reconsider `ADD_VECTOR_RETRIEVAL` only if a
  future DEV suite shows retrieval miss classes after role+semantic belts.
- Further `MORE_GROUND_MODEL_WORK` only if CLOSED goldens still conflict
  with published substantive gates after client golden review (D10-02).

### Confirmations

- No operator-specific / PCN-specific patches  
- No old blind-set tuning  
- No golden expectation edits to force passes  
- No P8 redesign; Claim Plan remains authority  
- No production deploy  

**STOP after development review.**
