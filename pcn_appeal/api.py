"""FastAPI surface.

`POST /appeal` is the one-click route: documents plus the customer's account in,
finished letter out - pausing only where a missing fact gates a ground that
would change the letter. The step-by-step routes (`/cases/...`) remain for a UI
that wants to drive the confirmation screen and questions itself.

Cases persist to Postgres when DATABASE_URL is set (see store/), otherwise they
live in a process dict. Extraction runs inline and there is no auth: production
runs the LLM steps as durable workflow activities and puts authentication,
tenant isolation and the product/case binding check in front of every route.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config
from .ingest import MAX_BYTES, UnsupportedUpload, fetch_upload, read_upload
from .kg.graph import KnowledgeGraph
from .llm import default_client
from .models import CaseFile, CaseState, EvidenceItem
from .notice_completeness import BOTH_SIDES_MESSAGE, upload_pages_sufficient
from .orchestrator import AppealPipeline
from .rules import scope
from .store import db


def _require_both_sides(case: CaseFile) -> None:
    """Block progression when front+reverse (or multipage PDF) are not present.

    Clears in-memory evidence on failure so the same case stays CREATED and the
    customer can retry from the upload screen without a 409.
    """
    ok, reason = upload_pages_sufficient(list(case.evidence.values()))
    if ok:
        return
    case.evidence.clear()
    case.audit.append({"event": "upload_rejected_incomplete_sides", "reason": reason})
    raise HTTPException(422, {
        "message": BOTH_SIDES_MESSAGE,
        "code": "NOTICE_SIDES_REQUIRED",
        "reason": reason,
    })


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Load `.env` when a server actually starts.

    Deliberately not at import time: the test suite imports this module, and a
    real OPENAI_API_KEY leaking in would turn every test into a paid network
    call. TestClient does not run the lifespan unless used as a context
    manager, so tests stay on the demo reader by construction.
    """
    applied = config.load_once()
    if applied:
        print(f"[config] loaded {len(applied)} setting(s) from .env: {', '.join(sorted(applied))}")
    yield


app = FastAPI(title="PCN Appeal AI", version="2.0", lifespan=lifespan)


def _load_kg() -> KnowledgeGraph:
    """Serve the published Postgres release when there is one; fall back to the
    authored YAML so the dev surface works with no database."""
    if db.enabled():
        try:
            from .store import kb_source
            return KnowledgeGraph.from_release(kb_source.load_release())
        except Exception as exc:
            print(f"[kb] Postgres release unavailable ({exc}); serving YAML")
    return KnowledgeGraph()


KG = _load_kg()                       # one graph per process; rebuilt on KB release
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
    # Optional so a UI can confirm the extracted details on one screen and
    # collect the customer's account on the next; route hints are recomputed
    # whenever this arrives.
    narrative: str = ""
    # External disclosure only. Absent / null / unrecognised → UNKNOWN.
    # Never inferred from narrative. Strictly parsed (not generic truthiness).
    driver_already_named_to_operator: Optional[Any] = None


class AnswersIn(BaseModel):
    answers: dict = {}
    skip: bool = False      # the customer declining to answer what is left


class UploadedBlob(BaseModel):
    """A file the browser sent straight to blob storage."""
    url: str
    filename: str = ""


class BlobsIn(BaseModel):
    blobs: list[UploadedBlob]


class AppealIn(BaseModel):
    """One-click input: what the customer uploaded plus what they say happened."""
    documents: list[DocumentIn] = []
    narrative: str = ""
    answers: dict = {}
    driver_already_named_to_operator: Optional[Any] = None


class DisclosureCorrectionIn(BaseModel):
    """Auditable correction of driver-disclosure status. Not a blanket reset."""
    status: str                         # CONFIRMED_YES | CONFIRMED_NO | UNKNOWN
    reason: str = ""


def _case(case_id: str) -> dict[str, Any]:
    rec = CASES.get(case_id)
    if rec is None and db.enabled():
        rec = _rehydrate(case_id)
    if rec is None:
        raise HTTPException(404, f"unknown case {case_id}")
    return rec


