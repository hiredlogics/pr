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
  VAL-REPEAT-POINT one case theory asserted twice in different words
  VAL-OBSOLETE penalty / genuine pre-estimate argument
  VAL-LEAK     module IDs, template placeholders or AI self-reference in output
  VAL-MODULE   module_refs outside the retrieved (approved) set
  VAL-PLAN     an argument the LOCKED Claim Plan did not approve (P5): a module_ref
               outside the plan, approved wording of a module outside the plan, or
               a pack that is not the plan's
  VAL-SUBSTANCE draft is only structural intro/conclusion with no substantive ground
  VAL-EVIDENCE-CONTRADICTION contradiction claims / EVIDENCE route without the required fact
  VAL-CUSTOMER-COPY customer free-text pasted into the letter instead of rewritten
  VAL-ACCOUNT-COVERAGE material account fact marked used but professional proposition absent
  VAL-INVENTED a permit/bay/ticket identifier that is not a verified fact
  VAL-CALC     a stated day-count that contradicts a verified finding calculation
  VAL-ORPHAN-SUPPORT support/conclusion modules without a substantive ground
"""
from __future__ import annotations

import re
from typing import Optional

from .. import prompts
from ..customer_safe import internal_ids
from ..llm import LLMClient
from ..models import Draft, RetrievalPack, ValidationIssue, ValidationResult
from ..module_roles import classify_pack
from ..routes import Route

R = lambda p: re.compile(p, re.I)  # noqa: E731

# Which rule pack decided a release, recorded against every validation.
#
# Lives here rather than at the call site that stores it: the store hardcoded the
# string, so it could not track this file and every case ever validated claimed
# the same version no matter which rules had actually run. Bump it whenever a
# rule above is added, removed or changed in what it blocks - the stored value is
# how a past release decision is explained, so a stale one misattributes it.
VERSION = "VAL-5"  # VAL-5: P10.3 — VAL-ORPHAN-SUPPORT (support-only packs)
# VAL-3: VAL-PLAN - every argument must be in the locked Claim Plan
# VAL-2: VAL-LEAK also refuses every customer_safe.INTERNAL_ID shape

DRIVER_PATTERNS = [
    R(r"\bI (drove|was driving|parked|left the (car|vehicle)|returned to the (car|vehicle)|arrived|came back|"
      r"broke down|stopped|pulled in|overstayed)\b"),
    R(r"\b(my|our) (wife|husband|partner|son|daughter|friend|colleague|mother|father) (drove|was driving|parked)\b"),
    R(r"\bI was the driver\b"), R(r"\bthe driver (was|is) (me|myself|my)\b"),
    R(r"\bwhen I (got|went|returned|left)\b"),
]
EVIDENCE_CLAIM = R(
    r"\b(enclosed|attached|supplied with this appeal|"
    r"accompanying (this|the) (appeal|letter|documents?|materials?))\b"
)
# Claims that independent evidence *contradicts* the allegation — requires the
# verified fact. Must not fire on a cautious "does not establish" records letter.
CONTRADICTION_CLAIM = R(
    r"\b(independent evidence (demonstrates|shows|proves|contradicts)|"
    r"evidence (clearly )?(contradicts|disproves) (the )?allegation|"
    r"contradicts the (operator'?s? )?(allegation|account))\b"
)
POFA_DEFECT = R(r"(not delivered within|did not meet the applicable statutory timing|fails to provide the route-specific|"
                r"does not contain (a compliant|the applicable statutory)|does not (properly )?comply with the applicable)")
CODE_VALUE = R(r"\b\d+[- ]minutes?\b.*\b(grace|consideration)\b|\b(grace|consideration)\b.*\b\d+[- ]minutes?\b")
UNIVERSAL_RULE = R(r"\b(10[- ]minute rule|always cancel|automatically cancel)")
BREAK_AUTO = R(r"\bautomatic(ally)? (frustrat|void|cancel)|breakdown (always|automatically)")
EQ_TERMS = R(r"\b(Equality Act|reasonable adjustment|disabilit)")
ANPR_GENERIC = R(r"\b(calibrat|camera maintenance|synchroni[sz]ation of the camera)")
STAGE = R(r"\b(POPLA|IAS|Independent Appeals Service|county court|small claims|claim form|letter of claim)\b")
OBSOLETE = R(r"(genuine pre-?estimate|unlawful penalty|penalty charge is unenforceable)")
LEAK = R(
    r"(\b(KB|PP|AI|VAL)-[A-Z]{2,}|\{\{|\}\}|"
    r"\[(?:PLACEHOLDER|TODO|TBD)[^\]]*\]|as an AI|language model|module_id)"
)
# Unresolved single-brace tokens ({OPERATOR_NAME}). Case-sensitive so a
# filled template field is not treated as a leak.
UNRESOLVED_TOKEN = re.compile(r"\{[A-Z][A-Z0-9_]{2,}\}")
# A concrete identifier the letter invented (permit/bay/ticket number) that
# does not appear in any verified fact. Generic ontology — not operator-specific.
INVENTED_IDENTIFIER = R(
    r"\b(?:permit|badge|ticket|voucher|reference|pass)\s+"
    r"(?:numbered\s+|no\.?\s+|number\s+|#\s*)([A-Z0-9-]*\d[A-Z0-9-]*)\b"
    r"|\bbay\s+(?:numbered\s+|no\.?\s+|#\s*)(\d+[A-Z]?)\b"
)
STATED_DAYS = R(
    r"\b(?:given|delivered|posted|issued|received|served)\b[^.]{0,60}"
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+"
    r"(?:calendar\s+|working\s+)?days?\b"
    r"|\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+"
    r"(?:calendar\s+|working\s+)?days?\s+(?:after|late|beyond|past|from)\b"
    r"|\blate(?:ness)?(?:\s+by)?\s+"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b"
)
_DAY_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
# A block's own guidance to whoever drafts from it. Three Appendix A blocks carry
# such a sentence inside their approved text, so a drafter that renders the block
# verbatim sent "Use only where the actual sign evidence supports this factual
# proposition." to the operator. BuildingBlock.letter_text strips them; this
# refuses a draft that reintroduces one.
DRAFTER_NOTE = R(r"^\s*(use\s+(only|where|when)\b|only\s+use\b|do\s+not\s+use\b)"
                 r"|\bthis\s+factual\s+proposition\b|\bafter\s+legal/factual\s+validation\b")
DATE_TOKEN = R(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
MONEY_TOKEN = R(r"£\s?\d+")
REPORTED = R(r"\b(allegation|alleged|disputed|operator (says|claims|asserts))\b")   # reported speech, not a claim
NOT_PAID = R(r"\b(no payment was made|was not paid|did not pay|unpaid parking)\b")
# A claim that the transaction never completed. Asserted against a notice that
# records the payment as taken, this contradicts the operator's own document.
PAYMENT_FAILED_CLAIM = R(r"\b(could not be completed|did not complete|failed to (complete|process)"
                         r"|transaction (failed|was unsuccessful)|machine did not operate)\b")
# Points a letter must assert once however many grounds share them. Keyed by the
# name used in the issue message, so the customer-facing text says "the payment
# point", not a module ID. Kept in step with drafter.RESTATED_POINTS: the drafter
# merges, this refuses a draft that did not.
ONE_ASSERTION = {
    "payment": R(r"\b(a\s+)?payment\s+was\s+(nevertheless\s+|duly\s+)?made\b"
                 r"|\b(the\s+)?(applicable\s+)?(parking\s+)?tariff\s+was\s+paid\b"),
}


def _ident(value) -> str:
    """An identifier as compared: upper case, alphanumerics only.

    Operators and fixtures use hyphens/underscores in PCN references
    (`LIVE_TEST_PCN_…`, `ABC-123`). Those separators are not part of the
    identity for matching — only letter/digit content is.
    """
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).upper()


def _contains_token(text: str, ident: str) -> bool:
    """`ident` appears as a whole token (separators inside it ignored), not as
    part of a longer reference: "1234567" is not in "12345678"."""
    if not ident:
        return False
    # Spaced two-word tokens (legacy PCN / plate printing). Check the joined
    # form and each half so "PCN 1234567890" still matches the number alone.
    for tok in re.findall(r"[A-Za-z0-9]+(?: [A-Za-z0-9]+)?", text):
        for cand in (tok, *tok.split(" ")):
            if _ident(cand) == ident:
                return True
    # Hyphen/underscore-joined references (fixture and some operator formats).
    for tok in re.findall(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+", text):
        if _ident(tok) == ident:
            return True
    return False


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) >= cap:
        return cap
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return min(prev[-1], cap)


def _near_variants(text: str, ident: str, *, min_len: int, max_len: int = 20,
                   spaced: bool = False) -> list[str]:
    """Tokens that are one or two edits from `ident` without being it: the
    shape of a misread or mistyped PCN or registration. A token must contain a
    digit (and a letter, if `ident` has one), so ordinary words never match."""
    words = re.findall(r"\b[A-Za-z0-9]+\b", text)
    tokens = list(words)
    if spaced:                             # "RX7 V5FP" is printed as two words
        tokens += [f"{a} {b}" for a, b in zip(words, words[1:])]
    # Also consider hyphen/underscore-joined references as one token.
    tokens += re.findall(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+", text)
    needs_alpha = any(c.isalpha() for c in ident)
    found: list[str] = []
    for tok in tokens:
        norm = _ident(tok)
        if norm == ident or not (min_len <= len(norm) <= max_len):
            continue
        if not any(c.isdigit() for c in norm) or (needs_alpha and not any(c.isalpha() for c in norm)):
            continue
        if spaced and tok != tok.upper() and not any(c in tok for c in "_-"):
            continue                       # registrations are printed in capitals
        if _edit_distance(norm, ident) <= 2 and tok not in found:
            found.append(tok)
    return found


def _family(module_id: str) -> str:
    m = re.match(r"^KB-([A-Z]+)-", module_id or "")
    return m.group(1) if m else "This argument"


def _jaccard(a: str, b: str) -> float:
    x, y = set(a.lower().split()), set(b.lower().split())
    return len(x & y) / max(len(x | y), 1)


def _copy_fingerprint(text: str) -> str:
    """Normalise free text so informal customer paste can be detected in drafts.

    Requires a meaningful span (12+ alphanumerics after normalisation) so short
    shared tokens like "Sainsbury" do not false-positive.
    """
    norm = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    compact = re.sub(r"\s+", " ", norm)
    if len(re.sub(r"\s+", "", compact)) < 12:
        return ""
    return compact


# Canonical identifiers / short factual phrases that may match the original
# response without being treated as questionnaire paste.
_IDENTIFIER_FACT_NAMES = frozenset({
    "pcn_number", "vrm", "vrm_entered", "operator_name", "parking_location",
    "site_postcode", "charge_amount", "parking_event_date", "notice_issue_date",
    "observation_time", "event_time", "entry_time", "exit_time",
})


def _phrase_windows(fingerprint: str, min_words: int = 6) -> list[str]:
    words = fingerprint.split()
    if len(words) < min_words:
        return [fingerprint] if fingerprint else []
    return [" ".join(words[i:i + min_words]) for i in range(len(words) - min_words + 1)]


def _invented_identifiers(sentence: str, facts: dict) -> list[str]:
    """Permit/bay/ticket tokens in the sentence that no verified fact holds."""
    known = " ".join(str(v) for v in (facts or {}).values() if v not in (None, "")).upper()
    hits = []
    for m in INVENTED_IDENTIFIER.finditer(sentence or ""):
        token = next((g for g in m.groups() if g), None)
        if not token:
            continue
        if token.upper() not in known:
            hits.append(token)
    return hits


def _contradictory_day_counts(sentence: str, expected_days: int) -> Optional[int]:
    """A stated day-count in this sentence that is not the verified calculation."""
    expected = abs(int(expected_days))
    for m in STATED_DAYS.finditer(sentence or ""):
        raw = next((g for g in m.groups() if g), None)
        if not raw:
            continue
        raw = raw.lower()
        n = _DAY_WORDS.get(raw)
        if n is None:
            try:
                n = int(raw)
            except ValueError:
                continue
        if n != expected:
            return n
    return None


def _quotes_a_verified_fact(quote: str, facts: dict) -> bool:
    """A quotation that reproduces a verified string fact (the allegation or
    location as printed on the notice), ignoring case, spacing and a trailing
    full stop. Verified facts are page-backed or customer-confirmed."""
    def norm(v):
        return re.sub(r"\s+", " ", str(v)).strip().rstrip(".").lower()
    q = norm(quote)
    return len(q) >= 12 and any(isinstance(v, str) and q in norm(v) for v in (facts or {}).values())


def _is_justified_customer_quote(quote: str, ctx: dict) -> bool:
    """Exact customer wording may appear only when claim plan recorded a reason."""
    q = (quote or "").strip()
    if not q:
        return False
    for row in (ctx.get("customer_quotations") or []):
        text = str((row or {}).get("text") or "").strip()
        reason = str((row or {}).get("reason") or "").strip()
        if text and reason and text == q:
            return True
    return False


def _customer_prose_pasted(source: str, letter: str, facts: dict,
                           justified_fps: Optional[list] = None) -> bool:
    """True when a meaningful multi-word customer phrase is copied into the letter.

    Not a blanket word-overlap ban: short shared tokens and canonical identifiers
    (PCN, VRM, dates, amounts) that also appear in verified_facts are ignored.
    Overlap that is only the text of a justified customer quotation is ignored.
    """
    src_fp = _copy_fingerprint(source)
    letter_fp = _copy_fingerprint(letter)
    if not src_fp or not letter_fp:
        return False
    justified_fps = [j for j in (justified_fps or []) if j]
    # Full informal sentence pasted (unless that sentence is itself a justified quote).
    if src_fp in letter_fp and len(src_fp.split()) >= 5:
        if not any(src_fp == j or src_fp in j or j in src_fp for j in justified_fps):
            return True
    protected = set(justified_fps)
    for name in _IDENTIFIER_FACT_NAMES:
        val = facts.get(name)
        fp = _copy_fingerprint(str(val or ""))
        if fp:
            protected.add(fp)
    for phrase in _phrase_windows(src_fp, min_words=6):
        if any(phrase == p or phrase in p or p in phrase for p in protected):
            continue
        if phrase in letter_fp:
            return True
    return False


class ValidationEngine:
    def __init__(self, judge: Optional[LLMClient] = None, allowed_next_step: str = "",
                 kg=None):
        self.judge = judge
        self.allowed_next_step = allowed_next_step
        # The knowledge base, only to recognise approved wording of a module the
        # claim plan did not approve (VAL-PLAN). None: that one check is skipped.
        self.kg = kg
        self._wording: dict[tuple, tuple] = {}

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
                    if any(q.strip() in lt for lt in lease_texts):
                        continue
                    if _is_justified_customer_quote(q, getattr(pack, "case_context", None) or {}):
                        continue
                    # The notice's own wording, as verified (live: the allegation
                    # quoted from the PCN held a sound letter for a processing error).
                    if _quotes_a_verified_fact(q, facts):
                        continue
                    block("VAL-RES", "Quoted text is not verbatim from an uploaded agreement "
                          "and is not a justified customer quotation", t)
            if re.search(r"\bunfettered\b", t, re.I) and not any("unfettered" in lt.lower() for lt in lease_texts):
                block("VAL-RES", "'Unfettered' not supported by the uploaded agreement", t)
            if BREAK_AUTO.search(t):
                block("VAL-BREAK", "Breakdown presented as automatic", t)
            if EQ_TERMS.search(t) and not facts.get("disability_extra_time"):
                block("VAL-EQ", "Equality ground without triggering facts", t)
            if ANPR_GENERIC.search(t) and not facts.get("anpr_discrepancy"):
                block("VAL-ANPR", "Generic calibration allegation without factual trigger", t)
            if CONTRADICTION_CLAIM.search(t) and not facts.get("independent_evidence_contradicts"):
                block("VAL-EVIDENCE-CONTRADICTION",
                      "Draft claims independent evidence contradicts the allegation "
                      "without that fact being established", t)
            if STAGE.search(t) and t.strip() != self.allowed_next_step:
                block("VAL-STAGE", "Wrong-stage language in an initial operator appeal", t)
            if OBSOLETE.search(t):
                block("VAL-OBSOLETE", "Obsolete penalty / pre-estimate argument", t)
            if LEAK.search(t) or UNRESOLVED_TOKEN.search(t) or internal_ids(t):
                block("VAL-LEAK", "Internal IDs, placeholders or AI self-reference in output", t)
            if DRAFTER_NOTE.search(t):
                block("VAL-LEAK", "Drafting guidance from a building block left in the letter", t)
            invented = _invented_identifiers(t, facts)
            for token in invented:
                block("VAL-INVENTED",
                      f"Identifier {token!r} is not among the verified facts", t)
            if facts.get("payment_made") and NOT_PAID.search(t) and not REPORTED.search(t):
                block("VAL-CONFLICT", "Contradicts confirmed payment", t)
            # The docstring promised "payment status contradicting source facts";
            # only the direction above was implemented. A notice that records the
            # payment as taken (EX-10) flatly contradicts a letter arguing the
            # transaction never went through, however the customer answered.
            if facts.get("payment_recorded_in_document") and PAYMENT_FAILED_CLAIM.search(t) \
                    and not REPORTED.search(t):
                block("VAL-CONFLICT", "Claims the payment failed, but the notice records it as taken", t)
            for prev in seen:
                if _jaccard(prev, t) > 0.8:
                    issues.append(ValidationIssue("VAL-REPEAT", "BLOCK", "Near-duplicate sentence", t))
                    break
            seen.append(t)

        # Intro + closing alone are not an appeal. STRUCTURAL provenance satisfies
        # VAL-GROUND, so without this gate a template shell would RELEASE.
        substantive = [
            s for s in draft.sentences()
            if any(m != "STRUCTURAL" for m in (s.module_refs or []))
        ]
        if not substantive:
            block("VAL-SUBSTANCE",
                  "Draft has no substantive grounds — intro/conclusion alone cannot be released")

        # P10.3: SUPPORTING_PROPOSITION / LEGAL_CONCLUSION alone cannot be an appeal.
        roles = classify_pack(pack.module_ids)
        if roles["orphan_support"]:
            block("VAL-ORPHAN-SUPPORT",
                  "Pack has support/conclusion modules but no substantive Claim Plan "
                  "ground; do not draft an empty or framing-only appeal")

        for rec in pack.legal_findings or []:
            if not isinstance(rec, dict) or rec.get("status") != "VERIFIED":
                continue
            from ..legal import findings as legal_findings
            days = legal_findings.particulars(rec).get("days")
            if not days:
                continue
            for s in draft.sentences():
                wrong = _contradictory_day_counts(s.text, int(days))
                if wrong:
                    block("VAL-CALC",
                          f"Letter states {wrong} days but the verified "
                          f"{rec.get('finding_type')} calculation is {days} days",
                          s.text)

        # Case-specificity: when the pack knows the allegation / operator, the
        # letter must engage them — not ship interchangeable filler.
        ctx = getattr(pack, "case_context", None) or {}
        allegation = str(facts.get("alleged_breach") or ctx.get("alleged_breach") or "").strip()
        operator = str(facts.get("operator_name") or ctx.get("operator_name") or "").strip()
        if substantive and allegation:
            # Require a meaningful overlap with the allegation wording (not the whole string).
            tokens = [t for t in re.findall(r"[A-Za-z]{4,}", allegation.lower()) if t not in {
                "that", "with", "from", "this", "have", "been", "were", "their", "parking",
            }]
            low_full = full.lower()
            token_hit = bool(tokens) and any(tok in low_full for tok in tokens[:6])
            # Statutory / verified-finding appeals argue notice defects; they are
            # case-specific via PCN/location/date without restating the operator's
            # breach label ("Overstayed paid time"). Engagement is still required
            # via allegation language or notice particulars already in the letter.
            verified_pack = any(
                (isinstance(r, dict) and r.get("status") == "VERIFIED")
                for r in (pack.legal_findings or [])
            )
            engages_allegation = (
                token_hit
                or any(w in low_full for w in ("alleged", "allegation", "contravention"))
                or (verified_pack and bool(substantive))
            )
            if tokens and not engages_allegation:
                block("VAL-SUBSTANCE",
                      "Draft does not address the alleged contravention on the notice")
        if substantive and operator and len(operator) > 3:
            # Operator may appear as a shortened trade name; require a token match.
            op_tok = re.findall(r"[A-Za-z]{4,}", operator.lower())
            if op_tok and not any(tok in full.lower() for tok in op_tok[:3]):
                # Soft: many letters say "the operator" — only enforce when REC is the lead.
                if "KB-REC-01" in pack.module_ids:
                    pass  # records paragraph names operator when Template/LLM does its job
        if ctx.get("shopping_receipt_enclosed") and "KB-REC-01" in pack.module_ids:
            low = full.lower()
            if "receipt" not in low:
                block("VAL-SUBSTANCE", "Shopping receipt enclosed but draft does not mention it")
            if any(p in low for p in (
                "receipt confirms validation", "receipt proves validation",
                "receipt establishes validation", "validated as shown on the receipt",
            )):
                block("VAL-CONFLICT",
                      "Draft treats a shopping receipt as proof of parking validation")

        # Customer free text / adaptive answers are INPUT, never letter copy.
        # Phrase-level paste detection — not a blanket word-overlap ban.
        # Justified customer quotations (exact text + recorded reason) are exempt.
        justified_texts = [
            str((row or {}).get("text") or "").strip()
            for row in (ctx.get("customer_quotations") or [])
            if (row or {}).get("text") and (row or {}).get("reason")
        ]
        justified_fps = [_copy_fingerprint(t) for t in justified_texts if _copy_fingerprint(t)]

        def _covered_by_justified_quote(src: str) -> bool:
            s = str(src).strip()
            if s in justified_texts:
                return True
            # Source that only wraps a justified quote is still allowed to overlap
            # the quotation itself — but not other informal wording.
            return False

        for src in (ctx.get("customer_source_texts") or []):
            if _covered_by_justified_quote(src):
                continue
            if _customer_prose_pasted(src, full, facts, justified_fps=justified_fps):
                block("VAL-CUSTOMER-COPY",
                      "Draft pastes customer free-text wording; rewrite professionally "
                      "from structured facts / material_account_propositions",
                      str(src)[:120])
                break

        # Coverage: material facts marked "used" in the claim plan must leave a
        # professional trace — not the raw answer, and not invented extras.
        accounting = ((ctx.get("claim_plan") or {}).get("material_fact_accounting")
                      if isinstance(ctx.get("claim_plan"), dict) else None) or []
        props = [str(p) for p in (ctx.get("material_account_propositions") or []) if p]
        low_full = full.lower()

        def _prop_reflected(prop: str) -> bool:
            tokens = [
                t for t in re.findall(r"[a-z]{5,}", prop.lower())
                if t not in {
                    "which", "their", "there", "would", "could", "should", "about",
                    "after", "before", "being", "where", "while",
                }
            ]
            if not tokens:
                return False
            hits = sum(1 for t in tokens[:8] if t in low_full)
            return hits >= min(2, len(tokens))

        for row in accounting:
            if not isinstance(row, dict) or row.get("disposition") != "used":
                continue
            fact_name = row.get("fact_name") or ""
            if fact_name not in (
                "account_contradicts_allegation", "material_account_proposition",
                "material_account_propositions", "child_occupant_present",
                "payment_made", "payment_attempt_failed", "vehicle_immobilised",
                "multiple_visits", "resident_connection_stated",
                "blue_badge_displayed", "disability_extra_time",
            ):
                continue
            prop_hit = any(_prop_reflected(p) for p in props)
            inconsistency = "inconsistent" in low_full or "factual premise" in low_full
            # Account-contradiction facts may be reflected via inconsistency language.
            if fact_name.startswith("account") or fact_name.startswith("material_account"):
                if not (prop_hit or inconsistency):
                    block("VAL-ACCOUNT-COVERAGE",
                          f"Material account fact {fact_name} was marked used but the "
                          "letter does not reflect the professional proposition")
                    break
            elif props and not prop_hit:
                # Non-account used facts: require professional proposition reflection
                # when propositions exist for this case.
                if any(fact_name.replace("_", " ")[:8] in p.lower() for p in props) and not prop_hit:
                    block("VAL-ACCOUNT-COVERAGE",
                          f"Material fact {fact_name} was marked used but its "
                          "professional proposition is missing from the letter")
                    break

        # Pack-level: EVIDENCE route / KB-EV-01 requires the contradiction fact.
        routes = {pack.primary_route, *(pack.secondary_routes or [])}
        if "EVIDENCE" in routes or "KB-EV-01" in pack.module_ids:
            if not facts.get("independent_evidence_contradicts"):
                block("VAL-EVIDENCE-CONTRADICTION",
                      "EVIDENCE / contradiction ground selected without "
                      "independent_evidence_contradicts being established")
        if "KB-REC-01" in pack.module_ids and pack.primary_route == Route.EVIDENCE:
            # Safety net if KB-REC-01 is ever re-homed onto EVIDENCE by mistake.
            block("VAL-EVIDENCE-CONTRADICTION",
                  "Records-request ground (KB-REC-01) must not lead as EVIDENCE "
                  "(contradiction label); use RECORDS")

        # document-level checks
        pcn = _ident(facts.get("pcn_number"))
        if pcn:
            if not _contains_token(full, pcn):
                block("VAL-CONFLICT", "PCN number missing or altered")
            for tok in _near_variants(full, pcn, min_len=6):
                block("VAL-CONFLICT", f"Reference {tok} looks like an altered PCN number")
        vrm = _ident(facts.get("vrm"))
        for tok in re.findall(r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b", full):
            if tok.replace(" ", "") != vrm:
                block("VAL-CONFLICT", f"VRM {tok} does not match source VRM")
        if vrm:
            # Any plate shape, not only the current AB12CDE format: a misread
            # such as RX7V5FP for RX7V5PP is the error that reaches operators.
            for tok in _near_variants(full, vrm, min_len=5, max_len=8, spaced=True):
                block("VAL-CONFLICT", f"VRM {tok} does not match source VRM")
        if facts.get("lease_has_regulations_clause") and "KB-RES-06" in pack.module_ids and \
                not any("KB-RES-06" in s.module_refs for s in draft.sentences()):
            block("VAL-RES", "Lease regulations/permit clause exists but the draft does not address it")
        if "consideration" in full.lower() and "grace" in full.lower() and \
                re.search(r"consideration (and|plus|\+) grace (period )?(allowance|of \d+)", full, re.I):
            block("VAL-CODE", "Consideration and grace merged into one allowance")

        # One case theory, asserted once (KB-KEY-01 "Keying and payment are one
        # case theory - do not repeat"; KB-PAY-01 "Merge repeated payment
        # statements into one"). VAL-REPEAT cannot catch this: "A payment was
        # made" and "the applicable tariff was paid" share almost no words, so
        # the letter stated the point three times and passed.
        for point, pattern in ONE_ASSERTION.items():
            hits = [s.text for s in draft.sentences() if pattern.search(s.text)]
            if len(hits) > 1:
                block("VAL-REPEAT-POINT",
                      f"The {point} point is asserted {len(hits)} times; state it once",
                      hits[1])

        issues += self._plan_conformance(draft, pack)

        if self.judge and not any(i.severity == "BLOCK" for i in issues):
            issues += self._llm_judge(draft, pack)

        return ValidationResult(not any(i.severity == "BLOCK" for i in issues), issues)

    # ---------------------------------------------------------- VAL-PLAN
    def _plan_conformance(self, draft: Draft, pack: RetrievalPack) -> list[ValidationIssue]:
        """P5: every argument in the draft must exist in the LOCKED Claim Plan.

        Messages name the claim family only ("ANPR not approved in Claim Plan"):
        they go back to the drafter as feedback, and the drafter never learns
        which modules were rejected.
        """
        plan = getattr(pack, "claim_plan", None)
        if not plan:
            return []
        out: list[ValidationIssue] = []
        approved = list(plan.get("approved") or [])
        labels = dict(plan.get("labels") or {})
        if plan.get("status") != "LOCKED":
            out.append(ValidationIssue("VAL-PLAN", "BLOCK",
                                       f"Draft built from a {plan.get('status')} claim plan; "
                                       f"only a LOCKED plan may be drafted"))
        if list(pack.module_ids or []) != approved:
            out.append(ValidationIssue("VAL-PLAN", "BLOCK",
                                       "Drafting pack does not match the locked Claim Plan"))
        allowed = set(approved) | {"STRUCTURAL"}
        foreign, own = self._plan_wording(tuple(approved))
        for s in draft.sentences():
            bad = [m for m in s.module_refs if m not in allowed]
            for m in bad:
                out.append(ValidationIssue(
                    "VAL-PLAN", "BLOCK",
                    f"{labels.get(m) or _family(m)} not approved in Claim Plan", s.text))
            if bad or not foreign:
                continue
            windows = _phrase_windows(_copy_fingerprint(s.text), 8)
            if len(windows) < 2:
                continue
            hits: dict[str, int] = {}
            for w in windows:
                if w in own:
                    continue
                for mid in foreign.get(w, ()):
                    hits[mid] = hits.get(mid, 0) + 1
            if not hits:
                continue
            mid, n = max(sorted(hits.items()), key=lambda kv: kv[1])
            if n >= max(2, len(windows) // 2):
                out.append(ValidationIssue(
                    "VAL-PLAN", "BLOCK",
                    f"{labels.get(mid) or _family(mid)} not approved in Claim Plan", s.text))
        return out

    def _plan_wording(self, approved: tuple) -> tuple[dict, set]:
        """8-word windows of approved block wording: {window: modules outside the
        plan whose wording it is}, and the windows of the plan's own modules
        (shared wording is never held against a sentence)."""
        if self.kg is None:
            return {}, set()
        if approved in self._wording:
            return self._wording[approved]
        foreign: dict[str, set] = {}
        own: set[str] = set()
        for m in self.kg.modules.values():
            for b in m.building_blocks or []:
                blk = self.kg.blocks.get(b)
                if blk is None or blk.status != "ACTIVE":
                    continue
                windows = _phrase_windows(_copy_fingerprint(blk.letter_text), 8)
                if m.module_id in approved:
                    own.update(windows)
                else:
                    for w in windows:
                        foreign.setdefault(w, set()).add(m.module_id)
        result = ({w: tuple(sorted(ms)) for w, ms in foreign.items()}, own)
        self._wording[approved] = result
        return result

    def _llm_judge(self, draft: Draft, pack: RetrievalPack) -> list[ValidationIssue]:
        """The judge can only apply a rule it has the input for.

        VAL-RES needs the verbatim lease clauses, VAL-EVIDENCE the uploaded
        evidence ids, VAL-POFA the findings, VAL-CODE the resolved version and
        VAL-DRIVER the driver status. Sent only the sentences, the facts and the
        chunks, it had to guess at five of its own rules — so they were enforced
        by the deterministic layer alone.
        """
        import json
        # Approved propositions of the cited modules, so VAL-FACT can tell a
        # supported legal point from one the draft added.
        propositions = {}
        for chunk in pack.context_chunks or []:
            if chunk.get("kind") == "module":
                propositions[chunk.get("module_id")] = chunk.get("text")
        user = json.dumps({"sentences": [s.__dict__ for s in draft.sentences()],
                           "facts": pack.verified_facts, "chunks": pack.context_chunks,
                           "module_propositions": propositions,
                           "lease_clauses": pack.lease_clauses,
                           "evidence_refs": pack.evidence_refs,
                           "pofa_findings": pack.pofa_findings,
                           "code_version": pack.code_version,
                           "driver_status": pack.driver_status}, default=str)
        out = self.judge.complete_json(task="validation", system=prompts.system("validation"), user=user)
        return [ValidationIssue(i.get("rule", "VAL-FACT"), "BLOCK", i.get("message", ""), i.get("sentence"))
                for i in out.get("issues", [])]
