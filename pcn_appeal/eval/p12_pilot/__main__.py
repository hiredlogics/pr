"""python -m pcn_appeal.eval.p12_pilot

P12 controlled production pilot — preflight, freeze, photo smoke, report.
Does not redesign architecture, fine-tune, add pgvector, or broaden rollout.
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

from . import freeze as freeze_mod
from . import observability
from . import photo_smoke
from . import preflight

REPORT = ROOT / "reports" / "p12_pilot"


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
    if not pre.get("passed"):
        # Pre-pilot blockers open — do not start / continue pilot traffic.
        return "ROLLBACK_AND_FIX"
    if cohort.get("safety_incidents"):
        return "ROLLBACK_AND_FIX"
    n = int(cohort.get("n_real_cases") or 0)
    if n == 0:
        # Infrastructure ready; cohort not yet executed.
        return "PILOT_EXTENSION_REQUIRED"
    # Exit criteria for limited rollout after cohort
    unsupported = cohort.get("unsupported_assertion_rate")
    gcov = cohort.get("draft_ground_coverage")
    partic = cohort.get("required_particular_coverage")
    if (
        n >= 10
        and not cohort.get("safety_incidents")
        and unsupported == 0
        and gcov == 1.0
        and partic == 1.0
        and bool((inv or {}).get("passed"))
        and freeze.get("working_tree_clean")
    ):
        return "READY_FOR_LIMITED_ROLLOUT"
    return "PILOT_EXTENSION_REQUIRED"


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    _load_staging_env()
    config.load()

    print("P12 freeze...")
    fr = freeze_mod.write_freeze(REPORT / "freeze")
    print("  commit", fr.get("git_commit"), "kb", fr.get("kb_release_id"))

    print("Physical-photo smoke...")
    photo = photo_smoke.run(max_base=3)
    (REPORT / "photo_smoke_detail.json").write_text(
        json.dumps(photo, indent=2, default=str) + "\n", encoding="utf-8")
    print("  photo passed", photo.get("passed"), "rows", photo.get("n_rows"))

    print("Preflight blockers...")
    pre = preflight.run_all(photo)
    (REPORT / "preflight.json").write_text(
        json.dumps(pre, indent=2, default=str) + "\n", encoding="utf-8")

    print("P8/P10 invariants...")
    inv = run_p8_p10_invariants()

    # Cohort registry: empty until real customer cases are appended by ops.
    cohort_path = REPORT / "cohort_registry.json"
    if cohort_path.exists():
        cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    else:
        cohort = observability.empty_cohort_metrics()
        cohort_path.write_text(
            json.dumps(cohort, indent=2) + "\n", encoding="utf-8")

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
    (REPORT / "aggregate.json").write_text(
        json.dumps(agg, indent=2, default=str) + "\n", encoding="utf-8")

    md = _render(agg)
    (ROOT / "P12_PRODUCTION_PILOT_REPORT.md").write_text(md, encoding="utf-8")
    (REPORT / "P12_PRODUCTION_PILOT_REPORT.md").write_text(md, encoding="utf-8")
    print("recommendation:", reco)
    print("report:", ROOT / "P12_PRODUCTION_PILOT_REPORT.md")
    return 0 if reco != "ROLLBACK_AND_FIX" else 1


def _yn(v) -> str:
    return "PASS" if v else "FAIL"


def _render(agg: dict) -> str:
    fr = agg.get("freeze") or {}
    pre = agg.get("preflight") or {}
    blockers = pre.get("blockers") or {}
    cohort = agg.get("cohort") or {}
    reco = agg.get("recommendation")
    return f"""# P12 — Controlled Production Pilot

Frozen, staging-approved architecture. No redesign, fine-tune, pgvector,
silent prompt/KB edits, or automatic rollout broadening.

## 1. Pilot release versions

```json
{json.dumps(fr, indent=2, default=str)}
```

Working tree clean: **{fr.get('working_tree_clean')}**
KB release id: `{fr.get('kb_release_id')}`
KB release digest: `{fr.get('kb_release_digest')}`

