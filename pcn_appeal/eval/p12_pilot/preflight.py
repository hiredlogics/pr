"""P12 pre-pilot release blockers."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def check_nsg_state() -> dict[str, Any]:
    """NO_SUPPORTED_GROUNDS must be CaseState + outcome, not MANUAL_REVIEW."""
    from pcn_appeal.models import CaseState
    from pcn_appeal.engines.outcome import OUTCOME_NO_SUPPORTED_GROUNDS
    from support import ReferenceAnalysisLLM
    from test_scenarios import fields as extraction_fields
    from pcn_appeal.models import CaseFile, EvidenceItem
    from pcn_appeal.orchestrator import AppealPipeline

    has_enum = hasattr(CaseState, "NO_SUPPORTED_GROUNDS")
    llm = ReferenceAnalysisLLM({
        "extraction": [{
            "fields": extraction_fields(
                operator_name="O", pcn_number="N1", vrm="AA11AAA",
                parking_location="L", site_postcode="A1 1AA",
                parking_event_date="01/09/2026", notice_issue_date="05/09/2026",
                alleged_breach="Breach", operator_ata="BPA",
            ),
            "doc_types": {"E1": "PCN"},
        }],
    })
    case = CaseFile("P12_NSG", evidence={
        "E1": EvidenceItem("E1", "PCN", "n.txt", text="PARKING CHARGE NOTICE"),
    })
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    pipe.confirm(case, {}, list(case.facts), "The weather was nice that day.")
    out = pipe.generate(case)
    state = getattr(out.state, "value", out.state)
    return {
        "passed": (
            has_enum
            and state == "NO_SUPPORTED_GROUNDS"
            and out.outcome == OUTCOME_NO_SUPPORTED_GROUNDS
            and case.state == CaseState.NO_SUPPORTED_GROUNDS
        ),
        "enum_present": has_enum,
        "pipeline_state": state,
        "case_state": getattr(case.state, "value", case.state),
        "outcome": out.outcome,
        "not_manual_review": state != "MANUAL_REVIEW",
    }


def check_kb_pin() -> dict[str, Any]:
    from pcn_appeal.kg.graph import KnowledgeGraph
    kg = KnowledgeGraph()
    rid = kg.release_id
    digest = kg.release_digest
    return {
        "passed": bool(rid) and bool(digest) and rid is not None,
        "kb_release_id": rid,
        "kb_release_digest": digest,
        "null_forbidden": True,
    }


def check_secrets() -> dict[str, Any]:
    """Static scan: no live API key literals in tracked source."""
    findings = []
    pat = re.compile(
        r"(sk-[A-Za-z0-9]{20,}|npg_[A-Za-z0-9]{16,}|OPENAI_API_KEY\s*=\s*['\"]sk-)",
    )
    scanned = 0
    for base in (ROOT / "pcn_appeal", ROOT / "frontend" / "lib", ROOT / "infra"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".sql", ".md", ".json"}:
                continue
            if "node_modules" in path.parts or "reports" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            scanned += 1
            if pat.search(text):
                findings.append(str(path.relative_to(ROOT)))
    return {
        "passed": len(findings) == 0,
        "files_scanned": scanned,
        "critical_count": len(findings),
        "findings": findings[:20],
        "note": (
            "Rotate/revoke any historically exposed production/provider "
            "credentials before pilot traffic. This scan only covers the repo."
        ),
        "operator_action_required": True,
    }


def check_rollback_docs() -> dict[str, Any]:
    """Rollback procedure must be documented and append-only history preserved."""
    mig = ROOT / "infra" / "migrations"
    files = sorted(mig.glob("*.sql")) if mig.exists() else []
    with_rollback = sum(
        1 for p in files
        if "rollback" in p.read_text(encoding="utf-8", errors="ignore").lower()
    )
    return {
        "passed": len(files) >= 12 and with_rollback >= 8,
        "migration_count": len(files),
        "with_rollback_section": with_rollback,
        "procedure": (
            "1) Redeploy previous git commit / deployment_version. "
            "2) Keep DATABASE_URL pointing at compatible schema (migrations "
            "append-only; do not DROP history tables). "
            "3) Pin previous prompts via published KB release or prior "
            "yaml-{digest} pin. "
            "4) Do not UPDATE/DELETE append-only fact_history / claim_plans / "
            "draft_versions (triggers block). "
            "5) Verify /health commit + kb_release match the prior freeze."
        ),
    }


def run_all(photo_result: dict | None = None) -> dict[str, Any]:
    nsg = check_nsg_state()
    kb = check_kb_pin()
    sec = check_secrets()
    rb = check_rollback_docs()
    photo = photo_result or {"passed": False, "ran": False}
    blockers = {
        "nsg_state_consistent": nsg["passed"],
        "kb_release_pinned": kb["passed"],
        "secrets_scan_clean": sec["passed"],
        "photo_smoke_safe": bool(photo.get("passed")),
        "rollback_documented": rb["passed"],
    }
    return {
        "passed": all(blockers.values()),
        "blockers": blockers,
        "nsg": nsg,
        "kb_pin": kb,
        "secrets": sec,
        "rollback": rb,
        "photo_smoke": {
            "passed": photo.get("passed"),
            "ran": photo.get("ran"),
            "legal_critical_accuracy_mean": photo.get("legal_critical_accuracy_mean"),
            "n_passed": photo.get("n_passed"),
            "n_rows": photo.get("n_rows"),
        },
    }
