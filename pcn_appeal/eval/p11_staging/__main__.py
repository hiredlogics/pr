"""python -m pcn_appeal.eval.p11_staging

P11 production-like staging validation.
Does not production deploy. Does not fine-tune. Does not add pgvector.
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
from . import live_llm
from . import migrations
from . import outcomes
from . import postgres_roundtrip
from . import provider_failures
from . import regression
from . import security

REPORT = ROOT / "reports" / "p11_staging"


def _gates(agg: dict) -> dict:
    g = {
        "p8_p10_invariants": bool((agg.get("invariants") or {}).get("passed")),
        "postgresql": bool((agg.get("postgresql") or {}).get("passed")),
        "migrations_static": bool((agg.get("migrations_static") or {}).get("passed")),
        "migrations_live": bool((agg.get("migrations_live") or {}).get("passed")),
        "live_extraction": bool((agg.get("live_extraction") or {}).get("passed")),
        "live_semantic": bool((agg.get("live_semantic") or {}).get("passed")),
        "live_drafting": bool((agg.get("live_drafting") or {}).get("passed")),
        "draft_coverage_100": (
            (agg.get("regression") or {}).get("clean_ground_coverage") == 1.0
        ),
        "provider_failures": bool((agg.get("provider_failures") or {}).get("passed")),
        "idempotency": bool((agg.get("idempotency") or {}).get("passed")),
        "outcome_consistency": bool((agg.get("outcomes") or {}).get("passed")),
        "front_back": bool((agg.get("front_back") or {}).get("passed")),
        "security_no_critical": bool((agg.get("security") or {}).get("passed")),
        "working_tree_clean_at_freeze": bool(
            (agg.get("freeze") or {}).get("working_tree_clean")
        ),
    }
    g["all_required"] = all([
        g["p8_p10_invariants"],
        g["postgresql"],
        g["migrations_static"],
        g["migrations_live"],
        g["draft_coverage_100"],
        g["provider_failures"],
        g["idempotency"],
        g["outcome_consistency"],
        g["front_back"],
        g["security_no_critical"],
        g["working_tree_clean_at_freeze"],
    ])
    return g


def _recommendation(agg: dict, gates: dict) -> str:
    if gates.get("all_required") and gates.get("live_semantic") and gates.get("live_drafting"):
        return "READY_FOR_PRODUCTION_PILOT"
    if not gates.get("postgresql") or not gates.get("migrations_live"):
        return "STAGING_FIXES_REQUIRED"
    if not gates.get("live_semantic") or not gates.get("live_drafting"):
        return "MODEL_PROVIDER_WORK_REQUIRED"
    if not gates.get("live_extraction"):
        return "EXTRACTION_WORK_REQUIRED"
    if not gates.get("all_required"):
        return "STAGING_FIXES_REQUIRED"
    return "STAGING_FIXES_REQUIRED"


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    config.load()  # OPENAI from .env; DATABASE_URL must be exported explicitly

    print("P11 freeze...")
    fr = freeze_mod.write_freeze(REPORT / "freeze")
    print("  commit", fr.get("git_commit"), "clean", fr.get("working_tree_clean"))

    print("Migration static audit...")
    mig_static = migrations.audit_static()
    print("Migration live audit...")
    mig_live = migrations.audit_live()

    print("PostgreSQL round-trip...")
    pg = postgres_roundtrip.run()

    print("Security scan...")
    sec = security.run()

    print("Front/back gates...")
    fb = outcomes.test_front_back()

    print("Outcome consistency...")
    oc = outcomes.test_outcome_consistency()

    print("Idempotency...")
    idem = outcomes.test_idempotency()

    print("Provider failure simulations...")
    pf = provider_failures.run()

    print("Live semantic probe...")
    sem = live_llm.probe_semantic()

    print("Live drafting probe...")
    draft = live_llm.probe_drafting()

    # Live extraction requires real images — mark not-run unless STAGING_EXTRACTION=1
    live_extraction = {
        "ran": False,
        "passed": False,
        "reason": (
            "No representative PDF/photo corpus wired for automated P11 extraction "
            "in this environment. Set STAGING_EXTRACTION=1 and provide sample paths "
            "to enable. Golden field injection is forbidden."
        ),
    }

    print("Staging regression set...")
    reg = regression.run(REPORT)

    print("P8/P10 invariants...")
    inv = run_p8_p10_invariants()

    agg = {
        "phase": "P11-STAGING",
        "freeze": fr,
        "migrations_static": mig_static,
        "migrations_live": mig_live,
        "postgresql": pg,
        "security": sec,
        "front_back": fb,
        "outcomes": oc,
        "idempotency": idem,
        "provider_failures": {k: v for k, v in pf.items() if k != "cases"},
        "provider_failure_cases": pf.get("cases"),
        "live_semantic": {k: v for k, v in sem.items() if k != "sample_concepts"},
        "live_drafting": {k: v for k, v in draft.items() if k != "text_preview"},
        "live_extraction": live_extraction,
        "regression": {k: v for k, v in reg.items() if k != "cases"},
        "invariants": {k: inv.get(k) for k in ("passed", "tests_run", "checks")
                       if isinstance(inv, dict)},
        "latency": {
            "regression_p50_ms": reg.get("latency_p50_ms"),
            "regression_p95_ms": reg.get("latency_p95_ms"),
            "semantic_latency_ms": sem.get("latency_ms"),
            "drafting_latency_ms": draft.get("latency_ms"),
        },
    }
    gates = _gates(agg)
    reco = _recommendation(agg, gates)
    agg["release_gates"] = gates
    agg["recommendation"] = reco

    (REPORT / "aggregate.json").write_text(
        json.dumps(agg, indent=2, default=str) + "\n", encoding="utf-8")
    # Keep case-level detail separately
    (REPORT / "regression_cases.json").write_text(
        json.dumps(reg.get("cases") or [], indent=2, default=str) + "\n", encoding="utf-8")
    (REPORT / "live_semantic_detail.json").write_text(
        json.dumps(sem, indent=2, default=str) + "\n", encoding="utf-8")
    (REPORT / "live_drafting_detail.json").write_text(
        json.dumps(draft, indent=2, default=str) + "\n", encoding="utf-8")

    md = _render(agg, gates, reco)
    (ROOT / "P11_STAGING_REPORT.md").write_text(md, encoding="utf-8")
    (REPORT / "P11_STAGING_REPORT.md").write_text(md, encoding="utf-8")
    print("recommendation:", reco)
    print("report:", ROOT / "P11_STAGING_REPORT.md")
    return 0 if reco == "READY_FOR_PRODUCTION_PILOT" else 1


def _yn(v) -> str:
    return "PASS" if v else "FAIL"


def _render(agg: dict, gates: dict, reco: str) -> str:
    fr = agg.get("freeze") or {}
    return f"""# P11-STAGING — Production-like End-to-End Validation

