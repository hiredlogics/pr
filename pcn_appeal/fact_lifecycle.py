"""P8.4: versioned fact lifecycle. Material facts are never hard-deleted.

Reassessment applies a delta (existing ∪ new − explicitly superseded). The
held graph still has one active value per name; every generation is kept on
`case.fact_versions` so a correction supersedes, it does not erase.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .fact_graph import same_value
from .models import CaseFile, Fact, FactStatus, SourceKind

EXTRACTOR_VERSION = "v2"

ACTIVE, SUPERSEDED, RETRACTED, CONFLICTED = (
    "ACTIVE", "SUPERSEDED", "RETRACTED", "CONFLICTED")
LIFECYCLES = (ACTIVE, SUPERSEDED, RETRACTED, CONFLICTED)

# Recomputed every pass; retracting them does not drop customer meaning.
_RECOMPUTE = frozenset({
    "account_contradicts_allegation",
    "material_account_propositions",
    "material_account_proposition",
    "material_account_points",
    "material_account_summary",
})

UNSUPPORTED_DERIVATION = "UNSUPPORTED_DERIVATION"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class FactRecord:
    fact_id: str
    name: str
    value: Any
    status: str
    lifecycle: str
    provenance: dict
    source: dict
    created_at: str
    created_by: str
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    confidence: float = 1.0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "name": self.name, "value": self.value,
            "status": self.status, "lifecycle": self.lifecycle,
            "provenance": dict(self.provenance), "source": dict(self.source),
            "created_at": self.created_at, "created_by": self.created_by,
            "supersedes": self.supersedes, "superseded_by": self.superseded_by,
            "confidence": self.confidence, "reason": self.reason,
        }


def _versions(case: CaseFile) -> list:
    store = getattr(case, "fact_versions", None)
    if store is None:
        store = []
        case.fact_versions = store
    return store


def _provenance(fact: Fact, *, answer_id: str = "") -> dict:
    excerpt = fact.source.excerpt or ""
    return {
        "source": fact.source.kind.value,
        "answer_id": answer_id or fact.source.ref or "",
        "text_span": excerpt[:240],
        "extractor_version": EXTRACTOR_VERSION,
        "timestamp": _now(),
    }


def note_version(case: CaseFile, fact: Fact, *, lifecycle: str, reason: str = "",
                 created_by: str = "system", supersedes: Optional[str] = None) -> dict:
    """Append one generation. Earlier rows are not edited."""
    node = case.facts.node_id(fact.name) or fact.fact_id
    rec = FactRecord(
        fact_id=fact.fact_id or node, name=fact.name, value=fact.value,
        status=fact.status.value, lifecycle=lifecycle,
        provenance=_provenance(fact),
        source={"kind": fact.source.kind.value, "ref": fact.source.ref},
        created_at=_now(), created_by=created_by, supersedes=supersedes,
        confidence=float(fact.confidence or 1.0), reason=reason,
    )
    row = rec.as_dict()
    _versions(case).append(row)
    return row


def latest_record(case: CaseFile, name: str) -> Optional[dict]:
    rows = [v for v in _versions(case) if v.get("name") == name]
    return rows[-1] if rows else None


def generations(case: CaseFile, name: Optional[str] = None) -> list[dict]:
    rows = list(_versions(case))
    if name:
        rows = [v for v in rows if v.get("name") == name]
    return rows


def apply_fact_delta(case: CaseFile, intended: dict[str, Any]) -> dict:
    """Differential update: keep same-value facts, supersede corrections,
    retract only material free-text that this pass no longer asserts and that
    no derived fact still needs.
    """
    kept, superseded, retracted = [], [], []
    dependents = _derived_source_names(case)
    for name in list(case.facts):
        fact = case.facts[name]
        if name in _RECOMPUTE:
            case.retract(name, reason="account_reassessed")
            retracted.append(name)
            continue
        if not _material_free_text(fact, name):
            continue
        if name in intended and same_value(fact.value, intended[name]):
            kept.append(name)
            continue
        if name in intended:
            note_version(case, fact, lifecycle=SUPERSEDED, reason="customer correction",
                         created_by="account.apply_fact_delta")
            superseded.append(name)
            continue
        if name in dependents:
            kept.append(name)
            continue
        note_version(case, fact, lifecycle=RETRACTED, reason="account_reassessed",
                     created_by="account.apply_fact_delta")
        case.retract(name, reason="account_reassessed")
        retracted.append(name)
    return {"kept": kept, "superseded": superseded, "retracted": retracted}


def protect_derived_facts(case: CaseFile) -> list[dict]:
    """A derived fact cannot stand without its sources. Restore from the
    retracted node when possible; otherwise mark UNSUPPORTED_DERIVATION."""
    from .case_state import master
    reports = []
    for name, row in master(case).derived_facts.items():
        missing = [s for s in (row.get("source_facts") or []) if not case.has(s)]
        if not missing:
            continue
        restored = []
        for src in missing:
            old = case.facts.last_value(src)
            if old is None:
                continue
            case.put(old, reason="restore_from_provenance")
            if case.has(src):
                restored.append(src)
        still = [s for s in missing if s not in restored]
        report = {"fact": name, "missing": still, "restored": restored,
                  "status": "RESTORED" if not still else UNSUPPORTED_DERIVATION}
        reports.append(report)
        if still:
            case.audit.append({"event": "unsupported_derivation", **report})
    return reports


def _material_free_text(fact, name: str) -> bool:
    ref = fact.source.ref or ""
    if fact.source.kind == SourceKind.CUSTOMER_FREE_TEXT and ref.startswith("free_text:"):
        return True
    if name.startswith("bay_") and name.endswith(
            ("_accounted", "_condition_accounted", "_occupant_accounted")) \
            and fact.source.kind in (SourceKind.CUSTOMER_FREE_TEXT, SourceKind.CALCULATION):
        return "material_account" in ref or ref.startswith("free_text:")
    return False


def _derived_source_names(case: CaseFile) -> set[str]:
    from .case_state import master
    names: set[str] = set()
    for row in master(case).derived_facts.values():
        names.update(row.get("source_facts") or [])
    # Narrative atoms that license multiple_visits even before a DERIVED write.
    if case.get("multiple_visits") is True:
        names.update(("left_site", "returned_same_day", "visited_premises"))
    return names


def persist_versions(case: CaseFile) -> None:
    """Persist generations on the audit trail. Underscore raw_answers keys
    are working values and must not look like customer questions."""
    versions = list(_versions(case))
    provenance = list(getattr(case, "free_text_provenance", None) or [])
    if not versions and not provenance:
        return
    for a in reversed(case.audit or []):
        if a.get("event") in ("material_account", "fact_lifecycle") and not a.get("_persisted"):
            a["fact_versions"] = versions
            if provenance:
                a.setdefault("provenance", provenance)
            return
    for a in reversed(case.audit or []):
        if a.get("event") in ("material_account", "fact_lifecycle"):
            if a.get("fact_versions") == versions:
                return
            break
    case.audit.append({
        "event": "fact_lifecycle",
        "fact_versions": versions,
        "provenance": provenance,
    })


def restore_versions(case: CaseFile, raw=None) -> None:
    """Restore generations from an explicit blob, then audit, then history."""
    import json
    blob = raw if raw is not None else (case.raw_answers or {}).get("_fact_versions")
    if blob:
        try:
            loaded = json.loads(blob) if isinstance(blob, str) else blob
        except (TypeError, ValueError):
            loaded = None
        if isinstance(loaded, list) and loaded:
            case.fact_versions = list(loaded)
    restore_lineage(case)


def restore_lineage(case: CaseFile) -> None:
    """Reload provenance and versions without using underscore answers."""
    for a in reversed(case.audit or []):
        if a.get("event") not in ("material_account", "fact_lifecycle"):
            continue
        if not getattr(case, "free_text_provenance", None) and a.get("provenance"):
            case.free_text_provenance = list(a["provenance"])
        if not _versions(case) and a.get("fact_versions"):
            case.fact_versions = list(a["fact_versions"])
        if case.free_text_provenance and _versions(case):
            break
    if not _versions(case):
        _rebuild_from_history(case)


def _rebuild_from_history(case: CaseFile) -> None:
    """Fallback: one generation per applied / retracted history row."""
    store = _versions(case)
    last: dict[str, dict] = {}
    for h in case.fact_history or []:
        outcome = h.get("outcome")
        if outcome in ("IGNORED", "IGNORED_DUPLICATE"):
            continue
        name = h.get("fact")
        lifecycle = RETRACTED if outcome == "RETRACTED" else ACTIVE
        prev = last.get(name)
        if prev is not None and prev.get("lifecycle") == ACTIVE and lifecycle == ACTIVE:
            prev["lifecycle"] = SUPERSEDED
            prev["reason"] = h.get("reason") or prev.get("reason") or "value_changed"
            prev["superseded_by"] = h.get("fact_id")
        row = {
            "fact_id": h.get("fact_id") or "",
            "name": name,
            "value": h.get("new"),
            "status": h.get("status"),
            "lifecycle": lifecycle,
            "provenance": {
                "source": h.get("source_kind") or "",
                "answer_id": h.get("source_ref") or "",
                "text_span": "",
                "extractor_version": EXTRACTOR_VERSION,
                "timestamp": h.get("at") or "",
            },
            "source": {"kind": h.get("source_kind"), "ref": h.get("source_ref")},
            "created_at": h.get("at") or "",
            "created_by": h.get("changed_by") or "system",
            "supersedes": prev.get("fact_id") if prev else None,
            "superseded_by": None,
            "confidence": 1.0,
            "reason": h.get("reason") or "",
        }
        store.append(row)
        last[name] = row


def _derived_users(case: CaseFile) -> dict[str, list[str]]:
    from .case_state import master
    users: dict[str, list[str]] = {}
    for name, row in master(case).derived_facts.items():
        for src in row.get("source_facts") or []:
            users.setdefault(src, []).append(name)
    if case.get("multiple_visits") is True:
        for src in ("left_site", "returned_same_day", "visited_premises"):
            bucket = users.setdefault(src, [])
            if "multiple_visits" not in bucket:
                bucket.append("multiple_visits")
    return users


def _authority_from_history(row: dict) -> str:
    from .fact_graph import (
        CUSTOMER_ASSERTED, CUSTOMER_CONFIRMED, DOCUMENT_CONFIRMED,
        INFERRED, VERIFIED_FINDING_AUTH,
    )
    kind = row.get("source_kind") or (row.get("source") or {}).get("kind") or ""
    status = row.get("status") or ""
    if kind == "DOCUMENT":
        return DOCUMENT_CONFIRMED
    if kind == "CALCULATION" and status == "DERIVED":
        ref = (row.get("source_ref") or "").lower()
        if any(tok in ref for tok in ("pofa", "legal.", "findings", "verified")):
            return VERIFIED_FINDING_AUTH
        return INFERRED
    if status in ("CONFIRMED", "CORRECTED"):
        return CUSTOMER_CONFIRMED
    if kind in ("ANSWER", "CUSTOMER_FREE_TEXT"):
        return CUSTOMER_ASSERTED
    return INFERRED


def fact_authority_issues(case: CaseFile) -> list[dict]:
    """VAL-FACT-AUTHORITY: no lower-authority APPLIED overwrite."""
    from .fact_graph import authority_rank
    last: dict[str, dict] = {}
    issues = []
    for h in case.fact_history or []:
        name = h.get("fact")
        prev = last.get(name)
        if h.get("outcome") == "APPLIED" and prev is not None:
            incoming = _authority_from_history(h)
            held = _authority_from_history(prev)
            reason = (h.get("reason") or "").lower()
            if authority_rank(incoming) < authority_rank(held) \
                    and "correction" not in reason and "conflict_resolved" not in reason:
                issues.append({
                    "rule": "VAL-FACT-AUTHORITY", "status": "FAIL",
                    "fact": name,
                    "message": f"{name}: {incoming} overwrote {held}",
                })
        if h.get("outcome") in ("APPLIED", "CONFLICT"):
            last[name] = h
    return issues


def fact_stability_issues(case: CaseFile) -> list[dict]:
    """VAL-FACT-STABILITY: same value must not produce a new APPLIED rewrite."""
    from .fact_graph import same_value
    last: dict[str, dict] = {}
    issues = []
    for h in case.fact_history or []:
        name = h.get("fact")
        prev = last.get(name)
        if prev is not None and h.get("outcome") == "APPLIED" \
                and same_value(prev.get("new"), h.get("new")) \
                and prev.get("status") == h.get("status") \
                and prev.get("source_kind") != h.get("source_kind"):
            issues.append({
                "rule": "VAL-FACT-STABILITY", "status": "FAIL",
                "fact": name,
                "message": f"{name}: same value rewritten ({prev.get('source_kind')} "
                           f"→ {h.get('source_kind')})",
            })
        if h.get("outcome") in ("APPLIED", "IGNORED_DUPLICATE"):
            last.setdefault(name, h)
            if h.get("outcome") == "APPLIED":
                last[name] = h
    return issues


def derived_consistency_issues(case: CaseFile) -> list[dict]:
    """VAL-DERIVED-CONSISTENCY: derived facts match current source facts."""
    from .case_state import master
    issues = []
    for name, row in master(case).derived_facts.items():
        missing = [s for s in (row.get("source_facts") or []) if not case.has(s)]
        if missing and row.get("lineage_status") == "COMPLETE" \
                and not row.get("source_evidence"):
            issues.append({
                "rule": "VAL-DERIVED-CONSISTENCY", "status": "FAIL",
                "fact": name, "message": f"{name} missing sources {missing}",
            })
        gens = [g for g in generations(case, name) if g.get("lifecycle") == ACTIVE]
        if len(gens) > 1:
            values = {str(g.get("value")) for g in gens}
            if len(values) == 1:
                issues.append({
                    "rule": "VAL-DERIVED-CONSISTENCY", "status": "FAIL",
                    "fact": name,
                    "message": f"{name} regenerated without a source change",
                })
    return issues


def fact_write_trace(case: CaseFile) -> list[dict]:
    """FACT WRITE TRACE: first write, later writes, decision, reason."""
    from .fact_graph import fact_authority
    by_name: dict[str, list] = {}
    for h in case.fact_history or []:
        by_name.setdefault(h.get("fact"), []).append(h)
    for a in case.audit or []:
        if a.get("event") == "fact_ignored_duplicate":
            by_name.setdefault(a.get("name"), [])
    out = []
    for name, rows in by_name.items():
        first = next((h for h in rows if h.get("outcome") == "APPLIED"),
                     rows[0] if rows else None)
        later = [h for h in rows[1:]] if rows else []
        held = case.facts.get(name)
        out.append({
            "fact": name,
            "value": held.value if held is not None else (first or {}).get("new"),
            "first_write": {
                "authority": _authority_from_history(first) if first else None,
                "source": (first or {}).get("source_kind"),
                "status": (first or {}).get("status"),
                "at": (first or {}).get("at"),
            } if first else None,
            "writes": [{
                "authority": _authority_from_history(h),
                "source": h.get("source_kind"),
                "decision": h.get("outcome"),
                "reason": h.get("reason"),
                "at": h.get("at"),
            } for h in later],
            "held_authority": fact_authority(held) if held is not None else None,
            "provenance_count": len([
                s for s in (case.fact_sources or []) if s.get("fact") == name
            ]),
        })
    return out


def lifecycle_trace(case: CaseFile) -> list[dict]:
    """FACT CREATED → USED → UPDATED → SUPERSEDED for the admin trace."""
    users = _derived_users(case)
    out = []
    seen: dict[str, int] = {}
    for v in _versions(case):
        name = v.get("name")
        n = seen.get(name, 0)
        seen[name] = n + 1
        if v.get("lifecycle") == SUPERSEDED:
            event = "FACT SUPERSEDED"
        elif v.get("lifecycle") == RETRACTED:
            event = "FACT SUPERSEDED"
        elif n == 0:
            event = "FACT CREATED"
        else:
            event = "FACT UPDATED"
        used_by = list(users.get(name) or [])
        row = {
            "event": event, "name": name, "value": v.get("value"),
            "reason": v.get("reason"), "at": v.get("created_at"),
            "fact_id": v.get("fact_id"), "supersedes": v.get("supersedes"),
            "used_by": used_by,
        }
        if used_by:
            row["event_used"] = "FACT USED"
        out.append(row)
    return out


__all__ = [
    "FactRecord", "EXTRACTOR_VERSION", "ACTIVE", "SUPERSEDED", "RETRACTED",
    "CONFLICTED", "UNSUPPORTED_DERIVATION", "apply_fact_delta",
    "protect_derived_facts", "note_version", "generations", "latest_record",
    "persist_versions", "restore_versions", "restore_lineage", "lifecycle_trace",
    "fact_write_trace", "fact_authority_issues", "fact_stability_issues",
    "derived_consistency_issues",
]
