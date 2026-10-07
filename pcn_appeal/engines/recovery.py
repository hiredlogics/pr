"""Deterministic missing-fact recovery — run before asking the customer.

Workflow position
-----------------
  extraction → enrich → **recovery** → UK rule applicability → analysis questions
  → draft → validation

The LLM may propose what is missing; this engine first tries to establish those
facts from documents, approved calculators and KB applicability. It never
promotes an inference to a confirmed defect, never invents dates, and never
claims access to operator systems the application does not have.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import case_state
from ..legal import code_versions, pofa
from ..models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from ..disclosure import keeper_route_blocked
from ..notice_completeness import reverse_page_read
from ..routes import GENERAL_GROUND_ROUTES, Route
from .extraction import (
    DATE_FIELDS, TIME_FIELDS, VRM_FIELDS, _hhmm, _pcn_candidates_from_text,
    derive_jurisdiction, known_operator_ata, normalise_operator_ata, parse_uk_date,
)


def _disclosure_blocks_keeper(case: CaseFile) -> bool:
    """Only an explicit external disclosure blocks Schedule 4 keeper analysis."""
    return keeper_route_blocked(case)

# Material gaps that can change assessment / drafting when still unknown.
MATERIAL_FACTS = (
    "operator_name", "pcn_number", "vrm", "parking_event_date", "notice_issue_date",
    "alleged_breach", "parking_location", "entry_time", "exit_time", "operator_ata",
    "site_postcode", "charge_amount", "notice_route",
)

# Gaps the operator (not the customer) is the proper source for.
OPERATOR_REQUESTABLE = (
    "kiosk_validation_log", "validation_transaction", "landowner_authority",
    "anpr_raw_images", "payment_system_log", "signage_plan",
)

# UK VRM (current format) as printed on notices.
VRM_RE = re.compile(
    r"\b([A-Z]{2}\d{2}\s?[A-Z]{3})\b",
    re.I,
)

# Labelled field patterns — only used when the fact is still missing/uncertain.
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
        r"(?:Contravention|Alleged\s+(?:contravention|breach)|Reason(?:\s+for\s+charge)?|"
        r"Breach)\s*[:\-]\s*([^\n]{5,160})",
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
        r"(?:Date\s+of\s+(?:Parking|Event)|Parking\s+(?:Date|Period)|Event\s+Date)\s*[:\-]\s*"
        r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+[A-Za-z]+\s+\d{2,4})",
        re.I,
    ),
    "notice_issue_date": re.compile(
        r"(?:Date\s+of\s+(?:Issue|Notice)|Issue\s+Date|Notice\s+Date|"
        r"Date\s+Issued)\s*[:\-]\s*"
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

ATA_RE = re.compile(r"\b(BPA|IPC|British Parking Association|International Parking Community)\b", re.I)

# Receipt proves purchase; never parking validation.
RECEIPT_PURCHASE_RE = re.compile(
    r"(?:total|amount|paid|purchase|sainsbury|tesco|asda|morrisons|lidl|aldi|receipt)",
    re.I,
)


@dataclass
class RecoveryReport:
    """What automatic recovery established, calculated, or left unknown."""
    recovered: dict[str, dict] = field(default_factory=dict)
    calculated: dict[str, Any] = field(default_factory=dict)
    verified: list[str] = field(default_factory=list)
    unknown_material: list[dict] = field(default_factory=list)
    unknown_non_essential: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    operator_requestable: list[str] = field(default_factory=list)
    do_not_ask: list[str] = field(default_factory=list)
    inferences: list[dict] = field(default_factory=list)
    sources_checked: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered,
            "calculated": self.calculated,
            "verified": self.verified,
            "unknown_material": self.unknown_material,
            "unknown_non_essential": self.unknown_non_essential,
            "conflicts": self.conflicts,
            "operator_requestable": self.operator_requestable,
            "do_not_ask": self.do_not_ask,
            "inferences": self.inferences,
            "sources_checked": self.sources_checked,
            "trace": self.trace,
        }


class FactRecoveryEngine:
    """Exhaust document + calculator sources before any customer question."""

    def __init__(self, kg: Any = None):
        self.kg = kg

    def recover(self, case: CaseFile) -> RecoveryReport:
        report = RecoveryReport()
        report.sources_checked = [
            f"{e.evidence_id}:{e.kind}:{e.filename}" for e in case.evidence.values()
        ]

        self._recover_from_documents(case, report)
        # P8: turn document facts into KB gate facts before any calculation
        # reads them (relevant_land feeds pofa.assess) and before analysis
        # decides what is still unknown enough to ask.
        from .derivation import derive
        derived = derive(case)
        for name, row in derived.items():
            report.calculated[name] = row
            report.do_not_ask.append(name)
            report.trace.append(f"derived {name}={row['value']} via {row['rule']}")
        self._classify_receipt_vs_validation(case, report)
        self._run_calculations(case, report)
        self._assess_ntk_schedule4_content(case, report)
        self._assess_keeper_warning(case, report)
        self._classify_gaps(case, report)

        case.recovery_report = report.as_dict()
        case.audit.append({"event": "fact_recovery", **{
            k: report.as_dict()[k] for k in (
                "recovered", "calculated", "unknown_material", "conflicts",
                "operator_requestable", "do_not_ask", "inferences",
            )
        }})
        return report

    # ----------------------------------------------------------- document scan
    def _recover_from_documents(self, case: CaseFile, report: RecoveryReport) -> None:
        corpus = "\n".join((e.text or "") for e in case.evidence.values())
        if corpus.strip():
            self._scan_document_corpus(case, report, corpus)
        else:
            report.trace.append("no document text available for recovery")

        # Known operator → ATA even when the notice text omitted the logo.
        if not case.has("operator_ata"):
            mapped = known_operator_ata(case.get("operator_name"))
            if mapped:
                self._adopt(case, report, "operator_ata", mapped, "known_operator_ata")
                report.do_not_ask.append("operator_ata")

        # Jurisdiction from postcode if still unknown.
        if case.get("jurisdiction") in (None, "", "UNKNOWN") and case.has("site_postcode"):
            derive_jurisdiction(case)
            report.trace.append("jurisdiction derived from recovered/known postcode")

    def _scan_document_corpus(self, case: CaseFile, report: RecoveryReport, corpus: str) -> None:
        if not case.has("pcn_number") or case.get("pcn_conflict"):
            candidates = set()
            for ev in case.evidence.values():
                candidates |= _pcn_candidates_from_text(ev.text or "")
            if len(candidates) == 1 and not case.get("pcn_conflict"):
                value = next(iter(candidates))
                self._adopt(case, report, "pcn_number", value, "document_label_scan")
            elif len(candidates) > 1:
                report.conflicts.append("pcn_number")
                report.trace.append(f"pcn conflict retained: {sorted(candidates)}")

        # VRM — only a single unique plate across notice-like text.
        if not case.has("vrm"):
            plates = {re.sub(r"\s+", "", m.group(1)).upper() for m in VRM_RE.finditer(corpus)}
            plates = {p for p in plates if 6 <= len(p) <= 8}
            if len(plates) == 1:
                self._adopt(case, report, "vrm", next(iter(plates)), "document_vrm_scan")
            elif len(plates) > 1:
                report.conflicts.append("vrm")
                report.trace.append(f"vrm conflict: {sorted(plates)}")

        for name, pattern in FIELD_PATTERNS.items():
            if case.has(name):
                continue
            hits = []
            for ev in case.evidence.values():
                for m in pattern.finditer(ev.text or ""):
                    raw = m.group(1).strip()
                    hits.append((raw, ev.evidence_id))
            if not hits:
                continue
            values = {self._normalise_field(name, h[0]) for h in hits}
            values.discard(None)
            if len(values) == 1:
                value = next(iter(values))
                src = hits[0][1]
                self._adopt(case, report, name, value, f"document_pattern:{src}")
            elif len(values) > 1:
                report.conflicts.append(name)
                report.trace.append(f"{name} conflict across docs: {sorted(str(v) for v in values)}")

        # Canonicalise a long-form ATA already on the case (OCR / prior answers).
        held = case.facts.get("operator_ata")
        if held and held.value not in ("BPA", "IPC", "NOT_SHOWN"):
            mapped = normalise_operator_ata(held.value)
            if mapped:
                case.put(Fact(
                    "F-operator_ata", "operator_ata", mapped, held.status,
                    FactSource(SourceKind.CALCULATION, "ata_normalise_existing"),
                    confidence=held.confidence,
                ))
                report.recovered["operator_ata"] = {"value": mapped, "method": "ata_normalise_existing"}
                report.do_not_ask.append("operator_ata")
                report.trace.append(f"normalised operator_ata {held.value!r} -> {mapped}")

        if not case.has("operator_ata"):
            atas = set()
            for m in ATA_RE.finditer(corpus):
                mapped = normalise_operator_ata(m.group(1))
                if mapped in ("BPA", "IPC"):
                    atas.add(mapped)
            if len(atas) == 1:
                self._adopt(case, report, "operator_ata", next(iter(atas)), "document_ata_scan")

    def _normalise_field(self, name: str, raw: str) -> Any:
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
            # Stop at common trailing noise.
            cleaned = re.split(r"\s{2,}|\n|Limited\.?$|Ltd\.?$", raw, maxsplit=1)[0].strip()
            if cleaned.lower() in {"operator", "name", "parking", "notice"}:
                return None
            return cleaned[:60]
        return raw

    def _adopt(self, case: CaseFile, report: RecoveryReport, name: str,
               value: Any, method: str) -> None:
        if value in (None, ""):
            return
        # Never overwrite a usable established fact; never silently "fix" a conflict.
        if case.has(name) and not case.get("pcn_conflict"):
            return
        if name == "pcn_number" and case.get("pcn_conflict"):
            return
        existing = case.facts.get(name)
        if existing and existing.status in (
                FactStatus.CONFIRMED, FactStatus.CORRECTED, FactStatus.ANSWERED):
            return
        case.put(Fact(
            f"F-{name}", name, value, FactStatus.DERIVED,
            FactSource(SourceKind.DOCUMENT, f"recovery:{method}"),
            confidence=0.9,
        ))
        report.recovered[name] = {"value": value, "method": method}
        report.do_not_ask.append(name)
        report.trace.append(f"recovered {name} via {method}")

    # ---------------------------------------------- receipt ≠ validation
    def _classify_receipt_vs_validation(self, case: CaseFile, report: RecoveryReport) -> None:
        receipts = [e for e in case.evidence.values()
                    if e.kind == "RECEIPT" and e.uploaded]
        if not receipts:
            # The uploaded evidence holds no receipt, so no purchase is confirmed by
            # one. That is known from the evidence set, not a guess about the visit;
            # without it the exclusion on a receipt left inconclusive could never
            # be settled for a case that does not rely on a receipt at all.
            held = case.facts.get("shopping_purchase_confirmed")
            if held is None or (held.source.ref == "receipt_scan" and held.value is not False):
                case.put(Fact(
                    "F-shopping_purchase_confirmed", "shopping_purchase_confirmed", False,
                    FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "receipt_scan"),
                ))
            return
        # Document kind RECEIPT evidences a purchase artefact. It never proves
        # that parking validation / kiosk use occurred.
        case.put(Fact(
            "F-shopping_purchase_confirmed", "shopping_purchase_confirmed", True,
            FactStatus.DERIVED, FactSource(SourceKind.DOCUMENT, "receipt_scan"),
        ))
        report.recovered["shopping_purchase_confirmed"] = {
            "value": True, "method": "receipt_present",
        }
        if any(RECEIPT_PURCHASE_RE.search(e.text or "") for e in receipts):
            report.trace.append("receipt text also matches purchase markers")
        # Explicit: validation remains unknown — not a guess either way.
        case.put(Fact(
            "F-parking_validation_status", "parking_validation_status", "UNKNOWN",
            FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "receipt_not_validation"),
        ))
        report.inferences.append({
            "claim": "shopping_purchase_confirmed",
            "status": "DERIVED_FROM_RECEIPT",
            "not_confirmed": "parking_validation",
        })
        report.operator_requestable.extend([
            "kiosk_validation_log", "validation_transaction",
        ])
        report.do_not_ask.extend(["further_evidence", "store_contact", "kiosk_validation_log"])
        report.trace.append("receipt present: purchase noted; validation left UNKNOWN")

    # -------------------------------------------------------- calculators
    def _run_calculations(self, case: CaseFile, report: RecoveryReport) -> None:
        derive_jurisdiction(case)
        version, code_status = code_versions.resolve(
            case.get("parking_event_date"), case.get("operator_ata"),
            case.get("operator_transitioned"),
        )
        report.calculated["code_status"] = code_status
        report.calculated["code_version"] = getattr(version, "version_id", None)

        res = pofa.assess(jurisdiction=derive_jurisdiction(case), **pofa_inputs(case))
        report.calculated["pofa_route"] = res.route
        report.calculated["pofa_findings"] = list(res.findings)
        report.calculated["pofa_notes"] = list(res.notes)
        with case_state.derives(case, "jurisdiction", "relevant_land", "notice_route",
                                "parking_event_date", "notice_issue_date", "ntd_date",
                                "notice_received_date", "delivery_date_proven",
                                rule="pofa.assess"):
            case.put(Fact(
                "F-pofa_route", "pofa_route", res.route, FactStatus.DERIVED,
                FactSource(SourceKind.CALCULATION, "pofa.assess"),
            ))
            case.put(Fact(
                "F-pofa_findings", "pofa_findings",
                list(res.findings or []), FactStatus.DERIVED,
                FactSource(SourceKind.CALCULATION, "pofa.assess"),
            ))
            case.put(Fact(
                "F-pofa_finding", "pofa_finding",
                res.findings[0] if res.findings else None, FactStatus.DERIVED,
                FactSource(SourceKind.CALCULATION, "pofa.assess"),
            ))
        if res.deadline:
            report.calculated["pofa_deadline"] = res.deadline.isoformat()
        if res.presumed_delivery:
            report.calculated["pofa_presumed_delivery"] = res.presumed_delivery.isoformat()

        # Duration already derived in extraction when both times exist.
        if case.has("total_recorded_duration_min"):
            report.calculated["total_recorded_duration_min"] = case.get(
                "total_recorded_duration_min")

        report.trace.append(
            f"calculators: code={code_status} pofa={res.route} findings={res.findings}")

    def _assess_keeper_warning(self, case: CaseFile, report: RecoveryReport) -> None:
        """Para 9(2)(f): a postal Notice to Keeper must warn that the keeper
        becomes liable if the charge is unpaid after 28 days and the driver's
        name and address are not known. A notice without it cannot found keeper
        liability, which is what KB-POFA-04 / KB-POFA-05 (PP-POFA-005B) state.

        Concluded only from the notice's own text (a PDF text layer or OCR) with
        BOTH sides uploaded. The extractor's `ntk_keeper_liability_warning`
        reading of a photo is recorded but never pleaded: live, on a blurred
        reverse the model returned statutory wording that is not printed on it.
        Unknown stays unknown - nothing is pleaded from a missing page."""
        if str(case.get("notice_route") or "") != "POSTAL" or _disclosure_blocks_keeper(case):
            return
        blob = "\n".join((e.text or "") for e in case.evidence.values()
                         if e.kind in ("PCN", "NTK", "NTD"))
        flag = pofa.scan_keeper_warning(blob)
        model_read = case.get("ntk_keeper_liability_warning")
        # Not seen on a lone front is not "absent": the back is optional, so
        # without it the warning stays undetermined rather than recorded False.
        report.calculated["ntk_keeper_liability_warning"] = (
            None if flag is False and not reverse_page_read(case) else flag)
        if flag is None and model_read is not None:
            report.trace.append(f"keeper warning: image read only ({model_read}), not relied on")
            return
        if flag is not False:
            report.trace.append(f"keeper warning: {'present' if flag else 'not determinable'}")
            return
        if not reverse_page_read(case):
            report.trace.append("keeper warning: not seen, but the back was not read")
            return
        for name in ("ntk_defect_keeper_warning", "ntk_defect_document_confirmed"):
            case.put(Fact(f"F-{name}", name, True, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "pofa.keeper_warning")))
        report.trace.append("keeper warning: absent on a two-sided postal notice (para 9(2)(f))")

    def _assess_ntk_schedule4_content(self, case: CaseFile, report: RecoveryReport) -> None:
        """Global Schedule 4 invitation scan on notice text (all postal NTKs).

        Records pofa_9_2_e_status as one of:
          SATISFIED / DEFECT_IDENTIFIED / UNRESOLVED / NOT_APPLICABLE / NOT_RUN

        Does not invent defects from an empty image upload: insufficient text
        yields UNRESOLVED (request reverse / clearer copy) rather than a pleaded
        content ground. First-person narrative never disables this assessment.
        Missing findings are not proof of compliance; missing pages are not a
        confirmed statutory omission.
        """
        route = str(case.get("notice_route") or "")
        if route not in ("POSTAL", "WINDSCREEN"):
            case.put(Fact(
                "F-pofa_9_2_e_status", "pofa_9_2_e_status", "NOT_APPLICABLE",
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.content"),
            ))
            report.calculated["pofa_9_2_e_status"] = "NOT_APPLICABLE"
            return

        if _disclosure_blocks_keeper(case):
            # Keeper content is not the pleaded route when disclosure is confirmed;
            # still record that the scan was not used for keeper liability.
            case.put(Fact(
                "F-pofa_9_2_e_status", "pofa_9_2_e_status", "NOT_APPLICABLE",
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.content"),
            ))
            report.calculated["pofa_9_2_e_status"] = "NOT_APPLICABLE"
            report.trace.append("ntk content: NOT_APPLICABLE (external driver disclosure confirmed)")
            return

        notice_ev = [
            e for e in case.evidence.values()
            if e.kind in ("PCN", "NTK", "NTD") or "pcn" in (e.filename or "").lower()
            or "ntk" in (e.filename or "").lower()
        ]
        # Prefer evidence text; vision may have left OCR chars=0 while still
        # extracting structured fields — that is not unreadability for those fields,
        # but invitation wording still needs text or explicit invitation flags.
        blob = "\n".join((e.text or "") for e in notice_ev).strip()
        name_flag = case.get("ntk_invites_name_driver")
        pass_flag = case.get("ntk_invites_pass_to_driver")
        scan = pofa.scan_ntk_invitations(blob)
        if name_flag is not None or pass_flag is not None:
            has_name = bool(name_flag) if name_flag is not None else scan.has_name_driver_invitation
            has_pass = bool(pass_flag) if pass_flag is not None else scan.has_pass_to_driver_invitation
            defect = has_pass is False
            notes = list(scan.notes) + [
                f"extraction flags: name_driver={name_flag!r} pass_to_driver={pass_flag!r}"
            ]
        else:
            has_name = scan.has_name_driver_invitation
            has_pass = scan.has_pass_to_driver_invitation
            defect = scan.defect_statutory_invitation
            notes = list(scan.notes)

        # The back is optional. Wording not found on a front-only upload is
        # unknown, never a recorded absence: only a positive find is kept.
        if not reverse_page_read(case):
            has_name = True if has_name else None
            has_pass = True if has_pass else None
            defect = False

        report.calculated["ntk_content_notes"] = notes
        report.calculated["ntk_has_name_driver_invitation"] = has_name
        report.calculated["ntk_has_pass_to_driver_invitation"] = has_pass
        for n in notes:
            report.trace.append(f"pofa-content:{n}")

        if has_name is not None:
            case.put(Fact(
                "F-ntk_invites_name_driver", "ntk_invites_name_driver", bool(has_name),
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.scan_ntk_invitations"),
            ))
        if has_pass is not None:
            case.put(Fact(
                "F-ntk_invites_pass_to_driver", "ntk_invites_pass_to_driver", bool(has_pass),
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.scan_ntk_invitations"),
            ))

        # Nothing seen is never compliance: SATISFIED needs the pass-to-driver
        # invitation positively found (by text or an explicit extraction flag).
        # A name-driver flag alone says nothing about the pass-on limb.
        if has_pass is None:
            status = "UNRESOLVED"
            case.put(Fact(
                "F-pofa_9_2_e_status", "pofa_9_2_e_status", status,
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.content"),
            ))
            report.calculated["pofa_9_2_e_status"] = status
            report.unknown_material.append(_reverse_not_supplied(report))
            report.trace.append("ntk content: UNRESOLVED — insufficient text")
            return

        if has_pass is True and not defect:
            status = "SATISFIED"
            case.put(Fact(
                "F-pofa_9_2_e_status", "pofa_9_2_e_status", status,
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.content"),
            ))
            report.calculated["pofa_9_2_e_status"] = status
            report.trace.append("ntk content: SATISFIED — pass-to-driver invitation present")
            return

        # Both sides must have been checked before concluding wording is absent.
        # A single face that lacks pass-on wording is UNRESOLVED, not a pleaded defect.
        if not reverse_page_read(case):
            status = "UNRESOLVED"
            case.put(Fact(
                "F-pofa_9_2_e_status", "pofa_9_2_e_status", status,
                FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.content"),
            ))
            report.calculated["pofa_9_2_e_status"] = status
            report.unknown_material.append(_reverse_not_supplied(report))
            report.trace.append(
                "ntk content: UNRESOLVED — cannot plead invitation defect without both sides"
            )
            return

        status = "DEFECT_IDENTIFIED"
        case.put(Fact(
            "F-pofa_9_2_e_status", "pofa_9_2_e_status", status,
            FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.content"),
        ))
        report.calculated["pofa_9_2_e_status"] = status
        case.put(Fact(
            "F-ntk_defect_statutory_invitation", "ntk_defect_statutory_invitation", True,
            FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.scan_ntk_invitations"),
        ))
        case.put(Fact(
            "F-ntk_defect_document_confirmed", "ntk_defect_document_confirmed", True,
            FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.scan_ntk_invitations"),
        ))
        findings = list(report.calculated.get("pofa_findings") or [])
        if "POFA_NTK_INVITATION_DEFECT" not in findings:
            findings.append("POFA_NTK_INVITATION_DEFECT")
        report.calculated["pofa_findings"] = findings
        if (report.calculated.get("pofa_route") in (None, "UNRESOLVED", "NOT_APPLICABLE")
                and not _disclosure_blocks_keeper(case)
                and case.get("jurisdiction") == "ENGLAND_WALES"
                and case.get("relevant_land") is not False
                and route == "POSTAL"):
            report.calculated["pofa_route"] = "POSTAL"
            case.put(Fact(
                "F-pofa_route", "pofa_route", "POSTAL", FactStatus.DERIVED,
                FactSource(SourceKind.CALCULATION, "pofa.content"),
            ))
        case.put(Fact(
            "F-pofa_finding", "pofa_finding", "POFA_NTK_INVITATION_DEFECT",
            FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "pofa.scan_ntk_invitations"),
        ))
        report.trace.append("ntk content: DEFECT_IDENTIFIED after both sides reviewed")

    # -------------------------------------------------------------- gaps
    def _classify_gaps(self, case: CaseFile, report: RecoveryReport) -> None:
        for name in MATERIAL_FACTS:
            if case.has(name):
                report.verified.append(name)
                report.do_not_ask.append(name)
                continue
            if name in report.conflicts or (
                    name == "pcn_number" and case.get("pcn_conflict")):
                report.conflicts.append(name) if name not in report.conflicts else None
                report.unknown_material.append({
                    "fact": name,
                    "why_material": "conflicting values across documents; must be confirmed",
                    "sources_checked": report.sources_checked,
                    "action": "block_or_confirm",
                })
                continue

            why = self._why_material(name, report, case)
            if why:
                report.unknown_material.append({
                    "fact": name,
                    "why_material": why,
                    "sources_checked": report.sources_checked,
                    "action": "ask_if_customer_knows_else_proceed_cautiously",
                })
            else:
                report.unknown_non_essential.append(name)

        # Validation kiosk: if allegation is validation-shaped and status unknown,
        # request operator records rather than endless customer questions.
        breach = str(case.get("alleged_breach") or "").lower()
        if any(tok in breach for tok in ("validat", "kiosk", "voucher", "ticket")):
            if case.get("parking_validation_status") in (None, "UNKNOWN"):
                report.operator_requestable.append("kiosk_validation_log")
                report.do_not_ask.append("store_contact")
                report.trace.append(
                    "validation-shaped allegation: operator records preferred over customer chase")

        # Deduplicate lists while preserving order.
        report.do_not_ask = list(dict.fromkeys(report.do_not_ask))
        report.operator_requestable = list(dict.fromkeys(report.operator_requestable))
        report.verified = list(dict.fromkeys(report.verified))
        report.conflicts = list(dict.fromkeys(report.conflicts))

    def _why_material(self, name: str, report: RecoveryReport,
                      case: Optional[CaseFile] = None) -> Optional[str]:
        code_status = str(report.calculated.get("code_status") or "")
        pofa_route = str(report.calculated.get("pofa_route") or "")
        if name in ("parking_event_date", "notice_issue_date"):
            if pofa_route == "UNRESOLVED" or "Missing" in str(
                    report.calculated.get("pofa_notes") or ""):
                return "required for Schedule 4 notice-timing calculation"
            if "no_event_date" in code_status:
                return "required to resolve which Code of Practice version applies"
            return "required to anchor the parking event on the notice"
        if name == "operator_ata" and "ata_unknown" in code_status:
            # Only material when a Code-dependent non-LAND ground is already
            # factually arguable — otherwise asking wastes the customer's time.
            if self.kg is not None and case is not None \
                    and not self._ata_unlocks_arguable_ground(case):
                return None
            return "required to resolve the applicable Code of Practice version"
        if name in ("operator_name", "pcn_number", "vrm", "alleged_breach"):
            return "required to identify the charge and address the allegation"
        if name in ("entry_time", "exit_time"):
            return None  # useful for ANPR grounds but not always essential
        if name == "site_postcode":
            # Only when jurisdiction is still unknown AND no fact-specific
            # non-PoFA ground is already open. Pure BAY/REC cases must not
            # interrupt the customer for a postcode that will not change the letter.
            jur = (case.get("jurisdiction") if case is not None else None)
            if jur not in (None, "", "UNKNOWN"):
                return None
            if self.kg is not None and case is not None \
                    and self._fact_specific_ground_open(case):
                return None
            if jur in (None, "", "UNKNOWN"):
                return "helps confirm jurisdiction when otherwise UNKNOWN"
            return None
        return None

    def _ata_unlocks_arguable_ground(self, case: CaseFile) -> bool:
        """True when some SCOP-based ground (other than always-on LANDOWNER) already
        satisfies use_when and would gain a resolved Code version from ATA."""
        from ..rules.dsl import evaluate
        facts = case.fact_view()
        for m in self.kg.active_modules():
            if m.route == Route.LANDOWNER:
                continue
            if not any(str(s).startswith("SCOP-") for s in (m.legal_basis or [])):
                continue
            if evaluate(m.do_not_use_when, facts):
                continue
            if evaluate(m.use_when, facts):
                return True
        return False

    def _fact_specific_ground_open(self, case: CaseFile) -> bool:
        """True when a non-PoFA / non-LANDOWNER module already clears its gates.

        Those letters do not need a site postcode to proceed.
        """
        from ..rules.dsl import evaluate
        facts = case.fact_view()
        for m in self.kg.active_modules():
            if m.route in GENERAL_GROUND_ROUTES:
                continue
            if evaluate(m.do_not_use_when, facts):
                continue
            if evaluate(m.use_when, facts):
                return True
        return False


def pofa_inputs(case: CaseFile) -> dict:
    """Everything the Schedule 4 calculator reads, except the jurisdiction."""
    return dict(
        relevant_land=case.get("relevant_land"),
        notice_route=case.get("notice_route", "UNKNOWN"),
        parking_event_date=case.get("parking_event_date"),
        notice_issue_date=case.get("notice_issue_date"),
        ntd_date=case.get("ntd_date"),
        actual_delivery_date=(
            case.get("notice_received_date") if case.get("delivery_date_proven") else None
        ),
        driver_identified=_disclosure_blocks_keeper(case),
    )


def postcode_unlocks(case: CaseFile, kg) -> list[str]:
    """The leading Schedule 4 grounds that only an unknown jurisdiction is
    withholding: re-runs the calculator as if the site were in England & Wales
    and returns the ACTIVE PoFA modules of leading strength that would then
    apply. Empty when jurisdiction is known, the postcode is held, or knowing it
    would change nothing - the only case in which asking for it is material.
    Never the keeper's address: this only decides whether to ask."""
    from ..rules.dsl import evaluate
    from .reasoning import SUPPORTING_THRESHOLD
    if case.has("site_postcode") or case.get("jurisdiction") not in (None, "", "UNKNOWN"):
        return []

    def supported(facts: dict) -> set[str]:
        out = set()
        for m in kg.active_modules():
            if m.route != Route.POFA or m.strength < SUPPORTING_THRESHOLD:
                continue
            if evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts):
                out.add(m.module_id)
        return out

    res = pofa.assess(jurisdiction="ENGLAND_WALES", **pofa_inputs(case))
    counterfactual = supported(dict(
        case.fact_view(), jurisdiction="ENGLAND_WALES", pofa_route=res.route,
        pofa_finding=res.findings[0] if res.findings else None))
    # The delta is the whole point: a ground that already applies without the
    # postcode is not unlocked by it, so asking cannot change the outcome.
    # Only the counterfactual set was computed before, so the question was
    # material by assumption rather than by calculation.
    baseline_facts = dict(case.fact_view())
    try:
        base = pofa.assess(jurisdiction=(case.get("jurisdiction") or "UNKNOWN"),
                           **pofa_inputs(case))
        baseline_facts.update(
            pofa_route=base.route,
            pofa_finding=base.findings[0] if base.findings else None)
    except Exception:
        pass                      # no baseline assessment: treat as unsupported
    already = supported(baseline_facts)
    return [mid for mid in sorted(counterfactual) if mid not in already]


def _reverse_not_supplied(report: RecoveryReport) -> dict:
    """The Schedule 4 wording gap when the back was not read. Recorded so the
    finding stays unresolved, but the back is optional: this is not a document
    to ask the customer for, and nothing waits on it."""
    return {
        "fact": "notice_reverse_wording",
        "why_material": (
            "Schedule 4 invitation wording was not found on the pages supplied. The back "
            "of the notice is optional, so the content finding stays unresolved and is "
            "not pleaded; do not ask the customer for the back."
        ),
        "sources_checked": report.sources_checked,
        "action": "unresolved_optional_page",
    }
