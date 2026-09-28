"""FastAPI surface (production). Requires: fastapi, uvicorn, pydantic.
Each endpoint maps to one orchestrator step; long LLM steps run in workers."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import BaseModel

app = FastAPI(title="PCN Appeal AI", version="2.0")


class ConfirmIn(BaseModel):
    corrections: dict = {}
    confirmed: list[str] = []
    narrative: str
    driver_already_named_to_operator: bool = False   # status only - never identity


class AnswersIn(BaseModel):
    answers: dict


@app.post("/cases")
def create_case(): ...                               # -> case_id, upload URLs (pre-signed S3)

@app.post("/cases/{case_id}/documents")
async def upload(case_id: str, files: list[UploadFile]): ...   # virus scan -> S3 -> enqueue extraction

@app.get("/cases/{case_id}/confirmation")
def confirmation_screen(case_id: str): ...           # extracted facts + flags for the customer

@app.post("/cases/{case_id}/confirm")
def confirm(case_id: str, body: ConfirmIn): ...      # -> first adaptive questions

@app.post("/cases/{case_id}/answers")
def answer(case_id: str, body: AnswersIn): ...       # -> follow-ups or [] (then enqueue generate)

@app.get("/cases/{case_id}/appeal")
def get_appeal(case_id: str):                        # RELEASED -> PDF url; MANUAL_REVIEW -> status
    raise HTTPException(404)

# Admin (role: legal_admin) - edit without redeploys (Dev Pack Phase 10)
@app.put("/admin/kb/modules/{module_id}")
def upsert_module(module_id: str, body: dict): ...  # creates new version in DRAFT

@app.post("/admin/kb/releases")
def publish_release(): ...                          # runs scenario suite; publishes only if green
