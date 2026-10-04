# P10.5 — Semantic Generalization Hardening

Improve semantic extraction generalization. No P8 redesign, no Claim Plan
authority change, no pgvector, no fine-tune, no production deploy.

## 1. Semantic extraction root-cause analysis

- Summary: Precision strong, recall weak because meaning extraction depended on exact phrase matchers and the LLM semantic path was never called.
- Trace: `['raw narrative', 'deterministic patterns (primary in P10.4)', 'optional LLM semantic_extraction (NOT INVOKED — llm never passed)', 'validate_concepts', 'concept promotion', 'FactManager']`
- P10.4 holdout classifications:
```json
{
  "HOLDOUT_A": {
    "missed": [
      "BROKEN_DOWN",
      "IMMOBILISED"
    ],
    "classes": [
      "A",
      "B"
    ],
    "first_defective_transition": "deterministic phrase matcher miss ('would not restart' \u2260 'would not start'); LLM extractor not invoked (assess_material_account passed no llm)"
  },
  "HOLDOUT_B": {
    "missed": [
      "PAYMENT_MADE",
      "KEYING_ERROR"
    ],
    "classes": [
      "A",
      "B"
    ],
    "first_defective_transition": "deterministic miss on 'Completed payment' / 'keyed plate'; LLM extractor not invoked"
  },
  "HOLDOUT_D": {
    "issue": "uncertainty/negation polarity handling",
    "classes": [
      "E"
    ],
    "first_defective_transition": "attribution/polarity prevented clean scoring under uncertain permit/site"
  }
}
```

Classes: **A** deterministic miss, **B** LLM not invoked, **E** polarity/attribution.

## 2. Concept definitions

- Ontology version: `p10_5_ontology_v1`
- Definitions: 21 concepts with meaning-first text

## 3. LLM extraction changes

- Added `semantic_extraction` prompt v1 (meaning-first, structured JSON).
- `assess_material_account(case, llm=...)` now receives `self.llm`.
- `extract_concepts` is LLM-primary; deterministic helpers merge second.
- `OPENAI_PREFERENCES['semantic_extraction']` registered.

## 4. Deterministic vs LLM responsibility

| Layer | Role |
| --- | --- |
| LLM / Reference meaning bridge | Primary meaning → ontology concepts |
| Deterministic helpers | High-confidence safety / supplement |
| validate_concepts | Drop module ids / illegal polarities |
| concepts_to_intended_facts | Affirmed + confidence ≥ threshold → FactManager |

## 5. Concept→fact promotion

- Affirmed only; UNCERTAIN/NEGATED/THIRD_PARTY not promoted.
- `PROMOTE_MIN_CONFIDENCE = 0.55`.
- Questions still run after promotion for gaps/conflicts.

## 6. Paraphrase test matrix

- DEVELOPMENT cases: 54
- Families cover LEFT_SITE, RETURNED, KEYING_ERROR, BROKEN_DOWN, LOADING/DELIVERY, PERMIT, multi-concept, P10.4 observation classes (reworded, not exact holdout copies).

## 7. Metric-calculation audit

- Aggregate semantic P/R now equals confusion-table totals: `{'tp': 54, 'fp': 0, 'fn': 14}`
- Note: `semantic_concept P/R derived from confusion-table sums so aggregate equals per-concept TP/FP/FN totals`
- Scored concepts: `['BROKEN_DOWN', 'COLLECTION', 'DELIVERY', 'DROP_OFF', 'IMMOBILISED', 'KEYING_ERROR', 'LEFT_SITE', 'LOADING', 'MULTIPLE_VISITS', 'PAYMENT_MADE', 'PERMIT_DISPLAYED', 'PERMIT_HELD', 'REGISTRATION_MISMATCH', 'RETURNED', 'SHOPPING']`
- Unscored concepts: `[]`

## 8. Module-role governance resolution

EVIDENCE_REQUIREMENT defaults can_lead_letter=false; only explicit can_lead_letter=true may override.

