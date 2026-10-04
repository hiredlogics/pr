"""P8.3: the document belt — DocumentBaseline and case analysis state.

The notice, its evidence, the deterministic calculations and the VERIFIED
legal findings are established before Case Intelligence runs. That snapshot is
the DocumentBaseline. A later analysis loads it and adds a customer delta; it
does not rebuild the case by replacing document truth.

Case Intelligence receives the baseline (plus customer facts and knowledge
relationships). It proposes only. It does not decide whether document findings
exist, and it cannot delete them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from .legal import findings as legal_findings
from .models import CaseFile, SourceKind

SCHEMA_VERSION = 1

# Notice fields the belt treats as document truth (not customer narrative).
_NOTICE_NAMES: tuple[str, ...] = (
    "operator_name", "operator_ata", "creditor_name",
    "pcn_number", "charge_amount", "notice_type", "notice_route",
    "practice_code_version",
    "vrm", "vehicle_make", "vehicle_colour",
    "parking_event_date", "notice_issue_date", "ntd_date",
    "notice_received_date", "entry_time", "exit_time", "payment_date",
    "parking_location", "site_postcode", "landowner_name",
    "alleged_breach", "contravention_code", "parking_duration_minutes",
    "jurisdiction", "relevant_land", "notice_sides_complete",
    "pofa_route", "pofa_findings", "pofa_finding",
    "total_recorded_duration_min",
)

_DOCUMENT_KINDS = (SourceKind.DOCUMENT, SourceKind.CALCULATION)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _plain(value: Any) -> Any:
    """JSON-stable form: dates are ISO strings so a reload hashes the same."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value") and type(value).__mro__[1].__name__ == "str":
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _sha(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _document_fact_names() -> set[str]:
    return set(_NOTICE_NAMES)


@dataclass
class DocumentBaseline:
    """Document-derived case truth. Independent of Case Intelligence."""
    version: int
    digest: str
    notice_facts: dict
    evidence: list
    legal_critical_facts: dict
    calculations: dict
    legal_findings: list
    document_grounds: list
    document_finding_types: list
    at: str = ""
    run_id: int = 0

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": self.version,
            "digest": self.digest,
            "notice_facts": _plain(self.notice_facts),
            "evidence": _plain(self.evidence),
            "legal_critical_facts": _plain(self.legal_critical_facts),
            "calculations": _plain(self.calculations),
            "legal_findings": _plain(self.legal_findings),
            "document_grounds": list(self.document_grounds),
            "document_finding_types": list(self.document_finding_types),
            "at": self.at,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentBaseline":
        return cls(
            version=int(d.get("version") or 1),
            digest=d.get("digest") or "",
            notice_facts=dict(d.get("notice_facts") or {}),
            evidence=list(d.get("evidence") or []),
            legal_critical_facts=dict(d.get("legal_critical_facts") or {}),
            calculations=dict(d.get("calculations") or {}),
            legal_findings=list(d.get("legal_findings") or []),
            document_grounds=list(d.get("document_grounds") or []),
            document_finding_types=list(d.get("document_finding_types") or []),
            at=d.get("at") or "",
            run_id=int(d.get("run_id") or 0),
        )

    @classmethod
    def capture(cls, case: CaseFile, *, version: int = 1) -> "DocumentBaseline":
        """Snapshot document truth only. Customer answers do not enter the digest."""
        notice, critical, calcs = {}, {}, {}
        allowed = _document_fact_names()
        for name, f in sorted(case.facts.items()):
            if not f.usable or f.value in (None, "", []):
                continue
            if name not in allowed and f.source.kind not in _DOCUMENT_KINDS:
                continue
            if name not in allowed:
                continue
            entry = _plain(f.value)
            if name in ("parking_event_date", "notice_issue_date", "ntd_date",
                        "notice_received_date", "entry_time", "exit_time",
                        "pcn_number", "vrm", "operator_name", "alleged_breach"):
                critical[name] = entry
            if name in ("pofa_route", "pofa_findings", "pofa_finding",
                        "jurisdiction", "notice_route", "relevant_land"):
                calcs[name] = entry
            notice[name] = entry
        evidence = [
            {"evidence_id": e.evidence_id, "kind": e.kind, "filename": e.filename}
            for e in sorted(case.evidence.values(), key=lambda e: e.evidence_id)
            if e.uploaded
        ]
        findings = []
        grounds, types = [], []
        for r in case.legal_findings or []:
            row = {
                "type": r.get("finding_type"),
                "status": r.get("status"),
                "legal_module_id": r.get("legal_module_id"),
            }
            findings.append(row)
            if r.get("status") == legal_findings.VERIFIED:
                if r.get("finding_type"):
                    types.append(str(r["finding_type"]))
                mid = r.get("legal_module_id")
                if mid and mid not in grounds:
                    grounds.append(mid)
        # Reload returns findings in table order; the digest is document truth,
        # not insertion order.
        findings.sort(key=lambda r: (str(r.get("type") or ""), str(r.get("status") or "")))
        types = sorted(set(types))
        grounds = list(dict.fromkeys(grounds))
        body = {
            "notice_facts": notice,
            "evidence": evidence,
            "legal_critical_facts": critical,
            "calculations": calcs,
            "legal_findings": findings,
            "document_grounds": grounds,
            "document_finding_types": types,
        }
        return cls(version=version, digest=_sha(body), at=_now(),
                   run_id=int(getattr(case, "run_id", 0) or 0), **body)


@dataclass
class CaseAnalysisState:
    """One reproducible Case Intelligence run against a document baseline."""
    case_id: str
    analysis_version: int
    document_baseline_version: int
    customer_analysis_version: int
    candidate_ground_ids: list
    selected_ground_ids: list
    verified_ground_ids: list
    rejected_ground_ids: list
    timestamp: str
    created_by: str = "case_intelligence"
    customer_facts_digest: str = ""
    add_ground_candidates: list = field(default_factory=list)
    support_existing_ground: list = field(default_factory=list)
    proposed_invalidations: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "analysis_version": self.analysis_version,
            "document_baseline_version": self.document_baseline_version,
            "customer_analysis_version": self.customer_analysis_version,
            "candidate_ground_ids": list(self.candidate_ground_ids),
            "selected_ground_ids": list(self.selected_ground_ids),
            "verified_ground_ids": list(self.verified_ground_ids),
            "rejected_ground_ids": list(self.rejected_ground_ids),
            "timestamp": self.timestamp,
            "created_by": self.created_by,
            "customer_facts_digest": self.customer_facts_digest,
            "add_ground_candidates": list(self.add_ground_candidates),
            "support_existing_ground": list(self.support_existing_ground),
            "proposed_invalidations": list(self.proposed_invalidations),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CaseAnalysisState":
        return cls(
            case_id=d.get("case_id") or "",
            analysis_version=int(d.get("analysis_version") or 1),
            document_baseline_version=int(d.get("document_baseline_version") or 0),
            customer_analysis_version=int(d.get("customer_analysis_version") or 0),
            candidate_ground_ids=list(d.get("candidate_ground_ids") or []),
            selected_ground_ids=list(d.get("selected_ground_ids") or []),
            verified_ground_ids=list(d.get("verified_ground_ids") or []),
            rejected_ground_ids=list(d.get("rejected_ground_ids") or []),
            timestamp=d.get("timestamp") or "",
            created_by=d.get("created_by") or "case_intelligence",
            customer_facts_digest=d.get("customer_facts_digest") or "",
            add_ground_candidates=list(d.get("add_ground_candidates") or []),
            support_existing_ground=list(d.get("support_existing_ground") or []),
            proposed_invalidations=list(d.get("proposed_invalidations") or []),
        )


def latest_baseline(case: CaseFile) -> Optional[DocumentBaseline]:
    rows = list(getattr(case, "document_baselines", None) or [])
    if not rows:
        return None
    return DocumentBaseline.from_dict(rows[-1])


def latest_analysis_state(case: CaseFile) -> Optional[CaseAnalysisState]:
    rows = list(getattr(case, "case_analysis_states", None) or [])
    if not rows:
        return None
    return CaseAnalysisState.from_dict(rows[-1])


def establish_document_baseline(pipe, case: CaseFile) -> DocumentBaseline:
    """Run the document belt, then record or reuse the baseline.

    Order: enrich → recovery (documents/calculators) → applicability
    (legal findings). Case Intelligence is not called here.
    """
    if hasattr(pipe, "reasoning") and hasattr(pipe.reasoning, "enrich"):
        pipe.reasoning.enrich(case)
    if hasattr(pipe, "recovery"):
        pipe.recovery.recover(case)
    if hasattr(pipe, "reasoning") and hasattr(pipe.reasoning, "applicability"):
        pipe.reasoning.applicability(case)
    snap = DocumentBaseline.capture(case)
    held = latest_baseline(case)
    if held is not None and held.digest == snap.digest:
        case.audit.append({
            "event": "document_baseline_reused",
            "version": held.version, "digest": held.digest,
        })
        return held
    snap.version = (held.version + 1) if held is not None else 1
    row = snap.as_dict()
    case.document_baselines.append(row)
    case.audit.append({
        "event": "document_baseline",
        "version": snap.version,
        "digest": snap.digest,
        "document_grounds": list(snap.document_grounds),
        "document_finding_types": list(snap.document_finding_types),
        "legal_findings": [{"type": f.get("type"), "status": f.get("status")}
                           for f in snap.legal_findings
                           if f.get("status") == legal_findings.VERIFIED],
    })
    return snap


def _customer_facts_digest(case: CaseFile) -> str:
    out = {}
    for name, f in sorted(case.facts.items()):
        if not f.usable:
            continue
        if f.source.kind in (SourceKind.ANSWER, SourceKind.CUSTOMER_FREE_TEXT):
            out[name] = _plain(f.value)
    stated = {k: v for k, v in (case.raw_answers or {}).items()
              if not str(k).startswith("_")}
    return _sha({"facts": out, "stated": stated})


def record_analysis_state(case: CaseFile, analysis, baseline: DocumentBaseline) -> CaseAnalysisState:
    """Persist one CI proposal run against the current document baseline."""
    prev = latest_analysis_state(case)
    cust = _customer_facts_digest(case)
    if prev is not None and prev.customer_facts_digest == cust:
        cust_ver = prev.customer_analysis_version
    else:
        cust_ver = (prev.customer_analysis_version + 1) if prev is not None else 1
    selected = [m for m in (getattr(analysis, "module_ids", None) or []) if m]
    rejected = [s.get("module_id") for s in (getattr(analysis, "suppressed", None) or [])
                if s.get("module_id")]
    verified = list(baseline.document_grounds)
    # Proposals may support existing verified grounds; they may not drop them.
    add = [m for m in selected if m not in verified]
    support = [m for m in selected if m in verified]
    proposed_inv = [
        {"ground_id": s.get("module_id"),
         "reason": s.get("why") or s.get("reason") or "suppressed"}
        for s in (getattr(analysis, "suppressed", None) or []) if s.get("module_id")
        and s.get("module_id") not in verified
    ]
    state = CaseAnalysisState(
        case_id=case.case_id,
        analysis_version=(prev.analysis_version + 1) if prev is not None else 1,
        document_baseline_version=int(baseline.version),
        customer_analysis_version=cust_ver,
        candidate_ground_ids=list(getattr(analysis, "candidate_ids", None) or []),
        selected_ground_ids=selected,
        verified_ground_ids=verified,
        rejected_ground_ids=rejected,
        timestamp=_now(),
        created_by="case_intelligence",
        customer_facts_digest=cust,
        add_ground_candidates=add,
        support_existing_ground=support,
        proposed_invalidations=proposed_inv,
    )
    case.case_analysis_states.append(state.as_dict())
    case.audit.append({
        "event": "case_analysis_state",
        "analysis_version": state.analysis_version,
        "document_baseline_version": state.document_baseline_version,
        "customer_analysis_version": state.customer_analysis_version,
        "candidate_ground_ids": state.candidate_ground_ids,
        "selected_ground_ids": state.selected_ground_ids,
        "verified_ground_ids": state.verified_ground_ids,
        "rejected_ground_ids": state.rejected_ground_ids,
        "created_by": state.created_by,
    })
    return state


def customer_delta(case: CaseFile) -> list[str]:
    """Grounds CI added that the document baseline did not already license."""
    base = latest_baseline(case)
    verified = set(base.document_grounds) if base is not None else set()
    state = latest_analysis_state(case)
    if state is None:
        return []
    return [m for m in state.add_ground_candidates if m not in verified]


def baseline_trace(case: CaseFile, plan=None) -> list[str]:
    """Admin trace: DOCUMENT BASELINE → CUSTOMER DELTA → FINAL."""
    base = latest_baseline(case)
    out = ["DOCUMENT BASELINE"]
    if base is None:
        out.append("  (none)")
    else:
        types = base.document_finding_types or base.document_grounds
        if types:
            out.extend(f"  ✓ {t}" for t in types)
        else:
            out.append("  (no verified document finding)")
    out.append("CUSTOMER DELTA")
    added = customer_delta(case)
    if added:
        out.extend(f"  + {m}" for m in added)
    else:
        out.append("  (none)")
    out.append("FINAL")
    finals: Iterable[str] = ()
    if plan is not None and getattr(plan, "supported_ids", None):
        finals = plan.supported_ids
    else:
        finals = list((latest_baseline(case).document_grounds if latest_baseline(case) else []))
        finals = list(finals) + customer_delta(case)
    if finals:
        out.extend(f"  ✓ {m}" for m in finals)
    else:
        out.append("  (none)")
    return out


__all__ = [
    "DocumentBaseline", "CaseAnalysisState", "SCHEMA_VERSION",
    "establish_document_baseline", "record_analysis_state",
    "latest_baseline", "latest_analysis_state", "customer_delta", "baseline_trace",
]
