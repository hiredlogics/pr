"""Knowledge nodes, relationships and their change log in Postgres (P4).

Tables: knowledge_nodes, knowledge_edges, knowledge_changes
(infra/migrations/0004_knowledge_graph.sql).

    seed(kg)            idempotent: one node per KB module, every DERIVED edge
                        and every CURATED edge of data/kb_relations.yaml. A
                        second run writes nothing.
    create_module       new node, status REVIEW
    update_module       version bump, predicates validated, status REVIEW,
                        DERIVED edges recomputed from the new gate
    disable_module      status DISABLED, version bump
    create_edge         CURATED only, status REVIEW
    remove_edge         CURATED only, soft delete (status REMOVED). A DERIVED
                        edge is the module's own gate: change the gate instead.

Every change writes one knowledge_changes row: user, timestamp, reason,
version, before and after. A change without a user or a reason is refused.

STAGED, not live: live reasoning reads the KB from YAML or a published release
(kg/graph.py, store/kb_source.py), and the relation graph is rebuilt from that
same source (kg/relations.py). Nothing written here reaches a case until it is
published through the KB release gate, which this module does not touch and
which stays deliberately unimplemented (api.py: /admin/kb/releases -> 501).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..kg.relations import (CURATED, DERIVED, NODE_TYPES, RELATIONSHIPS, KnowledgeEdge,
                            _uid, build, derived_edges, edge)
from .db import connect

NODE_STATUSES = ("DRAFT", "REVIEW", "ACTIVE", "DISABLED")
EDGE_STATUSES = ("ACTIVE", "REVIEW", "REMOVED")

# Fields an admin may change on a module, and where each lives on the node.
NODE_FIELDS = ("name", "effective_from", "effective_to")
META_FIELDS = ("route", "topic", "strength", "core_proposition", "use_when", "required_facts",
               "supporting_evidence", "blocked_conditions", "prohibited_claims",
               "drafting_guidance", "legal_basis", "building_block_ids", "source_reference")
PREDICATE_FIELDS = ("use_when", "blocked_conditions")

_LEAF_OPS = {"is", "exists", "missing", "has_evidence"}
_BINARY_OPS = {"eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"}


class KnowledgeChangeError(ValueError):
    """A change the knowledge store refuses (missing user / reason, unknown
    relationship, derived edge, bad predicate, unknown module)."""


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


def validate_predicate(pred: Any, where: str = "predicate") -> None:
    """Structural check against the DSL in rules/dsl.py. `evaluate` short-
    circuits, so evaluating on empty facts would not reach every branch."""
    if pred in (None, {}):
        return
    if not isinstance(pred, dict) or len(pred) != 1:
        raise KnowledgeChangeError(f"{where}: must be a single-key mapping, got {pred!r}")
    op, arg = next(iter(pred.items()))
    if op == "always":
        return
    if op in ("all", "any"):
        if not isinstance(arg, list) or not arg:
            raise KnowledgeChangeError(f"{where}: '{op}' takes a non-empty list")
        for i, p in enumerate(arg):
            validate_predicate(p, f"{where}.{op}[{i}]")
        return
    if op == "not":
        validate_predicate(arg, f"{where}.not")
        return
    if op in _LEAF_OPS:
        if not isinstance(arg, str) or not arg:
            raise KnowledgeChangeError(f"{where}: '{op}' takes a fact name")
        return
    if op in _BINARY_OPS:
        if not isinstance(arg, list) or len(arg) != 2 or not isinstance(arg[0], str):
            raise KnowledgeChangeError(f"{where}: '{op}' takes [fact_name, value]")
        return
    raise KnowledgeChangeError(f"{where}: unknown operator {op!r}")


def _bump(version: str) -> str:
    major, _, minor = str(version or "1.0").partition(".")
    try:
        return f"{int(major)}.{int(minor or 0) + 1}"
    except ValueError:
        return f"{version}.1"


def _log(conn, entity_type: str, entity_id: str, action: str, version: str, who: str,
         why: str, before: Any, after: Any, at: str) -> str:
    change_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO knowledge_changes (change_id, entity_type, entity_id, action, version, "
        "changed_by, changed_at, reason, before, after) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (change_id, entity_type, entity_id, action, str(version), who, at, why,
         _json(before) if before is not None else None,
         _json(after) if after is not None else None))
    return change_id


_NODE_COLS = ("knowledge_id", "module_id", "name", "category", "version", "status",
              "effective_from", "effective_to", "metadata", "updated_by", "updated_at")
_EDGE_COLS = ("edge_id", "source_type", "source_id", "relationship_type", "target_type",
              "target_id", "weight", "metadata", "origin", "status", "version", "updated_by",
              "updated_at")


def _node_row(row) -> dict:
    d = dict(zip(_NODE_COLS, row))
    for k in ("effective_from", "effective_to", "updated_at"):
        if d[k] is not None and not isinstance(d[k], str):
            d[k] = d[k].isoformat()
    return d


def _edge_row(row) -> dict:
    d = dict(zip(_EDGE_COLS, row))
    d["weight"] = float(d["weight"]) if d["weight"] is not None else None
    if d["updated_at"] is not None and not isinstance(d["updated_at"], str):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


def _get_node(conn, module_id: str) -> Optional[dict]:
    row = conn.execute(f"SELECT {', '.join(_NODE_COLS)} FROM knowledge_nodes "
                       "WHERE module_id = %s", (module_id,)).fetchone()
    return _node_row(row) if row else None


def _get_edge(conn, edge_id: str) -> Optional[dict]:
    row = conn.execute(f"SELECT {', '.join(_EDGE_COLS)} FROM knowledge_edges "
                       "WHERE edge_id = %s", (edge_id,)).fetchone()
    return _edge_row(row) if row else None


def _insert_node(conn, node: dict) -> None:
    conn.execute(
        f"INSERT INTO knowledge_nodes ({', '.join(_NODE_COLS)}) "
        f"VALUES ({', '.join(['%s'] * len(_NODE_COLS))})",
        tuple(_json(node[c]) if c == "metadata" else node.get(c) for c in _NODE_COLS))


def _insert_edge(conn, e: KnowledgeEdge, status: str, who: str, at: str) -> dict:
    row = {**e.as_row(), "origin": e.origin, "status": status, "version": 1,
           "updated_by": who, "updated_at": at}
    conn.execute(
        f"INSERT INTO knowledge_edges ({', '.join(_EDGE_COLS)}) "
        f"VALUES ({', '.join(['%s'] * len(_EDGE_COLS))})",
        tuple(_json(row[c]) if c == "metadata" else row[c] for c in _EDGE_COLS))
    return row


# ------------------------------------------------------------------ seed
def seed(kg, *, changed_by: str = "kb-seed",
         reason: str = "seed from kb_modules.yaml + kb_relations.yaml") -> dict:
    """Load every KB module and relationship into the tables. Inserts only
    what is absent, so it is safe to re-run; returns what it wrote."""
    who, why = _who(changed_by, reason)
    graph = build(kg)
    at, nodes, edges = _now(), 0, 0
    with connect() as conn:
        for mid, node in sorted(graph.nodes.items()):
            if _get_node(conn, mid) is not None:
                continue
            row = {**node.as_row(), "updated_by": who, "updated_at": at}
            _insert_node(conn, row)
            _log(conn, "NODE", mid, "SEED", node.version, who, why, None, node.as_row(), at)
            nodes += 1
        for e in graph.edges:
            if _get_edge(conn, e.edge_id) is not None:
                continue
            _insert_edge(conn, e, "ACTIVE", who, at)
            if e.origin == CURATED:
                _log(conn, "EDGE", e.edge_id, "SEED", "1", who, why, None, e.as_row(), at)
            edges += 1
        conn.commit()
    return {"nodes": nodes, "edges": edges, "relations_version": graph.version}


# ------------------------------------------------------------------ modules
class _ModuleView:
    """Enough of a KBModule for derived_edges(), from node metadata."""

    def __init__(self, module_id: str, meta: dict):
        self.module_id = module_id
        self.use_when = meta.get("use_when") or {}
        self.do_not_use_when = meta.get("blocked_conditions") or {}
        self.required_facts = meta.get("required_facts") or []


def _sync_derived(conn, module_id: str, meta: dict, who: str, at: str) -> dict:
    """Recompute a module's DERIVED edges from its (new) gate. Edges no longer
    implied are soft-removed; new ones are staged as REVIEW."""
    wanted = {e.edge_id: e for e in derived_edges(_ModuleView(module_id, meta))}
    rows = conn.execute(
        "SELECT edge_id, status FROM knowledge_edges WHERE origin = %s AND "
        "((target_type = 'MODULE' AND target_id = %s) OR (source_type = 'MODULE' AND source_id = %s)) "
        "AND relationship_type <> 'CONFLICTS_WITH'",
        (DERIVED, module_id, module_id)).fetchall()
    have = {r[0]: r[1] for r in rows}
    added = removed = 0
    for eid, status in have.items():
        if eid not in wanted and status != "REMOVED":
            conn.execute("UPDATE knowledge_edges SET status = 'REMOVED', updated_by = %s, "
                         "updated_at = %s WHERE edge_id = %s", (who, at, eid))
            removed += 1
    for eid, e in wanted.items():
        if eid not in have:
            _insert_edge(conn, e, "REVIEW", who, at)
            added += 1
        elif have[eid] == "REMOVED":
            conn.execute("UPDATE knowledge_edges SET status = 'REVIEW', updated_by = %s, "
                         "updated_at = %s WHERE edge_id = %s", (who, at, eid))
            added += 1
    return {"derived_added": added, "derived_removed": removed}


def _check_meta(meta: dict) -> None:
    for f in PREDICATE_FIELDS:
        if f in meta:
            validate_predicate(meta[f], f)
    for f in ("required_facts", "supporting_evidence", "prohibited_claims", "legal_basis",
              "building_block_ids"):
        if f in meta and not (isinstance(meta[f], list) and all(isinstance(x, str) for x in meta[f])):
            raise KnowledgeChangeError(f"{f}: must be a list of strings")
    if "strength" in meta and not (isinstance(meta["strength"], int) and 0 <= meta["strength"] <= 100):
        raise KnowledgeChangeError("strength: must be an integer 0..100")


def create_module(module: dict, *, changed_by: str, reason: str, category: str = "FACTUAL_GROUND") -> dict:
    """A new knowledge node, staged as REVIEW. `module` carries module_id,
    name and any META_FIELDS; use_when is required."""
    who, why = _who(changed_by, reason)
    mid = (module.get("module_id") or "").strip()
    if not mid or not module.get("name"):
        raise KnowledgeChangeError("module_id and name are required")
    if not module.get("use_when"):
        raise KnowledgeChangeError("use_when is required: a module with no gate cannot be reasoned about")
    unknown = set(module) - {"module_id", "category", "version", *NODE_FIELDS, *META_FIELDS}
    if unknown:
        raise KnowledgeChangeError(f"unknown fields: {', '.join(sorted(unknown))}")
    meta = {f: module[f] for f in META_FIELDS if f in module}
    meta.setdefault("blocked_conditions", {})
    _check_meta(meta)
    at = _now()
    node = {"knowledge_id": _uid("module", mid),
            "module_id": mid, "name": module["name"],
            "category": module.get("category", category), "version": str(module.get("version", "1.0")),
            "status": "REVIEW", "effective_from": module.get("effective_from"),
            "effective_to": module.get("effective_to"), "metadata": meta,
            "updated_by": who, "updated_at": at}
    with connect() as conn:
        if _get_node(conn, mid) is not None:
            raise KnowledgeChangeError(f"module {mid} already exists; update it instead")
        _insert_node(conn, node)
        derived = _sync_derived(conn, mid, meta, who, at)
        change = _log(conn, "NODE", mid, "CREATE", node["version"], who, why, None, node, at)
        conn.commit()
    return {"node": node, "change_id": change, **derived}


def update_module(module_id: str, changes: dict, *, changed_by: str, reason: str) -> dict:
    who, why = _who(changed_by, reason)
    if not changes:
        raise KnowledgeChangeError("no changes given")
    unknown = set(changes) - {*NODE_FIELDS, *META_FIELDS}
    if unknown:
        raise KnowledgeChangeError(f"fields not editable: {', '.join(sorted(unknown))}")
    meta_changes = {k: v for k, v in changes.items() if k in META_FIELDS}
    _check_meta(meta_changes)
    at = _now()
    with connect() as conn:
        before = _get_node(conn, module_id)
        if before is None:
            raise KnowledgeChangeError(f"unknown module {module_id}")
        if before["status"] == "DISABLED":
            raise KnowledgeChangeError(f"module {module_id} is disabled")
        meta = {**(before["metadata"] or {}), **meta_changes}
        after = {**before, **{k: v for k, v in changes.items() if k in NODE_FIELDS},
                 "metadata": meta, "version": _bump(before["version"]), "status": "REVIEW",
                 "updated_by": who, "updated_at": at}
        conn.execute(
            "UPDATE knowledge_nodes SET name = %s, version = %s, status = %s, effective_from = %s, "
            "effective_to = %s, metadata = %s, updated_by = %s, updated_at = %s WHERE module_id = %s",
            (after["name"], after["version"], after["status"], after["effective_from"],
             after["effective_to"], _json(meta), who, at, module_id))
        derived = (_sync_derived(conn, module_id, meta, who, at)
                   if set(meta_changes) & {"use_when", "blocked_conditions", "required_facts"}
                   else {"derived_added": 0, "derived_removed": 0})
        change = _log(conn, "NODE", module_id, "UPDATE", after["version"], who, why, before, after, at)
        conn.commit()
    return {"node": after, "change_id": change, **derived}


def disable_module(module_id: str, *, changed_by: str, reason: str) -> dict:
    who, why = _who(changed_by, reason)
    at = _now()
    with connect() as conn:
        before = _get_node(conn, module_id)
        if before is None:
            raise KnowledgeChangeError(f"unknown module {module_id}")
        if before["status"] == "DISABLED":
            raise KnowledgeChangeError(f"module {module_id} is already disabled")
        after = {**before, "status": "DISABLED", "version": _bump(before["version"]),
                 "updated_by": who, "updated_at": at}
        conn.execute("UPDATE knowledge_nodes SET status = 'DISABLED', version = %s, "
                     "updated_by = %s, updated_at = %s WHERE module_id = %s",
                     (after["version"], who, at, module_id))
        change = _log(conn, "NODE", module_id, "DISABLE", after["version"], who, why, before, after, at)
        conn.commit()
    return {"node": after, "change_id": change}


# ------------------------------------------------------------------ edges
def create_edge(source_type: str, source_id: str, relationship_type: str, target_type: str,
                target_id: str, *, changed_by: str, reason: str, weight: float = 1.0,
                edge_reason: str = "") -> dict:
    """A CURATED relationship, staged as REVIEW. `edge_reason` is the text the
    trace shows when it fires ("wrong evidence type ..."); `reason` is why the
    admin made the change."""
    who, why = _who(changed_by, reason)
    if relationship_type not in RELATIONSHIPS:
        raise KnowledgeChangeError(f"unknown relationship {relationship_type!r}; "
                                   f"allowed: {', '.join(RELATIONSHIPS)}")
    for t in (source_type, target_type):
        if t not in NODE_TYPES:
            raise KnowledgeChangeError(f"unknown node type {t!r}; allowed: {', '.join(NODE_TYPES)}")
    if not (source_id or "").strip() or not (target_id or "").strip():
        raise KnowledgeChangeError("source_id and target_id are required")
    e = edge(source_type, source_id, relationship_type, target_type, target_id, weight,
             origin=CURATED, reason=edge_reason or why)
    at = _now()
    with connect() as conn:
        for t, i in ((source_type, source_id), (target_type, target_id)):
            if t == "MODULE" and _get_node(conn, i) is None:
                raise KnowledgeChangeError(f"unknown module {i}")
        existing = _get_edge(conn, e.edge_id)
        if existing is not None and existing["status"] != "REMOVED":
            raise KnowledgeChangeError(f"relationship already exists ({e.edge_id})")
        if existing is None:
            row = _insert_edge(conn, e, "REVIEW", who, at)
        else:
            row = {**existing, "status": "REVIEW", "version": existing["version"] + 1,
                   "metadata": e.metadata, "weight": e.weight, "updated_by": who, "updated_at": at}
            conn.execute("UPDATE knowledge_edges SET status = 'REVIEW', version = %s, metadata = %s, "
                         "weight = %s, updated_by = %s, updated_at = %s WHERE edge_id = %s",
                         (row["version"], _json(e.metadata), e.weight, who, at, e.edge_id))
        change = _log(conn, "EDGE", e.edge_id, "CREATE", str(row["version"]), who, why,
                      existing, row, at)
        conn.commit()
    return {"edge": row, "change_id": change}


def remove_edge(edge_id: str, *, changed_by: str, reason: str) -> dict:
    who, why = _who(changed_by, reason)
    at = _now()
    with connect() as conn:
        before = _get_edge(conn, edge_id)
        if before is None:
            raise KnowledgeChangeError(f"unknown relationship {edge_id}")
        if before["origin"] != CURATED:
            raise KnowledgeChangeError(
                "a DERIVED relationship is the module's own gate; change the module's "
                "use_when / blocked_conditions instead")
        if before["status"] == "REMOVED":
            raise KnowledgeChangeError(f"relationship {edge_id} is already removed")
        after = {**before, "status": "REMOVED", "version": before["version"] + 1,
                 "updated_by": who, "updated_at": at}
        conn.execute("UPDATE knowledge_edges SET status = 'REMOVED', version = %s, "
                     "updated_by = %s, updated_at = %s WHERE edge_id = %s",
                     (after["version"], who, at, edge_id))
        change = _log(conn, "EDGE", edge_id, "REMOVE", str(after["version"]), who, why,
                      before, after, at)
        conn.commit()
    return {"edge": after, "change_id": change}


# ------------------------------------------------------------------ reads
def list_nodes(status: Optional[str] = None) -> list[dict]:
    sql = f"SELECT {', '.join(_NODE_COLS)} FROM knowledge_nodes"
    params: tuple = ()
    if status:
        sql, params = sql + " WHERE status = %s", (status,)
    with connect() as conn:
        return [_node_row(r) for r in conn.execute(sql + " ORDER BY module_id", params).fetchall()]


def list_edges(module_id: Optional[str] = None, *, include_removed: bool = False) -> list[dict]:
    where, params = [], []
    if module_id:
        where.append("((target_type = 'MODULE' AND target_id = %s) OR "
                     "(source_type = 'MODULE' AND source_id = %s))")
        params += [module_id, module_id]
    if not include_removed:
        where.append("status <> 'REMOVED'")
    sql = (f"SELECT {', '.join(_EDGE_COLS)} FROM knowledge_edges"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY edge_id")
    with connect() as conn:
        return [_edge_row(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def list_changes(entity_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    cols = ("change_id", "entity_type", "entity_id", "action", "version", "changed_by",
            "changed_at", "reason", "before", "after")
    sql = f"SELECT {', '.join(cols)} FROM knowledge_changes"
    params: tuple = ()
    if entity_id:
        sql, params = sql + " WHERE entity_id = %s", (entity_id,)
    sql += " ORDER BY changed_at DESC, change_id LIMIT %s"
    with connect() as conn:
        rows = conn.execute(sql, params + (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        if d["changed_at"] is not None and not isinstance(d["changed_at"], str):
            d["changed_at"] = d["changed_at"].isoformat()
        out.append(d)
    return out


__all__ = ["seed", "create_module", "update_module", "disable_module", "create_edge",
           "remove_edge", "list_nodes", "list_edges", "list_changes", "validate_predicate",
           "KnowledgeChangeError", "NODE_STATUSES", "EDGE_STATUSES"]
