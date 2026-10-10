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
from .context import DraftContext, fill_placeholder_text
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


def _argument_family(module_refs) -> Optional[int]:
    """Which ONE_ARGUMENT group a sentence belongs to, if any."""
    for mid in module_refs or []:
        s = str(mid)
        if s.startswith("KB-PAY-") or s.startswith("KB-KEY-"):
            return 0
    return None


def _split_draft_sentences(draft: Draft) -> Draft:
    """Split block-sized sentences so restated-point drop is sentence-safe."""
    paras = []
    for para in draft.paragraphs:
        split: list[DraftSentence] = []
        for s in para:
            parts = [p.strip() for p in _SENT.split(s.text or "") if p.strip()]
            if len(parts) <= 1:
                split.append(s)
                continue
            split.extend(DraftSentence(p, list(s.fact_refs or []), list(s.module_refs or []),
                                      list(s.evidence_refs or []), s.quote_of)
                         for p in parts)
        if split:
            paras.append(split)
    return Draft(draft.case_id, paras, draft.attempt,
                 no_ground_reason=draft.no_ground_reason,
                 model=draft.model, prompt_version=draft.prompt_version)


def apply_one_argument_rules(draft: Draft) -> Draft:
    """LLM-path counterpart of TemplateDrafter._one_argument_per_theory.

    Payment and keying are one case theory: merge their paragraphs and drop
    restated payment sentences. Other routes are unchanged. Applied only
    when both families are present so a payment-only letter is not rewritten.
    """
    has_pay = any(str(m).startswith("KB-PAY-")
                  for s in draft.sentences() for m in (s.module_refs or []))
    has_key = any(str(m).startswith("KB-KEY-")
                  for s in draft.sentences() for m in (s.module_refs or []))
    if not (has_pay and has_key):
        return draft
    draft = _split_draft_sentences(draft)
    theory_paras: dict[int, list] = {}
    order: list[tuple[str, object]] = []
    made: dict[int, set] = {}
    for para in draft.paragraphs:
        families = {_argument_family(s.module_refs) for s in para}
        families.discard(None)
        if not families:
            order.append(("plain", para))
            continue
        fam = next(iter(families))
        keep = [s for s in para if not _restated(s.text, made.setdefault(fam, set()))]
        if not keep:
            continue
        if fam in theory_paras:
            theory_paras[fam].extend(keep)
        else:
            theory_paras[fam] = keep
            order.append(("theory", fam))
    out: list[list] = []
    seen: set[int] = set()
    for kind, payload in order:
        if kind == "plain":
            out.append(payload)
            continue
        fam = int(payload)
        if fam in seen:
            continue
        seen.add(fam)
        if theory_paras.get(fam):
            out.append(theory_paras[fam])
    return Draft(draft.case_id, out, draft.attempt,
                 no_ground_reason=draft.no_ground_reason,
                 model=draft.model, prompt_version=draft.prompt_version,
                 section_ownership=draft.section_ownership)


def _clone_draft(draft: Draft, paragraphs) -> Draft:
    return Draft(draft.case_id, paragraphs, draft.attempt,
                 no_ground_reason=draft.no_ground_reason,
                 model=draft.model, prompt_version=draft.prompt_version,
                 section_ownership=draft.section_ownership)


def _jaccard_words(a: str, b: str) -> float:
    x, y = set((a or "").lower().split()), set((b or "").lower().split())
    return len(x & y) / max(len(x | y), 1)


_KEEPER_ATTR = re.compile(
    r"\b(?:the\s+)?(?:registered\s+)?keeper(?:'s|’s)\s+"
    r"(?:account|case|position|understanding|recollection|evidence|information|"
    r"instructions)\b"
    r"|\b(?:the\s+)?(?:registered\s+)?keeper\s+"
    r"(?:states?|says|reports?|has reported|has told|has explained|explains|"
    r"understands|believes|recalls|contends|maintains|advises|asserts)\b"
    r"|\baccording to (?:the\s+)?(?:registered\s+)?keeper\b"
    r"|\bthe account (?:given|provided) by (?:the\s+)?(?:registered\s+)?keeper\b"
    r"|\bthe account is (?:therefore )?that\b",
    re.I,
)


