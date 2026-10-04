"""Validator mutation suite for P10.4 (extends P9 classes + role/semantic cases)."""
from __future__ import annotations

from typing import Optional

from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.eval.p9.mutations import MUTATIONS as P9_MUTATIONS, apply_mutation, detect
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack
from pcn_appeal.module_roles import LEGAL_CONCLUSION, SUPPORTING_PROPOSITION

EXTRA_MUTATIONS = (
    "support_only_orphan",
    "legal_conclusion_only_pack",
    "semantic_concept_invented_by_draft",
    "ground_removed_from_final_prose",
)

ALL_MUTATIONS = tuple(P9_MUTATIONS) + EXTRA_MUTATIONS


def _clone_draft(draft: Draft) -> Draft:
    paras = []
    for para in draft.paragraphs:
        paras.append([
            DraftSentence(s.text, list(s.fact_refs), list(s.module_refs),
                          list(s.evidence_refs), s.quote_of)
            for s in para
        ])
    return Draft(draft.case_id, paras, draft.attempt)


def apply_extra(draft: Draft, kind: str, pack: RetrievalPack) -> tuple[Draft, RetrievalPack]:
    d = _clone_draft(draft)
    p = RetrievalPack(
        primary_route=pack.primary_route,
        secondary_routes=list(pack.secondary_routes or []),
        module_ids=list(pack.module_ids or []),
        verified_facts=dict(pack.verified_facts or {}),
        fact_refs=dict(getattr(pack, "fact_refs", None) or {}),
        missing_facts=list(pack.missing_facts or []),
        evidence_refs=list(pack.evidence_refs or []),
        prohibited_claims=list(pack.prohibited_claims or []),
        code_version=pack.code_version,
        pofa_route=pack.pofa_route,
        pofa_findings=list(pack.pofa_findings or []),
        driver_status=pack.driver_status,
        jurisdiction=pack.jurisdiction,
        context_chunks=list(pack.context_chunks or []),
        lease_clauses=list(pack.lease_clauses or []),
        claim_plan=dict(pack.claim_plan or {}) if isinstance(pack.claim_plan, dict) else pack.claim_plan,
    )
    if kind == "support_only_orphan":
        p.module_ids = ["KB-LAND-01", "KB-SIGN-01"]
        p.claim_plan = {"status": "LOCKED", "approved": list(p.module_ids)}
        d.paragraphs = [[DraftSentence(
            "Signage and landowner authority are unclear.",
            [], ["KB-LAND-01"])]]
    elif kind == "legal_conclusion_only_pack":
        p.module_ids = ["KB-POFA-01", "KB-POFA-05"]
        p.claim_plan = {"status": "LOCKED", "approved": list(p.module_ids)}
        d.paragraphs = [[DraftSentence(
            "Keeper liability is not established.",
            [], ["KB-POFA-01"])]]
    elif kind == "semantic_concept_invented_by_draft":
        d.paragraphs.append([DraftSentence(
            "The vehicle suffered a mechanical breakdown and was immobilised, "
            "and a full payment was completed on the app.",
            [], ["STRUCTURAL"])])
    elif kind == "ground_removed_from_final_prose":
        # Keep pack grounds but strip module-bearing prose
        d.paragraphs = [[DraftSentence(
            "Please cancel this parking charge.",
            [], ["STRUCTURAL"])]]
    return d, p


def run_mutations(draft: Optional[Draft], pack: Optional[RetrievalPack]) -> dict:
    if draft is None or pack is None:
        return {"status": "N/A", "reason": "no released draft/pack available"}

    clean = detect(draft, pack)
    rows = []
    detected = 0
    for kind in P9_MUTATIONS:
        mutated = apply_mutation(draft, kind, pack)
        result = detect(mutated, pack)
        hit = result["detected"]
        detected += int(hit)
        rows.append({"mutation": kind, "class": "p9", "detected": hit, **result})

    extra_detected = 0
    for kind in EXTRA_MUTATIONS:
        mutated, mpack = apply_extra(draft, kind, pack)
        result = detect(mutated, mpack)
        # For orphan / legal-conclusion packs, VAL-ORPHAN-SUPPORT (or hold) is required
        rules = set(result.get("rules") or [])
        if kind in ("support_only_orphan", "legal_conclusion_only_pack"):
            hit = bool(rules & {"VAL-ORPHAN-SUPPORT", "VAL-NO-LEAD", "VAL-COVERAGE",
                                "NO_SUPPORTED_GROUNDS"}) or result["detected"]
        elif kind == "ground_removed_from_final_prose":
            hit = result["detected"]  # missing ground / coverage
        else:
            hit = result["detected"]
        extra_detected += int(hit)
        detected += int(hit)
        rows.append({"mutation": kind, "class": "p10_4", "detected": hit, **result})

    n = len(ALL_MUTATIONS)
    false_positive = 1.0 if clean["detected"] else 0.0
    return {
        "status": "SCORED",
        "clean_draft_flagged": clean["detected"],
        "clean_rules": clean["rules"],
        "rows": rows,
        "detection_rate": detected / n if n else 0.0,
        "p9_detection_rate": sum(1 for r in rows if r["class"] == "p9" and r["detected"])
        / max(len(P9_MUTATIONS), 1),
        "extra_detection_rate": extra_detected / max(len(EXTRA_MUTATIONS), 1),
        "false_positive_rate": false_positive,
        "detected": detected,
        "total": n,
        "expected_detection_rate": 1.0,
        "expected_false_positive_rate": 0.0,
        "passed": (detected == n) and (false_positive == 0.0),
    }


def synthetic_role_mutation_packs() -> dict:
    """Standalone packs when no released draft exists — still exercise orphan rules."""
    from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

    pack = RetrievalPack(
        primary_route="POFA", secondary_routes=[],
        module_ids=["KB-POFA-01", "KB-LAND-01"],
        verified_facts={"pcn_number": "X", "vrm": "Y"},
        fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
        code_version="SCOP-1.1", pofa_route="POSTAL", pofa_findings=[],
        driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[], lease_clauses=[],
        claim_plan={"status": "LOCKED", "approved": ["KB-POFA-01", "KB-LAND-01"]},
    )
    draft = Draft("MUT-ORPH", [[DraftSentence(
        "Keeper liability is not automatic.", [], ["KB-POFA-01"])]])
    return run_mutations(draft, pack)
