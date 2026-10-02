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

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import config, customer_safe, manifest, prompts, runtime, version
from .ingest import MAX_BYTES, UnsupportedUpload, fetch_upload, read_upload
from .kg.graph import KnowledgeGraph
from .llm import default_client
from .models import CaseFile, CaseState, EvidenceItem
from .notice_completeness import BOTH_SIDES_MESSAGE
from .engines import question_authority
from .hypotheses import Hypotheses
from .orchestrator import AppealPipeline
from .store import db


def _intake(rec: dict[str, Any], enforce_completeness: bool = True) -> Optional[dict]:
    """Classify the upload and route it, before any service reads it.

    Returns the customer payload when the case stops here (a redirect, a stage
    this service does not take, or a classifier failure), or None to continue
    into the private parking engine - the only live service.

    A failed completeness check for the route keeps the case CREATED and clears
    the upload, so the customer can retry on the same case without a 409. That
    check is the route's own: a debt letter is never asked for "both sides".
    `enforce_completeness=False` is for the JSON text routes, which carry no
    page images and never had the upload page check.
    """
    from .intake import run_intake
    case: CaseFile = rec["case"]
    case.ensure_run("intake")
    result = run_intake(case, rec["pipe"].extraction.llm)
    if result.stop is not None:
        _persist(case)
        return _stopped_payload(case)
    if enforce_completeness and not result.check.ok:
        _reject_incomplete(case, result.check.reason, result.check.policy)
    return None


def _require_both_sides(case: CaseFile) -> None:
    """The private parking route's upload rule: front and reverse as distinct
    pages, or a multipage PDF. Raises the 422 below when they are missing."""
    from .services.private_parking import FRONT_AND_BACK
    ok, reason = FRONT_AND_BACK.check(case)
    if not ok:
        _reject_incomplete(case, reason, FRONT_AND_BACK.name)


def _reject_incomplete(case: CaseFile, reason: str, policy: str) -> None:
    """Clear the upload so the same case stays CREATED and the customer can
    retry from the upload screen without a 409, and forget the intake decision
    so the retry is classified afresh."""
    from .intake import reset
    from .notice_completeness import different_notices
    # Which reference differed, read before the reset forgets the classifications.
    differed = different_notices(case) if reason == "different_notices" else None
    case.evidence.clear()
    case.audit.append({"event": "upload_rejected_incomplete_sides", "reason": reason,
                       "policy": policy, "route": case.route,
                       **({"differed": differed} if differed else {})})
    reset(case)
    from .notice_completeness import rejection_message
    # The customer gets the message and a stable code; which check refused the
    # upload (`reason`, `differed`) is routing logic and stays in the audit.
    raise HTTPException(422, {
        "message": rejection_message(reason),
        "code": "NOTICE_SIDES_REQUIRED",
    })


def _stopped_at_intake(case: CaseFile) -> bool:
    """Whether intake already ended this case: a route with no engine, a stage
    the private service does not take, or a classifier failure."""
    from .services import intake_stop
    if case.route is not None:
        return intake_stop(case) is not None
    return case.state == CaseState.CLASSIFICATION_FAILED


def _stopped_payload(case: CaseFile) -> dict:
    return {"case_id": case.case_id, "state": case.state.value, "route": case.route,
            "questions": [], "flags": [], "skipped_questions": [], **_stop_payload(case)}


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
    _verify_provider_at_startup()
    yield


def _verify_provider_at_startup() -> None:
    """Production refuses to start on a provider it cannot use.

    Without this a missing or rejected key surfaced only when the first customer
    uploaded a notice - or, before the provider policy, not at all: the case ran
    on the demo stand-in. Failing here keeps a bad deploy from being promoted.
    """
    if not runtime.is_production():
        return
    from .llm import redact
    try:
        default_client()
    except Exception as exc:
        raise RuntimeError(
            f"refusing to start in production: LLM provider unusable ({redact(str(exc))})") from exc


app = FastAPI(title="PCN Appeal AI", version="2.0", lifespan=lifespan)


