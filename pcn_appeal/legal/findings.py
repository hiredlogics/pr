"""Verified legal findings - the Legal Claim Evidence Gate (P6.1).

A specific legal defect may appear in a letter only when a deterministic
calculation proved it. The flow is:

    document facts -> legal calculation engine -> LEGAL FINDING
        -> Claim Plan (a defect ground needs a VERIFIED finding)
        -> drafting (the drafter sees VERIFIED findings only)
        -> validation (VAL-LEGAL-FINDING: a defect sentence needs its finding)

A finding is one row per (case, finding_type):

    status           VERIFIED | NOT_SUPPORTED | UNRESOLVED
    supporting_facts the Fact Graph facts the calculation read
    calculation      the deterministic result (dates, deadline, days late)
    legal_module_id  the knowledge module the finding licenses, if any

The LLM never writes a finding. `evaluate` wraps the deterministic
calculators that already exist (legal/pofa.py and the notice-content scans);
adding a new defect family (signage, payment, authority) means registering a
new FindingSpec with its own calculator - never a prompt change and never an
operator-specific rule.

UNRESOLVED is not a defect: a missing date or a boundary case on a presumed
posting date yields no claim and no letter wording, only (possibly) a
question. NOT_SUPPORTED is a verified negative: the calculation ran and the
defect is not there.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .pofa import PofaResult

VERIFIED = "VERIFIED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNRESOLVED = "UNRESOLVED"
STATUSES = (VERIFIED, NOT_SUPPORTED, UNRESOLVED)

_NS = uuid.UUID("c0a1e984-5a2e-4d5e-9d1f-7b42f6f3a9d2")

_R = lambda p: re.compile(p, re.I | re.S)  # noqa: E731


@dataclass(frozen=True)
class FindingSpec:
    """One defect family: what proves it and what asserting it looks like."""
    finding_type: str
    description: str
    # Letter sentences that allege this defect (used by VAL-LEGAL-FINDING).
    assertion: re.Pattern
    # Fact Graph facts the calculation reads.
    facts: tuple[str, ...]
    # True when the date calculation itself is the proof: a letter arguing the
    # defect must then set out the calculated dates and the day count
    # (VAL-PARTICULARS), never a generic "outside the statutory period".
    timed: bool = False


# Sentence-level assertion patterns. Lookaheads, so word order does not matter
# inside one sentence. Generic: no operator, no PCN, no module names.
REGISTRY: dict[str, FindingSpec] = {spec.finding_type: spec for spec in (
    FindingSpec(
        "POFA_POSTAL_LATE",
        "late delivery of a postal Notice to Keeper (Schedule 4 para 9)",
        _R(r"^(?=.*\b(notice|ntk)\b)"
           r"(?=.*\b(deliver\w*|given|served|sent|posted|received|issued)\b)"
           r"(?=.*\b(not\s+(?:\w+\s+){0,3}within|outside|after|beyond|late\w*|exceed\w*|"
           r"fail\w*\s+to\s+meet)\b)"
           r"(?=.*\b(statutory|applicable|required|relevant|prescribed|14[-\s]?days?|"
           r"time\s*limit|period|deadline)\b)"),
        ("parking_event_date", "notice_issue_date", "notice_route"),
        timed=True),
    FindingSpec(
        "POFA_NTD_NTK_LATE",
        "Notice to Keeper after the Schedule 4 para 8 window following a Notice to Driver",
        _R(r"^(?=.*\b(notice|ntk)\b)(?=.*\b(notice to driver|windscreen|ntd)\b)"
           r"(?=.*\b(after|late\w*|outside|beyond|exceed\w*)\b)"
           r"(?=.*\b(56|window|permitted|allowed|prescribed)\b)"),
        ("ntd_date", "notice_issue_date", "notice_route"),
        timed=True),
    FindingSpec(
        "POFA_NTD_NTK_TOO_EARLY",
        "Notice to Keeper before the Schedule 4 para 8 window opened",
        _R(r"^(?=.*\b(notice|ntk)\b)"
           r"(?=.*\b(too early|prematurely|before|earlier than)\b)"
           r"(?=.*\b(28|window|permitted|allowed|prescribed)\b)"),
        ("ntd_date", "notice_issue_date", "notice_route"),
        timed=True),
    FindingSpec(
        "POFA_NTK_INVITATION_DEFECT",
        "missing mandatory Schedule 4 invitation wording in the Notice to Keeper",
        # "I invite the operator to cancel" must NOT match — only Schedule 4
        # keeper/driver invitation wording defects.
        _R(r"^(?=.*\b(notice|ntk)\b)"
           r"(?=.*\b(omit\w*|lack\w*|miss\w*|fail\w*|does not|without|absent|no)\b)"
           r"(?=.*\b(invitation|"
           r"invites?\s+(?:the\s+)?(?:keeper|recipient|addressee|driver)|"
           r"pass\W+(?:\w+\W+){0,4}driver|"
           r"mandatory\s+(wording|information|statement|invitation)|"
           r"prescribed\s+(wording|information|statement))\b)"),
        ("ntk_defect_statutory_invitation", "pofa_9_2_e_status", "notice_sides_complete")),
    FindingSpec(
        "POFA_NOT_RELEVANT_LAND",
        "the location is land under statutory control, not relevant land (Schedule 4 para 3)",
        _R(r"^(?=.*\b(relevant\s+land|statutory\s+control|byelaws?)\b)"
           r"(?=.*\b(not|no|outside|excluded|does not apply|do not apply)\b)"),
        ("relevant_land", "statutory_control_site")),
    FindingSpec(
        "NTK_CONTENT_DEFECT",
        "missing mandatory content in the Notice to Keeper (Schedule 4)",
        _R(r"^(?=.*\b(notice|ntk)\b)"
           r"(?=.*\b(omit\w*|lack\w*|miss\w*|fail\w*\s+to\s+(state|specify|identify|contain|"
           r"include)|does not\s+(state|specify|identify|contain|include)|without)\b)"
           r"(?=.*\b(keeper[\s-]+(liability[\s-]+)?warning|creditor|period of parking|"
           r"amount of the\s+(parking\s+)?charge|mandatory|prescribed|"
           r"required\s+(information|content|particulars))\b)"),
        ("ntk_defect_document_confirmed", "notice_sides_complete")),
)}

# A defect suggested rather than proved ("the notice appears non-compliant").
# Never allowed unless some defect is verified; the registry pattern that
# matches then still has to be verified itself.
VAGUE_DEFECT = _R(
    r"\b(notice|ntk|charge|pcn)\b[^.]{0,80}\b(appear\w*|seem\w*|may be|might be|likely|"
    r"arguably|possibly|presumably|probably)\b[^.]{0,60}\b(non-?compliant|invalid|defective|"
    r"deficient|unenforceable|unlawful|flawed|out of time|not\s+(?:\w+\s+){0,2}compliant)")

# Putting the operator to proof asserts no defect of the letter's own.
PUT_TO_PROOF = _R(
    r"\b(operator|creditor)\b[^.]{0,80}\b(is\s+)?(requested|required|invited|put to proof|"
    r"asked|must|should)\b[^.]{0,80}\b(produce|provide|demonstrate|establish|prove|show|"
    r"evidence|confirm)\b"
    r"|\brequested to (produce|provide|demonstrate|establish|prove|show|confirm)\b")

# The specific ntk_defect_* facts that make NTK_CONTENT_DEFECT verified. From
# the document pipeline only (document-confirmed), never from the narrative.
NTK_CONTENT_FACTS = ("ntk_defect_parking_details", "ntk_defect_keeper_warning",
                     "ntk_defect_creditor", "ntk_defect_charge_amount")


def finding_id(case_id: str, finding_type: str) -> str:
    return str(uuid.uuid5(_NS, f"{case_id}|legal-finding|{finding_type}"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- scan
def asserted_types(sentence: str) -> set[str]:
    """Which registered defects this sentence alleges. Empty when it alleges
    none (most sentences)."""
    text = (sentence or "").strip()
    if not text:
        return set()
    return {t for t, spec in REGISTRY.items() if spec.assertion.search(text)}


def describe(types) -> str:
    return "; ".join(REGISTRY[t].description for t in sorted(types) if t in REGISTRY) \
        or "a legal defect"


def verified_types(records, pofa_findings=()) -> set[str]:
    """The defect types the case has proved. `pofa_findings` (the pack's
    verified defect codes) is accepted for packs built before P6.1 and for
    tests that construct a RetrievalPack directly."""
    out = {str(r.get("finding_type")) for r in (records or [])
           if r.get("status") == VERIFIED}
    out |= {str(c) for c in (pofa_findings or [])}
    return out


# ------------------------------------------------------- module requirements
def referenced_findings(module) -> set[str]:
    """Finding codes the module's own gate names (use_when conditions on the
    `pofa_finding` fact). Generic: read from the gate, never hard-coded."""
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for op, arg in node.items():
                if op in ("eq", "in") and isinstance(arg, (list, tuple)) and arg \
                        and arg[0] == "pofa_finding":
                    rest = arg[1] if len(arg) > 1 else None
                    if isinstance(rest, (list, tuple)):
                        out.update(str(v) for v in rest)
                    elif rest is not None:
                        out.add(str(rest))
                else:
                    walk(arg)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(getattr(module, "use_when", None))
    return out


def gate_depends_on_finding(module, facts: dict) -> Optional[str]:
    """The finding code without which this module's gate would not pass on
    these facts - or None when the gate stands on its own (or does not pass).

    This is the hard belt: a ground whose gate passed *because of* a finding
    is only argued when that finding is VERIFIED. A module that also passes
    through a non-finding branch (for example a document-confirmed content
    defect) is untouched.

    P8.1: dependency is resolved against the full pofa_findings set (and any
    codes the module's gate names), never only against a collapsed scalar.
    """
    from ..rules.dsl import evaluate, _pofa_codes
    codes = _pofa_codes(facts) or []
    refs = referenced_findings(module)
    candidates = list(dict.fromkeys([*codes, *sorted(refs)]))
    if not candidates:
        return None
    try:
        if not evaluate(module.use_when, facts):
            return None
        # Gate stands without any finding codes.
        cleared = {**facts, "pofa_finding": None, "pofa_findings": []}
        if evaluate(module.use_when, cleared):
            return None
    except Exception:
        return None
    # Prefer a code the module names that is present; else first calculated code.
    for code in candidates:
        if refs and code not in refs:
            continue
        return str(code)
    return str(candidates[0]) if candidates else None


def gate_facts(facts: dict, finding_codes=()) -> dict:
    """Facts view for gate / eligibility with multi-finding authority (P8.1)."""
    out = dict(facts or {})
    codes = [str(c) for c in (finding_codes or []) if c]
    if not codes:
        raw = out.get("pofa_findings")
        if isinstance(raw, (list, tuple)):
            codes = [str(c) for c in raw if c]
        elif out.get("pofa_finding"):
            codes = [str(out["pofa_finding"])]
    out["pofa_findings"] = codes
    # Keep a scalar for legacy readers; do not invent one when empty.
    if codes and not out.get("pofa_finding"):
        out["pofa_finding"] = codes[0]
    return out


REJECTION_REASON = "Legal defect not verified"


def rejection(module, facts: dict, verified: set[str]) -> Optional[str]:
    """Why the Legal Claim Evidence Gate refuses this module, or None.

    Two layers, both generic:
    * the gate passed only because of `pofa_finding` -> that exact code must
      be a VERIFIED finding;
    * the module's gate names finding codes and none of them is VERIFIED ->
      even if the scalar fact slipped through, the claim is refused.
    """
    dep = gate_depends_on_finding(module, facts)
    if dep is not None and dep not in verified:
        return f"{REJECTION_REASON}: {dep} is not a verified legal finding"
    refs = referenced_findings(module)
    if dep is not None and refs and not (refs & verified):
        return f"{REJECTION_REASON}: requires one of {sorted(refs)}"
    return None


# ---------------------------------------------------------------- evaluate
def evaluate(case, res: PofaResult, modules=()) -> list[dict]:
    """Run the Legal Calculation Engine over the registered finding types and
    record the results on the case (one record per type, updated in place,
    marked dirty for the store). Returns the records.

    `res` is the merged PoFA assessment the reasoning engine already computed
    for this run; `modules` (optional) are the active KB modules, used only to
    note which module each finding licenses.
    """
    codes = set(res.findings or [])
    route = res.route

    def fact_entry(name: str):
        f = case.facts.get(name)
        return {"fact": name, "fact_id": getattr(f, "fact_id", None)} if f is not None else None

    def facts_of(spec: FindingSpec) -> list[dict]:
        return [e for e in (fact_entry(n) for n in spec.facts) if e]

    base_calc: dict[str, Any] = {"pofa_route": route}
    for key in ("parking_event_date", "notice_issue_date", "ntd_date"):
        v = case.get(key)
        if key == "notice_issue_date":
            # P8: for a reminder / driver letter, the date the calculation used
            # is the original notice's, never the later letter's.
            from ..engines.derivation import LATER_STAGES, timing_issue_date
            v = timing_issue_date(case)
            if case.get("notice_stage") in LATER_STAGES:
                f = case.facts.get("original_notice_issue_date")
                base_calc["notice_issue_date_source"] = (
                    "customer" if f is not None and f.source.kind.value in (
                        "ANSWER", "CUSTOMER_FREE_TEXT") else "documents")
        if v is not None:
            base_calc[key] = str(v)
    if res.deadline:
        base_calc["deadline"] = res.deadline.isoformat()
    if res.presumed_delivery:
        base_calc["presumed_delivery"] = res.presumed_delivery.isoformat()
    if res.deadline and res.presumed_delivery:
        base_calc["days_between"] = (res.presumed_delivery - res.deadline).days

    def decide(ftype: str) -> tuple[str, dict]:
        calc = dict(base_calc)
        timing = ftype in ("POFA_POSTAL_LATE", "POFA_NTD_NTK_LATE", "POFA_NTD_NTK_TOO_EARLY")
        if ftype in codes:
            return VERIFIED, calc
        if timing:
            if route == "UNRESOLVED":
                calc["note"] = "; ".join(res.notes[-2:]) or "calculation unresolved"
                return UNRESOLVED, calc
            if route == "NOT_APPLICABLE":
                calc["note"] = "Schedule 4 keeper route not in play"
                return NOT_SUPPORTED, calc
            if ftype == "POFA_POSTAL_LATE" and route != "POSTAL":
                calc["note"] = "not a postal Notice to Keeper"
                return NOT_SUPPORTED, calc
            if ftype != "POFA_POSTAL_LATE" and route != "WINDSCREEN":
                calc["note"] = "no Notice to Driver route"
                return NOT_SUPPORTED, calc
            calc["note"] = "timing compliant on the established dates"
            return NOT_SUPPORTED, calc
        if ftype == "POFA_NTK_INVITATION_DEFECT":
            if case.get("notice_sides_complete") is False or \
                    case.get("pofa_9_2_e_status") in (None, "UNKNOWN"):
                calc["note"] = "notice content not yet fully reviewed"
                return UNRESOLVED, calc
            calc["note"] = "mandatory invitation wording present"
            return NOT_SUPPORTED, calc
        if ftype == "NTK_CONTENT_DEFECT":
            confirmed = bool(case.get("ntk_defect_document_confirmed"))
            specific = [n for n in NTK_CONTENT_FACTS if case.get(n)]
            if confirmed and specific:
                calc["defects"] = specific
                return VERIFIED, calc
            if case.get("notice_sides_complete") is False:
                calc["note"] = "both notice sides required before a content defect"
                return UNRESOLVED, calc
            calc["note"] = "no document-confirmed content defect"
            return NOT_SUPPORTED, calc
        if ftype == "POFA_NOT_RELEVANT_LAND":
            # Only reached when the calculator did not emit the code: the
            # location is not a confirmed statutory-control site.
            calc["note"] = "location not confirmed as land under statutory control"
            return NOT_SUPPORTED, calc
        return UNRESOLVED, calc                               # pragma: no cover

    licenses: dict[str, str] = {}
    for m in modules or ():
        for code in referenced_findings(m):
            licenses.setdefault(code, m.module_id)

    existing = {r.get("finding_type"): r for r in (case.legal_findings or [])}
    records: list[dict] = []
    for ftype, spec in REGISTRY.items():
        status, calc = decide(ftype)
        rec = existing.get(ftype)
        if rec is None:
            rec = {"finding_id": finding_id(case.case_id, ftype), "case_id": case.case_id,
                   "finding_type": ftype, "created_at": _now()}
            case.legal_findings.append(rec)
        changed = (rec.get("status") != status or rec.get("calculation_result") != calc)
        rec.update(status=status, supporting_facts=facts_of(spec), calculation_result=calc,
                   legal_module_id=licenses.get(ftype), run_id=case.run_id)
        if changed:
            rec["_dirty"] = True
            rec.pop("_persisted", None)
        records.append(rec)
    return records


def for_pack(case) -> list[dict]:
    """The VERIFIED findings only, in the shape the pack / drafter receives.
    UNRESOLVED and NOT_SUPPORTED findings never reach the drafting model."""
    out = []
    for r in case.legal_findings or []:
        if r.get("status") != VERIFIED:
            continue
        out.append({"finding_id": r.get("finding_id"),
                    "finding_type": r.get("finding_type"),
                    "status": VERIFIED,
                    "description": REGISTRY[r["finding_type"]].description
                    if r.get("finding_type") in REGISTRY else "",
                    "supporting_facts": [e.get("fact") for e in r.get("supporting_facts") or []],
                    "legal_module_id": r.get("legal_module_id"),
                    "calculation": dict(r.get("calculation_result") or {})})
    return out


# ------------------------------------------------------------- particulars
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
SMALL = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
         8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
         14: "fourteen"}


def date_stated(iso: str, text: str) -> bool:
    """Whether the letter states this ISO date in any customary rendering:
    '3 August 2026', '3rd August 2026', '03/08/2026', 'August 3, 2026', ISO."""
    try:
        y, m, d = (int(x) for x in str(iso)[:10].split("-"))
    except ValueError:
        return False
    month = MONTHS[m - 1]
    pats = (rf"\b0?{d}(?:st|nd|rd|th)?\s+(?:of\s+)?{month}\s+{y}\b",
            rf"\b{month}\s+0?{d}(?:st|nd|rd|th)?,?\s+{y}\b",
            rf"\b0?{d}[/.]0?{m}[/.]{y}\b",
            rf"\b{y}-{m:02d}-{d:02d}\b")
    return any(re.search(p, text, re.I) for p in pats)


def days_stated(n: int, text: str) -> bool:
    """Whether the letter states this day count ('3 days', 'three days')."""
    n = abs(int(n))
    words = SMALL.get(n)
    pat = rf"\b(?:{n}{f'|{words}' if words else ''})\s+(?:calendar\s+|working\s+)?days?\b"
    return bool(re.search(pat, text, re.I))


def particulars(finding: dict) -> dict:
    """The calculated values a letter stating this defect must set out: the
    dates in the calculation, and the day count when one exists. Generic: it
    reads the calculation, not a defect-specific template. Empty for a defect
    the calculation does not prove by dates (content defects)."""
    spec = REGISTRY.get(str(finding.get("finding_type")))
    if spec is None or not spec.timed:
        return {}
    calc = dict(finding.get("calculation") or finding.get("calculation_result") or {})
    dates = {k: v for k, v in calc.items()
             if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)}
    out: dict[str, Any] = {"dates": dates}
    if isinstance(calc.get("days_between"), int) and calc["days_between"] > 0:
        out["days"] = calc["days_between"]
    return out


__all__ = ["VERIFIED", "NOT_SUPPORTED", "UNRESOLVED", "STATUSES", "REGISTRY", "FindingSpec",
           "VAGUE_DEFECT", "PUT_TO_PROOF", "asserted_types", "describe", "verified_types",
           "referenced_findings", "gate_depends_on_finding", "rejection", "REJECTION_REASON",
           "evaluate", "for_pack", "finding_id"]


def render_date(iso: str) -> str:
    """'2026-06-15' -> '15 June 2026' (the rendering `date_stated` accepts)."""
    y, m, d = (int(x) for x in str(iso)[:10].split("-"))
    return f"{d} {MONTHS[m - 1]} {y}"


def particularised_sentence(finding: dict) -> str:
    """One sentence arguing a timed defect on its own calculation, stating
    every particular VAL-PARTICULARS requires: the dates the calculation read,
    the deadline, the deemed delivery date, the day count, and the
    transfer-failure conclusion. Used by the demo drafters; a real model gets
    the same calculation and the same rule in the drafting prompt."""
    p = particulars(finding)
    dates = dict(p.get("dates") or {})
    if not dates:
        return ""
    lead = []
    if dates.get("parking_event_date"):
        lead.append(f"the parking event took place on {render_date(dates['parking_event_date'])}")
    if dates.get("ntd_date"):
        lead.append(f"a Notice to Driver was given on {render_date(dates['ntd_date'])}")
    if dates.get("notice_issue_date"):
        lead.append(f"the Notice to Keeper was issued on {render_date(dates['notice_issue_date'])}")
    tail = []
    if dates.get("deadline"):
        tail.append("the statutory period for delivery of the notice ended on "
                    f"{render_date(dates['deadline'])}")
    if dates.get("presumed_delivery"):
        tail.append(f"it is deemed delivered on {render_date(dates['presumed_delivery'])}")
    days = p.get("days")
    daytxt = f", {days} day{'s' if days != 1 else ''} outside that period" if days else ""
    source = (finding.get("calculation") or finding.get("calculation_result") or {}).get(
        "notice_issue_date_source")
    opener = ("On the dates available" if source == "customer"
              else "On the operator's own documents")
    return (f"{opener} {', '.join(lead)}; "
            f"{' and '.join(tail)}{daytxt}, so the notice was not delivered within the "
            "statutory period and keeper liability does not transfer.")
