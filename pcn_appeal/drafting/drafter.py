"""Drafting stage (between Engine 3 and Engine 4).

The drafter only ever sees the RetrievalPack: keeper-safe facts, gated module
propositions, allowed building blocks, verbatim lease clauses, and a structured
case_context digest. It never sees raw customer wording, so it cannot copy a
first-person driver admission.

Output is STRUCTURED: paragraphs -> sentences, each sentence carrying the
fact_ids / module_ids / evidence_ids it relies on. The validator checks those
refs; the renderer strips them.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..kg.graph import KnowledgeGraph
from .. import prompts
from ..llm import LLMClient
from ..models import Draft, DraftSentence, RetrievalPack


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class LLMDrafter:
    """Primary production drafter: case-specific prose from the RetrievalPack."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def draft(self, case_id: str, pack: RetrievalPack, feedback: Optional[list[str]] = None,
              attempt: int = 1) -> Draft:
        payload = {k: getattr(pack, k) for k in (
            "primary_route", "secondary_routes", "verified_facts", "fact_refs", "evidence_refs",
            "prohibited_claims", "code_version", "pofa_route", "pofa_findings", "driver_status",
            "context_chunks", "lease_clauses", "case_context", "module_ids", "evidence_index")}
        if feedback:
            payload["validator_feedback"] = feedback
        out = self.llm.complete_json(task="drafting", system=prompts.system("drafting"),
                                     user=json.dumps(payload, default=str))
        paras = [[DraftSentence(**s) for s in p] for p in out["paragraphs"]]
        return Draft(case_id, paras, attempt)