def _rehydrate(case_id: str) -> Optional[dict[str, Any]]:
    """Recover a case written by an earlier process (restart, second worker).
    The pipeline is rebuilt rather than loaded - it holds no case state."""
    from .store import cases as case_store
    try:
        case = case_store.load(case_id)
    except Exception:
        return None
    rec = {"case": case, "pipe": AppealPipeline(default_client(), kg=KG),
           "flags": [], "questions": [], "output": None}
    CASES[case_id] = rec
    return rec


def _persist(case: CaseFile, out=None) -> None:
    if not db.enabled():
        return
    from .store import cases as case_store
    case_store.save(case)
    if out is not None:
        case_store.save_output(case, out)


def _new_case() -> tuple[str, dict[str, Any]]:
    if db.enabled():
        from .store import cases as case_store
        case = case_store.new_case(kb_release_id=KG.release_id)
    else:
        case = CaseFile(f"C-{len(CASES) + 1:04d}")
    rec = {"case": case, "pipe": AppealPipeline(default_client(), kg=KG),
           "flags": [], "questions": [], "output": None}
    CASES[case.case_id] = rec
    return case.case_id, rec


WEB = Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
def customer_app():
    """Customer journey: upload -> answer what is missing -> letter."""
    return FileResponse(WEB / "index.html", media_type="text/html")


@app.get("/console", include_in_schema=False)
def reviewer_console():
    """Reviewer view: facts, confidence, decision trace, per-sentence provenance."""
    return FileResponse(WEB / "console.html", media_type="text/html")


@app.get("/health")
def health():
    from .llm import default_client, probe
    p = probe()
    return {"status": "ok", "modules": len(KG.modules), "blocks": len(KG.blocks),
            "kb_release": KG.release_id, "store": "postgres" if db.enabled() else "memory",
            "provider": p["provider"], "models": p["models"], "provider_note": p["reason"],
            # False means a photographed notice or scanned PDF will yield no facts,
            # so the UI can say so before the customer uploads one.
            "vision": bool(getattr(default_client(), "SUPPORTS_IMAGES", False)),
            "max_upload_bytes": MAX_BYTES}


# --------------------------------------------------------------------- one click
@app.post("/appeal")
def appeal(body: AppealIn):
    """Upload -> extract -> auto-confirm -> reason -> draft -> validate, in one call.

    Returns either `questions` (a fact is missing that gates a ground worth
    having - answer them via POST /appeal/{case_id}) or the finished `letter`.
    """
    case_id, rec = _new_case()
    case: CaseFile = rec["case"]
    from .disclosure import apply_disclosure
    apply_disclosure(case, body.driver_already_named_to_operator, source="appeal_json")
    for d in body.documents:
        case.evidence[d.evidence_id] = EvidenceItem(d.evidence_id, d.kind, d.filename, text=d.text)
    return _run_auto(rec, body.narrative, body.answers or None)


@app.post("/appeal/files")
async def appeal_files(files: list[UploadFile] = File(...), narrative: str = Form(""),
                       driver_already_named_to_operator: Optional[str] = Form(None)):
    """Same one-click journey, but taking the files a customer actually has:
    a photo of the notice, a PDF that arrived by email, a receipt screenshot.

    Each file is decoded to text, or to JPEG pages for the vision model, by
    ingest.py. A file we cannot read is reported in `rejected` rather than
    failing the whole upload - one unreadable receipt should not lose the case.

    `driver_already_named_to_operator` is parsed strictly as a string Form field
    (never a coerced bool): the string \"false\" must not become True.
    """
    case_id, rec = _new_case()
    case: CaseFile = rec["case"]
    from .disclosure import apply_disclosure
    apply_disclosure(case, driver_already_named_to_operator, source="appeal_files_form")

    rejected: list[dict] = []
    for i, upload in enumerate(files, start=1):
        evidence_id = f"E{i}"
        try:
            doc = read_upload(evidence_id, upload.filename or evidence_id,
                              upload.content_type, await upload.read())
        except UnsupportedUpload as exc:
            rejected.append({"filename": upload.filename, "reason": str(exc)})
            continue
        if not doc.readable:
            rejected.append({"filename": upload.filename, "reason": "no readable content"})
            continue
        case.evidence[evidence_id] = EvidenceItem(evidence_id, "OTHER", doc.filename,
                                                  text=doc.text, images=doc.images)
        case.audit.append({"event": "upload_read", "evidence": evidence_id,
                           "filename": doc.filename, "note": doc.note,
                           "chars": len(doc.text), "images": len(doc.images)})

    if not case.evidence:
        raise HTTPException(422, {"message": "nothing readable was uploaded", "rejected": rejected})

    _require_both_sides(case)

    payload = _run_auto(rec, narrative, None)
    payload["rejected"] = rejected
    payload["read_as"] = [{"evidence_id": e.evidence_id, "filename": e.filename,
                           "chars": len(e.text), "images": len(e.images)}
                          for e in case.evidence.values()]
    return payload


