"""Canonical GoldenCase schema for P9.

Two completeness classes:

    COMPLETE      full end-to-end ground truth is allowed
    NOTICE_ONLY   historical front-notice reference; not full ground truth

Approval statuses prevent treating development snapshots as client-approved
wording, and prevent scoring layers that have no gold.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

COMPLETE = "COMPLETE"
NOTICE_ONLY = "NOTICE_ONLY"
COMPLETENESS = (COMPLETE, NOTICE_ONLY)

DEVELOPMENT = "DEVELOPMENT"
VALIDATION = "VALIDATION"
BLIND_HOLDOUT = "BLIND_HOLDOUT"
SPLITS = (DEVELOPMENT, VALIDATION, BLIND_HOLDOUT)

CLIENT_APPROVED = "CLIENT_APPROVED"
DEVELOPMENT_APPROVED = "DEVELOPMENT_APPROVED"
UNOBSERVED = "UNOBSERVED"
REFERENCE_ONLY = "REFERENCE_ONLY"

EXTRACTION_FIELDS = (
    "operator_name", "pcn_number", "vrm", "parking_event_date",
    "notice_issue_date", "entry_time", "exit_time", "parking_location",
    "site_postcode", "alleged_breach", "notice_route", "notice_type",
)
LEGAL_CRITICAL_FIELDS = (
    "operator_name", "pcn_number", "vrm", "parking_event_date",
    "notice_issue_date", "alleged_breach", "notice_route", "notice_type",
)
ORDINARY_FIELDS = (
    "entry_time", "exit_time", "parking_location", "site_postcode",
)

FAILURE_LAYERS = (
    "CLASSIFICATION_ERROR",
    "EXTRACTION_ERROR",
    "FACT_ERROR",
    "NARRATIVE_FACT_ERROR",
    "LINEAGE_ERROR",
    "QUESTION_ERROR",
    "LEGAL_FINDING_ERROR",
    "KNOWLEDGE_RETRIEVAL_ERROR",
    "GROUND_SELECTION_ERROR",
    "GROUND_MERGE_ERROR",
    "CLAIM_PLAN_ERROR",
    "DRAFT_CONTEXT_ERROR",
    "DRAFT_ERROR",
    "VALIDATION_ERROR",
    "OUTCOME_STATE_ERROR",
    "INFRASTRUCTURE_ERROR",
)

LAYER_ORDER = FAILURE_LAYERS


@dataclass
class GoldenInput:
    notice_front: str = ""
    notice_back: str = ""
    customer_narrative: str = ""
    customer_answers: dict = field(default_factory=dict)
    answer_policy: str = "skip"
    evidence: list = field(default_factory=list)
    corrections: dict = field(default_factory=dict)
    extraction_fields: dict = field(default_factory=dict)
    doc_types: dict = field(default_factory=dict)
    image_path: Optional[str] = None


@dataclass
class AppealRequirements:
    must_express: list = field(default_factory=list)
    must_not_express: list = field(default_factory=list)
    exact_wording_approved: bool = False


@dataclass
class ExpectedLayer:
    """What may be scored for one layer.

    `evaluate` False means the metric is N/A — gold is missing or the case
    class forbids using that layer as ground truth.
    """
    evaluate: bool = False
    approval: str = UNOBSERVED
    values: Any = None


@dataclass
class GoldenExpected:
    classification: ExpectedLayer = field(default_factory=ExpectedLayer)
    extraction: ExpectedLayer = field(default_factory=ExpectedLayer)
    facts: ExpectedLayer = field(default_factory=ExpectedLayer)
    derived_facts: ExpectedLayer = field(default_factory=ExpectedLayer)
    relationships: ExpectedLayer = field(default_factory=ExpectedLayer)
    questions: ExpectedLayer = field(default_factory=ExpectedLayer)
    legal_findings: ExpectedLayer = field(default_factory=ExpectedLayer)
    knowledge_matches: ExpectedLayer = field(default_factory=ExpectedLayer)
    supported_grounds: ExpectedLayer = field(default_factory=ExpectedLayer)
    rejected_grounds: ExpectedLayer = field(default_factory=ExpectedLayer)
    invalidated_grounds: ExpectedLayer = field(default_factory=ExpectedLayer)
    claim_plan: ExpectedLayer = field(default_factory=ExpectedLayer)
    required_particulars: ExpectedLayer = field(default_factory=ExpectedLayer)
    prohibited_claims: ExpectedLayer = field(default_factory=ExpectedLayer)
    final_outcome: ExpectedLayer = field(default_factory=ExpectedLayer)
    final_appeal_requirements: ExpectedLayer = field(default_factory=ExpectedLayer)
    ground_origins: dict = field(default_factory=dict)


@dataclass
class GoldenCase:
    case_id: str
    dataset_version: str
    completeness: str
    split: str
    approval_status: str
    family: str
    operator: str
    allegation: str
    legal_route: str
    circumstance: str
    evidence_type: str
    notes: str = ""
    paired_with: Optional[str] = None
    input: GoldenInput = field(default_factory=GoldenInput)
    expected: GoldenExpected = field(default_factory=GoldenExpected)
    governance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GoldenCase":
        raw = dict(data)
        inp = GoldenInput(**(raw.pop("input", None) or {}))
        exp_raw = dict(raw.pop("expected", None) or {})
        origins = exp_raw.pop("ground_origins", {}) or {}
        layers = {}
        for name in GoldenExpected.__dataclass_fields__:
            if name == "ground_origins":
                continue
            val = exp_raw.get(name)
            if isinstance(val, ExpectedLayer):
                layers[name] = val
            elif isinstance(val, dict):
                layers[name] = ExpectedLayer(**{
                    k: v for k, v in val.items()
                    if k in ExpectedLayer.__dataclass_fields__
                })
            else:
                layers[name] = ExpectedLayer()
        expected = GoldenExpected(ground_origins=origins, **layers)
        known = {f for f in cls.__dataclass_fields__}
        return cls(input=inp, expected=expected,
                   **{k: v for k, v in raw.items() if k in known})


def layer(evaluate: bool, approval: str, values: Any = None) -> ExpectedLayer:
    return ExpectedLayer(evaluate=evaluate, approval=approval, values=values)