class TemplateDrafter:
    """Deterministic fallback: used in tests, on LLM outage, and when the model
    returns unusable JSON. Builds case-specific REC paragraphs from pack facts
    rather than shipping a one-size-fits-all shell."""

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
                v = f"{v[:4]} {v[4:]}"
            return str(v)
        return _PLACEHOLDER.sub(sub, text), refs

    def _sentences(self, block_text: str, pack, module_id, evidence=None) -> list[DraftSentence]:
        filled, refs = self._fill(block_text, pack)
        return [DraftSentence(s, refs, [module_id], evidence or []) for s in _SENT.split(filled) if s.strip()]

    def _rec_paragraph(self, pack: RetrievalPack) -> list[DraftSentence]:
        """Case-specific records-request paragraph from verified facts + evidence.

        Does not claim validation occurred or failed. Names the allegation,
        enclosed shopping receipts, and asks the named operator to check records.
        """
        facts = pack.verified_facts
        ctx = pack.case_context or {}
        refs_pcn = [pack.fact_refs[k] for k in ("pcn_number",) if k in pack.fact_refs]
        refs_breach = [pack.fact_refs[k] for k in ("alleged_breach",) if k in pack.fact_refs]
        receipt_ids = [eid for eid, kind in pack.evidence_index.items() if kind == "RECEIPT"]

        operator = str(facts.get("operator_name") or ctx.get("operator_name") or "the operator")
        location = str(facts.get("parking_location") or ctx.get("parking_location") or "the site")
        breach = str(facts.get("alleged_breach") or ctx.get("alleged_breach") or "").strip()
        event = str(facts.get("parking_event_date") or "").strip()
        pcn = str(facts.get("pcn_number") or "").strip()
        vrm = str(facts.get("vrm") or "").strip()
        if isinstance(vrm, str) and len(vrm) == 7 and " " not in vrm:
            vrm = f"{vrm[:4]} {vrm[4:]}"

        sentences: list[DraftSentence] = []
        if breach:
            where = f" at {location}" if location and location != "the site" else ""
            when = f" on {event}" if event else ""
            sentences.append(DraftSentence(
                f"The Parking Charge Notice alleges: {breach}{where}{when}.",
                refs_breach, ["KB-REC-01"], []))
        else:
            sentences.append(DraftSentence(
                f"The allegation on Parking Charge Notice {pcn or 'the notice'} turns on whether "
                f"a required validation, authorisation or payment step was completed at {location}.",
                refs_pcn, ["KB-REC-01"], []))

        if receipt_ids:
            sentences.append(DraftSentence(
                "A shopping receipt for a purchase at the premises has been supplied with this appeal. "
                "That receipt confirms a purchase. It does not, of itself, establish that any parking "
                "validation, voucher or kiosk step was completed.",
                [], ["KB-REC-01"], receipt_ids))
        else:
            sentences.append(DraftSentence(
                "The materials available to the registered keeper do not establish whether any "
                "required validation or kiosk step was completed.",
                [], ["KB-REC-01"], []))

        unresolved = ctx.get("unresolved_topics") or []
        if "kiosk_validation" in unresolved or ctx.get("validation_status") == "UNCONFIRMED":
            sentences.append(DraftSentence(
                "Whether the validation kiosk or equivalent step was used remains unconfirmed "
                "on the information available to the keeper.",
                [], ["KB-REC-01"], []))

        who = operator if operator != "the operator" else "The operator"
        sentences.append(DraftSentence(
            f"{who} is requested to review its validation, kiosk, voucher and transaction records "
            f"for vehicle {vrm or 'the vehicle'} at {location}"
            f"{(' on ' + event) if event else ''}, and to explain the specific records relied upon "
            f"before maintaining Parking Charge Notice {pcn or 'the charge'}.",
            refs_pcn + ([pack.fact_refs["vrm"]] if "vrm" in pack.fact_refs else []),
            ["KB-REC-01"], []))
        return sentences

    def draft(self, case_id: str, pack: RetrievalPack, feedback=None, attempt: int = 1) -> Draft:
        chunks = {c["id"]: c for c in pack.context_chunks if c["kind"] == "block"}
        used: set[str] = set()
        paras: list[list[DraftSentence]] = []

        intro = self._sentences(self.kg.blocks["PP-INTRO-001"].text, pack, "STRUCTURAL")
        if pack.driver_status == "UNIDENTIFIED":
            intro += self._sentences(self.kg.blocks["PP-INTRO-002"].text, pack, "STRUCTURAL")
        paras.append(intro)
        used |= {"PP-INTRO-001", "PP-INTRO-002"}

        # Case frame: name the allegation so non-REC template routes still engage
        # the notice (VAL-SUBSTANCE) rather than shipping interchangeable filler.
        if "KB-REC-01" not in pack.module_ids:
            facts = pack.verified_facts
            ctx = pack.case_context or {}
            breach = str(facts.get("alleged_breach") or ctx.get("alleged_breach") or "").strip()
            location = str(facts.get("parking_location") or ctx.get("parking_location") or "").strip()
            event = str(facts.get("parking_event_date") or "").strip()
            if breach:
                refs = [pack.fact_refs[k] for k in ("alleged_breach",) if k in pack.fact_refs]
                where = f" at {location}" if location else ""
                when = f" on {event}" if event else ""
                paras.append([DraftSentence(
                    f"The Parking Charge Notice alleges: {breach}{where}{when}.",
                    refs, ["STRUCTURAL"], [])])

        for mid in pack.module_ids:
            mod = self.kg.modules.get(mid)
            if mod is None:
                continue
            para: list[DraftSentence] = []
            if mid == "KB-REC-01":
                # Prefer fact-built prose over the generic Appendix block.
                para = self._rec_paragraph(pack)
                used.add("PP-REC-001")
            elif mod.route == "RESIDENTIAL" and mid == "KB-RES-01":
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
            if mid != "KB-REC-01":
                for bid in mod.building_blocks:
                    if bid in used:
                        continue
                    # Prefer retrieved chunks; for material-account bay blocks also
                    # allow the KB text when facts already prove the assertion.
                    if bid not in chunks and not (
                            mid == "KB-BAY-01" and bid == "PP-BAY-002"
                            and pack.verified_facts.get("account_contradicts_allegation")
                            and pack.verified_facts.get("material_account_proposition")):
                        continue
                    if mid == "KB-POFA-01" and pack.pofa_findings == [] and bid == "PP-POFA-001":
                        continue
                    # Skip always-on landowner filler when a fact-specific REC ground is present.
                    if mid == "KB-LAND-01" and "KB-REC-01" in pack.module_ids:
                        continue
                    blk = self.kg.blocks[bid]
                    if blk.requires_facts and not all(
                            pack.verified_facts.get(f) for f in blk.requires_facts):
                        continue
                    ev = [eid for eid, kind in pack.evidence_index.items() if kind in blk.requires_evidence]
                    para += self._sentences(blk.text, pack, mid, ev)
                    used.add(bid)
            if para:
                paras.append(para)

        closing = self._sentences(self.kg.blocks["PP-END-001"].text, pack, "STRUCTURAL") + \
            self._sentences(self.kg.blocks["PP-END-002"].text, pack, "STRUCTURAL")
        paras.append(closing)
        return Draft(case_id, paras, attempt)
