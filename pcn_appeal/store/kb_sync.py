"""Push the authored KB (data/*.yaml) into Postgres + pgvector.

    YAML (authored, git-versioned, legal-reviewable)
      -> kb_modules / kb_blocks        the rules themselves
      -> kb_embeddings                 pgvector + tsvector retrieval index
      -> kb_releases                   immutable {id: version} snapshot
      -> code_versions                 versioned Code provisions

A release is what the engines serve (see kb_source.py) and what a case records
in `cases.kb_release_id`, so an appeal can always be replayed against the exact
rules that produced it (KB-GOV-01).

`use_when` / `do_not_use_when` go in as jsonb and come back out unchanged -
they are evaluated by rules/dsl.py either way, so moving the KB into Postgres
does not move any decision into the database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ..rag.embedder import HashingEmbedder
from .db import EMBED_DIM, connect

DATA = Path(__file__).resolve().parent.parent / "data"


def _read(d: Path) -> dict[str, Any]:
    src = {name: yaml.safe_load((d / f"{name}.yaml").read_text())
           for name in ("kb_modules", "building_blocks", "routes", "questions", "code_versions",
                        "prompts")}
    relations = d / "kb_relations.yaml"
    src["kb_relations"] = yaml.safe_load(relations.read_text()) if relations.exists() else {}
    return src


def new_release_id() -> str:
    return "kb-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def release_manifest(src: dict[str, Any], *, embedder_id: str) -> dict[str, Any]:
    """The immutable snapshot one release pins (P7 B5). Beyond the id->version
    maps it carries the curated relations and a digest per block text, so a
    served release argues the relations it was published with and a block
    wording change is visible drift, not a silent re-sync over version 1.0."""
    modules: list[dict] = src["kb_modules"]["modules"]
    blocks: dict[str, dict] = src["building_blocks"]["blocks"]
    return {
        "modules": {m["module_id"]: str(m.get("version", "1.0")) for m in modules},
        "blocks": {bid: str(b.get("version", "1.0")) for bid, b in blocks.items()},
        "block_texts": {bid: _sha(b["text"]) for bid, b in blocks.items()},
        "prompts": {task: int(pr.get("version", 1))
                    for task, pr in (src["prompts"].get("prompts", {}) or {}).items()},
        "routes": src["routes"],
        "questions": src["questions"],
        "conflicts_with": src["kb_modules"].get("conflicts_with", []) or [],
        "legal_sources": src["kb_modules"].get("legal_sources", {}) or {},
        "relations": src.get("kb_relations") or {},
        "embedder": embedder_id,
        "embed_dim": EMBED_DIM,
    }


def sync(data_dir: Path = DATA, release_id: Optional[str] = None,
         embedder: Any = None, publish: bool = True, published_by: str = "cli") -> dict[str, Any]:
    """Upsert every module/block/code-version, rebuild the retrieval index and
    (by default) publish a release snapshot. Returns a summary dict."""
    src = _read(data_dir)
    embedder = embedder or HashingEmbedder(dim=EMBED_DIM)
    release_id = release_id or new_release_id()

    modules: list[dict] = src["kb_modules"]["modules"]
    blocks: dict[str, dict] = src["building_blocks"]["blocks"]

    # ---------------------------------------------------------------- embed
    index: list[tuple[str, str, str, str]] = []          # item_id, version, kind, text
    for m in modules:
        text = f"{m['topic']}. {m.get('core_proposition', '')} {m.get('drafting_notes', '') or ''}"
        index.append((m["module_id"], str(m.get("version", "1.0")), "module", text.strip()))
    for bid, b in blocks.items():
        index.append((bid, str(b.get("version", "1.0")), "block", b["text"]))
    vectors = embedder([t for _, _, _, t in index]) if index else []

    with connect() as conn:
        with conn.cursor() as cur:
            for m in modules:
                cur.execute("""
                    INSERT INTO kb_modules (module_id, version, route, topic, use_when, do_not_use_when,
                        core_proposition, required_facts, evidence_helpful, legal_basis, drafting_notes,
                        prohibited_claims, building_blocks, strength, status, effective_from, effective_to,
                        last_legal_review, reviewed_by, change_notes)
                    VALUES (%(module_id)s, %(version)s, %(route)s, %(topic)s, %(use_when)s, %(do_not_use_when)s,
                        %(core_proposition)s, %(required_facts)s, %(evidence_helpful)s, %(legal_basis)s,
                        %(drafting_notes)s, %(prohibited_claims)s, %(building_blocks)s, %(strength)s,
                        %(status)s, %(effective_from)s, %(effective_to)s, %(last_legal_review)s,
                        %(reviewed_by)s, %(change_notes)s)
                    ON CONFLICT (module_id, version) DO UPDATE SET
                        route = EXCLUDED.route, topic = EXCLUDED.topic,
                        use_when = EXCLUDED.use_when, do_not_use_when = EXCLUDED.do_not_use_when,
                        core_proposition = EXCLUDED.core_proposition,
                        required_facts = EXCLUDED.required_facts, evidence_helpful = EXCLUDED.evidence_helpful,
                        legal_basis = EXCLUDED.legal_basis, drafting_notes = EXCLUDED.drafting_notes,
                        prohibited_claims = EXCLUDED.prohibited_claims,
                        building_blocks = EXCLUDED.building_blocks, strength = EXCLUDED.strength,
                        status = EXCLUDED.status
                """, _module_params(m))

            for bid, b in blocks.items():
                cur.execute("""
                    INSERT INTO kb_blocks (block_id, version, text, requires_evidence_any, requires_facts, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (block_id, version) DO UPDATE SET
                        text = EXCLUDED.text,
                        requires_evidence_any = EXCLUDED.requires_evidence_any,
                        requires_facts = EXCLUDED.requires_facts, status = EXCLUDED.status
                """, (bid, str(b.get("version", "1.0")), b["text"],
                      b.get("requires_evidence_any", []) or [], b.get("requires_facts", []) or [],
                      b.get("status", "ACTIVE")))

            for (item_id, version, kind, text), vec in zip(index, vectors):
                cur.execute("""
                    INSERT INTO kb_embeddings (item_id, version, kind, embedding, tsv)
                    VALUES (%s, %s, %s, %s, to_tsvector('english', %s))
                    ON CONFLICT (item_id, version) DO UPDATE SET
                        kind = EXCLUDED.kind, embedding = EXCLUDED.embedding, tsv = EXCLUDED.tsv
                """, (item_id, version, kind, list(map(float, vec)), text))

            for cv in (src["code_versions"].get("versions", []) or []):
                cur.execute("""
                    INSERT INTO code_versions (version_id, label, effective_from, effective_to,
                        applies_to_ata, provisions, verified, verified_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (version_id) DO UPDATE SET
                        label = EXCLUDED.label, effective_from = EXCLUDED.effective_from,
                        effective_to = EXCLUDED.effective_to, applies_to_ata = EXCLUDED.applies_to_ata,
                        provisions = EXCLUDED.provisions, verified = EXCLUDED.verified
                """, (cv["version_id"], cv.get("label"), cv.get("effective_from"), cv.get("effective_to"),
                      cv.get("applies_to_ata", []) or [], _json(cv.get("provisions", {})),
                      bool(cv.get("verified", False)), cv.get("verified_by")))

            for task, pr in (src["prompts"].get("prompts", {}) or {}).items():
                cur.execute("""
                    INSERT INTO prompts (prompt_id, version, task, body, active)
                    VALUES (%s, %s, %s, %s, true)
                    ON CONFLICT (prompt_id, version) DO UPDATE SET
                        task = EXCLUDED.task, body = EXCLUDED.body, active = EXCLUDED.active
                """, (task, int(pr.get("version", 1)), task, str(pr["body"]).rstrip()))

            if publish:
                manifest = release_manifest(src, embedder_id=getattr(embedder, "id", "unknown"))
                # Insert-only (P7 B5): a release is immutable the moment it is
                # published - a case stamped with its id must always replay the
                # same rules. Re-publishing the same id is an error, never an
                # update. The schema enforces the same rule with a trigger
                # (0010), so no other code path can rewrite one either.
                held = cur.execute("SELECT 1 FROM kb_releases WHERE kb_release_id = %s",
                                   (release_id,)).fetchone()
                if held is not None:
                    raise ValueError(
                        f"KB release {release_id} is already published and immutable; "
                        "publish under a new release id")
                cur.execute("""
                    INSERT INTO kb_releases (kb_release_id, published_at, published_by, manifest)
                    VALUES (%s, now(), %s, %s)
                """, (release_id, published_by, _json(manifest)))
        conn.commit()

    return {"release_id": release_id if publish else None, "modules": len(modules),
            "blocks": len(blocks), "embeddings": len(index),
            "prompts": len(src["prompts"].get("prompts", {}) or {}),
            "embedder": getattr(embedder, "id", "unknown"), "dim": EMBED_DIM}


def _module_params(m: dict) -> dict:
    return {
        "module_id": m["module_id"], "version": str(m.get("version", "1.0")),
        "route": m["route"], "topic": m["topic"],
        "use_when": _json(m.get("use_when") or {}),
        "do_not_use_when": _json(m.get("do_not_use_when") or {}),
        "core_proposition": m.get("core_proposition", ""),
        "required_facts": m.get("required_facts", []) or [],
        "evidence_helpful": m.get("evidence_helpful", []) or [],
        "legal_basis": m.get("legal_basis", []) or [],
        "drafting_notes": m.get("drafting_notes") or "",
        "prohibited_claims": m.get("prohibited_claims", []) or [],
        "building_blocks": m.get("building_blocks", []) or [],
        "strength": int(m.get("strength", 50)), "status": m.get("status", "ACTIVE"),
        "effective_from": m.get("effective_from"), "effective_to": m.get("effective_to"),
        "last_legal_review": m.get("last_legal_review"), "reviewed_by": m.get("reviewed_by"),
        "change_notes": m.get("change_notes"),
    }


def _json(value: Any):
    from psycopg.types.json import Jsonb
    return Jsonb(value)