@app.post("/appeal/{case_id}")
def appeal_continue(case_id: str, body: AnswersIn):
    """Answer the questions the one-click run paused on; it then finishes itself."""
    rec = _case(case_id)
    return _run_auto(rec, "", body.answers, skip=body.skip)


@app.get("/cases/{case_id}/letter.pdf")
def letter_pdf(case_id: str):
    """The customer-facing document: a formatted letter, not the raw `letter`
    text field. 404 until the case is RELEASED - there is nothing to lay out
    before then, and laying out a MANUAL_REVIEW draft would hand a customer
    a letter nobody has approved for release."""
    from fastapi.responses import Response

    from .pdf import render_letter_pdf

    rec = _case(case_id)
    out = rec.get("output")
    if out is None or out.state != CaseState.RELEASED:
        raise HTTPException(404, "no released letter for this case yet")
    # Read from the case, not from the pack: the keeper's name and address are
    # withheld from the drafter so no sentence can contain them, which leaves the
    # letterhead as the only place they belong.
    case: CaseFile = rec["case"]
    try:
        pdf = render_letter_pdf(out.draft, out.pack, case_id,
                                evidence_list=out.evidence_list, grounds=_ground_labels(out.pack),
                                keeper_name=case.get("keeper_name"),
                                keeper_address=case.get("keeper_address"))
    except (ImportError, OSError) as exc:
        # WeasyPrint renders through Pango and cairo, which are system libraries.
        # A serverless function has no way to install them, so PDF rendering is
        # unavailable there. Say so plainly: the letter text is still complete,
        # and a 503 with a reason beats a 500 with a stack trace.
        raise HTTPException(503, {
            "message": "PDF rendering is not available on this deployment. "
                       "The letter text is complete and can be copied.",
            "detail": f"{type(exc).__name__}: {exc}"[:200],
        }) from exc
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="appeal-{case_id}.pdf"'})


def _ground_labels(pack) -> list[str]:
    routes = ([pack.primary_route] if pack.primary_route else []) + list(pack.secondary_routes)
    return [KG.routes.get(r, {}).get("label", r) for r in routes]


def _outcome_fields(out) -> dict:
    """Customer hold outcome — never module IDs or validator internals."""
    if out is None or out.state == CaseState.RELEASED:
        return {}
    if not getattr(out, "outcome", None):
        return {}
    fields = {
        "outcome": out.outcome,
        "outcome_title": out.outcome_title,
        "outcome_message": out.outcome_message,
        "outcome_next": out.outcome_next,
        "can_continue": bool(getattr(out, "can_continue", True)),
    }
    label = getattr(out, "cta_label", None)
    if label:
        fields["cta"] = {"label": label, "action": "CONTINUE_CASE"}
    return fields



# Flag kinds a customer can actually act on. Everything else extraction raises is
# a signal for us, not for them: `injection_suspected` is a security finding, and
# a customer shown a raw flag name learns nothing and worries anyway. The
# internal view of a case lives at GET /cases/{id}/trace.
CUSTOMER_FLAG_KINDS = ("uncertain", "conflict", "chronology")
# OCR hints that never gate the letter and cannot be corrected by the customer.
_CUSTOMER_FLAG_HIDE_FIELDS = frozenset({"relevant_land_hint"})


def _customer_flags(flags: list[str]) -> list[str]:
    out: list[str] = []
    for f in (flags or []):
        kind, _, field = f.partition(":")
        if kind not in CUSTOMER_FLAG_KINDS:
            continue
        if field in _CUSTOMER_FLAG_HIDE_FIELDS:
            continue
        out.append(f)
    return out


def _stop_payload(case: CaseFile) -> dict:
    """Engine 0's refusal, in the customer's words, with the service to use instead."""
    stop = scope.STOPS.get(case.scope_stop or "")
    if stop is None:
        return {}
    return {"stop_code": stop.code, "stop_reason": stop.message,
            "recommendation": stop.recommendation,
            "cta": {"label": stop.cta_label, "action": stop.cta_action}
                   if stop.cta_action else None}


