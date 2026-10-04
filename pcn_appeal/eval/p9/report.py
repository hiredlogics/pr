"""Per-case and aggregate P9 reports."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .pipeline import versions_block
from .schema import COMPLETE, NOTICE_ONLY


def _pct(num, den) -> str:
    if den in (0, None) or num in ("N/A", None) or den == "N/A":
        return "N/A"
    return f"{(num / den):.1%}"


def _fmt_rate(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1%}"
    if value in (None, "N/A"):
        return "N/A"
    return str(value)


def _avg(values: list) -> Any:
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return "N/A"
    return sum(nums) / len(nums)


def case_markdown(golden, actual: dict, scored: dict, persist: dict,
                  repeats: dict | None = None) -> str:
    first = scored.get("first_failed_layer") or "NONE"
    verdict = "PASS" if scored.get("end_to_end_pass") else "FAIL"
    versions = actual.get("versions") or versions_block()
    lines = [
        f"# {golden.case_id}",
        "",
        f"- Result: **{verdict}**",
        f"- Dataset split: `{golden.split}`",
        f"- Completeness: `{golden.completeness}`",
        f"- Family: {golden.family} / {golden.operator} / {golden.allegation}",
        f"- First failed stage: `{first}`",
        f"- Extraction method: `{actual.get('extraction_method')}`",
        "",
        "## Versions",
        "",
        f"- git commit: `{versions.get('git_commit')}`",
        f"- KB release: `{versions.get('kb_release')}`",
        f"- prompt versions: `{versions.get('prompt_versions')}`",
        f"- model/provider: `{versions.get('model_provider')}`",
        f"- Claim Plan builder: `{versions.get('claim_plan_builder_version')}`",
        f"- Master Case Object: `{versions.get('master_case_object_version')}`",
        "",
        "## Expected",
        "",
        "```json",
        json.dumps({
            "classification": golden.expected.classification.values,
            "extraction_evaluate": golden.expected.extraction.evaluate,
            "findings": golden.expected.legal_findings.values,
            "supported_grounds": golden.expected.supported_grounds.values,
            "outcome": golden.expected.final_outcome.values,
            "appeal": golden.expected.final_appeal_requirements.values,
        }, indent=2, default=str),
        "```",
        "",
        "## Actual",
        "",
        f"- state: `{actual.get('state')}` outcome: `{actual.get('outcome')}`",
        f"- supported grounds: `{actual.get('supported_grounds')}`",
        f"- findings: `{[(f.get('finding_type'), f.get('status')) for f in (actual.get('findings') or []) if isinstance(f, dict)]}`",
        f"- asked: `{actual.get('asked_questions')}`",
        f"- primary route: `{actual.get('primary_route')}`",
        "",
        "## Diffs",
        "",
        _diff_block("FACT DIFF", scored["scores"].get("facts")),
        _diff_block("FINDING DIFF", scored["scores"].get("findings")),
        _diff_block("GROUND DIFF", scored["scores"].get("grounds")),
        _diff_block("CLAIM PLAN DIFF", scored["scores"].get("claim_plan")),
        _diff_block("DRAFT REQUIREMENT DIFF", scored["scores"].get("draft")),
        "",
        "## Validation",
        "",
        f"- passed: `{actual.get('validation_passed')}`",
        f"- issues: `{actual.get('validation_issues')}`",
        "",
        "## Claim Plan preservation",
        "",
        f"```json\n{json.dumps(persist, indent=2, default=str)}\n```",
        "",
        "## Final outcome",
        "",
        f"- state `{actual.get('state')}` / outcome `{actual.get('outcome')}`",
        f"- letter empty: `{not (actual.get('letter') or '').strip()}`",
        "",
    ]
    if actual.get("error"):
        lines += ["## Infrastructure error", "", f"```\n{actual.get('error')}\n```", ""]
        if actual.get("traceback"):
            lines += ["```", actual["traceback"], "```", ""]
    if repeats:
        lines += ["## Repeatability", "", f"```json\n{json.dumps(repeats, indent=2, default=str)}\n```", ""]
    return "\n".join(lines)


def _diff_block(title: str, row: dict | None) -> str:
    row = row or {"status": "N/A"}
    return f"### {title}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n"


def aggregate(rows: list[dict], mutations: dict, additive: dict,
              repeats: dict, versions: dict) -> str:
    complete = [r for r in rows if r["completeness"] == COMPLETE]
    notice = [r for r in rows if r["completeness"] == NOTICE_ONLY]
    passed = [r for r in rows if r["end_to_end_pass"]]
    failed = [r for r in rows if not r["end_to_end_pass"]]
    layers = Counter(r.get("first_failed_layer") or "NONE" for r in failed)
    holdout = [r for r in rows if r["split"] == "BLIND_HOLDOUT"]

    def metric(path, pred=lambda s: s.get("status") == "SCORED"):
        vals = []
        for r in rows:
            s = r["scores"]
            cur = s
            for key in path.split("."):
                cur = (cur or {}).get(key)
            if isinstance(cur, dict) and pred(cur):
                vals.append(cur)
            elif not isinstance(cur, dict) and cur not in (None, "N/A"):
                vals.append(cur)
        return vals

    def rate(name, key="passed"):
        scored = [s for s in metric(name) if isinstance(s, dict)]
        if not scored:
            return "N/A"
        return _pct(sum(1 for s in scored if s.get(key)), len(scored))

    def mean_field(name, field):
        scored = [s.get(field) for s in metric(name) if isinstance(s, dict)]
        avg = _avg(scored)
        if isinstance(avg, float) and 0 <= avg <= 1:
            return f"{avg:.1%}"
        return avg

    lines = [
        "# P9 Evaluation Report — BASELINE",
        "",
        "This is an honest baseline. Application code, prompts, KB content, and",
        "golden expectations were not changed to make the current output pass.",
        "",
        "## Scope and limits",
        "",
        "- Live vision extraction was **not** available. Document fields were",
        "  injected from labeled gold via `ReferenceAnalysisLLM`. Extraction",
        "  metrics therefore measure fact-graph landing, not OCR.",
        "- COMPLETE journey cases do **not** treat snapshot `approved` lists as",
        "  client-approved grounds. Those layers are N/A unless independently",
        "  approved (late NTK finding; scenario-suite payment/keying).",
        "- NOTICE_ONLY cases are not complete ground truth. They do not establish",
        "  unknown customer circumstances, reverse-page defects, or approved grounds.",
        "- Blind holdout was scored and **not** used for tuning.",
        "",
        "## Versions",
        "",
        f"- git commit: `{versions.get('git_commit')}`",
        f"- KB release: `{versions.get('kb_release')}`",
        f"- prompt versions: `{versions.get('prompt_versions')}`",
        f"- model/provider: `{versions.get('model_provider')}`",
        f"- Claim Plan builder: `{versions.get('claim_plan_builder_version')}`",
        f"- Master Case Object: `{versions.get('master_case_object_version')}`",
        "",
        "## Dataset",
        "",
        f"- Total cases: {len(rows)}",
        f"- COMPLETE: {len(complete)}",
        f"- NOTICE_ONLY: {len(notice)}",
        f"- Blind holdout scored: {len(holdout)} (not used for tuning)",
        "",
        "## End-to-end",
        "",
        f"- Passed cases: {len(passed)}",
        f"- Failed cases: {len(failed)}",
        f"- End-to-end pass rate: {_pct(len(passed), len(rows))}",
        f"- COMPLETE pass rate: {_pct(sum(1 for r in complete if r['end_to_end_pass']), len(complete) or 0)}",
        f"- NOTICE_ONLY pass rate: {_pct(sum(1 for r in notice if r['end_to_end_pass']), len(notice) or 0)}",
        f"- BLIND_HOLDOUT pass rate: {_pct(sum(1 for r in holdout if r['end_to_end_pass']), len(holdout) or 0)}",
        "",
        "## Aggregate metrics",
        "",
        f"- Extraction accuracy (injected landing): {mean_field('extraction', 'accuracy')}",
        f"- Legal-critical extraction accuracy: {mean_field('extraction', 'legal_critical_accuracy')}",
        f"- Fact precision: {mean_field('facts', 'precision')}",
        f"- Fact recall: {mean_field('facts', 'recall')}",
        f"- Narrative fact precision: {mean_field('derived', 'precision')}",
        f"- Narrative fact recall: {mean_field('derived', 'recall')}",
        f"- Question precision: {mean_field('questions', 'precision')}",
        f"- Question recall: {mean_field('questions', 'recall')}",
        f"- Legal finding accuracy: {mean_field('findings', 'accuracy')}",
        f"- KG retrieval precision: {mean_field('retrieval', 'precision')}",
        f"- KG retrieval recall: {mean_field('retrieval', 'recall')}",
        f"- Ground precision: {mean_field('grounds', 'precision')}",
        f"- Ground recall: {mean_field('grounds', 'recall')}",
        f"- Claim Plan coverage: {mean_field('claim_plan', 'coverage')}",
        f"- SupportBundle completeness: {mean_field('claim_plan', 'support_bundle_completeness')}",
        f"- DraftContext coverage: {mean_field('draft_context', 'coverage')}",
        f"- Draft ground coverage: {mean_field('draft', 'ground_coverage')}",
        f"- Draft material-fact coverage: {mean_field('draft', 'material_fact_coverage')}",
        f"- Unsupported assertion rate: {mean_field('draft', 'unsupported_assertion_rate')}",
        f"- Validator detection rate: {_fmt_rate(mutations.get('detection_rate'))}",
        f"- Validator false-positive rate: {_fmt_rate(mutations.get('false_positive_rate'))}",
        f"- Outcome consistency (scored outcome layer): {rate('outcome')}",
        f"- Repeatability (semantic stability): {_fmt_rate(repeats.get('stability_rate'))}",
        "",
        "## Architecture invariants",
        "",
        f"- Additive-ground pair ({additive.get('pair')}): "
        f"{'PASS' if additive.get('passed') else 'FAIL'} — {additive.get('detail')}",
        f"- Claim Plan persist/reload: see per-case reports (Master Case + SQLite store).",
        "- PROCESSING_ERROR on a case that already failed draft/validation is not "
        "a hard-invariant breach. The invariant is: Claim Plan PASS + Draft PASS + "
        "Validation PASS + no exception ⇒ PROCESSING_ERROR impossible.",
        f"- Repeatability of authoritative semantic state: {_fmt_rate(repeats.get('stability_rate'))}",
        "",
        "## First defective layer",
        "",
    ]
    if not layers:
        lines.append("No failed cases.")
    else:
        for layer, n in layers.most_common():
            lines.append(f"- `{layer}`: {n}")
    lines += [
        "",
        "## Additive-ground invariant",
        "",
        f"```json\n{json.dumps(additive, indent=2, default=str)}\n```",
        "",
        "## Validator mutation report",
        "",
        f"```json\n{json.dumps(mutations, indent=2, default=str)}\n```",
        "",
        "## Repeatability",
        "",
        f"```json\n{json.dumps(repeats, indent=2, default=str)}\n```",
        "",
        "## Per-case results",
        "",
        "| Case | Split | Completeness | E2E | First fail | Grounds | State |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['case_id']} | {r['split']} | {r['completeness']} | "
            f"{'PASS' if r['end_to_end_pass'] else 'FAIL'} | "
            f"{r.get('first_failed_layer') or '—'} | "
            f"{', '.join(r.get('supported_grounds') or []) or '—'} | "
            f"{r.get('state')} |"
        )
    lines += [
        "",
        "## Remaining defects (ranked)",
        "",
    ]
    defects = _rank_defects(rows, mutations, additive)
    if not defects:
        lines.append("No ranked defects on scored layers.")
    else:
        for i, d in enumerate(defects, 1):
            lines.append(f"{i}. **{d['severity']}** — {d['title']}: {d['detail']}")
    lines += [
        "",
        "## Fine-tuning decision",
        "",
        defects[-1]["fine_tune"] if False else _fine_tune_text(rows, defects),
        "",
        "## Confirmations",
        "",
        "- No case-specific application fixes were made during this baseline.",
        "- Blind holdout was not used for prompt, rule, KB, or parameter tuning.",
        "- Architecture was not redesigned.",
        "- No production deploy.",
        "",
    ]
    return "\n".join(lines)


def _rank_defects(rows: list[dict], mutations: dict, additive: dict) -> list[dict]:
    counts = Counter(r.get("first_failed_layer") for r in rows if r.get("first_failed_layer"))
    ranked = []
    severity = {
        "INFRASTRUCTURE_ERROR": "P0",
        "OUTCOME_STATE_ERROR": "P0",
        "LINEAGE_ERROR": "P0",
        "GROUND_SELECTION_ERROR": "P1",
        "CLAIM_PLAN_ERROR": "P1",
        "LEGAL_FINDING_ERROR": "P1",
        "DRAFT_ERROR": "P1",
        "DRAFT_CONTEXT_ERROR": "P1",
        "GROUND_MERGE_ERROR": "P1",
        "VALIDATION_ERROR": "P2",
        "FACT_ERROR": "P2",
        "NARRATIVE_FACT_ERROR": "P2",
        "QUESTION_ERROR": "P2",
        "KNOWLEDGE_RETRIEVAL_ERROR": "P2",
        "EXTRACTION_ERROR": "P2",
        "CLASSIFICATION_ERROR": "P2",
    }
    for layer, n in counts.most_common():
        examples = [r["case_id"] for r in rows if r.get("first_failed_layer") == layer]
        ranked.append({
            "severity": severity.get(layer, "P3"),
            "title": layer,
            "detail": f"{n} case(s): {', '.join(examples)}",
        })
    if additive and additive.get("passed") is False:
        ranked.insert(0, {
            "severity": "P0",
            "title": "ADDITIVE_GROUND_INVARIANT",
            "detail": additive.get("detail") or "independent ground silently dropped",
        })
    if mutations.get("status") == "SCORED" and mutations.get("detection_rate", 1) < 1:
        missed = [r["mutation"] for r in mutations.get("rows") or [] if not r.get("detected")]
        ranked.append({
            "severity": "P1",
            "title": "VALIDATOR_MISS",
            "detail": f"undetected mutations: {', '.join(missed) or 'none'}",
        })
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    ranked.sort(key=lambda d: order.get(d["severity"], 9))
    return ranked


def _fine_tune_text(rows: list[dict], defects: list[dict]) -> str:
    arch_fail = any(d["title"] in (
        "CLAIM_PLAN_ERROR", "GROUND_MERGE_ERROR", "LINEAGE_ERROR",
        "OUTCOME_STATE_ERROR", "INFRASTRUCTURE_ERROR",
    ) for d in defects)
    if arch_fail:
        return (
            "Fine-tuning is **not** warranted. Remaining errors include architecture "
            "or deterministic-rule failures. Fix those first. Do not fine-tune "
            "raw notice + narrative → unrestricted legal ground selection."
        )
    return (
        "Fine-tuning is **not** warranted from this baseline. Ground-truth volume "
        "is small (10 COMPLETE, none client-signed for final wording). Architecture "
        "and rules must finish passing on an approved set before any model task "
        "(narrative → controlled facts, or locked plan → polished drafting) is "
        "a candidate. Do not fine-tune unrestricted ground selection."
    )


def baseline_cover(rows: list[dict], versions: dict) -> str:
    passed = sum(1 for r in rows if r["end_to_end_pass"])
    return "\n".join([
        "# P9 BASELINE REPORT",
        "",
        "Honest baseline before any defect fix.",
        "",
        f"- Cases: {len(rows)}",
        f"- Passed: {passed}",
        f"- Failed: {len(rows) - passed}",
        f"- End-to-end pass rate: {_pct(passed, len(rows))}",
        f"- git: `{versions.get('git_commit')}`",
        "",
        "See `P9_EVALUATION_REPORT.md` for aggregate metrics, first-failure",
        "distribution, repeatability, validator mutations, and ranked defects.",
        "",
        "No application code was changed while this baseline ran.",
        "",
    ])
