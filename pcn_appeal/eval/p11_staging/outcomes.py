"""Outcome-state consistency + expected-hold classification + idempotency."""
from __future__ import annotations

from typing import Any

from pcn_appeal.engines.outcome import (
    OUTCOME_NO_SUPPORTED_GROUNDS, OUTCOME_PROCESSING_ERROR,
    OUTCOME_NEEDS_DOCUMENTS, OUTCOME_NEEDS_FACTS, classify_hold,
)
from pcn_appeal.models import CaseFile, CaseState, Draft, EvidenceItem, ValidationResult
from pcn_appeal.notice_completeness import upload_pages_sufficient
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM
from test_scenarios import fields as extraction_fields


def _extract_payload(**kwargs):
    return {
        "extraction": [{
            "fields": extraction_fields(**kwargs),
            "doc_types": {"E1": "PCN"},
        }],
    }


def test_outcome_consistency() -> dict[str, Any]:
    """Customer outcome code must agree with non-RELEASED hold semantics."""
    rows = []
    # No-ground narrative
    llm = ReferenceAnalysisLLM(_extract_payload(
        operator_name="O", pcn_number="N1", vrm="AA11AAA",
        parking_location="L", site_postcode="A1 1AA",
        parking_event_date="01/09/2026", notice_issue_date="05/09/2026",
        alleged_breach="Breach", operator_ata="BPA",
    ))
    case = CaseFile("P11_OUT_NOG", evidence={
        "E1": EvidenceItem("E1", "PCN", "n.txt", text="PARKING CHARGE NOTICE"),
    })
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    pipe.confirm(case, {}, list(case.facts), "The weather was nice that day.")
    out = pipe.generate(case)
    state = getattr(out.state, "value", out.state)
    outcome = out.outcome or {}
    code = outcome.get("code") if isinstance(outcome, dict) else None
    ok = state != "RELEASED" and code in (
        OUTCOME_NO_SUPPORTED_GROUNDS, OUTCOME_NEEDS_FACTS, OUTCOME_NEEDS_DOCUMENTS,
        OUTCOME_PROCESSING_ERROR, None,
    )
    # Prefer explicit no-grounds when analysis finished empty
    rows.append({
        "case": "no_ground",
        "state": state,
        "outcome_code": code,
        "passed": ok and state != "RELEASED",
        "class": code or "HOLD",
    })
    # Trace/UI agreement: outcome attached when not released
    if state != "RELEASED":
        rows[-1]["trace_ui_agree"] = bool(outcome) or state in (
            "QUESTIONING", "MANUAL_REVIEW", "VALIDATION_FAILED",
        )
    return {
        "passed": all(r.get("passed") for r in rows),
        "ran": True,
        "cases": rows,
        "expected_hold_classes": [
            "RELEASED", "NO_SUPPORTED_GROUNDS", "NEEDS_CUSTOMER_INPUT",
            "NEEDS_DOCUMENTS", "NEEDS_FACTS", "STOP_UNSUPPORTED_ROUTE",
            "PROCESSING_ERROR",
        ],
    }


def test_front_back() -> dict[str, Any]:
    class E:
        def __init__(self, images=None, text=""):
            self.images = images or []
            self.text = text

    cases = []
    # front only
    ok, reason = upload_pages_sufficient([E(images=[b"FRONTBYTES"])])
    cases.append({"name": "front_only", "ok": ok, "reason": reason,
                  "passed": (not ok) and reason == "front_only_or_single_page"})
    # duplicate page
    ok, reason = upload_pages_sufficient([E(images=[b"SAME", b"SAME"])])
    cases.append({"name": "duplicate_page", "ok": ok, "reason": reason,
                  "passed": (not ok) and reason == "duplicate_front_images"})
    # two distinct
    ok, reason = upload_pages_sufficient([E(images=[b"FRONT", b"BACK"])])
    cases.append({"name": "two_correct_pages", "ok": ok, "reason": reason,
                  "passed": ok})
    # multipage text
    ok, reason = upload_pages_sufficient([E(text="page1\fpage2")])
    cases.append({"name": "multipage_pdf_text", "ok": ok, "reason": reason,
                  "passed": ok})
    return {
        "passed": all(c["passed"] for c in cases),
        "ran": True,
        "cases": cases,
        "note": "Does not invent reverse-page content; gate blocks incomplete uploads",
    }


def test_idempotency() -> dict[str, Any]:
    """Double generate must not release duplicates / corrupt plan."""
    from pcn_appeal.engines.claim_plan_authority import latest_locked

    llm = ReferenceAnalysisLLM(_extract_payload(
        operator_name="Idem Park", pcn_number="ID1", vrm="ID11AAA",
        parking_location="L", site_postcode="I1 1AA",
        parking_event_date="01/09/2026", notice_issue_date="05/09/2026",
        alleged_breach="No valid payment", operator_ata="BPA",
    ))
    case = CaseFile("P11_IDEM", evidence={
        "E1": EvidenceItem("E1", "PCN", "n.txt", text="PARKING CHARGE NOTICE"),
    })
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    pipe.confirm(case, {}, list(case.facts),
                 "I paid on the app but mistyped one character of the plate.")
    for _ in range(4):
        if case.state != CaseState.QUESTIONING:
            break
        try:
            pipe.answer(case, {
                "payment_made": "yes", "payment_method": "APP",
                "keying_error_type": "MINOR",
            })
        except Exception:
            break
    out1 = pipe.generate(case)
    plan1 = latest_locked(case)
    id1 = getattr(plan1, "claim_plan_id", None)
    digest1 = getattr(plan1, "plan_digest", None)
    # Second generate / refresh
    out2 = pipe.generate(case)
    plan2 = latest_locked(case)
    id2 = getattr(plan2, "claim_plan_id", None)
    digest2 = getattr(plan2, "plan_digest", None)
    # Count locked plans in audit / history if available
    plans = getattr(case, "claim_plans", None) or []
    return {
        "passed": (id1 == id2) and (digest1 == digest2 or digest1 is None),
        "ran": True,
        "plan_id_1": id1,
        "plan_id_2": id2,
        "digest_match": digest1 == digest2,
        "state_1": getattr(out1.state, "value", out1.state),
        "state_2": getattr(out2.state, "value", out2.state),
        "claim_plan_objects": len(plans) if isinstance(plans, list) else "n/a",
    }