## 0 / Pre-pilot blockers

| Blocker | Result |
| --- | --- |
| NO_SUPPORTED_GROUNDS state consistency | {_yn(blockers.get('nsg_state_consistent'))} |
| KB release pinned (non-null) | {_yn(blockers.get('kb_release_pinned'))} |
| Secrets scan clean | {_yn(blockers.get('secrets_scan_clean'))} |
| Physical-photo smoke safe | {_yn(blockers.get('photo_smoke_safe'))} |
| Rollback documented | {_yn(blockers.get('rollback_documented'))} |

```json
{json.dumps(pre, indent=2, default=str)}
```

## 2. Number of real cases

**{cohort.get('n_real_cases', 0)}** (cohort cap {cohort.get('cohort_cap', 20)}; auto-scale forbidden)

## 3. Case outcome distribution

```json
{json.dumps(cohort.get('outcome_distribution') or {{}}, indent=2)}
```

## 4. Extraction metrics

Photo smoke:
```json
{json.dumps(agg.get('photo_smoke_summary'), indent=2, default=str)}
```

Cohort legal-critical accuracy: {cohort.get('legal_critical_extraction_accuracy')}

## 5. Semantic metrics

Precision: {cohort.get('semantic_precision')} · Recall: {cohort.get('semantic_recall')}

## 6. Ground precision / recall

Precision: {cohort.get('ground_precision')} · Recall: {cohort.get('ground_recall')}

## 7. Drafting coverage

Ground: {cohort.get('draft_ground_coverage')} · Material: {cohort.get('material_fact_coverage')} · Particulars: {cohort.get('required_particular_coverage')} · Unsupported: {cohort.get('unsupported_assertion_rate')}

## 8. Validator results

Validation failure rate: {cohort.get('validation_failure_rate')} · Processing error rate: {cohort.get('processing_error_rate')} · NO_SUPPORTED_GROUNDS rate: {cohort.get('no_supported_grounds_rate')}

Invariants:
```json
{json.dumps(agg.get('invariants'), indent=2, default=str)}
```

## 9. Operational failures

DB failures: {cohort.get('db_failures')} · Provider failures: {cohort.get('provider_failures')} · 429 rate: {cohort.get('http_429_rate')} · 5xx rate: {cohort.get('http_5xx_rate')}

## 10. Latency

P50: {cohort.get('latency_p50_ms')} · P95: {cohort.get('latency_p95_ms')}

## 11. Cost / case

Calls/case: {cohort.get('openai_calls_per_case')} · In tokens: {cohort.get('input_tokens_per_case')} · Out tokens: {cohort.get('output_tokens_per_case')} · Cost/case: {cohort.get('cost_per_case')}

## 12. Safety incidents

```json
{json.dumps(cohort.get('safety_incidents') or [], indent=2)}
```

Safety-stop catalogue:
```json
{json.dumps((agg.get('observability_schema') or {{}}).get('safety_stops'), indent=2)}
```

## 13. Rollback events

```json
{json.dumps(cohort.get('rollback_events') or [], indent=2)}
```

Rollback procedure:
```json
{json.dumps((pre.get('rollback') or {{}}).get('procedure'), indent=2)}
```

## 14. Unresolved issues

- Real-customer cohort not yet executed (n={cohort.get('n_real_cases', 0)}). Append case records to `reports/p12_pilot/cohort_registry.json` during the pilot; do not auto-scale past the cap.
- Operator must rotate/revoke any historically exposed production/provider credentials before live traffic.
- Pilot must serve this exact freeze; behavioural changes require a new release.
- Case-quality review for the initial cohort is pilot QA only — not a permanent human-review dependency.

## Customer path (no bypass)

{' → '.join(agg.get('customer_path') or [])}

## 15. Recommendation

**{reco}**

Do not automatically expand rollout. STOP after pilot report.
"""


if __name__ == "__main__":
    raise SystemExit(main())
