# P8 — Architecture hardening (additive case state)

## WHAT
Remove competing authorities across the case lifecycle so deterministic legal
findings, claim-plan decisions, fact lineage and draft particularisation cannot
be silently overwritten by Case Intelligence selection order, scalar PoFA
collapse, or retract-all material clears.

## WHY
P8.0 audit: independent PoFA / keeper-liability timing can disappear from the
drafting set when CI replaces selection and claim-plan rescue is short-circuited,
or when `pofa_finding = findings[0]` hides a non-first VERIFIED defect. Narrative
compression is a symptom, not the root.

## SCOPE
Architecture only. No operator-specific rules, no PCN-specific logic, no prompt
edits for examples. Sainsbury's-shaped late NTK + narrative is a regression test
only (`tests/test_p8_architecture.py`).

## CHANGES

| ID | Fix | Components |
|----|-----|------------|
| P8.1 | **Master Case Object**: one authoritative case state (notice, customer, derived + lineage, findings, knowledge matches, grounds, claim plan), serialized and reloaded | `case_state.py`, `models.CaseFile.master`, `fact_graph.py`, `knowledge_matcher.py`, `claim_plan_authority.decide`, `store/cases.py`, `infra/migrations/0010_master_case_state.sql` |
| P8.1 | Multi-finding gate authority (`pofa_findings[]` + DSL membership) | `rules/dsl.py`, `legal/findings.py`, `reasoning.py`, `recovery.py` |
| P8.2 | Claim-plan union + invalidation: VF > CF > SELECTED > CANDIDATE; CI is a proposer only; drop only via `GroundInvalidation` | `claim_plan_authority.py` (BUILDER_VERSION=3), `analysis.py` audit contract, `integrity/console.py` GROUND SOURCES, trace UI |
| P8.3 | Document belt before CI: persisted `DocumentBaseline` + `case_analysis_state`; CI is a customer delta, never a rebuild. Digest is canonical (ISO dates, sorted findings) so save/reload/re-establish keeps the same baseline | `document_baseline.py`, `orchestrator.analysis_of`, `store/cases.py`, `0011_document_baseline.sql`, console/trace |
| P8.4 | Versioned fact lifecycle: `apply_fact_delta` replaces wipe-clear; same-value keep; correction supersedes; derived sources restored or marked `UNSUPPORTED_DERIVATION`; provenance + versions persist on audit / fact_history (not underscore answers) | `fact_lifecycle.py`, `engines/account.py`, `fact_graph.py`, `store/cases.py`, console/trace |
| P8.5 | Particularisation contract: every SUPPORTED item carries `SupportBundle` + `DraftRequirement`; DraftContext only from LOCKED plan + bundle + requirement; `VAL-LINEAGE`, `VAL-CLAIM-PLAN-SUPPORT`, `VAL-DRAFT-PARTICULARS` | `drafting/support_contract.py`, `claim_plan_authority.py`, `drafting/context.py`, `draft_validation_engine.py`, console/trace |
| P8.6 | `ModuleDecision` lifecycle KM → applicability → CI → Claim Plan → Draft; joined MODULE JOURNEY; `VAL-MODULE-TRACE` / `VAL-VERIFIED-GROUND-PRESENCE` / `VAL-EXPECTED-REJECTION`; compare-run explanations | `integrity/module_decisions.py`, `integrity/console.py`, `integrity/checks.py`, trace UI |
| P8.7 | Fact authority + `IGNORED_DUPLICATE`; no silent overwrite; provenance additive; derived not re-versioned when sources hold; `VAL-FACT-AUTHORITY` / `VAL-FACT-STABILITY` / `VAL-DERIVED-CONSISTENCY`; FACT WRITE TRACE | `fact_graph.py`, `fact_lifecycle.py`, `case_state.py`, `integrity/checks.py`, console/trace |

## MASTER CASE OBJECT (P8.1)
`pcn_appeal/case_state.py`. `case.master` is the one state object; every stage
reads and writes through it.

| Section | Contents | Where it lives |
|---------|----------|----------------|
| `notice_facts` | operator, PCN, VRM, dates, location, allegation, evidence references | projection over the fact graph |
| `customer_facts` | stated, confirmed, inferred, hypotheses, provenance | projection over the graph, `raw_answers`, `fact_hypotheses`, `free_text_provenance` |
| `derived_facts` | every derived value with `derived_from: [source_fact_ids]`, source documents and the rule | `master_derivations` (append-only) |
| `legal_findings` | calculation, rule, evidence, confidence, status | `legal_findings` (Legal Calculation Engine) |
| `knowledge_matches` | module, relationship, support / block reason | `master_knowledge_matches` (append-only) |
| `grounds` | origin, supporting facts, evidence, findings, required particulars | `master_grounds` (append-only, per plan version) |
| `claim_plan` | final authority | `claim_plans` (LOCKED) |

Guarantees:
* **One authority.** Grounds enter only through `record_grounds(plan)` with a
  CONFIRMED/LOCKED `FinalClaimPlan`; anything else raises `GroundsAuthorityError`.
* **Lineage.** `FactManager` records a derivation for every applied DERIVED
  write; calculators declare their inputs with `case_state.derives(...)` (or the
  `CALCULATOR_INPUTS` table), and `lineage_gaps()` reports what declared nothing.
* **Additive.** The three stored sections are append-only in memory and in the
  database (`master_case_state`, trigger-enforced): a later run adds a
  generation, never edits or drops an earlier one.
* **Serialization.** `to_dict` / `from_dict` / `digest`, round-tripped through
  the real store SQL in `tests/test_master_case_object.py`.

## NOT CHANGING
Prompts, KB module YAML content, operator allowlists, per-case patches.

## TEST PLAN
```bash
python -m unittest tests.test_master_case_object tests.test_p8_architecture tests.test_cumulative_drafting tests.test_verified_legal_findings tests.test_case_console -v
```
