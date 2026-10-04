"""python -m pcn_appeal.eval.p10_5

P10.5 semantic generalization evaluation:
  freeze → role governance → DEV semantics → VALIDATION → sealed HOLDOUT once.

Distinguishes ReferenceAnalysisLLM (test double) from live OpenAI when available.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from pcn_appeal.eval.p10_4.freeze import write_freeze
from pcn_appeal.eval.p10_4.invariants import run_p8_p10_invariants
from pcn_appeal.eval.p10_4.metrics import aggregate_metrics, score_case
from pcn_appeal.eval.p10_4.pipeline import load_json_cases, persist_reload_check, run_once
from pcn_appeal.eval.p10_4.report import recommendation
from pcn_appeal.module_roles import (
    EVIDENCE_REQUIREMENT, EXPLICIT_CAN_LEAD, MODULE_ROLE_VERSION,
    can_lead_letter, governance_basis, role_of,
)
from pcn_appeal.semantics.ontology import CONCEPT_DEFINITIONS, ONTOLOGY_VERSION

from .holdout_labels import HOLDOUT_LABELS

REPORT = ROOT / "reports" / "p10_5"
DS = ROOT / "datasets" / "p10_5_v1"


ROOT_CAUSE = {
    "evidence": {
        "p10_4_validation_e2e": 1.0,
        "p10_4_holdout_e2e": 0.25,
        "p10_4_holdout_semantic_precision": 1.0,
        "p10_4_holdout_semantic_recall": 0.333,
        "first_defective_layer": "SEMANTIC x3",
    },
    "trace": [
        "raw narrative",
        "deterministic patterns (primary in P10.4)",
        "optional LLM semantic_extraction (NOT INVOKED — llm never passed)",
        "validate_concepts",
        "concept promotion",
        "FactManager",
    ],
    "holdout_classifications": {
        "HOLDOUT_A": {
            "missed": ["BROKEN_DOWN", "IMMOBILISED"],
            "classes": ["A", "B"],
            "first_defective_transition": (
                "deterministic phrase matcher miss "
                "('would not restart' ≠ 'would not start'); "
                "LLM extractor not invoked (assess_material_account passed no llm)"
            ),
        },
        "HOLDOUT_B": {
            "missed": ["PAYMENT_MADE", "KEYING_ERROR"],
            "classes": ["A", "B"],
            "first_defective_transition": (
                "deterministic miss on 'Completed payment' / 'keyed plate'; "
                "LLM extractor not invoked"
            ),
        },
        "HOLDOUT_D": {
            "issue": "uncertainty/negation polarity handling",
            "classes": ["E"],
            "first_defective_transition": (
                "attribution/polarity prevented clean scoring under uncertain permit/site"
            ),
        },
    },
    "summary": (
        "Precision strong, recall weak because meaning extraction depended on "
        "exact phrase matchers and the LLM semantic path was never called."
    ),
}


def _write_role_governance(dest: Path) -> dict:
    from pcn_appeal.kg.graph import KnowledgeGraph
    kg = KnowledgeGraph()
    rows = []
    for mid in sorted(kg.modules):
        mod = kg.modules[mid]
        g = governance_basis(mod)
        g["status"] = mod.status
        g["strength"] = mod.strength
        rows.append(g)
    flagged = [r for r in rows if r["module_role"] == EVIDENCE_REQUIREMENT
               and r["can_lead_letter"]]
    review = [r for r in rows if r["module_id"] in (
        "KB-ANPR-02", "KB-ANPR-03", "KB-EV-01", "KB-POFA-06", "KB-TIME-01")]
    summary = {
        "module_role_version": MODULE_ROLE_VERSION,
        "policy": (
            "EVIDENCE_REQUIREMENT defaults can_lead_letter=false; "
            "only explicit can_lead_letter=true may override."
        ),
        "resolved_review_modules": review,
        "evidence_still_leading_count": len(flagged),
        "explicit_map": EXPLICIT_CAN_LEAD,
        "rows": rows,
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "ROLE_GOVERNANCE.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _score_spec(spec: dict, actual: dict) -> tuple[dict, dict, dict]:
    expected = dict(spec.get("expected") or {})
    if spec.get("sealed") or spec.get("case_id") in HOLDOUT_LABELS:
        labels = HOLDOUT_LABELS.get(spec["case_id"]) or {}
        merged = dict(labels)
        for k, v in expected.items():
            if k not in ("sealed", "note"):
                merged.setdefault(k, v)
        expected = merged
        spec = dict(spec)
        if labels.get("family"):
            spec["family"] = labels["family"]
    return score_case(expected, actual), expected, spec


def _run_split(cases: list[dict], dest: Path) -> tuple[list[dict], dict]:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "cases").mkdir(exist_ok=True)
    rows = []
    for spec in cases:
        print(f"  run {spec['case_id']} ...", flush=True)
        result = run_once(spec)
        case_obj = result.pop("_case", None)
        result.pop("_out", None)
        result.pop("_pipe", None)
        persist = {"status": "N/A"}
        if case_obj is not None and spec.get("split") != "HOLDOUT":
            persist = persist_reload_check(case_obj)
        result["persist"] = persist
        scored, expected, spec2 = _score_spec(spec, result)
        row = {
            "case_id": spec2["case_id"],
            "split": spec2.get("split"),
            "family": spec2.get("family"),
            "end_to_end_pass": scored["end_to_end_pass"],
            "first_failed_layer": scored["first_failed_layer"],
            "supported_grounds": result.get("supported_grounds") or [],
            "concepts": [
                (c.get("concept"), c.get("polarity"))
                for c in (result.get("concepts") or [])
            ],
            "state": result.get("state"),
            "scores": scored["scores"],
        }
        rows.append(row)
        (dest / "cases" / f"{spec['case_id']}.json").write_text(
            json.dumps({
                "spec": spec2, "expected_scored": expected,
                "actual": {k: v for k, v in result.items() if not k.startswith("_")},
                "scores": scored,
            }, indent=2, default=str) + "\n", encoding="utf-8")
    agg = aggregate_metrics([
        {"end_to_end_pass": r["end_to_end_pass"], "scores": r["scores"]}
        for r in rows
    ])
    return rows, agg


def _live_semantic_probe() -> dict:
    """Run a few paraphrases on the live provider if OPENAI_API_KEY is set."""
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    probes = [
        "After stopping briefly the car failed to restart; recovery was called.",
        "Finished paying in the operator app; one character on the plate was wrong.",
        "I departed the location for a while then came back.",
        "I did not leave the site at any point.",
        "Possibly the vehicle lost power; I'm not sure.",
    ]
    if not key:
        return {
            "status": "SKIPPED",
            "reason": "OPENAI_API_KEY not set",
            "reference_model": "ReferenceAnalysisLLM + meaning_bridge",
            "note": (
                "DEV/VAL/HOLDOUT pipeline scores use ReferenceAnalysisLLM. "
                "Live probe skipped."
            ),
        }
    try:
        from pcn_appeal.llm import OpenAIClient
        from pcn_appeal import prompts
        from pcn_appeal.semantics import extract_concepts
        client = OpenAIClient(api_key=key)
        model = client.models.get("semantic_extraction")
        rows = []
        for text in probes:
            concepts = extract_concepts([text], llm=client)
            rows.append({
                "text": text,
                "concepts": [c.as_dict() for c in concepts],
            })
        return {
            "status": "SCORED",
            "provider": "openai",
            "model": model,
            "prompt_version": prompts.version("semantic_extraction"),
            "ontology_version": ONTOLOGY_VERSION,
            "temperature": "default (API json_object)",
            "probes": rows,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "reference_model": "ReferenceAnalysisLLM + meaning_bridge",
        }


def _write_report(freeze, roles, manifest, root_cause, live,
                  dev_rows, dev_agg, val_rows, val_agg,
                  hold_rows, hold_agg, invariants) -> Path:
    confusion = dev_agg.get("confusion_by_concept") or {}
    totals = dev_agg.get("semantic_confusion_totals") or {}
    reco = recommendation(val_agg, hold_agg, {"passed": True, "status": "N/A"},
                          invariants, {
                              "role_review_required_count": roles.get(
                                  "evidence_still_leading_count", 0),
                          })
    # P10.5 primary gate: sealed holdout semantic P/R + precision preserved.
    hold_r = hold_agg.get("semantic_concept_recall")
    hold_p = hold_agg.get("semantic_concept_precision")
    hold_e2e = hold_agg.get("e2e_pass_rate")
    val_r = val_agg.get("semantic_concept_recall")
    draft_cov = val_agg.get("draft_ground_coverage")
    reasons = []
    if isinstance(hold_p, float) and hold_p < 0.95:
        reasons.append("holdout semantic precision soft")
    if isinstance(hold_r, float) and hold_r < 0.85:
        reasons.append(f"sealed holdout semantic recall {hold_r:.0%}")
    if isinstance(val_r, float) and val_r < 0.7:
        reasons.append(f"validation semantic recall {val_r:.0%} (reference bridge)")
    if roles.get("evidence_still_leading_count"):
        reasons.append("evidence modules still leading")

    holdout_semantic_ok = (
        isinstance(hold_e2e, float) and hold_e2e >= 0.9
        and isinstance(hold_r, float) and hold_r >= 0.85
        and isinstance(hold_p, float) and hold_p >= 0.95
    )
    if not holdout_semantic_ok:
        reco["recommendation"] = "MORE_SEMANTIC_WORK"
    elif isinstance(draft_cov, float) and draft_cov < 0.75:
        reco["recommendation"] = "DRAFT_MODEL_WORK_REQUIRED"
        reasons.append(
            f"clean-upstream draft ground coverage {draft_cov:.0%} "
            f"(tracked separately; semantic holdout clean)"
        )
    else:
        reco["recommendation"] = "READY_FOR_STAGING"
    if isinstance(val_r, float) and val_r < 0.85:
        reasons.append(
            f"validation semantic recall {val_r:.0%} still below DEV target "
            f"on ReferenceAnalysisLLM meaning-bridge (live LLM probe "
            f"{'ran' if live.get('status') == 'SCORED' else 'skipped'})"
        )
    reco["reasons"] = reasons or ["holdout semantic gate passed"]
    reco["add_vector_retrieval"] = False
    reco["do_not_production_deploy"] = True
    reco["do_not_fine_tune"] = True

    md = [
        "# P10.5 — Semantic Generalization Hardening",
        "",
        "Improve semantic extraction generalization. No P8 redesign, no Claim Plan",
        "authority change, no pgvector, no fine-tune, no production deploy.",
        "",
        "## 1. Semantic extraction root-cause analysis",
        "",
        f"- Summary: {root_cause['summary']}",
        f"- Trace: `{root_cause['trace']}`",
        f"- P10.4 holdout classifications:",
        "```json",
        json.dumps(root_cause["holdout_classifications"], indent=2),
        "```",
        "",
        "Classes: **A** deterministic miss, **B** LLM not invoked, **E** polarity/attribution.",
        "",
        "## 2. Concept definitions",
        "",
        f"- Ontology version: `{ONTOLOGY_VERSION}`",
        f"- Definitions: {len(CONCEPT_DEFINITIONS)} concepts with meaning-first text",
        "",
        "## 3. LLM extraction changes",
        "",
        "- Added `semantic_extraction` prompt v1 (meaning-first, structured JSON).",
        "- `assess_material_account(case, llm=...)` now receives `self.llm`.",
        "- `extract_concepts` is LLM-primary; deterministic helpers merge second.",
        "- `OPENAI_PREFERENCES['semantic_extraction']` registered.",
        "",
        "## 4. Deterministic vs LLM responsibility",
        "",
        "| Layer | Role |",
        "| --- | --- |",
        "| LLM / Reference meaning bridge | Primary meaning → ontology concepts |",
        "| Deterministic helpers | High-confidence safety / supplement |",
        "| validate_concepts | Drop module ids / illegal polarities |",
        "| concepts_to_intended_facts | Affirmed + confidence ≥ threshold → FactManager |",
        "",
        "## 5. Concept→fact promotion",
        "",
        "- Affirmed only; UNCERTAIN/NEGATED/THIRD_PARTY not promoted.",
        "- `PROMOTE_MIN_CONFIDENCE = 0.55`.",
        "- Questions still run after promotion for gaps/conflicts.",
        "",
        "## 6. Paraphrase test matrix",
        "",
        f"- DEVELOPMENT cases: {manifest['counts']['development']}",
        f"- Families cover LEFT_SITE, RETURNED, KEYING_ERROR, BROKEN_DOWN, "
        f"LOADING/DELIVERY, PERMIT, multi-concept, P10.4 observation classes "
        f"(reworded, not exact holdout copies).",
        "",
        "## 7. Metric-calculation audit",
        "",
        f"- Aggregate semantic P/R now equals confusion-table totals: `{totals}`",
        f"- Note: `{dev_agg.get('metric_note')}`",
        f"- Scored concepts: `{dev_agg.get('scored_concepts')}`",
        f"- Unscored concepts: `{dev_agg.get('unscored_concepts')}`",
        "",
        "## 8. Module-role governance resolution",
        "",
        roles.get("policy") or "",
        "",
        "| module | role | claim? | lead? | basis |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in roles.get("resolved_review_modules") or []:
        md.append(
            f"| {r['module_id']} | {r['module_role']} | {r['can_enter_claim_plan']} | "
            f"{r['can_lead_letter']} | {r['governance_basis']} |"
        )
    md += [
        "",
        f"- Evidence modules still leading: **{roles.get('evidence_still_leading_count')}**",
        "",
        "## 9. DEVELOPMENT semantic metrics (ReferenceAnalysisLLM)",
        "",
        f"- E2E: {_pct(dev_agg.get('e2e_pass_rate'))}",
        f"- Semantic P/R: {_pct(dev_agg.get('semantic_concept_precision'))} / "
        f"{_pct(dev_agg.get('semantic_concept_recall'))}",
        f"- Negation / uncertainty / attribution: "
        f"{_pct(dev_agg.get('negation_accuracy'))} / "
        f"{_pct(dev_agg.get('uncertainty_accuracy'))} / "
        f"{_pct(dev_agg.get('attribution_accuracy'))}",
        f"- Fact-promotion: {_pct(dev_agg.get('fact_promotion_accuracy'))}",
        "",
        "| concept | tp | fp | fn |",
        "| --- | --- | --- | --- |",
    ]
    for c, row in sorted(confusion.items()):
        md.append(f"| {c} | {row.get('tp',0)} | {row.get('fp',0)} | {row.get('fn',0)} |")
    md += [
        "",
        "### Live model probe",
        "",
        f"- Status: `{live.get('status')}`",
        f"- Provider/model: `{live.get('provider')}` / `{live.get('model')}`",
        f"- Prompt version: `{live.get('prompt_version')}`",
        f"- Note: {live.get('note') or live.get('reason') or live.get('error') or '—'}",
        "",
        "## 10. Fresh VALIDATION metrics",
        "",
        f"- E2E: {_pct(val_agg.get('e2e_pass_rate'))}",
        f"- Semantic P/R: {_pct(val_agg.get('semantic_concept_precision'))} / "
        f"{_pct(val_agg.get('semantic_concept_recall'))}",
        f"- Ground P/R: {_pct(val_agg.get('ground_precision'))} / "
        f"{_pct(val_agg.get('ground_recall'))}",
        f"- LEGAL_CONCLUSION independent: "
        f"{val_agg.get('legal_conclusion_independent_grounds')}",
        f"- SUPPORTING incorrectly in plan: "
        f"{val_agg.get('supporting_incorrectly_reached_claim_plan')}",
        "",
        "## 11. New sealed HOLDOUT metrics",
        "",
        f"- E2E: {_pct(hold_agg.get('e2e_pass_rate'))}",
        f"- Semantic P/R: {_pct(hold_agg.get('semantic_concept_precision'))} / "
        f"{_pct(hold_agg.get('semantic_concept_recall'))}",
        f"- Prior P10.4 holdout NOT reused as blind.",
        "",
        "| case_id | e2e | first_fail | concepts | grounds |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in hold_rows:
        md.append(
            f"| {r['case_id']} | {'PASS' if r['end_to_end_pass'] else 'FAIL'} | "
            f"{r.get('first_failed_layer') or '—'} | `{r.get('concepts')}` | "
            f"`{r.get('supported_grounds')}` |"
        )
    md += [
        "",
        "## 12. Clean-upstream draft coverage",
        "",
        f"- Clean cases: {val_agg.get('clean_upstream_draft_cases')}",
        f"- Draft ground coverage: {_pct(val_agg.get('draft_ground_coverage'))}",
        f"- Material-fact coverage: {_pct(val_agg.get('material_fact_coverage'))}",
        f"- Unsupported assertion rate: {_pct(val_agg.get('unsupported_assertion_rate'))}",
        "",
        "## 13. P8 invariant results",
        "",
        f"- Passed: **{invariants.get('passed')}**",
        f"- Checks: `{invariants.get('checks')}`",
        "",
        "## Recommendation",
        "",
        f"**{reco.get('recommendation')}**",
        "",
        f"- ADD_VECTOR_RETRIEVAL: `{reco.get('add_vector_retrieval')}`",
        f"- Production deploy: forbidden",
        f"- Fine-tune: forbidden",
        "",
        "Reasons:",
    ]
    for reason in reco.get("reasons") or ["—"]:
        md.append(f"- {reason}")
    md += [
        "",
        "---",
        "",
        "STOP after evaluation. No pgvector. No fine-tune. No production deploy.",
    ]
    REPORT.mkdir(parents=True, exist_ok=True)
    path = REPORT / "P10_5_EVALUATION_REPORT.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    (ROOT / "P10_5_EVALUATION_REPORT.md").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")
    (REPORT / "RECOMMENDATION.json").write_text(
        json.dumps(reco, indent=2) + "\n", encoding="utf-8")
    (REPORT / "ROOT_CAUSE.json").write_text(
        json.dumps(root_cause, indent=2) + "\n", encoding="utf-8")
    (REPORT / "LIVE_PROBE.json").write_text(
        json.dumps(live, indent=2, default=str) + "\n", encoding="utf-8")
    (REPORT / "aggregate.json").write_text(
        json.dumps({
            "development": dev_agg, "validation": val_agg, "holdout": hold_agg,
        }, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _pct(v):
    if v is None or v == "N/A":
        return "N/A"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(v)


def main() -> int:
    from .build_dataset import build
    print("P10.5 build dataset ...", flush=True)
    manifest = build()

    print("P10.5 freeze ...", flush=True)
    freeze = write_freeze(REPORT)
    freeze["semantic_ontology_version"] = ONTOLOGY_VERSION
    freeze["module_role_version"] = MODULE_ROLE_VERSION
    freeze["evaluation_release"] = "P10_5_SEMANTIC_2026-10-04"
    (REPORT / "FREEZE.json").write_text(
        json.dumps(freeze, indent=2) + "\n", encoding="utf-8")

    print("P10.5 role governance ...", flush=True)
    roles = _write_role_governance(REPORT)

    print("P10.5 live semantic probe ...", flush=True)
    live = _live_semantic_probe()

    print("P10.5 DEVELOPMENT ...", flush=True)
    dev_rows, dev_agg = _run_split(
        load_json_cases(DS / "development"), REPORT / "development")

    print("P10.5 VALIDATION ...", flush=True)
    val_rows, val_agg = _run_split(
        load_json_cases(DS / "validation"), REPORT / "validation")

    print("P10.5 sealed HOLDOUT (once) ...", flush=True)
    hold_rows, hold_agg = _run_split(
        load_json_cases(DS / "holdout"), REPORT / "holdout")

    print("P10.5 P8/P10 invariants ...", flush=True)
    invariants = run_p8_p10_invariants()

    path = _write_report(
        freeze, roles, manifest, ROOT_CAUSE, live,
        dev_rows, dev_agg, val_rows, val_agg, hold_rows, hold_agg, invariants,
    )
    reco = json.loads((REPORT / "RECOMMENDATION.json").read_text(encoding="utf-8"))
    print(f"FINAL: {path}", flush=True)
    print(f"RECOMMENDATION: {reco.get('recommendation')}", flush=True)
    print(f"DEV semantic R={dev_agg.get('semantic_concept_recall')} "
          f"VAL R={val_agg.get('semantic_concept_recall')} "
          f"HOLD R={hold_agg.get('semantic_concept_recall')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