# The customer journey, as the public proxy allows it (frontend/app/api/[...path]/route.ts),
# plus GET /cases/{id}. Every JSON body on these routes - results, holds,
# questions, refusals and errors alike - passes customer_safe.scrub. Operator
# routes are not listed: they are the internal view and need the admin token.
CUSTOMER_ROUTES: tuple[tuple[str, re.Pattern], ...] = (
    ("POST", re.compile(r"^/appeal(/files|/[^/]+)?$")),
    ("POST", re.compile(r"^/cases$")),
    ("POST", re.compile(r"^/cases/[^/]+/(files|blobs|confirm)$")),
    ("GET", re.compile(r"^/cases/[^/]+(/confirmation|/letter\.pdf)?$")),
)


def is_customer_route(method: str, path: str) -> bool:
    return any(m == method and rx.match(path) for m, rx in CUSTOMER_ROUTES)


@app.middleware("http")
async def frontend_version_header(request, call_next):
    """Which frontend build sent this request, for the execution manifest.
    Bounded and printable only: it is a client-supplied header."""
    raw = (request.headers.get("x-frontend-version") or "").strip()
    value = re.sub(r"[^A-Za-z0-9._:+-]", "", raw)[:64] or "unknown"
    token = manifest.FRONTEND_VERSION.set(value)
    try:
        return await call_next(request)
    finally:
        manifest.FRONTEND_VERSION.reset(token)


@app.middleware("http")
async def customer_safe_responses(request, call_next):
    response = await call_next(request)
    if not is_customer_route(request.method, request.url.path):
        return response
    if "application/json" not in (response.headers.get("content-type") or ""):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        payload = json.loads(body or b"null")
    except ValueError:
        return Response(body, status_code=response.status_code, headers=dict(response.headers))
    clean = customer_safe.scrub(payload, where=f"{request.method} {request.url.path}")
    headers = {k: v for k, v in response.headers.items()
               if k.lower() not in ("content-length", "content-type")}
    return JSONResponse(clean, status_code=response.status_code, headers=headers)


# Start anyway when the KB source cannot be trusted: an unreachable release
# table (serves the authored YAML) or a release that disagrees with the YAML in
# this build (serves the release, and /health reports the drift). Without it
# either case stops the API, which is the point of this gate - but an operator
# still needs a deliberate, logged way to bring the surface up.
ALLOW_KB_DRIFT = (os.getenv("ALLOW_KB_DRIFT") or "").strip().lower() in ("1", "true", "yes")

# What the running process is actually serving, for /health.
KB_STATUS: dict[str, Any] = {"source": "yaml", "reason": "no database configured", "drift": []}


def _load_kg() -> KnowledgeGraph:
    """Serve the published Postgres release when there is one; fall back to the
    authored YAML so the dev surface works with no database.

    A release that EXISTS but cannot be served, or that disagrees with the
    authored YAML, is a hard failure rather than a fallback. Quietly serving YAML
    instead is how the app ends up arguing different law from the release every
    case is stamped with - which is exactly how a client came to be retesting a
    version nobody could identify.
    """
    if not db.enabled():
        return KnowledgeGraph()

    from .store import kb_source
    try:
        released = kb_source.latest_release_id()
    except Exception as exc:
        if not ALLOW_KB_DRIFT:
            raise RuntimeError(
                f"cannot reach the KB release table ({exc}). The database is the system of "
                "record when DATABASE_URL is set, so serving the YAML here would argue "
                "unverified law. Fix the database, or set ALLOW_KB_DRIFT=1 to serve the "
                "authored YAML deliberately.") from exc
        KB_STATUS.update(source="yaml", reason=f"release table unreachable: {exc}", drift=[])
        print(f"[kb] release table unreachable ({exc}); serving YAML by ALLOW_KB_DRIFT")
        return KnowledgeGraph()

    if released is None:
        KB_STATUS.update(source="yaml", reason="no KB release published", drift=[])
        print("[kb] no KB release published; serving YAML "
              "(publish with `python -m pcn_appeal.store sync`)")
        return KnowledgeGraph()

    release = kb_source.load_release()
    drift = kb_source.release_differs_from_yaml(release)
    if drift and not ALLOW_KB_DRIFT:
        raise RuntimeError(
            f"KB release {release['release_id']} does not match the authored YAML in this "
            f"build:\n  - " + "\n  - ".join(drift)
            + "\nRepublish with `python -m pcn_appeal.store sync --publish`, deploy the build "
              "the release was cut from, or set ALLOW_KB_DRIFT=1 to override.")
    # The release pins a prompt version per task. Without this the pinned prompts
    # were fetched and then ignored, so the app ran YAML prompts while stamping
    # every case with a release that named different ones.
    prompts.use_release(release["prompts"])
    KB_STATUS.update(source="postgres-release", reason="", drift=drift)
    return KnowledgeGraph.from_release(release)


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


