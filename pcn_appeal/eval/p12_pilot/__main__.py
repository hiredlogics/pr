"""python -m pcn_appeal.eval.p12_pilot

P12 controlled production pilot — freeze, preflight, cohort, report.
Does not redesign architecture, fine-tune, change pgvector, or broaden rollout.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from pcn_appeal import config
from pcn_appeal.eval.p10_4.invariants import run_p8_p10_invariants

from . import cohort as cohort_mod
from . import freeze as freeze_mod
from . import observability
from . import photo_smoke
from . import preflight

REPORT_PILOT = ROOT / "reports" / "p12_pilot"
REPORT_P12 = ROOT / "reports" / "p12"


def _load_staging_env() -> None:
    staging = ROOT / ".env.staging.local"
    if not staging.exists():
        return
    for line in staging.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, val)


def _recommendation(pre: dict, freeze: dict, cohort: dict, inv: dict) -> str:
    if not freeze.get("working_tree_clean"):
        return "PAUSE_AND_FIX"
    if cohort.get("safety_incidents"):
        return "PAUSE_AND_FIX"
    if not pre.get("passed"):
        return "PAUSE_AND_FIX"
    n = int(cohort.get("n_real_cases") or 0)
    if n < 10:
        return "PILOT_EXTENSION_REQUIRED"
    unsupported = cohort.get("unsupported_assertion_rate")
    gcov = cohort.get("draft_ground_coverage")
    partic = cohort.get("required_particular_coverage")
    driver_v = int(cohort.get("driver_disclosure_violations") or 0)
    persist = cohort.get("persist_ok_rate")
    if (
        n >= 10
        and not cohort.get("safety_incidents")
        and unsupported == 0
        and gcov == 1.0
        and partic == 1.0
        and driver_v == 0
        and (persist is None or persist >= 0.95)
        and bool((inv or {}).get("passed"))
        and freeze.get("working_tree_clean")
    ):
        return "READY_FOR_LIMITED_ROLLOUT"
    return "PILOT_EXTENSION_REQUIRED"


def main() -> int:
    REPORT_PILOT.mkdir(parents=True, exist_ok=True)
    REPORT_P12.mkdir(parents=True, exist_ok=True)
    _load_staging_env()
    config.load()

    print("P12 freeze...")
    fr = freeze_mod.write_freeze(REPORT_PILOT / "freeze")
    (REPORT_P12 / "FREEZE.json").write_text(
        json.dumps(fr, indent=2) + "\n", encoding="utf-8")
    print("  commit", fr.get("git_commit"), "kb", fr.get("kb_release_id"),
          "clean", fr.get("working_tree_clean"))

    if not fr.get("working_tree_clean"):
        print("WARNING: working tree not clean — freeze gate FAIL (PAUSE_AND_FIX)")

    print("Physical-photo smoke...")
    photo = photo_smoke.run(max_base=3)
    (REPORT_PILOT / "photo_smoke_detail.json").write_text(
        json.dumps(photo, indent=2, default=str) + "\n", encoding="utf-8")
    print("  photo passed", photo.get("passed"), "rows", photo.get("n_rows"))

    print("Preflight blockers...")
    pre = preflight.run_all(photo)
    (REPORT_PILOT / "preflight.json").write_text(
        json.dumps(pre, indent=2, default=str) + "\n", encoding="utf-8")

    print("P8/P10 invariants...")
    inv = run_p8_p10_invariants()

    # Cohort: only when freeze is clean (immutable pilot release).
    if fr.get("working_tree_clean") and pre.get("passed"):
        print("Cohort (10–20 real/varied cases, no invented reverse pages)...")
        cohort = cohort_mod.run_cohort(fr, max_cases=20)
    else:
        print("Cohort skipped — freeze/preflight not green.")
        cohort = observability.empty_cohort_metrics()
        cohort["ran"] = False
        cohort["skip_reason"] = (
            "working_tree_dirty" if not fr.get("working_tree_clean")
            else "preflight_failed"
        )

    (REPORT_PILOT / "cohort_registry.json").write_text(
        json.dumps(cohort, indent=2, default=str) + "\n", encoding="utf-8")
    (REPORT_P12 / "cohort_registry.json").write_text(
        json.dumps(cohort, indent=2, default=str) + "\n", encoding="utf-8")

    reco = _recommendation(pre, fr, cohort, inv if isinstance(inv, dict) else {})
    agg = {
        "phase": "P12-PRODUCTION-PILOT",
        "freeze": fr,
        "preflight": {
            "passed": pre.get("passed"),
            "blockers": pre.get("blockers"),
            "nsg": pre.get("nsg"),
            "kb_pin": pre.get("kb_pin"),
            "secrets": {k: (pre.get("secrets") or {}).get(k)
                        for k in ("passed", "files_scanned", "critical_count",
                                  "note", "operator_action_required")},
            "rollback": pre.get("rollback"),
            "photo_smoke": pre.get("photo_smoke"),
        },
        "photo_smoke_summary": {
            k: photo.get(k) for k in (
                "ran", "passed", "physical_photos_present", "n_rows", "n_passed",
                "n_base_photos", "legal_critical_accuracy_mean", "normal_all_passed",
                "modes", "note", "reason",
            ) if k in photo
        },
        "cohort": {k: v for k, v in cohort.items() if k != "case_records"},
        "cohort_case_ids": [
            {"cohort_id": r.get("cohort_id"), "case_id": r.get("case_id"),
             "state": r.get("final_state"), "layer": r.get("first_defective_layer")}
            for r in (cohort.get("case_records") or [])
        ],
        "observability_schema": {
            "case_record_fields": list(observability.CASE_RECORD_FIELDS),
            "safety_stops": list(observability.SAFETY_STOPS),
            "release_conditions": list(observability.RELEASE_CONDITIONS),
        },
        "invariants": {k: inv.get(k) for k in ("passed", "tests_run", "checks")
                       if isinstance(inv, dict)},
        "customer_path": [
            "upload_front_back", "classification_extraction",
            "customer_confirmation", "semantic_extraction",
            "questions_where_needed", "legal_findings", "kb_eligibility",
            "claim_plan", "draft_plan", "live_llm_drafting", "validation",
            "customer_outcome",
        ],
        "recommendation": reco,
        "auto_broaden_rollout": False,
    }
    (REPORT_PILOT / "aggregate.json").write_text(
        json.dumps(agg, indent=2, default=str) + "\n", encoding="utf-8")
    (REPORT_P12 / "aggregate.json").write_text(
        json.dumps(agg, indent=2, default=str) + "\n", encoding="utf-8")

    md = _render(agg, cohort)
    for dest in (
        ROOT / "P12_PRODUCTION_PILOT_REPORT.md",
        REPORT_PILOT / "P12_PRODUCTION_PILOT_REPORT.md",
        REPORT_P12 / "P12_PRODUCTION_PILOT_REPORT.md",
    ):
        dest.write_text(md, encoding="utf-8")
    print("recommendation:", reco)
    print("report:", REPORT_P12 / "P12_PRODUCTION_PILOT_REPORT.md")
    return 0 if reco != "PAUSE_AND_FIX" else 1


def _yn(v) -> str:
    return "PASS" if v else "FAIL"


def _render(agg: dict, cohort: dict) -> str:
    fr = agg.get("freeze") or {}
    pre = agg.get("preflight") or {}
    blockers = pre.get("blockers") or {}
    reco = agg.get("recommendation")
    defects = cohort.get("defect_register") or []
    return f"""# P12 — Controlled Production Pilot