| module | role | claim? | lead? | basis |
| --- | --- | --- | --- | --- |
| KB-ANPR-02 | EVIDENCE_REQUIREMENT | True | False | EVIDENCE_REQUIREMENT defaults can_lead_letter=false; no explicit true override |
| KB-ANPR-03 | EVIDENCE_REQUIREMENT | True | False | EVIDENCE_REQUIREMENT defaults can_lead_letter=false; no explicit true override |
| KB-EV-01 | EVIDENCE_REQUIREMENT | True | False | EVIDENCE_REQUIREMENT defaults can_lead_letter=false; no explicit true override |
| KB-POFA-06 | SUBSTANTIVE_GROUND | True | False | SUBSTANTIVE but REVIEW; explicit can_lead_letter=false until activation |
| KB-TIME-01 | EVIDENCE_REQUIREMENT | True | False | EVIDENCE_REQUIREMENT defaults can_lead_letter=false; no explicit true override |

- Evidence modules still leading: **0**

## 9. DEVELOPMENT semantic metrics (ReferenceAnalysisLLM)

- E2E: 77.8%
- Semantic P/R: 100.0% / 79.4%
- Negation / uncertainty / attribution: 98.1% / 96.3% / 100.0%
- Fact-promotion: 100.0%

| concept | tp | fp | fn |
| --- | --- | --- | --- |
| BROKEN_DOWN | 9 | 0 | 0 |
| COLLECTION | 1 | 0 | 2 |
| DELIVERY | 4 | 0 | 0 |
| DROP_OFF | 1 | 0 | 0 |
| IMMOBILISED | 6 | 0 | 0 |
| KEYING_ERROR | 7 | 0 | 4 |
| LEFT_SITE | 7 | 0 | 2 |
| LOADING | 3 | 0 | 0 |
| MULTIPLE_VISITS | 1 | 0 | 2 |
| PAYMENT_MADE | 5 | 0 | 2 |
| PERMIT_DISPLAYED | 1 | 0 | 0 |
| PERMIT_HELD | 2 | 0 | 0 |
| REGISTRATION_MISMATCH | 1 | 0 | 0 |
| RETURNED | 5 | 0 | 2 |
| SHOPPING | 1 | 0 | 0 |

### Live model probe

- Status: `SKIPPED`
- Provider/model: `None` / `None`
- Prompt version: `None`
- Note: DEV/VAL/HOLDOUT pipeline scores use ReferenceAnalysisLLM. Live probe skipped.

## 10. Fresh VALIDATION metrics

- E2E: 70.0%
- Semantic P/R: 100.0% / 73.3%
- Ground P/R: 100.0% / 50.0%
- LEGAL_CONCLUSION independent: 0
- SUPPORTING incorrectly in plan: 0

## 11. New sealed HOLDOUT metrics

- E2E: 100.0%
- Semantic P/R: 100.0% / 100.0%
- Prior P10.4 holdout NOT reused as blind.

| case_id | e2e | first_fail | concepts | grounds |
| --- | --- | --- | --- | --- |
| HOLDOUT_E | PASS | — | `[('BROKEN_DOWN', 'AFFIRMED'), ('IMMOBILISED', 'AFFIRMED')]` | `[]` |
| HOLDOUT_F | PASS | — | `[('PAYMENT_MADE', 'AFFIRMED'), ('KEYING_ERROR', 'AFFIRMED')]` | `['KB-PAY-01', 'KB-KEY-01']` |
| HOLDOUT_G | PASS | — | `[('COLLECTION', 'AFFIRMED')]` | `['KB-ACT-01']` |
| HOLDOUT_H | PASS | — | `[('LEFT_SITE', 'UNCERTAIN')]` | `[]` |

## 12. Clean-upstream draft coverage

- Clean cases: 5
- Draft ground coverage: 20.0%
- Material-fact coverage: N/A
- Unsupported assertion rate: 0.0%

## 13. P8 invariant results

- Passed: **True**
- Checks: `{'Master Case': True, 'FactManager / P8 architecture': True, 'P10.3 semantic + roles': True, 'P10 remediation': True}`

## Recommendation

**DRAFT_MODEL_WORK_REQUIRED**

- ADD_VECTOR_RETRIEVAL: `False`
- Production deploy: forbidden
- Fine-tune: forbidden

Reasons:
- clean-upstream draft ground coverage 20% (tracked separately; semantic holdout clean)
- validation semantic recall 73% still below DEV target on ReferenceAnalysisLLM meaning-bridge (live LLM probe skipped)

---

STOP after evaluation. No pgvector. No fine-tune. No production deploy.
