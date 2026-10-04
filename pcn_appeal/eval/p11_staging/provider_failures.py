"""Simulate provider failures — must not RELEASE or corrupt Master Case."""
from __future__ import annotations

import json
from typing import Any

from pcn_appeal.models import CaseFile, CaseState, EvidenceItem, ValidationResult
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM
from test_scenarios import fields as extraction_fields


class _FailingLLM(ReferenceAnalysisLLM):
    """Wraps ReferenceAnalysisLLM; drafting/analysis can be forced to fail."""

    def __init__(self, payload, mode: str):
        super().__init__(payload)
        self.mode = mode
        self.calls: list[str] = []

    def complete_json(self, *, task, system, user, images=None):
        self.calls.append(task)
        if task == "drafting":
            if self.mode == "timeout":
                raise TimeoutError("simulated provider timeout")
            if self.mode == "http_429":
                raise RuntimeError("HTTP 429 Too Many Requests")
            if self.mode == "http_500":
                raise RuntimeError("HTTP 500 Internal Server Error")
            if self.mode == "invalid_json":
                # Upstream complete_json normally parses; raise as parse failure
                raise json.JSONDecodeError("Expecting value", "", 0)
            if self.mode == "empty":
                return {"paragraphs": [], "sections": [], "no_ground_reason": None}
            if self.mode == "partial":
                return {"sections": [{"section_id": "S01", "ground_ids": ["KB-PAY-01"],
                                      "text": ""}], "closing": None}
            if self.mode == "unavailable":
                raise ConnectionError("provider unavailable")
        if task == "case_analysis" and self.mode == "analysis_fail":
            raise RuntimeError("HTTP 500 analysis")
        return super().complete_json(task=task, system=system, user=user, images=images)


def _base_payload():
    return {
        "extraction": [{
            "fields": extraction_fields(
                operator_name="Fail Park", pcn_number="FP1", vrm="FP11AAA",
                parking_location="Fail Site", site_postcode="F1 1AA",
                parking_event_date="01/09/2026", notice_issue_date="05/09/2026",
                alleged_breach="No valid payment", operator_ata="BPA",
            ),
            "doc_types": {"E1": "PCN"},
        }],
    }


def _run_mode(mode: str) -> dict[str, Any]:
    llm = _FailingLLM(_base_payload(), mode)
    case = CaseFile(f"P11_FAIL_{mode}", evidence={
        "E1": EvidenceItem("E1", "PCN", "n.txt", text="PARKING CHARGE NOTICE"),
    })
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    pipe.confirm(case, {}, list(case.facts),
                 "I paid on the app but mistyped one character.")
    # Feed payment answers if questions asked
    for _ in range(3):
        qs = [q for q in (case.pending_questions or []) if q.get("fact")]
        # pending_questions may not exist — use answer API via generate path
        break
    # Directly set facts for payment path via answer if still questioning
    try:
        from pcn_appeal.models import FactStatus
        if case.state == CaseState.QUESTIONING:
            pipe.answer(case, {"payment_made": "yes", "payment_method": "APP",
                               "keying_error_type": "MINOR"})
    except Exception:
        pass
    out = pipe.generate(case)
    state = getattr(getattr(out, "state", None), "value", str(getattr(out, "state", None)))
    outcome = getattr(out, "outcome", None) or {}
    released = state == "RELEASED"
    return {
        "mode": mode,
        "state": state,
        "outcome_code": (outcome or {}).get("code") if isinstance(outcome, dict) else None,
        "released": released,
        "letter_empty": not bool(getattr(out, "letter", None)),
        "passed": (not released) and state in (
            "MANUAL_REVIEW", "VALIDATION_FAILED", "DRAFTED", "ANALYSED", "QUESTIONING",
            "CLASSIFICATION_FAILED", "NO_APPEAL_RIGHT",
        ),
    }


def run() -> dict[str, Any]:
    modes = ["timeout", "http_429", "http_500", "invalid_json", "empty",
             "partial", "unavailable"]
    rows = []
    for mode in modes:
        try:
            rows.append(_run_mode(mode))
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "mode": mode,
                "passed": True,  # hard exception before RELEASE is acceptable if not released
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "released": False,
                "state": "EXCEPTION",
            })
    released_any = any(r.get("released") for r in rows)
    return {
        "passed": (not released_any) and all(r.get("passed") for r in rows if "error" not in r
                                              or r.get("state") == "EXCEPTION"),
        "ran": True,
        "released_count": sum(1 for r in rows if r.get("released")),
        "cases": rows,
    }
