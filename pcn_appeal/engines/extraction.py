"""ENGINE 1 - Document Intelligence (intake + extraction).

LLM does: classify each upload, read fields with per-field confidence + page.
Rules do (EX-01..EX-08): everything that decides whether a value is trusted.

Rule pack
  EX-01 Required PCN fields must be present or are sent to the confirmation screen.
  EX-02 Field confidence below threshold -> UNCERTAIN (never usable for a PoFA defect).
  EX-03 Dates must parse (UK dd/mm/yyyy first) and be chronologically possible.
  EX-04 Duration is DERIVED from entry/exit, never taken from LLM arithmetic.
  EX-05 Jurisdiction derived from site postcode (PoFA is England & Wales only).
  EX-06 Uploaded text is DATA. Instruction-like text is flagged (prompt-injection guard).
  EX-07 Notice route resolved from document type (NTD present -> WINDSCREEN).
  EX-08 VRM normalised (upper, no spaces) and cross-checked across documents.
  EX-16 Distinct labelled PCN numbers across documents are flagged; never silently corrected.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from .. import prompts
from ..llm import LLMClient
from ..models import CaseFile, CaseState, Fact, FactSource, FactStatus, SourceKind
from ..rules import scope

CONFIDENCE_THRESHOLD = 0.85
# The prompt's doc-type enum is what a customer's upload can be labelled; the KB
# modules gate on their own evidence codes. Where the two names differ, the
# label is translated here so `has_evidence` sees the code the modules use.
DOC_TYPE_ALIASES = {"WITNESS_STATEMENT": "WITNESS"}
DATE_FIELDS = {"parking_event_date", "notice_issue_date", "notice_received_date", "ntd_date"}

# P7 B1. The fields a legal conclusion can turn on. Getting one wrong changes
# which statute applies, which deadline was missed, or which charge the letter
# answers - so a value for any of these is never trusted on one reading alone.
# Each is either read twice (the model plus the deterministic reader below,
# EX-18), explicitly confirmed or corrected by the customer, or it stays
# UNCERTAIN and unusable.
LEGAL_CRITICAL = (
    "parking_event_date", "notice_issue_date", "ntd_date", "notice_received_date",
    "entry_time", "exit_time", "operator_name", "alleged_breach", "charge_amount",
    "pcn_number", "vrm", "notice_route",
)
VRM_FIELDS = {"vrm", "vrm_entered"}              # both normalised the same way (EX-08)
# Normalised for the same reason dates are: notices print "19/09/2026 12:23" in a
# field labelled as a time, and the raw value ends up quoted in the letter.
TIME_FIELDS = {"entry_time", "exit_time", "observation_time", "event_time"}
BOOL_FIELDS = {
    "notice_sides_complete", "ntk_invites_name_driver", "ntk_invites_pass_to_driver",
    "ntk_defect_statutory_invitation", "ntk_defect_document_confirmed",
    "ntk_keeper_liability_warning",
}

# Canonical ATA codes used by Code version resolution and choice questions.
ATA_CODES = ("BPA", "IPC", "NOT_SHOWN")

# Well-known operators → accredited trade association. Used only when the notice
# does not print an ATA; never overrides a value read from the document.
KNOWN_OPERATOR_ATA: dict[str, str] = {
    "euro car parks": "BPA",
    "euro car park": "BPA",
    "parkingeye": "BPA",
    "parking eye": "BPA",
    "ncp": "BPA",
    "national car parks": "BPA",
    "apcoa": "BPA",
    "cp plus": "BPA",
    "civil enforcement": "IPC",
    "ukpc": "IPC",
    "uk parking control": "IPC",
    "parking control management": "IPC",
    "pcm": "IPC",
    "vehicle control services": "IPC",
    "vcs": "IPC",
}


def known_operator_ata(operator_name: Any) -> Optional[str]:
    """Return BPA/IPC when the operator is a known trade-body member."""
    if not operator_name:
        return None
    low = re.sub(r"\s+", " ", str(operator_name).strip().lower())
    if low in KNOWN_OPERATOR_ATA:
        return KNOWN_OPERATOR_ATA[low]
    for key, code in KNOWN_OPERATOR_ATA.items():
        if key in low or low in key:
            return code
    return None


def normalise_operator_ata(value: Any) -> Optional[str]:
    """Map OCR / full trade-body names to BPA | IPC | NOT_SHOWN.

    Extraction and customers often supply "International Parking Community (IPC)"
    or "British Parking Association"; Code resolution and choice answers only
    accept the short codes.
    """
    if value in (None, ""):
        return None
    raw = str(value).strip()
    upper = raw.upper()
    if upper in ATA_CODES:
        return upper
    if upper in ("UNKNOWN", "N/A", "NA", "NONE", "NO"):
        return "NOT_SHOWN"
    compact = re.sub(r"[^A-Z0-9]+", " ", upper)
    # Prefer explicit acronym tokens over loose substrings.
    if re.search(r"\bIPC\b", compact) or "INTERNATIONAL PARKING" in compact:
        return "IPC"
    if re.search(r"\bBPA\b", compact) or "BRITISH PARKING" in compact:
        return "BPA"
    if any(tok in compact for tok in ("NOT SHOWN", "NO LOGO", "NO ATA", "NO TRADE")):
        return "NOT_SHOWN"
    return None


# EX-10. An operator that says in its own notice that it took the money is
# better evidence of a completed transaction than a customer's recollection
# that the machine failed. Deterministic on purpose: a regex over the notice,
# not a judgement call handed to a model.
#
# _GAP stays inside one sentence, so a payment mentioned in one sentence and a
# refusal in the next are not joined up. A full stop between digits is a decimal
# point, not a sentence end - without that exception "Payment of GBP 4.50 was
# recorded" fails to match, which is the exact wording these notices use.
_GAP = r"(?:[^.\n]|\.(?=\d))"
_TAKEN = r"(recorded|received|processed|successful|completed|taken)"
PAYMENT_RECORDED = re.compile(
    rf"\bpayments?\b{_GAP}{{0,80}}\b{_TAKEN}\b"
    rf"|\b{_TAKEN}\b{_GAP}{{0,40}}\bpayments?\b",
    re.I)

# EX-13. Debt-recovery / closed-appeal stage. The pattern itself now lives in
# rules/scope.py, which owns the routing decision and runs it as the backup
# check behind the classifier's label. Re-exported under its old name so the
# one detector has one definition.
DEBT_RECOVERY = scope.DEBT_SIGNALS

# EX-14. Hire / lease-firm keeper. Triggers the hire-documentation ground; the
# model still decides whether that ground is worth arguing, but the fact that
# the notice addresses a hire firm is read off the document.
HIRE_KEEPER = re.compile(
    r"\b(hire\s+(?:company|firm|vehicle)|vehicle\s+hire|lease\s+(?:company|firm)|"
    r"rental\s+(?:company|firm|vehicle)|contract\s+hire|"
    r"registered\s+keeper.{0,40}\bhire\b|\bhire\b.{0,40}registered\s+keeper)\b",
    re.I)

# EX-12. Bays reserved for a class of user (parent and child, family, disabled,
# EV, permit holder) are enforced on who was using them, not on how long. That
# makes the operator's observation window the whole basis of the allegation,
# which is what KB-BAY-01 addresses - so the allegation type has to be a fact.
RESTRICTED_BAY = re.compile(
    r"\b(parent\s*(and|&|/)\s*child|family|child[- ]friendly|disabled|blue\s*badge|accessible"
    r"|electric\s*vehicle|ev\b|permit\s*holder|staff|loading)\b[^.\n]{0,40}\b(bay|space|spaces)\b"
    r"|\b(bay|space)\b[^.\n]{0,40}\breserved\b",
    re.I)

# EX-20. Allegations whose wording involves a site validation / permit /
# payment mechanism. This is a CANDIDATE signal only: it says the mechanism may
# be in issue, never that it is. KB-REC-01's records request needs a case fact
# establishing materiality as well (see `_validation_mechanism_material`).
VALIDATION_SHAPED = re.compile(
    r"\b(validat(e|ed|ion)|voucher|kiosk|ticket\s*machine|pay[\s-]*(and|&)?[\s-]*display|"
    r"permit|season\s*ticket|tariff|scratch\s*card|pay[\s-]*by[\s-]*phone)\b",
    re.I)

SCOTLAND = {"AB", "DD", "DG", "EH", "FK", "G", "HS", "IV", "KA", "KW", "KY", "ML", "PA", "PH", "ZE"}
MIXED_BORDER = {"TD", "CA", "NP", "SY", "CH", "LD", "LL"}   # needs full-postcode lookup
INJECTION = re.compile(r"(ignore (all|previous|the above)|system prompt|you are (now )?an? (ai|assistant)|"
                       r"disregard .{0,20}instructions|<\s*/?\s*(system|instruction))", re.I)

# EX-16. Distinct PCN reference tokens labelled as such in document text.
# Never silently overwrite one value with another — conflicts are flagged.
# "Parking Charge Notice" alone must not capture the next word (e.g. Operator).
# Require an explicit Number/No/Ref/# label, or bare "PCN" followed by a
# digit-bearing token.
PCN_LABELLED = re.compile(
    r"(?:"
    r"(?:Parking\s+Charge\s+Notice|Charge\s+Notice)\s+"
    r"(?:No\.?|Number|Ref(?:erence)?\.?|#)\s*[:.]?\s*"
    r"|"
    r"PCN\s*(?:No\.?|Number|Ref(?:erence)?\.?|#)?\s*[:.]?\s*"
    r")"
    r"([A-Z0-9][-A-Z0-9]{5,14})",
    re.I,
)


def _normalise_pcn(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _pcn_candidates_from_text(text: str) -> set[str]:
    found: set[str] = set()
    for m in PCN_LABELLED.finditer(text or ""):
        tok = _normalise_pcn(m.group(1))
        # Real PCN refs always contain digits; reject prose false positives.
        if 6 <= len(tok) <= 14 and any(c.isdigit() for c in tok):
            found.add(tok)
    return found


def parse_uk_date(v: Any) -> Optional[date]:
    if isinstance(v, date):
        return v
    if not v:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


# EX-18 (P7 B1). Labelled field patterns: the deterministic second reader for
# legal-critical fields. Shared with FactRecoveryEngine, which uses them to
# fill gaps; here they run on every extraction as a CROSS-CHECK of what the
# model read, never as a fallback. A photographed notice has no text layer, so
# the reader returning nothing proves nothing - it can only agree or disagree.
FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "operator_name": re.compile(
        r"(?:Parking\s+Operator|Operator(?:\s+Name)?|Issued\s+by)\s*[:\-]\s*"
        r"([A-Za-z0-9][A-Za-z0-9&.'\- ]{2,60})",
        re.I,
    ),
    "parking_location": re.compile(
        r"(?:Location|Site(?:\s+Name)?|Car\s+Park|Parking\s+at)\s*[:\-]\s*"
        r"([^\n]{3,80})",
        re.I,
    ),
    "alleged_breach": re.compile(
        # "Date of Contravention: 12/06/2026" is a date line, not the
        # allegation - the lookbehind keeps it out of the second reading.
        r"(?:(?<!of\s)Contravention|Alleged\s+(?:contravention|breach)|"
        r"Reason(?:\s+for\s+charge)?|Breach)\s*[:\-]\s*([^\n]{5,160})",
        re.I,
    ),
    "charge_amount": re.compile(
        r"(?:Parking\s+Charge|Charge\s+Amount|Amount\s+Due|Total)\s*[:\-]?\s*"
        r"(£\s?\d+(?:\.\d{2})?)",
        re.I,
    ),
    "site_postcode": re.compile(
        r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b",
        re.I,
    ),
    "parking_event_date": re.compile(
        r"(?:Date\s+of\s+(?:Parking|Event|Contravention)|Parking\s+(?:Date|Period)|"
        r"Event\s+Date)\s*[:\-]\s*"
        r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+[A-Za-z]+\s+\d{2,4})",
        re.I,
    ),
    "notice_issue_date": re.compile(
        r"(?:Date\s+of\s+(?:Issue|Notice)|Issue\s+Date|Notice\s+Date|"
        r"Date\s+Issued)\s*[:\-]\s*"
        r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+[A-Za-z]+\s+\d{2,4})",
        re.I,
    ),
    "ntd_date": re.compile(
        r"(?:Date\s+of\s+)?Notice\s+to\s+Driver\s*(?:date|issued|given)?\s*[:\-]\s*"
        r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+[A-Za-z]+\s+\d{2,4})",
        re.I,
    ),
    "entry_time": re.compile(
        r"(?:Entry|Arrival|In)\s*(?:Time)?\s*[:\-]\s*(\d{1,2}[:.]\d{2})",
        re.I,
    ),
    "exit_time": re.compile(
        r"(?:Exit|Departure|Out)\s*(?:Time)?\s*[:\-]\s*(\d{1,2}[:.]\d{2})",
        re.I,
    ),
}


def normalise_field_value(name: str, raw: str) -> Any:
    """One normaliser for both readers, so the comparison compares values,
    not formatting."""
    if name in DATE_FIELDS:
        return parse_uk_date(raw)
    if name in TIME_FIELDS:
        return _hhmm(raw)
    if name in VRM_FIELDS:
        return re.sub(r"\s+", "", str(raw)).upper()
    if name == "charge_amount":
        return re.sub(r"\s+", "", raw)
    if name == "site_postcode":
        return re.sub(r"\s+", " ", raw.upper()).strip()
    if name == "alleged_breach":
        return re.sub(r"\s+", " ", raw).strip()[:160]
    if name == "parking_location":
        return re.sub(r"\s+", " ", raw).strip()[:80]
    if name == "operator_name":
        cleaned = re.split(r"\s{2,}|\n|Limited\.?$|Ltd\.?$", raw, maxsplit=1)[0].strip()
        if cleaned.lower() in {"operator", "name", "parking", "notice"}:
            return None
        return cleaned[:60]
    return raw


def _canon(name: str, value: Any) -> Any:
    """A comparable form of one reading. Dates stay dates; money keeps only its
    digits ("£100" and "100" are the same amount); free text is lower-cased
    with whitespace collapsed."""
    if value is None:
        return None
    if name in DATE_FIELDS:
        return parse_uk_date(value)
    if name in TIME_FIELDS:
        return _hhmm(value)
    if name == "charge_amount":
        digits = re.sub(r"[^\d.]", "", str(value))
        return digits.rstrip("0").rstrip(".") if "." in digits else digits
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _readings_agree(name: str, llm: Any, doc: Any) -> bool:
    a, b = _canon(name, llm), _canon(name, doc)
    if a is None or b is None:
        return False
    if a == b:
        return True
    # Names and prose labels: one reading containing the other is the same
    # reading ("Acme Parking" on the label line, "Acme Parking Ltd" extracted).
    if name in ("operator_name", "alleged_breach", "parking_location"):
        return a in b or b in a
    return False


def cross_check_legal_critical(case: CaseFile) -> list[str]:
    """EX-18 (P7 B1). Read every legal-critical field a second time with the
    deterministic patterns and compare against the model's value.

      agree     -> the field is recorded as cross-checked (findings may rely
                   on it without customer confirmation);
      disagree  -> the field becomes UNCERTAIN and is flagged for the
                   confirmation screen - never confidently continued with;
      ambiguous -> two labelled values in the documents: same treatment;
      no reading-> nothing proven either way (photos have no text layer).
    """
    flags: list[str] = []
    agreed: list[str] = []
    for name in LEGAL_CRITICAL:
        pattern = FIELD_PATTERNS.get(name)
        f = case.facts.get(name)
        if pattern is None or f is None or f.source.kind != SourceKind.DOCUMENT \
                or f.status != FactStatus.EXTRACTED:
            continue
        readings: set[Any] = set()
        for ev in case.evidence.values():
            for m in pattern.finditer(ev.text or ""):
                v = normalise_field_value(name, m.group(1).strip())
                if v is not None:
                    readings.add(v)
        if not readings:
            continue
        canon = {_canon(name, r) for r in readings}
        some_agree = any(_readings_agree(name, f.value, r) for r in readings)
        if some_agree and len(canon) == 1:
            agreed.append(name)
            continue
        # Prose labels: a label line matching the model's value corroborates
        # it; a second, differently-worded label elsewhere (a covering letter's
        # own summary) does not put the allegation in doubt. Dates, times and
        # amounts are different: two distinct labelled values is a real
        # conflict, whichever one the model happened to pick.
        if some_agree and name in ("operator_name", "alleged_breach", "parking_location"):
            agreed.append(name)
            continue
        if some_agree:
            reason, detail = "cross_check_ambiguous", "documents carry more than one value"
        else:
            reason, detail = "cross_check_mismatch", "deterministic reading disagrees"
        case.set_status(name, FactStatus.UNCERTAIN, reason=reason)
        flags.append(f"conflict:{name}")
        case.audit.append({"event": reason, "fact": name, "note": detail,
                           "model_value": str(f.value),
                           "document_values": sorted(str(r) for r in readings)})
    if agreed:
        case.put(Fact("F-cross_checked_fields", "cross_checked_fields", sorted(agreed),
                      FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "legal_critical_cross_check")))
    return flags


def cross_checked(case: CaseFile) -> set[str]:
    """The legal-critical fields whose two readings agreed (empty when the
    cross-check never ran or nothing agreed)."""
    return set(case.get("cross_checked_fields") or [])


def validate_chronology(case: CaseFile, *, stage: str) -> list[str]:
    """EX-03 (P7 B1): what the dates and times say must be possible.

    Impossible orderings never continue confidently: each offending fact is
    made UNCERTAIN (whatever its status - a corrected value is re-validated
    like any other) and flagged for the customer. Where the rule cannot tell
    which of a pair is wrong, both are.

      * no legal-critical date may be in the future;
      * parking event <= Notice to Driver <= Notice to Keeper issue;
      * notice issue <= notice received;
      * entry time before exit time (same-day reading).
    """
    flags: list[str] = []

    def demote(name: str, flag: str) -> None:
        if name in case.facts and case.facts[name].status != FactStatus.UNCERTAIN:
            case.set_status(name, FactStatus.UNCERTAIN, reason=f"chronology:{flag}")
        if f"chronology:{flag}" not in flags:
            flags.append(f"chronology:{flag}")

    def value(name: str) -> Optional[date]:
        f = case.facts.get(name)
        v = parse_uk_date(f.value) if f is not None else None
        return v

    today = date.today()
    held = {n: value(n) for n in
            ("parking_event_date", "ntd_date", "notice_issue_date", "notice_received_date")}
    for name, v in held.items():
        if v is not None and v > today:
            demote(name, f"future:{name}")
    ev, ntd, iss, rec = (held[n] for n in
                         ("parking_event_date", "ntd_date", "notice_issue_date",
                          "notice_received_date"))
    if ev and iss and iss < ev:
        demote("notice_issue_date", "issue_before_event")
    if ev and ntd and ntd < ev:
        demote("ntd_date", "ntd_before_event")
        demote("parking_event_date", "ntd_before_event")
    if ntd and iss and iss < ntd:
        demote("ntd_date", "ntk_before_ntd")
        demote("notice_issue_date", "ntk_before_ntd")
    if iss and rec and rec < iss:
        demote("notice_received_date", "received_before_issue")
    a, b = _hhmm(case.get("entry_time")), _hhmm(case.get("exit_time"))
    if a and b and b < a:
        demote("entry_time", "times_inverted")
        demote("exit_time", "times_inverted")
    if flags:
        case.audit.append({"event": "chronology_flags", "stage": stage, "flags": flags})
    return flags


def jurisdiction_from_postcode(pc: Optional[str]) -> str:
    if not pc:
        return "UNKNOWN"
    area = re.match(r"[A-Z]{1,2}", pc.upper().strip())
    if not area:
        return "UNKNOWN"
    a = area.group(0)
    if a == "BT":
        return "NORTHERN_IRELAND"
    if a in MIXED_BORDER:
        return "UNKNOWN"
    if a in SCOTLAND:
        return "SCOTLAND"
    return "ENGLAND_WALES"


def derive_jurisdiction(case: CaseFile) -> str:
    """EX-05. Also called again before the PoFA assessment: `site_postcode` can
    arrive from an answer long after extraction, and Schedule 4 does not apply
    outside England & Wales, so a postcode supplied late has to be able to turn
    UNKNOWN into a real answer instead of losing the PoFA route for good.

    A real value the customer corrected or confirmed outranks the postcode lookup
    and is left alone - `put` overwrites without checking. "UNKNOWN" is excluded
    from that: the confirmation screen promotes it like any other extracted fact,
    so treating it as authoritative would make an auto-confirmed placeholder
    permanent and a postcode answered afterwards would never take effect.
    """
    held = case.facts.get("jurisdiction")
    if held is not None and held.value not in (None, "", "UNKNOWN") \
            and held.status in (FactStatus.CORRECTED, FactStatus.CONFIRMED, FactStatus.ANSWERED):
        return held.value
    j = jurisdiction_from_postcode(case.get("site_postcode"))
    # Keeper letterhead postcodes must not decide site jurisdiction. If the
    # postcode lookup is UNKNOWN, a clear England/Wales site wording on the
    # notice is enough to open Schedule 4 for further checks (timing/content).
    if j == "UNKNOWN":
        loc = " ".join(str(case.get(n) or "") for n in (
            "parking_location", "relevant_land_hint", "alleged_breach"))
        if re.search(
            r"\b(London|Wembley|Manchester|Birmingham|Leeds|Liverpool|Bristol|"
            r"Sheffield|England|Wales|EW|United\s+Kingdom|UK)\b",
            loc, re.I,
        ) and not re.search(r"\b(Scotland|Northern\s+Ireland|\bNI\b)\b", loc, re.I):
            j = "ENGLAND_WALES"
    case.put(Fact("F-jurisdiction", "jurisdiction", j,
                  FactStatus.DERIVED if j != "UNKNOWN" else FactStatus.UNCERTAIN,
                  FactSource(SourceKind.CALCULATION, "postcode_jurisdiction")))
    return j


def _fields_of(out: dict) -> dict[str, Any]:
    """Field entries from either response shape.

    The prompt asks for them nested under "fields", but a model that returns
    them at the top level should not silently produce an empty case - losing
    every value is worse than accepting a slightly different envelope. A field
    entry is recognised by being a mapping with a "value" key.
    """
    nested = out.get("fields")
    if isinstance(nested, dict) and nested:
        return nested
    return {name: entry for name, entry in out.items()
            if name != "doc_types" and isinstance(entry, dict) and "value" in entry}


def _hhmm(v: Any) -> Optional[str]:
    """The HH:MM in a time field. Notices routinely print a date and a time in
    one line ("19/09/2026 12:23"), so a bare strptime drops the value entirely."""
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", str(v or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _minutes(t1: Any, t2: Any, signed: bool = True) -> Optional[int]:
    a, b = _hhmm(t1), _hhmm(t2)
    if a is None or b is None:
        return None
    m = int((datetime.strptime(b, "%H:%M") - datetime.strptime(a, "%H:%M")).total_seconds() // 60)
    if signed:
        return m if m >= 0 else None
    return abs(m)


def _validation_mechanism_material(case: CaseFile) -> tuple[bool, str]:
    """EX-20. Whether a validation / permit / payment mechanism is actually in
    issue in this case, and the case fact that establishes it.

    KB-REC-01 asks the operator to go and review its validation, kiosk, permit
    and transaction records. That request only belongs in an appeal when
    something in the case puts the mechanism in issue. The allegation's wording
    is not that something: "permit" or "pay and display" appears in enormous
    numbers of notices as a description of the site, and a records request sent
    on that basis is generic rather than material.

    Each basis below is a fact established from the case itself - a document
    read, a calculation, or an answer the customer gave - not a word in the
    allegation. The first match wins and is recorded as the basis, so the
    reason the request entered the appeal is always auditable.
    """
    # The customer engaged the mechanism and whether it worked is unresolved:
    # the operator's own records are the only thing that can settle it.
    if case.get("parking_validation_status") == "UNKNOWN":
        return True, "parking_validation_status_unknown"
    if case.get("shopping_purchase_confirmed") and not case.get("parking_validation_status"):
        return True, "purchase_confirmed_validation_unresolved"
    # A payment or permit the customer asserts, which the notice does not
    # reflect: the discrepancy is in the operator's transaction records.
    if case.get("payment_made") is True and not case.get("payment_recorded_in_document"):
        return True, "payment_asserted_not_recorded"
    if case.get("payment_attempt_failed") is True:
        return True, "payment_attempt_failed"
    if case.get("permit_held") is True:
        return True, "permit_asserted"
    if case.get("resident_connection_stated") is True and case.get("permit_held") is not False:
        return True, "resident_permit_in_issue"
    # Evidence the customer supplied that only the operator's records can
    # reconcile with the allegation.
    kinds = {e.kind for e in case.evidence.values() if e.uploaded}
    if kinds & {"RECEIPT", "APP_SCREENSHOT", "PERMIT"}:
        return True, "customer_supplied_transaction_evidence"
    return False, ""


def _keying_error(vrm: Optional[str], entered: Optional[str]) -> Optional[str]:
    """EX-09. MINOR when the keyed registration is one character out of the
    vehicle's; DIFFERENT_VEHICLE when it is a different plate altogether.

    The distinction is legally load-bearing - the Code treats a minor keying
    error and a different vehicle differently, and KB-KEY-02 is explicitly
    barred from claiming the minor-error outcome - so it is computed here
    rather than left to a model's idea of "close enough".
    """
    if not vrm or not entered:
        return None
    # Normalised here, not just by the caller: EX-08 only reaches extracted
    # fields, so an answered "AB12 CDF" would otherwise be one character longer
    # than "AB12CDE" and a one-letter typo would classify as a different vehicle.
    a, b = (re.sub(r"\s+", "", str(v)).upper() for v in (vrm, entered))
    if a == b:
        return "NONE"
    if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
        return "MINOR"                              # one substituted character
    if abs(len(a) - len(b)) == 1:                   # one inserted or dropped character
        longer, shorter = (a, b) if len(a) > len(b) else (b, a)
        for i in range(len(longer)):
            if longer[:i] + longer[i + 1:] == shorter:
                return "MINOR"
    return "DIFFERENT_VEHICLE"


class ExtractionEngine:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def run(self, case: CaseFile) -> list[str]:
        """Returns list of fields that need customer attention on the confirmation screen."""
        flags: list[str] = []
        # EX-06 prompt-injection guard on raw text before it reaches any model
        for ev in case.evidence.values():
            if INJECTION.search(ev.text or ""):
                flags.append(f"injection_suspected:{ev.evidence_id}")
                case.audit.append({"event": "injection_flag", "evidence": ev.evidence_id})

        # Photographed notices and scanned PDFs have no text, so their pixels go to
        # the vision model. The clients append images in list order with no labels,
        # so the manifest below is what ties image N back to a document id.
        images: list[bytes] = []
        manifest: list[str] = []
        for e in case.evidence.values():
            for page, img in enumerate(e.images or [], start=1):
                images.append(img)
                manifest.append(f"[image {len(images)}] document id='{e.evidence_id}' "
                                f"filename='{e.filename}' page={page}")

        docs = "\n\n".join(f"<document id='{e.evidence_id}' filename='{e.filename}'>\n{e.text}\n</document>"
                           for e in case.evidence.values())
        if manifest:
            docs += ("\n\n<attached_images>\nThese images are pages of the documents above, in order.\n"
                     + "\n".join(manifest) + "\n</attached_images>")
        out = self.llm.complete_json(task="extraction", system=prompts.system("extraction"),
                                     user=docs, images=images or None)

        # Engine 0: the classification the routing gate decides on. Kept on the
        # case as well as on the evidence because downstream code reassigns
        # `kind`, and an absent label has to stay distinguishable from OTHER.
        for ev_id, kind in (out.get("doc_types") or {}).items():
            if ev_id in case.evidence:
                kind = DOC_TYPE_ALIASES.get(kind, kind)
                case.evidence[ev_id].kind = kind
                case.document_classes[ev_id] = kind

        for name, f in _fields_of(out).items():
            if f is None or f.get("value") in (None, ""):
                continue
            val, conf = f["value"], float(f.get("confidence", 0))
            if name in DATE_FIELDS:
                val = parse_uk_date(val)
                if val is None:
                    conf = 0.0                          # EX-03
            if name in VRM_FIELDS:
                val = re.sub(r"\s+", "", str(val)).upper()  # EX-08
            if name in TIME_FIELDS:
                val = _hhmm(val)
                if val is None:
                    conf = 0.0
            if name in BOOL_FIELDS:
                if isinstance(val, str):
                    low = val.strip().lower()
                    if low in ("1", "true", "yes", "on"):
                        val = True
                    elif low in ("0", "false", "no", "off"):
                        val = False
                    else:
                        conf = 0.0
                else:
                    val = bool(val)
            if name == "operator_ata":
                normalised = normalise_operator_ata(val)
                if normalised:
                    val = normalised
                else:
                    # Unrecognised trade-body string cannot drive Code resolution.
                    conf = 0.0
            status = FactStatus.EXTRACTED if conf >= CONFIDENCE_THRESHOLD else FactStatus.UNCERTAIN  # EX-02
            src = FactSource(SourceKind.DOCUMENT, f"{f.get('evidence_id')}#p{f.get('page', 1)}")
            case.put(Fact(f"F-{name}", name, val, status, src, conf))
            if status == FactStatus.UNCERTAIN:
                flags.append(f"uncertain:{name}")

        # Re-normalise ATA if a prior put left a long-form / alias value.
        held_ata = case.facts.get("operator_ata")
        if held_ata and held_ata.value not in ATA_CODES:
            mapped = normalise_operator_ata(held_ata.value)
            if mapped:
                case.put(Fact("F-operator_ata", "operator_ata", mapped, held_ata.status,
                              FactSource(SourceKind.CALCULATION, "ata_normalise"),
                              confidence=held_ata.confidence))
                flags = [f for f in flags if f != "uncertain:operator_ata"]
            elif held_ata.usable:
                case.set_status("operator_ata", FactStatus.UNCERTAIN, reason="ata_unrecognised")
                flags.append("uncertain:operator_ata")

        # EX-16: never silently reconcile conflicting PCN numbers across documents.
        extracted_pcn = _normalise_pcn(case.get("pcn_number"))
        scanned: set[str] = set()
        for ev in case.evidence.values():
            scanned |= _pcn_candidates_from_text(ev.text or "")
        if extracted_pcn:
            scanned.add(extracted_pcn)
            # The classifier reads each notice's printed references
            # independently. A photo has no text layer, so without this a
            # misread digit ("...45642" for "...45842") had nothing to disagree
            # with and went into the letter. Two readings of the same page that
            # differ make the number UNCERTAIN: the confirmation screen marks it
            # for the customer to check against the notice. It is not the
            # cross-document conflict below - both readings may be wrong, so a
            # closed choice between them would not help.
            readings = {_normalise_pcn(((c or {}).get("references") or {}).get("pcn_number"))
                        for c in (case.classifications or {}).values()
                        if (c or {}).get("document_type") == "PRIVATE_PARKING_NOTICE"}
            readings = {r for r in readings if len(r) >= 6}
            if readings and extracted_pcn not in readings and "pcn_number" in case.facts:
                case.set_status("pcn_number", FactStatus.UNCERTAIN, reason="pcn_read_disagreement")
                flags.append("uncertain:pcn_number")
                case.audit.append({"event": "pcn_read_disagreement",
                                   "readings": sorted(readings | {extracted_pcn})})
        if len(scanned) > 1:
            flags.append("conflict:pcn_number")
            case.put(Fact("F-pcn_conflict", "pcn_conflict", True, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "pcn_cross_check"),
                          confidence=1.0))
            if "pcn_number" in case.facts:
                case.set_status("pcn_number", FactStatus.UNCERTAIN, reason="pcn_conflict")
            # Kept as a fact, not only in the audit, so the pipeline can put the
            # candidates to the customer as a closed choice. Without them the
            # conflict was unresolvable in the one-click flow: the number is
            # UNCERTAIN so auto-confirm skips it (EX-02) and nothing asked.
            case.put(Fact("F-pcn_candidates", "pcn_candidates", sorted(scanned),
                          FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "pcn_cross_check")))
            case.audit.append({"event": "pcn_conflict",
                               "candidates": sorted(scanned)})

        # EX-01 deliberately no longer flags a field merely for being absent. A
        # fixed required-field list is what produced "field missing -> ask the
        # customer": it fired on fields no ground needed and on fields the
        # documents still held. Absence is now handled where it can be judged -
        # FactRecoveryEngine exhausts the documents and the calculators, and case
        # analysis decides whether what is left is material enough to ask about.

        # EX-18 (P7 B1): second reading of every legal-critical field, then the
        # chronology / plausibility suite. Both run on every extraction.
        flags += cross_check_legal_critical(case)
        flags += validate_chronology(case, stage="extraction")

        # EX-04 derived duration
        mins = _minutes(case.get("entry_time"), case.get("exit_time"))
        if mins is not None:
            case.put(Fact("F-total_recorded_duration_min", "total_recorded_duration_min", mins,
                          FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "duration_calc")))

        # EX-09 keying error derived from the two registrations, never from the
        # model's own comparison. A notice alleging a keying error normally
        # prints what was keyed in, so the ground should not depend on the
        # customer retyping it. An explicit answer later overwrites this.
        kind = _keying_error(case.get("vrm"), case.get("vrm_entered"))
        if kind:
            case.put(Fact("F-keying_error_type", "keying_error_type", kind, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "vrm_compare")))

        # EX-11 how wide the operator's own observation window was. Unsigned: the
        # two times are a pair of readings, and which is printed first carries no
        # meaning. Zero is a real finding - a single instant - so it is stored,
        # and every gate on it uses `exists` plus a comparison, never `is`.
        window = _minutes(case.get("observation_time"), case.get("event_time"), signed=False)
        if window is not None:
            case.put(Fact("F-observation_window_min", "observation_window_min", window,
                          FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "observation_window")))

        # EX-12 what class of allegation this is (see KB-BAY-01)
        if RESTRICTED_BAY.search(str(case.get("alleged_breach") or "")):
            case.put(Fact("F-restricted_bay_alleged", "restricted_bay_alleged", True,
                          FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "breach_classify")))

            # EX-19 whether the operator's recorded observation can establish
            # THIS contravention, which is what decides if a put-to-proof
            # request is warranted (KB-BAY-01).
            #
            # There is no threshold here, and deliberately so: no minute figure
            # carries independent legal significance, and an earlier five-minute
            # cut-off implied one that no approved source supports. What matters
            # is the nature of the allegation against the nature of the record.
            #
            # An eligibility bay is enforced on WHO OR WHAT was using it - a
            # parent with a child, a badge holder, a vehicle on charge, a permit
            # holder. That is a status, and a status is not shown by noting when
            # a vehicle was seen: a timestamp records presence, not entitlement.
            # So where the allegation turns on eligibility and the operator's
            # case rests on its own recorded observation, the operator is put to
            # proof of the eligibility element with the photographs and records
            # it relies on. A wide window does not cure this; a narrow one is
            # not what creates it.
            #
            # The request is withheld when the operator has evidenced the
            # eligibility element some other way, because then there is nothing
            # to put to proof - that is the "context of the alleged
            # contravention" the gate is assessed in.
            has_observation = (case.get("observation_time") is not None
                               or case.get("observation_window_min") is not None
                               or case.get("event_time") is not None)
            eligibility_evidenced = bool(
                case.get("bay_eligibility_evidenced")
                or case.get("bay_conditions_met_accounted"))
            if has_observation and not eligibility_evidenced:
                case.put(Fact(
                    "F-bay_eligibility_unevidenced", "bay_eligibility_unevidenced", True,
                    FactStatus.DERIVED,
                    FactSource(SourceKind.CALCULATION, "bay_eligibility_assessment")))

        # EX-20 whether a validation / permit / payment mechanism is genuinely
        # material to THIS case, which is what KB-REC-01's records request
        # needs. The allegation's wording alone is only a candidate signal: the
        # word "permit" appearing in a notice does not make the operator's
        # permit records material, and a module must activate because the case
        # facts make it relevant, not because a keyword matched.
        if VALIDATION_SHAPED.search(str(case.get("alleged_breach") or "")):
            case.put(Fact(
                "F-validation_mechanism_alleged", "validation_mechanism_alleged", True,
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "breach_classify")))
            material, why = _validation_mechanism_material(case)
            if material:
                case.put(Fact(
                    "F-validation_mechanism_material", "validation_mechanism_material", True,
                    FactStatus.DERIVED,
                    FactSource(SourceKind.CALCULATION, f"validation_materiality:{why}")))
                case.audit.append({
                    "event": "validation_materiality",
                    "material": True, "basis": why,
                })
            else:
                case.audit.append({
                    "event": "validation_materiality",
                    "material": False,
                    "basis": "allegation mentions a validation/permit mechanism but no case "
                             "fact establishes that it is in issue; records request withheld",
                })

        # EX-10 the notice's own record of a completed payment (see VAL-CONFLICT)
        if any(PAYMENT_RECORDED.search(e.text or "") for e in case.evidence.values()):
            case.put(Fact("F-payment_recorded_in_document", "payment_recorded_in_document", True,
                          FactStatus.DERIVED, FactSource(SourceKind.DOCUMENT, "payment_recorded")))

        # EX-13 debt-recovery / closed appeal window (see CaseState.NO_APPEAL_RIGHT)
        #
        # Two independent signals, because either alone has a blind spot. The
        # regex reads the operator's own wording, but only ever sees `e.text` -
        # and a photographed or scanned letter has no text at all, its pixels
        # having gone to the vision model, so a DCBL demand uploaded as a photo
        # was invisible to it. The classification covers exactly that case.
        # The regex result is written back as a classification so the routing
        # gate has one input to read rather than two.
        #
        # Classification-supported: the wording only relabels a document the
        # model could not place. A notice the model read as a notice is left
        # alone - private notices routinely warn that unpaid charges "may be
        # passed to debt recovery", and that warning is not a debt demand.
        for ev in case.evidence.values():
            label = case.document_classes.get(ev.evidence_id)
            if label in scope.STOP_ORDER or label in scope.NOTICE_KINDS:
                continue
            if scope.DEBT_SIGNALS.search(ev.text or ""):
                ev.kind = "DEBT_RECOVERY"
                case.document_classes[ev.evidence_id] = "DEBT_RECOVERY"

        doc_kinds = set(case.document_classes.values())
        if "DEBT_RECOVERY" in doc_kinds:
            case.put(Fact("F-debt_recovery_stage", "debt_recovery_stage", True,
                          FactStatus.DERIVED, FactSource(SourceKind.DOCUMENT, "debt_recovery")))

        # EX-17 a statutory council PCN is not this service's jurisdiction at all.
        # Kept as its own fact rather than folded into debt_recovery_stage: the
        # customer needs a different service, so they need a different message.
        if "COUNCIL_PCN" in doc_kinds:
            case.put(Fact("F-council_pcn", "council_pcn", True, FactStatus.DERIVED,
                          FactSource(SourceKind.DOCUMENT, "council_pcn")))

        # EX-14 hire / lease-firm keeper wording on the notice
        if any(HIRE_KEEPER.search(e.text or "") for e in case.evidence.values()):
            case.put(Fact("F-keeper_is_hire_firm", "keeper_is_hire_firm", True,
                          FactStatus.DERIVED, FactSource(SourceKind.DOCUMENT, "hire_keeper")))
            hire_kinds = {"HIRE_AGREEMENT", "LEASE", "TENANCY", "PERMIT"}
            supplied = any(e.kind in hire_kinds and e.uploaded for e in case.evidence.values())
            case.put(Fact("F-hire_docs_supplied", "hire_docs_supplied", supplied,
                          FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "hire_docs")))

        # EX-15 both sides of a paper notice. Content defects (KB-POFA-04) must
        # not be asserted from a single photographed face: Schedule 4 particulars
        # often sit on the reverse. Distinct page images / multipage text only —
        # two copies of the same front are not completeness.
        from ..notice_completeness import apply_notice_sides_fact
        apply_notice_sides_fact(case)

        # EX-05 jurisdiction. Unresolved jurisdiction alone raises nothing: it
        # withholds the Code and PoFA grounds that depend on it, and case analysis
        # asks for the site only where one of those grounds is otherwise in reach.
        derive_jurisdiction(case)

        # EX-07 notice route
        kinds = {e.kind for e in case.evidence.values()}
        route = "WINDSCREEN" if "NTD" in kinds or case.has("ntd_date") else (
            "POSTAL" if "NTK" in kinds or "PCN" in kinds else "UNKNOWN")
        case.put(Fact("F-notice_route", "notice_route", route, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "notice_route_rule")))

        case.state = CaseState.EXTRACTED
        return flags

    @staticmethod
    def confirm(case: CaseFile, corrections: dict[str, Any], confirmed: list[str]) -> None:
        """Confirmation screen result. Corrections override extraction; confirmed
        fields are promoted. Driver-identification status is asked HERE as a
        status question (has it already been given to the operator?), never as
        'who was driving'.

        P7 B1: an UNCERTAIN field is never promoted by its mere presence in
        `confirmed` - the UI sends every displayed field, and a value the
        system itself does not trust needs the customer to actually type it
        (a correction). An unparseable corrected date is an error, not a
        silently-written None. Corrected values are re-validated: a correction
        that creates an impossible chronology goes straight back to UNCERTAIN.
        """
        for name, value in corrections.items():
            if name in DATE_FIELDS:
                parsed = parse_uk_date(value)
                if parsed is None:
                    raise ValueError(f"{name}: {value!r} is not a recognisable date")
                value = parsed
            if name in TIME_FIELDS:
                parsed = _hhmm(value)
                if parsed is None:
                    raise ValueError(f"{name}: {value!r} is not a recognisable time")
                value = parsed
            if name == "operator_ata":
                value = normalise_operator_ata(value) or value
            case.put(Fact(f"F-{name}", name, value, FactStatus.CORRECTED,
                          FactSource(SourceKind.ANSWER, f"confirm:{name}")))
        for name in confirmed:
            if name in corrections:
                continue
            if name in case.facts and case.facts[name].status == FactStatus.EXTRACTED:
                case.set_status(name, FactStatus.CONFIRMED, reason="confirmation_screen")
            elif name in case.facts and case.facts[name].status == FactStatus.UNCERTAIN:
                case.audit.append({"event": "uncertain_not_auto_confirmed", "fact": name})
        # Only an explicit correction (the customer typed the number) clears a
        # cross-document conflict; blanket confirmation of pre-filled fields
        # does not choose between two charge numbers.
        if "pcn_number" in corrections:
            case.put(Fact("F-pcn_conflict", "pcn_conflict", False, FactStatus.DERIVED,
                          FactSource(SourceKind.ANSWER, "confirm:pcn_number")))
        validate_chronology(case, stage="confirm")
        case.state = CaseState.CONFIRMED
