"""python -m pcn_appeal.eval.p9

Writes the dataset, runs the real pipeline on every golden, and emits the
P9 baseline reports. Does not modify application code.
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

from .dataset import DATASET_DIR, build_manifest, load_cases, write_dataset
from .metrics import score_all
from .mutations import run_mutations
from .pipeline import persist_reload_check, run_once, semantic_state, versions_block
from .report import aggregate, baseline_cover, case_markdown
from .schema import COMPLETE, NOTICE_ONLY

REPORT_DIR = ROOT / "reports" / "p9"
REPEAT_N = 5
REPEAT_CASES = {
    "REG_late_ntk", "REG_late_ntk_anpr", "REG_overstay_skip_all",
    "REG_payment_keying", "REG_breakdown",
}
ADDITIVE_A = "REG_late_ntk"
ADDITIVE_B = "REG_late_ntk_anpr"
MUTATION_CASE = "REG_late_ntk"


def _strip_runtime(actual: dict) -> dict:
    return {k: v for k, v in actual.items() if not k.startswith("_")}


def run_evaluation(splits: tuple[str, ...] | None = None,
                   out_dir: Path | None = None,
                   write_p9_root: bool = True,
                   repeat: bool = True) -> dict:
    dest = Path(out_dir) if out_dir else REPORT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "cases").mkdir(exist_ok=True)
    manifest = write_dataset()
    cases = load_cases()
    if splits:
        wanted = {s.upper() for s in splits}
        cases = [c for c in cases if c.split.upper() in wanted]
    versions = versions_block()
    rows = []
    actuals = {}
    mutation_pack = None
    mutation_draft = None

    for golden in cases:
        result = run_once(golden)
        case_obj = result.pop("_case", None)
        out = result.pop("_out", None)
        result.pop("_pipe", None)
        persist = {"status": "N/A"}
        if case_obj is not None and golden.completeness == COMPLETE:
            persist = persist_reload_check(case_obj)
        result["persist"] = persist
        scored = score_all(golden, result)
        if persist.get("passed") is False and golden.expected.claim_plan.evaluate:
            scored["scores"]["persist"] = {
                "status": "SCORED", "passed": False, **persist,
            }
            if scored.get("end_to_end_pass"):
                scored["end_to_end_pass"] = False
                scored["first_failed_layer"] = scored.get("first_failed_layer") or "CLAIM_PLAN_ERROR"
        actuals[golden.case_id] = result
        if golden.case_id == MUTATION_CASE and out is not None:
            mutation_draft = getattr(out, "draft", None)
            mutation_pack = getattr(out, "pack", None)
        row = {
            "case_id": golden.case_id,
            "split": golden.split,
            "completeness": golden.completeness,
            "end_to_end_pass": scored["end_to_end_pass"],
            "first_failed_layer": scored["first_failed_layer"],
            "supported_grounds": result.get("supported_grounds") or [],
            "state": result.get("state"),
            "outcome": result.get("outcome"),
            "scores": scored["scores"],
            "scored": scored,
        }
        rows.append(row)
        md = case_markdown(golden, result, scored, persist)
        (dest / "cases" / f"{golden.case_id}.md").write_text(md, encoding="utf-8")
        (dest / "cases" / f"{golden.case_id}.json").write_text(
            json.dumps({
                "golden": golden.to_dict(),
                "actual": _strip_runtime(result),
                "scores": scored,
                "persist": persist,
            }, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    additive = _additive(actuals)
    if additive.get("passed") is False:
        for row in rows:
            if row["case_id"] == ADDITIVE_B and not row["first_failed_layer"]:
                row["end_to_end_pass"] = False
                row["first_failed_layer"] = "GROUND_MERGE_ERROR"
                row["scored"]["end_to_end_pass"] = False
                row["scored"]["first_failed_layer"] = "GROUND_MERGE_ERROR"

    repeats = _repeatability(cases) if repeat else {
        "runs_per_case": 0, "stability_rate": "N/A", "note": "skipped"
    }
    mutations = run_mutations(mutation_draft, mutation_pack)

    report = aggregate(rows, mutations, additive, repeats, versions)
    baseline = baseline_cover(rows, versions)
    (dest / "P9_EVALUATION_REPORT.md").write_text(report, encoding="utf-8")
    (dest / "BASELINE_REPORT.md").write_text(baseline, encoding="utf-8")
    if write_p9_root:
        ROOT.joinpath("P9_EVALUATION_REPORT.md").write_text(report, encoding="utf-8")
        ROOT.joinpath("BASELINE_REPORT.md").write_text(baseline, encoding="utf-8")
    (dest / "aggregate.json").write_text(
        json.dumps({
            "manifest": manifest,
            "versions": versions,
            "rows": [{k: v for k, v in r.items() if k != "scored"} for r in rows],
            "additive": additive,
            "repeatability": repeats,
            "mutations": mutations,
        }, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    _write_lists(manifest, dest)
    return {
        "cases": len(rows),
        "passed": sum(1 for r in rows if r["end_to_end_pass"]),
        "report": str(dest / "P9_EVALUATION_REPORT.md"),
        "rows": [{k: v for k, v in r.items() if k != "scored"} for r in rows],
        "mutations": mutations,
    }


def _additive(actuals: dict) -> dict:
    a = actuals.get(ADDITIVE_A) or {}
    b = actuals.get(ADDITIVE_B) or {}
    grounds_a = set(a.get("supported_grounds") or [])
    grounds_b = set(b.get("supported_grounds") or [])
    dropped = sorted(grounds_a - grounds_b)
    added = sorted(grounds_b - grounds_a)
    return {
        "pair": [ADDITIVE_A, ADDITIVE_B],
        "run_a": sorted(grounds_a),
        "run_b": sorted(grounds_b),
        "dropped_without_check": dropped,
        "added": added,
        "passed": not dropped,
        "detail": (
            f"independent grounds dropped from B without InvalidationRecord: {dropped}"
            if dropped else "all still-valid A grounds remain in B"
        ),
        "note": "This baseline cannot inspect InvalidationRecord if the ground "
                "is absent; any drop is treated as a silent remove.",
    }


def _repeatability(cases) -> dict:
    selected = [c for c in cases if c.case_id in REPEAT_CASES]
    per = {}
    stable = 0
    for golden in selected:
        states = []
        for _ in range(REPEAT_N):
            result = run_once(golden)
            result.pop("_case", None)
            result.pop("_out", None)
            result.pop("_pipe", None)
            states.append(semantic_state(result))
        uniq = []
        for s in states:
            blob = json.dumps(s, sort_keys=True, default=str)
            if blob not in uniq:
                uniq.append(blob)
        ok = len(uniq) == 1
        stable += int(ok)
        per[golden.case_id] = {
            "runs": REPEAT_N,
            "unique_semantic_states": len(uniq),
            "stable": ok,
        }
    n = len(selected) or 1
    return {
        "runs_per_case": REPEAT_N,
        "cases": [c.case_id for c in selected],
        "stable_cases": stable,
        "stability_rate": stable / n,
        "per_case": per,
        "note": "Exact wording may vary; this compares facts, findings, grounds, "
                "plan digest, and outcome only.",
    }


def _write_lists(manifest: dict, dest: Path = REPORT_DIR) -> None:
    (dest / "DATASET_MANIFEST.md").write_text(
        "\n".join([
            "# P9 dataset manifest",
            "",
            f"Dataset: `{manifest['dataset_id']}`",
            "",
            "## Split",
            "",
            *[f"- {k}: {', '.join(v)}" for k, v in (manifest.get("split") or {}).items()],
            "",
            "## COMPLETE goldens",
            "",
            *[f"- `{i}`" for i in manifest.get("complete_case_ids") or []],
            "",
            "## NOTICE_ONLY reference cases",
            "",
            *[f"- `{i}`" for i in manifest.get("notice_only_case_ids") or []],
            "",
            "## Leakage controls",
            "",
            *[f"- {c}" for c in (manifest.get("split_policy") or {}).get("leakage_controls") or []],
            "",
        ]),
        encoding="utf-8",
    )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--split", action="append", default=[],
                   help="DEVELOPMENT, VALIDATION, and/or BLIND_HOLDOUT (repeatable)")
    p.add_argument("--out", default="", help="report directory (default reports/p9)")
    p.add_argument("--no-p9-root", action="store_true")
    p.add_argument("--no-repeat", action="store_true")
    args = p.parse_args()
    summary = run_evaluation(
        splits=tuple(args.split) or None,
        out_dir=Path(args.out) if args.out else None,
        write_p9_root=not args.no_p9_root,
        repeat=not args.no_repeat,
    )
    printable = {k: v for k, v in summary.items() if k not in ("rows", "mutations")}
    print(json.dumps(printable, indent=2))
