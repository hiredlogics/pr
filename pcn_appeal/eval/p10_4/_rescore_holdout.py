"""Rescore sealed holdout from saved actuals; rebuild final report. No pipeline re-run."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pcn_appeal.eval.p10_4.holdout_labels import HOLDOUT_LABELS
from pcn_appeal.eval.p10_4.metrics import aggregate_metrics, score_case
from pcn_appeal.eval.p10_4.report import (
    recommendation, write_final_report, write_holdout_report,
)

ROOT = Path(__file__).resolve().parents[3]
REPORT = ROOT / "reports" / "p10_4"
HOLD = REPORT / "holdout" / "cases"


def main() -> None:
    holdout_rows = []
    for path in sorted(HOLD.glob("*.json")):
        blob = json.loads(path.read_text(encoding="utf-8"))
        spec = blob["spec"]
        actual = blob["actual"]
        labels = dict(HOLDOUT_LABELS.get(spec["case_id"]) or {})
        expected = dict(labels)
        for k, v in (spec.get("expected") or {}).items():
            if k not in ("sealed", "note"):
                expected.setdefault(k, v)
        if labels.get("family"):
            spec = dict(spec)
            spec["family"] = labels["family"]
        scored = score_case(expected, actual)
        row = {
            "case_id": spec["case_id"],
            "split": spec.get("split"),
            "family": spec.get("family"),
            "end_to_end_pass": scored["end_to_end_pass"],
            "first_failed_layer": scored["first_failed_layer"],
            "supported_grounds": actual.get("supported_grounds") or [],
            "retrieved_modules": actual.get("retrieved_modules") or [],
            "state": actual.get("state"),
            "outcome": actual.get("outcome"),
            "scores": scored["scores"],
        }
        holdout_rows.append(row)
        blob["expected_scored"] = expected
        blob["scores"] = scored
        path.write_text(json.dumps(blob, indent=2, default=str) + "\n", encoding="utf-8")

    holdout_agg = aggregate_metrics([
        {"end_to_end_pass": r["end_to_end_pass"], "scores": r["scores"]}
        for r in holdout_rows
    ])

    freeze = json.loads((REPORT / "FREEZE.json").read_text(encoding="utf-8"))
    role_audit = json.loads(
        (REPORT / "ROLE_GOVERNANCE_AUDIT.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (ROOT / "datasets" / "p10_4_v1" / "manifest.json").read_text(encoding="utf-8"))
    val_metrics = json.loads(
        (REPORT / "validation" / "VALIDATION_METRICS.json").read_text(encoding="utf-8"))
    val_rows = val_metrics["cases"]
    val_agg = json.loads(
        (REPORT / "validation" / "aggregate.json").read_text(encoding="utf-8"))
    mutations = val_metrics.get("mutations") or {}
    invariants = val_metrics.get("p8_p10_invariants") or {}

    write_holdout_report(REPORT / "holdout", freeze, holdout_rows, holdout_agg)

    first_defective = []
    for r in val_rows + holdout_rows:
        if not r.get("end_to_end_pass"):
            first_defective.append(
                f"{r.get('case_id')}: {r.get('first_failed_layer') or 'UNKNOWN'}"
            )
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
        REPORT, freeze, role_audit, manifest, val_agg, holdout_agg,
        mutations, invariants, reco, first_defective, val_rows, holdout_rows,
    )
    (ROOT / "P10_4_EVALUATION_REPORT.md").write_text(
        final.read_text(encoding="utf-8"), encoding="utf-8")

    print("VAL E2E", val_agg.get("e2e_pass_rate"))
    print("HOLD E2E", holdout_agg.get("e2e_pass_rate"))
    print("RECO", reco.get("recommendation"), reco.get("reasons"))
    for r in val_rows:
        print("VAL", r["case_id"],
              "PASS" if r["end_to_end_pass"] else "FAIL",
              r.get("first_failed_layer"), r.get("supported_grounds"))
    for r in holdout_rows:
        print("HO", r["case_id"],
              "PASS" if r["end_to_end_pass"] else "FAIL",
              r.get("first_failed_layer"), r.get("supported_grounds"))
    print("FINAL", final)


if __name__ == "__main__":
    main()
