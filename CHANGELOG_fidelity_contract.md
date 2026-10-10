# Drafting fidelity contract

Problem: correct facts + correct supported grounds -> the model sometimes adds, changes,
exaggerates, omits or misattributes meaning. Fixed once, at the generic drafting-contract
level. Nothing here names a case, operator, place, ground or wording.

## What already held (unchanged)

The locked Claim Plan decides the grounds (VAL-PLAN); the drafter is a renderer fed one
`DraftContext` package (verified facts + provenance, evidence, approved wording, prohibited
claims, no raw customer text); every sentence needs a fact, ground or evidence ref
(VAL-GROUND / VAL-FACT); driver, PoFA, Code, Equality, evidence, stage and leak checks stay
as they were. None of those was weakened.

## What was missing, and what now covers it

| Gap | Change |
| --- | --- |
| A weaker fact restated as a stronger one passed every validator (driver unidentified -> "refuses to identify", attempt -> "payment made", receipt -> "authorised", ...) | `engines/assertion_strength.py` + rule **VAL-STRENGTH** (VAL-5 -> VAL-6). A table of *claim kinds*, each licensed only by a verified fact or by the approved wording itself. A denial or put-to-proof ("does not establish that...") is not an assertion. `register()` adds a claim kind for a future ground without new logic. |
| An approved conclusion could be widened ("not established" -> "unlawful") | Same table: conclusion and intention terms must appear in the approved wording the pack carries. |
| The drafting prompt had per-topic rules but no single contract | Drafting prompt **v20**: one FIDELITY CONTRACT (case package is the only source; never strengthen or weaken a fact; never widen a conclusion; be specific to this charge; a retry rewrites the same case). |
| A retry could, in principle, build a new case theory | `rewrite_contract` is sent with any retry: same facts, same plan, same wording; the section/ground set is named and locked; only the wording the feedback names may change. Retried payloads are identical to the first attempt apart from the feedback and the contract. |
| The quality judge was off by default and recorded verdicts it never acted on | Quality judge **v2** enforces: eight HARD findings (unsupported, ungrounded argument, changed conclusion, certainty inflation, strengthened fact, misattributed statement, removed supported fact, weakened customer fact) plus "generic" and "does not say why". A REWRITE goes back to the drafter over the same case (max 2, not counted against validation attempts); a hard finding that survives holds release as **VAL-QUALITY**; a score short of its floor with no hard finding is rewritten but never withheld; an unreachable judge never blocks and is audited. |

## Defaults and switches

- Judge **on** for a live provider (OpenAI / Groq / fallback), **off** for test doubles.
  `QUALITY_JUDGE=1|0` overrides; `AppealPipeline(quality_judge_enabled=...)` overrides both.
- **Enforcement is off by default** (`QUALITY_JUDGE_ENFORCE=1` turns it on). Live, it held 4 of 5 sound letters (airport drop-off, driver letter) on false "hard findings"; the judge still scores and records every letter, and the deterministic validators alone decide release.
- Costs one extra (mini-model) call per draft that passes validation, plus up to two rewrites.

## Tests

`tests/test_fidelity_contract.py` (new): guards over unrelated operators/places/wordings,
licensing by fact and by wording, denials not blocked, registry extension, the judge's
hard-finding gate, rewrite contract, the orchestrator loop (rewrite -> release, rewrite
exhausted -> hold, soft score -> release, judge outage -> release, enforce off), and
three unseen breakdown cases through the real pipeline.
`tests/test_prompts.py` / `tests/test_appeal_quality_judge.py` updated for prompt v20 / v2 and
for the judge now enforcing (it used to assert it "never blocks a release yet").

## Not changed

Which *additional* grounds the analysis proposes can still depend on how an allegation is
phrased (a records request appeared for one wording of "stayed beyond the permitted period"
and not another, in the reference stand-in). That choice is made upstream of drafting.
