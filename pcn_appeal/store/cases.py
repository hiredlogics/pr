"""Case persistence: cases, evidence, facts, raw answers, drafts, validations, audit.

Replaces the process dict in api.py when DATABASE_URL is set, so a restart or a
second worker does not lose a customer's case.

Two schema properties are load-bearing and this module preserves them:

  * `facts` is APPEND-ONLY. A corrected value inserts a new row and marks the
    previous one `superseded`, so the confirmation screen's history survives and
    an appeal can be audited against what was known when it was drafted.
  * `raw_answers` holds the customer's own wording. load() restores it to the
    CaseFile, as the process that received it held it, so the account engine can
    re-read it; the drafting path still sees only normalised Facts (rule Q-06).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from ..models import (CaseFile, CaseState, DriverStatus, EvidenceItem, Fact, FactSource,
                      FactStatus, SourceKind)
from .db import connect

RETENTION_DAYS = int(os.getenv("CASE_RETENTION_DAYS", "365"))
DEV_CUSTOMER_ID = os.getenv("DEV_CUSTOMER_ID", "00000000-0000-0000-0000-000000000001")


def new_case(customer_id: Optional[str] = None, kb_release_id: Optional[str] = None) -> CaseFile:
    """Insert a row and return a CaseFile whose case_id IS the database uuid.

    `commit_sha` and `llm_provider` are recorded at creation so a case can be
    tied to the code and the provider that handled it. Without them, confirming
    that a deployed fix was the one a case actually ran on means guessing.
    """
    from .. import version
    from ..llm import probe
    case_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("""
            INSERT INTO cases (case_id, customer_id, state, driver_status, kb_release_id,
                               commit_sha, llm_provider, retention_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (case_id, customer_id or DEV_CUSTOMER_ID, CaseState.CREATED.value,
              DriverStatus.UNIDENTIFIED.value, kb_release_id,
              version.commit(), probe()["provider"],
              date.today() + timedelta(days=RETENTION_DAYS)))
        conn.commit()
    return CaseFile(case_id)


