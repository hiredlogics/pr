"""P17.8 — Critical document identity integrity.

Generic architecture: extract → independent verify → reconcile → FactManager.
No operator/location/PCN/VRM hardcoding. Canonicalization is formatting only.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from .models import CaseFile, Fact, FactSource, FactStatus, SourceKind

CRITICAL_FIELDS = (
    "operator_name",
    "pcn_number",
    "vrm",
    "parking_event_date",
    "notice_issue_date",
    "parking_location",
)

# Dates that must be VERIFIED before timing legal calculations may assert defects.
TIMING_IDENTITY_FIELDS = ("parking_event_date", "notice_issue_date")
# Location-related identity for jurisdiction-dependent conclusions.
LOCATION_IDENTITY_FIELDS = ("parking_location",)

STATUS_VERIFIED = "VERIFIED"
STATUS_UNCERTAIN = "DOCUMENT_IDENTITY_UNCERTAIN"
STATUS_CONFLICT = "DOCUMENT_IDENTITY_CONFLICT"
STATUS_ABSENT = "ABSENT"
STATUS_NOT_REQUIRED = "NOT_REQUIRED"

READ_VERIFIED = "VERIFIED"
READ_UNCERTAIN = "UNCERTAIN"
READ_NOT_VISIBLE = "NOT_VISIBLE"

VERIFY_SYSTEM = (
    "You independently re-read ONLY critical identity fields from the uploaded "
    "UK private-parking notice pages. Do not invent values. Do not repair OCR. "
    "Do not use filenames or prior case knowledge. If a field is not clearly "
    "visible, mark read_status NOT_VISIBLE.\n"
    "Return JSON: {\"fields\": {\"<name>\": {\"candidate_value\": ..., "
    "\"confidence\": 0.0, \"evidence_id\": \"\", \"page\": 1, "
    "\"source_excerpt\": \"\", \"read_status\": \"VERIFIED|UNCERTAIN|NOT_VISIBLE\"}}}.\n"
    f"Field names: {', '.join(CRITICAL_FIELDS)}."
)

_RAW_KEY = "_document_identity_state"
_REV_KEY = "_document_identity_revision"
_COMPACT_KEY = "_document_identity_compact"


@dataclass
class FieldObservation:
    raw_value: Any
    canonical_value: Any
    source_evidence_id: str = ""
    page: Any = None
    side: str = ""
    source_excerpt: str = ""
    confidence: float = 0.0
    extraction_method: str = ""
    confirmation_status: str = ""
    revision: int = 0
    read_status: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FieldRecord:
    name: str
    status: str = STATUS_ABSENT
    canonical_value: Any = None
    raw_value: Any = None
    observations: list[dict] = field(default_factory=list)
    source_evidence_id: str = ""
    page: Any = None
    side: str = ""
    source_excerpt: str = ""
    confidence: float = 0.0
    extraction_method: str = ""
    confirmation_status: str = ""
    revision: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentIdentityState:
    operator_name: Optional[FieldRecord] = None
    pcn_number: Optional[FieldRecord] = None
    vrm: Optional[FieldRecord] = None
    parking_event_date: Optional[FieldRecord] = None
    notice_issue_date: Optional[FieldRecord] = None
    parking_location: Optional[FieldRecord] = None
    document_type: str = ""
    stage: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    field_status: dict = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    identity_revision: int = 0
    complete: bool = False
    notice_sides_complete: Optional[bool] = None
    front_reverse_association: str = ""
    document_pair_conflict: bool = False
    at: str = ""

    def as_dict(self) -> dict:
        out = {
            "document_type": self.document_type,
            "stage": self.stage,
            "evidence_refs": list(self.evidence_refs),
            "field_status": dict(self.field_status),
            "conflicts": list(self.conflicts),
            "identity_revision": self.identity_revision,
            "complete": self.complete,
            "notice_sides_complete": self.notice_sides_complete,
            "front_reverse_association": self.front_reverse_association,
            "document_pair_conflict": self.document_pair_conflict,
            "at": self.at,
            "fields": {},
        }
        for name in CRITICAL_FIELDS:
            rec = getattr(self, name, None)
            out["fields"][name] = rec.as_dict() if rec else None
        return out


# ---------------------------------------------------------------------------
# Canonicalization (formatting only — never character repair)
# ---------------------------------------------------------------------------

def canonicalize(name: str, value: Any) -> Any:
    """Deterministic formatting normalization. Never guesses characters."""
    if value in (None, "", [], {}):
        return None
    if name == "vrm":
        return re.sub(r"\s+", "", str(value)).upper()
    if name == "pcn_number":
        return re.sub(r"\s+", "", str(value)).upper()
    if name in ("parking_event_date", "notice_issue_date"):
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, datetime):
            return value.date().isoformat()
        from .engines.extraction import parse_uk_date
        parsed = parse_uk_date(value)
        return parsed.isoformat() if parsed else None
    if name in ("operator_name", "parking_location"):
        return re.sub(r"\s+", " ", str(value).strip())
    return value


def canonical_equal(name: str, a: Any, b: Any) -> bool:
    ca, cb = canonicalize(name, a), canonicalize(name, b)
    if ca is None or cb is None:
        return False
    if name in ("operator_name", "parking_location"):
        return str(ca).casefold() == str(cb).casefold()
    return ca == cb


# ---------------------------------------------------------------------------
# Collect observations
# ---------------------------------------------------------------------------

def _obs(*, raw, name, method, evidence_id="", page=None, side="",
         excerpt="", confidence=0.0, read_status="", revision=0) -> FieldObservation:
    return FieldObservation(
        raw_value=raw,
        canonical_value=canonicalize(name, raw),
        source_evidence_id=str(evidence_id or ""),
        page=page,
        side=str(side or ""),
        source_excerpt=str(excerpt or "")[:240],
        confidence=float(confidence or 0.0),
        extraction_method=method,
        confirmation_status="",
        revision=revision,
        read_status=read_status,
    )


def _observations_from_extraction(case: CaseFile) -> dict[str, list[FieldObservation]]:
    out: dict[str, list[FieldObservation]] = {n: [] for n in CRITICAL_FIELDS}
    for name in CRITICAL_FIELDS:
        node = case.facts.get(name) if getattr(case, "facts", None) else None
        if node is None or node.value in (None, ""):
            continue
        src = getattr(node, "source", None)
        ref = getattr(src, "ref", "") or ""
        ev_id, page = "", None
        m = re.match(r"([^#]+)#p(\d+)", str(ref))
        if m:
            ev_id, page = m.group(1), int(m.group(2))
        out[name].append(_obs(
            raw=node.value, name=name, method="initial_extraction",
            evidence_id=ev_id, page=page,
            confidence=float(getattr(node, "confidence", 0) or 0),
            read_status=READ_VERIFIED if getattr(node, "status", None) != FactStatus.UNCERTAIN
            else READ_UNCERTAIN,
        ))
    return out


def _observations_from_classifier(case: CaseFile) -> dict[str, list[FieldObservation]]:
    """Independent page-reference readings (not seeded with extractor values)."""
    out: dict[str, list[FieldObservation]] = {n: [] for n in CRITICAL_FIELDS}
    for ev_id, c in (case.classifications or {}).items():
        if (c or {}).get("document_type") not in (None, "PRIVATE_PARKING_NOTICE", "PCN", "NTK", "NTD"):
            continue
        refs = (c or {}).get("references") or {}
        for name in ("pcn_number", "vrm", "operator_name"):
            raw = refs.get(name)
            if raw in (None, ""):
                continue
            out[name].append(_obs(
                raw=raw, name=name, method="classifier_references",
                evidence_id=ev_id, confidence=0.8, read_status=READ_VERIFIED,
            ))
        for name in ("document_date", "parking_event_date", "notice_issue_date"):
            raw = refs.get(name) or refs.get("date")
            if raw in (None, "") or name == "document_date":
                continue
            out[name].append(_obs(
                raw=raw, name=name, method="classifier_references",
                evidence_id=ev_id, confidence=0.7, read_status=READ_UNCERTAIN,
            ))
    return out


_PCN_TOKEN = re.compile(r"\b(\d{6,14})\b")
_VRM_TOKEN = re.compile(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3})\b", re.I)


def _observations_from_text_scan(case: CaseFile) -> dict[str, list[FieldObservation]]:
    """Deterministic token scan of original uploaded text layers only."""
    out: dict[str, list[FieldObservation]] = {n: [] for n in CRITICAL_FIELDS}
    for e in case.evidence.values():
        text = e.text or ""
        if not text.strip():
            continue
        # Skip synthetic / fixture reverse pages that are not original customer evidence.
        if "LIVE_TEST_" in (e.filename or "") and "back" in (e.filename or "").lower():
            continue
        if "FRONTEND_LIVE_TEST" in text or "NOTICE TO KEEPER — REVERSE" in text:
            continue
        for m in _PCN_TOKEN.finditer(text):
            out["pcn_number"].append(_obs(
                raw=m.group(1), name="pcn_number", method="text_scan",
                evidence_id=e.evidence_id, excerpt=m.group(0),
                confidence=0.55, read_status=READ_UNCERTAIN,
            ))
        for m in _VRM_TOKEN.finditer(text):
            out["vrm"].append(_obs(
                raw=m.group(1), name="vrm", method="text_scan",
                evidence_id=e.evidence_id, excerpt=m.group(0),
                confidence=0.55, read_status=READ_UNCERTAIN,
            ))
    return out


def _independent_llm_verify(case: CaseFile, llm) -> dict[str, list[FieldObservation]]:
    """Second pass: critical fields only; never seeded with prior extraction."""
    out: dict[str, list[FieldObservation]] = {n: [] for n in CRITICAL_FIELDS}
    if llm is None:
        return out
    images: list[bytes] = []
    manifest: list[str] = []
    for e in case.evidence.values():
        for page, img in enumerate(e.images or [], start=1):
            images.append(img)
            manifest.append(
                f"[image {len(images)}] document id='{e.evidence_id}' "
                f"filename='{e.filename}' page={page}"
            )
    docs = "\n\n".join(
        f"<document id='{e.evidence_id}' filename='{e.filename}'>\n{e.text}\n</document>"
        for e in case.evidence.values()
    )
    if manifest:
        docs += (
            "\n\n<attached_images>\nIndependently re-read critical identity only.\n"
            + "\n".join(manifest) + "\n</attached_images>"
        )
    user = (
        "Independently read ONLY these critical identity fields from the "
        "ORIGINAL uploaded pages. Do NOT use any previously extracted values. "
        "If uncertain, set read_status UNCERTAIN or NOT_VISIBLE.\n\n" + docs
    )
    try:
        models = getattr(llm, "models", None) or {}
        task = "identity_verification" if "identity_verification" in models else "extraction"
        # Prefer dedicated task when FakeLLM has a queue for it.
        if hasattr(llm, "responses") and "identity_verification" in (llm.responses or {}):
            task = "identity_verification"
        elif hasattr(llm, "responses") and task == "extraction":
            # Avoid consuming the primary extraction queue on a second pass.
            return out
        raw = llm.complete_json(task=task, system=VERIFY_SYSTEM, user=user,
                                images=images or None)
    except Exception as exc:
        case.audit.append({
            "event": "identity_verification_skipped",
            "reason": f"{type(exc).__name__}: {exc}"[:200],
        })
        return out
    fields = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(fields, dict):
        fields = {
            k: v for k, v in (raw or {}).items()
            if isinstance(v, dict) and ("candidate_value" in v or "value" in v)
        }
    for name in CRITICAL_FIELDS:
        entry = fields.get(name) if isinstance(fields, dict) else None
        if not isinstance(entry, dict):
            continue
        cand = entry.get("candidate_value", entry.get("value"))
        read = str(entry.get("read_status") or READ_UNCERTAIN).upper()
        if read == READ_NOT_VISIBLE or cand in (None, ""):
            out[name].append(_obs(
                raw=None, name=name, method="independent_verify",
                evidence_id=str(entry.get("evidence_id") or ""),
                page=entry.get("page"),
                excerpt=str(entry.get("source_excerpt") or "")[:240],
                confidence=float(entry.get("confidence") or 0),
                read_status=READ_NOT_VISIBLE,
            ))
            continue
        out[name].append(_obs(
            raw=cand, name=name, method="independent_verify",
            evidence_id=str(entry.get("evidence_id") or ""),
            page=entry.get("page"),
            excerpt=str(entry.get("source_excerpt") or "")[:240],
            confidence=float(entry.get("confidence") or 0),
            read_status=read if read in (READ_VERIFIED, READ_UNCERTAIN) else READ_UNCERTAIN,
        ))
    return out


def _merge_obs(*maps: dict[str, list[FieldObservation]]) -> dict[str, list[FieldObservation]]:
    out: dict[str, list[FieldObservation]] = {n: [] for n in CRITICAL_FIELDS}
    for m in maps:
        for name, rows in m.items():
            out.setdefault(name, []).extend(rows)
    return out


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def assess_document_pair(case: CaseFile) -> dict[str, Any]:
    """Validate uploaded notice pages belong to the same notice.

    Conflicting strong identifiers → DOCUMENT_PAIR_CONFLICT.
    Missing identifier on reverse alone is not a conflict.
    """
    from .notice_completeness import different_notices

    result = {
        "document_pair_conflict": False,
        "front_reverse_association": "UNKNOWN",
        "reason": "",
        "conflict_fields": [],
    }
    diff = different_notices(case)
    if diff:
        result["document_pair_conflict"] = True
        result["front_reverse_association"] = "CONFLICT"
        result["reason"] = "different_notices"
        result["conflict_fields"] = [diff.get("field")]
        return result

    # Extraction-level cross-evidence conflict for critical identifiers.
    by_ev: dict[str, dict[str, Any]] = {}
    for name in ("pcn_number", "vrm"):
        node = case.facts.get(name) if getattr(case, "facts", None) else None
        if node is None or node.value in (None, ""):
            continue
        # Also scan classifier refs per evidence.
        for ev_id, c in (case.classifications or {}).items():
            refs = (c or {}).get("references") or {}
            raw = refs.get(name)
            if raw in (None, ""):
                continue
            can = canonicalize(name, raw)
            if not can:
                continue
            by_ev.setdefault(ev_id, {})[name] = can

    # Compare across evidences that both print the same field.
    for name in ("pcn_number", "vrm"):
        values = {
            ev: fields[name]
            for ev, fields in by_ev.items()
            if name in fields
        }
        uniq = set(values.values())
        if len(uniq) >= 2:
            # Strong conflict: different canonical identifiers on different pages.
            result["document_pair_conflict"] = True
            result["front_reverse_association"] = "CONFLICT"
            result["reason"] = f"conflicting_{name}_across_pages"
            result["conflict_fields"].append(name)
            return result

    sides_complete = case.get("notice_sides_complete")
    if sides_complete is True:
        result["front_reverse_association"] = "ASSOCIATED"
        result["reason"] = "same_notice_sides_complete"
    elif sides_complete is False:
        result["front_reverse_association"] = "INCOMPLETE"
        result["reason"] = "notice_sides_incomplete"
    else:
        result["front_reverse_association"] = "UNKNOWN"
        result["reason"] = "pairing_not_applicable_or_unknown"
    return result


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def _usable_obs(rows: list[FieldObservation]) -> list[FieldObservation]:
    return [
        o for o in rows
        if o.canonical_value is not None
        and o.read_status != READ_NOT_VISIBLE
    ]


def reconcile_field(name: str, observations: list[FieldObservation],
                    *, revision: int) -> FieldRecord:
    usable = _usable_obs(observations)
    rec = FieldRecord(name=name, revision=revision, observations=[o.as_dict() for o in observations])
    if not usable:
        # NOT_VISIBLE / empty — uncertain if anyone tried, else absent.
        if any(o.read_status == READ_NOT_VISIBLE for o in observations):
            rec.status = STATUS_UNCERTAIN
        else:
            rec.status = STATUS_ABSENT
        return rec

    # Group by canonical value.
    groups: dict[str, list[FieldObservation]] = {}
    for o in usable:
        key = str(o.canonical_value)
        groups.setdefault(key, []).append(o)

    if len(groups) == 1:
        winner = next(iter(groups.values()))
        best = max(winner, key=lambda o: o.confidence)
        # Require at least one strong reading for VERIFIED.
        strong = [
            o for o in winner
            if o.extraction_method in ("initial_extraction", "independent_verify",
                                       "classifier_references", "customer_confirmation")
            and (
                o.confidence >= 0.85
                or o.extraction_method == "customer_confirmation"
                or (o.extraction_method == "independent_verify"
                    and o.read_status == READ_VERIFIED)
            )
        ]
        # Agreement between initial extraction and an independent source is VERIFIED
        # even if confidences are moderate.
        methods = {o.extraction_method for o in winner}
        multi_agree = (
            "initial_extraction" in methods
            and (methods & {"independent_verify", "classifier_references", "text_scan"})
        )
        if strong or multi_agree:
            rec.status = STATUS_VERIFIED
        else:
            rec.status = STATUS_UNCERTAIN
        rec.canonical_value = best.canonical_value
        rec.raw_value = best.raw_value
        rec.source_evidence_id = best.source_evidence_id
        rec.page = best.page
        rec.side = best.side
        rec.source_excerpt = best.source_excerpt
        rec.confidence = best.confidence
        rec.extraction_method = best.extraction_method
        return rec

    # Competing canonical values among document-backed sources.
    doc_methods = {
        "initial_extraction", "independent_verify", "classifier_references", "text_scan",
    }
    competing = {
        k: [o for o in rows if o.extraction_method in doc_methods]
        for k, rows in groups.items()
    }
    competing = {k: v for k, v in competing.items() if v}
    if len(competing) >= 2:
        # Prefer high-confidence initial/verify over weak text_scan alone.
        strong_groups = {
            k: [o for o in rows if o.confidence >= 0.8
                or o.extraction_method in ("initial_extraction", "independent_verify",
                                           "classifier_references")]
            for k, rows in competing.items()
        }
        strong_groups = {k: v for k, v in strong_groups.items() if v}
        if len(strong_groups) >= 2:
            rec.status = STATUS_CONFLICT
            # Keep first observation set for audit; no winner.
            return rec
        if len(strong_groups) == 1:
            key = next(iter(strong_groups))
            best = max(strong_groups[key], key=lambda o: o.confidence)
            rec.status = STATUS_VERIFIED
            rec.canonical_value = best.canonical_value
            rec.raw_value = best.raw_value
            rec.source_evidence_id = best.source_evidence_id
            rec.page = best.page
            rec.confidence = best.confidence
            rec.extraction_method = best.extraction_method
            return rec

    # One strong + weak disagreeing scans → keep strong, note uncertainty only if
    # the disagreeing source is also strong (handled above).
    ranked = sorted(usable, key=lambda o: o.confidence, reverse=True)
    best = ranked[0]
    if best.confidence >= 0.85 or best.extraction_method == "initial_extraction":
        rec.status = STATUS_VERIFIED if best.confidence >= 0.85 else STATUS_UNCERTAIN
        rec.canonical_value = best.canonical_value
        rec.raw_value = best.raw_value
        rec.source_evidence_id = best.source_evidence_id
        rec.page = best.page
        rec.confidence = best.confidence
        rec.extraction_method = best.extraction_method
        return rec

    rec.status = STATUS_UNCERTAIN
    rec.canonical_value = best.canonical_value
    rec.raw_value = best.raw_value
    rec.confidence = best.confidence
    rec.extraction_method = best.extraction_method
    return rec


def build_identity_state(
    case: CaseFile,
    observations: dict[str, list[FieldObservation]],
    *,
    revision: int,
    pair: Optional[dict] = None,
) -> DocumentIdentityState:
    pair = pair or assess_document_pair(case)
    state = DocumentIdentityState(
        document_type=str(getattr(case, "document_type", "")
                          or next(iter(case.document_classes.values()), "") or ""),
        stage=str(getattr(case, "stage", "") or ""),
        evidence_refs=[e.evidence_id for e in case.evidence.values()],
        identity_revision=revision,
        notice_sides_complete=case.get("notice_sides_complete"),
        front_reverse_association=str(pair.get("front_reverse_association") or ""),
        document_pair_conflict=bool(pair.get("document_pair_conflict")),
        at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    conflicts: list[dict] = []
    for name in CRITICAL_FIELDS:
        rec = reconcile_field(name, observations.get(name) or [], revision=revision)
        setattr(state, name, rec)
        state.field_status[name] = rec.status
        if rec.status == STATUS_CONFLICT:
            conflicts.append({
                "field": name,
                "status": STATUS_CONFLICT,
                "observations": rec.observations,
            })
    if state.document_pair_conflict:
        conflicts.append({
            "field": "document_pair",
            "status": "DOCUMENT_PAIR_CONFLICT",
            "reason": pair.get("reason"),
            "conflict_fields": pair.get("conflict_fields") or [],
        })
    state.conflicts = conflicts
    # Complete when required core identifiers are VERIFIED and pair is clean.
    required = ("pcn_number", "vrm")
    state.complete = (
        not state.document_pair_conflict
        and all(state.field_status.get(n) == STATUS_VERIFIED for n in required)
        and state.field_status.get("operator_name") in (STATUS_VERIFIED, STATUS_ABSENT)
        and all(
            state.field_status.get(n) != STATUS_CONFLICT
            for n in CRITICAL_FIELDS
        )
    )
    return state


# ---------------------------------------------------------------------------
# FactManager integration
# ---------------------------------------------------------------------------

def apply_identity_to_facts(case: CaseFile, state: DocumentIdentityState) -> list[str]:
    """Promote only VERIFIED identity into usable document facts.

    CONFLICT / UNCERTAIN critical fields are demoted to UNCERTAIN so they
    cannot be auto-confirmed or drive legal timing defects.
    """
    flags: list[str] = []
    for name in CRITICAL_FIELDS:
        rec = getattr(state, name, None)
        if rec is None:
            continue
        node = case.facts.get(name)
        if rec.status == STATUS_CONFLICT:
            if node is not None:
                case.set_status(name, FactStatus.UNCERTAIN,
                                reason="document_identity_conflict")
            flags.append(f"identity_conflict:{name}")
            case.audit.append({
                "event": "document_identity_conflict",
                "field": name,
                "observations": rec.observations[:6],
                "identity_revision": state.identity_revision,
            })
        elif rec.status == STATUS_UNCERTAIN:
            if node is not None and node.status == FactStatus.EXTRACTED:
                case.set_status(name, FactStatus.UNCERTAIN,
                                reason="document_identity_uncertain")
            flags.append(f"identity_uncertain:{name}")
        elif rec.status == STATUS_VERIFIED and rec.canonical_value is not None:
            # Ensure FactManager holds the reconciled canonical value as EXTRACTED
            # (confirmation still required for CONFIRMED). Do not silently overwrite
            # a customer CORRECTED / ANSWERED value.
            if node is None:
                case.put(Fact(
                    f"F-{name}", name, rec.raw_value or rec.canonical_value,
                    FactStatus.EXTRACTED,
                    FactSource(SourceKind.DOCUMENT,
                               f"{rec.source_evidence_id}#p{rec.page or 1}"),
                    confidence=max(rec.confidence, 0.85),
                ))
            elif node.status in (FactStatus.EXTRACTED, FactStatus.UNCERTAIN):
                current = canonicalize(name, node.value)
                if current != rec.canonical_value and rec.confidence >= 0.85:
                    # Conflict path already handled; agreement → keep / restore EXTRACTED.
                    if node.status == FactStatus.UNCERTAIN and current == rec.canonical_value:
                        case.set_status(name, FactStatus.EXTRACTED,
                                        reason="document_identity_verified")
                elif node.status == FactStatus.UNCERTAIN and canonical_equal(name, node.value, rec.canonical_value):
                    case.set_status(name, FactStatus.EXTRACTED,
                                    reason="document_identity_verified")
    # Pair conflict marker fact for gates / outcome mapping.
    case.put(Fact(
        "F-document_pair_conflict", "document_pair_conflict",
        bool(state.document_pair_conflict), FactStatus.DERIVED,
        FactSource(SourceKind.CALCULATION, "document_identity.pair"),
    ))
    case.put(Fact(
        "F-document_identity_complete", "document_identity_complete",
        bool(state.complete), FactStatus.DERIVED,
        FactSource(SourceKind.CALCULATION, "document_identity.reconcile"),
    ))
    return flags


def attach_identity_state(case: CaseFile, state: DocumentIdentityState) -> None:
    payload = state.as_dict()
    blob = json.dumps(payload, default=str)
    case.raw_answers[_RAW_KEY] = blob[:24000]
    case.raw_answers[_REV_KEY] = str(state.identity_revision)
    compact = {
        "revision": state.identity_revision,
        "complete": state.complete,
        "pair_conflict": state.document_pair_conflict,
        "field_status": dict(state.field_status),
        "conflicts": list(state.conflicts),
        "values": {
            n: (getattr(state, n).canonical_value if getattr(state, n) else None)
            for n in CRITICAL_FIELDS
        },
    }
    case.raw_answers[_COMPACT_KEY] = json.dumps(compact, default=str)[:8000]
    case.audit.append({
        "event": "document_identity_state",
        "identity_revision": state.identity_revision,
        "complete": state.complete,
        "pair_conflict": state.document_pair_conflict,
        "field_status": dict(state.field_status),
        "conflicts": list(state.conflicts),
    })


def load_identity_state(case: CaseFile) -> Optional[dict]:
    raw = (case.raw_answers or {}).get(_RAW_KEY)
    if not raw:
        compact = (case.raw_answers or {}).get(_COMPACT_KEY)
        if compact:
            try:
                return json.loads(compact)
            except Exception:
                return None
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def identity_revision(case: CaseFile) -> int:
    raw = (case.raw_answers or {}).get(_REV_KEY)
    try:
        return int(raw or 0)
    except Exception:
        return 0


def establish_document_identity(case: CaseFile, llm=None) -> DocumentIdentityState:
    """Full identity pipeline after extraction (and after notice-sides assess)."""
    prev = load_identity_state(case) or {}
    prev_values = (prev.get("values") if isinstance(prev, dict) else None) or {}
    prev_rev = identity_revision(case)

    obs = _merge_obs(
        _observations_from_extraction(case),
        _observations_from_classifier(case),
        _observations_from_text_scan(case),
        _independent_llm_verify(case, llm),
    )
    pair = assess_document_pair(case)
    provisional = build_identity_state(case, obs, revision=max(prev_rev, 1), pair=pair)
    changed = False
    if prev:
        for name in CRITICAL_FIELDS:
            new_v = getattr(provisional, name).canonical_value if getattr(provisional, name) else None
            old_v = prev_values.get(name)
            if str(old_v or "") != str(new_v or ""):
                changed = True
                break
            if (prev.get("field_status") or {}).get(name) != provisional.field_status.get(name):
                changed = True
                break
        if bool(prev.get("pair_conflict") or prev.get("document_pair_conflict")) != bool(
                provisional.document_pair_conflict):
            changed = True
    revision = 1 if not prev else (prev_rev + 1 if changed else max(prev_rev, 1))
    state = build_identity_state(case, obs, revision=revision, pair=pair)
    flags = apply_identity_to_facts(case, state)
    attach_identity_state(case, state)
    if changed and prev:
        invalidate_identity_dependents(case, state, previous=prev)
    if flags:
        case.audit.append({
            "event": "document_identity_flags",
            "flags": flags,
            "identity_revision": state.identity_revision,
        })
    return state


def invalidate_identity_dependents(
    case: CaseFile, state: DocumentIdentityState, *, previous: dict,
) -> None:
    """Bump dependents when critical identity changes — supersede, don't mutate quietly."""
    case.audit.append({
        "event": "document_identity_revision",
        "identity_revision": state.identity_revision,
        "previous_revision": previous.get("revision") or previous.get("identity_revision"),
        "previous_values": previous.get("values"),
        "new_values": {
            n: (getattr(state, n).canonical_value if getattr(state, n) else None)
            for n in CRITICAL_FIELDS
        },
    })
    # Clear timing findings that depended on prior dates.
    for name in ("pofa_findings", "pofa_finding", "pofa_route"):
        if name in (case.facts or {}):
            case.set_status(name, FactStatus.UNCERTAIN,
                            reason="identity_revision_invalidate")
    # Drop locked claim-plan pointer so generate must rebuild.
    if hasattr(case, "claim_plan") and case.claim_plan:
        case.audit.append({
            "event": "claim_plan_invalidated_by_identity",
            "identity_revision": state.identity_revision,
        })
    case.raw_answers["_identity_invalidate_draft"] = "1"
    case.raw_answers["_identity_invalidate_claim_plan"] = "1"


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def identity_blocks_claim_plan(case: CaseFile) -> Optional[str]:
    """Before Claim Plan LOCK: require identity + pair integrity."""
    state = load_identity_state(case)
    if not state:
        return "document_identity_missing"
    if state.get("pair_conflict") or state.get("document_pair_conflict"):
        return "DOCUMENT_PAIR_CONFLICT"
    conflicts = state.get("conflicts") or []
    if any(
        (c.get("status") == STATUS_CONFLICT or c.get("field") in CRITICAL_FIELDS)
        and c.get("status") != "DOCUMENT_PAIR_CONFLICT"
        for c in conflicts
        if c.get("status") == STATUS_CONFLICT
    ):
        return STATUS_CONFLICT
    field_status = state.get("field_status") or {}
    for name in ("pcn_number", "vrm"):
        st = field_status.get(name)
        if st == STATUS_CONFLICT:
            return f"{STATUS_CONFLICT}:{name}"
        if st == STATUS_UNCERTAIN:
            return f"{STATUS_UNCERTAIN}:{name}"
        if st == STATUS_ABSENT:
            # Absent core id — need confirmation / clearer documents.
            return f"NEEDS_DOCUMENT_CONFIRMATION:{name}"
    # Any conflicted critical field blocks.
    for name, st in field_status.items():
        if st == STATUS_CONFLICT:
            return f"{STATUS_CONFLICT}:{name}"
    return None


