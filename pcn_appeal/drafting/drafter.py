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
from ..routes import Route


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

# Routes whose modules argue ONE case theory and must therefore be drafted as one
# argument, not as consecutive paragraphs each opening on the same point.
#   KB-KEY-01 drafting note: "Keying and payment are one case theory - do not repeat."
#   KB-PAY-01 drafting note: "Merge repeated payment statements into one."
# Route-level rather than module-level because the grouping is about what the
# letter argues, and the ordering it overrides (routes.yaml rank) is route-level too.
ONE_ARGUMENT: tuple[frozenset[str], ...] = (
    frozenset({"PAYMENT", "KEYING"}),
)

# Points the letter must assert once. The approved blocks of two routes in one
# argument each state the shared point in their own words, which is right when
# the block stands alone and repetition when they are merged. Lexical
# near-duplicate detection (VAL-REPEAT) cannot see this: "A payment was made"
# and "the applicable tariff was paid" share almost no words.
RESTATED_POINTS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(a\s+)?payment\s+was\s+(nevertheless\s+)?made\b"
               r"|\b(the\s+)?(applicable\s+)?(parking\s+)?tariff\s+was\s+paid\b"
               r"|\bpayment\s+was\s+(duly\s+)?made\b", re.I),
)


def _restated(text: str, made: set[int]) -> bool:
    """Whether this sentence asserts a point already made in this argument.

    Works a sentence at a time, which is what makes it safe to drop: in the
    approved keying blocks the bare restatement is its own sentence, and the
    consequence drawn from it ("The case should not be treated as though the
    parking facility was used without payment") is the next one and survives.
    """
    for i, pattern in enumerate(RESTATED_POINTS):
        if not pattern.search(text):
            continue
        if i in made:
            return True
        made.add(i)
    return False


# P5: case_context keys the drafter never receives. customer_source_texts are
# the customer's own wording (narrative and free-text answers), kept in the pack
# only so validation can tell pasted customer prose (VAL-CUSTOMER-COPY). The
# drafter works from normalised facts and the locked claim plan, never raw text.
WITHHELD_FROM_DRAFTER = ("customer_source_texts",)


def drafting_payload(pack: RetrievalPack) -> dict:
    """Everything the drafter sees: the LOCKED claim plan's claims (module_ids,
    their approved wording in context_chunks, case_context.claim_plan), verified
    facts and uploaded evidence. Not the knowledge base, not rejected or
    candidate modules, not the raw narrative."""
    payload = {k: getattr(pack, k) for k in (
        "primary_route", "secondary_routes", "verified_facts", "fact_refs", "evidence_refs",
        "prohibited_claims", "code_version", "pofa_route", "pofa_findings", "driver_status",
        "context_chunks", "lease_clauses", "case_context", "module_ids", "evidence_index")}
    payload["case_context"] = {k: v for k, v in (pack.case_context or {}).items()
                               if k not in WITHHELD_FROM_DRAFTER}
    return payload


