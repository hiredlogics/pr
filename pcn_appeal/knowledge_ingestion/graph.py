"""Knowledge records -> graph nodes and edges.

Node types (graph_nodes.node_type):
  KNOWLEDGE   one per module (entity_id = knowledge_id)
  FACT        one per fact name any module or relationship refers to
  EVIDENCE    one per evidence kind, and per evidence method (ANPR, ATTENDANT_PHOTO)
  RULE        document-wide rules (governance, validators, drafting priority,
              selection matrix, release checklist, source register) and the
              case classifications of kb_relations.yaml (allegation_class=...)
  CLAIM, QUESTION   reserved for P5; nothing creates them yet

Edges, by origin:
  YAML_COMPILED  the P4 relation graph's DERIVED edges (from the live gates) -
                 SUPPORTS / BLOCKS / REQUIRES / EVIDENCE_SUPPORTS / CONFLICTS_WITH
                 - and evidence_helpful as EVIDENCE_SUPPORTS
  CURATED        kb_relations.yaml: signal BLOCKS / SUPPORTS, fact DEPENDS_ON
  DOCX           what the controlled document adds: AI MUST CHECK -> REQUIRES
                 (inferred, confidence 0.6) and its EVIDENCE list ->
                 EVIDENCE_SUPPORTS (kind inferred, 0.7)
  ADMIN          made through governance; ingestion never creates or removes them

Ids are UUIDv5 of content, so the same inputs give the same rows.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from ..kg.relations import CURATED, RELATIONSHIPS, RelationGraph
from .extract import ModuleRecord
from .parser import ParsedDocument

NODE_TYPES = ("FACT", "KNOWLEDGE", "EVIDENCE", "CLAIM", "QUESTION", "RULE")
ORIGINS = ("YAML_COMPILED", "CURATED", "DOCX", "ADMIN")
INGESTED_ORIGINS = ("YAML_COMPILED", "CURATED", "DOCX")

_NS = uuid.UUID("0b7f9c52-2e64-5a8e-9d1c-5a3f7e21c0d4")


def uid(*parts: Any) -> str:
    return str(uuid.uuid5(_NS, json.dumps(parts, sort_keys=True, default=str)))


def knowledge_id(module_id: str) -> str:
    from ..kg.relations import _uid
    return _uid("module", module_id)            # same id P4's relation graph uses


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_type: str
    entity_id: str
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    def as_row(self) -> dict:
        return {"node_id": self.node_id, "node_type": self.node_type,
                "entity_id": self.entity_id, "metadata": self.metadata}


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source_node: str
    relationship_type: str
    target_node: str
    confidence: float
    origin: str
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    def as_row(self) -> dict:
        return {"edge_id": self.edge_id, "source_node": self.source_node,
                "relationship_type": self.relationship_type, "target_node": self.target_node,
                "confidence": self.confidence, "origin": self.origin, "metadata": self.metadata}


class Builder:
    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}

    def node(self, node_type: str, key: str, entity_id: Optional[str] = None, **meta) -> str:
        if node_type not in NODE_TYPES:
            raise ValueError(f"unknown node type {node_type!r}")
        nid = uid("node", node_type, key)
        if nid not in self.nodes:
            self.nodes[nid] = GraphNode(nid, node_type, entity_id or key, {"key": key, **meta})
        return nid

    def edge(self, source: str, rel: str, target: str, origin: str, confidence: float = 1.0,
             **meta) -> str:
        if rel not in RELATIONSHIPS:
            raise ValueError(f"unknown relationship {rel!r}")
        if origin not in ORIGINS:
            raise ValueError(f"unknown origin {origin!r}")
        eid = uid("edge", source, rel, target, origin, meta.get("condition"),
                  meta.get("from_field"))
        if eid not in self.edges:
            self.edges[eid] = GraphEdge(eid, source, rel, target, float(confidence), origin,
                                        {k: v for k, v in meta.items() if v is not None})
        return eid

    def module(self, mid: str) -> str:
        return self.node("KNOWLEDGE", mid, entity_id=knowledge_id(mid), module_id=mid)

    def fact(self, name: str) -> str:
        return self.node("FACT", name, fact_name=name)


def _signal_node(b: Builder, signal: str, graph: RelationGraph) -> str:
    """A case classification. Evidence methods are EVIDENCE nodes (the notice's
    kind of evidence); the rest are RULE nodes."""
    name, _, value = signal.partition("=")
    if name == "evidence_method":
        return b.node("EVIDENCE", value, kind="EVIDENCE_METHOD", signal=signal,
                      basis=_signal_basis(graph, name, value))
    return b.node("RULE", signal, kind="CASE_SIGNAL", signal=signal,
                  basis=_signal_basis(graph, name, value))


def _signal_basis(graph: RelationGraph, name: str, value: str):
    for opt in (graph.signals.get(name) or {}).get("values") or []:
        if opt.get("value") == value:
            return opt.get("when")
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")[:40]


def build_graph(records: list[ModuleRecord], relations: RelationGraph,
                doc: Optional[ParsedDocument] = None) -> tuple[list[GraphNode], list[GraphEdge]]:
    b = Builder()
    for r in records:
        b.module(r.module_id)
    known = {r.module_id for r in records}

    # ---- compiled + curated relationships (P4 relation graph)
    for e in relations.edges:
        def end(t: str, i: str) -> Optional[str]:
            if t == "MODULE":
                return b.module(i) if i in known else None
            if t == "FACT":
                return b.fact(i)
            if t == "EVIDENCE":
                return b.node("EVIDENCE", i, evidence_kind=i)
            if t == "SIGNAL":
                return _signal_node(b, i, relations)
            return None
        s, t = end(e.source_type, e.source_id), end(e.target_type, e.target_id)
        if s is None or t is None:
            continue
        origin = "CURATED" if e.origin == CURATED else "YAML_COMPILED"
        b.edge(s, e.relationship_type, t, origin, e.weight,
               condition=e.metadata.get("condition"), from_field=e.metadata.get("from_field"),
               reason=e.metadata.get("reason"), relation_edge_id=e.edge_id)

    # ---- what the controlled document adds
    for r in records:
        m = b.module(r.module_id)
        for f in r.required_facts:
            if f["requirement_type"] == "CHECK":
                b.edge(m, "REQUIRES", b.fact(f["fact_name"]), "DOCX",
                       f["metadata"].get("confidence", 0.6), from_field="AI_MUST_CHECK",
                       source_text=f["metadata"].get("source_text"))
        for ev in r.evidence:
            req = ev["requirement"]
            key = req.get("evidence_kind") or ev["evidence_type"].upper()
            node = b.node("EVIDENCE", key, evidence_kind=req.get("evidence_kind"),
                          label=req.get("label"))
            origin = "DOCX" if req.get("source") == "DOCX" else "YAML_COMPILED"
            b.edge(node, "EVIDENCE_SUPPORTS", m, origin, req.get("confidence") or 0.5,
                   from_field="EVIDENCE" if origin == "DOCX" else "evidence_helpful",
                   source_text=req.get("label"))

    # ---- document-wide rules
    if doc is not None:
        for rs in doc.rule_sets:
            for i, row in enumerate(rs.rows, 1):
                first = row[0] if row else ""
                key = first if re.match(r"^[A-Z]{2,}-[A-Z0-9-]+$", first) else \
                    f"{_slug(rs.section)}-{i:02d}"
                b.node("RULE", key, kind="DOCUMENT_RULE", section=rs.section,
                       header=rs.header, values=row)
        for section, bullets in doc.checklists.items():
            for i, text in enumerate(bullets, 1):
                b.node("RULE", f"{_slug(section)}-{i:02d}", kind="CHECKLIST", section=section,
                       text=text)

    nodes = sorted(b.nodes.values(), key=lambda n: n.node_id)
    edges = sorted(b.edges.values(), key=lambda e: e.edge_id)
    return nodes, edges


__all__ = ["build_graph", "GraphNode", "GraphEdge", "NODE_TYPES", "ORIGINS",
           "INGESTED_ORIGINS", "knowledge_id", "uid"]
