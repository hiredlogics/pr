"""ENGINE 3 - Legal Reasoning (applicability + knowledge graph + restricted RAG).

Pipeline
  1. enrich()        derive document facts (lease clauses, regulations power)
  2. applicability   jurisdiction, driver status, Code version, PoFA calculator
  3. gate            evaluate every ACTIVE module's use_when / do_not_use_when
  4. resolve         drop conflicting modules (graph CONFLICTS_WITH), keep stronger
  5. rank routes     strength + tier bonus; supporting modules can't lead
  6. retrieve        hybrid RAG over the gated set only -> context chunks
  7. pack            RetrievalPack (KB section 18 schema + provenance)

Rule pack
  R-01 Applicability before drafting (KB-GOV-05): unresolved Code -> no Code values.
  R-02 PoFA findings come ONLY from legal/pofa.py, never from the LLM.
  R-03 A module is eligible only if use_when is TRUE and do_not_use_when is FALSE.
  R-04 Conflicting modules: keep the stronger; log the suppression.
  R-05 Route order: dispositive -> strong factual -> evidence -> signage/authority (KB-GOV-07).
  R-06 Max 3 secondary routes; supporting-only routes (strength < 50) never lead.
  R-07 Landowner authority is always concise and always last.
  R-08 Building blocks whose evidence is not uploaded are withheld.
  R-08b Building blocks that assert a fact (requires_facts) are withheld unless that fact is proven.
  R-09 Global prohibited claims are always attached (penalty / pre-estimate etc.).
  R-10 Mitigation is never selected when a substantive route is eligible.
"""
from __future__ import annotations

from ..kg.graph import KnowledgeGraph
from ..legal import code_versions, pofa
from ..models import CaseFile, CaseState, Fact, FactSource, FactStatus, RetrievalPack, SourceKind
from ..rag.retriever import Doc, HybridRetriever, find_parking_clauses
from ..rules.dsl import evaluate

TIER_BONUS = {1: 10, 2: 5, 3: 0, 4: -10}
SUPPORTING_THRESHOLD = 50
GLOBAL_PROHIBITED = [
    "genuine pre-estimate of loss", "unlawful penalty", "who was driving",
    "breakdown automatically frustrates", "10 minutes always cancels",
]


