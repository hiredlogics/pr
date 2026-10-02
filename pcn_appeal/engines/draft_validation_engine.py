"""Draft validation and sentence grounding (P6 §3, §4, §5).

Sits between the drafter and the existing ValidationEngine (VAL-*), which it
does not replace:

    LLM draft -> sentence grounding -> DraftValidationEngine -> ValidationEngine -> release

It asks one question of the draft: is every sentence something the LOCKED
Claim Plan, the Fact Graph and the uploaded evidence allow the letter to say?

GROUNDING. Every sentence is mapped to

    claim_plan_item   an approved module (or STRUCTURAL: opening, case
                      identification, request to cancel)
    fact_reference    the Fact Graph facts it cites
    evidence_reference  the uploaded evidence it cites

and recorded (`DraftValidation.grounding`, stored in draft_versions). A
sentence that maps to no claim plan item is UNGROUNDED. The orchestrator
treats ungrounded sentences like any blocked sentence: the drafter is asked
again with the rule and the sentence, and what still fails is removed.

CHECKS (rule ids DV-*; all BLOCK)

    FACT       every cited fact exists in the Fact Graph; a completed payment
               is only asserted when payment_made is established
    CLAIM      every argument is in the locked Claim Plan (messages name the
               claim family only - the drafter must never learn what was rejected)
    EVIDENCE   every cited evidence id was uploaded; "enclosed" needs a reference
    DRIVER     no driver admission and no statement of who parked or drove,
               unless the driver has been formally identified
    LEGAL      no legal conclusion the approved wording does not make
    STRUCTURE  the letter asks for the charge to be cancelled
    LEAK       no KB / VAL / internal ids, placeholders, prompt text, trace
    ACCOUNT    what only the keeper says is attributed to the keeper
               ("The keeper's account is that ..."), never stated as proven

plus VAL-LEGAL-FINDING (P6.1): a sentence that states a specific legal defect
(late Notice to Keeper, missing mandatory wording or content ...) must map to
a VERIFIED legal finding (legal/findings.py); a vague "appears non-compliant"
is refused when nothing is verified. Putting the operator to proof asserts no
defect and stays allowed.

Generic by construction: no check names an operator, a PCN or a module. What
a claim may say comes from the plan and the approved wording it carries.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from ..customer_safe import internal_ids
from ..drafting.context import ACCOUNT_DERIVED_FACTS
from ..legal import findings as legal
from ..models import Draft, RetrievalPack, ValidationIssue

VERSION = "DV-1"
STRUCTURAL = "STRUCTURAL"

GROUNDED, STRUCTURAL_OK, UNGROUNDED = "GROUNDED", "STRUCTURAL", "UNGROUNDED"

_R = lambda p: re.compile(p, re.I)  # noqa: E731

# First person is never the keeper's to write about driving; "the driver ..."
# is only for a formally identified driver.
FIRST_PERSON_DRIVING = _R(
    r"\b(I|we)\s+(?:\w+\s+)?(drove|was driving|were driving|parked|left (?:the|my|our)|arrived|"
    r"returned|came back|stayed|stopped|went|walked|got out|collected|paid|pulled)\b"
    r"|\bwhen\s+(I|we)\s+(parked|arrived|returned|left)\b"
    r"|\bI\s+(am|was)\s+the\s+(driver|person (who|that))\b"
    r"|\b(my|our)\s+(vehicle|car)\s+was\s+(parked|driven|left)\s+by\b")
THIRD_PERSON_DRIVING = _R(
    r"\bthe\s+(driver|motorist|person driving)\s+(parked|drove|was driving|left|returned|"
    r"arrived|stopped|stayed|went|paid|walked|got out|collected|pulled)\b"
    r"|\bthe keeper\s+(parked|drove|was driving|left the (vehicle|car)|returned to the "
    r"(vehicle|car)|arrived)\b"
    r"|\b(was|were)\s+driven\s+by\b|\bdriven by the keeper\b")

# A legal conclusion: a finding about validity, lawfulness or breach.
LEGAL_CONCLUSION = _R(
    r"\bthe\s+(notice|charge|pcn|parking charge)\s+(is|was|are)\s+(therefore\s+|hereby\s+)?"
    r"(invalid|void|unlawful|unenforceable|defective|illegal|a nullity)\b"
    r"|\b(is|are|was|were)\s+(in breach of|contrary to|unlawful|illegal)\b"
    r"|\b(breach(?:es|ed)?|contravene[sd]?|violat(?:e|es|ed))\s+(?:of\s+)?(?:the\s+)?"
    r"(Protection of Freedoms Act|Schedule 4|PoFA|Code of Practice|Consumer Rights Act|"
    r"Equality Act|Data Protection|Consumer Protection)"
    r"|\bfail(?:s|ed)?\s+to\s+comply\s+with\s+(?:the\s+)?(PoFA|Schedule 4|Code|Act)\b"
    r"|\bhas\s+no\s+(?:legal\s+)?(right|standing|authority|power)\b"
    r"|\b(cannot|could not)\s+(lawfully|legally)\b|\b(unlawfully|illegally)\b"
    r"|\bit is (clear|beyond doubt|undisputed) that\b|\bin law\b.{0,30}\b(must|cannot)\b")
# Reported speech is not a conclusion of the letter's own.
REPORTED = _R(r"\b(alleg\w+|disputed?|claims?|asserts?|states?|says|contends?)\b")
# Case law: a name or a neutral citation.
CASE_LAW = _R(r"\b[A-Z][\w&' -]{2,40}\s+v\.?\s+[A-Z][\w&' -]{2,40}\b|\[\d{4}\]\s+(UKSC|UKHL|EWCA|EWHC|"
              r"UKUT|WLR|AC|QB)\b")

CANCEL_REQUEST = _R(r"\b(request\w*|ask\w*|should|please|invited?|require\w*|urge\w*)\b[^.]{0,80}"
                    r"\b(cancel\w*|withdraw\w*|set aside|rescind\w*)")

PAYMENT_COMPLETED = _R(
    r"\b(payment\s+(?:was|has been|had been)\s+(?:made|completed|taken|successful|processed)|"
    r"(?:was|has been|had been)\s+paid|paid\s+(?:for\s+)?(?:the\s+)?(?:tariff|charge|parking|"
    r"fee)|tariff\s+was\s+paid|(?:the\s+)?keeper\s+paid|successfully\s+paid)\b")

# The keeper's account, attributed.
_KEEPER = r"(?:the\s+)?(?:registered\s+)?keeper"
ATTRIBUTED = _R(
    rf"\b{_KEEPER}(?:'s|’s)\s+(account|case|position|understanding|recollection|evidence|"
    r"information|instructions)\b"
    rf"|\b{_KEEPER}\s+(states?|says|reports?|has reported|has told|has explained|"
    r"explains|understands|believes|recalls|contends|maintains|advises|asserts)\b"
    rf"|\baccording to {_KEEPER}\b|\bthe account (given|provided) by {_KEEPER}\b"
    rf"|\bit is {_KEEPER}(?:'s|’s)\b"
    r"|\b(information|account|instructions|evidence|details)\b[^.]{0,40}\b(available to|"
    rf"given by|provided by|received from|supplied by|reported by)\s+{_KEEPER}\b"
    rf"|\b(?:is|are|has been|have been) (?:reported|said|understood|informed|told|advised)\b"
    rf"|\bI (?:am|have been) (?:told|informed|instructed|advised)\b"
    rf"|\b{_KEEPER} (?:was|is) (?:told|informed|advised)\b")

ENCLOSED = _R(r"\b(enclosed|attached|enclosure|appended|exhibit)\b")
PLACEHOLDER = _R(r"\{\{|\}\}|\[image \d+\]|<document id=|\bTODO\b|\bXXX+\b")
TRACE_WORDS = _R(r"\b(use_when|do_not_use_when|RetrievalPack|module_id|claim_plan|fact_id|run_id|"
                 r"building_block|knowledge_match|case_analysis|supporting_facts|inputs_digest|"
                 r"validator_feedback|claim plan|fact graph)\b")
SELF_REFERENCE = _R(r"\b(as an AI|language model|I am an AI|the system prompt|the prompt)\b")


@dataclass
class DraftValidation:
    issues: list[ValidationIssue] = field(default_factory=list)
    grounding: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity == "BLOCK" for i in self.issues)

    @property
    def ungrounded(self) -> list[dict]:
        return [g for g in self.grounding if g["status"] == UNGROUNDED]

    def summary(self) -> dict:
        by = {}
        for g in self.grounding:
            by[g["status"]] = by.get(g["status"], 0) + 1
        return {"version": VERSION, "sentences": len(self.grounding), "status": by,
                "rules": sorted({i.rule for i in self.issues}),
                "passed": self.passed}


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


class DraftValidationEngine:
    def __init__(self, kg=None):
        # Only to recognise approved wording (LEGAL). None: wording is read from
        # the pack's chunks alone.
        self.kg = kg
        self._fragments: Optional[list[str]] = None

    # ---------------------------------------------------------------- helpers
    def prompt_fragments(self) -> list[str]:
        if self._fragments is None:
            from ..integrity.checks import _prompt_fragments
            try:
                self._fragments = _prompt_fragments()
            except Exception:                              # pragma: no cover - no prompts
                self._fragments = []
        return self._fragments

    @staticmethod
    def _approved_text(pack: RetrievalPack) -> dict[str, str]:
        """module_id -> the approved wording given to the drafter for it."""
        out: dict[str, list[str]] = {}
        for c in pack.context_chunks or []:
            out.setdefault(c.get("module_id") or STRUCTURAL, []).append(str(c.get("text") or ""))
        return {k: "\n".join(v) for k, v in out.items()}

    # ------------------------------------------------------------------ check
    def check(self, draft: Draft, pack: RetrievalPack,
              fact_ids: Optional[set] = None) -> DraftValidation:
        result = DraftValidation()
        plan = getattr(pack, "claim_plan", None) or {}
        approved = list(plan.get("approved") or pack.module_ids or [])
        labels = dict(plan.get("labels") or {})
        allowed = set(approved) | {STRUCTURAL}
        by_id = {fid: name for name, fid in (pack.fact_refs or {}).items()}
        known_ids = set(fact_ids) if fact_ids is not None else set(by_id)
        uploaded = set(pack.evidence_refs or ())
        facts = pack.verified_facts or {}
        ctx = pack.case_context or {}
        customer_facts = ({n for n in (ctx.get("customer_reported_facts") or [])
                           if n not in set(ctx.get("document_established_facts") or [])}
                          | ACCOUNT_DERIVED_FACTS)
        text_by_module = self._approved_text(pack)
        all_approved_text = "\n".join(text_by_module.values())
        identified = str(pack.driver_status) == "FORMALLY_IDENTIFIED"
        fragments = self.prompt_fragments()
        verified_findings = legal.verified_types(pack.legal_findings, pack.pofa_findings)

        def add(rule, message, sentence):
            result.issues.append(ValidationIssue(rule, "BLOCK", message, sentence))

        for n, s in enumerate(draft.sentences()):
            t = s.text
            reasons: list[str] = []
            refs = [m for m in s.module_refs or []]
            claim_items = [m for m in refs if m in approved]
            names = [by_id.get(f) for f in s.fact_refs or [] if by_id.get(f)]

            # ---- CLAIM / grounding to a claim plan item
            foreign = [m for m in refs if m not in allowed]
            for m in foreign:
                add("DV-CLAIM", f"{labels.get(m) or 'That argument'} not approved in Claim Plan", t)
                reasons.append("claim_outside_plan")
            if not refs:
                add("DV-GROUND", "Sentence is not mapped to a claim plan item (use the "
                    "approved claim it supports, or STRUCTURAL for the opening, case "
                    "identification or request to cancel)", t)
                reasons.append("no_claim_plan_item")

            # ---- FACT
            unknown = [f for f in s.fact_refs or [] if f not in known_ids]
            if unknown:
                add("DV-FACT", f"Cites facts that are not in the Fact Graph: {unknown}", t)
                reasons.append("unknown_fact")
            if PAYMENT_COMPLETED.search(t) and not REPORTED.search(t) and \
                    not facts.get("payment_made"):
                add("DV-FACT", "States that a payment was made, but a completed payment is "
                    "not an established fact", t)
                reasons.append("payment_not_established")

            # ---- EVIDENCE
            bad_ev = [e for e in s.evidence_refs or [] if e not in uploaded]
            if bad_ev:
                add("DV-EVIDENCE", f"Cites evidence that was not uploaded: {bad_ev}", t)
                reasons.append("unknown_evidence")
            if ENCLOSED.search(t) and not (set(s.evidence_refs or []) & uploaded) \
                    and not self._enclosure_is_approved_wording(t, text_by_module):
                add("DV-EVIDENCE", "Says material is enclosed without referencing uploaded "
                    "evidence", t)
                reasons.append("enclosure_without_evidence")

            # ---- DRIVER
            if FIRST_PERSON_DRIVING.search(t) or \
                    (not identified and THIRD_PERSON_DRIVING.search(t)):
                add("DV-DRIVER", "States or implies who parked, drove or left the vehicle; "
                    "write about the vehicle and the keeper's account only", t)
                reasons.append("driver_statement")

            # ---- LEGAL
            m = LEGAL_CONCLUSION.search(t)
            if m and not REPORTED.search(t) and \
                    not self._conclusion_authorised(m, refs, t, text_by_module, pack):
                add("DV-LEGAL", "Legal conclusion that the approved wording and the verified "
                    "findings do not support; state the facts and put the operator to proof", t)
                reasons.append("legal_conclusion")

            # ---- LEGAL FINDING (P6.1): a specific defect needs its VERIFIED finding.
            # P7 B6: assertion-only types (no calculator yet) additionally accept
            # approved module wording - the controlled document's own proposition,
            # gated by the plan and the facts - but never the drafter's own claim.
            asserted = legal.asserted_types(t)
            hard = asserted - legal.ASSERTION_ONLY
            soft = asserted & legal.ASSERTION_ONLY
            if hard and not legal.PUT_TO_PROOF.search(t) and \
                    not (hard & verified_findings):
                add("VAL-LEGAL-FINDING",
                    f"States that a legal defect exists ({legal.describe(hard)}) "
                    "but no verified legal finding supports it; a defect may only be "
                    "stated when the deterministic calculation proved it", t)
                reasons.append("unverified_legal_defect")
            elif soft and not legal.PUT_TO_PROOF.search(t) and \
                    not (soft & verified_findings) and \
                    not self._attributed_in_wording(t, text_by_module):
                add("VAL-LEGAL-FINDING",
                    f"Asserts a defect ({legal.describe(soft)}) that no verified "
                    "finding and no approved wording supports; state the facts and "
                    "put the operator to proof instead", t)
                reasons.append("unverified_legal_defect")
            elif not asserted and legal.VAGUE_DEFECT.search(t) and not REPORTED.search(t) \
                    and not verified_findings:
                add("VAL-LEGAL-FINDING",
                    "Suggests the notice is non-compliant, but no legal defect has been "
                    "verified for this case", t)
                reasons.append("unverified_legal_defect")
            if CASE_LAW.search(t) and not CASE_LAW.search(all_approved_text):
                add("DV-LEGAL", "Names or cites case law", t)
                reasons.append("case_law")

            # ---- LEAK
            leak = (internal_ids(t) or PLACEHOLDER.search(t) or TRACE_WORDS.search(t)
                    or SELF_REFERENCE.search(t) or any(f in t for f in fragments))
            if leak:
                add("DV-LEAK", "Internal identifiers, placeholders, prompt or trace wording "
                    "in the letter", t)
                reasons.append("leak")

            # ---- ACCOUNT: the keeper's word is attributed to the keeper
            account = [x for x in names if x in customer_facts]
            if account and not ATTRIBUTED.search(t) \
                    and not self._attributed_in_wording(t, text_by_module):
                add("DV-ACCOUNT", "Presents what only the keeper says as established; attribute "
                    "it ('The keeper's account is that ...')", t)
                reasons.append("unattributed_account")

            if refs and all(r == STRUCTURAL for r in refs):
                status = STRUCTURAL_OK
            elif claim_items and not reasons:
                status = GROUNDED
            else:
                status = UNGROUNDED
            if reasons and status != UNGROUNDED and any(
                    r in reasons for r in ("claim_outside_plan", "no_claim_plan_item")):
                status = UNGROUNDED
            result.grounding.append({
                "i": n, "sentence_sha": _sha(t), "status": status,
                "claim_plan_items": claim_items,
                "facts": sorted(set(names)), "evidence": list(s.evidence_refs or []),
                "reasons": reasons})

        # ---- STRUCTURE
        if draft.paragraphs and not CANCEL_REQUEST.search(draft.plain_text()):
            add("DV-STRUCTURE", "The letter never asks for the charge to be cancelled", None)

        # ---- PARTICULARS (P6.2, letter-level): a verified timing defect that
        # licenses an approved ground must be argued on its calculation - the
        # dates and the day count - never only "outside the statutory period".
        letter = draft.plain_text() if draft.paragraphs else ""
        if letter:
            for f in pack.legal_findings or []:
                if f.get("legal_module_id") not in approved:
                    continue
                p = legal.particulars(f)
                missing = [f"{k} {v}" for k, v in sorted((p.get("dates") or {}).items())
                           if not legal.date_stated(v, letter)]
                days = p.get("days")
                if days and not legal.days_stated(days, letter):
                    missing.append(f"the day count ({days} days)")
                if missing:
                    add("VAL-PARTICULARS",
                        f"A verified finding ({f.get('description') or f.get('finding_type')}) "
                        "supports an approved ground, so the letter must set out the "
                        "calculation it rests on. State each of these and the conclusion "
                        "that keeper liability does not transfer: " + "; ".join(missing),
                        None)

        # ---- COVERAGE (P6.2, letter-level): every approved ground is argued.
        if draft.paragraphs:
            covered = {m for g in result.grounding if g["status"] == GROUNDED
                       for m in g["claim_plan_items"]}
            # Approved wording can be SHARED between sibling modules of one
            # theory (the same block listed by several modules). A sentence
            # belongs to exactly one claim, so the letter rightly states that
            # wording once - the siblings are still argued by it, not dropped.
            texts = [re.sub(r"\s+", " ", s.text.strip().lower())
                     for s in draft.sentences()]
            grounded_texts = [texts[g["i"]] for g in result.grounding
                              if g["status"] == GROUNDED and g["claim_plan_items"]]
            for mid in approved:
                if mid == STRUCTURAL or mid in covered:
                    continue
                wording = re.sub(r"\s+", " ", (text_by_module.get(mid) or "").lower())
                if wording and any(t and t in wording for t in grounded_texts):
                    continue
                add("VAL-COVERAGE",
                    f"{labels.get(mid) or mid} is approved in the Claim Plan but the "
                    "letter never argues it; every approved ground needs at least one "
                    "grounded sentence", None)
        return result

    # --------------------------------------------------------------- internals
    @staticmethod
    def _enclosure_is_approved_wording(sentence: str, text_by_module: dict) -> bool:
        """Approved wording may itself speak of enclosures (e.g. the closing
        block); the evidence rule is for the drafter's own claims."""
        low = re.sub(r"\s+", " ", sentence.lower()).strip()
        return any(low and low in re.sub(r"\s+", " ", t.lower()) for t in text_by_module.values())

    @staticmethod
    def _attributed_in_wording(sentence: str, text_by_module: dict) -> bool:
        low = re.sub(r"\s+", " ", sentence.lower()).strip()
        return any(low and low in re.sub(r"\s+", " ", t.lower()) for t in text_by_module.values())

    @staticmethod
    def _conclusion_authorised(match, refs, sentence, text_by_module, pack) -> bool:
        """A legal conclusion is allowed when the wording approved for the claims
        it cites makes a conclusion of the same kind, or a verified PoFA finding
        stands behind a PoFA conclusion."""
        said = match.group(0).lower()
        cited = [text_by_module.get(m, "") for m in refs if m != STRUCTURAL]
        if any(LEGAL_CONCLUSION.search(c or "") for c in cited):
            return True
        if said in "\n".join(cited).lower():
            return True
        if re.search(r"pofa|schedule 4|protection of freedoms", said):
            # P6.1: a conclusion that asserts a specific defect stands only on
            # that defect's VERIFIED finding; a generic Schedule 4 conclusion
            # needs at least one verified finding.
            verified = legal.verified_types(pack.legal_findings, pack.pofa_findings)
            asserted = legal.asserted_types(sentence)
            if asserted:
                return bool(asserted & verified)
            return bool(verified)
        return False


def merge(result, draft_validation: DraftValidation):
    """The existing ValidationResult plus this engine's issues, without
    repeating an issue the first already raised for the same sentence."""
    from ..models import ValidationResult
    seen = {(i.sentence, i.message) for i in result.issues}
    extra = [i for i in draft_validation.issues if (i.sentence, i.message) not in seen]
    issues = list(result.issues) + extra
    return ValidationResult(not any(i.severity == "BLOCK" for i in issues), issues)


__all__ = ["DraftValidationEngine", "DraftValidation", "merge", "VERSION", "GROUNDED",
           "STRUCTURAL_OK", "UNGROUNDED"]