def sanitize_draft_citations(draft: Draft, pack: RetrievalPack) -> Draft:
    """Drop invented fact refs and unattributed customer-account citations.

    The LLM sometimes invents ids such as F-driver_status (a pack field, not a
    Fact Graph row). Section assembly also copies one fact_ids_used list onto
    every sentence in a section, which falsely marks operational ANPR sentences
    as stating unattributed account facts (DV-ACCOUNT).
    """
    known_ids = set((pack.fact_refs or {}).values())
    by_id = {fid: name for name, fid in (pack.fact_refs or {}).items()}
    customer = set((pack.case_context or {}).get("customer_reported_facts") or [])
    seen: list[str] = []
    paras: list[list[DraftSentence]] = []
    for para in draft.paragraphs or []:
        kept: list[DraftSentence] = []
        for s in para:
            text = (s.text or "").strip()
            if not text:
                continue
            if any(_jaccard_words(prev, text) > 0.8 for prev in seen):
                continue
            refs = [r for r in (s.fact_refs or []) if r in known_ids]
            if customer and not _KEEPER_ATTR.search(text):
                refs = [r for r in refs if by_id.get(r) not in customer]
            kept.append(DraftSentence(
                text, refs, list(s.module_refs or []),
                list(s.evidence_refs or []), s.quote_of,
            ))
            seen.append(text)
        if kept:
            paras.append(kept)
    return _clone_draft(draft, paras)


def apply_locked_plan_particulars(draft: Draft, pack: RetrievalPack) -> Draft:
    """Render particulars the LOCKED plan already holds (lease text, one-theory merge).

    Does not retrieve narrative, Case Intelligence, or extra modules.
    """
    draft = sanitize_draft_citations(draft, pack)
    draft = apply_one_argument_rules(draft)
    letter = (draft.plain_text() or "")
    extra: list[list[DraftSentence]] = []
    paras = list(draft.paragraphs)

    # Identity: when the pack knows the PCN, the letter must name it. Structured
    # openings sometimes omit it; VAL-CONFLICT then holds an otherwise-sound draft.
    pcn = str((pack.verified_facts or {}).get("pcn_number") or "").strip()
    if pcn:
        from ..engines.validation import _contains_token, _ident
        if not _contains_token(letter, _ident(pcn)):
            refs = ([pack.fact_refs["pcn_number"]]
                    if getattr(pack, "fact_refs", None) and "pcn_number" in pack.fact_refs
                    else [])
            clause = f"This appeal concerns Parking Charge Notice {pcn}."
            injected = False
            if paras:
                first = list(paras[0])
                if first and set(first[0].module_refs or []) <= {"STRUCTURAL"}:
                    base = (first[0].text or "").rstrip()
                    if base and not base.endswith("."):
                        base += "."
                    first[0] = DraftSentence(
                        f"{base} {clause}".strip(),
                        list(first[0].fact_refs or []) + refs,
                        list(first[0].module_refs or ["STRUCTURAL"]),
                        list(first[0].evidence_refs or []),
                    )
                    paras[0] = first
                    injected = True
                    letter = " ".join(
                        s.text for para in paras for s in para if getattr(s, "text", None)
                    )
            if not injected:
                extra.append([DraftSentence(clause, refs, ["STRUCTURAL"], [])])
                letter += " " + clause

    res_ids = [m for m in (pack.module_ids or []) if str(m).startswith("KB-RES-")]
    clauses = list(pack.lease_clauses or [])
    if res_ids and clauses and not any((c.get("text") or "") and (c.get("text") or "") in letter
                                       for c in clauses):
        c = clauses[0]
        quote = (c.get("text") or "").strip()
        if quote:
            mid = "KB-RES-01" if "KB-RES-01" in res_ids else res_ids[0]
            extra.append([DraftSentence(
                f'Clause {c.get("clause_ref") or ""} of the uploaded agreement '
                f'provides: "{quote}"'.strip(),
                [], [mid],
                [c["evidence_id"]] if c.get("evidence_id") else [],
                quote_of=c.get("evidence_id"),
            )])
            letter += " " + quote
    if "KB-RES-06" in (pack.module_ids or []) and "parking regulations" not in letter.lower():
        ref = next((c.get("clause_ref") for c in clauses if c.get("has_regulations_power")), None)
        extra.append([DraftSentence(
            f"The agreement also contains a provision concerning parking regulations "
            f"(clause {ref}). The operator is requested to explain whether any permit "
            f"requirement relied upon was validly made and notified under that provision, "
            f"and how it is said to qualify the parking right set out above.",
            [], ["KB-RES-06"], [])])
    if not extra and paras == list(draft.paragraphs):
        return draft
    insert_at = 1 if paras else 0
    for block in extra:
        paras.insert(insert_at, block)
        insert_at += 1
    return _clone_draft(draft, paras)