class ReasoningEngine:
    def __init__(self, kg: KnowledgeGraph, embedder=None):
        self.kg = kg
        corpus = []
        for m in kg.modules.values():
            corpus.append(Doc(m.module_id, f"{m.topic}. {m.core_proposition} {m.drafting_notes}",
                              {"module_id": m.module_id, "kind": "module"}))
            for b in m.building_blocks:
                if b in kg.blocks:
                    corpus.append(Doc(b, kg.blocks[b].text, {"module_id": m.module_id, "kind": "block",
                                                            "block_id": b}))
        self.retriever = HybridRetriever(corpus, embedder)

    # ------------------------------------------------------------------ 1
    def enrich(self, case: CaseFile) -> None:
        clauses = []
        for ev in case.evidence.values():
            if ev.kind in ("LEASE", "TENANCY") and ev.text:
                clauses += find_parking_clauses(ev.evidence_id, ev.text)
        if clauses:
            case.put(Fact("F-lease_clauses", "lease_clauses", clauses, FactStatus.DERIVED,
                          FactSource(SourceKind.DOCUMENT, clauses[0]["evidence_id"])))
        case.put(Fact("F-lease_parking_clause_found", "lease_parking_clause_found", bool(clauses),
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "lease_clause_finder")))
        case.put(Fact("F-lease_has_regulations_clause", "lease_has_regulations_clause",
                      any(c["has_regulations_power"] for c in clauses), FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "lease_clause_finder")))

    # ------------------------------------------------------------------ 2
    def _applicability(self, case: CaseFile, trace: list[str]):
        version, status = code_versions.resolve(case.get("parking_event_date"), case.get("operator_ata"),
                                                case.get("operator_transitioned"))
        trace.append(f"code_version={getattr(version, 'version_id', None)} status={status}")
        res = pofa.assess(
            jurisdiction=case.get("jurisdiction", "UNKNOWN"),
            relevant_land=case.get("relevant_land"),
            notice_route=case.get("notice_route", "UNKNOWN"),
            parking_event_date=case.get("parking_event_date"),
            notice_issue_date=case.get("notice_issue_date"),
            ntd_date=case.get("ntd_date"),
            actual_delivery_date=case.get("notice_received_date") if case.get("delivery_date_proven") else None,
            driver_identified=case.driver_status.value == "FORMALLY_IDENTIFIED")
        trace += [f"pofa:{n}" for n in res.notes]
        case.put(Fact("F-pofa_route", "pofa_route", res.route, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "pofa.assess")))
        case.put(Fact("F-pofa_finding", "pofa_finding", res.findings[0] if res.findings else None,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.assess")))
        return version if status == "RESOLVED" else None, res

    # ------------------------------------------------------------------ main
    def analyse(self, case: CaseFile) -> RetrievalPack:
        trace: list[str] = []
        version, pofa_res = self._applicability(case, trace)
        facts = case.fact_view()

        # 3. gate (R-03)
        eligible = []
        for m in self.kg.active_modules():
            ok = evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts)
            if ok and any(s.startswith("SCOP-") for s in m.legal_basis) and version is None \
                    and m.route in ("GRACE", "CONSIDERATION", "KEYING"):
                trace.append(f"withheld {m.module_id}: Code version unresolved (R-01)")
                ok = False
            if ok:
                eligible.append(m)
        # 4. conflicts (R-04)
        eligible.sort(key=lambda m: -m.strength)
        kept, dropped = [], set()
        for m in eligible:
            if m.module_id in dropped:
                continue
            kept.append(m)
            for c in self.kg.conflicts(m.module_id):
                if c not in dropped:
                    dropped.add(c)
                    trace.append(f"suppressed {c}: conflicts with {m.module_id} (R-04)")
        kept = [m for m in kept if m.module_id not in dropped]

        # 5. rank routes (R-05/R-06/R-07)
        score: dict[str, float] = {}
        for m in kept:
            s = m.strength + TIER_BONUS.get(self.kg.route_tier(m.route), 0)
            score[m.route] = max(score.get(m.route, -1e9), s)
        leaders = [r for r in score if max(mm.strength for mm in kept if mm.route == r) >= SUPPORTING_THRESHOLD]
        order = sorted(leaders, key=lambda r: -score[r]) + sorted(
            [r for r in score if r not in leaders], key=lambda r: -score[r])
        order = [r for r in order if r != "LANDOWNER"] + (["LANDOWNER"] if "LANDOWNER" in order else [])
        primary = order[0] if order and order[0] in leaders else None
        secondary = [r for r in order[1:] if r != "POFA" or pofa_res.findings][: self.kg.max_secondary]
        if "LANDOWNER" in order and "LANDOWNER" not in secondary:
            secondary = secondary[: self.kg.max_secondary - 1] + ["LANDOWNER"]
        routes = ([primary] if primary else []) + secondary
        selected = [m for m in kept if m.route in routes or m.module_id == "KB-POFA-01"]
        trace.append(f"routes={routes}")

        # 6. restricted retrieval
        allowed = {m.module_id for m in selected}
        query = f"{primary or ''} {case.get('alleged_breach', '')} " + " ".join(m.topic for m in selected)
        hits = self.retriever.search(query, allowed_ids=allowed, k=40)
        uploaded = {e.kind for e in case.evidence.values() if e.uploaded}
        chunks = []
        for d in hits:
            if d.meta["kind"] == "block":
                blk = self.kg.blocks[d.meta["block_id"]]
                if blk.requires_evidence and not (set(blk.requires_evidence) & uploaded):
                    trace.append(f"withheld block {blk.block_id}: evidence not uploaded (R-08)")
                    continue
                if not all(facts.get(f) for f in blk.requires_facts):
                    trace.append(f"withheld block {blk.block_id}: asserts unproven fact {blk.requires_facts} (R-08b)")
                    continue
            chunks.append({"id": d.doc_id, "module_id": d.meta["module_id"], "kind": d.meta["kind"],
                           "text": d.text, "sources": self.kg.sources(d.meta["module_id"])})

        prohibited = sorted({p for m in selected for p in m.prohibited_claims} | set(GLOBAL_PROHIBITED))
        missing = sorted({f for m in selected for f in m.required_facts if not case.has(f)})
        verified = {k: v for k, v in facts.items() if k not in ("route_hints", "lease_clauses")}

        case.state = CaseState.ANALYSED
        return RetrievalPack(
            primary_route=primary, secondary_routes=secondary,
            module_ids=[m.module_id for m in sorted(selected, key=lambda m: (
                routes.index(m.route) if m.route in routes else -1,
                0 if m.strength < SUPPORTING_THRESHOLD else 1))],   # framing point before its finding
            verified_facts=verified, fact_refs={k: f.fact_id for k, f in case.facts.items() if f.usable},
            missing_facts=missing, evidence_refs=[e.evidence_id for e in case.evidence.values() if e.uploaded],
            prohibited_claims=prohibited,
            code_version=version.version_id if version else None,
            pofa_route=pofa_res.route, pofa_findings=pofa_res.findings,
            driver_status=case.driver_status.value, jurisdiction=case.get("jurisdiction", "UNKNOWN"),
            context_chunks=chunks, lease_clauses=case.get("lease_clauses", []), trace=trace,
            evidence_index={e.evidence_id: e.kind for e in case.evidence.values() if e.uploaded})
