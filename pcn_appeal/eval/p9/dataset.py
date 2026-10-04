"""Load and write the P9 v1 dataset. Split is recorded in the manifest."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import DATASET_VERSION
from .goldens import all_cases
from .schema import (
    BLIND_HOLDOUT, COMPLETE, DEVELOPMENT, NOTICE_ONLY, SPLITS, VALIDATION,
    GoldenCase,
)

ROOT = Path(__file__).resolve().parents[3]
DATASET_DIR = ROOT / "datasets" / DATASET_VERSION


def write_dataset(dest: Path = DATASET_DIR) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "complete").mkdir(exist_ok=True)
    (dest / "notice_only").mkdir(exist_ok=True)
    cases = all_cases()
    for case in cases:
        folder = dest / ("complete" if case.completeness == COMPLETE else "notice_only")
        path = folder / f"{case.case_id}.json"
        path.write_text(json.dumps(case.to_dict(), indent=2, default=str) + "\n",
                        encoding="utf-8")
    manifest = build_manifest(cases)
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return manifest


def load_cases(dest: Path = DATASET_DIR) -> list[GoldenCase]:
    if not (dest / "manifest.json").exists():
        write_dataset(dest)
    cases = []
    for folder in ("complete", "notice_only"):
        for path in sorted((dest / folder).glob("*.json")):
            cases.append(GoldenCase.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return cases


def build_manifest(cases: list[GoldenCase]) -> dict:
    complete = [c for c in cases if c.completeness == COMPLETE]
    notice = [c for c in cases if c.completeness == NOTICE_ONLY]
    split = {s: [c.case_id for c in cases if c.split == s] for s in SPLITS}
    return {
        "dataset_id": DATASET_VERSION,
        "dataset_version": DATASET_VERSION,
        "evaluation_release": "P9_BASELINE_2026-10-04",
        "immutable": True,
        "governance": {
            "client_approved_expectations_immutable": True,
            "expectation_change_requires_new_dataset_version": True,
            "blind_holdout_may_not_tune": [
                "prompts", "rules", "kb_content", "model_parameters",
            ],
            "no_silent_golden_edits": True,
            "approved_by": "development_baseline",
            "approved_at": "2026-10-04",
            "why": "First honest P9 baseline. Journey snapshot approved-grounds "
                   "were not copied. NOTICE_ONLY cases are not complete ground truth.",
        },
        "split_policy": {
            COMPLETE: {DEVELOPMENT: 6, VALIDATION: 2, BLIND_HOLDOUT: 2},
            NOTICE_ONLY: {DEVELOPMENT: 5, VALIDATION: 2, BLIND_HOLDOUT: 2},
            "leakage_controls": [
                "Near-duplicate Acme overstay notices stay together in DEVELOPMENT.",
                "LATE / LATE+ANPR additive pair both in DEVELOPMENT.",
                "CP Plus NTK + reminder stay together in DEVELOPMENT.",
                "APCOA Luton pair stay together in VALIDATION.",
                "BLIND_HOLDOUT uses different operators, allegations, and evidence types.",
            ],
        },
        "counts": {
            "total": len(cases),
            "complete": len(complete),
            "notice_only": len(notice),
            "by_split": dict(Counter(c.split for c in cases)),
            "complete_by_split": dict(Counter(c.split for c in complete)),
            "notice_only_by_split": dict(Counter(c.split for c in notice)),
        },
        "complete_case_ids": [c.case_id for c in complete],
        "notice_only_case_ids": [c.case_id for c in notice],
        "split": split,
        "cases": [
            {
                "case_id": c.case_id,
                "completeness": c.completeness,
                "split": c.split,
                "family": c.family,
                "operator": c.operator,
                "allegation": c.allegation,
                "legal_route": c.legal_route,
                "circumstance": c.circumstance,
                "evidence_type": c.evidence_type,
                "paired_with": c.paired_with,
                "approval_status": c.approval_status,
            }
            for c in cases
        ],
    }
