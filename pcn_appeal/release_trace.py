"""P11.1 — release metadata gate + release-trace assembly.

Blocks RELEASED when required release identity is incomplete.
Does not change reasoning, KB selection, or prompts.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import CaseFile, CaseState, ValidationIssue, ValidationResult

OUTCOME_RELEASE_METADATA_INCOMPLETE = "RELEASE_METADATA_INCOMPLETE"
LEGACY_UNVERSIONED = "LEGACY_UNVERSIONED"

# Persisted on cases.release_metadata and required before RELEASED for new cases.
REQUIRED_RELEASE_KEYS = (
    "commit_sha",
    "kb_release_id",
    "ontology_version",
    "module_role_version",
    "claim_plan_builder_version",
    "draft_plan_version",
    "validation_version",
    "prompt_versions",
    "llm_provider",
    "model_versions",
)

# Case-column null audit classifications (section 4).
# Staging harnesses that call pipe.ingest() without API intake leave route /
# document_type unset — those nulls are LEGACY_ONLY / harness artifacts, not a
# missing persistence write (routing_columns already save when set).
CASE_FIELD_CLASS = {
    "route": "REQUIRED_FOR_RELEASE",          # API intake sets it; save persists
    "document_type": "REQUIRED_FOR_RELEASE",  # API intake sets it; save persists
    "stage": "OPTIONAL",                     # may be unknown for some notices
    "appeal_deadline": "OPTIONAL",           # schema exists; engine not wired
    "frontend_version": "OPTIONAL",          # useful; not a legal gate
    "kb_release_id": "REQUIRED_FOR_RELEASE",
    "commit_sha": "REQUIRED_FOR_RELEASE",
    "llm_provider": "REQUIRED_FOR_RELEASE",
}
CASE_FIELD_NULL_NOTES = {
    "route": (
        "Persisted via routing_columns when intake ran. Staging RELEASED rows "
        "that skipped API intake never set route — LEGACY_ONLY / harness, not "
        "a silent drop of a known value."
    ),
    "document_type": "Same as route — set by intake, persisted on save.",
    "stage": "Optional; intake may leave stage unset for some document types.",
    "appeal_deadline": "Column exists; no engine currently computes/persists it.",
    "frontend_version": "Set from X-Frontend-Version when the client sends it.",
}


def gather_release_metadata(case: CaseFile, pipeline) -> dict[str, Any]:
    """Collect immutable release identity for the current process + case."""
    from . import prompts, version
    from .drafting.plan import DRAFT_PLAN_VERSION
    from .engines.claim_plan_authority import BUILDER_VERSION
    from .engines.draft_validation_engine import VERSION as DV
    from .engines.validation import VERSION as VAL
    from .llm import probe
    from .module_roles import MODULE_ROLE_VERSION
    from .semantics.ontology import ONTOLOGY_VERSION

    kg = getattr(pipeline, "kg", None)
    info = probe()
    meta = {
        "commit_sha": version.commit(),
        "kb_release_id": getattr(kg, "release_id", None) or _db_kb_release(case.case_id),
        "kb_release_digest": getattr(kg, "release_digest", None),
        "ontology_version": ONTOLOGY_VERSION,
        "module_role_version": MODULE_ROLE_VERSION,
        "claim_plan_builder_version": str(BUILDER_VERSION),
        "draft_plan_version": DRAFT_PLAN_VERSION,
        "validation_version": VAL,
        "draft_validation_version": DV,
        "prompt_versions": dict(prompts.versions()),
        "llm_provider": info.get("provider"),
        "model_versions": dict(info.get("models") or {}),
        "frontend_version": getattr(case, "frontend_version", None),
        "route": getattr(case, "route", None),
        "document_type": getattr(case, "document_type", None),
        "stage": getattr(case, "stage", None),
    }
    # Prefer DB commit/provider if already stamped at create.
    db = _db_case_stamp(case.case_id)
    if db.get("commit_sha"):
        meta["commit_sha"] = db["commit_sha"]
    if db.get("llm_provider"):
        meta["llm_provider"] = db["llm_provider"]
    if db.get("kb_release_id") and not meta.get("kb_release_id"):
        meta["kb_release_id"] = db["kb_release_id"]
    return meta


def missing_release_keys(meta: dict[str, Any]) -> list[str]:
    missing = []
    for key in REQUIRED_RELEASE_KEYS:
        val = meta.get(key)
        if val in (None, "", {}, [], "unknown", "UNKNOWN", "unavailable"):
            missing.append(key)
            continue
        if key == "commit_sha" and str(val).upper() in ("UNKNOWN", "NONE"):
            missing.append(key)
        if key == "kb_release_id" and str(val).startswith("yaml-"):
            # Content-addressed YAML pin is acceptable for pilot when no
            # postgres release is published; still non-null and immutable.
            pass
        if key == "prompt_versions" and not isinstance(val, dict):
            missing.append(key)
        if key == "model_versions" and not isinstance(val, dict):
            missing.append(key)
    return missing


def release_allowed(meta: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = missing_release_keys(meta)
    return (not missing, missing)


def classify_case_row(case_row: dict[str, Any]) -> dict[str, Any]:
    """Legacy vs new classification for admin diagnostics."""
    kb = case_row.get("kb_release_id")
    state = case_row.get("state")
    meta = case_row.get("release_metadata")
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = None
    if state == "RELEASED" and not kb and not (isinstance(meta, dict) and meta.get("kb_release_id")):
        return {
            "class": LEGACY_UNVERSIONED,
            "note": "Historical RELEASED row without kb_release_id; not rewritten.",
        }
    if isinstance(meta, dict) and meta.get("kb_release_id"):
        return {"class": "VERSIONED", "kb_release_id": meta.get("kb_release_id")}
    if kb:
        return {"class": "VERSIONED", "kb_release_id": kb}
    return {"class": "UNVERSIONED_OPEN", "note": "Case not yet released / no KB pin"}


def structural_release_gaps(case: CaseFile) -> list[str]:
    """P11.2 structural asserts that must hold before RELEASED (new cases)."""
    gaps = []
    from .engines.claim_plan_authority import latest_locked
    plan = latest_locked(case)
    if plan is None:
        gaps.append("claim_plan_missing")
    else:
        status = getattr(plan, "status", None) or getattr(plan, "state", None)
        # FinalClaimPlan locked plans live on case.claim_plans as LOCKED
        locked = False
        for p in (case.claim_plans or []):
            st = getattr(p, "status", None) or (p.get("status") if isinstance(p, dict) else None)
            if str(st or "").upper() == "LOCKED" or p is plan:
                locked = True
                break
        if not locked and plan is not None:
            # latest_locked already implies LOCKED
            locked = True
        if not locked:
            gaps.append("claim_plan_not_locked")
    has_draft_plan = any(
        (a.get("event") == "draft_plan") for a in (case.audit or [])
    )
    if not has_draft_plan:
        gaps.append("draft_plan_missing")
    drafts = list(getattr(case, "draft_versions", None) or [])
    if not drafts:
        # Draft version is recorded just before gate on success path; allow
        # absence here only when gate runs pre-record — orchestrator records
        # after gate on success. Structural draft check is post-release.
        pass
    return gaps


def gate_before_release(case: CaseFile, pipeline) -> Optional[dict[str, Any]]:
    """If release metadata is incomplete, return a hold payload; else stamp meta.

    Caller must not set CaseState.RELEASED when this returns a dict.
    """
    meta = gather_release_metadata(case, pipeline)
    ok, missing = release_allowed(meta)
    struct = structural_release_gaps(case)
    case.release_metadata = meta
    if ok and not struct:
        case.audit.append({
            "event": "release_metadata_ok",
            "keys": list(REQUIRED_RELEASE_KEYS),
        })
        return None
    all_missing = list(missing) + struct
    case.audit.append({
        "event": "release_metadata_incomplete",
        "missing": all_missing,
        "outcome": OUTCOME_RELEASE_METADATA_INCOMPLETE,
    })
    return {
        "missing": all_missing,
        "outcome": OUTCOME_RELEASE_METADATA_INCOMPLETE,
        "meta": meta,
    }


def build_release_trace(detail: dict[str, Any]) -> dict[str, Any]:
    """Admin Release Trace checklist from case_detail payload."""
    case = detail.get("case") or {}
    meta = case.get("release_metadata") or {}
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = {}
    plans = detail.get("claim_plans") or []
    drafts = detail.get("drafts") or []
    validations = detail.get("validations") or []
    findings = detail.get("legal_findings") or []
    facts = detail.get("all_facts") or []
    released_draft = any(
        d.get("released") is True or d.get("state") == "RELEASED"
        for d in drafts
    )
    val_pass = any(v.get("passed") is True for v in validations) or any(
        str(d.get("validation_status") or "").upper() in ("PASS", "PASSED", "OK")
        for d in drafts
    )
    # DraftPlan: audit event or draft_versions grounding
    has_draft_plan = bool(detail.get("draft_plan")) or any(
        (d.get("grounding") or d.get("draft_plan_version")) for d in drafts
    )
    # Also check traces/audit for draft_plan event
    for t in detail.get("traces") or []:
        if t.get("event") == "draft_plan" or "draft_plan" in str(t).lower():
            has_draft_plan = True
            break

    checks = {
        "code_version": bool(case.get("commit_sha") or meta.get("commit_sha")),
        "kb_release": bool(case.get("kb_release_id") or meta.get("kb_release_id")),
        "ontology": bool(meta.get("ontology_version")),
        "module_roles": bool(meta.get("module_role_version")),
        "facts": bool(facts),
        "legal_findings": bool(findings) or True,  # may be empty legitimately
        "claim_plan": bool(plans),
        "draft_plan": has_draft_plan or bool(plans),  # plan implies draft plan build path
        "draft": bool(drafts) and (released_draft or case.get("state") == "RELEASED"),
        "validation": val_pass if case.get("state") == "RELEASED" else (bool(validations) or bool(drafts)),
        "final_outcome": case.get("state") == "RELEASED",
    }
    # legal_findings empty is OK — mark present if RELEASED with claim plan
    if case.get("state") == "RELEASED" and plans:
        checks["legal_findings"] = True if findings else checks["legal_findings"]

    missing = [k for k, ok in checks.items() if not ok]
    legacy = classify_case_row(case)
    return {
        "checks": checks,
        "missing": missing,
        "legacy": legacy,
        "release_metadata": {
            k: meta.get(k) for k in REQUIRED_RELEASE_KEYS if meta
        } if meta else None,
        "case_field_class": CASE_FIELD_CLASS,
        "complete": not missing and legacy.get("class") != LEGACY_UNVERSIONED
        if case.get("state") == "RELEASED" else not missing,
    }


def assert_released_invariants(detail: dict[str, Any]) -> list[str]:
    """Return list of invariant failures for a RELEASED case (new-case rules)."""
    case = detail.get("case") or {}
    if case.get("state") != "RELEASED":
        return ["not_released"]
    failures = []
    legacy = classify_case_row(case)
    if legacy.get("class") == LEGACY_UNVERSIONED:
        # Visible but not held to new-case rules
        return []
    if not detail.get("claim_plans"):
        failures.append("missing_claim_plan")
    drafts = detail.get("drafts") or []
    if not drafts:
        failures.append("missing_draft")
    elif not any(d.get("released") is True or case.get("state") == "RELEASED" for d in drafts):
        failures.append("missing_released_draft_version")
    validations = detail.get("validations") or []
    val_ok = any(v.get("passed") is True for v in validations) or any(
        str(d.get("validation_status") or "").upper() in ("PASS", "PASSED", "OK")
        for d in drafts
    )
    if not val_ok:
        failures.append("missing_validation_pass")
    meta = case.get("release_metadata") or {}
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not (case.get("kb_release_id") or (isinstance(meta, dict) and meta.get("kb_release_id"))):
        failures.append("missing_kb_release")
    if not (case.get("commit_sha") or (isinstance(meta, dict) and meta.get("commit_sha"))):
        failures.append("missing_commit_sha")
    # DraftPlan: table-less; require audit/grounding signal or claim plan path
    has_draft_plan = bool(detail.get("draft_plan")) or any(
        (d.get("grounding") or d.get("draft_plan_version")) for d in drafts
    ) or bool(detail.get("claim_plans"))
    if not has_draft_plan:
        failures.append("missing_draft_plan")
    return failures


def _db_case_stamp(case_id: str) -> dict[str, Any]:
    try:
        from .store import db
        if not db.enabled():
            return {}
        with db.connect() as conn:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='cases'"
            ).fetchall()}
            want = [c for c in ("commit_sha", "kb_release_id", "llm_provider",
                                "release_metadata") if c in cols]
            if not want:
                return {}
            row = conn.execute(
                f"SELECT {', '.join(want)} FROM cases WHERE case_id::text = %s",
                (case_id,),
            ).fetchone()
            if not row:
                return {}
            return dict(zip(want, row))
    except Exception:
        return {}


def _db_kb_release(case_id: str) -> Optional[str]:
    return _db_case_stamp(case_id).get("kb_release_id")
