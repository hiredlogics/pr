"""Knowledge nodes and the relationships between facts, evidence and modules (P4).

A knowledge node is one approved KB module as reasoning sees it: what turns it
on (`use_when`), what turns it off (`do_not_use_when` = blocked conditions),
what it needs (`required_facts`), what evidence helps (`supporting_evidence`),
what it must never claim and how it is to be drafted (guidance only - the
building-block paragraphs stay in building_blocks.yaml and are not knowledge).

Relationships are typed edges:

  SUPPORTS           FACT/SIGNAL -> MODULE   the condition holding counts for it
  BLOCKS             FACT/SIGNAL/EVIDENCE -> MODULE   the condition holding rules it out
  REQUIRES           MODULE -> FACT          the module needs the fact to be argued
  CONFLICTS_WITH     MODULE -> MODULE        never argued together
  DEPENDS_ON         FACT -> FACT            a derived fact is computed from another
  EVIDENCE_SUPPORTS  EVIDENCE -> MODULE      an uploaded kind of document counts for it

Two origins:
  DERIVED  computed from the module's own predicates and kb_modules.yaml
           `conflicts_with`. They cannot drift from the gates the reasoning
           engine enforces, because they are read from the same predicates.
  CURATED  data/kb_relations.yaml: signal edges and fact dependencies, the
           relationships a gate cannot express (evidence type, allegation type).

Ids are UUIDv5 of the node / edge content, so the same KB always produces the
same ids (the matcher is deterministic run to run, spec test 5).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

SUPPORTS, BLOCKS, REQUIRES = "SUPPORTS", "BLOCKS", "REQUIRES"
CONFLICTS_WITH, DEPENDS_ON, EVIDENCE_SUPPORTS = "CONFLICTS_WITH", "DEPENDS_ON", "EVIDENCE_SUPPORTS"
RELATIONSHIPS = (SUPPORTS, BLOCKS, REQUIRES, CONFLICTS_WITH, DEPENDS_ON, EVIDENCE_SUPPORTS)
NODE_TYPES = ("MODULE", "FACT", "EVIDENCE", "SIGNAL")
DERIVED, CURATED = "DERIVED", "CURATED"

_NS = uuid.UUID("6f1c9a1e-4b7e-5d1a-9c33-0b8d6e2f4a10")
DATA = Path(__file__).resolve().parent.parent / "data"

# Category from the route's section-16 tier (routes.yaml): what kind of
# knowledge it is, not how strong it is.
CATEGORY_BY_TIER = {1: "LEGAL_RULE", 2: "FACTUAL_GROUND", 3: "EVIDENCE_CHALLENGE",
                    4: "SECONDARY_POINT"}


def _uid(*parts: Any) -> str:
    return str(uuid.uuid5(_NS, json.dumps(parts, sort_keys=True, default=str)))


@dataclass(frozen=True)
class KnowledgeNode:
    knowledge_id: str
    module_id: str
    name: str
    category: str
    version: str
    status: str
    effective_from: Optional[str]
    effective_to: Optional[str]
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    def as_row(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeEdge:
    edge_id: str
    source_type: str
    source_id: str
    relationship_type: str
    target_type: str
    target_id: str
    weight: float = 1.0
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def origin(self) -> str:
        return self.metadata.get("origin", DERIVED)

    def as_row(self) -> dict:
        return asdict(self)


def edge(source_type: str, source_id: str, rel: str, target_type: str, target_id: str,
         weight: float = 1.0, **meta: Any) -> KnowledgeEdge:
    if rel not in RELATIONSHIPS:
        raise ValueError(f"unknown relationship {rel!r}; allowed: {', '.join(RELATIONSHIPS)}")
    for t in (source_type, target_type):
        if t not in NODE_TYPES:
            raise ValueError(f"unknown node type {t!r}")
    key = (source_type, source_id, rel, target_type, target_id, meta.get("condition"),
           meta.get("from_field"))
    return KnowledgeEdge(_uid("edge", *key), source_type, source_id, rel, target_type,
                         target_id, float(weight), dict(meta))


# ------------------------------------------------------------------ nodes
def node_for(module, tier: int) -> KnowledgeNode:
    """The reasoning view of one module. Drafting paragraphs are NOT copied:
    building blocks are referenced by id only, for the drafter."""
    meta = {
        "route": module.route, "topic": module.topic, "strength": module.strength,
        "core_proposition": module.core_proposition,
        "use_when": module.use_when,
        "required_facts": list(module.required_facts or []),
        "supporting_evidence": list(module.evidence_helpful or []),
        "blocked_conditions": module.do_not_use_when,
        "prohibited_claims": list(module.prohibited_claims or []),
        "drafting_guidance": module.drafting_notes or "",
        "legal_basis": list(module.legal_basis or []),
        "building_block_ids": list(module.building_blocks or []),
        "source_reference": module.source_reference or "",
    }
    return KnowledgeNode(
        knowledge_id=_uid("module", module.module_id),
        module_id=module.module_id, name=module.topic,
        category=CATEGORY_BY_TIER.get(tier, "GROUND"),
        version=str(module.version), status=module.status,
        effective_from=module.effective_from.isoformat() if module.effective_from else None,
        effective_to=module.effective_to.isoformat() if module.effective_to else None,
        metadata=meta)


# ------------------------------------------------------------------ derived edges
def _leaves(pred: Any, positive: bool = True) -> Iterable[tuple[bool, dict]]:
    """Every leaf condition of a predicate with its polarity (False under an
    odd number of `not`s)."""
    if not isinstance(pred, dict) or len(pred) != 1:
        return
    op, arg = next(iter(pred.items()))
    if op in ("all", "any"):
        for p in arg:
            yield from _leaves(p, positive)
    elif op == "not":
        yield from _leaves(arg, not positive)
    elif op != "always":
        yield positive, pred


def _leaf_subject(leaf: dict) -> tuple[str, str]:
    op, arg = next(iter(leaf.items()))
    if op == "has_evidence":
        return "EVIDENCE", str(arg)
    return "FACT", str(arg if isinstance(arg, str) else arg[0])


def derived_edges(module) -> list[KnowledgeEdge]:
    mid, out = module.module_id, []
    for positive, leaf in _leaves(module.use_when):
        st, sid = _leaf_subject(leaf)
        rel = (EVIDENCE_SUPPORTS if st == "EVIDENCE" else SUPPORTS) if positive else BLOCKS
        out.append(edge(st, sid, rel, "MODULE", mid, origin=DERIVED, condition=leaf,
                        from_field="use_when"))
    for positive, leaf in _leaves(module.do_not_use_when):
        st, sid = _leaf_subject(leaf)
        # A condition that holds inside do_not_use_when blocks the module; under
        # a `not` there, its absence blocks it, so its presence counts for it.
        rel = BLOCKS if positive else (EVIDENCE_SUPPORTS if st == "EVIDENCE" else SUPPORTS)
        out.append(edge(st, sid, rel, "MODULE", mid, origin=DERIVED, condition=leaf,
                        from_field="do_not_use_when"))
    for f in module.required_facts or []:
        out.append(edge("MODULE", mid, REQUIRES, "FACT", f, origin=DERIVED,
                        from_field="required_facts"))
    return out


# ------------------------------------------------------------------ the graph
class RelationGraph:
    """Nodes and edges for one KB (YAML or a published release)."""

    def __init__(self, nodes: dict[str, KnowledgeNode], edges: list[KnowledgeEdge],
                 signals: dict[str, dict], depends_on: dict[str, list[str]], version: str):
        self.nodes = nodes
        self.edges = sorted(edges, key=lambda e: e.edge_id)
        self.signals = signals
        self.depends_on = depends_on
        self.version = version
        self._into: dict[str, list[KnowledgeEdge]] = {}
        for e in self.edges:
            if e.target_type == "MODULE":
                self._into.setdefault(e.target_id, []).append(e)
            elif e.source_type == "MODULE":
                self._into.setdefault(e.source_id, []).append(e)

    def edges_of(self, module_id: str, rel: Optional[str] = None) -> list[KnowledgeEdge]:
        return [e for e in self._into.get(module_id, [])
                if rel is None or e.relationship_type == rel]

    def conflicts(self, module_id: str) -> set[str]:
        return {e.target_id for e in self.edges
                if e.relationship_type == CONFLICTS_WITH and e.source_id == module_id}

    def as_dict(self) -> dict:
        return {"version": self.version,
                "nodes": [n.as_row() for n in self.nodes.values()],
                "edges": [e.as_row() for e in self.edges]}


def load_curated(path: Path = DATA / "kb_relations.yaml") -> dict:
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def build(kg, curated: Optional[dict] = None) -> RelationGraph:
    """The relation graph for a KnowledgeGraph: every module (any status - the
    matcher filters to in-force ones), derived edges, curated edges."""
    curated = load_curated() if curated is None else curated
    nodes = {mid: node_for(m, kg.route_tier(m.route)) for mid, m in sorted(kg.modules.items())}
    edges: list[KnowledgeEdge] = []
    for mid in sorted(kg.modules):
        edges += derived_edges(kg.modules[mid])
    for u, v, d in kg.g.edges(data=True):
        if d.get("type") == "CONFLICTS_WITH":
            edges.append(edge("MODULE", u[1], CONFLICTS_WITH, "MODULE", v[1], origin=DERIVED,
                              from_field="conflicts_with"))
    for spec in curated.get("edges") or []:
        st, sid = spec["source"]
        for target in spec["targets"]:
            if target not in kg.modules:
                raise ValueError(f"kb_relations.yaml: unknown module {target}")
            edges.append(edge(st, sid, spec["relationship"], "MODULE", target,
                              spec.get("weight", 1.0), origin=CURATED,
                              reason=spec.get("reason", "")))
    depends = {k: list(v) for k, v in (curated.get("depends_on") or {}).items()}
    for derived, sources in sorted(depends.items()):
        for s in sources:
            edges.append(edge("FACT", derived, DEPENDS_ON, "FACT", s, origin=CURATED))
    # Dedupe: the same condition can appear in two places of one predicate.
    unique = {e.edge_id: e for e in edges}
    return RelationGraph(nodes, list(unique.values()), curated.get("signals") or {},
                         depends, str(curated.get("version", "0")))
