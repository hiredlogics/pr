"""Neutral document classification: what each uploaded document is.

Runs once per upload, before any service reads a document. Its output is the
only input to routing (router.py). It does not extract a service's facts and it
does not judge merits - the private-parking extractor still does its own
reading, but only after the router has sent the case there.

The model labels; this module checks the label against the contract and never
lets a malformed answer through as a confident one:

  * an unrecognised document_type becomes UNKNOWN with an ambiguity reason;
  * a stage outside the type's allowed list falls back to the type's default;
  * a document the model skipped is UNKNOWN ("not classified"), not dropped;
  * no usable answer at all is ClassificationFailed - our failure, which the
    case reports as CLASSIFICATION_FAILED, never as a verdict on the document.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .. import prompts
from ..models import CaseFile
from . import document_types as T


class ClassificationFailed(RuntimeError):
    """The classifier gave no usable answer. Retryable; not the customer's fault."""


@dataclass
class DocumentClassification:
    evidence_id: str
    document_type: str
    service_family: str
    stage: str
    issuer: dict = field(default_factory=dict)
    references: dict = field(default_factory=dict)
    document_date: Optional[str] = None
    confidence: float = 0.0
    evidence_spans: list = field(default_factory=list)
    ambiguity_reason: Optional[str] = None
    # Contract corrections this module made to the model's answer, for the audit.
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DocumentClassification":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def _confidence(value: Any) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    return c if 0.0 <= c <= 1.0 else 0.0


def _span_found(span_text: str, doc_text: str) -> Optional[bool]:
    """Whether a quoted span appears in the document's own text. None when the
    document has no text layer (a photo), where the quote cannot be checked."""
    if not doc_text.strip():
        return None
    squash = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    return squash(span_text) in squash(doc_text) if span_text.strip() else False


def _normalise(raw: dict[str, Any], case: CaseFile) -> DocumentClassification:
    ev_id = str(raw.get("evidence_id") or "")
    notes: list[str] = []
    doc_type = str(raw.get("document_type") or "").strip().upper()
    ambiguity = raw.get("ambiguity_reason") or None
    if doc_type not in T.ALL_TYPES:
        notes.append(f"unrecognised document_type {doc_type!r}")
        ambiguity = ambiguity or f"classifier returned an unrecognised label ({doc_type or 'none'})"
        doc_type = T.UNKNOWN

    stage = str(raw.get("stage") or "").strip().upper()
    if stage not in T.STAGES[doc_type]:
        if stage:
            notes.append(f"stage {stage!r} not allowed for {doc_type}; using default")
        stage = T.default_stage(doc_type)

    family = str(raw.get("service_family") or "").strip().upper() or T.UNKNOWN
    if family not in T.FAMILIES:
        notes.append(f"unrecognised service_family {family!r}")
        family = T.UNKNOWN

    spans = []
    doc_text = case.evidence[ev_id].text if ev_id in case.evidence else ""
    for s in raw.get("evidence_spans") or []:
        if isinstance(s, dict) and str(s.get("text") or "").strip():
            spans.append({"page": s.get("page"), "text": str(s["text"])[:300],
                          "found_in_text": _span_found(str(s["text"]), doc_text or "")})

    issuer = raw.get("issuer") if isinstance(raw.get("issuer"), dict) else {}
    refs = raw.get("references") if isinstance(raw.get("references"), dict) else {}
    return DocumentClassification(
        evidence_id=ev_id, document_type=doc_type, service_family=family, stage=stage,
        issuer={"name": issuer.get("name"), "kind": issuer.get("kind")},
        references={k: refs.get(k) for k in ("pcn_number", "vrm", "claim_number", "other")
                    if refs.get(k) not in (None, "", [])},
        document_date=raw.get("document_date") or None,
        confidence=_confidence(raw.get("confidence")),
        evidence_spans=spans, ambiguity_reason=ambiguity, notes=notes)


def _payload(case: CaseFile) -> tuple[str, list[bytes]]:
    """The same document framing the extractor uses, so image N maps back to a
    document id the same way in both calls."""
    images: list[bytes] = []
    manifest: list[str] = []
    for e in case.evidence.values():
        for page, img in enumerate(e.images or [], start=1):
            images.append(img)
            manifest.append(f"[image {len(images)}] document id='{e.evidence_id}' "
                            f"filename='{e.filename}' page={page}")
    docs = "\n\n".join(f"<document id='{e.evidence_id}' filename='{e.filename}'>\n{e.text}\n</document>"
                       for e in case.evidence.values())
    if manifest:
        docs += ("\n\n<attached_images>\nThese images are pages of the documents above, in order.\n"
                 + "\n".join(manifest) + "\n</attached_images>")
    return docs, images


def classify(case: CaseFile, llm) -> dict[str, DocumentClassification]:
    """One classification per uploaded document, keyed by evidence id.

    Raises ClassificationFailed when the model call fails or answers nothing
    usable. Partial answers are completed with UNKNOWN entries so every document
    is accounted for.
    """
    if not case.evidence:
        raise ClassificationFailed("no documents to classify")
    user, images = _payload(case)
    try:
        out = llm.complete_json(task="classification", system=prompts.system("classification"),
                                user=user, images=images or None)
    except Exception as exc:                      # provider error, bad JSON, timeout
        raise ClassificationFailed(f"classifier call failed: {type(exc).__name__}") from exc

    rows = out.get("documents") if isinstance(out, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ClassificationFailed("classifier returned no documents")

    result: dict[str, DocumentClassification] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        c = _normalise(raw, case)
        if c.evidence_id in case.evidence and c.evidence_id not in result:
            result[c.evidence_id] = c
    if not result:
        raise ClassificationFailed("classifier named none of the uploaded documents")

    for ev_id in case.evidence:
        if ev_id not in result:
            result[ev_id] = DocumentClassification(
                ev_id, T.UNKNOWN, T.UNKNOWN, T.default_stage(T.UNKNOWN),
                ambiguity_reason="not classified", notes=["missing from classifier answer"])
    return result


def from_legacy_doc_types(doc_types: dict[str, str], confidence: float = 0.9) -> dict[str, Any]:
    """A classifier answer equivalent to an extraction-style `doc_types` map.

    For test doubles and the demo reader only: a fixture written before the
    classifier existed scripts only an extraction response, and this keeps its
    routing identical to what the extraction labels already said.
    """
    docs = []
    for ev_id, label in (doc_types or {}).items():
        doc_type, stage = T.from_legacy_label(label)
        docs.append({"evidence_id": ev_id, "document_type": doc_type,
                     "service_family": T.FIXED_FAMILY.get(doc_type, T.UNKNOWN),
                     "stage": stage, "confidence": confidence,
                     "ambiguity_reason": None if doc_type != T.UNKNOWN else "legacy label OTHER"})
    return {"documents": docs}
