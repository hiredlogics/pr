"""Staging regression set — real pipeline paths (ReferenceAnalysisLLM + optional live)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DS = ROOT / "datasets" / "p10_6_v1"

# Map expected hold / release classes for healthy journeys.
EXPECTED_CLASS = {
    "DEV_DRAFT_no_ground": "NO_SUPPORTED_GROUNDS",
    "DEV_DRAFT_support_only": "NO_SUPPORTED_GROUNDS",
    "HOLDOUT_DRAFT_D": "NO_SUPPORTED_GROUNDS",
}


def run(report_dir: Path) -> dict[str, Any]:
    from pcn_appeal.eval.p10_4.metrics import score_drafting
    from pcn_appeal.eval.p10_4.pipeline import load_json_cases, run_once
    from pcn_appeal.drafting.plan import build_draft_plan, coverage_trace

    cases = []
    for split in ("development", "holdout"):
        d = DS / split
        if d.exists():
            cases.extend(load_json_cases(d))
    # Prefer a compact staging slice covering families
    prefer = {
        "DEV_DRAFT_single_payment", "DEV_DRAFT_pay_key_merge",
        "DEV_DRAFT_multiple_visits", "DEV_DRAFT_mechanical",
        "DEV_DRAFT_loading_delivery", "DEV_DRAFT_permit",
        "DEV_DRAFT_legal_finding_rebuttal", "DEV_DRAFT_no_ground",
        "DEV_DRAFT_support_only", "HOLDOUT_DRAFT_A", "HOLDOUT_DRAFT_D",
    }
    selected = [c for c in cases if c.get("case_id") in prefer] or cases[:12]

    rows = []
    latencies = []
    for spec in selected:
        t0 = time.time()
        actual = run_once(spec)
        latency = int((time.time() - t0) * 1000)
        latencies.append(latency)
        public = {k: v for k, v in actual.items() if not k.startswith("_")}
        drafting = score_drafting(spec.get("expected") or {}, public)
        state = public.get("state")
        outcome = public.get("outcome") or {}
        code = outcome.get("code") if isinstance(outcome, dict) else None
        expected_hold = EXPECTED_CLASS.get(spec["case_id"])
        if expected_hold:
            e2e_ok = state != "RELEASED" and (
                code == expected_hold
                or code in ("NEEDS_FACTS", "NEEDS_DOCUMENTS", "NO_SUPPORTED_GROUNDS")
                or state == "MANUAL_REVIEW"
            )
            result_class = code or "HOLD"
        else:
            # Successful path: RELEASED with full draft coverage, or clean upstream N/A
            if drafting.get("clean_upstream"):
                e2e_ok = (
                    state == "RELEASED"
                    and drafting.get("draft_ground_coverage") == 1.0
                    and drafting.get("unsupported_assertion_rate") in (0.0, "N/A", None)
                )
                result_class = "RELEASED" if e2e_ok else "DRAFT_FAIL"
            else:
                # Upstream incomplete — classify, do not treat as drafting defect
                result_class = code or ("CLAIM_PLAN_EMPTY" if not public.get("supported_grounds")
                                        else "UPSTREAM_INCOMPLETE")
                e2e_ok = result_class in (
                    "NO_SUPPORTED_GROUNDS", "NEEDS_FACTS", "NEEDS_DOCUMENTS",
                    "CLAIM_PLAN_EMPTY", "UPSTREAM_INCOMPLETE",
                ) or state == "MANUAL_REVIEW"

        pack = getattr(actual.get("_out"), "pack", None) if actual.get("_out") else None
        draft = getattr(actual.get("_out"), "draft", None) if actual.get("_out") else None
        cov = []
        if pack is not None:
            try:
                cov = coverage_trace(build_draft_plan(pack, spec["case_id"]), draft, pack)
            except Exception:
                pass
        row = {
            "case_id": spec["case_id"],
            "family": spec.get("family"),
            "state": state,
            "outcome_code": code,
            "result_class": result_class,
            "e2e_ok": e2e_ok,
            "latency_ms": latency,
            "drafting": drafting,
            "supported_grounds": public.get("supported_grounds"),
            "coverage_trace": cov,
        }
        rows.append(row)
        (report_dir / "regression").mkdir(parents=True, exist_ok=True)
        (report_dir / "regression" / f"{spec['case_id']}.json").write_text(
            json.dumps(row, indent=2, default=str) + "\n", encoding="utf-8")

    latencies_sorted = sorted(latencies)
    def pct(p):
        if not latencies_sorted:
            return None
        idx = min(len(latencies_sorted) - 1, int(round((p / 100) * (len(latencies_sorted) - 1))))
        return latencies_sorted[idx]

    clean = [r for r in rows if (r.get("drafting") or {}).get("clean_upstream")]
    return {
        "passed": all(r["e2e_ok"] for r in rows),
        "ran": True,
        "n_cases": len(rows),
        "n_e2e_ok": sum(1 for r in rows if r["e2e_ok"]),
        "n_clean_upstream": len(clean),
        "clean_ground_coverage": (
            sum((r["drafting"].get("draft_ground_coverage") or 0) for r in clean) / len(clean)
            if clean else None
        ),
        "latency_p50_ms": pct(50),
        "latency_p95_ms": pct(95),
        "cases": rows,
    }
