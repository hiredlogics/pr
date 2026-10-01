"""Customer-facing hold outcomes — distinct from internal MANUAL_REVIEW.

MANUAL_REVIEW is a pipeline state. The customer message must name *why* we
stopped, not treat every hold as "your case has no merit".
"""
from __future__ import annotations

from typing import Any, Optional


# Stable codes the API/UI map. Do not reuse as legal grounds.
OUTCOME_NO_SUPPORTED_GROUNDS = "NO_SUPPORTED_GROUNDS"
OUTCOME_PROCESSING_ERROR = "PROCESSING_ERROR"
OUTCOME_NEEDS_DOCUMENTS = "NEEDS_DOCUMENTS"
OUTCOME_NEEDS_FACTS = "NEEDS_FACTS"
OUTCOME_SCOPE = "SCOPE_INELIGIBLE"
OUTCOME_CLASSIFICATION = "CLASSIFICATION_FAILED"

CUSTOMER_COPY = {
    OUTCOME_NO_SUPPORTED_GROUNDS: {
        "title": "We could not write an appeal we can stand behind",
        "lede": (
            "From the notice and the answers you gave, we did not find a "
            "supported ground we are willing to put in a letter. That is our "
            "assessment of what we can argue — not a ruling that you have no "
            "case. You can add more detail to this same case, or appeal to the "
            "operator in your own words using the method on your notice."
        ),
        "next": (
            "You can still appeal to the operator using the method on your "
            "notice. If they reject it, you can take it to their appeals service."
        ),
        "cta": "Add more detail to this case",
        "can_continue": True,
    },
    OUTCOME_PROCESSING_ERROR: {
        "title": "Something went wrong while preparing your appeal",
        "lede": (
            "A processing error stopped us finishing the letter. This is not a "
            "judgment on the strength of your case. Your answers are saved — "
            "you can continue this case without starting again."
        ),
        "next": "Try continuing this case. If it keeps failing, contact support with your case reference.",
        "cta": "Continue this case",
        "can_continue": True,
    },
    OUTCOME_NEEDS_DOCUMENTS: {
        "title": "We need clearer documents",
        "lede": (
            "Parts of the notice we need are missing or unreadable. Upload the "
            "missing pages or a clearer photo to this same case."
        ),
        "next": "Add the other side of the notice or a clearer scan, then continue.",
        "cta": "Upload more documents",
        "can_continue": True,
    },
    OUTCOME_NEEDS_FACTS: {
        "title": "A few more details are needed",
        "lede": (
            "The appeal is not ready yet. Answer the remaining questions so a "
            "case-specific letter can be prepared."
        ),
        "next": "Answer the questions shown, then continue.",
        "cta": "Continue answering",
        "can_continue": True,
    },
    OUTCOME_SCOPE: {
        "title": "We cannot generate an appeal for this",
        "lede": (
            "This document is outside the stage or service we handle here. "
            "Use the route recommended for this kind of notice."
        ),
        "next": "Follow the recommendation shown for the correct service.",
        "cta": "See what to do instead",
        "can_continue": False,
    },
    OUTCOME_CLASSIFICATION: {
        "title": "Something went wrong at our end",
        "lede": (
            "We could not classify the documents reliably. That is our failure, "
            "not a judgment on your case. Your uploads are saved — try again on "
            "this same case."
        ),
        "next": "Try continuing this case with the same documents, or upload a clearer copy.",
        "cta": "Continue this case",
        "can_continue": True,
    },
}


