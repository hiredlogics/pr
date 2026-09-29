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

from typing import Optional

from ..kg.graph import KnowledgeGraph
from .extraction import derive_jurisdiction
from ..legal import code_versions, pofa
from ..models import CaseFile, CaseState, Fact, FactSource, FactStatus, RetrievalPack, SourceKind
from ..rag.retriever import Doc, HybridRetriever, find_parking_clauses
from ..rules.dsl import evaluate

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
                # A block awaiting legal review is not approved wording, so it
                # never enters the corpus - it must not be retrievable at all.
                if b in kg.blocks and kg.blocks[b].status == "ACTIVE":
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
    def applicability(self, case: CaseFile, trace: Optional[list[str]] = None):
        """Deterministic inputs the analysis engine must respect: the resolved
        Code version and the PoFA calculation. Public because case analysis is
        given them rather than allowed to infer them."""
        return self._applicability(case, trace if trace is not None else [])

    def _applicability(self, case: CaseFile, trace: list[str]):
        version, status = code_versions.resolve(case.get("parking_event_date"), case.get("operator_ata"),
                                                case.get("operator_transitioned"))
        trace.append(f"code_version={getattr(version, 'version_id', None)} status={status}")
        res = pofa.assess(
            jurisdiction=derive_jurisdiction(case),
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
    def analyse(self, case: CaseFile, selected_ids: Optional[list[str]] = None) -> RetrievalPack:
        """Build the drafting pack for the grounds case analysis chose.

        `selected_ids` are those grounds, already vetoed against the KB. This
        engine gates them again, resolves conflicts and orders them; it does not
        select. Route ranking used to select here, and that is gone: routes now
        only order the letter. None means nothing was selected.
        """
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

        # 5. assemble the chosen grounds. Case analysis already decided, and its
        # choice was vetoed against do_not_use_when, the PoFA calculation and the
        # Code resolver. Nothing here re-opens that decision.
        by_id = {m.module_id: m for m in kept}
        selected = [by_id[mid] for mid in (selected_ids or []) if mid in by_id]
        unavailable = [mid for mid in (selected_ids or []) if mid not in by_id]
        if unavailable:
            trace.append(f"analysis grounds dropped by conflict resolution: {unavailable}")

        # Empty analysis selection used to ship intro+end alone. Seed with
        # allegation-shaped records requests only — not always-on LAND filler.
        if not selected:
            seeded = [m for m in kept if m.module_id == "KB-REC-01"]
            selected = seeded
            if seeded:
                trace.append(f"seeded grounds after empty analysis selection: "
                             f"{[m.module_id for m in seeded]}")
            else:
                trace.append("empty analysis selection and no allegation-shaped REC ground")

        # KB-GOV-07 and section 16 order the letter: dispositive statutory or
        # contractual point, then the strongest fact-specific ground, then
        # Code/evidence/signage, with landowner authority concise and last.
        #
        # This is ordering, not selection - which ground is argued is the
        # analysis engine's decision. Leaving the order to the model too
        # would put a governance rule at the mercy of its judgement, and
        # "a weak secondary ground must not dilute a strong primary" is not
        # a matter of taste.
        selected = self._drafting_priority(selected)
        trace.append(f"grounds from case analysis: {selected_ids or []}")
        trace.append(f"ordered by drafting priority (KB-GOV-07): {[m.module_id for m in selected]}")

        primary = selected[0].route if selected else None
        secondary = []
        for m in selected[1:]:
            if m.route != primary and m.route not in secondary:
                secondary.append(m.route)
        return self._pack(case, selected, primary, secondary, facts, version, pofa_res,
                          trace, ordered=[m.module_id for m in selected])

    def leading_grounds(self, module_ids) -> list[str]:
        """Of `module_ids`, those the KB allows to carry the letter.

        The strength calibration in kb_modules.yaml is explicit that below
        SUPPORTING_THRESHOLD a module is "evidence / signage / authority support
        only" and "can never lead the letter". A selection made up entirely of
        those has nothing to support: the letter comes out as a landowner-authority
        or keeper-liability-framing paragraph presented as an appeal.
        """
        return [mid for mid in (module_ids or [])
                if (self.kg.modules.get(mid) and
                    self.kg.modules[mid].strength >= SUPPORTING_THRESHOLD)]

    def _drafting_priority(self, selected: list) -> list:
        """KB-GOV-07 / section 16 priorities 1-4.

        Section 16 says lead with a *confirmed dispositive* point, and KB-GOV-07
        that a weak secondary ground must not dilute a strong primary one. Tier
        alone does not express that: KB-POFA-01 is tier 1 but strength 40 - it is
        the framing point ("keeper liability is not automatic"), not a defect, so
        it cannot lead a letter. Only a route holding a module at or above
        SUPPORTING_THRESHOLD is allowed to.

        Within a route the weaker module comes first, so the framing paragraph
        introduces the finding that follows it rather than trailing behind it.

        Within a tier, `rank` is the admin's declared order: tier says how strong
        a class of argument is, rank settles which of two equally-tiered routes
        goes first, and strength only breaks a remaining tie. Ordering on tier and
        strength alone let the KB's stated sequence be overridden by a weight set
        for a different purpose.

        Landowner authority is pinned last whatever it scores (section 16.4), and
        every tie breaks on a name so one case never yields two orderings.
        """
        best: dict[str, int] = {}
        for m in selected:
            best[m.route] = max(best.get(m.route, 0), m.strength)

        def route_key(route: str) -> tuple:
            return (
                1 if route == "LANDOWNER" else 0,
                0 if best[route] >= SUPPORTING_THRESHOLD else 1,   # may this route lead?
                self.kg.route_tier(route),
                self.kg.route_rank(route),
                -best[route],
                route,
            )

        return sorted(selected, key=lambda m: (route_key(m.route), m.strength, m.module_id))

    # ------------------------------------------------------------------ pack
    def _pack(self, case: CaseFile, selected, primary, secondary, facts,
              version, pofa_res, trace: list[str], ordered: list[str]) -> RetrievalPack:
        """Retrieval restricted to the chosen grounds, then the pack the drafter sees.

        The block gates below are safeguards, not selection: a paragraph that
        claims enclosed evidence is withheld unless it was uploaded (R-08), and one
        that asserts a fact is withheld unless that fact is proven (R-08b). Those
        apply however the grounds were chosen.
        """
        allowed = {m.module_id for m in selected}
        query = f"{primary or ''} {case.get('alleged_breach', '')} " + " ".join(m.topic for m in selected)
        hits = self.retriever.search(query, allowed_ids=allowed, k=40)
        uploaded = {e.kind for e in case.evidence.values() if e.uploaded}
        chunks = []
        placeholder_maps: dict[str, dict[str, str]] = {}
        for d in hits:
            if d.meta["kind"] == "block":
                blk = self.kg.blocks[d.meta["block_id"]]
                if blk.status != "ACTIVE":
                    trace.append(f"withheld block {blk.block_id}: status {blk.status}")
                    continue
                if blk.requires_evidence and not (set(blk.requires_evidence) & uploaded):
                    trace.append(f"withheld block {blk.block_id}: evidence not uploaded (R-08)")
                    continue
                if not all(facts.get(f) for f in blk.requires_facts):
                    trace.append(f"withheld block {blk.block_id}: asserts unproven fact {blk.requires_facts} (R-08b)")
                    continue
                if blk.placeholder_map:
                    placeholder_maps[blk.block_id] = dict(blk.placeholder_map)
            chunk = {"id": d.doc_id, "module_id": d.meta["module_id"], "kind": d.meta["kind"],
                     "text": d.text, "sources": self.kg.sources(d.meta["module_id"])}
            # Which verified fact fills each {{placeholder}} in this block, where
            # the approved wording's own noun differs from the fact's name. Without
            # it the drafter sees a hole it has no fact to fill and either invents
            # a value or leaves the template marker in the letter.
            if d.doc_id in placeholder_maps:
                chunk["placeholder_map"] = placeholder_maps[d.doc_id]
            chunks.append(chunk)

        prohibited = sorted({p for m in selected for p in m.prohibited_claims} | set(GLOBAL_PROHIBITED))
        missing = sorted({f for m in selected for f in m.required_facts if not case.has(f)})
        # keeper_name/keeper_address are the letterhead's, not the drafter's. The
        # body never needs them, and withholding them means no generated sentence
        # can put the customer's name or home address into the argument.
        # Free-text answer values and raw material points must never reach the
        # drafter as pasteable copy — only structured / professionally authored facts.
        withheld_from_drafter = (
            "lease_clauses", "keeper_name", "keeper_address",
            "material_account_points", "material_account_summary",
        )
        verified = {}
        for k, v in facts.items():
            if k in withheld_from_drafter:
                continue
            fact_obj = case.facts.get(k)
            if fact_obj and fact_obj.source.kind in (
                    SourceKind.ANSWER, SourceKind.CUSTOMER_FREE_TEXT) \
                    and isinstance(v, str) and len(v) > 48:
                # Long free-text answers are input, not letter copy.
                verified[f"{k}_provided"] = True
                continue
            # Structured free-text facts (bools/enums) ARE draftable — they are
            # normalized facts, not customer wording.
            verified[k] = v

        # Structured digest so drafting can name the allegation, evidence and
        # unresolved validation without seeing raw narrative text.
        unresolved = []
        for name, fact in case.facts.items():
            if not fact.usable:
                continue
            low = str(fact.value).strip().lower()
            if any(m in low for m in (
                "don't remember", "do not remember", "cannot remember", "can't remember",
                "not sure", "unknown", "don't know", "do not know",
            )) and any(tok in name for tok in ("kiosk", "validat", "voucher")):
                unresolved.append("kiosk_validation")
        if any("kiosk" in a or "validat" in a for a in case.asked_questions) and \
                not case.has("kiosk_validation_attempted"):
            # Asked but unanswered / unusable — still unconfirmed.
            if "kiosk_validation" not in unresolved:
                unresolved.append("kiosk_validation")
        receipt_present = any(e.kind == "RECEIPT" and e.uploaded for e in case.evidence.values())
        props = list(case.get("material_account_propositions") or [])
        if not props and case.get("material_account_proposition"):
            props = [case.get("material_account_proposition")]
        provenance = list(getattr(case, "free_text_provenance", None) or [])
        # Source snippets for VAL-CUSTOMER-COPY only — drafter must not paste them.
        source_blob = (case.raw_answers or {}).get("_material_source_texts") or ""
        source_texts = [s.strip() for s in source_blob.split("\n") if s.strip()]
        for name, raw in (case.raw_answers or {}).items():
            if name.startswith("_") or name == "narrative":
                continue
            text = str(raw or "").strip()
            if len(text) >= 20 and text.lower() not in (
                    "yes", "no", "true", "false", "bpa", "ipc", "not_shown"):
                source_texts.append(text)
        narrative = str((case.raw_answers or {}).get("narrative") or "").strip()
        if len(narrative) >= 12:
            source_texts.append(narrative)

        case_context = {
            "operator_name": case.get("operator_name"),
            "parking_location": case.get("parking_location"),
            "alleged_breach": case.get("alleged_breach"),
            "parking_event_date": str(case.get("parking_event_date") or ""),
            "pcn_number": case.get("pcn_number"),
            "vrm": case.get("vrm"),
            "evidence": [
                {"evidence_id": e.evidence_id, "kind": e.kind, "filename": e.filename}
                for e in case.evidence.values() if e.uploaded
            ],
            "unresolved_topics": unresolved,
            "validation_status": "UNCONFIRMED" if (
                "KB-REC-01" in ordered or unresolved or receipt_present
            ) and "KB-REC-01" in {m.module_id for m in selected} else None,
            "shopping_receipt_enclosed": receipt_present,
            # Professional propositions only (system-authored). Never customer prose.
            "material_account_propositions": props,
            "material_account_proposition": case.get("material_account_proposition") or "",
            "account_contradicts_allegation": bool(
                case.get("account_contradicts_allegation")),
            # Provenance chain for audit / intelligence — originals must not be pasted.
            "free_text_provenance": [
                {k: v for k, v in row.items() if k != "original"}
                for row in provenance
            ],
            "customer_source_texts": source_texts[:12],
            "recovery": {
                "unknown_material": (case.recovery_report or {}).get("unknown_material") or [],
                "operator_requestable": (case.recovery_report or {}).get("operator_requestable") or [],
                "conflicts": (case.recovery_report or {}).get("conflicts") or [],
                "calculated": (case.recovery_report or {}).get("calculated") or {},
                "validation_purchase_split": bool(case.get("shopping_purchase_confirmed")) and (
                    case.get("parking_validation_status") == "UNKNOWN"),
            },
        }

        case.state = CaseState.ANALYSED
        return RetrievalPack(
            primary_route=primary, secondary_routes=secondary,
            module_ids=ordered,
            verified_facts=verified, fact_refs={k: f.fact_id for k, f in case.facts.items() if f.usable},
            missing_facts=missing, evidence_refs=[e.evidence_id for e in case.evidence.values() if e.uploaded],
            prohibited_claims=prohibited,
            code_version=version.version_id if version else None,
            pofa_route=pofa_res.route, pofa_findings=pofa_res.findings,
            driver_status=case.driver_status.value, jurisdiction=case.get("jurisdiction", "UNKNOWN"),
            context_chunks=chunks, lease_clauses=case.get("lease_clauses", []), trace=trace,
            evidence_index={e.evidence_id: e.kind for e in case.evidence.values() if e.uploaded},
            case_context=case_context)
