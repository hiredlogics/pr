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
        "release_id": release_id,
    }
