"""python -m pcn_appeal.eval.p10_4

P10.4 freeze → role audit → fresh VALIDATION → mutations/invariants →
finalize validation report → sealed holdout once → final report.

Does not modify application behaviour, prompts, KB, or goldens.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from .freeze import write_freeze
from .holdout_labels import HOLDOUT_LABELS
from .invariants import repeatability_check, run_p8_p10_invariants
from .metrics import aggregate_metrics, score_case
from .mutations import run_mutations, synthetic_role_mutation_packs
from .pipeline import HOLDOUT_DIR, VAL_DIR, load_json_cases, persist_reload_check, run_once
from .report import (
    case_markdown, recommendation, write_final_report,
    write_holdout_report, write_validation_report,
)
from .role_audit import write_audit

REPORT_DIR = ROOT / "reports" / "p10_4"
REPEAT_CASE = "VAL_payment_keying"


def _strip(actual: dict) -> dict:
    return {k: v for k, v in actual.items() if not k.startswith("_")}


def _score_spec(spec: dict, actual: dict) -> dict:
    expected = dict(spec.get("expected") or {})
    # Merge evaluator-side holdout labels without editing sealed JSON.
    if spec.get("sealed") or spec.get("case_id") in HOLDOUT_LABELS:
        labels = HOLDOUT_LABELS.get(spec["case_id"]) or {}
        merged = dict(labels)
        # keep sealed marker out of scoring keys
        for k, v in expected.items():
            if k not in ("sealed", "note"):
                merged.setdefault(k, v)
        expected = merged
        if labels.get("family"):
            spec = dict(spec)
            spec["family"] = labels["family"]
    return score_case(expected, actual), expected, spec


def run_split(cases: list[dict], dest: Path, *, persist: bool = True) -> tuple[list[dict], dict, object, object]:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "cases").mkdir(exist_ok=True)
    rows = []
    mutation_draft = None
    mutation_pack = None
    for spec in cases:
        print(f"  run {spec['case_id']} ...", flush=True)
        result = run_once(spec)
        case_obj = result.pop("_case", None)
        out = result.pop("_out", None)
        result.pop("_pipe", None)
        persist_row = {"status": "N/A"}
        if persist and case_obj is not None:
            persist_row = persist_reload_check(case_obj)
        result["persist"] = persist_row
        scored, expected, spec2 = _score_spec(spec, result)
        if persist_row.get("passed") is False:
            scored.setdefault("scores", {})["persist"] = {
                "status": "SCORED", "passed": False, **persist_row,
            }
            if scored.get("end_to_end_pass"):
                scored["end_to_end_pass"] = False
                scored["first_failed_layer"] = scored.get("first_failed_layer") or "PERSIST"
        row = {
            "case_id": spec2["case_id"],
            "split": spec2.get("split"),
            "family": spec2.get("family"),
            "end_to_end_pass": scored["end_to_end_pass"],
            "first_failed_layer": scored["first_failed_layer"],
            "supported_grounds": result.get("supported_grounds") or [],
            "retrieved_modules": result.get("retrieved_modules") or [],
            "state": result.get("state"),
            "outcome": result.get("outcome"),
            "scores": scored["scores"],
        }
        rows.append(row)
        (dest / "cases" / f"{spec['case_id']}.md").write_text(
            case_markdown(spec2, result, scored), encoding="utf-8")
        (dest / "cases" / f"{spec['case_id']}.json").write_text(
            json.dumps({
                "spec": {k: v for k, v in spec2.items()},
                "expected_scored": expected,
                "actual": _strip(result),
                "scores": scored,
                "persist": persist_row,
            }, indent=2, default=str) + "\n",
            encoding="utf-8")
        if out is not None and mutation_draft is None:
            if getattr(out, "draft", None) is not None and result.get("state") == "RELEASED":
                mutation_draft = out.draft
                mutation_pack = out.pack
    agg = aggregate_metrics([
        {"end_to_end_pass": r["end_to_end_pass"], "scores": r["scores"]}
        for r in rows
    ])
    return rows, agg, mutation_draft, mutation_pack


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    only = None
    if argv and argv[0] in ("validation", "holdout", "freeze"):
        only = argv[0]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("P10.4 freeze ...", flush=True)
    freeze = write_freeze(REPORT_DIR)
    print(f"  commit={freeze.get('git_commit')} kb={freeze.get('kb_release')}", flush=True)

    print("P10.4 role governance audit ...", flush=True)
    role_audit = write_audit(REPORT_DIR)
    print(f"  ROLE_REVIEW_REQUIRED={role_audit.get('role_review_required_count')}", flush=True)

    if only == "freeze":
        return 0

    manifest = json.loads(
        (ROOT / "datasets" / "p10_4_v1" / "manifest.json").read_text(encoding="utf-8"))

    mutations = {"status": "N/A"}
    invariants = {"passed": None}
    val_rows: list[dict] = []
    val_agg: dict = {}

    if only in (None, "validation"):
        print("P10.4 fresh VALIDATION ...", flush=True)
        val_cases = load_json_cases(VAL_DIR)
        val_rows, val_agg, mut_draft, mut_pack = run_split(
            val_cases, REPORT_DIR / "validation", persist=True)

        print("P10.4 validator mutations ...", flush=True)
        if mut_draft is not None:
            mutations = run_mutations(mut_draft, mut_pack)
        else:
            mutations = synthetic_role_mutation_packs()
            mutations["note"] = "no RELEASED draft on validation; used synthetic orphan pack base"

        print("P10.4 P8/P10 invariants ...", flush=True)
        invariants = run_p8_p10_invariants()

        # Repeatability on one VAL case
        repeat_spec = next(
            (c for c in val_cases if c["case_id"] == REPEAT_CASE), val_cases[0])
        print(f"P10.4 repeatability ({REPEAT_CASE}) ...", flush=True)
        rep = repeatability_check(lambda s: _strip(run_once(s)), repeat_spec, n=3)
        invariants["repeatability"] = rep
        if not rep.get("passed"):
            invariants["passed"] = False
            checks = dict(invariants.get("checks") or {})
            checks["repeatability"] = False
            invariants["checks"] = checks

        write_validation_report(
            REPORT_DIR / "validation",
            freeze, role_audit, manifest, val_rows, val_agg, mutations, invariants,
        )
        print("Validation report written.", flush=True)

        if only == "validation":
            return 0

    # Sealed holdout — only after validation report finalized
    print("P10.4 sealed HOLDOUT (once) ...", flush=True)
    holdout_cases = load_json_cases(HOLDOUT_DIR)
    holdout_rows, holdout_agg, _, _ = run_split(
        holdout_cases, REPORT_DIR / "holdout", persist=False)
    write_holdout_report(REPORT_DIR / "holdout", freeze, holdout_rows, holdout_agg)

    # If validation was skipped, load prior aggregate if present
    if not val_agg:
        prior = REPORT_DIR / "validation" / "aggregate.json"
        if prior.exists():
            val_agg = json.loads(prior.read_text(encoding="utf-8"))
        val_metrics = REPORT_DIR / "validation" / "VALIDATION_METRICS.json"
        if val_metrics.exists():
            blob = json.loads(val_metrics.read_text(encoding="utf-8"))
            val_rows = blob.get("cases") or []
            mutations = blob.get("mutations") or mutations
            invariants = blob.get("p8_p10_invariants") or invariants

    first_defective = []
    for r in val_rows + holdout_rows:
        if not r.get("end_to_end_pass"):
            first_defective.append(
                f"{r.get('case_id')}: {r.get('first_failed_layer') or 'UNKNOWN'}"
            )
    # Layer frequency
    from collections import Counter
    layer_counts = Counter(
        r.get("first_failed_layer") for r in val_rows + holdout_rows
        if r.get("first_failed_layer")
    )
    if layer_counts:
        first_defective.append(
            "Frequency: " + ", ".join(f"{k}×{v}" for k, v in layer_counts.most_common())
        )

    reco = recommendation(val_agg, holdout_agg, mutations, invariants, role_audit)
    final = write_final_report(
        REPORT_DIR, freeze, role_audit, manifest, val_agg, holdout_agg,
        mutations, invariants, reco, first_defective, val_rows, holdout_rows,
    )
    # Also root-level convenience copy
    root_copy = ROOT / "P10_4_EVALUATION_REPORT.md"
    root_copy.write_text(final.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"FINAL: {final}", flush=True)
    print(f"RECOMMENDATION: {reco.get('recommendation')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
