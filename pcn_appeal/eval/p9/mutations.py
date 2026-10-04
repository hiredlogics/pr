"""Intentionally mutated bad drafts to measure validator detection.

Does not change application validators. Uses the real DraftValidationEngine
and ValidationEngine against a pack produced by the real pipeline.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Optional

from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

MUTATIONS = (
    "missing_ground",
    "missing_material_fact",
    "wrong_calculation",
    "unsupported_legal_proposition",
    "invented_fact",
    "driver_disclosure",
    "unresolved_placeholder",
    "missing_cancellation_request",
    "source_lineage_failure",
)


def _clone_draft(draft: Draft) -> Draft:
    paras = []
    for para in draft.paragraphs:
        paras.append([
            DraftSentence(s.text, list(s.fact_refs), list(s.module_refs),
                          list(s.evidence_refs), s.quote_of)
            for s in para
        ])
    return Draft(draft.case_id, paras, draft.attempt)


def _sentences(draft: Draft) -> list[DraftSentence]:
    return [s for p in draft.paragraphs for s in p]


def apply_mutation(draft: Draft, kind: str, pack: RetrievalPack) -> Draft:
    d = _clone_draft(draft)
    if kind == "missing_ground":
        drop = next((m for s in _sentences(d) for m in (s.module_refs or [])
                     if m and m != "STRUCTURAL"), None)
        keep = []
        for para in d.paragraphs:
            row = [s for s in para if drop not in (s.module_refs or [])]
            if row:
                keep.append(row)
        d.paragraphs = keep or [[DraftSentence("Please cancel this charge.", [], ["STRUCTURAL"])]]
    elif kind == "missing_material_fact":
        for s in _sentences(d):
            s.text = re_sub_dates(s.text)
            s.fact_refs = []
    elif kind == "wrong_calculation":
        injected = False
        for s in _sentences(d):
            if any(m and m != "STRUCTURAL" for m in (s.module_refs or [])):
                s.text = s.text.rstrip(".") + " The notice was given three days after the event."
                injected = True
                break
        if not injected:
            d.paragraphs.append([DraftSentence(
                "The notice was given three days after the event.", [], ["STRUCTURAL"])])
    elif kind == "unsupported_legal_proposition":
        d.paragraphs.append([DraftSentence(
            "The charge is an unlawful penalty and therefore void.",
            [], ["STRUCTURAL"])])
    elif kind == "invented_fact":
        d.paragraphs.append([DraftSentence(
            "A valid permit numbered Z-999 was clearly displayed in the windscreen.",
            [], ["STRUCTURAL"])])
    elif kind == "driver_disclosure":
        d.paragraphs.append([DraftSentence(
            "I parked the car at 10am and I was the driver.",
            [], ["STRUCTURAL"])])
    elif kind == "unresolved_placeholder":
        d.paragraphs.append([DraftSentence(
            "The keeper refers to {OPERATOR_NAME} and [PLACEHOLDER].",
            [], ["STRUCTURAL"])])
    elif kind == "missing_cancellation_request":
        kept = []
        for para in d.paragraphs:
            row = [s for s in para if "cancel" not in s.text.lower()]
            if row:
                kept.append(row)
        d.paragraphs = kept or [[DraftSentence("The keeper disputes the charge.", [], ["STRUCTURAL"])]]
    elif kind == "source_lineage_failure":
        for s in _sentences(d):
            s.fact_refs = ["fact-id-that-does-not-exist"]
    return d


def re_sub_dates(text: str) -> str:
    import re
    text = re.sub(r"\b\d{1,2}\s+\w+\s+20\d{2}\b", "the relevant date", text)
    text = re.sub(r"\b\d{1,2}/\d{1,2}/20\d{2}\b", "the relevant date", text)
    return text


def detect(draft: Draft, pack: RetrievalPack) -> dict:
    dv = DraftValidationEngine().check(draft, pack)
    ve = ValidationEngine().validate(draft, pack)
    rules = sorted({i.rule for i in list(dv.issues) + list(ve.issues)})
    return {
        "detected": bool(dv.issues or ve.issues),
        "rules": rules,
        "draft_validation_issues": [i.rule for i in dv.issues],
        "validation_issues": [i.rule for i in ve.issues],
    }


def run_mutations(draft: Optional[Draft], pack: Optional[RetrievalPack]) -> dict:
    if draft is None or pack is None:
        return {"status": "N/A", "reason": "no released draft/pack available"}
    clean = detect(draft, pack)
    rows = []
    detected = 0
    for kind in MUTATIONS:
        mutated = apply_mutation(draft, kind, pack)
        result = detect(mutated, pack)
        hit = result["detected"]
        detected += int(hit)
        rows.append({"mutation": kind, "detected": hit, **result})
    n = len(MUTATIONS)
    false_positive = 1.0 if clean["detected"] and not clean.get("rules") else (
        1.0 if (clean["detected"] and False) else 0.0
    )
    # A clean released draft that still raises BLOCK issues is a validator FP
    # against the pipeline's own output.
    if clean["detected"]:
        false_positive = 1.0
    return {
        "status": "SCORED",
        "clean_draft_flagged": clean["detected"],
        "clean_rules": clean["rules"],
        "rows": rows,
        "detection_rate": detected / n,
        "false_positive_rate": false_positive,
        "detected": detected,
        "total": n,
    }
