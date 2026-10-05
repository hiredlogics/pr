"""P17.9 — generic CaseUnderstandingInput contract.

All substantive customer/document information enters the same reasoning lifecycle.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class CaseUnderstandingInput:
    case_id: str
    documents: list[dict] = field(default_factory=list)
    extracted_document_observations: list[dict] = field(default_factory=list)
    customer_narrative: str = ""
    customer_answers: list[dict] = field(default_factory=list)
    existing_case_state: dict = field(default_factory=dict)
    revision: int = 0
    texts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def from_case(case, *, narrative: Optional[str] = None,
              texts: Optional[list[str]] = None) -> CaseUnderstandingInput:
    """Normalize a CaseFile into the generic understanding input."""
    from ..engines.account import _collect_customer_texts

    if texts is not None:
        collected = list(texts)
    else:
        collected = list(_collect_customer_texts(case))
        narr = narrative or (case.raw_answers or {}).get("narrative") or ""
        if narr and narr not in collected:
            collected.insert(0, str(narr))

    docs = []
    for e in (case.evidence or {}).values():
        docs.append({
            "evidence_id": e.evidence_id,
            "kind": e.kind,
            "filename": e.filename,
            "uploaded": bool(e.uploaded),
            "has_text": bool((e.text or "").strip()),
            "page_images": len(e.images or []),
        })

    observations = []
    for name, node in (getattr(case, "facts", None) or {}).items():
        src = getattr(node, "source", None)
        kind = getattr(getattr(src, "kind", None), "value", None) or str(
            getattr(src, "kind", "") or "")
        if kind not in ("DOCUMENT", "SourceKind.DOCUMENT") and "DOCUMENT" not in str(kind):
            continue
        observations.append({
            "name": name,
            "value": node.value,
            "status": getattr(getattr(node, "status", None), "value", None)
                      or str(node.status),
            "source_ref": getattr(src, "ref", ""),
            "confidence": getattr(node, "confidence", None),
        })

    answers = []
    for name, raw in (case.raw_answers or {}).items():
        if str(name).startswith("_"):
            continue
        answers.append({"fact": name, "raw": str(raw)[:500]})

    rev = 0
    try:
        rev = int((case.raw_answers or {}).get("_semantic_revision") or 0)
    except Exception:
        rev = 0

    return CaseUnderstandingInput(
        case_id=str(getattr(case, "case_id", "") or ""),
        documents=docs,
        extracted_document_observations=observations,
        customer_narrative=str(
            narrative
            or (case.raw_answers or {}).get("narrative")
            or ""
        ),
        customer_answers=answers,
        existing_case_state={
            "state": str(getattr(getattr(case, "state", None), "value", "")
                         or getattr(case, "state", "")),
            "route": getattr(case, "route", None),
            "document_type": getattr(case, "document_type", None),
            "stage": getattr(case, "stage", None),
        },
        revision=rev,
        texts=collected,
    )