def _run_auto(rec: dict[str, Any], narrative: str, answers: Optional[dict],
              skip: bool = False) -> dict:
    case: CaseFile = rec["case"]
    try:
        result = rec["pipe"].auto_appeal(case, narrative, answers, skip_remaining=skip)
    except (ValueError, TypeError) as exc:                 # bad choice / non-int answer
        raise HTTPException(422, str(exc)) from exc
    rec["questions"] = result.questions
    rec["flags"] = result.flags
    rec["output"] = result.output
    _persist(case, result.output)

    # This is the customer surface. Routes, PoFA codes, Code versions, module IDs
    # and the retrieval trace are deliberately absent - they belong to
    # GET /cases/{id}/trace, which is the internal view of the same case.
    payload = {"case_id": result.case_id, "state": result.state.value,
               "flags": _customer_flags(result.flags), "questions": result.questions,
               "skipped_questions": result.skipped_questions}
    if result.stop_reason:
        payload.update(_stop_payload(case))
        return payload
    if result.output is None:
        return payload
    out = result.output
    payload["evidence_list"] = out.evidence_list
    if out.state == CaseState.RELEASED:
        # Plain-English route labels summarising what this letter argues. Only a
        # released letter gets them: on a held case they described grounds the
        # customer never received, which read as a letter that had been written.
        payload["grounds"] = _ground_labels(out.pack)
        payload["letter"] = out.letter
        # the plain-text field above is what validation checked; this is the
        # same letter laid out as a document a customer can actually send
        payload["letter_pdf_url"] = f"/cases/{result.case_id}/letter.pdf"
    else:
        # Held cases: say *why* we stopped. Validator rule names and module IDs
        # stay on GET /cases/{id}/trace (admin/audit), not the customer payload.
        payload.update(_outcome_fields(out))
    return payload


@app.post("/cases")
def create_case():
    case_id, _ = _new_case()
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
    _persist(case)
    return {"state": case.state.value, "flags": rec["flags"],
            "doc_types": {e.evidence_id: e.kind for e in case.evidence.values()}}


@app.post("/cases/{case_id}/files")
async def upload_files(case_id: str, files: list[UploadFile] = File(...)):
    """Multipart upload into an existing case, stopping after extraction.

    `/appeal/files` creates its own case and runs straight through to a draft.
    This is the step-by-step equivalent, for a UI that shows the customer what
    was read and lets them correct it before anything is argued on their behalf.
    """
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state != CaseState.CREATED:
        raise HTTPException(409, f"documents already extracted (state {case.state.value})")

    rejected: list[dict] = []
    for i, upload in enumerate(files, start=1):
        evidence_id = f"E{i}"
        try:
            doc = read_upload(evidence_id, upload.filename or evidence_id,
                              upload.content_type, await upload.read())
        except UnsupportedUpload as exc:
            rejected.append({"filename": upload.filename, "reason": str(exc)})
            continue
        if not doc.readable:
            rejected.append({"filename": upload.filename, "reason": "no readable content"})
            continue
        case.evidence[evidence_id] = EvidenceItem(evidence_id, "OTHER", doc.filename,
                                                  text=doc.text, images=doc.images)
        case.audit.append({"event": "upload_read", "evidence": evidence_id,
                           "filename": doc.filename, "note": doc.note,
                           "chars": len(doc.text), "images": len(doc.images)})

    if not case.evidence:
        raise HTTPException(422, {"message": "nothing readable was uploaded", "rejected": rejected})

    _require_both_sides(case)

    rec["flags"] = rec["pipe"].ingest(case)
    _persist(case)
    return {"case_id": case_id, "state": case.state.value, "flags": rec["flags"],
            "rejected": rejected,
            "read_as": [{"evidence_id": e.evidence_id, "filename": e.filename,
                         "chars": len(e.text), "images": len(e.images)}
                        for e in case.evidence.values()]}


