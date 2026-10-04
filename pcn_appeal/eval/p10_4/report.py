"""Emit P10.4 validation + holdout reports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _pct(v: Any) -> str:
    if v is None or v == "N/A":
        return "N/A"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(v)


def case_markdown(spec: dict, actual: dict, scored: dict) -> str:
    exp = spec.get("expected") or {}
    lines = [
        f"# {spec['case_id']}",
        "",
        f"- Family: `{spec.get('family')}`",
        f"- Split: `{spec.get('split')}`",
        f"- State: `{actual.get('state')}`",
        f"- Outcome: `{actual.get('outcome')}`",
        f"- E2E: {'PASS' if scored.get('end_to_end_pass') else 'FAIL'}"
        f" (first fail: {scored.get('first_failed_layer') or '—'})",
        f"- Supported grounds: `{actual.get('supported_grounds')}`",
        f"- Retrieved: `{actual.get('retrieved_modules')}`",
        "",
        "## Concepts",
        "```json",
        json.dumps(actual.get("concepts") or [], indent=2)[:4000],
        "```",
        "",
        "## Scores",
        "```json",
        json.dumps(scored.get("scores") or {}, indent=2, default=str)[:8000],
        "```",
    ]
    if exp:
        lines += ["", "## Expected keys", f"`{sorted(exp.keys())}`"]
    return "\n".join(lines) + "\n"


def write_validation_report(
    dest: Path,
    freeze: dict,
    role_audit: dict,
    manifest: dict,
    case_rows: list[dict],
    aggregate: dict,
    mutations: dict,
    invariants: dict,
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "freeze": freeze,
        "role_governance": {
            "module_count": role_audit.get("module_count"),
            "by_role": role_audit.get("by_role"),
            "role_review_required_count": role_audit.get("role_review_required_count"),
            "role_review_required": role_audit.get("role_review_required"),
            "policy_note": role_audit.get("policy_note"),
        },
        "manifest": manifest,
        "cases": case_rows,
        "aggregate": aggregate,
        "mutations": mutations,
        "p8_p10_invariants": {
            "passed": invariants.get("passed"),
            "tests_run": invariants.get("tests_run"),
            "checks": invariants.get("checks"),
        },
    }
    (dest / "VALIDATION_METRICS.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    (dest / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, default=str) + "\n", encoding="utf-8")

    confusion = aggregate.get("confusion_by_concept") or {}
    md = [
        "# P10.4 Validation Report",
        "",
        "Frozen P10.3 semantic-layer + ground-role evaluation. "
        "**No code / prompt / KB / golden changes during scoring.**",
        "",
        "## 1. Frozen versions",
        "",
        f"- Evaluation release: `{freeze.get('evaluation_release')}`",
        f"- Git commit: `{freeze.get('git_commit')}`",
        f"- Branch: `{freeze.get('git_branch')}`",
        f"- Dirty tree: `{freeze.get('working_tree_dirty')}`",
        f"- KB release: `{freeze.get('kb_release')}`",
        f"- Semantic ontology: `{freeze.get('semantic_ontology_version')}` "
        f"({freeze.get('semantic_ontology_digest')})",
        f"- Module roles: `{freeze.get('module_role_version')}` "
        f"({freeze.get('module_role_digest')})",
        f"- Claim Plan builder: `{freeze.get('claim_plan_builder_version')}`",
        f"- Model/provider: `{freeze.get('model_provider')}`",
        f"- Prompt versions: `{freeze.get('prompt_versions')}`",
        "",
        "## 2. Role governance audit",
        "",
        role_audit.get("policy_note") or "",
        "",
        f"- Modules: {role_audit.get('module_count')}",
        f"- By role: `{role_audit.get('by_role')}`",
        f"- ROLE_REVIEW_REQUIRED: **{role_audit.get('role_review_required_count')}**",
        f"- Flagged: `{role_audit.get('role_review_required')}`",
        "",
        "See `ROLE_GOVERNANCE_AUDIT.md`.",
        "",
        "## 3. Fresh validation dataset",
        "",
        f"- Dataset: `{manifest.get('dataset_id')}`",
        f"- Cases: {manifest.get('counts', {}).get('validation')}",
        f"- IDs: `{manifest.get('split', {}).get('VALIDATION')}`",
        "",
        "## 4–9. Validation metrics",
        "",
        f"| Metric | Value |",
        f"| --- | --- |",
        f"| E2E pass rate | {_pct(aggregate.get('e2e_pass_rate'))} |",
        f"| Semantic concept P / R | {_pct(aggregate.get('semantic_concept_precision'))} / "
        f"{_pct(aggregate.get('semantic_concept_recall'))} |",
        f"| Narrative fact P / R | {_pct(aggregate.get('narrative_fact_precision'))} / "
        f"{_pct(aggregate.get('narrative_fact_recall'))} |",
        f"| Negation accuracy | {_pct(aggregate.get('negation_accuracy'))} |",
        f"| Uncertainty accuracy | {_pct(aggregate.get('uncertainty_accuracy'))} |",
        f"| Attribution accuracy | {_pct(aggregate.get('attribution_accuracy'))} |",
        f"| Fact-promotion accuracy | {_pct(aggregate.get('fact_promotion_accuracy'))} |",
        f"| Derived-lineage completeness | {_pct(aggregate.get('derived_lineage_completeness'))} |",
        f"| KB candidate P / R | {_pct(aggregate.get('kb_candidate_precision'))} / "
        f"{_pct(aggregate.get('kb_candidate_recall'))} |",
        f"| Role-filter P / R | {_pct(aggregate.get('role_filter_precision'))} / "
        f"{_pct(aggregate.get('role_filter_recall'))} |",
        f"| Ground P / R | {_pct(aggregate.get('ground_precision'))} / "
        f"{_pct(aggregate.get('ground_recall'))} |",
        f"| Orphan-support letters | {aggregate.get('orphan_support_letter_count')} |",
        f"| SUPPORTING incorrectly in Claim Plan | "
        f"{aggregate.get('supporting_incorrectly_reached_claim_plan')} "
        f"(expected {aggregate.get('supporting_incorrect_expected')}) |",
        f"| LEGAL_CONCLUSION independent grounds | "
        f"{aggregate.get('legal_conclusion_independent_grounds')} "
        f"(expected {aggregate.get('legal_conclusion_incorrect_expected')}) |",
        f"| Clean-upstream draft cases | {aggregate.get('clean_upstream_draft_cases')} |",
        f"| Draft ground coverage | {_pct(aggregate.get('draft_ground_coverage'))} |",
        f"| Material-fact coverage | {_pct(aggregate.get('material_fact_coverage'))} |",
        f"| Required-particular coverage | {_pct(aggregate.get('required_particular_coverage'))} |",
        f"| Unsupported assertion rate | {_pct(aggregate.get('unsupported_assertion_rate'))} |",
        "",
        "## 5. Semantic concept confusion",
        "",
        "| concept | tp | fp | fn |",
        "| --- | --- | --- | --- |",
    ]
    for concept, row in sorted(confusion.items()):
        md.append(
            f"| {concept} | {row.get('tp', 0)} | {row.get('fp', 0)} | {row.get('fn', 0)} |"
        )
    if not confusion:
        md.append("| — | 0 | 0 | 0 |")

    md += [
        "",
        "## 10. Validator mutation results",
        "",
        f"- Status: `{mutations.get('status')}`",
        f"- Detection rate: {_pct(mutations.get('detection_rate'))} "
        f"(expected 100%)",
        f"- False-positive rate: {_pct(mutations.get('false_positive_rate'))} "
        f"(expected 0%)",
        f"- Passed: **{mutations.get('passed')}**",
        "",
        "## 11. P8/P10 invariant results",
        "",
        f"- Passed: **{invariants.get('passed')}**",
        f"- Tests run: {invariants.get('tests_run')}",
        f"- Checks: `{invariants.get('checks')}`",
        "",
        "## Per-case first-fail",
        "",
        "| case_id | family | e2e | first_fail | grounds |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in case_rows:
        md.append(
            f"| {row.get('case_id')} | {row.get('family')} | "
            f"{'PASS' if row.get('end_to_end_pass') else 'FAIL'} | "
            f"{row.get('first_failed_layer') or '—'} | "
            f"`{row.get('supported_grounds')}` |"
        )

    path = dest / "VALIDATION_REPORT.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return path


def write_holdout_report(dest: Path, freeze: dict, case_rows: list[dict],
                         aggregate: dict) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "freeze": {
            "git_commit": freeze.get("git_commit"),
            "evaluation_release": freeze.get("evaluation_release"),
            "kb_release": freeze.get("kb_release"),
        },
        "note": "Sealed holdout run once after validation finalization. No post-holdout code changes.",
        "cases": case_rows,
        "aggregate": aggregate,
    }
    (dest / "HOLDOUT_METRICS.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    md = [
        "# P10.4 Sealed Holdout Report",
        "",
        "Run once after validation. Holdout JSON expected blocks were not edited; "
        "labels applied from `pcn_appeal/eval/p10_4/holdout_labels.py`.",
        "",
        f"- Git commit: `{freeze.get('git_commit')}`",
        f"- E2E pass rate: {_pct(aggregate.get('e2e_pass_rate'))}",
        f"- Semantic P/R: {_pct(aggregate.get('semantic_concept_precision'))} / "
        f"{_pct(aggregate.get('semantic_concept_recall'))}",
        f"- Ground P/R: {_pct(aggregate.get('ground_precision'))} / "
        f"{_pct(aggregate.get('ground_recall'))}",
        f"- LEGAL_CONCLUSION independent: "
        f"{aggregate.get('legal_conclusion_independent_grounds')}",
        f"- SUPPORTING incorrectly in plan: "
        f"{aggregate.get('supporting_incorrectly_reached_claim_plan')}",
        "",
        "| case_id | family | e2e | first_fail | grounds |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in case_rows:
        md.append(
            f"| {row.get('case_id')} | {row.get('family')} | "
            f"{'PASS' if row.get('end_to_end_pass') else 'FAIL'} | "
            f"{row.get('first_failed_layer') or '—'} | "
            f"`{row.get('supported_grounds')}` |"
        )
    path = dest / "HOLDOUT_REPORT.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return path


def recommendation(aggregate: dict, holdout_agg: dict, mutations: dict,
                   invariants: dict, role_audit: dict) -> dict:
    """Decide readiness without deploying."""
    reasons = []
    legal_bad = (aggregate.get("legal_conclusion_independent_grounds") or 0) + (
        holdout_agg.get("legal_conclusion_independent_grounds") or 0)
    support_bad = (aggregate.get("supporting_incorrectly_reached_claim_plan") or 0) + (
        holdout_agg.get("supporting_incorrectly_reached_claim_plan") or 0)
    e2e = aggregate.get("e2e_pass_rate")
    hold_e2e = holdout_agg.get("e2e_pass_rate")
    hold_sem_r = holdout_agg.get("semantic_concept_recall")
    ground_p = aggregate.get("ground_precision")
    cand_r = aggregate.get("kb_candidate_recall")
    sem_p = aggregate.get("semantic_concept_precision")
    draft_cov = aggregate.get("draft_ground_coverage")
    review_n = role_audit.get("role_review_required_count") or 0

    if not invariants.get("passed"):
        reasons.append("P8/P10 invariant suite failed")
    if mutations.get("status") == "SCORED" and not mutations.get("passed"):
        reasons.append("validator mutation suite below 100% detection / 0% FP")
    if legal_bad or support_bad:
        reasons.append("role/ground model leak (LEGAL_CONCLUSION or SUPPORTING in plan)")
    if isinstance(e2e, float) and e2e < 0.8:
        reasons.append("validation E2E pass rate < 80%")
    if isinstance(hold_e2e, float) and hold_e2e < 0.75:
        reasons.append(
            f"sealed holdout E2E {hold_e2e:.0%} - wording/pattern generalisation gap")
    if isinstance(hold_sem_r, float) and hold_sem_r < 0.75:
        reasons.append(f"sealed holdout semantic recall {hold_sem_r:.0%}")
    if isinstance(sem_p, float) and sem_p < 0.85:
        reasons.append("semantic precision soft")
    if review_n:
        reasons.append(f"{review_n} modules ROLE_REVIEW_REQUIRED (evidence lead inheritance)")

    # pgvector only if precision good but recall of candidates materially low
    add_vector = False
    if (isinstance(sem_p, float) and sem_p >= 0.85
            and (ground_p == "N/A" or (isinstance(ground_p, float) and ground_p >= 0.8))
            and isinstance(cand_r, float) and cand_r < 0.6
            and isinstance(hold_sem_r, float) and hold_sem_r >= 0.75):
        add_vector = True
        reasons.append("candidate recall low while precision acceptable → vector retrieval candidate")

    holdout_semantic_gap = (
        (isinstance(hold_e2e, float) and hold_e2e < 0.75)
        or (isinstance(hold_sem_r, float) and hold_sem_r < 0.75)
    )

    if legal_bad or support_bad or (
            isinstance(ground_p, float) and ground_p < 0.8):
        primary = "MORE_GROUND_MODEL_WORK"
    elif holdout_semantic_gap or (isinstance(sem_p, float) and sem_p < 0.85):
        primary = "MORE_SEMANTIC_WORK"
    elif add_vector:
        primary = "ADD_VECTOR_RETRIEVAL"
    elif isinstance(draft_cov, float) and draft_cov < 0.7 and (
            isinstance(e2e, float) and e2e >= 0.8):
        primary = "DRAFT_MODEL_WORK_REQUIRED"
    elif (invariants.get("passed") and mutations.get("passed")
          and isinstance(e2e, float) and e2e >= 0.9
          and legal_bad == 0 and support_bad == 0
          and not holdout_semantic_gap):
        primary = "READY_FOR_STAGING"
        if review_n:
            reasons.append("staging OK with ROLE_REVIEW_REQUIRED follow-up (no deploy)")
    else:
        primary = "MORE_GROUND_MODEL_WORK" if (legal_bad or support_bad) else "MORE_SEMANTIC_WORK"

    return {
        "recommendation": primary,
        "add_vector_retrieval": add_vector,
        "reasons": reasons,
        "do_not_production_deploy": True,
        "do_not_fine_tune": True,
    }


def write_final_report(
    dest: Path,
    freeze: dict,
    role_audit: dict,
    manifest: dict,
    val_aggregate: dict,
    holdout_aggregate: dict,
    mutations: dict,
    invariants: dict,
    reco: dict,
    first_defective: list[str],
    val_rows: list[dict],
    holdout_rows: list[dict],
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    confusion = val_aggregate.get("confusion_by_concept") or {}
    md = [
        "# P10.4 — Fresh Validation + Sealed Holdout Evaluation",
        "",
        "Evaluation of the **frozen** P10.3 semantic-layer + ground-role architecture.",
        "No behavioural code changes, prompt edits, KB edits, golden edits, pgvector,",
        "fine-tuning, or holdout-tuned patches were applied during scoring.",
        "",
        "## 1. Frozen versions",
        "",
        "```json",
        json.dumps({k: freeze[k] for k in freeze if k != "freeze_note"}, indent=2)[:4000],
        "```",
        "",
        f"_Note: {freeze.get('freeze_note')}_",
        "",
        "## 2. Role-governance audit",
        "",
        role_audit.get("policy_note") or "",
        "",
        f"- ROLE_REVIEW_REQUIRED count: **{role_audit.get('role_review_required_count')}**",
        f"- Flagged modules: `{role_audit.get('role_review_required')}`",
        f"- By role: `{role_audit.get('by_role')}`",
        "",
        "Full table: `ROLE_GOVERNANCE_AUDIT.md`.",
        "",
        "## 3. Fresh validation dataset manifest",
        "",
        f"- `{manifest.get('dataset_id')}` — "
        f"{manifest.get('counts', {}).get('validation')} cases",
        f"- Families: `{manifest.get('families')}`",
        "",
        "## 4. Validation metrics",
        "",
        f"- E2E: {_pct(val_aggregate.get('e2e_pass_rate'))}",
        f"- Semantic P/R: {_pct(val_aggregate.get('semantic_concept_precision'))} / "
        f"{_pct(val_aggregate.get('semantic_concept_recall'))}",
        f"- Narrative fact P/R: {_pct(val_aggregate.get('narrative_fact_precision'))} / "
        f"{_pct(val_aggregate.get('narrative_fact_recall'))}",
        f"- Negation / uncertainty / attribution: "
        f"{_pct(val_aggregate.get('negation_accuracy'))} / "
        f"{_pct(val_aggregate.get('uncertainty_accuracy'))} / "
        f"{_pct(val_aggregate.get('attribution_accuracy'))}",
        f"- Fact-promotion / lineage: "
        f"{_pct(val_aggregate.get('fact_promotion_accuracy'))} / "
        f"{_pct(val_aggregate.get('derived_lineage_completeness'))}",
        "",
        "## 5. Semantic concept confusion table",
        "",
        "| concept | tp | fp | fn |",
        "| --- | --- | --- | --- |",
    ]
    for concept, row in sorted(confusion.items()):
        md.append(
            f"| {concept} | {row.get('tp', 0)} | {row.get('fp', 0)} | {row.get('fn', 0)} |"
        )
    if not confusion:
        md.append("| — | 0 | 0 | 0 |")

    md += [
        "",
        "## 6. KB candidate metrics",
        "",
        f"- Precision: {_pct(val_aggregate.get('kb_candidate_precision'))}",
        f"- Recall: {_pct(val_aggregate.get('kb_candidate_recall'))}",
        f"- Role-filter P/R: {_pct(val_aggregate.get('role_filter_precision'))} / "
        f"{_pct(val_aggregate.get('role_filter_recall'))}",
        "",
        "## 7. Ground precision / recall",
        "",
        f"- Precision: {_pct(val_aggregate.get('ground_precision'))}",
        f"- Recall: {_pct(val_aggregate.get('ground_recall'))}",
        "",
        "## 8. Orphan-support results",
        "",
        f"- Orphan-support letters: {val_aggregate.get('orphan_support_letter_count')}",
        f"- SUPPORTING incorrectly reached Claim Plan: "
        f"**{val_aggregate.get('supporting_incorrectly_reached_claim_plan')}** "
        f"(expected 0)",
        f"- LEGAL_CONCLUSION independent grounds: "
        f"**{val_aggregate.get('legal_conclusion_independent_grounds')}** "
        f"(expected 0)",
        "",
        "## 9. Drafting metrics (clean upstream subset)",
        "",
        f"- Clean cases: {val_aggregate.get('clean_upstream_draft_cases')}",
        f"- Ground coverage: {_pct(val_aggregate.get('draft_ground_coverage'))}",
        f"- Material-fact coverage: {_pct(val_aggregate.get('material_fact_coverage'))}",
        f"- Required-particular coverage: "
        f"{_pct(val_aggregate.get('required_particular_coverage'))}",
        f"- Unsupported assertion rate: "
        f"{_pct(val_aggregate.get('unsupported_assertion_rate'))}",
        "",
        "## 10. Validator mutation results",
        "",
        f"- Detection: {_pct(mutations.get('detection_rate'))} "
        f"(P9 classes {_pct(mutations.get('p9_detection_rate'))}; "
        f"extras {_pct(mutations.get('extra_detection_rate'))})",
        f"- False positives: {_pct(mutations.get('false_positive_rate'))}",
        f"- Passed: **{mutations.get('passed')}**",
        "",
        "## 11. P8/P10 invariant results",
        "",
        f"- Passed: **{invariants.get('passed')}**",
        f"- `{invariants.get('checks')}`",
        "",
        "## 12. Sealed holdout results",
        "",
        f"- E2E: {_pct(holdout_aggregate.get('e2e_pass_rate'))}",
        f"- Semantic P/R: {_pct(holdout_aggregate.get('semantic_concept_precision'))} / "
        f"{_pct(holdout_aggregate.get('semantic_concept_recall'))}",
        f"- Ground P/R: {_pct(holdout_aggregate.get('ground_precision'))} / "
        f"{_pct(holdout_aggregate.get('ground_recall'))}",
        f"- LEGAL_CONCLUSION independent: "
        f"{holdout_aggregate.get('legal_conclusion_independent_grounds')}",
        f"- SUPPORTING incorrectly in plan: "
        f"{holdout_aggregate.get('supporting_incorrectly_reached_claim_plan')}",
        "",
        "| case_id | e2e | first_fail | grounds |",
        "| --- | --- | --- | --- |",
    ]
    for row in holdout_rows:
        md.append(
            f"| {row.get('case_id')} | "
            f"{'PASS' if row.get('end_to_end_pass') else 'FAIL'} | "
            f"{row.get('first_failed_layer') or '—'} | "
            f"`{row.get('supported_grounds')}` |"
        )

    md += [
        "",
        "## 13. Remaining first-defective layers",
        "",
    ]
    if first_defective:
        for item in first_defective:
            md.append(f"- {item}")
    else:
        md.append("- None observed on validation + holdout under current gold.")

    md += [
        "",
        "## 14. Recommendation",
        "",
        f"**{reco.get('recommendation')}**",
        "",
        f"- ADD_VECTOR_RETRIEVAL: `{reco.get('add_vector_retrieval')}`",
        f"- Production deploy: **forbidden** (`do_not_production_deploy="
        f"{reco.get('do_not_production_deploy')}`)",
        f"- Fine-tune: **forbidden** (`do_not_fine_tune={reco.get('do_not_fine_tune')}`)",
        "",
        "Reasons:",
    ]
    for r in reco.get("reasons") or ["—"]:
        md.append(f"- {r}")
    md += [
        "",
        "---",
        "",
        "STOP after report. No post-evaluation code changes in this release.",
    ]

    path = dest / "P10_4_EVALUATION_REPORT.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    (dest / "RECOMMENDATION.json").write_text(
        json.dumps(reco, indent=2) + "\n", encoding="utf-8")
    return path
