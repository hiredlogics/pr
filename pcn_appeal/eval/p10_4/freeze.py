"""Record frozen implementation identity for P10.4 (no behavioural changes)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SEMANTIC_ONTOLOGY_VERSION = "p10_3_ontology_v1"
MODULE_ROLE_VERSION = "p10_3_roles_v1"


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
    from pcn_appeal.engines.claim_plan_authority import BUILDER_VERSION
    from pcn_appeal.engines.draft_validation_engine import VERSION as DV
    from pcn_appeal.engines.validation import VERSION as VAL
    from pcn_appeal.kg.graph import KnowledgeGraph
    from pcn_appeal.module_roles import ROLE_BY_MODULE
    from pcn_appeal.semantics.ontology import CONCEPTS

    kg = KnowledgeGraph()
    role_blob = json.dumps(ROLE_BY_MODULE, sort_keys=True)
    ont_blob = json.dumps(sorted(CONCEPTS), sort_keys=True)
    return {
        "evaluation_release": "P10_4_FROZEN_2026-10-04",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "working_tree_dirty": bool(_git("status", "--porcelain")),
        "freeze_note": (
            "P10.4 evaluation freeze. Application behaviour must not change "
            "during scoring. Working tree may contain uncommitted P10.3 work."
        ),
        "kb_release": getattr(kg, "release_id", None),
        "module_count": len(kg.modules),
        "semantic_ontology_version": SEMANTIC_ONTOLOGY_VERSION,
        "semantic_ontology_digest": hashlib.sha256(ont_blob.encode()).hexdigest()[:16],
        "semantic_concept_count": len(CONCEPTS),
        "module_role_version": MODULE_ROLE_VERSION,
        "module_role_digest": hashlib.sha256(role_blob.encode()).hexdigest()[:16],
        "claim_plan_builder_version": BUILDER_VERSION,
        "master_case_schema_version": SCHEMA_VERSION,
        "validation_engine_version": VAL,
        "draft_validation_version": DV,
        "prompt_versions": {t: prompts.version(t) for t in prompts.TASKS},
        "model_provider": "ReferenceAnalysisLLM",
    }


def write_freeze(dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    block = freeze_block()
    (dest / "FREEZE.json").write_text(
        json.dumps(block, indent=2) + "\n", encoding="utf-8")
    return block