Frozen, staging-approved architecture. No redesign, fine-tune, pgvector change,
silent prompt/KB edits, or automatic rollout broadening.

## 1. Pilot release metadata

```json
{json.dumps(fr, indent=2, default=str)}
```

Working tree clean: **{fr.get('working_tree_clean')}**  
deployment_id: `{fr.get('deployment_id')}`  
KB release id: `{fr.get('kb_release_id')}`  
KB release digest: `{fr.get('kb_release_digest')}`  
validation_version: `{fr.get('validation_version') or fr.get('validation_engine_version')}`

## Pre-pilot blockers

| Blocker | Result |
| --- | --- |
| NO_SUPPORTED_GROUNDS state consistency | {_yn(blockers.get('nsg_state_consistent'))} |
| KB release pinned (non-null) | {_yn(blockers.get('kb_release_pinned'))} |
| Secrets scan clean | {_yn(blockers.get('secrets_scan_clean'))} |
| Physical-photo smoke safe | {_yn(blockers.get('photo_smoke_safe'))} |
| Rollback documented | {_yn(blockers.get('rollback_documented'))} |
| Working tree clean | {_yn(fr.get('working_tree_clean'))} |

## 2. Number of real cases

**{cohort.get('n_real_cases', 0)}** (cap {cohort.get('cohort_cap', 20)}; auto-scale forbidden)