# The fields a customer is asked to check, in the order they appear on the
# notice. `notice_issue_date` is in here because it drives the PoFA deadline -
# a misread issue date silently decides whether the strongest ground exists.
CUSTOMER_FIELDS: list[tuple[str, str]] = [
    ("operator_name", "Parking company"),
    ("pcn_number", "PCN reference number"),
    ("vrm", "Vehicle registration"),
    ("parking_event_date", "Date of parking event"),
    ("notice_issue_date", "Date the notice was issued"),
    ("parking_location", "Location"),
    ("charge_amount", "Amount"),
    # A Notice to Keeper is addressed to the keeper, so these are usually on the
    # document. They are the sender block of the letter: without them the PDF is
    # not postable, which is why they are checked here rather than assumed.
    ("keeper_name", "Your name"),
    ("keeper_address", "Your address"),
]


@app.post("/cases/{case_id}/blobs")
def upload_blobs(case_id: str, body: BlobsIn):
    """Ingest documents the browser uploaded directly to blob storage.

    The multipart route above is fine behind a normal server, but a serverless
    web tier caps request bodies well below the size of a phone photo. Here the
    client uploads to storage and sends only URLs, so nothing large crosses the
    function. `ingest.fetch_upload` treats those URLs as hostile input.
    """
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state != CaseState.CREATED:
        raise HTTPException(409, f"documents already extracted (state {case.state.value})")

    rejected: list[dict] = []
    for i, blob in enumerate(body.blobs, start=1):
        evidence_id = f"E{i}"
        label = blob.filename or blob.url
        try:
            doc = fetch_upload(evidence_id, blob.url, blob.filename or None)
        except UnsupportedUpload as exc:
            rejected.append({"filename": label, "reason": str(exc)})
            continue
        if not doc.readable:
            rejected.append({"filename": label, "reason": "no readable content"})
            continue
        case.evidence[evidence_id] = EvidenceItem(evidence_id, "OTHER", doc.filename,
                                                  text=doc.text, images=doc.images,
                                                  storage_url=blob.url)
        case.audit.append({"event": "upload_read", "evidence": evidence_id,
                           "filename": doc.filename, "note": doc.note, "source": "blob",
                           "storage_url": blob.url,
                           "chars": len(doc.text), "images": len(doc.images)})

    if not case.evidence:
        raise HTTPException(422, {"message": "nothing readable was uploaded", "rejected": rejected})

    _require_both_sides(case)

    rec["flags"] = rec["pipe"].ingest(case)
    _persist(case)
    return {"case_id": case_id, "state": case.state.value, "flags": rec["flags"],
            "rejected": rejected,
            "read_as": [{"evidence_id": e.evidence_id, "filename": e.filename,
                         "chars": len(e.text), "images": len(e.images)}
                        for e in case.evidence.values()]}


@app.get("/cases/{case_id}/confirmation")
def confirmation_screen(case_id: str):
    """Extracted facts + flags for the customer to confirm or correct.

    `details` is the curated, labelled, ordered subset a customer should check;
    `facts` is everything, for the reviewer console.
    """
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    details = []
    for name, label in CUSTOMER_FIELDS:
        fact = case.facts.get(name)
        details.append({
            "name": name,
            "label": label,
            "value": "" if fact is None else str(fact.value),
            # UNCERTAIN and missing both mean "we could not rely on this", which
            # is what the UI should draw attention to.
            "needs_attention": fact is None or not fact.usable,
        })
    return {"case_id": case.case_id, "state": case.state.value, "flags": rec["flags"],
            "details": details,
            "facts": [{"name": f.name, "value": str(f.value), "status": f.status.value,
                       "confidence": f.confidence, "source": f.source.ref}
                      for f in case.facts.values()]}