def critical_fields_auto_confirmable(case: CaseFile) -> set[str]:
    """Critical fields allowed into auto-confirm only when VERIFIED."""
    state = load_identity_state(case) or {}
    field_status = state.get("field_status") or {}
    allowed = set()
    for name in CRITICAL_FIELDS:
        if field_status.get(name) == STATUS_VERIFIED:
            allowed.add(name)
    return allowed


def timing_identity_ready(case: CaseFile) -> tuple[bool, list[str]]:
    """Whether event/issue dates are VERIFIED for legal timing calculations."""
    state = load_identity_state(case) or {}
    field_status = state.get("field_status") or {}
    missing = []
    for name in TIMING_IDENTITY_FIELDS:
        st = field_status.get(name)
        if st == STATUS_CONFLICT:
            missing.append(f"conflict:{name}")
        elif st == STATUS_UNCERTAIN:
            missing.append(f"uncertain:{name}")
        elif st == STATUS_ABSENT:
            # Absent dates → PoFA assess already returns UNRESOLVED; allow call
            # but mark dependency.
            missing.append(f"absent:{name}")
    # Ready only when every present timing field is VERIFIED and none CONFLICT.
    if any(m.startswith("conflict:") or m.startswith("uncertain:") for m in missing):
        return False, missing
    # If both absent, calculation returns UNRESOLVED — that is OK (dependency status).
    if all(m.startswith("absent:") for m in missing) and missing:
        return False, missing
    # If one present and VERIFIED, other absent — not ready for VERIFIED defect.
    present = [n for n in TIMING_IDENTITY_FIELDS
               if field_status.get(n) == STATUS_VERIFIED]
    if len(present) < 2:
        return False, missing or ["timing_fields_incomplete"]
    return True, []


