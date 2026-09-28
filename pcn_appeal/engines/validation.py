"""ENGINE 4 - Validation (independent release gate).

Two layers, both must pass:
  A. Deterministic checks (below) - cheap, exact, cannot be talked round.
  B. LLM judge (optional, separate prompt + ideally separate model) for
     semantic grounding: "does this sentence claim anything its refs don't support?"

Any BLOCK issue stops release. The orchestrator regenerates with the issues as
feedback (max N attempts) and then routes to manual review - never silently ships.

Rule pack (KB section 17 + gaps found in review)
  VAL-DRIVER   first-person driving / identification language
  VAL-FACT     sentence with dates/amounts/VRM/times but no fact refs; values must match facts
  VAL-GROUND   sentence with no fact, module or evidence ref at all
  VAL-EVIDENCE "enclosed/supplied/attached" without an uploaded evidence ref
  VAL-POFA     statutory-defect language without a verified pofa finding
  VAL-CODE     Code/grace/consideration minutes without a resolved Code version
  VAL-RES      quotes not verbatim; 'unfettered' unsupported; regulations clause ignored
  VAL-BREAK    'automatic frustration' style language
  VAL-EQ       Equality Act language without the triggering fact
  VAL-ANPR     calibration / maintenance allegations without a discrepancy fact
  VAL-STAGE    POPLA / IAS / court language at initial appeal
  VAL-CONFLICT PCN number / VRM / payment status contradicting source facts
  VAL-REPEAT   near-duplicate sentences
  VAL-OBSOLETE penalty / genuine pre-estimate argument
  VAL-LEAK     module IDs, template placeholders or AI self-reference in output
  VAL-MODULE   module_refs outside the retrieved (approved) set
"""
from __future__ import annotations

import re
from typing import Optional

from ..llm import LLMClient
from ..models import Draft, RetrievalPack, ValidationIssue, ValidationResult

R = lambda p: re.compile(p, re.I)  # noqa: E731

DRIVER_PATTERNS = [
    R(r"\bI (drove|was driving|parked|left the (car|vehicle)|returned to the (car|vehicle)|arrived|came back|"
      r"broke down|stopped|pulled in|overstayed)\b"),
    R(r"\b(my|our) (wife|husband|partner|son|daughter|friend|colleague|mother|father) (drove|was driving|parked)\b"),
    R(r"\bI was the driver\b"), R(r"\bthe driver (was|is) (me|myself|my)\b"),
    R(r"\bwhen I (got|went|returned|left)\b"),
]
EVIDENCE_CLAIM = R(r"\b(enclosed|attached|supplied with this appeal|accompanying)\b")
POFA_DEFECT = R(r"(not delivered within|did not meet the applicable statutory timing|fails to provide the route-specific|"
                r"does not contain (a compliant|the applicable statutory)|does not (properly )?comply with the applicable)")
