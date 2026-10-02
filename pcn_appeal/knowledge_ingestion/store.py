"""Postgres knowledge store: import and governance (0005_knowledge_graph.sql).

    ingest(path)        parse -> extract -> graph -> drift -> write.
                        Idempotent: an import whose inputs (document hash,
                        compiled KB digest, relations version, parser version)
                        match an existing release writes nothing and returns
                        that release. Any change is a NEW release, with the
                        previous one as its parent and a snapshot of what it
                        contained, so two versions can be compared.

Governance (every change: user, timestamp, reason -> knowledge_changes):
    list_modules / get_module          view, search
    activate_module / disable_module   status only; content comes from the document
    create_edge / remove_edge          ADMIN relationships only - an ingested
                                       relationship comes from the document or
                                       the compiled gate and is changed there
    list_releases / compare_releases / relationship_changes
    release_for_digest                 which knowledge release a case ran on

NOT live: reasoning reads kb_modules.yaml or a published kb_release. Nothing
here changes what a case argues.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..store.db import connect
from .extract import ModuleRecord, extract
from .graph import INGESTED_ORIGINS, GraphEdge, GraphNode, build_graph, knowledge_id, uid
from .parser import PARSER_VERSION, parse
from . import drift as drift_mod

DEFAULT_DOCUMENT = (Path(__file__).resolve().parent.parent / "data"
                    / "Private_Parking_AI_Legal_Knowledge_Base_COMPLETE_V2.docx")


class KnowledgeChangeError(ValueError):
    """A change the knowledge store refuses."""


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _who(changed_by: Optional[str], reason: Optional[str]) -> tuple[str, str]:
    who, why = (changed_by or "").strip(), (reason or "").strip()
    if not who:
        raise KnowledgeChangeError("changed_by is required for every knowledge change")
    if not why:
        raise KnowledgeChangeError("reason is required for every knowledge change")
    return who, why


def _ts(v):
    return v.isoformat() if v is not None and not isinstance(v, str) else v


def _log(conn, entity_type, entity_id, action, version, who, why, before, after, at) -> str:
    change_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO knowledge_changes (change_id, entity_type, entity_id, action, version, "
        "changed_by, changed_at, reason, before, after) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (change_id, entity_type, entity_id, action, str(version), who, at, why,
         None if before is None else _json(before), None if after is None else _json(after)))
    return change_id


def _rows(conn, sql, params=()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


_MODULE_COLS = ("knowledge_id", "module_id", "name", "category", "version", "status",
                "effective_from", "effective_to", "source_document", "source_reference",
                "source_hash", "release_id", "metadata", "created_at", "updated_at", "updated_by")
_EDGE_COLS = ("edge_id", "source_node", "relationship_type", "target_node", "confidence",
              "origin", "status", "metadata", "updated_by", "updated_at")


def _module_row(r) -> dict:
    d = dict(zip(_MODULE_COLS, r))
    for k in ("effective_from", "effective_to", "created_at", "updated_at"):
        d[k] = _ts(d[k])
    d["release_id"] = None if d["release_id"] is None else str(d["release_id"])
    d["knowledge_id"] = str(d["knowledge_id"])
    return d


def _edge_row(r) -> dict:
    d = dict(zip(_EDGE_COLS, r))
    d["confidence"] = float(d["confidence"])
    d["updated_at"] = _ts(d["updated_at"])
    for k in ("edge_id", "source_node", "target_node"):
        d[k] = str(d[k])
    return d


def _get_module(conn, module_id) -> Optional[dict]:
    r = conn.execute(f"SELECT {', '.join(_MODULE_COLS)} FROM knowledge_modules WHERE module_id = %s",
                     (module_id,)).fetchone()
    return _module_row(r) if r else None


# ------------------------------------------------------------------ module writes
def _children(rec: ModuleRecord) -> dict[str, list[tuple]]:
    kid = knowledge_id(rec.module_id)
    return {
        "knowledge_rules": [
            (uid("rule", rec.module_id, r["rule_type"], r["rule_definition"]), kid,
             r["rule_type"], _json(r["rule_definition"])) for r in rec.rules],
        "knowledge_required_facts": [
            (uid("fact", rec.module_id, f["fact_name"], f["requirement_type"]), kid,
             f["fact_name"], f["requirement_type"], _json(f["metadata"]))
            for f in rec.required_facts],
        "knowledge_evidence_requirements": [
            (uid("evidence", rec.module_id, e["evidence_type"]), kid, e["evidence_type"],
             _json(e["requirement"])) for e in rec.evidence],
        "knowledge_restrictions": [
            (uid("restriction", rec.module_id, x["restriction_type"], x["content"]), kid,
             x["restriction_type"], x["content"], _json(x["metadata"])) for x in rec.restrictions],
    }


_CHILD_COLS = {
    "knowledge_rules": ("rule_id", "knowledge_id", "rule_type", "rule_definition"),
    "knowledge_required_facts": ("id", "knowledge_id", "fact_name", "requirement_type", "metadata"),
    "knowledge_evidence_requirements": ("id", "knowledge_id", "evidence_type", "requirement"),
    "knowledge_restrictions": ("id", "knowledge_id", "restriction_type", "content", "metadata"),
}


def _write_children(conn, rec: ModuleRecord) -> None:
    kid = knowledge_id(rec.module_id)
    for table, rows in _children(rec).items():
        conn.execute(f"DELETE FROM {table} WHERE knowledge_id = %s", (kid,))
        cols = _CHILD_COLS[table]
        for row in rows:
            conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) "
                         f"VALUES ({', '.join(['%s'] * len(cols))})", row)


def _module_summary(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    return {k: row.get(k) for k in ("module_id", "name", "category", "version", "status",
                                    "source_document", "source_hash")}


# ------------------------------------------------------------------ ingest
def ingest(path: Path = DEFAULT_DOCUMENT, *, created_by: str, reason: str, kg=None) -> dict:
    who, why = _who(created_by, reason)
    from ..kg.graph import KnowledgeGraph
    from ..manifest import kb_digest
    kg = kg or KnowledgeGraph()
    doc = parse(path)
    relations = kg.relations
    records = extract(doc, kg)
    nodes, edges = build_graph(records, relations, doc)
    drift = drift_mod.report(doc, records, kg)
    digest = kb_digest(kg)
    key = (doc.source_hash, digest, relations.version, PARSER_VERSION)
    at = _now()

    with connect() as conn:
        hit = conn.execute(
            "SELECT release_id, module_count, relationship_count FROM knowledge_release WHERE "
            "source_document_hash = %s AND compiled_digest = %s AND relations_version = %s "
            "AND parser_version = %s", key).fetchone()
        if hit:
            return {"release_id": str(hit[0]), "created": False, "module_count": hit[1],
                    "relationship_count": hit[2], "node_count": len(nodes),
                    "changes": {}, "drift": drift_mod.summary(drift)}

        parent = conn.execute("SELECT release_id FROM knowledge_release "
                              "ORDER BY created_at DESC LIMIT 1").fetchone()
        release_id = str(uuid.uuid4())
        manifest = {"modules": {r.module_id: r.content_hash for r in sorted(
                        records, key=lambda r: r.module_id)},
                    "edges": [e.edge_id for e in edges],
                    "document": {"title": doc.title, "version": doc.version,
                                 "sections": doc.sections, "building_block_ids": sorted(doc.block_ids)}}
        conn.execute(
            "INSERT INTO knowledge_release (release_id, source_document, source_document_hash, "
            "compiled_digest, relations_version, parser_version, document_version, "
            "parent_release_id, created_at, created_by, reason, module_count, relationship_count, "
            "manifest, drift) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (release_id, doc.source_document, doc.source_hash, digest, relations.version,
             PARSER_VERSION, doc.version, str(parent[0]) if parent else None, at, who, why,
             len(records), len(edges), _json(manifest), _json(drift)))
        changes = {"modules_created": 0, "modules_updated": 0, "modules_retired": 0,
                   "edges_added": 0, "edges_removed": 0, "nodes_added": 0}

        # ---- modules
        for rec in records:
            before = _get_module(conn, rec.module_id)
            if before is not None and before["source_hash"] == rec.content_hash:
                continue
            # A governance decision outlives a content change: an admin who
            # disabled a module keeps it disabled until they activate it.
            status = (before["status"] if before is not None
                      and before["status"] in ("DISABLED",) else rec.status)
            vals = (rec.name, rec.category, rec.version, status, rec.effective_from,
                    rec.effective_to, rec.source_document, rec.source_reference, rec.content_hash,
                    release_id, _json(rec.metadata), at, who)
            if before is None:
                conn.execute(
                    "INSERT INTO knowledge_modules (knowledge_id, module_id, name, category, version, "
                    "status, effective_from, effective_to, source_document, source_reference, "
                    "source_hash, release_id, metadata, created_at, updated_at, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (knowledge_id(rec.module_id), rec.module_id, *vals[:-2], at, at, who))
                changes["modules_created"] += 1
                action = "CREATE"
            else:
                conn.execute(
                    "UPDATE knowledge_modules SET name = %s, category = %s, version = %s, status = %s, "
                    "effective_from = %s, effective_to = %s, source_document = %s, "
                    "source_reference = %s, source_hash = %s, release_id = %s, metadata = %s, "
                    "updated_at = %s, updated_by = %s WHERE module_id = %s", (*vals, rec.module_id))
                changes["modules_updated"] += 1
                action = "UPDATE"
            _write_children(conn, rec)
            _log(conn, "MODULE", rec.module_id, action, rec.version, who, why,
                 _module_summary(before), {**_module_summary({**rec.as_dict(), "status": status})},
                 at)
        wanted = {r.module_id for r in records}
        for (mid, status, version) in _rows(conn, "SELECT module_id, status, version FROM knowledge_modules"):
            if mid not in wanted and status != "RETIRED":
                conn.execute("UPDATE knowledge_modules SET status = 'RETIRED', release_id = %s, "
                             "updated_at = %s, updated_by = %s WHERE module_id = %s",
                             (release_id, at, who, mid))
                _log(conn, "MODULE", mid, "RETIRE", version, who, why, {"status": status},
                     {"status": "RETIRED"}, at)
                changes["modules_retired"] += 1

        # ---- nodes
        have_nodes = {str(r[0]): r[1] for r in _rows(conn, "SELECT node_id, metadata FROM graph_nodes")}
        for n in nodes:
            if n.node_id not in have_nodes:
                conn.execute("INSERT INTO graph_nodes (node_id, node_type, entity_id, metadata) "
                             "VALUES (%s, %s, %s, %s)",
                             (n.node_id, n.node_type, n.entity_id, _json(n.metadata)))
                changes["nodes_added"] += 1
            elif have_nodes[n.node_id] != n.metadata:
                conn.execute("UPDATE graph_nodes SET metadata = %s WHERE node_id = %s",
                             (_json(n.metadata), n.node_id))

        # ---- edges: ingested ones follow the inputs; ADMIN ones are left alone
        have = {str(r[0]): (r[1], r[2]) for r in _rows(
            conn, "SELECT edge_id, origin, status FROM graph_edges")}
        desired = {e.edge_id: e for e in edges}
        for eid, e in desired.items():
            if eid not in have:
                conn.execute(
                    "INSERT INTO graph_edges (edge_id, source_node, relationship_type, target_node, "
                    "confidence, origin, status, metadata, updated_by, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s, %s)",
                    (e.edge_id, e.source_node, e.relationship_type, e.target_node, e.confidence,
                     e.origin, _json(e.metadata), who, at))
                changes["edges_added"] += 1
            elif have[eid][1] == "REMOVED":
                conn.execute("UPDATE graph_edges SET status = 'ACTIVE', updated_by = %s, "
                             "updated_at = %s WHERE edge_id = %s", (who, at, eid))
                changes["edges_added"] += 1
        for eid, (origin, status) in have.items():
            if origin in INGESTED_ORIGINS and eid not in desired and status != "REMOVED":
                conn.execute("UPDATE graph_edges SET status = 'REMOVED', updated_by = %s, "
                             "updated_at = %s WHERE edge_id = %s", (who, at, eid))
                changes["edges_removed"] += 1

        # ---- what this release contained, for compare
        labels = {n.node_id: f"{n.node_type}:{n.metadata.get('key', n.entity_id)}" for n in nodes}
        for rec in records:
            conn.execute("INSERT INTO knowledge_release_items (release_id, item_type, item_id, "
                         "content_hash, content) VALUES (%s, 'MODULE', %s, %s, %s)",
                         (release_id, rec.module_id, rec.content_hash, _json(rec.as_dict())))
        for e in edges:
            conn.execute("INSERT INTO knowledge_release_items (release_id, item_type, item_id, "
                         "content_hash, content) VALUES (%s, 'EDGE', %s, %s, %s)",
                         (release_id, e.edge_id, e.edge_id, _json({
                             "source": labels.get(e.source_node), "relationship": e.relationship_type,
                             "target": labels.get(e.target_node), "origin": e.origin,
                             "confidence": e.confidence, "metadata": e.metadata})))
        _log(conn, "RELEASE", release_id, "IMPORT", doc.version, who, why, None,
             {"source_document": doc.source_document, "source_document_hash": doc.source_hash,
              "compiled_digest": digest, "changes": changes,
              "drift": drift_mod.summary(drift)}, at)
        conn.commit()

    return {"release_id": release_id, "created": True, "module_count": len(records),
            "relationship_count": len(edges), "node_count": len(nodes), "changes": changes,
            "drift": drift_mod.summary(drift)}


# ------------------------------------------------------------------ governance: modules
def list_modules(q: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list[dict]:
    where, params = [], []
    if category:
        where.append("category = %s"), params.append(category.upper())
    if status:
        where.append("status = %s"), params.append(status.upper())
    sql = (f"SELECT {', '.join(_MODULE_COLS)} FROM knowledge_modules"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY module_id")
    with connect() as conn:
        rows = [_module_row(r) for r in _rows(conn, sql, tuple(params))]
        if not q:
            return rows
        needle = q.lower()
        rule_text: dict[str, str] = {}
        for kid, definition in _rows(conn, "SELECT knowledge_id, rule_definition FROM knowledge_rules"):
            rule_text[str(kid)] = rule_text.get(str(kid), "") + json.dumps(definition).lower()
    return [r for r in rows if needle in r["module_id"].lower() or needle in r["name"].lower()
            or needle in json.dumps(r["metadata"]).lower()
            or needle in rule_text.get(r["knowledge_id"], "")]


def get_module(module_id: str) -> dict:
    with connect() as conn:
        m = _get_module(conn, module_id)
        if m is None:
            raise KnowledgeChangeError(f"unknown module {module_id}")
        kid = m["knowledge_id"]
        m["rules"] = [{"rule_type": t, "rule_definition": d} for t, d in _rows(
            conn, "SELECT rule_type, rule_definition FROM knowledge_rules WHERE knowledge_id = %s "
                  "ORDER BY rule_type", (kid,))]
        m["required_facts"] = [{"fact_name": f, "requirement_type": t, "metadata": md} for f, t, md in _rows(
            conn, "SELECT fact_name, requirement_type, metadata FROM knowledge_required_facts "
                  "WHERE knowledge_id = %s ORDER BY requirement_type, fact_name", (kid,))]
        m["evidence"] = [{"evidence_type": t, "requirement": r} for t, r in _rows(
            conn, "SELECT evidence_type, requirement FROM knowledge_evidence_requirements "
                  "WHERE knowledge_id = %s ORDER BY evidence_type", (kid,))]
        m["restrictions"] = [{"restriction_type": t, "content": c} for t, c in _rows(
            conn, "SELECT restriction_type, content FROM knowledge_restrictions "
                  "WHERE knowledge_id = %s ORDER BY restriction_type, content", (kid,))]
        node = conn.execute("SELECT node_id FROM graph_nodes WHERE node_type = 'KNOWLEDGE' "
                            "AND entity_id = %s", (kid,)).fetchone()
        m["relationships"] = _labelled_edges(conn, str(node[0])) if node else []
    return m


def _labelled_edges(conn, node_id: str) -> list[dict]:
    rows = _rows(conn,
                 "SELECT e.edge_id, s.node_type, s.metadata, e.relationship_type, t.node_type, "
                 "t.metadata, e.confidence, e.origin, e.status, e.metadata FROM graph_edges e "
                 "JOIN graph_nodes s ON s.node_id = e.source_node "
                 "JOIN graph_nodes t ON t.node_id = e.target_node "
                 "WHERE (e.source_node = %s OR e.target_node = %s) AND e.status <> 'REMOVED' "
                 "ORDER BY e.relationship_type, e.edge_id", (node_id, node_id))
    return [{"edge_id": str(r[0]), "source": f"{r[1]}:{r[2].get('key')}", "relationship": r[3],
             "target": f"{r[4]}:{r[5].get('key')}", "confidence": float(r[6]), "origin": r[7],
             "status": r[8], "metadata": r[9]} for r in rows]


def _set_status(module_id: str, status: str, action: str, changed_by: str, reason: str) -> dict:
    who, why = _who(changed_by, reason)
    at = _now()
    with connect() as conn:
        before = _get_module(conn, module_id)
        if before is None:
            raise KnowledgeChangeError(f"unknown module {module_id}")
        if before["status"] == status:
            raise KnowledgeChangeError(f"module {module_id} is already {status}")
        if before["status"] == "RETIRED":
            raise KnowledgeChangeError(f"module {module_id} is retired: it is no longer in the source")
        conn.execute("UPDATE knowledge_modules SET status = %s, updated_at = %s, updated_by = %s "
                     "WHERE module_id = %s", (status, at, who, module_id))
        change = _log(conn, "MODULE", module_id, action, before["version"], who, why,
                      {"status": before["status"]}, {"status": status}, at)
        conn.commit()
    return {"module_id": module_id, "status": status, "version": before["version"],
            "change_id": change, "changed_by": who, "changed_at": at, "reason": why}


def activate_module(module_id: str, *, changed_by: str, reason: str) -> dict:
    return _set_status(module_id, "ACTIVE", "ACTIVATE", changed_by, reason)


def disable_module(module_id: str, *, changed_by: str, reason: str) -> dict:
    return _set_status(module_id, "DISABLED", "DISABLE", changed_by, reason)


# ------------------------------------------------------------------ governance: edges
def _node(conn, node_type: str, key: str, create: bool, who: str) -> str:
    from .graph import NODE_TYPES
    if node_type not in NODE_TYPES:
        raise KnowledgeChangeError(f"unknown node type {node_type!r}; allowed: {', '.join(NODE_TYPES)}")
    entity = knowledge_id(key) if node_type == "KNOWLEDGE" else key
    row = conn.execute("SELECT node_id FROM graph_nodes WHERE node_type = %s AND entity_id = %s",
                       (node_type, entity)).fetchone()
    if row:
        return str(row[0])
    if node_type == "KNOWLEDGE" or not create:
        raise KnowledgeChangeError(f"unknown {node_type.lower()} {key}")
    nid = uid("node", node_type, key)
    conn.execute("INSERT INTO graph_nodes (node_id, node_type, entity_id, metadata) VALUES (%s, %s, %s, %s)",
                 (nid, node_type, entity, _json({"key": key, "created_by": who})))
    return nid


def create_edge(source_type: str, source_key: str, relationship_type: str, target_type: str,
                target_key: str, *, changed_by: str, reason: str, confidence: float = 1.0,
                edge_reason: str = "") -> dict:
    """An ADMIN relationship, staged as REVIEW. `edge_reason` is what the trace
    shows when it applies; `reason` is why the admin made the change."""
    from ..kg.relations import RELATIONSHIPS
    who, why = _who(changed_by, reason)
    if relationship_type not in RELATIONSHIPS:
        raise KnowledgeChangeError(f"unknown relationship {relationship_type!r}; "
                                   f"allowed: {', '.join(RELATIONSHIPS)}")
    if not (0.0 <= float(confidence) <= 1.0):
        raise KnowledgeChangeError("confidence must be between 0 and 1")
    at = _now()
    with connect() as conn:
        s = _node(conn, source_type, source_key, True, who)
        t = _node(conn, target_type, target_key, True, who)
        eid = uid("edge", s, relationship_type, t, "ADMIN", None, None)
        existing = conn.execute(f"SELECT {', '.join(_EDGE_COLS)} FROM graph_edges WHERE edge_id = %s",
                                (eid,)).fetchone()
        if existing and existing[6] != "REMOVED":
            raise KnowledgeChangeError(f"relationship already exists ({eid})")
        meta = {"reason": edge_reason or why}
        if existing:
            conn.execute("UPDATE graph_edges SET status = 'REVIEW', confidence = %s, metadata = %s, "
                         "updated_by = %s, updated_at = %s WHERE edge_id = %s",
                         (float(confidence), _json(meta), who, at, eid))
        else:
            conn.execute("INSERT INTO graph_edges (edge_id, source_node, relationship_type, target_node, "
                         "confidence, origin, status, metadata, updated_by, updated_at) "
                         "VALUES (%s, %s, %s, %s, %s, 'ADMIN', 'REVIEW', %s, %s, %s)",
                         (eid, s, relationship_type, t, float(confidence), _json(meta), who, at))
        after = {"source": f"{source_type}:{source_key}", "relationship": relationship_type,
                 "target": f"{target_type}:{target_key}", "confidence": float(confidence),
                 "origin": "ADMIN", "status": "REVIEW", "metadata": meta}
        change = _log(conn, "EDGE", eid, "CREATE", "1", who, why, None, after, at)
        conn.commit()
    return {"edge_id": eid, **after, "change_id": change}


def remove_edge(edge_id: str, *, changed_by: str, reason: str) -> dict:
    who, why = _who(changed_by, reason)
    at = _now()
    with connect() as conn:
        r = conn.execute(f"SELECT {', '.join(_EDGE_COLS)} FROM graph_edges WHERE edge_id = %s",
                         (edge_id,)).fetchone()
        if r is None:
            raise KnowledgeChangeError(f"unknown relationship {edge_id}")
        e = _edge_row(r)
        if e["origin"] != "ADMIN":
            raise KnowledgeChangeError(
                f"a {e['origin']} relationship comes from the source (the controlled document, "
                "the compiled gate or kb_relations.yaml); change it there and re-import")
        if e["status"] == "REMOVED":
            raise KnowledgeChangeError(f"relationship {edge_id} is already removed")
        conn.execute("UPDATE graph_edges SET status = 'REMOVED', updated_by = %s, updated_at = %s "
                     "WHERE edge_id = %s", (who, at, edge_id))
        change = _log(conn, "EDGE", edge_id, "REMOVE", "1", who, why, {"status": e["status"]},
                      {"status": "REMOVED"}, at)
        conn.commit()
    return {"edge_id": edge_id, "status": "REMOVED", "change_id": change}


def list_edges(module_id: Optional[str] = None, *, include_removed: bool = False) -> list[dict]:
    with connect() as conn:
        if module_id:
            node = conn.execute("SELECT node_id FROM graph_nodes WHERE node_type = 'KNOWLEDGE' "
                                "AND entity_id = %s", (knowledge_id(module_id),)).fetchone()
            if node is None:
                raise KnowledgeChangeError(f"unknown module {module_id}")
            out = _labelled_edges(conn, str(node[0]))
        else:
            out = [_edge_row(r) for r in _rows(
                conn, f"SELECT {', '.join(_EDGE_COLS)} FROM graph_edges ORDER BY edge_id")]
    if include_removed or module_id:
        return out
    return [e for e in out if e["status"] != "REMOVED"]


def list_changes(entity_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    cols = ("change_id", "entity_type", "entity_id", "action", "version", "changed_by",
            "changed_at", "reason", "before", "after")
    sql = f"SELECT {', '.join(cols)} FROM knowledge_changes"
    params: tuple = ()
    if entity_id:
        sql, params = sql + " WHERE entity_id = %s", (entity_id,)
    sql += " ORDER BY changed_at DESC, change_id LIMIT %s"
    with connect() as conn:
        rows = _rows(conn, sql, params + (int(limit),))
    return [{**dict(zip(cols, r)), "changed_at": _ts(r[6]), "change_id": str(r[0])} for r in rows]


# ------------------------------------------------------------------ releases
_RELEASE_COLS = ("release_id", "source_document", "source_document_hash", "compiled_digest",
                 "relations_version", "parser_version", "document_version", "parent_release_id",
                 "created_at", "created_by", "reason", "module_count", "relationship_count", "drift")


def _release_row(r) -> dict:
    d = dict(zip(_RELEASE_COLS, r))
    d["release_id"] = str(d["release_id"])
    d["parent_release_id"] = None if d["parent_release_id"] is None else str(d["parent_release_id"])
    d["created_at"] = _ts(d["created_at"])
    return d


def list_releases() -> list[dict]:
    with connect() as conn:
        return [_release_row(r) for r in _rows(
            conn, f"SELECT {', '.join(_RELEASE_COLS)} FROM knowledge_release ORDER BY created_at DESC")]


def release_for_digest(digest: str) -> Optional[dict]:
    """The latest knowledge release built from exactly the KB a case loaded
    (manifest.kb.digest). None when the KB a case ran on was never imported."""
    with connect() as conn:
        r = conn.execute(f"SELECT {', '.join(_RELEASE_COLS)} FROM knowledge_release "
                         "WHERE compiled_digest = %s ORDER BY created_at DESC LIMIT 1",
                         (digest,)).fetchone()
    return _release_row(r) if r else None


def _items(conn, release_id: str, item_type: str) -> dict[str, tuple[str, dict]]:
    return {r[0]: (r[1], r[2]) for r in _rows(
        conn, "SELECT item_id, content_hash, content FROM knowledge_release_items "
              "WHERE release_id = %s AND item_type = %s", (release_id, item_type))}


def _field_diff(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b) - {"source_hash"})
    return {k: {"from": a.get(k), "to": b.get(k)} for k in keys
            if k != "source_hash" and a.get(k) != b.get(k)}


def compare_releases(release_a: str, release_b: str) -> dict:
    with connect() as conn:
        for rid in (release_a, release_b):
            if conn.execute("SELECT 1 FROM knowledge_release WHERE release_id = %s",
                            (rid,)).fetchone() is None:
                raise KnowledgeChangeError(f"unknown release {rid}")
        ma, mb = _items(conn, release_a, "MODULE"), _items(conn, release_b, "MODULE")
        ea, eb = _items(conn, release_a, "EDGE"), _items(conn, release_b, "EDGE")
    return {
        "from": release_a, "to": release_b,
        "modules_added": sorted(set(mb) - set(ma)),
        "modules_removed": sorted(set(ma) - set(mb)),
        "modules_changed": {mid: _field_diff(ma[mid][1], mb[mid][1])
                            for mid in sorted(set(ma) & set(mb)) if ma[mid][0] != mb[mid][0]},
        "relationships_added": sorted((eb[i][1] for i in set(eb) - set(ea)),
                                      key=lambda e: (e["relationship"], e["source"], e["target"])),
        "relationships_removed": sorted((ea[i][1] for i in set(ea) - set(eb)),
                                        key=lambda e: (e["relationship"], e["source"], e["target"])),
    }


def relationship_changes(release_id: Optional[str] = None) -> dict:
    """Relationship changes a release brought (vs its parent), plus every
    ADMIN relationship change since."""
    with connect() as conn:
        if release_id is None:
            r = conn.execute("SELECT release_id FROM knowledge_release ORDER BY created_at DESC "
                             "LIMIT 1").fetchone()
            if r is None:
                raise KnowledgeChangeError("no knowledge release has been imported")
            release_id = str(r[0])
        r = conn.execute("SELECT parent_release_id FROM knowledge_release WHERE release_id = %s",
                         (release_id,)).fetchone()
        if r is None:
            raise KnowledgeChangeError(f"unknown release {release_id}")
        parent = None if r[0] is None else str(r[0])
    cmp = (compare_releases(parent, release_id) if parent else
           {"relationships_added": [], "relationships_removed": [], "first_release": True})
    admin = [c for c in list_changes() if c["entity_type"] == "EDGE"]
    return {"release_id": release_id, "parent_release_id": parent,
            "added": cmp.get("relationships_added", []),
            "removed": cmp.get("relationships_removed", []),
            "admin_changes": admin}


__all__ = ["ingest", "list_modules", "get_module", "activate_module", "disable_module",
           "create_edge", "remove_edge", "list_edges", "list_changes", "list_releases",
           "compare_releases", "relationship_changes", "release_for_digest",
           "KnowledgeChangeError", "DEFAULT_DOCUMENT"]