PROVIDER_UNAVAILABLE = {
    "code": "PROCESSING_ERROR",
    "message": "Our appeal service is temporarily unavailable. Nothing has been decided "
               "about your parking charge - please try again in a few minutes.",
}


def _pipeline() -> AppealPipeline:
    """A pipeline on the configured provider, or a customer-safe 503.

    A provider that cannot be built is our processing failure. It must never
    become a case silently run on the demo stand-in, and never a merits outcome.
    """
    try:
        client = default_client()
    except Exception as exc:
        from .llm import redact
        print(f"[llm] provider unavailable: {redact(str(exc))}")
        raise HTTPException(503, PROVIDER_UNAVAILABLE) from exc
    return AppealPipeline(client, kg=KG)


def _require_admin(authorization: Optional[str], x_admin_token: Optional[str]) -> None:
    """Operator-only routes: trace, console, disclosure correction and the
    step routes that return module ids, validator ids and PoFA internals.

    With ADMIN_TRACE_TOKEN (or ADMIN_TOKEN) set, the caller must present it as
    `Authorization: Bearer <token>` or `X-Admin-Token: <token>`. With no token
    configured these routes stay open in development and are CLOSED in
    production - an unset secret must not mean "public".
    """
    token = (os.getenv("ADMIN_TRACE_TOKEN") or os.getenv("ADMIN_TOKEN") or "").strip()
    if not token:
        if runtime.is_production():
            raise HTTPException(403, "admin endpoints are disabled: no admin token configured")
        return
    presented = [f"Bearer {token}", token]
    offered = [authorization or "", x_admin_token or ""]
    if not any(hmac.compare_digest(o.encode(), p.encode())
               for o, p in zip(offered, presented) if o):
        raise HTTPException(401, "admin authorization required")


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
    try:
        # The letter already released, so the PDF is the one the customer saw.
        output = case_store.load_output(case)
    except Exception as exc:
        output = None
        case.audit.append({"event": "output_rehydrate_failed", "reason": str(exc)[:200]})
    rec = {"case": case, "pipe": _pipeline(),
           "flags": [], "questions": list(case.pending_questions), "output": output}
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
    pipe = _pipeline()                   # before the case row: no orphan on a 503
    if db.enabled():
        from .store import cases as case_store
        case = case_store.new_case(kb_release_id=KG.release_id)
    else:
        case = CaseFile(f"C-{len(CASES) + 1:04d}")
    case.frontend_version = manifest.FRONTEND_VERSION.get()
    rec = {"case": case, "pipe": pipe,
           "flags": [], "questions": [], "output": None}
    CASES[case.case_id] = rec
    return case.case_id, rec


WEB = Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
def customer_app():
    """Customer journey: upload -> answer what is missing -> letter."""
    return FileResponse(WEB / "index.html", media_type="text/html")