def classify_hold(case, pack, validation, draft=None) -> dict[str, Any]:
    """Derive a customer outcome from audit + pack — not from the UI state alone.

    Only the current run counts (P0.3). A draft error, a ground-recovery wipe or
    a no-grounds finding from an earlier run says nothing about this one, and
    combining them was how a completed analysis kept reporting an old failure.
    """
    audit = current_run_audit(case)
    events = {a.get("event") for a in audit}

    state_val = getattr(getattr(case, "state", None), "value", None) or str(
        getattr(case, "state", "") or "")

    if state_val == "CLASSIFICATION_FAILED" or "classification_failed" in events:
        return _pack(OUTCOME_CLASSIFICATION, case)
    if getattr(case, "scope_stop", None) or state_val == "NO_APPEAL_RIGHT":
        return _pack(OUTCOME_SCOPE, case)

    if "held_needs_fact_confirmation" in events:
        return _pack(OUTCOME_NEEDS_FACTS, case, detail="fact_confirmation")

    if "draft_error" in events:
        return _pack(OUTCOME_PROCESSING_ERROR, case, detail="draft_error")

    # Ground recovery wiped a prior selection to empty — technical, not merits.
    for a in audit:
        if a.get("event") == "ground_recovery":
            was, now = a.get("was") or [], a.get("now") or []
            if was and not now:
                return _pack(OUTCOME_PROCESSING_ERROR, case,
                             detail="ground_recovery_cleared_selection")
        if a.get("event") == "ground_recovery_preserved":
            # Selection was restored after a wipe attempt; if we are still held,
            # the hold is a processing/validation failure, not "no merit".
            return _pack(OUTCOME_PROCESSING_ERROR, case,
                         detail="ground_recovery_preserved")

    issues = []
    if validation is not None:
        issues = [getattr(i, "rule", None) or (i.get("rule") if isinstance(i, dict) else None)
                  for i in (validation.issues or [])]
    if "VAL-DRAFT" in issues:
        return _pack(OUTCOME_PROCESSING_ERROR, case, detail="val_draft")
    if "VAL-CONFLICT" in issues and case.get("pcn_conflict"):
        return _pack(OUTCOME_NEEDS_FACTS, case, detail="pcn_conflict")

    # Incomplete notice sides that blocked content grounds.
    if case.get("notice_sides_complete") is False:
        module_ids_preview = list(
            (pack.module_ids if pack else None) or case.analysis_module_ids or [])
        if not module_ids_preview and any(
                "notice sides" in str(a).lower() or "notice_sides" in str(a).lower()
                for a in audit):
            return _pack(OUTCOME_NEEDS_DOCUMENTS, case)

    # Analysis never ran (model/provider failure): an empty selection says
    # nothing about the case. Checked before any no-supported-grounds path.
    if analysis_failed(case):
        return _pack(OUTCOME_PROCESSING_ERROR, case, detail="case_analysis_error")

    # A ground is withheld only because the site postcode is unknown.
    if "held_needs_site_postcode" in events and not case.has("site_postcode"):
        return _pack(OUTCOME_NEEDS_FACTS, case, detail={"missing": ["site_postcode"]})

    # Truthful completed analysis with nothing to argue — set before drafting.
    if "analysis_complete_no_supported_grounds" in events:
        return _pack(OUTCOME_NO_SUPPORTED_GROUNDS, case)

    module_ids = list((pack.module_ids if pack else None) or case.analysis_module_ids or [])
    leading = _leading(case, module_ids)

    # Empty pack / no_ground / VAL-SUBSTANCE without a completed no-grounds
    # analysis event means retrieval or drafting failed — not a merits finding.
    if not module_ids:
        if events & {"no_ground", "no_ground_after_widen", "no_leading_ground_drafting_simple"}:
            return _pack(OUTCOME_PROCESSING_ERROR, case,
                         detail="empty_pack_or_no_ground")
        if "VAL-SUBSTANCE" in issues:
            return _pack(OUTCOME_PROCESSING_ERROR, case,
                         detail="val_substance_empty_pack")
        # Analysis finished with an empty selection and we never drafted.
        return _pack(OUTCOME_NO_SUPPORTED_GROUNDS, case,
                     detail={"module_ids": module_ids, "issues": issues})

    if not leading:
        # Support-only pack held after drafting/validation failure.
        if issues or events & {"no_ground", "no_ground_after_widen", "draft_error"}:
            return _pack(OUTCOME_PROCESSING_ERROR, case,
                         detail={"issues": issues, "module_ids": module_ids})
        return _pack(OUTCOME_NO_SUPPORTED_GROUNDS, case,
                     detail={"module_ids": module_ids, "issues": issues})

    # Had leading grounds but still held (validation etc.) → processing/validation.
    if issues:
        return _pack(OUTCOME_PROCESSING_ERROR, case,
                     detail={"issues": issues, "module_ids": module_ids})
    return _pack(OUTCOME_PROCESSING_ERROR, case, detail="held_with_leading_grounds")


def current_run_audit(case) -> list[dict]:
    scoped = getattr(case, "current_run_audit", None)
    return scoped() if callable(scoped) else list(getattr(case, "audit", None) or [])


def analysis_failed(case) -> bool:
    """True when the most recent case analysis attempt failed rather than
    completed. Earlier rounds do not count: a later successful round supersedes
    an earlier failure, and a later failure supersedes an earlier success."""
    for a in reversed(list(getattr(case, "audit", None) or [])):
        event = a.get("event")
        if event == "case_analysis_completed":
            return False
        if event == "case_analysis_error":
            return True
    return False


def _leading(case, module_ids: list[str]) -> list[str]:
    try:
        from ..kg.graph import KnowledgeGraph
        kg = KnowledgeGraph()
    except Exception:
        return [m for m in module_ids if not str(m).startswith("KB-LAND")]
    out = []
    for mid in module_ids:
        m = kg.modules.get(mid)
        if m and m.strength >= 50:
            out.append(mid)
    return out


def _pack(code: str, case, detail: Any = None) -> dict[str, Any]:
    copy = dict(CUSTOMER_COPY.get(code) or CUSTOMER_COPY[OUTCOME_NO_SUPPORTED_GROUNDS])
    return {
        "outcome": code,
        "outcome_title": copy["title"],
        "outcome_message": copy["lede"],
        "outcome_next": copy["next"],
        "cta_label": copy["cta"],
        "can_continue": bool(copy.get("can_continue")),
        "case_id": getattr(case, "case_id", None),
        "detail": detail,
    }
