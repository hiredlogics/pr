"""P11 staging release freeze — identity of what is under validation."""
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
    from pcn_appeal import prompts
    from pcn_appeal.case_state import SCHEMA_VERSION
    from pcn_appeal.drafting.plan import DRAFT_PLAN_VERSION
    from pcn_appeal.engines.claim_plan_authority import BUILDER_VERSION
    from pcn_appeal.engines.draft_validation_engine import VERSION as DV
    from pcn_appeal.engines.validation import VERSION as VAL
    from pcn_appeal.kg.graph import KnowledgeGraph
    from pcn_appeal.llm import probe
    from pcn_appeal.module_roles import MODULE_ROLE_VERSION, ROLE_BY_MODULE
    from pcn_appeal.semantics.ontology import CONCEPTS, ONTOLOGY_VERSION

    kg = KnowledgeGraph()
    role_blob = json.dumps(ROLE_BY_MODULE, sort_keys=True)
    ont_blob = json.dumps(sorted(CONCEPTS), sort_keys=True)
    porcelain = _git("status", "--porcelain")
    # Allow local staging report artefacts without failing the freeze gate.
    material = [
        line for line in (porcelain or "").splitlines()
        if line.strip()
        and "reports/p11_staging" not in line
        and "P11_STAGING_REPORT.md" not in line
    ]
    dirty = bool(material)
    llm = probe()
    return {
        "evaluation_release": "P11_STAGING_2026-10-04",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "working_tree_clean": not dirty,
        "working_tree_dirty": dirty,
        "working_tree_ignored_for_freeze": [
            "reports/p11_staging/**", "P11_STAGING_REPORT.md",
        ],
        "kb_release": getattr(kg, "release_id", None),
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
    }


def write_freeze(dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    block = freeze_block()
    (dest / "FREEZE.json").write_text(
        json.dumps(block, indent=2) + "\n", encoding="utf-8")
    return block
