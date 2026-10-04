"""python -m pcn_appeal.eval.p17_live"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reports" / "live"


def _render(report: dict) -> str:
    meta = report.get("meta") or {}
    health = meta.get("health") or {}
    summary = report.get("summary") or {}
    lat = report.get("latency") or {}
    fails = report.get("failures") or []
    critical = report.get("critical_blockers") or []
    results = report.get("results") or []
    lines = [
        "# P17 — Live Backend Full System Test",
        "",
        f"**Target:** `{meta.get('base')}`",
        f"**Recommendation:** `{report.get('recommendation')}`",
        "",
        "Dedicated `LIVE_TEST_` cases only. No production customer data modified.",
        "No deploys, no destructive SQL, no secret values in this report.",
        "",
        "## 1. Backend availability",
        "",
        f"- Root/docs/openapi reachable via discovery probes",
        f"- Environment: `{health.get('environment')}`",
        f"- App version: `{health.get('app_version')}`",
        f"- Build: `{health.get('build_id')}`",
        f"- Commit: `{health.get('commit')}`",
        "",
        "## 2. API endpoints discovered",
        "",
        f"- OpenAPI paths: **{meta.get('openapi_n_paths')}**",
        "",
        "```",
        "\n".join(meta.get("openapi_paths") or [])[:4000],
        "```",
        "",
        "## 3. Health result",
        "",
        "```json",
        json.dumps({
            k: health.get(k) for k in (
                "status", "store", "provider", "kb_release", "kb_release_digest",
                "kb_source", "modules", "vision", "prompt_versions", "models",
                "kb_drift",
            )
        }, indent=2),
        "```",
        "",
        "## 4. Auth result",
        "",
        "See AUTH_* rows in results. Admin token configured locally: "
        f"**{meta.get('admin_token_configured_locally')}**",
        "",
        "## 5. Test cases PASS/FAIL",
        "",
        f"- PASS: {summary.get('pass')}  FAIL: {summary.get('fail')}  "
        f"SKIP: {summary.get('skip')}  TOTAL: {summary.get('n_tests')}",
        "",
        "| ID | Result | Layer |",
        "| --- | --- | --- |",
    ]
    for r in results:
        lines.append(
            f"| {r.get('id')} | {r.get('result')} | {r.get('first_defective_layer') or ''} |"
        )
    lines += [
        "",
        "## 6–18. Metrics & subsystem results",
        "",
        "Per-test details are in `P17_LIVE_BACKEND_RESULTS.json`.",
        "",
        f"- Cases created (indexed): {len(report.get('cases') or [])}",
        f"- CP Plus: `{json.dumps(meta.get('cp_plus') or {}, default=str)[:1500]}`",
        "",
        "## 19. pgvector health",
        "",
        "Read-only admin searches (if token available) are under `meta.pgvector_searches` in JSON.",
        "",
        "## 20–21. Integrity / security",
        "",
        f"- Security headers sample: `{json.dumps(meta.get('security_headers_sample') or {})}`",
        "",
        "## 22. Latency",
        "",
        f"- API P50: {lat.get('api_p50_ms')} ms",
        f"- API P95: {lat.get('api_p95_ms')} ms",
        f"- Requests: {lat.get('n_requests')}",
        "",
        "## 23. Failures + first defective layer",
        "",
        "```json",
        json.dumps([
            {"id": f.get("id"), "layer": f.get("first_defective_layer"),
             "detail": f.get("detail")}
            for f in fails
        ], indent=2, default=str)[:8000],
        "```",
        "",
        "## 24. Critical blockers",
        "",
        "```json",
        json.dumps(critical, indent=2, default=str)[:5000],
        "```",
        "",
        "## 25. Recommendation",
        "",
        f"**{report.get('recommendation')}**",
        "",
        "Do not deploy. STOP after this report.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    from .suite import run
    OUT.mkdir(parents=True, exist_ok=True)
    report = run()
    (OUT / "P17_LIVE_BACKEND_RESULTS.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    md = _render(report)
    (OUT / "P17_LIVE_BACKEND_FULL_TEST_REPORT.md").write_text(md, encoding="utf-8")
    (ROOT / "P17_LIVE_BACKEND_FULL_TEST_REPORT.md").write_text(md, encoding="utf-8")
    print(json.dumps({
        "recommendation": report.get("recommendation"),
        "summary": report.get("summary"),
        "report": str(OUT / "P17_LIVE_BACKEND_FULL_TEST_REPORT.md"),
    }, indent=2))
    return 0 if report.get("recommendation") == "LIVE_BACKEND_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
