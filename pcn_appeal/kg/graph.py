"""Legal knowledge graph.

Node types : Route, Module, Fact, Question, Evidence, LegalSource, Block
Edge types : Module-BELONGS_TO->Route        Module-REQUIRES->Fact
             Module-GATED_BY->Fact            Module-CITES->LegalSource
             Module-EXPRESSED_BY->Block        Module-HELPED_BY->Evidence
             Module-CONFLICTS_WITH->Module     Fact-ASKED_BY->Question
             Block-NEEDS_EVIDENCE->Evidence
             Block-ASSERTS_FACT->Fact

Reference impl: networkx (the whole KB is a few hundred nodes, fits in memory,
rebuilt when an admin publishes a KB version). Production option: Neo4j using
infra/neo4j_schema.cypher - same node/edge vocabulary, so this class becomes a
thin Cypher adapter.

What the graph gives you that vector search cannot:
  * which facts a module's gates depend on                    -> Analysis engine
  * which modules contradict each other                      -> Reasoning engine
  * which legal source + version backs a proposition          -> Validation engine
  * which blocks need which evidence                          -> Validation engine
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

import networkx as nx
import yaml

from ..models import BuildingBlock, KBModule
from ..routes import validate_ground_routes
from ..rules.dsl import referenced_facts

DATA = Path(__file__).resolve().parent.parent / "data"


def _as_date(value: object) -> Optional[date]:
    """YAML gives a date for `2026-01-01` but a str via the Postgres release."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class KnowledgeGraph:
    def __init__(self, data_dir: Path = DATA):
        self.g = nx.MultiDiGraph()
        self.modules: dict[str, KBModule] = {}
        self.blocks: dict[str, BuildingBlock] = {}
        self.routes: dict[str, dict] = {}
        self.questions: dict[str, dict] = {}
        self.question_cfg: dict = {}
        self.release_id: str | None = None
        self.release_digest: str | None = None
        self._load(data_dir)
        self._pin_release_identity()

    @classmethod
    def from_release(cls, release: dict) -> "KnowledgeGraph":
        """Build from a published Postgres release (see store/kb_source.py).
        The dicts are YAML-equivalent, so the graph is identical either way."""
        self = cls.__new__(cls)
        self.g = nx.MultiDiGraph()
        self.modules, self.blocks, self.routes = {}, {}, {}
        self.questions, self.question_cfg = {}, {}
        self.release_id = release.get("release_id")
        self.release_digest = None
        self._build(release["kb_modules"], release["routes"],
                    release["building_blocks"], release["questions"])
        self._pin_release_identity()
        return self

    def _pin_release_identity(self) -> None:
        """Ensure every loaded KG has a non-null release id + content digest.

        Production/pilot cases must never stamp `kb_release = null`. YAML loads
        get a stable content-addressed pin; published releases keep their id and
        gain the same digest fingerprint.
        """
        from ..manifest import kb_digest
        digest = kb_digest(self)
        self.release_digest = digest
        if not self.release_id:
            self.release_id = f"yaml-{digest[:16]}"

    # ------------------------------------------------------------------ load
    def _load(self, d: Path) -> None:
        self._build(
            yaml.safe_load((d / "kb_modules.yaml").read_text()),
            yaml.safe_load((d / "routes.yaml").read_text()),
            yaml.safe_load((d / "building_blocks.yaml").read_text()),
            yaml.safe_load((d / "questions.yaml").read_text()))

    def _build(self, kb: dict, rt: dict, bb: dict, qs: dict) -> None:
        # Fail the load, not a later comparison: a route the code does not know
        # is a ground no `Route.X` check can ever match (see routes.py).
        validate_ground_routes(((m.get("module_id"), m.get("route")) for m in kb["modules"]),
                               rt["routes"])
        self.routes = rt["routes"]
        for r, meta in self.routes.items():
            self.g.add_node(("Route", r), **meta)

        for bid, b in bb["blocks"].items():
            blk = BuildingBlock(bid, b["text"], b.get("requires_evidence_any", []),
                                b.get("requires_facts", []),
                                status=b.get("status", "ACTIVE"),
                                placeholder_map=b.get("placeholder_map") or {},
                                source_reference=b.get("source_reference", ""))
            self.blocks[bid] = blk
            self.g.add_node(("Block", bid))
            for ev in blk.requires_evidence:
                self.g.add_edge(("Block", bid), ("Evidence", ev), type="NEEDS_EVIDENCE")
            for f in blk.requires_facts:
                self.g.add_edge(("Block", bid), ("Fact", f), type="ASSERTS_FACT")

        for src_id, meta in kb.get("legal_sources", {}).items():
            self.g.add_node(("LegalSource", src_id), **meta)

        from ..module_roles import ROLE_BY_MODULE, normalize_role

        for m in kb["modules"]:
            mid = m.get("module_id")
            role = normalize_role(
                m.get("module_role") or ROLE_BY_MODULE.get(mid),
                default="SUBSTANTIVE_GROUND",
            )
            lead_raw = m.get("can_lead_letter", None)
            if lead_raw is None:
                lead_flag = None
            else:
                lead_flag = bool(lead_raw)
            mod = KBModule(**{k: m.get(k) for k in (
                "module_id", "route", "topic", "use_when", "do_not_use_when", "core_proposition",
                "required_facts", "evidence_helpful", "legal_basis", "drafting_notes",
                "prohibited_claims", "building_blocks")},
                strength=m.get("strength", 50), status=m.get("status", "ACTIVE"),
                version=str(m.get("version", "1.0")),
                # Date bounds were declared in the KB but never loaded, so a module
                # withdrawn on a date stayed live for ever. Provenance comes with
                # them so a reviewer can trace a proposition without leaving the KB.
                effective_from=_as_date(m.get("effective_from")),
                effective_to=_as_date(m.get("effective_to")),
                source_reference=m.get("source_reference", "") or "",
                legal_basis_origin=m.get("legal_basis_origin", "") or "",
                last_legal_review=_as_date(m.get("last_legal_review")),
                change_notes=m.get("change_notes", "") or "",
                module_role=role,
                can_lead_letter=lead_flag)
            self.modules[mod.module_id] = mod
            n = ("Module", mod.module_id)
            self.g.add_node(n, topic=mod.topic, strength=mod.strength)
            self.g.add_edge(n, ("Route", mod.route), type="BELONGS_TO")
            for f in mod.required_facts:
                self.g.add_edge(n, ("Fact", f), type="REQUIRES")
            for f in referenced_facts(mod.use_when) | referenced_facts(mod.do_not_use_when):
                self.g.add_edge(n, ("Fact", f), type="GATED_BY")
            for s in mod.legal_basis:
                self.g.add_edge(n, ("LegalSource", s), type="CITES")
            for b in mod.building_blocks:
                self.g.add_edge(n, ("Block", b), type="EXPRESSED_BY")
            for e in mod.evidence_helpful:
                self.g.add_edge(n, ("Evidence", e), type="HELPED_BY")

        for a, b in kb.get("conflicts_with", []):
            self.g.add_edge(("Module", a), ("Module", b), type="CONFLICTS_WITH")
            self.g.add_edge(("Module", b), ("Module", a), type="CONFLICTS_WITH")

        self.question_cfg = {k: v for k, v in qs.items() if k != "questions"}
        for fact, q in qs["questions"].items():
            self.questions[fact] = q
            self.g.add_edge(("Fact", fact), ("Question", fact), type="ASKED_BY")

    # ------------------------------------------------------------------ queries
    def _out(self, node, etype: str) -> list:
        return [v for _, v, d in self.g.out_edges(node, data=True) if d.get("type") == etype]

    def active_modules(self, on: Optional[date] = None) -> Iterable[KBModule]:
        """ACTIVE modules that are in force. `effective_from`/`effective_to` are
        the KB's way of withdrawing a ground when the law or the code changes;
        until they were loaded, a module marked as ending last year still ran."""
        day = on or date.today()
        return (m for m in self.modules.values()
                if m.status == "ACTIVE"
                and (m.effective_from is None or m.effective_from <= day)
                and (m.effective_to is None or day <= m.effective_to))

    @property
    def relations(self):
        """The knowledge relation graph (kg/relations.py): nodes, and the typed
        edges between facts, evidence, signals and modules. Built once, from
        the same modules this graph holds plus data/kb_relations.yaml."""
        if getattr(self, "_relations", None) is None:
            from .relations import build
            self._relations = build(self)
        return self._relations

    def route_tier(self, route: str) -> int:
        return self.routes.get(route, {}).get("tier", 9)

    def route_rank(self, route: str) -> int:
        """Admin-set order within a tier. Unranked routes sort after ranked ones."""
        return self.routes.get(route, {}).get("rank", 99)

    def gating_facts(self, module_id: str) -> set[str]:
        n = ("Module", module_id)
        direct = {v[1] for v in self._out(n, "GATED_BY") + self._out(n, "REQUIRES")}
        via_blocks = {f[1] for b in self._out(n, "EXPRESSED_BY") for f in self._out(b, "ASSERTS_FACT")}
        return direct | via_blocks

    def conflicts(self, module_id: str) -> set[str]:
        return {v[1] for v in self._out(("Module", module_id), "CONFLICTS_WITH")}

    def sources(self, module_id: str) -> list[dict]:
        return [{"id": v[1], **self.g.nodes[v]} for v in self._out(("Module", module_id), "CITES")]

    def question_for(self, fact: str) -> dict | None:
        return self.questions.get(fact)

    def export_cypher(self) -> str:
        """Emit MERGE statements to seed Neo4j from the same YAML source."""
        lines = []
        for n, attrs in self.g.nodes(data=True):
            label, key = n
            lines.append(f"MERGE (:{label} {{id: '{key}'}});")
        for u, v, d in self.g.edges(data=True):
            lines.append(f"MATCH (a:{u[0]} {{id:'{u[1]}'}}),(b:{v[0]} {{id:'{v[1]}'}}) "
                         f"MERGE (a)-[:{d['type']}]->(b);")
        return "\n".join(lines)
