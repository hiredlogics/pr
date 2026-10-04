# P10 — Model improvement decision

Fine-tuning is **not** justified when remaining errors are state, authority,
deterministic calculation, lineage, persistence, ground merge, validation, or
KB relationships.

It **may** be considered only if stable repeated weaknesses remain in:

- Narrative → controlled facts, or
- Claim Plan → professional prose

after those layers are otherwise correct.

---

## Recommendation

**Fine-tuning: NOT YET**

P10 remaining failures are not model-shaped:

| Remainder | Shape |
| --- | --- |
| Extra `KB-POFA-01/04/05` vs closed goldens (LATE pair, payment_keying) | Published `use_when` / Claim Plan authority. GOLDEN_REVIEW_REQUIRED |
| `REF_stn1947529` empty letter | Support-only pack; no leading ground (state / authority) |
| Validator gaps | Deterministic checks — now 9/9 on DEV mutations |
| ANPR seconds | Schema mapping — fixed |
| Keying fact missing | Ontology CircumstanceRule — fixed |
| Holdout breakdown facts | Narrative → facts, **one** blind case |
| Holdout residential outcome | Outcome/golden; not used for this release |

Do not fine-tune on authority or golden-set tightness. Do not fine-tune on
the blind set.

---

## Candidate tasks

### A. Narrative → controlled facts

| | |
| --- | --- |
| Current metric | DEV narrative P/R 100%. Blind narrative P/R 0% (REG_breakdown only). P9 overall was 66.7% |
| Failure examples | Blind `REG_breakdown` (D10-08). Not inspected for remediation |
| Approved examples | 1 COMPLETE holdout + scenario-suite breakdown. Too few, and holdout is frozen |
| Expected gain | Unknown; one case cannot justify a training set |
| Risk | Teaching case-specific breakdown language; leakage from holdout |
| Appropriate lever | **Not yet.** Collect more DEVELOPMENT-approved narrative goldens in a later dataset version. Prompt/ontology first if the class repeats on DEV |

### B. Claim Plan → professional prose

| | |
| --- | --- |
| Current metric | DEV draft material-fact coverage 85.7%; unsupported assertion 0%; validator 100% |
| Failure examples | LLM path left `{{placeholders}}` and restated PAY+KEY (fixed by DraftContext fill + one-argument merge, not by prompt/model swap) |
| Approved examples | Scenario suite + COMPLETE DEV letters. No client-signed wording set |
| Expected gain | Low for remaining DEV fails (those are golden-set / no-leading-ground) |
| Risk | Prompt churn around one operator’s style; golden drift |
| Appropriate lever | **Prompt/model only if** Claim Plan, SupportBundle, and DraftContext are correct and prose still fails on a **development** class. That bar is not met for a new model or a fine-tune |

---

## Decision

| Question | Answer |
| --- | --- |
| Fine-tune now? | **NO** |
| Fine-tune later? | **NOT YET** — only if a future DEV/VAL cycle shows a stable Narrative→facts or Plan→prose class after ontology and DraftContext are exhausted |
| Prompt change this release? | No. Remaining DEV fails are not prose failures |
| Model replacement this release? | No |

P10 stops here. No production deploy.
