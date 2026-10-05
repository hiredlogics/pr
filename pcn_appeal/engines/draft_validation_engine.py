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
    r"|\bthe account is (therefore )?that\b"
    rf"|\bit is {_KEEPER}(?:'s|’s)\b"
    r"|\b(information|account|instructions|evidence|details)\b[^.]{0,40}\b(available to|"
    rf"given by|provided by|received from|supplied by|reported by)\s+{_KEEPER}\b"
    rf"|\b(?:is|are|has been|have been) (?:reported|said|understood|informed|told|advised)\b"
    rf"|\bI (?:am|have been) (?:told|informed|instructed|advised)\b"
    rf"|\b{_KEEPER} (?:was|is) (?:told|informed|advised)\b")

ENCLOSED = _R(r"\b(enclosed|attached|enclosure|appended|exhibit)\b")
PLACEHOLDER = _R(
    r"\{\{|\}\}|\[image \d+\]|"
    r"\[(?:PLACEHOLDER|TODO|TBD)[^\]]*\]|<document id=|\bTODO\b|\bXXX+\b"
)
UNRESOLVED_TOKEN = re.compile(r"\{[A-Z][A-Z0-9_]{2,}\}")
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

            # ---- LEGAL FINDING (P6.1): a specific defect needs its VERIFIED finding
            asserted = legal.asserted_types(t)
            if asserted and not legal.PUT_TO_PROOF.search(t) and \
                    not (asserted & verified_findings):
                add("VAL-LEGAL-FINDING",
                    f"States that a legal defect exists ({legal.describe(asserted)}) "
                    "but no verified legal finding supports it; a defect may only be "
                    "stated when the deterministic calculation proved it", t)
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
            leak = (internal_ids(t) or PLACEHOLDER.search(t) or UNRESOLVED_TOKEN.search(t)
                    or TRACE_WORDS.search(t)
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
            for mid in approved:
                if mid != STRUCTURAL and mid not in covered:
                    add("VAL-COVERAGE",
                        f"{labels.get(mid) or mid} is approved in the Claim Plan but the "
                        "letter never argues it; every approved ground needs at least one "
                        "grounded sentence", None)

        # ---- GROUND COVERAGE via DraftPlan (P10.6): section + text + semantic expression.
        # Filler-only paragraphs do not count. BLOCK RELEASE on failure.
        from ..drafting.plan import (
            build_draft_plan, particular_expressed, section_expresses_ground,
        )
        from ..module_roles import (
            SUPPORTING_PROPOSITION,
            SUBSTANTIVE_GROUND,
            LEGAL_CONCLUSION as MODULE_LEGAL_CONCLUSION,
            role_of,
        )
        draft_plan = build_draft_plan(pack)
        letter_by_ground: dict[str, str] = {}
        for s in draft.sentences() if draft.paragraphs else []:
            for mid in s.module_refs or []:
                letter_by_ground[mid] = (letter_by_ground.get(mid) or "") + " " + (s.text or "")
        owned = draft_plan.owned_grounds()
        for section in draft_plan.sections:
            text = " ".join(letter_by_ground.get(m, "") for m in section.ground_ids).strip()
            if not text:
                # Merged ownership may put text under one ground_id only
                text = " ".join(
                    letter_by_ground.get(m, "") for m in section.ground_ids
                ).strip() or letter
            expressed = section_expresses_ground(text, section)
            if not expressed:
                add("VAL-GROUND-COVERAGE",
                    f"DraftSection {section.section_id} for "
                    f"{', '.join(section.ground_ids)} has no semantic expression in the "
                    f"letter (section must exist, render text, and express the ground; "
                    f"generic filler does not count)", None)
            # Also alias into VAL-COVERAGE for grounds missing from sentence links
            for mid in section.ground_ids:
                if mid not in owned:
                    continue
                linked = bool((letter_by_ground.get(mid) or "").strip())
                if not linked and not expressed:
                    add("VAL-COVERAGE",
                        f"{labels.get(mid) or mid} is in the DraftPlan but was not linked "
                        "to any rendered section text", None)

        # ---- LINEAGE / SUPPORT / PARTICULARS (P8.5 + P10.6)
        from ..drafting.support_contract import DraftRequirement, SupportBundle
        from .narrative import NARRATIVE_FACTS
        support_facts = (plan.get("support_facts") or {}) if isinstance(plan, dict) else {}
        bundles = (plan.get("support_bundles") or {}) if isinstance(plan, dict) else {}
        reqs = (plan.get("draft_requirements") or {}) if isinstance(plan, dict) else {}
        cited_by_ground: dict[str, set] = {}
        for g in result.grounding:
            for mid in g.get("claim_plan_items") or []:
                cited_by_ground.setdefault(mid, set()).update(g.get("facts") or [])
        for mid in approved:
            if mid == STRUCTURAL:
                continue
            # Support-only modules do not require standalone particular coverage.
            # Legal conclusions still need finding particulars when they appear.
            if role_of(mid) == SUPPORTING_PROPOSITION:
                continue
            names = list(support_facts.get(mid) or [])
            bundle = SupportBundle.from_dict(bundles.get(mid))
            req = DraftRequirement.from_dict(reqs.get(mid))
            derived = list(bundle.derived_fact_names)
            sources = list(bundle.source_fact_names)
            if derived and not sources:
                add("VAL-LINEAGE",
                    f"{labels.get(mid) or mid} uses a derived fact "
                    f"({', '.join(derived)}) without source facts or a derivation "
                    "relationship", None)
            narrative_deps = [n for n in names if n in NARRATIVE_FACTS]
            bridge = [n for n in names if n not in NARRATIVE_FACTS]
            if narrative_deps and bridge and not any(n in facts for n in bridge):
                add("VAL-LINEAGE",
                    f"{labels.get(mid) or mid} is supported by narrative atoms "
                    f"({', '.join(narrative_deps)}) that drafting withholds; a derived "
                    f"fact ({', '.join(bridge)}) must remain in the draft context so the "
                    "letter can particularise the ground without customer prose",
                    None)
            elif narrative_deps and not bridge and not any(
                    n in facts for n in ("multiple_visits", "account_contradicts_allegation")):
                add("VAL-LINEAGE",
                    f"{labels.get(mid) or mid} depends on withheld narrative atoms "
                    f"({', '.join(narrative_deps)}) with no derived fact in draft context",
                    None)
            if mid in bundles or mid in reqs:
                if not bundle.complete() or not req.required_particulars:
                    add("VAL-CLAIM-PLAN-SUPPORT",
                        f"{labels.get(mid) or mid} cannot proceed to drafting: every "
                        "SUPPORTED claim needs a complete SupportBundle and a "
                        "DraftRequirement", None)
            if not draft.paragraphs:
                continue
            from ..drafting.plan import LETTER_PARTICULARS, NON_LETTER_PARTICULARS
            section = next((s for s in draft_plan.sections if mid in s.ground_ids), None)
            section_text = ""
            if section:
                section_text = " ".join(
                    letter_by_ground.get(m, "") for m in section.ground_ids
                ).strip() or letter
            cited = cited_by_ground.get(mid, set())
            missing_part = []
            linked_findings = [f for f in (pack.legal_findings or [])
                               if f.get("legal_module_id") == mid
                               or (f.get("finding_id") or f.get("finding_type") or "")
                               in bundle.legal_finding_ids]
            # Material letter particulars only (not gating meta-facts).
            material = []
            if section and section.required_particulars:
                material.extend(
                    n for n in section.required_particulars
                    if n not in NON_LETTER_PARTICULARS
                )
            else:
                material.extend(n for n in req.required_particulars if n in NARRATIVE_FACTS)
                material.extend(
                    n for n in req.required_particulars
                    if n in LETTER_PARTICULARS and n not in NON_LETTER_PARTICULARS
                )
            for f in linked_findings:
                p = legal.particulars(f)
                material.extend(p.get("dates") or {})
                if p.get("days") is not None:
                    material.append("days_late")
            material = list(dict.fromkeys(material))
            for name in material:
                if name in NON_LETTER_PARTICULARS:
                    continue
                value = (section.particular_values.get(name) if section else None)
                if value in (None, "", False):
                    value = facts.get(name)
                if value in (None, "", False) and name not in ("days_late", "days"):
                    if not any(
                            name in ((legal.particulars(f).get("dates") or {}))
                            or (name in ("days_late", "days") and legal.particulars(f).get("days"))
                            for f in linked_findings):
                        continue
                if name in cited:
                    continue
                check_text = section_text or letter
                if check_text and particular_expressed(check_text, name, value):
                    continue
                if value not in (None, "", False) and legal.date_stated(str(value), letter):
                    continue
                found = False
                for f in linked_findings:
                    p = legal.particulars(f)
                    dates = p.get("dates") or {}
                    if name in dates and legal.date_stated(dates[name], letter):
                        found = True
                        break
                    if name in ("days_late", "days") and p.get("days") \
                            and legal.days_stated(p["days"], letter):
                        found = True
                        break
                if found:
                    continue
                missing_part.append(name)
            for f in linked_findings:
                p = legal.particulars(f)
                for key, value in sorted((p.get("dates") or {}).items()):
                    if not legal.date_stated(value, letter) and key not in missing_part:
                        missing_part.append(key)
                days = p.get("days")
                if days and not legal.days_stated(days, letter) and "days_late" not in missing_part:
                    missing_part.append("days_late")
            if missing_part:
                sid = section.section_id if section else "?"
                fact_hint = ", ".join(
                    (section.supporting_fact_ids[:3] if section else []) or []
                ) or "n/a"
                add("VAL-DRAFT-PARTICULARS",
                    f"ground_id={mid}; section_id={sid}; missing particular(s)="
                    f"{', '.join(missing_part)}; supporting_fact_ids={fact_hint}; "
                    "a summary of the conclusion is not a substitute for the material "
                    "factual sequence", None)
                # Explicit material-coverage block for substantive grounds.
                if role_of(mid) in (SUBSTANTIVE_GROUND,):
                    add("VAL-MATERIAL-FACT-COVERAGE",
                        f"ground_id={mid}; section_id={sid}; material particular(s) "
                        f"missing from letter meaning: {', '.join(missing_part)}; "
                        "BLOCK release — professional paraphrase required, not omission",
                        None)
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