@app.post("/cases/{case_id}/confirm")
def confirm(case_id: str, body: ConfirmIn):
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    from .disclosure import apply_disclosure
    apply_disclosure(case, body.driver_already_named_to_operator, source="cases_confirm")

    from .notice_completeness import incompleteness_payload, requires_complete_notice
    # Server-side completeness gate for in-scope private parking. Preserve answers;
    # do not run merits analysis on a front-only notice.
    if requires_complete_notice(case):
        # Still accept narrative into audit so the account is not lost.
        if body.narrative:
            case.raw_answers["narrative"] = body.narrative
        case.audit.append({"event": "blocked_notice_sides_incomplete",
                           "stage": "confirm"})
        _persist(case)
        return incompleteness_payload(case)

    rec["questions"] = rec["pipe"].confirm(case, body.corrections, body.confirmed, body.narrative)
    _persist(case)
    if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
        # Read the wording from the scope table rather than restating it here:
        # the inline copy only ever described debt recovery, so a council PCN on
        # this route was told to use the Debt Recovery Letter service.
        return {"case_id": case.case_id, "state": case.state.value, "questions": [],
                "flags": [], "skipped_questions": [], **_stop_payload(case)}
    # Material questions remain — pause for answers (same shape as auto_appeal pause).
    if rec["questions"]:
        return {"case_id": case.case_id, "state": case.state.value,
                "flags": _customer_flags(rec.get("flags") or []),
                "questions": rec["questions"], "skipped_questions": []}
    # Nothing material left to ask — finish the letter now. Previously the step-by-step
    # UI called /confirm only and never /generate, so question-free cases never drafted.
    out = rec["pipe"].generate(case)
    rec["output"] = out
    _persist(case, out)
    payload = {"case_id": case.case_id, "state": out.state.value,
               "flags": _customer_flags(rec.get("flags") or []), "questions": [],
               "skipped_questions": [], "evidence_list": out.evidence_list}
    if out.state == CaseState.RELEASED:
        payload["grounds"] = _ground_labels(out.pack)      # see /auto_appeal
        payload["letter"] = out.letter
        payload["letter_pdf_url"] = f"/cases/{case.case_id}/letter.pdf"
    else:
        payload.update(_outcome_fields(out))
    return payload


@app.post("/cases/{case_id}/disclosure")
def correct_disclosure_status(
    case_id: str,
    body: DisclosureCorrectionIn,
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Auditable correction of external driver-disclosure status.

    Requires ADMIN_TRACE_TOKEN when configured. Does not blanket-reset cases.
    """
    import os
    expected = os.getenv("ADMIN_TRACE_TOKEN") or os.getenv("ADMIN_TOKEN")
    if expected and x_admin_token != expected:
        raise HTTPException(403, "admin token required")
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    from .disclosure import correct_disclosure
    if not body.reason.strip():
        raise HTTPException(422, "reason is required for an auditable correction")
    status = correct_disclosure(case, body.status, reason=body.reason.strip(), actor="admin")
    _persist(case)
    return {
        "case_id": case.case_id,
        "disclosure_status": status,
        "driver_status": case.driver_status.value,
    }


@app.post("/cases/{case_id}/answers")
def answer(case_id: str, body: AnswersIn):
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    try:
        rec["questions"] = rec["pipe"].answer(case, body.answers)
        _persist(case)
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
    _persist(case, out)
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
def get_trace(case_id: str, authorization: Optional[str] = Header(None)):
    """Why the system argued what it argued — admin/audit view only.

    Customer UI must not call this. When ADMIN_TRACE_TOKEN is set, require
    `Authorization: Bearer <token>`; otherwise the endpoint stays available to
    operators with network access to the API (no public customer surface).
    """
    import os
    token = (os.environ.get("ADMIN_TRACE_TOKEN") or "").strip()
    if token:
        expected = f"Bearer {token}"
        if (authorization or "") != expected:
            raise HTTPException(401, "admin authorization required")
    rec = _case(case_id)
    out = rec["output"]
    if out is None:
        raise HTTPException(409, "not generated yet - POST /generate")
    return {"trace": out.pack.trace, "module_ids": out.pack.module_ids,
            "missing_facts": out.pack.missing_facts, "prohibited_claims": out.pack.prohibited_claims,
            "sentences": [{"text": s.text, "fact_refs": s.fact_refs, "module_refs": s.module_refs,
                           "evidence_refs": s.evidence_refs} for s in out.draft.sentences()],
            "audit": rec["case"].audit,
            "outcome": getattr(out, "outcome", None)}



# Admin (role: legal_admin) - edit without redeploys (Dev Pack Phase 10).
# Unimplemented deliberately: these are the highest-risk endpoints in the system
# and need RBAC, dual control and the scenario suite as a publish gate first.
@app.put("/admin/kb/modules/{module_id}")
def upsert_module(module_id: str, body: dict):
    raise HTTPException(501, "not implemented: needs RBAC + dual control + publish gate")


@app.post("/admin/kb/releases")
def publish_release():
    raise HTTPException(501, "not implemented: must run the scenario suite and publish only if green")
