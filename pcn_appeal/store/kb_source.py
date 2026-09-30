"""Read a published KB release out of Postgres.

Returns exactly the four dicts `kg.graph.KnowledgeGraph` builds itself from
YAML, so the graph, the predicates and every engine behave identically whether
the rules came from a file or from a table. The database is a distribution
mechanism, not a second source of logic.
"""
from __future__ import annotations

from typing import Any, Optional

from .db import connect


def latest_release_id() -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT kb_release_id FROM kb_releases ORDER BY published_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def release_differs_from_yaml(release: dict[str, Any]) -> list[str]:
    """Where the release being served and the authored YAML disagree.

    The YAML in the deployed image is what a reviewer reads and what the next
    release is built from. If it disagrees with the release actually being
    served, the running app argues law nobody on the team is looking at - and
    every case is stamped with a release id that does not describe it.

    Modules and prompts are compared because both carry a real version. Block
    text is NOT compared: no block in building_blocks.yaml declares a version, so
    `kb_sync` pins them all at "1.0" and a wording change re-syncs over the same
    row. Block drift is therefore invisible here and to replay - a separate gap,
    not one this check can honestly cover.
    """
    from .. import prompts
    from ..kg.graph import KnowledgeGraph

    out: list[str] = []
    authored = {mid: str(m.version) for mid, m in KnowledgeGraph().modules.items()}
    pinned = {m["module_id"]: str(m.get("version") or "1.0")
              for m in release["kb_modules"]["modules"]}
    for module_id in sorted(set(pinned) | set(authored)):
        served, local = pinned.get(module_id), authored.get(module_id)
        if served is None:
            out.append(f"module {module_id}@{local} is authored but not in the release")
        elif local is None:
            out.append(f"module {module_id}@{served} is in the release but no longer authored")
        elif served != local:
            out.append(f"module {module_id}: release serves {served}, YAML has {local}")

    pinned_prompts = {t: int(p["version"]) for t, p in (release.get("prompts") or {}).items()}
    authored_prompts = {t: int(p["version"]) for t, p in prompts.load().items()}
    for task in sorted(set(pinned_prompts) | set(authored_prompts)):
        served, local = pinned_prompts.get(task), authored_prompts.get(task)
        if served != local:
            out.append(f"prompt {task}: release serves {served}, YAML has {local}")
    return out


def load_release(release_id: Optional[str] = None) -> dict[str, Any]:
    """-> {"kb_modules": ..., "building_blocks": ..., "routes": ..., "questions": ...,
           "release_id": ...} in YAML-equivalent shape."""
    with connect() as conn:
        if release_id is None:
            row = conn.execute("SELECT kb_release_id, manifest FROM kb_releases "
                               "ORDER BY published_at DESC LIMIT 1").fetchone()
        else:
            row = conn.execute("SELECT kb_release_id, manifest FROM kb_releases "
                               "WHERE kb_release_id = %s", (release_id,)).fetchone()
        if row is None:
            raise RuntimeError("no KB release published; run `python -m pcn_appeal.store sync`")
        release_id, manifest = row

        # A release pins an exact version per item, so serving it never picks up
        # an in-progress edit made after publication.
        module_versions = manifest["modules"]
        block_versions = manifest["blocks"]

        modules = []
        for module_id, version in module_versions.items():
            r = conn.execute("""
                SELECT module_id, version, route, topic, use_when, do_not_use_when, core_proposition,
                       required_facts, evidence_helpful, legal_basis, drafting_notes, prohibited_claims,
                       building_blocks, strength, status, effective_from, effective_to
                FROM kb_modules WHERE module_id = %s AND version = %s
            """, (module_id, version)).fetchone()
            if r is None:
                raise RuntimeError(f"release {release_id} references missing module {module_id}@{version}")
            modules.append({
                "module_id": r[0], "version": r[1], "route": r[2], "topic": r[3],
                "use_when": r[4], "do_not_use_when": r[5], "core_proposition": r[6],
                "required_facts": r[7] or [], "evidence_helpful": r[8] or [], "legal_basis": r[9] or [],
                "drafting_notes": r[10] or "", "prohibited_claims": r[11] or [],
                "building_blocks": r[12] or [], "strength": r[13], "status": r[14],
                "effective_from": r[15], "effective_to": r[16],
            })

        # Prompts are pinned per release too, so replaying a case uses the exact
        # instructions that produced it, not whatever the file says today.
        prompts = {}
        for task, version in (manifest.get("prompts") or {}).items():
            r = conn.execute("SELECT body, version FROM prompts WHERE prompt_id = %s AND version = %s",
                             (task, version)).fetchone()
            if r is None:
                raise RuntimeError(f"release {release_id} references missing prompt {task}@{version}")
            prompts[task] = {"body": r[0], "version": r[1]}

        blocks = {}
        for block_id, version in block_versions.items():
            r = conn.execute("""
                SELECT text, requires_evidence_any, requires_facts, version
                FROM kb_blocks WHERE block_id = %s AND version = %s
            """, (block_id, version)).fetchone()
            if r is None:
                raise RuntimeError(f"release {release_id} references missing block {block_id}@{version}")
            blocks[block_id] = {"text": r[0], "requires_evidence_any": r[1] or [],
                                "requires_facts": r[2] or [], "version": r[3]}

    return {
        "kb_modules": {"modules": modules,
                       "conflicts_with": manifest.get("conflicts_with", []),
                       "legal_sources": manifest.get("legal_sources", {})},
        "building_blocks": {"blocks": blocks},
        "routes": manifest["routes"],
        "questions": manifest["questions"],
        "prompts": prompts,
        "release_id": release_id,
    }
