"""Draft versions (P6 §7): every draft the system wrote, immutable, tied to
the Claim Plan it was written from.

    draft_id        uuid5(case, claim plan, content hash): the same content under
                    the same plan is the same draft, however often it is
                    regenerated or reloaded
    version         1, 2, ... per case, in the order distinct drafts first appeared
    content_hash    SHA-256 of the canonical sentences (text + every reference)

The record holds the draft's structured content so a reloaded case serves the
draft it released (`draft_of`), plus how it fared: validation status and issues,
sentence grounding, the shadow judge's verdict.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from ..models import Draft, DraftSentence

NAMESPACE = uuid.UUID("5b0a5c6e-6f1c-4b2e-9a41-0d6a6f0d7a11")
PASSED, FAILED, NOT_RUN = "PASSED", "FAILED", "NOT_RUN"


def canonical(draft: Draft) -> list[list[dict]]:
    return [[{"text": s.text, "fact_refs": list(s.fact_refs or []),
              "module_refs": list(s.module_refs or []),
              "evidence_refs": list(s.evidence_refs or []), "quote_of": s.quote_of}
             for s in p] for p in draft.paragraphs]


def content_hash(draft: Draft) -> str:
    return hashlib.sha256(json.dumps(canonical(draft), sort_keys=True).encode()).hexdigest()


def draft_id(case_id: str, claim_plan_id: Optional[str], digest: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{case_id}|{claim_plan_id}|{digest}"))


def record(case, plan, draft: Draft, validation, grounding: Optional[list] = None,
           judge: Optional[dict] = None, parent: Optional[str] = None,
           released: bool = False) -> dict:
    """The case's version row for this draft: the existing one when this content
    was already written under this plan (idempotent), else a new one."""
    digest = content_hash(draft)
    plan_id = getattr(plan, "claim_plan_id", None)
    did = draft_id(case.case_id, plan_id, digest)
    row = next((r for r in case.draft_versions if r["draft_id"] == did), None)
    status = (PASSED if validation.passed else FAILED) if validation is not None else NOT_RUN
    fields = {
        "validation_status": status,
        "issues": [{"rule": i.rule, "severity": i.severity, "message": i.message}
                   for i in (validation.issues if validation is not None else [])],
        "grounding": list(grounding or []), "judge": judge,
        "released": bool(released or (row or {}).get("released")),
        "attempt": draft.attempt, "run_id": case.run_id, "parent_draft_id": parent,
    }
    if row is None:
        row = {"draft_id": did, "case_id": case.case_id, "claim_plan_id": plan_id,
               "version": 1 + max([r["version"] for r in case.draft_versions], default=0),
               "model": draft.model, "prompt_version": draft.prompt_version,
               "content_hash": digest, "content": canonical(draft),
               "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
        row.update(fields)
        case.draft_versions.append(row)
    else:
        row.update(fields)
        row["_dirty"] = True
    return row


def draft_of(row: dict) -> Draft:
    """The Draft a stored version describes."""
    return Draft(row["case_id"],
                 [[DraftSentence(**s) for s in p] for p in row["content"]],
                 attempt=row.get("attempt") or 1, model=row.get("model"),
                 prompt_version=row.get("prompt_version"))


__all__ = ["record", "draft_of", "content_hash", "draft_id", "canonical", "PASSED", "FAILED"]