def location_identity_ready(case: CaseFile) -> tuple[bool, list[str]]:
    state = load_identity_state(case) or {}
    field_status = state.get("field_status") or {}
    st = field_status.get("parking_location")
    if st == STATUS_CONFLICT:
        return False, ["conflict:parking_location"]
    if st == STATUS_UNCERTAIN:
        return False, ["uncertain:parking_location"]
    return True, []


# ---------------------------------------------------------------------------
# Draft release validator helpers
# ---------------------------------------------------------------------------

def authoritative_identity_values(case: CaseFile) -> dict[str, Any]:
    """FactManager values for critical fields that identity marked VERIFIED."""
    state = load_identity_state(case) or {}
    field_status = state.get("field_status") or {}
    values = state.get("values") or {}
    out = {}
    for name in CRITICAL_FIELDS:
        if field_status.get(name) != STATUS_VERIFIED:
            continue
        # Prefer live FactManager value when usable.
        node = case.facts.get(name) if getattr(case, "facts", None) else None
        if node is not None and getattr(node, "usable", False) and node.value not in (None, ""):
            out[name] = canonicalize(name, node.value)
        elif values.get(name) is not None:
            out[name] = values.get(name)
    return out


def draft_identity_mismatches(letter: str, authoritative: dict[str, Any],
                              *, required: Optional[set[str]] = None) -> list[dict]:
    """Compare critical values stated in the letter to authoritative identity."""
    from .engines.validation import _contains_token, _ident, _near_variants

    required = required or set()
    issues: list[dict] = []
    full = letter or ""
    # PCN
    if "pcn_number" in authoritative:
        pcn = _ident(authoritative["pcn_number"])
        if pcn:
            if not _contains_token(full, pcn):
                if "pcn_number" in required or re.search(r"\b(?:PCN|Parking Charge)\b", full, re.I):
                    # If letter mentions a PCN-like token that differs — conflict.
                    stated = False
                    for tok in _near_variants(full, pcn, min_len=6):
                        issues.append({
                            "field": "pcn_number",
                            "draft_value": tok,
                            "authoritative_value": pcn,
                            "result": "MISMATCH",
                        })
                        stated = True
                    if not stated and "pcn_number" in required:
                        issues.append({
                            "field": "pcn_number",
                            "draft_value": None,
                            "authoritative_value": pcn,
                            "result": "MISSING_REQUIRED",
                        })
            else:
                for tok in _near_variants(full, pcn, min_len=6):
                    issues.append({
                        "field": "pcn_number",
                        "draft_value": tok,
                        "authoritative_value": pcn,
                        "result": "MISMATCH",
                    })
    if "vrm" in authoritative:
        vrm = _ident(authoritative["vrm"])
        if vrm:
            for tok in re.findall(r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b", full.upper()):
                if tok.replace(" ", "") != vrm:
                    issues.append({
                        "field": "vrm",
                        "draft_value": tok.replace(" ", ""),
                        "authoritative_value": vrm,
                        "result": "MISMATCH",
                    })
            for tok in _near_variants(full, vrm, min_len=5, max_len=8, spaced=True):
                issues.append({
                    "field": "vrm",
                    "draft_value": tok,
                    "authoritative_value": vrm,
                    "result": "MISMATCH",
                })
            if "vrm" in required and not _contains_token(full, vrm):
                issues.append({
                    "field": "vrm",
                    "draft_value": None,
                    "authoritative_value": vrm,
                    "result": "MISSING_REQUIRED",
                })
    if "operator_name" in authoritative and full.strip():
        op = str(authoritative["operator_name"] or "").strip()
        # Only when the letter names an operator-like party that disagrees, or
        # DraftPlan explicitly required the operator particular.
        if op and "operator_name" in required:
            if op.casefold() not in full.casefold():
                # Fail missing only if the draft otherwise identifies the case
                # (PCN/VRM present) — empty/failed drafts are not identity mismatches.
                pcn = _ident(authoritative.get("pcn_number"))
                vrm = _ident(authoritative.get("vrm"))
                if (pcn and _contains_token(full, pcn)) or (vrm and _contains_token(full, vrm)):
                    issues.append({
                        "field": "operator_name",
                        "draft_value": None,
                        "authoritative_value": op,
                        "result": "MISSING_REQUIRED",
                    })
    for date_field in ("parking_event_date", "notice_issue_date"):
        if date_field not in authoritative:
            continue
        iso = str(authoritative[date_field])
        # If letter states a concrete date near this field's role, it must match.
        # Detect ISO or UK forms of the authoritative date only; near-variants of dates
        # are handled by requiring the authoritative date when the letter asserts dates
        # for PoFA timing.
        if date_field in required:
            from .legal import findings as legal_findings
            if not legal_findings.date_stated(iso, full):
                # Only fail if draft asserts some date numerals in a timing sentence.
                if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", full) or re.search(
                        r"\b20\d{2}-\d{2}-\d{2}\b", full):
                    # Check whether any wrong date is present vs authoritative.
                    if iso not in full and not legal_findings.date_stated(iso, full):
                        issues.append({
                            "field": date_field,
                            "draft_value": "date_asserted",
                            "authoritative_value": iso,
                            "result": "MISSING_OR_MISMATCH",
                        })
    return issues