# P5: case_context keys the drafter never receives. customer_source_texts are
# the customer's own wording (narrative and free-text answers), kept in the pack
# only so validation can tell pasted customer prose (VAL-CUSTOMER-COPY). The
# drafter works from normalised facts and the locked claim plan, never raw text.
WITHHELD_FROM_DRAFTER = ("customer_source_texts",)


def drafting_payload(pack: RetrievalPack) -> dict:
    """Everything the drafter sees, built by DraftContext (drafting/context.py):
    the LOCKED claim plan's approved claims, verified facts with where each
    came from, uploaded evidence, the approved wording for those claims. Not
    the knowledge base, not rejected or candidate modules, not the raw
    narrative, not trace or confidence."""
    return DraftContext.from_pack(pack).to_payload()


def rewrite_contract(payload: dict) -> dict:
    """What a retry may and may not change.

    A rewrite exists to fix what the validators or the quality judge named. It is
    handed the very same case package - nothing in `payload` differs from the first
    attempt - and this block says so in terms the drafter cannot mistake for an
    invitation to re-plan: the section/ground set is fixed, and the feedback is a
    list of defects in the wording, not a reason to argue something else. The
    grounds are listed so a retry that quietly swaps one for another can be seen
    against the contract; VAL-PLAN refuses it either way.
    """
    sections = (payload.get("draft_plan") or {}).get("sections") or []
    return {
        "same_case": True,
        "locked_section_ids": [s.get("section_id") for s in sections if s.get("section_id")],
        "locked_ground_ids": sorted({g for s in sections for g in (s.get("ground_ids") or [])}),
        "may_change": "the wording of the sentences validator_feedback names",
        "may_not_change": ["which grounds are argued", "which facts are stated",
                           "what any fact or approved conclusion means",
                           "who a statement is attributed to"],
        "instruction": ("Rewrite the SAME case. Keep every sentence the feedback does not name. "
                        "Fix each named problem by stating exactly what the case package "
                        "supports, no more and no less."),
    }


def _sentence_from_dict(s: dict) -> DraftSentence:
    return DraftSentence(
        text=str(s.get("text") or ""),
        fact_refs=list(s.get("fact_refs") or s.get("fact_ids_used") or []),
        module_refs=list(s.get("module_refs") or s.get("ground_ids") or []),
        evidence_refs=list(s.get("evidence_refs") or s.get("evidence_ids") or []),
        quote_of=s.get("quote_of"),
    )


