"""Deep trace of live T07 case — admin API + PostgreSQL. Never prints secrets."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import _env
from .client import DEFAULT_BASE, LiveClient

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reports" / "live"
CASE_ID = "1341a256-8de3-430e-8f53-106467fa78fd"


def _stage(audit: list[dict], names: tuple[str, ...]) -> str:
    events = {a.get("event") for a in audit}
    for n in names:
        if n in events:
            # look for error sibling
            for a in audit:
                if a.get("event") == n and (
                    a.get("error") or a.get("failed") or str(a.get("status", "")).upper() == "FAIL"
                ):
                    return "FAILED"
            return "COMPLETED"
    # failure events
    for n in names:
        err = f"{n}_error" if not n.endswith("_error") else n
        if err in events or any(
            a.get("event") == n and a.get("error") for a in audit
        ):
            return "FAILED"
    return "NOT_REACHED"


def run() -> dict[str, Any]:
    flags = _env.load_env()
    client = LiveClient(DEFAULT_BASE, admin_headers=_env.admin_headers())
    report: dict[str, Any] = {
        "case_id": CASE_ID,
        "admin_configured": bool(client.admin_headers),
        "env_keys_present": flags,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not client.admin_headers:
        report["error"] = "admin token not configured"
        return report

    endpoints = {
        "status": f"/cases/{CASE_ID}",
        "console": f"/admin/cases/{CASE_ID}/console",
        "claim_plans": f"/admin/cases/{CASE_ID}/claim-plans",
        "claim_explain": f"/admin/cases/{CASE_ID}/claim-plans/explain",
        "audit": f"/admin/cases/{CASE_ID}/audit",
        "trace": f"/cases/{CASE_ID}/trace",
        "execution_trace": f"/admin/cases/{CASE_ID}/execution-trace",
        "facts": f"/cases/{CASE_ID}/facts",
    }
    bodies: dict[str, Any] = {}
    for key, path in endpoints.items():
        r = client.get(path, admin=True, timeout=120)
        bodies[key] = {
            "status": r["status"], "ok": r["ok"], "ms": r["ms"],
            "body": r.get("body"),
        }
        print(f"  {key}: {r['status']} {r['ms']}ms", flush=True)

    console = bodies.get("console", {}).get("body") or {}
    audit_body = bodies.get("audit", {}).get("body") or {}
    audit = []
    if isinstance(audit_body, dict):
        audit = audit_body.get("audit") or audit_body.get("events") or []
    if not audit and isinstance(console, dict):
        audit = console.get("audit") or []

    # Stage map
    stages = {
        "extraction": _stage(audit, ("upload_read", "intake", "extraction", "service_facts")),
        "customer_narrative": _stage(audit, ("confirm", "narrative", "material_account")),
        "semantic_concepts": _stage(audit, ("semantic_extraction", "semantic_promote", "semantic_concepts")),
        "narrative_atoms": _stage(audit, ("narrative_atom",)),
        "facts": "COMPLETED" if any(a.get("event") in ("fact_applied", "fact_write", "confirm") for a in audit) or bodies.get("facts", {}).get("ok") else "NOT_REACHED",
        "legal_findings": _stage(audit, ("legal_findings", "pofa_findings", "legal_finding")),
        "kb_eligibility": _stage(audit, ("case_analysis", "case_analysis_completed", "eligibility")),
        "claim_plan": _stage(audit, ("claim_plan", "claim_plan_locked")),
        "draft_plan": _stage(audit, ("draft_plan",)),
        "drafting": _stage(audit, ("draft", "drafted", "draft_error", "llm_draft")),
        "validation": _stage(audit, ("validation",)),
        "release_gate": _stage(audit, ("release_metadata_incomplete", "release_gate")),
        "outcome": _stage(audit, ("customer_outcome", "outcome")),
    }
    # Refine drafting failure
    if any(a.get("event") == "draft_error" for a in audit):
        stages["drafting"] = "FAILED"
    if any(a.get("event") == "case_analysis_error" for a in audit):
        stages["kb_eligibility"] = "FAILED"

    # Extract key artefacts
    facts = {}
    fb = bodies.get("facts", {}).get("body")
    if isinstance(fb, dict):
        for name in (
            "purpose_of_visit", "left_site", "returned_same_day", "multiple_visits",
            "departure_reason", "notice_sides_complete", "pcn_number", "operator_name",
        ):
            node = (fb.get("facts") or fb.get("graph") or {}).get(name) if isinstance(fb.get("facts"), dict) else None
            if node is None and name in fb:
                node = fb[name]
            facts[name] = node

    # Also scan console
    if isinstance(console, dict):
        for name in list(facts):
            if facts[name] is None and name in (console.get("facts") or {}):
                facts[name] = console["facts"][name]

    atoms = [a for a in audit if a.get("event") == "narrative_atom"]
    draft_errors = [a for a in audit if a.get("event") in (
        "draft_error", "section_regeneration_error", "pdf_render_failed", "no_ground",
        "no_ground_after_widen", "release_metadata_incomplete", "validation",
    )]
    validations = [a for a in audit if a.get("event") == "validation"]

    claim = bodies.get("claim_plans", {}).get("body")
    explain = bodies.get("claim_explain", {}).get("body")

    # PostgreSQL direct (Railway DATABASE_URL from .env.p17.local)
    db_snap: dict[str, Any] = {"ran": False}
    try:
        from pcn_appeal.store import db
        if db.enabled():
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT case_id, state, driver_status, kb_release_id, "
                    "release_metadata::text "
                    "FROM cases WHERE case_id = %s",
                    (CASE_ID,),
                ).fetchone()
                if row:
                    db_snap = {
                        "ran": True,
                        "state": row[1],
                        "driver_status": row[2],
                        "kb_release_id": row[3],
                        "release_metadata": row[4],
                    }
                fact_rows = conn.execute(
                    "SELECT fact_name, fact_value::text, source_type, status "
                    "FROM facts WHERE case_id = %s "
                    "AND fact_name IN ('purpose_of_visit','left_site','returned_same_day',"
                    "'multiple_visits','departure_reason','notice_sides_complete') "
                    "ORDER BY fact_name",
                    (CASE_ID,),
                ).fetchall()
                db_snap["facts"] = [
                    {"name": r[0], "value": r[1], "source_type": r[2], "status": r[3]}
                    for r in fact_rows
                ]
                # claim plans
                try:
                    cps = conn.execute(
                        "SELECT claim_plan_id, status, "
                        "coalesce(plan_json::text, payload::text, '{}') "
                        "FROM claim_plans WHERE case_id = %s ORDER BY created_at DESC NULLS LAST LIMIT 3",
                        (CASE_ID,),
                    ).fetchall()
                    db_snap["claim_plans"] = [
                        {"id": str(r[0]), "status": r[1], "preview": (r[2] or "")[:2000]}
                        for r in cps
                    ]
                except Exception as exc:
                    db_snap["claim_plans_error"] = f"{type(exc).__name__}: {exc}"[:200]
                # draft versions
                try:
                    dvs = conn.execute(
                        "SELECT draft_id, released, "
                        "coalesce(validation_passed::text,''), "
                        "coalesce(issues::text, validation_issues::text, '') "
                        "FROM draft_versions WHERE case_id = %s "
                        "ORDER BY created_at DESC NULLS LAST LIMIT 5",
                        (CASE_ID,),
                    ).fetchall()
                    db_snap["draft_versions"] = [
                        {"id": str(r[0]), "released": r[1], "validation_passed": r[2],
                         "issues_preview": (r[3] or "")[:800]}
                        for r in dvs
                    ]
                except Exception as exc:
                    db_snap["draft_versions_error"] = f"{type(exc).__name__}: {exc}"[:200]
    except Exception as exc:
        db_snap = {"ran": False, "error": f"{type(exc).__name__}: {exc}"[:300]}

    # Determine first defective layer
    layer = "INFRASTRUCTURE_ERROR"
    internal_error = None
    for a in audit:
        if a.get("event") == "draft_error":
            layer = "DRAFT_RENDERING_ERROR"
            internal_error = a
            break
        if a.get("event") == "release_metadata_incomplete":
            layer = "RELEASE_GATE_ERROR"
            internal_error = a
            break
        if a.get("event") == "case_analysis_error":
            layer = "GROUND_SELECTION_ERROR"
            internal_error = a
            break
    if layer == "INFRASTRUCTURE_ERROR":
        for a in reversed(validations):
            if a.get("passed") is False:
                layer = "VALIDATION_ERROR"
                internal_error = a
                break
    # If claim plan missing entirely
    if not claim and stages.get("claim_plan") == "NOT_REACHED" and stages.get("drafting") != "FAILED":
        if stages.get("kb_eligibility") == "FAILED":
            layer = "GROUND_SELECTION_ERROR"

    report.update({
        "stages": stages,
        "facts_api": facts,
        "narrative_atoms_audit": atoms[:10],
        "draft_related_audit": draft_errors[:20],
        "validations": validations[-5:],
        "claim_plans": claim,
        "claim_explain": explain,
        "console_keys": sorted(console.keys()) if isinstance(console, dict) else [],
        "db": db_snap,
        "first_defective_layer": layer,
        "internal_error": internal_error,
        "audit_event_counts": {},
    })
    counts: dict[str, int] = {}
    for a in audit:
        e = a.get("event") or "?"
        counts[e] = counts.get(e, 0) + 1
    report["audit_event_counts"] = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    report["audit_tail"] = audit[-40:]
    return report


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Tracing T07...", flush=True)
    report = run()
    path = OUT / "P17_3_T07_TRACE.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "case_id": report.get("case_id"),
        "admin_configured": report.get("admin_configured"),
        "first_defective_layer": report.get("first_defective_layer"),
        "stages": report.get("stages"),
        "db_ran": (report.get("db") or {}).get("ran"),
        "report": str(path),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