def save(case: CaseFile) -> None:
    """Write the whole case through. Facts are appended; evidence and state are
    upserted. Small enough to do in one transaction per step.

    The test of this function is that load() gives back the case the pipeline
    was holding: a second worker, or this one after a restart, must carry on
    exactly where the first stopped."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE cases SET state = %s, driver_status = %s, route = %s,
                                 document_type = %s, stage = %s, scope_stop = %s,
                                 document_classes = %s, classifications = %s, timeline = %s,
                                 asked_questions = %s, pending_questions = %s,
                                 current_run_id = %s, run_status = %s, frontend_version = %s
                WHERE case_id = %s
            """, (case.state.value, case.driver_status.value, *routing_columns(case),
                  *question_columns(case), case.run_id, case.run_status,
                  case.frontend_version, case.case_id))

            for ev in case.evidence.values():
                cur.execute("""
                    INSERT INTO evidence (evidence_id, case_id, label, kind, s3_key, sha256,
                                          ocr_text, filename)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (evidence_id) DO UPDATE SET
                        kind = EXCLUDED.kind, ocr_text = EXCLUDED.ocr_text,
                        s3_key = EXCLUDED.s3_key, filename = EXCLUDED.filename
                """, (_as_uuid(ev.evidence_id, case.case_id), case.case_id, ev.evidence_id,
                      # s3_key is where the file IS; filename is what the customer
                      # called it. Putting the filename in s3_key - as this did -
                      # loses the only pointer back to the document.
                      ev.kind, ev.storage_url, _sha(ev.text), ev.text, ev.filename))
            # A document the case no longer holds (an incomplete upload that was
            # rejected and cleared) must not come back on the next load.
            for (label,) in cur.execute("SELECT label FROM evidence WHERE case_id = %s",
                                        (case.case_id,)).fetchall():
                if label not in case.evidence:
                    cur.execute("DELETE FROM evidence WHERE case_id = %s AND label = %s",
                                (case.case_id, label))
            _save_pages(cur, case)

            known = {r[0]: tuple(r[1:]) for r in cur.execute(
                "SELECT name, value, status, source_kind, source_ref FROM facts "
                "WHERE case_id = %s AND NOT superseded", (case.case_id,)).fetchall()}
            for name, f in case.facts.items():
                serialised = _jsonable(f.value)
                # Status and source are part of the fact: confirming a value the
                # extractor read changes EXTRACTED to CONFIRMED without changing
                # the value, and comparing values alone dropped that confirmation.
                if known.get(name) == (serialised, f.status.value, f.source.kind.value,
                                       f.source.ref):
                    continue                                   # unchanged, no new version
                if name in known:
                    _supersede(cur, case.case_id, name)
                cur.execute("""
                    INSERT INTO facts (fact_id, case_id, name, value, status, source_kind,
                                       source_ref, excerpt, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (f.fact_id, case.case_id, name, _json(serialised), f.status.value,
                      f.source.kind.value, f.source.ref, f.source.excerpt, f.confidence))
            # Removed from the case (e.g. a re-read account no longer supports it):
            # superseded, never deleted, so the history stays auditable.
            for name in known.keys() - case.facts.keys():
                _supersede(cur, case.case_id, name)

            latest = _latest_raw_answers(cur, case.case_id)
            for question, raw in case.raw_answers.items():
                # "_" keys are working values derived from the answers on every
                # analysis round; storing them made them look like questions.
                if question.startswith("_") or latest.get(question) == raw:
                    continue
                cur.execute("INSERT INTO raw_answers (case_id, question, raw_text) VALUES (%s, %s, %s)",
                            (case.case_id, question, raw))

            for entry in case.audit:
                if entry.get("_persisted"):
                    continue
                cur.execute("INSERT INTO audit_log (case_id, actor, event, detail, run_id) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (case.case_id, entry.get("actor", "system"),
                             entry.get("event", "unknown"), _json(_jsonable(entry)),
                             entry.get("run_id")))
                entry["_persisted"] = True

            # P0.4: every fact write, applied, refused or removed. Append-only.
            for h in case.fact_history:
                if h.get("_persisted"):
                    continue
                cur.execute("""
                    INSERT INTO fact_history (case_id, run_id, fact, previous, new,
                                              previous_status, status, source_kind,
                                              source_ref, reason, outcome, at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (case.case_id, h.get("run_id"), h["fact"], _json(_jsonable(h.get("previous"))),
                      _json(_jsonable(h.get("new"))), h.get("previous_status"), h.get("status"),
                      h.get("source_kind"), h.get("source_ref"), h.get("reason") or None,
                      h["outcome"], h["at"]))
                h["_persisted"] = True
        conn.commit()


def _supersede(cur, case_id: str, name: str) -> None:
    cur.execute("UPDATE facts SET superseded = true "
                "WHERE case_id = %s AND name = %s AND NOT superseded", (case_id, name))


def _save_pages(cur, case: CaseFile) -> None:
    """Sync evidence_pages with the case's page images, writing only what changed:
    a page is ~100-300KB and save() runs at every step."""
    stored = {(r[0], r[1]): r[2] for r in cur.execute(
        "SELECT label, page_no, sha256 FROM evidence_pages WHERE case_id = %s",
        (case.case_id,)).fetchall()}
    wanted = {(ev.evidence_id, n): img for ev in case.evidence.values()
              for n, img in enumerate(ev.images, start=1)}
    for (label, page_no), image in wanted.items():
        digest = _sha_bytes(image)
        if stored.get((label, page_no)) == digest:
            continue
        if (label, page_no) in stored:
            cur.execute("UPDATE evidence_pages SET sha256 = %s, image = %s "
                        "WHERE case_id = %s AND label = %s AND page_no = %s",
                        (digest, image, case.case_id, label, page_no))
        else:
            cur.execute("INSERT INTO evidence_pages (case_id, label, page_no, sha256, image) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (case.case_id, label, page_no, digest, image))
    for label, page_no in stored.keys() - wanted.keys():
        cur.execute("DELETE FROM evidence_pages WHERE case_id = %s AND label = %s AND page_no = %s",
                    (case.case_id, label, page_no))


def _latest_raw_answers(cur, case_id: str) -> dict[str, str]:
    """Each question's most recent wording. raw_answers is append-only, so an
    edited narrative is a new row rather than an overwrite."""
    latest: dict[str, str] = {}
    for question, raw in cur.execute(
            "SELECT question, raw_text FROM raw_answers WHERE case_id = %s ORDER BY created_at",
            (case_id,)).fetchall():
        latest[question] = raw
    return latest


def load(case_id: str) -> CaseFile:
    with connect() as conn:
        row = conn.execute(f"SELECT state, driver_status, {', '.join(ROUTING_COLUMNS)}, "
                           f"{', '.join(QUESTION_COLUMNS)}, current_run_id, run_status, "
                           "frontend_version "
                           "FROM cases WHERE case_id = %s", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        case = CaseFile(case_id, state=CaseState(row[0]), driver_status=DriverStatus(row[1]))
        n = len(ROUTING_COLUMNS)
        apply_routing_columns(case, row[2:2 + n])

        for label, kind, s3_key, ocr, filename in conn.execute(
                "SELECT label, kind, s3_key, ocr_text, filename FROM evidence WHERE case_id = %s",
                (case_id,)).fetchall():
            case.evidence[label] = EvidenceItem(label, kind, filename or label,
                                                text=ocr or "", storage_url=s3_key)
        for label, _page_no, image in conn.execute(
                "SELECT label, page_no, image FROM evidence_pages WHERE case_id = %s "
                "ORDER BY label, page_no", (case_id,)).fetchall():
            if label in case.evidence:
                case.evidence[label].images.append(bytes(image))

        for fact_id, name, value, status, kind, ref, excerpt, conf in conn.execute("""
                SELECT fact_id, name, value, status, source_kind, source_ref, excerpt, confidence
                FROM facts WHERE case_id = %s AND NOT superseded ORDER BY created_at
        """, (case_id,)).fetchall():
            case.facts[name] = Fact(fact_id, name, _revived(value), FactStatus(status),
                                    FactSource(SourceKind(kind), ref, excerpt), conf or 1.0)

        # The customer's own wording comes back so the account is re-read from
        # it on the next analysis round; without it that round found no text and
        # cleared every fact the customer's account had established. It still
        # reaches the drafter only as normalised Facts (rule Q-06), exactly as
        # in the process that first received it.
        case.raw_answers = _latest_raw_answers(conn, case_id)
        apply_question_columns(case, row[2 + n:2 + n + len(QUESTION_COLUMNS)])
        run_id, run_status, frontend_version = row[2 + n + len(QUESTION_COLUMNS):]

        case.audit = [{**(_loaded(detail) or {}), "_persisted": True} for (detail,) in conn.execute(
            "SELECT detail FROM audit_log WHERE case_id = %s ORDER BY id", (case_id,)).fetchall()]
        # The run this case is in. Set after the audit, which stamps nothing on
        # load: each stored entry already carries its own run.
        case.run_id = int(run_id or 0)
        case.run_status = run_status or "NONE"
        case.frontend_version = frontend_version

        for (run, fact, previous, new, previous_status, status, source_kind, source_ref,
             reason, outcome, at) in conn.execute("""
                SELECT run_id, fact, previous, new, previous_status, status, source_kind,
                       source_ref, reason, outcome, at
                FROM fact_history WHERE case_id = %s ORDER BY id
        """, (case_id,)).fetchall():
            at = at.isoformat() if hasattr(at, "isoformat") else at
            # jsonb comes back decoded (as the facts table's value does): a string
            # value is already the value, so it must not be parsed again.
            entry = {"fact": fact, "previous": previous, "new": new,
                     "previous_status": previous_status, "status": status,
                     "source_kind": source_kind, "source_ref": source_ref,
                     "reason": reason or "", "outcome": outcome, "run_id": run, "at": at,
                     "_persisted": True}
            case.fact_history.append(entry)
            if outcome == "CONFLICT":
                case.fact_conflicts.append({
                    "fact": fact, "old": entry["previous"], "new": entry["new"],
                    "old_status": previous_status, "new_status": status,
                    "new_source": source_ref, "rule": reason, "status": "CONFLICT",
                    "run_id": run, "at": at})
    return case


QUESTION_COLUMNS = ("asked_questions", "pending_questions")


def question_columns(case: CaseFile) -> tuple:
    return (_json(list(case.asked_questions)), _json(_jsonable(case.pending_questions)))


def apply_question_columns(case: CaseFile, values) -> None:
    asked, pending = (_loaded(v) for v in values)
    if asked is None:
        # A row written before these columns existed: fall back to the answered
        # questions, minus the narrative and internal "_" working keys.
        asked = [q for q in case.raw_answers if q != "narrative" and not q.startswith("_")]
    case.asked_questions = list(asked)
    case.pending_questions = list(pending or [])


# Intake's decision and the classifier labels behind it. Persisted so a case
# rehydrated by another worker routes exactly as it did when it was classified:
# without `document_classes` a reloaded private case had no labels and the
# scope gate read that as a classifier failure.
ROUTING_COLUMNS = ("route", "document_type", "stage", "scope_stop", "document_classes",
                   "classifications", "timeline")


def routing_columns(case: CaseFile) -> tuple:
    return (case.route, case.document_type, case.stage, case.scope_stop,
            _json(dict(case.document_classes)), _json(_jsonable(case.classifications)),
            _json(_jsonable(case.timeline)))


def apply_routing_columns(case: CaseFile, values) -> None:
    route, document_type, stage, scope_stop, classes, classifications, timeline = values
    case.route, case.document_type, case.stage, case.scope_stop = (route, document_type,
                                                                  stage, scope_stop)
    case.document_classes = dict(_loaded(classes) or {})
    case.classifications = dict(_loaded(classifications) or {})
    case.timeline = list(_loaded(timeline) or [])


def _loaded(value: Any) -> Any:
    """psycopg returns jsonb already decoded; a text column or a test double
    may hand back the JSON string."""
    return json.loads(value) if isinstance(value, str) else value


def save_output(case: CaseFile, out) -> None:
    """Persist the draft + its validation result (drafts.retrieval_pack keeps the
    exact context the letter was written from, for replay).

    `model`, `prompt_version` and `validator_version` come from the draft and the
    validator themselves. They were a hardcoded None and a hardcoded "VAL-1"
    here, so every stored case claimed the same validator and no model at all -
    a replay could reach the right KB release and still not know what wrote the
    letter or which rules cleared it.
    """
    from dataclasses import asdict

    from ..engines import validation
    draft_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            outcome = {k: getattr(out, k, None) for k in OUTCOME_FIELDS}
            cur.execute("""
                INSERT INTO drafts (draft_id, case_id, attempt, drafter, model, prompt_version,
                                    structured, retrieval_pack, state, letter, evidence_list,
                                    outcome, no_ground_reason, run_id, manifest)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (draft_id, case.case_id, out.draft.attempt, type(out.draft).__name__,
                  out.draft.model, out.draft.prompt_version,
                  _json(_jsonable([[asdict(s) for s in p] for p in out.draft.paragraphs])),
                  _json(_jsonable(asdict(out.pack))),
                  # What the customer was given. Without these a restarted
                  # process had the draft but not whether it was released, so
                  # the letter PDF answered 404 for a released case.
                  out.state.value, getattr(out, "letter", None),
                  _json(list(getattr(out, "evidence_list", None) or [])),
                  _json(_jsonable(outcome)), getattr(out.draft, "no_ground_reason", None),
                  case.run_id, _json(_jsonable(getattr(out, "manifest", None)))))
            cur.execute("""
                INSERT INTO validations (draft_id, passed, issues, validator_version)
                VALUES (%s, %s, %s, %s)
            """, (draft_id, out.validation.passed,
                  _json(_jsonable([asdict(i) for i in out.validation.issues])),
                  validation.VERSION))
            if out.state == CaseState.MANUAL_REVIEW:
                cur.execute("INSERT INTO review_queue (case_id, reason, sla_due) VALUES (%s, %s, now())",
                            (case.case_id, "validation failed after max attempts"))
        conn.commit()


OUTCOME_FIELDS = ("outcome", "outcome_title", "outcome_message", "outcome_next",
                  "cta_label", "can_continue")


def load_output(case: CaseFile):
    """The case's latest AppealOutput, rebuilt from drafts + validations, or None
    when nothing was generated. Lets a restarted process serve the letter and
    PDF it already released instead of generating a different one."""
    from dataclasses import fields

    from ..models import (Draft, DraftSentence, RetrievalPack, ValidationIssue,
                          ValidationResult)
    from ..orchestrator import AppealOutput, render
    with connect() as conn:
        row = conn.execute("""
            SELECT d.attempt, d.model, d.prompt_version, d.structured, d.retrieval_pack,
                   d.state, d.letter, d.evidence_list, d.outcome, d.no_ground_reason,
                   v.passed, v.issues, d.manifest
            FROM drafts d LEFT JOIN validations v ON v.draft_id = d.draft_id
            WHERE d.case_id = %s ORDER BY d.created_at DESC LIMIT 1
        """, (case.case_id,)).fetchone()
    if row is None:
        return None
    (attempt, model, prompt_version, structured, pack, state, letter, evidence_list,
     outcome, no_ground_reason, passed, issues, manifest) = row
    draft = Draft(case.case_id,
                  [[DraftSentence(**s) for s in p] for p in (_loaded(structured) or [])],
                  attempt=attempt or 1, no_ground_reason=no_ground_reason, model=model,
                  prompt_version=prompt_version)
    pack_fields = {f.name for f in fields(RetrievalPack)}
    pack = RetrievalPack(**{k: v for k, v in (_loaded(pack) or {}).items() if k in pack_fields})
    validation = ValidationResult(bool(passed),
                                  [ValidationIssue(**i) for i in (_loaded(issues) or [])])
    if state is None:
        # A draft stored before `state` was recorded: released only if the case
        # itself says so and the stored validation passed.
        state = (CaseState.RELEASED.value
                 if case.state == CaseState.RELEASED and validation.passed
                 else case.state.value)
    state = CaseState(state)
    if letter is None and state == CaseState.RELEASED:
        letter = render(draft)
    out = AppealOutput(state, letter, pack, draft, validation, list(_loaded(evidence_list) or []))
    for k, v in (_loaded(outcome) or {}).items():
        if k in OUTCOME_FIELDS and v is not None:
            setattr(out, k, v)
    out.manifest = _loaded(manifest)
    return out


# ------------------------------------------------------------------ helpers
def _json(value: Any):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and type(value).__mro__[1].__name__ == "str":   # str Enum
        return value.value
    return value


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _revived(value: Any) -> Any:
    """jsonb has no date type, so _jsonable stored dates as ISO strings and they
    came back as str: a reloaded case then crashed comparing a date with a str
    (code_versions.resolve). An exact YYYY-MM-DD string is a stored date."""
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


def _sha(text: str) -> str:
    return _sha_bytes((text or "").encode())


def _sha_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _as_uuid(evidence_id: str, case_id: str) -> str:
    """Evidence ids from the API are short labels ("E1"); the schema wants a uuid.
    Derive one deterministically so re-uploading the same label updates its row."""
    try:
        return str(uuid.UUID(evidence_id))
    except ValueError:
        return str(uuid.uuid5(uuid.UUID(case_id), evidence_id))