Validate frozen P10.6 architecture in a production-like staging environment.
No architecture redesign, fine-tune, pgvector, golden modification, or production deploy.

## 1. Staging release versions

```json
{json.dumps(fr, indent=2, default=str)}
```

Working tree clean at freeze: **{fr.get('working_tree_clean')}**

## 2. PostgreSQL result

```json
{json.dumps(agg.get('postgresql'), indent=2, default=str)}
```

## 3. Migration result

Static:
```json
{json.dumps({k: (agg.get('migrations_static') or {}).get(k) for k in
 ('passed','migration_count','numbers','duplicates','missing_numbers','ordered','rollback_docs')}, indent=2)}
```

Live:
```json
{json.dumps(agg.get('migrations_live'), indent=2, default=str)}
```

## 4. Live extraction metrics

```json
{json.dumps(agg.get('live_extraction'), indent=2, default=str)}
```

## 5. Live semantic-model metrics

```json
{json.dumps(agg.get('live_semantic'), indent=2, default=str)}
```

## 6. Live drafting metrics

```json
{json.dumps(agg.get('live_drafting'), indent=2, default=str)}
```

## 7. Provider-failure results

```json
{json.dumps(agg.get('provider_failures'), indent=2, default=str)}
```

## 8. Idempotency / concurrency results

```json
{json.dumps(agg.get('idempotency'), indent=2, default=str)}
```

## 9. Outcome-state results

```json
{json.dumps(agg.get('outcomes'), indent=2, default=str)}
```

Front/back:
```json
{json.dumps(agg.get('front_back'), indent=2, default=str)}
```

## 10. Security findings

```json
{json.dumps({k: (agg.get('security') or {}).get(k) for k in
 ('passed','files_scanned','critical_count','note','findings')}, indent=2, default=str)}
```

## 11. Latency / cost metrics

```json
{json.dumps(agg.get('latency'), indent=2, default=str)}
```

Estimated cost per completed appeal: not metered in this harness (provider dashboard).

## 12. Staging E2E / regression

```json
{json.dumps(agg.get('regression'), indent=2, default=str)}
```

Invariants:
```json
{json.dumps(agg.get('invariants'), indent=2, default=str)}
```

## Release gates

| Gate | Result |
| --- | --- |
| P8/P10 invariants | {_yn(gates.get('p8_p10_invariants'))} |
| Real PostgreSQL | {_yn(gates.get('postgresql'))} |
| Migrations static | {_yn(gates.get('migrations_static'))} |
| Migrations live | {_yn(gates.get('migrations_live'))} |
| Draft coverage 100% (clean) | {_yn(gates.get('draft_coverage_100'))} |
| Provider failures | {_yn(gates.get('provider_failures'))} |
| Idempotency | {_yn(gates.get('idempotency'))} |
| Outcome consistency | {_yn(gates.get('outcome_consistency'))} |
| Front/back | {_yn(gates.get('front_back'))} |
| Security (no critical) | {_yn(gates.get('security_no_critical'))} |
| Working tree clean | {_yn(gates.get('working_tree_clean_at_freeze'))} |
| Live semantic | {_yn(gates.get('live_semantic'))} |
| Live drafting | {_yn(gates.get('live_drafting'))} |
| Live extraction | {_yn(gates.get('live_extraction'))} |

## 13. Unresolved issues

- `DATABASE_URL` must be exported explicitly for real Postgres (not loaded from `.env` by design).
- Docker is unavailable in this agent environment; local Postgres service requires credentials.
- Representative PDF/photo extraction corpus not automated in this run.
- Live OpenAI probes run only when provider resolves to openai (not DemoLLM).

## 14. Recommendation

**{reco}**

Do not production deploy. STOP after staging review.
"""


if __name__ == "__main__":
    raise SystemExit(main())
