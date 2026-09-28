"""FastAPI surface. Each endpoint maps to one orchestrator step.

This is a LOCAL DEV surface: cases live in a process dict, extraction runs
inline, and there is no auth. Production replaces the store with Postgres, runs
the LLM steps as durable workflow activities, and puts authentication, tenant
isolation and the product/case binding check in front of every route.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .kg.graph import KnowledgeGraph
from .llm import default_client
from .models import CaseFile, CaseState, EvidenceItem
from .orchestrator import AppealPipeline

app = FastAPI(title="PCN Appeal AI", version="2.0")

KG = KnowledgeGraph()                 # one graph per process; rebuilt on KB release
CASES: dict[str, dict[str, Any]] = {}


class DocumentIn(BaseModel):
    evidence_id: str
    filename: str
    text: str = ""                    # OCR output; production reads this from S3
    kind: str = "OTHER"               # customer's guess; extraction overwrites it


class DocumentsIn(BaseModel):
    documents: list[DocumentIn]


class ConfirmIn(BaseModel):
    corrections: dict = {}
    confirmed: list[str] = []
    narrative: str
    driver_already_named_to_operator: bool = False   # status only - never identity


class AnswersIn(BaseModel):
    answers: dict


def _case(case_id: str) -> dict[str, Any]:
    rec = CASES.get(case_id)
    if rec is None:
        raise HTTPException(404, f"unknown case {case_id}")
    return rec


@app.get("/health")
def health():
    from .llm import DemoLLM
    return {"status": "ok", "modules": len(KG.modules), "blocks": len(KG.blocks),
            "extractor": "demo-label-matcher" if isinstance(default_client(), DemoLLM) else "anthropic"}


@app.post("/cases")
def create_case():
    case_id = f"C-{len(CASES) + 1:04d}"
    CASES[case_id] = {"case": CaseFile(case_id), "pipe": AppealPipeline(default_client(), kg=KG),
                      "flags": [], "questions": [], "output": None}
    return {"case_id": case_id, "state": CaseState.CREATED.value}


@app.post("/cases/{case_id}/documents")
def upload(case_id: str, body: DocumentsIn):
    """Production: virus scan -> S3 -> enqueue extraction. Here: inline."""
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state != CaseState.CREATED:
        raise HTTPException(409, f"documents already extracted (state {case.state.value})")
    for d in body.documents:
        case.evidence[d.evidence_id] = EvidenceItem(d.evidence_id, d.kind, d.filename, text=d.text)
    rec["flags"] = rec["pipe"].ingest(case)
    return {"state": case.state.value, "flags": rec["flags"],
            "doc_types": {e.evidence_id: e.kind for e in case.evidence.values()}}


@app.get("/cases/{case_id}/confirmation")
def confirmation_screen(case_id: str):
    """Extracted facts + flags for the customer to confirm or correct."""
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    return {"state": case.state.value, "flags": rec["flags"],
            "facts": [{"name": f.name, "value": str(f.value), "status": f.status.value,
                       "confidence": f.confidence, "source": f.source.ref}
                      for f in case.facts.values()]}


@app.post("/cases/{case_id}/confirm")
def confirm(case_id: str, body: ConfirmIn):
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    if body.driver_already_named_to_operator:
        from .models import DriverStatus
        case.driver_status = DriverStatus.FORMALLY_IDENTIFIED
    rec["questions"] = rec["pipe"].confirm(case, body.corrections, body.confirmed, body.narrative)
    return {"state": case.state.value, "route_hints": case.get("route_hints", []),
            "questions": rec["questions"]}


@app.post("/cases/{case_id}/answers")
def answer(case_id: str, body: AnswersIn):
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    try:
        rec["questions"] = rec["pipe"].answer(case, body.answers)
    except (ValueError, TypeError) as exc:                 # bad choice / non-int answer
        raise HTTPException(422, str(exc)) from exc
    return {"state": case.state.value, "questions": rec["questions"]}


@app.post("/cases/{case_id}/generate")
def generate(case_id: str):
    """Production: enqueued worker step; the customer polls GET /appeal."""
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    out = rec["pipe"].generate(case)
    rec["output"] = out
    return {"state": out.state.value, "primary_route": out.pack.primary_route,
            "secondary_routes": out.pack.secondary_routes,
            "blocking_issues": [asdict(i) for i in out.validation.issues if i.severity == "BLOCK"]}


@app.get("/cases/{case_id}/appeal")
def get_appeal(case_id: str):
    """RELEASED -> letter (production: PDF url); MANUAL_REVIEW -> status only."""
    rec = _case(case_id)
    out = rec["output"]
    if out is None:
        raise HTTPException(409, "not generated yet - POST /generate")
    if out.state != CaseState.RELEASED:
        raise HTTPException(409, {"state": out.state.value,
                                  "issues": [asdict(i) for i in out.validation.issues]})
    return {"state": out.state.value, "letter": out.letter, "evidence_list": out.evidence_list,
            "primary_route": out.pack.primary_route, "secondary_routes": out.pack.secondary_routes,
            "pofa_route": out.pack.pofa_route, "pofa_findings": out.pack.pofa_findings,
            "code_version": out.pack.code_version, "module_ids": out.pack.module_ids}


@app.get("/cases/{case_id}/trace")
def get_trace(case_id: str):
    """Why the system argued what it argued - reviewer/audit view."""
    rec = _case(case_id)
    out = rec["output"]
    if out is None:
        raise HTTPException(409, "not generated yet - POST /generate")
    return {"trace": out.pack.trace, "module_ids": out.pack.module_ids,
            "missing_facts": out.pack.missing_facts, "prohibited_claims": out.pack.prohibited_claims,
            "sentences": [{"text": s.text, "fact_refs": s.fact_refs, "module_refs": s.module_refs,
                           "evidence_refs": s.evidence_refs} for s in out.draft.sentences()],
            "audit": rec["case"].audit}


# Admin (role: legal_admin) - edit without redeploys (Dev Pack Phase 10).
# Unimplemented deliberately: these are the highest-risk endpoints in the system
# and need RBAC, dual control and the scenario suite as a publish gate first.
@app.put("/admin/kb/modules/{module_id}")
def upsert_module(module_id: str, body: dict):
    raise HTTPException(501, "not implemented: needs RBAC + dual control + publish gate")


@app.post("/admin/kb/releases")
def publish_release():
    raise HTTPException(501, "not implemented: must run the scenario suite and publish only if green")