def assemble_structured_draft(case_id: str, out: dict, attempt: int,
                              model: str, prompt_version: int,
                              draft_plan: Optional[dict] = None) -> Draft:
    """Assemble a Draft from structured section output (P10.6) or legacy paragraphs."""
    reason = (out.get("no_ground_reason") or "").strip() or None
    ownership: dict = {}
    plan_sections = {
        s.get("section_id"): s for s in (draft_plan or {}).get("sections") or []
        if s.get("section_id")
    }

    if out.get("sections") is not None:
        paras: list[list[DraftSentence]] = []
        opening = (out.get("opening") or "").strip()
        if opening:
            paras.append([DraftSentence(opening, [], ["STRUCTURAL"], [])])
        for sec in out.get("sections") or []:
            sid = sec.get("section_id") or ""
            plan_sec = plan_sections.get(sid) or {}
            grounds = list(sec.get("ground_ids") or plan_sec.get("ground_ids")
                           or ([plan_sec["ground_id"]] if plan_sec.get("ground_id") else []))
            text = (sec.get("text") or "").strip()
            if not text:
                continue
            facts = list(sec.get("fact_ids_used") or sec.get("fact_refs")
                         or plan_sec.get("supporting_fact_ids") or [])
            findings = list(sec.get("finding_ids_used") or [])
            evidence = list(sec.get("evidence_ids") or sec.get("evidence_refs")
                            or plan_sec.get("evidence_ids") or [])
            # Encode finding attribution in module_refs via grounds; findings audited separately.
            ownership[sid] = {
                "ground_ids": grounds,
                "merged_ground_ids": grounds if len(grounds) > 1 else [],
                "fact_ids": facts,
                "finding_ids": findings,
                "evidence_ids": evidence,
            }
            # Split multi-sentence section into DraftSentences sharing ownership.
            parts = [p.strip() for p in _SENT.split(text) if p.strip()] or [text]
            paras.append([
                DraftSentence(p, list(facts), list(grounds), list(evidence))
                for p in parts
            ])
        closing = (out.get("closing") or "").strip()
        if closing:
            paras.append([DraftSentence(closing, [], ["STRUCTURAL"], [])])
        return Draft(case_id, paras, attempt, no_ground_reason=reason,
                     model=model, prompt_version=prompt_version,
                     section_ownership=ownership)

    # Legacy paragraphs shape
    paras = [[_sentence_from_dict(s) for s in p] for p in (out.get("paragraphs") or [])]
    return Draft(case_id, paras, attempt, no_ground_reason=reason,
                 model=model, prompt_version=prompt_version, section_ownership=ownership)


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
            payload["rewrite_contract"] = rewrite_contract(payload)
        out = self.llm.complete_json(task="drafting", system=prompts.system("drafting"),
                                     user=json.dumps(payload, default=str))
        draft = assemble_structured_draft(
            case_id, out, attempt, self._model(), prompts.version("drafting"),
            draft_plan=payload.get("draft_plan"))
        return apply_locked_plan_particulars(draft, pack)

    def regenerate_sections(self, case_id: str, pack: RetrievalPack, draft: Draft,
                            section_ids: list[str], feedback: Optional[list[str]] = None,
                            attempt: int = 1) -> Draft:
        """Regenerate only failed DraftSections; Claim Plan stays locked (P10.6)."""
        payload = drafting_payload(pack)
        plan = payload.get("draft_plan") or {}
        wanted = set(section_ids or [])
        sections = [s for s in (plan.get("sections") or []) if s.get("section_id") in wanted]
        if not sections:
            return self.draft(case_id, pack, feedback, attempt)
        regen_payload = {
            "mode": "section_regeneration",
            "draft_plan": {
                "case_id": plan.get("case_id"),
                "claim_plan_id": plan.get("claim_plan_id"),
                "draft_plan_version": plan.get("draft_plan_version"),
                "sections": sections,
                "closing_requirements": plan.get("closing_requirements") or [],
            },
            "verified_facts": payload.get("verified_facts"),
            "fact_refs": payload.get("fact_refs"),
            "fact_basis": payload.get("fact_basis"),
            "driver_status": payload.get("driver_status"),
            "driver_rule": payload.get("driver_rule"),
            "case_context": {
                k: (payload.get("case_context") or {}).get(k)
                for k in ("operator_name", "parking_location", "alleged_breach",
                          "pcn_number", "vrm", "parking_event_date")
            },
            "verified_legal_findings": [
                f for f in (payload.get("verified_legal_findings") or [])
                if f.get("legal_module_id") in {
                    g for s in sections for g in (s.get("ground_ids") or [])
                }
            ],
            "context_chunks": [
                c for c in (payload.get("context_chunks") or [])
                if c.get("module_id") in {
                    g for s in sections for g in (s.get("ground_ids") or [])
                }
            ],
            "validator_feedback": list(feedback or []),
        }
        if feedback:
            regen_payload["rewrite_contract"] = rewrite_contract(regen_payload)
        out = self.llm.complete_json(task="drafting", system=prompts.system("drafting"),
                                     user=json.dumps(regen_payload, default=str))
        new_by_id = {s.get("section_id"): s for s in (out.get("sections") or [])
                     if s.get("section_id")}
        # If model returned legacy paragraphs only, fall back to full draft.
        if not new_by_id and out.get("paragraphs"):
            return assemble_structured_draft(
                case_id, out, attempt, self._model(), prompts.version("drafting"), plan)

        ownership = dict(draft.section_ownership or {})
        paras: list[list[DraftSentence]] = []
        plan_by_id = {s.get("section_id"): s for s in (plan.get("sections") or [])}
        # Preserve letter structure: STRUCTURAL paras + one para per plan section.
        structural_open = []
        structural_close = []
        body_by_grounds: dict[tuple, list[DraftSentence]] = {}
        for para in draft.paragraphs:
            refs = {m for s in para for m in (s.module_refs or [])}
            if refs and refs <= {"STRUCTURAL"}:
                text = " ".join(s.text for s in para)
                if re.search(r"\bcancel\b", text, re.I):
                    structural_close.append(para)
                else:
                    structural_open.append(para)
                continue
            key = tuple(sorted(refs))
            body_by_grounds[key] = para

        for sid, sec in plan_by_id.items():
            grounds = tuple(sorted(sec.get("ground_ids") or []))
            if sid in new_by_id:
                rendered = new_by_id[sid]
                text = (rendered.get("text") or "").strip()
                facts = list(rendered.get("fact_ids_used") or sec.get("supporting_fact_ids") or [])
                evidence = list(rendered.get("evidence_ids") or sec.get("evidence_ids") or [])
                gids = list(rendered.get("ground_ids") or sec.get("ground_ids") or [])
                ownership[sid] = {
                    "ground_ids": gids,
                    "merged_ground_ids": gids if len(gids) > 1 else [],
                    "fact_ids": facts,
                    "finding_ids": list(rendered.get("finding_ids_used") or []),
                    "evidence_ids": evidence,
                }
                parts = [p.strip() for p in _SENT.split(text) if p.strip()] or ([text] if text else [])
                if parts:
                    paras.append([DraftSentence(p, facts, gids, evidence) for p in parts])
            elif grounds in body_by_grounds:
                paras.append(body_by_grounds[grounds])
            else:
                # Keep any existing para that cites any ground in the section.
                for key, para in body_by_grounds.items():
                    if set(key) & set(grounds):
                        paras.append(para)
                        break

        merged = structural_open + paras + structural_close
        if out.get("closing"):
            merged = structural_open + paras + [[
                DraftSentence(str(out["closing"]), [], ["STRUCTURAL"], [])
            ]]
        result = Draft(case_id, merged, attempt, model=self._model(),
                       prompt_version=prompts.version("drafting"),
                       section_ownership=ownership)
        return apply_locked_plan_particulars(result, pack)


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
        kinds = list((pack.evidence_index or {}).values()) or pack.verified_facts.get("evidence_kinds")
        filled = fill_placeholder_text(text, pack.verified_facts, placeholders, kinds)
        for token in _PLACEHOLDER.findall(text or ""):
            name = (placeholders or {}).get(token, token)
            if name in pack.fact_refs:
                refs.append(pack.fact_refs[name])
        return filled, refs

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

        if (pack.case_context or {}).get("writer_capacity") == "LETTER_RECIPIENT":
            # A driver-addressed letter is answered by its recipient (P8 follow-up).
            refs = [pack.fact_refs[k] for k in ("vrm", "pcn_number") if k in pack.fact_refs]
            vf = pack.verified_facts or {}
            intro = [DraftSentence(
                f"I write as the recipient of your letter about Parking Charge Notice "
                f"{vf.get('pcn_number') or ''}".rstrip() + ". No admission is made as to who "
                f"was driving.", refs, ["STRUCTURAL"], [])]
        else:
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
