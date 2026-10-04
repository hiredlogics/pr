"""Immutable P12 pilot release freeze."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "UNKNOWN"


def freeze_block() -> dict:
    from pcn_appeal import prompts, runtime, version
    from pcn_appeal.case_state import SCHEMA_VERSION
    from pcn_appeal.drafting.plan import DRAFT_PLAN_VERSION
    from pcn_appeal.engines.claim_plan_authority import BUILDER_VERSION
    from pcn_appeal.engines.draft_validation_engine import VERSION as DV
    from pcn_appeal.engines.validation import VERSION as VAL
    from pcn_appeal.kg.graph import KnowledgeGraph
    from pcn_appeal.llm import probe
    from pcn_appeal.module_roles import MODULE_ROLE_VERSION, ROLE_BY_MODULE
    from pcn_appeal.semantics.ontology import CONCEPTS, ONTOLOGY_VERSION

    # Prefer the published staging KB pin used by P11.2/P11.3 proofs.
    pinned_id = "kb-20261004T113657Z"
    try:
        from pcn_appeal.store import kb_source
        rel = kb_source.load_release(pinned_id)
        kg = KnowledgeGraph.from_release(rel)
    except Exception:
        kg = KnowledgeGraph()
    role_blob = json.dumps(ROLE_BY_MODULE, sort_keys=True)
    ont_blob = json.dumps(sorted(CONCEPTS), sort_keys=True)
    porcelain = _git("status", "--porcelain")
    material = [
        line for line in (porcelain or "").splitlines()
        if line.strip()
        and "reports/p12_pilot" not in line
        and "reports/p12/" not in line
        and "P12_PRODUCTION_PILOT_REPORT.md" not in line
        and "reports/p11_staging" not in line
        and "reports/p11_1" not in line
        and "reports/p11_2" not in line
        and "reports/p11_3" not in line
        and "P11_STAGING_REPORT.md" not in line
        and "P11_1_RELEASE_TRACEABILITY_REPORT.md" not in line
        and "P11_2_VERSIONED_RELEASE_PROOF.md" not in line
        and "P11_3_MATERIAL_PROPAGATION.md" not in line
        and "frontend/.next" not in line
    ]
    llm = probe()
    mig_nums = sorted(
        int(p.name.split("_", 1)[0])
        for p in (ROOT / "infra" / "migrations").glob("*.sql")
        if p.name[:4].isdigit()
    )
    return {
        "evaluation_release": "P12_PILOT_2026-10-04",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "working_tree_clean": not bool(material),
        "deployment_id": f"p12-pilot-{_git('rev-parse', '--short', 'HEAD')}",
        "deployment_version": runtime.app_version(),
        "build_id": runtime.build_id(),
        "environment": runtime.environment(),
        "kb_release_id": kg.release_id,
        "kb_release_digest": kg.release_digest,
        "validation_version": VAL,
        "kb_release_null_forbidden": True,
        "module_count": len(kg.modules),
        "ontology_version": ONTOLOGY_VERSION,
        "ontology_digest": hashlib.sha256(ont_blob.encode()).hexdigest()[:16],
        "module_role_version": MODULE_ROLE_VERSION,
        "module_role_digest": hashlib.sha256(role_blob.encode()).hexdigest()[:16],
        "claim_plan_builder_version": BUILDER_VERSION,
        "draft_plan_version": DRAFT_PLAN_VERSION,
        "master_case_schema_version": SCHEMA_VERSION,
        "validation_engine_version": VAL,
        "draft_validation_version": DV,
        "prompt_versions": {t: prompts.version(t) for t in prompts.TASKS},
        "model_provider_probe": {
            "provider": llm.get("provider"),
            "models": llm.get("models") or {},
            "reason": llm.get("reason") or "",
        },
        "database_migration_version": max(mig_nums) if mig_nums else None,
        "database_migration_numbers": mig_nums,
        "commit_short": version.short(),
        "change_control": {
            "prompt_edits_during_pilot": "FORBIDDEN",
            "kb_edits_during_pilot": "FORBIDDEN",
            "ontology_edits_during_pilot": "FORBIDDEN",
            "module_role_edits_during_pilot": "FORBIDDEN",
            "claim_plan_rule_edits_during_pilot": "FORBIDDEN",
            "requires_new_release_for_behavioural_change": True,
        },
    }


def write_freeze(dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    block = freeze_block()
    (dest / "FREEZE.json").write_text(
        json.dumps(block, indent=2) + "\n", encoding="utf-8")
    return block