@app.get("/console", include_in_schema=False)
def reviewer_console(authorization: Optional[str] = Header(None),
                     x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Reviewer view: facts, confidence, decision trace, per-sentence provenance."""
    _require_admin(authorization, x_admin_token)
    return FileResponse(WEB / "console.html", media_type="text/html")


@app.get("/health")
def health():
    """What this process is actually running, so a retest can be trusted.

    `commit` is the answer to "is my fix deployed?" - it was previously
    unanswerable from the running app, and had to be inferred by eye during an
    incident. `kb_source` says whether the served law came from a published
    release or the authored YAML, and `prompt_versions` which instructions the
    drafter and validator are really using.
    """
    from .engines import validation
    from .llm import probe
    p = probe()
    # Production on anything but the real provider is not a healthy service,
    # whatever else works: it cannot produce a letter anyone should receive.
    healthy = p["provider"] == "openai" or not runtime.is_production()
    body = {"status": "ok" if healthy else "unhealthy",
            "modules": len(KG.modules), "blocks": len(KG.blocks),
            "environment": runtime.environment(), "build_id": runtime.build_id(),
            "commit": version.commit(),
            "kb_release": KG.release_id, "kb_source": KB_STATUS["source"],
            "kb_source_note": KB_STATUS["reason"], "kb_drift": KB_STATUS["drift"],
            "prompt_versions": prompts.versions(), "validator_version": validation.VERSION,
            "store": "postgres" if db.enabled() else "memory",
            "provider": p["provider"], "models": p["models"], "provider_note": p["reason"],
            # False means a photographed notice or scanned PDF will yield no facts,
            # so the UI can say so before the customer uploads one.
            # Only the OpenAI client reads images (DemoLLM.SUPPORTS_IMAGES is
            # False). Derived from the probe rather than building a second client,
            # which in production raises when the provider is unusable.
            "vision": p["provider"] == "openai",
            "max_upload_bytes": MAX_BYTES}
    return body if healthy else JSONResponse(body, status_code=503)


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
    if not case.evidence:
        raise HTTPException(422, {"message": "no documents were supplied", "rejected": []})
    stopped = _intake(rec, enforce_completeness=False)
    if stopped is not None:
        return stopped
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

    payload = _intake(rec) or _run_auto(rec, narrative, None)
    payload["rejected"] = rejected
    payload["read_as"] = _read_as(case)
    return payload


@app.post("/appeal/{case_id}")
def appeal_continue(case_id: str, body: AnswersIn):
    """Answer the questions the one-click run paused on; it then finishes itself."""
    rec = _case(case_id)
    if _stopped_at_intake(rec["case"]):
        return _stopped_payload(rec["case"])
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
        case.audit.append({"event": "pdf_render_failed",
                           "error": f"{type(exc).__name__}: {exc}"[:200]})
        raise HTTPException(503, {
            "message": "PDF rendering is not available on this deployment. "
                       "The letter text is complete and can be copied.",
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


def _cta(stop) -> Optional[dict]:
    """The stop's call to action. `action` is a stable key the frontend maps to
    its own page; `href` is added only where a deployment configures one
    (CTA_URL_<ACTION>, a site path or an https URL), e.g. the Resources template."""
    if not stop.cta_action:
        return None
    cta = {"label": stop.cta_label, "action": stop.cta_action}
    href = (os.getenv(f"CTA_URL_{stop.cta_action}") or "").strip()
    if href.startswith("/") or href.startswith("https://"):
        cta["href"] = href
    return cta


def _stop_payload(case: CaseFile) -> dict:
    """The refusal or redirect, in the customer's words, with where to go instead."""
    from .services import stop_by_code
    stop = stop_by_code(case.scope_stop or "")
    if stop is None:
        return {}
    return {"stop_code": stop.code, "stop_title": stop.title, "stop_reason": stop.message,
            "recommendation": stop.recommendation, "cta": _cta(stop)}


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
    payload = {"case_id": result.case_id, "state": result.state.value, "route": case.route,
               "flags": _customer_flags(result.flags),
               "questions": customer_safe.customer_questions(result.questions),
               "skipped_questions": customer_safe.customer_questions(result.skipped_questions)}
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
        payload.update(_held_questions(rec["case"], out))
    return payload


def _held_questions(case: CaseFile, out) -> dict:
    """A NEEDS_FACTS hold carries the question that would unblock it, so the
    customer can answer it on this case (it was already shown once and skipped)."""
    if out.outcome == "NEEDS_FACTS" and case.pending_questions:
        return {"questions": customer_safe.customer_questions(case.pending_questions)}
    return {}


@app.post("/cases")
def create_case():
    case_id, _ = _new_case()
    return {"case_id": case_id, "state": CaseState.CREATED.value}


@app.post("/cases/{case_id}/documents")
def upload(case_id: str, body: DocumentsIn,
           authorization: Optional[str] = Header(None),
           x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Production: virus scan -> S3 -> enqueue extraction. Here: inline.

    Operator route (console): takes client-supplied text and document kinds and
    skips the upload page check, so it is not a customer entry point.
    """
    _require_admin(authorization, x_admin_token)
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state != CaseState.CREATED:
        raise HTTPException(409, f"documents already extracted (state {case.state.value})")
    for d in body.documents:
        case.evidence[d.evidence_id] = EvidenceItem(d.evidence_id, d.kind, d.filename, text=d.text)
    if not case.evidence:
        raise HTTPException(422, "no documents were supplied")
    stopped = _intake(rec, enforce_completeness=False)
    if stopped is not None:
        return {**stopped, "doc_types": {}}
    rec["flags"] = _private_service(rec).extract_service_facts(case)
    _persist(case)
    return {"state": case.state.value, "route": case.route, "flags": rec["flags"],
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

    return _ingest_upload(rec, rejected)


def _read_as(case: CaseFile) -> list[dict]:
    return [{"evidence_id": e.evidence_id, "filename": e.filename,
             "chars": len(e.text), "images": len(e.images)} for e in case.evidence.values()]


def _private_service(rec: dict[str, Any]):
    from .services.private_parking import PrivateParkingService
    return PrivateParkingService(rec["pipe"])


def _ingest_upload(rec: dict[str, Any], rejected: list[dict]) -> dict:
    """After the files are read: intake, then the private engine's extraction
    only if intake routed the case there."""
    case: CaseFile = rec["case"]
    stopped = _intake(rec)
    if stopped is not None:
        return {**stopped, "rejected": rejected, "read_as": _read_as(case)}
    rec["flags"] = _private_service(rec).extract_service_facts(case)
    _persist(case)
    return {"case_id": case.case_id, "state": case.state.value, "route": case.route,
            "flags": _customer_flags(rec["flags"]), "rejected": rejected, "read_as": _read_as(case)}


@app.get("/cases/{case_id}")
def case_status(case_id: str):
    """Where a case is: its state, the route intake chose, and - when it stopped
    - the customer-safe explanation. No facts, module ids or reasoning."""
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    body = {"case_id": case.case_id, "state": case.state.value, "route": case.route,
            "document_type": case.document_type, "stage": case.stage}
    if case.scope_stop:
        body.update(_stop_payload(case))
    return body


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

    return _ingest_upload(rec, rejected)


@app.get("/cases/{case_id}/confirmation")
def confirmation_screen(case_id: str):
    """The extracted details for the customer to confirm or correct: the
    curated, labelled, ordered subset in CUSTOMER_FIELDS, and the flags a
    customer can act on. The full fact list is GET /cases/{id}/facts (admin)."""
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
    return {"case_id": case.case_id, "state": case.state.value,
            "flags": _customer_flags(rec["flags"]), "details": details}


_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _public(rows: list[dict]) -> list[dict]:
    """Graph records without their working keys ("_held", "_persisted")."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


@app.get("/cases/{case_id}/facts")
def case_facts(case_id: str, authorization: Optional[str] = Header(None),
               x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """The case's Fact Graph: every fact node with its source type, status,
    owner and confidence; every reading of each fact (sources); conflicts; and
    the raw flags - the reviewer console's view. Operator route: none of this
    is customer output (it was on /confirmation until P0.1)."""
    from . import fact_graph
    _require_admin(authorization, x_admin_token)
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    return {"case_id": case.case_id, "state": case.state.value, "flags": rec["flags"],
            "facts": [{"name": f.name, "value": str(f.value), "status": f.status.value,
                       "confidence": f.confidence, "source": f.source.ref}
                      for f in case.facts.values()],
            "nodes": fact_graph.nodes(case),
            "fact_sources": _public(case.fact_sources),
            "fact_conflicts": _public(case.fact_conflicts),
            "needs_confirmation": [c["fact"] for c in
                                   fact_graph.FactManager.needs_confirmation(case)],
            "fact_hypotheses": _public(case.fact_hypotheses),
            "hypothesis_trace": Hypotheses.trace(case),
            # P3: every question decision, approved or rejected, and why.
            "question_trace": question_authority.trace(case)}


class FactWriteIn(BaseModel):
    """An operator write. Either a fact (`fact_name` + `value`) or the
    settlement of a conflict (`conflict_id` + `value`). `reason` is required:
    every write is in the fact's history."""
    reason: str
    value: Any = None
    fact_name: Optional[str] = None
    conflict_id: Optional[str] = None


@app.post("/cases/{case_id}/facts")
def write_fact(case_id: str, body: FactWriteIn, authorization: Optional[str] = Header(None),
               x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Internal Fact API write. Goes through FactManager like every other
    write, so an operator cannot silently overwrite a document reading either:
    the same ownership rules apply, and a conflict is settled by naming it."""
    from . import fact_graph
    from .models import Fact, FactSource, FactStatus, SourceKind
    from .store.cases import _revived
    _require_admin(authorization, x_admin_token)
    if not body.reason.strip():
        raise HTTPException(422, "reason is required")
    if bool(body.fact_name) == bool(body.conflict_id):
        raise HTTPException(422, "give fact_name or conflict_id")
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    value = _revived(body.value)
    if body.conflict_id:
        try:
            result = fact_graph.FactManager.resolve_conflict(
                case, body.conflict_id, value, changed_by="admin", reason=body.reason.strip())
        except KeyError:
            raise HTTPException(404, "no open conflict with that id")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    else:
        name = fact_graph.canonical(body.fact_name.strip())
        result = fact_graph.FactManager.update_fact(
            case, Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                       FactSource(SourceKind.ANSWER, f"admin:{name}")),
            reason=body.reason.strip(), changed_by="admin")
    case.audit.append({"event": "fact_api_write", "outcome": result.outcome,
                       "fact": body.fact_name or (result.conflict or {}).get("fact")})
    _persist(case)
    conflict = result.conflict and {k: v for k, v in result.conflict.items()
                                    if not k.startswith("_")}
    fact = (result.conflict or {}).get("fact") or fact_graph.canonical(body.fact_name or "")
    return {"outcome": result.outcome, "conflict": conflict,
            "fact": fact_graph.node(case, fact)}


@app.get("/facts/{fact_id}/history")
def fact_history(fact_id: str, authorization: Optional[str] = Header(None),
                 x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Every write to one fact node, oldest first: previous and new value,
    source, who, why, when and whether it was applied."""
    from . import fact_graph
    _require_admin(authorization, x_admin_token)
    case = next((r["case"] for r in CASES.values() if r["case"].facts.name_of(fact_id)), None)
    if case is None and db.enabled() and _UUID.fullmatch(fact_id):
        with db.connect() as conn:
            row = conn.execute("SELECT case_id FROM facts WHERE fact_id = %s",
                               (fact_id,)).fetchone()
        if row is not None:
            case = _case(str(row[0]))["case"]
    if case is None:
        raise HTTPException(404, "unknown fact")
    name = case.facts.name_of(fact_id)
    if name is None:
        raise HTTPException(404, "unknown fact")
    return {"fact_id": fact_id, "case_id": case.case_id, "fact_name": name,
            "current": fact_graph.node(case, name),
            "history": _public(fact_graph.history_of(case, fact_id))}


@app.post("/cases/{case_id}/confirm")
def confirm(case_id: str, body: ConfirmIn):
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    if _stopped_at_intake(case):
        # No private-parking step runs on a case intake routed elsewhere.
        if body.narrative:
            case.raw_answers["narrative"] = body.narrative
        _persist(case)
        return _stopped_payload(case)
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
        # Read the wording from the stop tables rather than restating it here:
        # the inline copy only ever described debt recovery, so a council PCN on
        # this route was told to use the Debt Recovery Letter service.
        return _stopped_payload(case)
    # Material questions remain — pause for answers (same shape as auto_appeal pause).
    if rec["questions"]:
        return {"case_id": case.case_id, "state": case.state.value,
                "flags": _customer_flags(rec.get("flags") or []),
                "questions": customer_safe.customer_questions(rec["questions"]),
                "skipped_questions": []}
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
        payload.update(_held_questions(case, out))
    return payload


@app.post("/cases/{case_id}/disclosure")
def correct_disclosure_status(
    case_id: str,
    body: DisclosureCorrectionIn,
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None),
):
    """Auditable correction of external driver-disclosure status.

    Operator route (see _require_admin). Does not blanket-reset cases.
    """
    _require_admin(authorization, x_admin_token)
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
def answer(case_id: str, body: AnswersIn,
           authorization: Optional[str] = Header(None),
           x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Operator route (console). Customers answer through POST /appeal/{id}."""
    _require_admin(authorization, x_admin_token)
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    if _stopped_at_intake(case):
        return _stopped_payload(case)
    try:
        rec["questions"] = rec["pipe"].answer(case, body.answers)
        _persist(case)
    except (ValueError, TypeError) as exc:                 # bad choice / non-int answer
        raise HTTPException(422, str(exc)) from exc
    return {"state": case.state.value,
            "questions": customer_safe.customer_questions(rec["questions"])}


@app.post("/cases/{case_id}/generate")
def generate(case_id: str,
             authorization: Optional[str] = Header(None),
             x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Operator route (console): returns route ids and validator issues."""
    _require_admin(authorization, x_admin_token)
    rec = _case(case_id)
    case: CaseFile = rec["case"]
    if case.state == CaseState.CREATED:
        raise HTTPException(409, "upload documents first")
    if _stopped_at_intake(case):
        return _stopped_payload(case)
    out = rec["pipe"].generate(case)
    rec["output"] = out
    _persist(case, out)
    return {"state": out.state.value, "primary_route": out.pack.primary_route,
            "secondary_routes": out.pack.secondary_routes,
            "blocking_issues": [asdict(i) for i in out.validation.issues if i.severity == "BLOCK"]}


@app.get("/cases/{case_id}/appeal")
def get_appeal(case_id: str,
               authorization: Optional[str] = Header(None),
               x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Operator route (console): returns module ids, PoFA findings and
    validator issues. Customers receive their letter from POST /appeal*."""
    _require_admin(authorization, x_admin_token)
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
            "code_version": out.pack.code_version, "module_ids": out.pack.module_ids,
            "manifest": getattr(out, "manifest", None)}


@app.get("/cases/{case_id}/trace")
def get_trace(case_id: str, authorization: Optional[str] = Header(None),
              x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Why the system argued what it argued — admin/audit view only.

    Customer UI must not call this. Operator route (see _require_admin): it
    was open whenever ADMIN_TRACE_TOKEN was unset, which in production it was.
    """
    _require_admin(authorization, x_admin_token)
    rec = _case(case_id)
    out = rec["output"]
    if out is None:
        raise HTTPException(409, "not generated yet - POST /generate")
    return {"trace": out.pack.trace, "module_ids": out.pack.module_ids,
            "missing_facts": out.pack.missing_facts, "prohibited_claims": out.pack.prohibited_claims,
            "sentences": [{"text": s.text, "fact_refs": s.fact_refs, "module_refs": s.module_refs,
                           "evidence_refs": s.evidence_refs} for s in out.draft.sentences()],
            "audit": rec["case"].audit,
            "outcome": getattr(out, "outcome", None),
            "run_id": rec["case"].run_id,
            "manifest": getattr(out, "manifest", None),
            "fact_conflicts": _public(rec["case"].fact_conflicts),
            # P2: narrative -> hypothesis -> question -> answer -> final fact.
            "hypotheses": Hypotheses.trace(rec["case"]),
            # P3: candidate -> module -> target fact -> approved/rejected -> reason.
            "question_trace": question_authority.trace(rec["case"])}



# Admin (role: legal_admin) - edit without redeploys (Dev Pack Phase 10).
# Unimplemented deliberately: these are the highest-risk endpoints in the system
# and need RBAC, dual control and the scenario suite as a publish gate first.
@app.put("/admin/kb/modules/{module_id}")
def upsert_module(module_id: str, body: dict):
    raise HTTPException(501, "not implemented: needs RBAC + dual control + publish gate")


@app.post("/admin/kb/releases")
def publish_release():
    raise HTTPException(501, "not implemented: must run the scenario suite and publish only if green")
