"""Pilot case observability schema + safety stop classifiers."""
from __future__ import annotations

from typing import Any


# Required fields on every pilot case record (section 6).
CASE_RECORD_FIELDS = (
    "case_id", "run_id", "deployment_version", "kb_release_id",
    "kb_release_digest", "ontology_version", "model_versions", "prompt_versions",
    "extraction_latency_ms", "semantic_latency_ms", "draft_latency_ms",
    "facts_created", "legal_findings", "eligible_modules", "selected_grounds",
    "claim_plan_id", "draft_plan_version", "validation_passed", "final_outcome",
    "final_state",
)

# Immediate pause if any case hits these (section 9).
SAFETY_STOPS = (
    "incorrect_deterministic_legal_calculation",
    "unsupported_substantive_legal_ground",
    "invented_material_fact_released",
    "driver_identity_improperly_inferred",
    "claim_plan_ground_silently_lost",
    "validation_block_but_draft_released",
    "processing_error_shown_as_released",
    "cross_customer_data_exposure",
    "duplicate_appeal_submission",
    "corrupted_authoritative_state",
)

# Release conditions (section 4) — all must hold for RELEASED.
RELEASE_CONDITIONS = (
    "claim_plan_valid",
    "support_bundle_complete",
    "draft_plan_complete",
    "ground_coverage_pass",
    "material_fact_coverage_pass",
    "required_particulars_pass",
    "no_unsupported_material_assertion",
    "no_driver_safety_violation",
    "no_unresolved_placeholder",
    "no_calculation_contradiction",
    "outcome_resolver_released",
)


def empty_cohort_metrics() -> dict[str, Any]:
    return {
        "n_real_cases": 0,
        "cohort_cap": 20,
        "auto_scale_forbidden": True,
        "outcome_distribution": {},
        "legal_critical_extraction_accuracy": None,
        "semantic_precision": None,
        "semantic_recall": None,
        "ground_precision": None,
        "ground_recall": None,
        "claim_plan_completeness": None,
        "draft_plan_completeness": None,
        "draft_ground_coverage": None,
        "material_fact_coverage": None,
        "required_particular_coverage": None,
        "unsupported_assertion_rate": None,
        "validation_failure_rate": None,
        "processing_error_rate": None,
        "no_supported_grounds_rate": None,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "openai_calls_per_case": None,
        "input_tokens_per_case": None,
        "output_tokens_per_case": None,
        "cost_per_case": None,
        "retry_rate": None,
        "http_429_rate": None,
        "http_5xx_rate": None,
        "structured_output_retry_rate": None,
        "db_failures": 0,
        "provider_failures": 0,
        "safety_incidents": [],
        "rollback_events": [],
        "case_records": [],
    }


def classify_safety(case_record: dict[str, Any]) -> list[str]:
    """Return safety-stop codes triggered by one case record (pilot QA)."""
    hits = []
    state = case_record.get("final_state")
    outcome = case_record.get("final_outcome")
    if state == "RELEASED" and outcome == "PROCESSING_ERROR":
        hits.append("processing_error_shown_as_released")
    if state == "RELEASED" and case_record.get("validation_passed") is False:
        hits.append("validation_block_but_draft_released")
    if case_record.get("invented_material_fact"):
        hits.append("invented_material_fact_released")
    if case_record.get("driver_inferred"):
        hits.append("driver_identity_improperly_inferred")
    if case_record.get("ground_silently_lost"):
        hits.append("claim_plan_ground_silently_lost")
    if case_record.get("cross_customer_leak"):
        hits.append("cross_customer_data_exposure")
    if case_record.get("duplicate_appeal"):
        hits.append("duplicate_appeal_submission")
    if case_record.get("authoritative_state_corrupt"):
        hits.append("corrupted_authoritative_state")
    return hits
