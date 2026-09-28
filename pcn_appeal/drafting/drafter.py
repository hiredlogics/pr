"""Drafting stage (between Engine 3 and Engine 4).

The drafter only ever sees the RetrievalPack: keeper-safe facts, gated module
propositions, allowed building blocks, verbatim lease clauses. It never sees
raw customer wording, so it cannot copy a first-person driver admission.

Output is STRUCTURED: paragraphs -> sentences, each sentence carrying the
fact_ids / module_ids / evidence_ids it relies on. The validator checks those
refs; the renderer strips them. That is how "every material assertion is
traceable" (Dev Pack acceptance criteria) becomes testable.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..kg.graph import KnowledgeGraph
from ..llm import LLMClient
from ..models import Draft, DraftSentence, RetrievalPack

DRAFTING_SYSTEM = """You draft an initial private parking appeal to the operator, as the registered keeper.
HARD RULES
- Never identify, infer or imply who was driving. Write about "the vehicle", never "I parked/drove".
- Use ONLY facts in verified_facts, propositions in context_chunks and verbatim lease_clauses.
- Never invent facts, dates, evidence, signage, payment, lease terms, operator records or case law.
- Do not allege a PoFA defect unless pofa_findings is non-empty.
- Do not state Code values unless code_version is set.
- Do not call ANPR entry-to-exit time 'parking time'. Do not merge consideration and grace.
- No universal 10-minute rule. Nothing 'automatically' cancels. No penalty / pre-estimate argument.
- No POPLA / IAS / court language. No module IDs, no internal reasoning.
- Lead with primary_route; merge repeated points; keep it concise.
- Say evidence is enclosed only if its evidence_id is in evidence_refs.
- Quote lease text only verbatim from lease_clauses and set quote_of to its evidence_id.
OUTPUT JSON: {"paragraphs": [[{"text": str, "fact_refs": [fact_id], "module_refs": [module_id],
"evidence_refs": [evidence_id], "quote_of": evidence_id|null}]]}"""

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class LLMDrafter:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def draft(self, case_id: str, pack: RetrievalPack, feedback: Optional[list[str]] = None,
              attempt: int = 1) -> Draft:
        payload = {k: getattr(pack, k) for k in (
            "primary_route", "secondary_routes", "verified_facts", "fact_refs", "evidence_refs",
            "prohibited_claims", "code_version", "pofa_route", "pofa_findings", "driver_status",
            "context_chunks", "lease_clauses")}
        if feedback:
            payload["validator_feedback"] = feedback
        out = self.llm.complete_json(task="drafting", system=DRAFTING_SYSTEM,
                                     user=json.dumps(payload, default=str))
        paras = [[DraftSentence(**s) for s in p] for p in out["paragraphs"]]
        return Draft(case_id, paras, attempt)


class TemplateDrafter:
    """Deterministic drafter: used for tests, as an LLM-outage fallback, and as
    the 'safe baseline' a reviewer can release when the LLM draft keeps failing."""

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    def _fill(self, text: str, pack: RetrievalPack) -> tuple[str, list[str]]:
        refs = []

        def sub(m):
            name = m.group(1)
            if name == "lease_or_tenancy":
                return "tenancy agreement" if "TENANCY" in pack.verified_facts.get("evidence_kinds", []) else "lease"
            if name in pack.fact_refs:
                refs.append(pack.fact_refs[name])
            v = pack.verified_facts.get(name, m.group(0))
            if name == "vrm" and isinstance(v, str) and len(v) == 7:
                v = f"{v[:4]} {v[4:]}"                    # display format for current-style UK plates
            return str(v)
        return _PLACEHOLDER.sub(sub, text), refs

    def _sentences(self, block_text: str, pack, module_id, evidence=None) -> list[DraftSentence]:
        filled, refs = self._fill(block_text, pack)
        return [DraftSentence(s, refs, [module_id], evidence or []) for s in _SENT.split(filled)]

    def draft(self, case_id: str, pack: RetrievalPack, feedback=None, attempt: int = 1) -> Draft:
        chunks = {c["id"]: c for c in pack.context_chunks if c["kind"] == "block"}
        used: set[str] = set()
        paras: list[list[DraftSentence]] = []

        intro = self._sentences(self.kg.blocks["PP-INTRO-001"].text, pack, "STRUCTURAL")
        if pack.driver_status == "UNIDENTIFIED":
            intro += self._sentences(self.kg.blocks["PP-INTRO-002"].text, pack, "STRUCTURAL")
        paras.append(intro)
        used |= {"PP-INTRO-001", "PP-INTRO-002"}

        for mid in pack.module_ids:
            mod = self.kg.modules[mid]
            para: list[DraftSentence] = []
            if mod.route == "RESIDENTIAL" and mid == "KB-RES-01":
                for c in pack.lease_clauses[:1]:
                    para.append(DraftSentence(
                        f'Clause {c["clause_ref"]} of the uploaded agreement provides: "{c["text"]}"',
                        [], [mid], [c["evidence_id"]], quote_of=c["evidence_id"]))
            if mid == "KB-RES-06":
                ref = next((c["clause_ref"] for c in pack.lease_clauses if c["has_regulations_power"]), None)
                para.append(DraftSentence(
                    f"The agreement also contains a provision concerning parking regulations (clause {ref}). "
                    "The operator is requested to explain whether any permit requirement relied upon was validly "
                    "made and notified under that provision, and how it is said to qualify the parking right "
                    "set out above.", [], [mid], []))
            for bid in mod.building_blocks:
                if bid in used or bid not in chunks:
                    continue
                if mid == "KB-POFA-01" and pack.pofa_findings == [] and bid == "PP-POFA-001":
                    continue          # generic PoFA statement only when a finding supports it
                blk = self.kg.blocks[bid]
                ev = [eid for eid, kind in pack.evidence_index.items() if kind in blk.requires_evidence]
                para += self._sentences(blk.text, pack, mid, ev)
                used.add(bid)
            if para:
                paras.append(para)

        closing = self._sentences(self.kg.blocks["PP-END-001"].text, pack, "STRUCTURAL") + \
            self._sentences(self.kg.blocks["PP-END-002"].text, pack, "STRUCTURAL")
        paras.append(closing)
        return Draft(case_id, paras, attempt)