Case index:
```json
{json.dumps(agg.get('cohort_case_ids') or [], indent=2)}
```

## 3. Outcome distribution

```json
{json.dumps(cohort.get('outcome_distribution') or dict(), indent=2)}
```

## 4. Extraction results

Photo smoke:
```json
{json.dumps(agg.get('photo_smoke_summary'), indent=2, default=str)}
```

Front-only physical photos do **not** invent reverse pages (P12 §4). Incomplete
sides yield safe holds, not forced RELEASED.

## 5. Semantic results

Precision: {cohort.get('semantic_precision')} · Recall: {cohort.get('semantic_recall')}  
Narrative atoms / departure_reason propagate via existing architecture when present
in customer account (see per-case `narrative_atoms` in cohort_registry.json).

## 6. Legal-finding results

PoFA grounds must remain notice-derived. Per-case `pofa_from_narrative` must be false.
Findings recorded with type/status/id on each case record.

## 7. Ground precision / recall

Precision: {cohort.get('ground_precision')} · Recall: {cohort.get('ground_recall')}  
(Full labelled review is pilot QA; automated rates filled when labels available.)

## 8. Claim Plan quality

Inspected per case: selected grounds, rejected grounds, SupportBundle lineage.
Support-only / legal-conclusion modules must not stand alone (P8/P10 invariants:
{_yn((agg.get('invariants') or dict()).get('passed'))}).

## 9. DraftPlan quality

Completeness: {cohort.get('draft_plan_completeness')}

## 10. Final appeal coverage

Ground: {cohort.get('draft_ground_coverage')} · Material: {cohort.get('material_fact_coverage')} · Required particulars: {cohort.get('required_particular_coverage')} · Unsupported: {cohort.get('unsupported_assertion_rate')} · Driver disclosures: {cohort.get('driver_disclosure_violations')}

## 11. Validator performance

Validation failure rate: {cohort.get('validation_failure_rate')} · Processing error rate: {cohort.get('processing_error_rate')} · NO_SUPPORTED_GROUNDS rate: {cohort.get('no_supported_grounds_rate')}

## 12. Provider failures

```json
{json.dumps(cohort.get('provider_failure_probe') or dict(), indent=2, default=str)}
```

429/5xx rates: {cohort.get('http_429_rate')} / {cohort.get('http_5xx_rate')}

## 13. Persistence / idempotency

Persist OK rate: {cohort.get('persist_ok_rate')}  
Idempotency probe:
```json
{json.dumps(cohort.get('idempotency_probe') or dict(), indent=2, default=str)}
```

## 14. Latency

P50: {cohort.get('latency_p50_ms')} ms · P95: {cohort.get('latency_p95_ms')} ms

## 15. Cost / case

{cohort.get('cost_note') or 'n/a'}  
mean={cohort.get('mean_cost_case')} median={cohort.get('median_cost_case')} highest={cohort.get('highest_cost_case')}

## 16. Safety incidents

```json
{json.dumps(cohort.get('safety_incidents') or [], indent=2)}
```

## 17. Defect register (first defective layer)

```json
{json.dumps(defects, indent=2)}
```

## 18. Unresolved issues

- pgvector: observe only; 11 duplicate HNSW indexes remain KNOWN_DB_MAINTENANCE_ITEM.
- Token/cost metering not in-process — capture from OpenAI usage for the pilot window.
- Pilot QA review of each case is temporary and must not become architectural dependency.
- Change control: KB/ontology/prompts/Claim Plan rules frozen for this release.

## Customer path (no bypass)

{' → '.join(agg.get('customer_path') or [])}

## 19. Recommendation

**{reco}**

Do not automatically expand rollout. STOP after pilot evaluation.
"""


if __name__ == "__main__":
    raise SystemExit(main())
