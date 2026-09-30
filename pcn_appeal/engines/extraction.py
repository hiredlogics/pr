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
VRM_FIELDS = {"vrm", "vrm_entered"}              # both normalised the same way (EX-08)
# Normalised for the same reason dates are: notices print "19/09/2026 12:23" in a
# field labelled as a time, and the raw value ends up quoted in the letter.
TIME_FIELDS = {"entry_time", "exit_time", "observation_time", "event_time"}
BOOL_FIELDS = {
    "notice_sides_complete", "ntk_invites_name_driver", "ntk_invites_pass_to_driver",
    "ntk_defect_statutory_invitation", "ntk_defect_document_confirmed",
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
                held_ata.status = FactStatus.UNCERTAIN
                flags.append("uncertain:operator_ata")

        # EX-16: never silently reconcile conflicting PCN numbers across documents.
        extracted_pcn = _normalise_pcn(case.get("pcn_number"))
        scanned: set[str] = set()
        for ev in case.evidence.values():
            scanned |= _pcn_candidates_from_text(ev.text or "")
        if extracted_pcn:
            scanned.add(extracted_pcn)
        if len(scanned) > 1:
            flags.append("conflict:pcn_number")
            case.put(Fact("F-pcn_conflict", "pcn_conflict", True, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "pcn_cross_check"),
                          confidence=1.0))
            if "pcn_number" in case.facts:
                case.facts["pcn_number"].status = FactStatus.UNCERTAIN
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

        # EX-03 chronology
        ev_d, is_d = case.get("parking_event_date"), case.get("notice_issue_date")
        if ev_d and is_d and is_d < ev_d:
            case.facts["notice_issue_date"].status = FactStatus.UNCERTAIN
            flags.append("chronology:issue_before_event")

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
        for ev in case.evidence.values():
            if case.document_classes.get(ev.evidence_id) in scope.STOP_ORDER:
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
        'who was driving'."""
        for name, value in corrections.items():
            if name in DATE_FIELDS:
                value = parse_uk_date(value)
            if name == "operator_ata":
                value = normalise_operator_ata(value) or value
            case.put(Fact(f"F-{name}", name, value, FactStatus.CORRECTED,
                          FactSource(SourceKind.ANSWER, f"confirm:{name}")))
        for name in confirmed:
            if name in case.facts and case.facts[name].status in (FactStatus.EXTRACTED, FactStatus.UNCERTAIN):
                case.facts[name].status = FactStatus.CONFIRMED
        # Explicit confirm/correct of the PCN clears a cross-document conflict gate.
        if "pcn_number" in corrections or "pcn_number" in confirmed:
            case.put(Fact("F-pcn_conflict", "pcn_conflict", False, FactStatus.DERIVED,
                          FactSource(SourceKind.ANSWER, "confirm:pcn_number")))
        case.state = CaseState.CONFIRMED
