"""Re-score DEVELOPMENT + VALIDATION after semantic fixes; keep sealed holdout."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from pcn_appeal.eval.p10_4.pipeline import load_json_cases
from pcn_appeal.eval.p10_5.__main__ import (
    REPORT, ROOT_CAUSE, _live_semantic_probe, _run_split, _write_report,
    _write_role_governance,
)
from pcn_appeal.eval.p10_4.invariants import run_p8_p10_invariants
from pcn_appeal.semantics.ontology import ONTOLOGY_VERSION
from pcn_appeal.module_roles import MODULE_ROLE_VERSION

DS = ROOT / "datasets" / "p10_5_v1"


def main() -> None:
    freeze = json.loads((REPORT / "FREEZE.json").read_text(encoding="utf-8"))
    roles = _write_role_governance(REPORT)
    live = json.loads((REPORT / "LIVE_PROBE.json").read_text(encoding="utf-8"))
    manifest = json.loads((DS / "manifest.json").read_text(encoding="utf-8"))

    print("Re-run DEVELOPMENT ...", flush=True)
    dev_rows, dev_agg = _run_split(
        load_json_cases(DS / "development"), REPORT / "development")
    print("Re-run VALIDATION ...", flush=True)
    val_rows, val_agg = _run_split(
        load_json_cases(DS / "validation"), REPORT / "validation")

    # Keep prior holdout actuals; rescore with current metrics only
    from pcn_appeal.eval.p10_4.metrics import aggregate_metrics, score_case
    from pcn_appeal.eval.p10_5.holdout_labels import HOLDOUT_LABELS
    hold_rows = []
    for path in sorted((REPORT / "holdout" / "cases").glob("*.json")):
        blob = json.loads(path.read_text(encoding="utf-8"))
        spec = blob["spec"]
        actual = blob["actual"]
        labels = dict(HOLDOUT_LABELS.get(spec["case_id"]) or {})
        expected = dict(labels)
        scored = score_case(expected, actual)
        hold_rows.append({
            "case_id": spec["case_id"],
            "split": "HOLDOUT",
            "family": labels.get("family") or spec.get("family"),
            "end_to_end_pass": scored["end_to_end_pass"],
            "first_failed_layer": scored["first_failed_layer"],
            "supported_grounds": actual.get("supported_grounds") or [],
            "concepts": [
                (c.get("concept"), c.get("polarity"))
                for c in (actual.get("concepts") or [])
            ],
            "state": actual.get("state"),
            "scores": scored["scores"],
        })
        blob["scores"] = scored
        path.write_text(json.dumps(blob, indent=2, default=str) + "\n", encoding="utf-8")
    hold_agg = aggregate_metrics([
        {"end_to_end_pass": r["end_to_end_pass"], "scores": r["scores"]}
        for r in hold_rows
    ])

    invariants = run_p8_p10_invariants()
    path = _write_report(
        freeze, roles, manifest, ROOT_CAUSE, live,
        dev_rows, dev_agg, val_rows, val_agg, hold_rows, hold_agg, invariants,
    )
    reco = json.loads((REPORT / "RECOMMENDATION.json").read_text(encoding="utf-8"))
    print("DEV R", dev_agg.get("semantic_concept_recall"),
          "VAL R", val_agg.get("semantic_concept_recall"),
          "HOLD R", hold_agg.get("semantic_concept_recall"),
          "HOLD E2E", hold_agg.get("e2e_pass_rate"))
    print("RECO", reco.get("recommendation"))
    print("FINAL", path)


if __name__ == "__main__":
    main()
