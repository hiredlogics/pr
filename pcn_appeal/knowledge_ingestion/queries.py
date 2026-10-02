"""Graph queries over the knowledge store.

`knowledge_for_facts` is retrieval, not reasoning: it says which knowledge a
case's facts are CONNECTED to, through which relationship, and what blocks it.
Whether a module's gate actually holds is still decided by the reasoning gate
(engines/knowledge_matcher.py on the live KB); this never selects a ground.

A fact reaches a module directly (FACT -SUPPORTS-> KNOWLEDGE) or through the
derived facts computed from it (DERIVED -DEPENDS_ON-> FACT, then DERIVED
-SUPPORTS-> KNOWLEDGE): children_present (alias of child_occupant_present) ->
account_contradicts_allegation -> KB-BAY-02.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import store

# Example queries for admins and the deliverable (Postgres).
EXAMPLES = {
    "knowledge supported by a fact": """
SELECT km.module_id, km.name, e.relationship_type, e.confidence, e.origin
FROM graph_nodes f
JOIN graph_edges e ON e.source_node = f.node_id AND e.status = 'ACTIVE'
JOIN graph_nodes k ON k.node_id = e.target_node AND k.node_type = 'KNOWLEDGE'
JOIN knowledge_modules km ON km.knowledge_id::text = k.entity_id
WHERE f.node_type = 'FACT' AND f.entity_id = 'payment_made'
  AND e.relationship_type IN ('SUPPORTS', 'EVIDENCE_SUPPORTS');""",
    "what blocks a module": """
SELECT s.node_type, s.entity_id, e.metadata->>'reason' AS reason, e.origin
FROM knowledge_modules km
JOIN graph_nodes k ON k.entity_id = km.knowledge_id::text AND k.node_type = 'KNOWLEDGE'
JOIN graph_edges e ON e.target_node = k.node_id AND e.relationship_type = 'BLOCKS'
JOIN graph_nodes s ON s.node_id = e.source_node
WHERE km.module_id = 'KB-ANPR-01' AND e.status = 'ACTIVE';""",
    "facts a module needs, by source": """
SELECT rf.fact_name, rf.requirement_type, rf.metadata->>'source' AS source
FROM knowledge_required_facts rf JOIN knowledge_modules km USING (knowledge_id)
WHERE km.module_id = 'KB-POFA-01' ORDER BY rf.requirement_type, rf.fact_name;""",
    "derived fact -> its inputs -> the knowledge it supports": """
WITH RECURSIVE up(node_id, path) AS (
  SELECT node_id, ARRAY[entity_id::text] FROM graph_nodes
   WHERE node_type = 'FACT' AND entity_id = 'child_occupant_present'
  UNION
  SELECT e.source_node, up.path || d.entity_id::text
  FROM up JOIN graph_edges e ON e.target_node = up.node_id
                            AND e.relationship_type = 'DEPENDS_ON' AND e.status = 'ACTIVE'
          JOIN graph_nodes d ON d.node_id = e.source_node
)
SELECT km.module_id, up.path
FROM up JOIN graph_edges s ON s.source_node = up.node_id AND s.relationship_type = 'SUPPORTS'
                          AND s.status = 'ACTIVE'
        JOIN graph_nodes k ON k.node_id = s.target_node AND k.node_type = 'KNOWLEDGE'
        JOIN knowledge_modules km ON km.knowledge_id::text = k.entity_id;""",
    "evidence that supports a module": """
SELECT ev.entity_id AS evidence, e.confidence, e.origin, e.metadata->>'source_text' AS as_written
FROM knowledge_modules km
JOIN graph_nodes k ON k.entity_id = km.knowledge_id::text AND k.node_type = 'KNOWLEDGE'
JOIN graph_edges e ON e.target_node = k.node_id AND e.relationship_type = 'EVIDENCE_SUPPORTS'
JOIN graph_nodes ev ON ev.node_id = e.source_node
WHERE km.module_id = 'KB-PAY-01' AND e.status = 'ACTIVE';""",
    "modules a case could run on, by KB version": """
SELECT r.release_id, r.document_version, r.created_at
FROM knowledge_release r WHERE r.compiled_digest = '<manifest.kb.digest>';""",
}


def _canonical(name: str) -> str:
    from ..fact_graph import ALIASES
    return ALIASES.get(name, name)


def _truthy(v: Any) -> bool:
    return v not in (None, False, "", [], {}, 0)


def knowledge_for_facts(facts: dict[str, Any], *, max_depth: int = 3) -> list[dict]:
    """Modules connected to the case's present facts, with the path.

    Returns one row per (module, relationship): module_id, name, status,
    relationship (SUPPORTS / BLOCKS), via (fact path), confidence, origin,
    condition, reason. Sorted, so the same facts give the same rows.
    """
    present = sorted({_canonical(k) for k, v in facts.items() if _truthy(v)})
    if not present:
        return []
    with store.connect() as conn:
        nodes = {str(r[0]): (r[1], r[2]) for r in conn.execute(
            "SELECT node_id, node_type, entity_id FROM graph_nodes").fetchall()}
        edges = [(str(r[0]), r[1], str(r[2]), float(r[3]), r[4], r[5]) for r in conn.execute(
            "SELECT source_node, relationship_type, target_node, confidence, origin, metadata "
            "FROM graph_edges WHERE status = 'ACTIVE'").fetchall()]
        modules = {str(r[0]): (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT knowledge_id, module_id, name, status FROM knowledge_modules").fetchall()}
    fact_node = {e: n for n, (t, e) in nodes.items() if t == "FACT"}
    depends_into: dict[str, list[str]] = {}       # input fact node -> derived fact nodes
    out_edges: dict[str, list[tuple]] = {}
    for s, rel, t, conf, origin, meta in edges:
        if rel == "DEPENDS_ON":
            depends_into.setdefault(t, []).append(s)
        out_edges.setdefault(s, []).append((rel, t, conf, origin, meta))

    rows: dict[tuple, dict] = {}
    for name in present:
        start = fact_node.get(name)
        if start is None:
            continue
        frontier = [(start, [name])]
        seen = {start}
        for _ in range(max_depth):
            nxt = []
            for node, path in frontier:
                for rel, t, conf, origin, meta in out_edges.get(node, []):
                    if rel not in ("SUPPORTS", "BLOCKS") or nodes.get(t, (None,))[0] != "KNOWLEDGE":
                        continue
                    mid, mname, mstatus = modules.get(nodes[t][1], (None, None, None))
                    if mid is None:
                        continue
                    key = (mid, rel, tuple(path))
                    if key not in rows:
                        rows[key] = {"module_id": mid, "name": mname, "status": mstatus,
                                     "relationship": rel, "via": list(path), "confidence": conf,
                                     "origin": origin, "condition": meta.get("condition"),
                                     "from_field": meta.get("from_field"),
                                     "reason": meta.get("reason")}
                for derived in depends_into.get(node, []):
                    if derived not in seen:
                        seen.add(derived)
                        nxt.append((derived, path + [nodes[derived][1]]))
            frontier = nxt
    return sorted(rows.values(), key=lambda r: (r["module_id"], r["relationship"], r["via"]))


def modules_for_fact(fact: str) -> list[str]:
    return sorted({r["module_id"] for r in knowledge_for_facts({fact: True})
                   if r["relationship"] == "SUPPORTS"})


__all__ = ["knowledge_for_facts", "modules_for_fact", "EXAMPLES"]