CODE_VALUE = R(r"\b\d+[- ]minutes?\b.*\b(grace|consideration)\b|\b(grace|consideration)\b.*\b\d+[- ]minutes?\b")
UNIVERSAL_RULE = R(r"\b(10[- ]minute rule|always cancel|automatically cancel)")
BREAK_AUTO = R(r"\bautomatic(ally)? (frustrat|void|cancel)|breakdown (always|automatically)")
EQ_TERMS = R(r"\b(Equality Act|reasonable adjustment|disabilit)")
ANPR_GENERIC = R(r"\b(calibrat|camera maintenance|synchroni[sz]ation of the camera)")
STAGE = R(r"\b(POPLA|IAS|Independent Appeals Service|county court|small claims|claim form|letter of claim)\b")
OBSOLETE = R(r"(genuine pre-?estimate|unlawful penalty|penalty charge is unenforceable)")
LEAK = R(r"(\b(KB|PP|AI|VAL)-[A-Z]{2,}|\{\{|\}\}|as an AI|language model|module_id)")
DATE_TOKEN = R(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
MONEY_TOKEN = R(r"£\s?\d+")
REPORTED = R(r"\b(allegation|alleged|disputed|operator (says|claims|asserts))\b")   # reported speech, not a claim
NOT_PAID = R(r"\b(no payment was made|was not paid|did not pay|unpaid parking)\b")


def _jaccard(a: str, b: str) -> float:
    x, y = set(a.lower().split()), set(b.lower().split())
    return len(x & y) / max(len(x | y), 1)


class ValidationEngine:
    def __init__(self, judge: Optional[LLMClient] = None, allowed_next_step: str = ""):
        self.judge = judge
        self.allowed_next_step = allowed_next_step

    def validate(self, draft: Draft, pack: RetrievalPack) -> ValidationResult:
        issues: list[ValidationIssue] = []
        facts = pack.verified_facts
        fact_ids = set(pack.fact_refs.values())
        allowed_modules = set(pack.module_ids) | {"STRUCTURAL"}
        uploaded = set(pack.evidence_refs)
        lease_texts = {c["text"] for c in pack.lease_clauses}
        full = draft.plain_text()

        def block(rule, msg, s=None):
            issues.append(ValidationIssue(rule, "BLOCK", msg, s))

        seen: list[str] = []
        for s in draft.sentences():
            t = s.text
            if pack.driver_status == "UNIDENTIFIED" and any(p.search(t) for p in DRIVER_PATTERNS):
                block("VAL-DRIVER", "Driver identification / first-person driving language", t)
            if not (s.fact_refs or s.module_refs or s.evidence_refs):
                block("VAL-GROUND", "Sentence has no provenance", t)
            bad = [r for r in s.fact_refs if r not in fact_ids]
            if bad:
                block("VAL-FACT", f"Unknown fact refs {bad}", t)
            if (DATE_TOKEN.search(t) or MONEY_TOKEN.search(t)) and not s.fact_refs:
                block("VAL-FACT", "Date/amount stated without a fact reference", t)
            badm = [m for m in s.module_refs if m not in allowed_modules]
            if badm:
                block("VAL-MODULE", f"Module(s) not in approved retrieval set: {badm}", t)
            if EVIDENCE_CLAIM.search(t) and not (set(s.evidence_refs) & uploaded):
                block("VAL-EVIDENCE", "Claims evidence is enclosed but none is uploaded/referenced", t)
            if POFA_DEFECT.search(t) and not pack.pofa_findings:
                block("VAL-POFA", "PoFA defect alleged without verified finding", t)
            if CODE_VALUE.search(t) and not pack.code_version:
                block("VAL-CODE", "Code value used without resolved Code version", t)
            if UNIVERSAL_RULE.search(t):
                block("VAL-CODE", "Universal cancellation rule stated", t)
            if s.quote_of or re.search(r'"[^"]{12,}"', t):
                for q in re.findall(r'"([^"]{12,})"', t):
                    if not any(q.strip() in lt for lt in lease_texts):
                        block("VAL-RES", "Quoted text is not verbatim from an uploaded agreement", t)
            if re.search(r"\bunfettered\b", t, re.I) and not any("unfettered" in lt.lower() for lt in lease_texts):
                block("VAL-RES", "'Unfettered' not supported by the uploaded agreement", t)
            if BREAK_AUTO.search(t):
                block("VAL-BREAK", "Breakdown presented as automatic", t)
            if EQ_TERMS.search(t) and not facts.get("disability_extra_time"):
                block("VAL-EQ", "Equality ground without triggering facts", t)
            if ANPR_GENERIC.search(t) and not facts.get("anpr_discrepancy"):
                block("VAL-ANPR", "Generic calibration allegation without factual trigger", t)
            if STAGE.search(t) and t.strip() != self.allowed_next_step:
                block("VAL-STAGE", "Wrong-stage language in an initial operator appeal", t)
            if OBSOLETE.search(t):
                block("VAL-OBSOLETE", "Obsolete penalty / pre-estimate argument", t)
            if LEAK.search(t):
                block("VAL-LEAK", "Internal IDs, placeholders or AI self-reference in output", t)
            if facts.get("payment_made") and NOT_PAID.search(t) and not REPORTED.search(t):
                block("VAL-CONFLICT", "Contradicts confirmed payment", t)
            for prev in seen:
                if _jaccard(prev, t) > 0.8:
                    issues.append(ValidationIssue("VAL-REPEAT", "BLOCK", "Near-duplicate sentence", t))
                    break
            seen.append(t)

        # document-level checks
        pcn = str(facts.get("pcn_number", ""))
        if pcn and pcn not in full:
            block("VAL-CONFLICT", "PCN number missing or altered")
        for tok in re.findall(r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b", full):
            if tok.replace(" ", "") != facts.get("vrm"):
                block("VAL-CONFLICT", f"VRM {tok} does not match source VRM")
        if facts.get("lease_has_regulations_clause") and "KB-RES-06" in pack.module_ids and \
                not any("KB-RES-06" in s.module_refs for s in draft.sentences()):
            block("VAL-RES", "Lease regulations/permit clause exists but the draft does not address it")
        if "consideration" in full.lower() and "grace" in full.lower() and \
                re.search(r"consideration (and|plus|\+) grace (period )?(allowance|of \d+)", full, re.I):
            block("VAL-CODE", "Consideration and grace merged into one allowance")

        if self.judge and not any(i.severity == "BLOCK" for i in issues):
            issues += self._llm_judge(draft, pack)

        return ValidationResult(not any(i.severity == "BLOCK" for i in issues), issues)

    def _llm_judge(self, draft: Draft, pack: RetrievalPack) -> list[ValidationIssue]:
        import json
        system = ("You are an independent checker. For each sentence decide if it asserts anything not "
                  "supported by its cited facts/modules. Return {\"issues\":[{\"rule\":\"VAL-FACT\",\"sentence\":..,"
                  "\"message\":..}]}. Return an empty list if all are supported.")
        user = json.dumps({"sentences": [s.__dict__ for s in draft.sentences()],
                           "facts": pack.verified_facts, "chunks": pack.context_chunks}, default=str)
        out = self.judge.complete_json(task="validation", system=system, user=user)
        return [ValidationIssue(i.get("rule", "VAL-FACT"), "BLOCK", i.get("message", ""), i.get("sentence"))
                for i in out.get("issues", [])]