class LLMDrafter:
    """Primary production drafter: case-specific prose from the RetrievalPack."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def _model(self) -> str:
        """The model that actually drafted, or the stand-in's class name.

        Never None: an unrecorded model is how a letter written by the demo
        stand-in gets read back as a real provider's work.
        """
        return (getattr(self.llm, "models", None) or {}).get("drafting") or type(self.llm).__name__

    def draft(self, case_id: str, pack: RetrievalPack, feedback: Optional[list[str]] = None,
              attempt: int = 1) -> Draft:
        payload = drafting_payload(pack)
        if feedback:
            payload["validator_feedback"] = feedback
        out = self.llm.complete_json(task="drafting", system=prompts.system("drafting"),
                                     user=json.dumps(payload, default=str))
        paras = [[DraftSentence(**s) for s in p] for p in out["paragraphs"]]
        reason = (out.get("no_ground_reason") or "").strip() or None
        return Draft(case_id, paras, attempt, no_ground_reason=reason,
                     model=self._model(), prompt_version=prompts.version("drafting"))


class TemplateDrafter:
    """Layout / unit-test helper for approved block assembly.

    Production AI failure paths must NOT call this to silently release a
    different substantive appeal (see orchestrator generate loop). Keep intro/
    closing and block rendering for direct unit tests and layout experiments.
    """

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    def _fill(self, text: str, pack: RetrievalPack,
              placeholders: Optional[dict[str, str]] = None) -> tuple[str, list[str]]:
        refs = []

        def sub(m):
            # A block's placeholder_map names the fact behind a placeholder whose
            # wording differs from it ({{bay_reference}} -> allocated_bay).
            name = (placeholders or {}).get(m.group(1), m.group(1))
            if name == "lease_or_tenancy":
                return "tenancy agreement" if "TENANCY" in pack.verified_facts.get("evidence_kinds", []) else "lease"
            if name in pack.fact_refs:
                refs.append(pack.fact_refs[name])
            v = pack.verified_facts.get(name, m.group(0))
            if name == "vrm" and isinstance(v, str) and len(v) == 7:
                v = f"{v[:4]} {v[4:]}"
            return str(v)
        return _PLACEHOLDER.sub(sub, text), refs

    def _sentences(self, block_text: str, pack, module_id, evidence=None,
                   placeholders: Optional[dict[str, str]] = None) -> list[DraftSentence]:
        filled, refs = self._fill(block_text, pack, placeholders)
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

    @staticmethod
    def _one_argument_per_theory(
            grouped: list[tuple[str, list[DraftSentence]]]) -> list[list[DraftSentence]]:
        """Combine routes that argue one case theory, and state its point once.

        Payment and keying were drafted as two paragraphs, each opening by
        asserting that a payment was made - the same point three times across the
        two, which is what KB-KEY-01 ("one case theory - do not repeat") and
        KB-PAY-01 ("merge repeated payment statements into one") forbid. The
        sentences keep their own module_refs, so the validator can still check
        each one against the ground it came from.
        """
        theory_of: dict[str, int] = {}
        for i, routes in enumerate(ONE_ARGUMENT):
            for route in routes:
                theory_of[route] = i

        out: list[list[DraftSentence]] = []
        at: dict[int, int] = {}          # theory index -> its paragraph in `out`
        made: dict[int, set[int]] = {}   # theory index -> points already asserted
        for route, sentences in grouped:
            theory = theory_of.get(route)
            if theory is None:
                out.append(sentences)
                continue
            keep = [s for s in sentences
                    if not _restated(s.text, made.setdefault(theory, set()))]
            if theory in at:
                out[at[theory]] += keep
            elif keep:
                at[theory] = len(out)
                out.append(keep)
        return [p for p in out if p]

    def draft(self, case_id: str, pack: RetrievalPack, feedback=None, attempt: int = 1) -> Draft:
        chunks = {c["id"]: c for c in pack.context_chunks if c["kind"] == "block"}
        used: set[str] = set()
        paras: list[list[DraftSentence]] = []
        # (route, sentences) per module, so routes arguing one case theory can be
        # combined before they become paragraphs. Order is preserved: the KB-GOV-07
        # ordering has already been applied to pack.module_ids.
        grouped: list[tuple[str, list[DraftSentence]]] = []

        intro = self._sentences(self.kg.blocks["PP-INTRO-001"].letter_text, pack, "STRUCTURAL")
        if pack.driver_status == "UNIDENTIFIED":
            intro += self._sentences(self.kg.blocks["PP-INTRO-002"].letter_text, pack, "STRUCTURAL")
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
            elif mod.route == Route.RESIDENTIAL and mid == "KB-RES-01":
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
                    if blk.status != "ACTIVE":      # wording not yet approved
                        continue
                    if blk.requires_facts and not all(
                            pack.verified_facts.get(f) for f in blk.requires_facts):
                        continue
                    ev = [eid for eid, kind in pack.evidence_index.items() if kind in blk.requires_evidence]
                    para += self._sentences(blk.letter_text, pack, mid, ev, blk.placeholder_map)
                    used.add(bid)
            if para:
                grouped.append((mod.route, para))

        paras += self._one_argument_per_theory(grouped)

        closing = self._sentences(self.kg.blocks["PP-END-001"].letter_text, pack, "STRUCTURAL") + \
            self._sentences(self.kg.blocks["PP-END-002"].letter_text, pack, "STRUCTURAL")
        paras.append(closing)
        return Draft(case_id, paras, attempt)
