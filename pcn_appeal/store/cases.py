"""Case persistence: cases, evidence, facts, raw answers, drafts, validations, audit.

Replaces the process dict in api.py when DATABASE_URL is set, so a restart or a
second worker does not lose a customer's case.

Two schema properties are load-bearing and this module preserves them:

  * `facts` is APPEND-ONLY. A corrected value inserts a new row and marks the
    previous one `superseded`, so the confirmation screen's history survives and
    an appeal can be audited against what was known when it was drafted.
  * `raw_answers` holds the customer's own wording and is never read back into a
    CaseFile here - only the keeper-safe normalised Fact is. Nothing on the
    drafting path can reach it (rule Q-06).
"""
from __future__ import annotations

import json
import os
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
    upserted. Small enough to do in one transaction per step."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE cases SET state = %s, driver_status = %s WHERE case_id = %s",
                        (case.state.value, case.driver_status.value, case.case_id))

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

            known = {r[0]: (r[1], r[2]) for r in cur.execute(
                "SELECT name, value, fact_id FROM facts WHERE case_id = %s AND NOT superseded",
                (case.case_id,)).fetchall()}
            for name, f in case.facts.items():
                serialised = _jsonable(f.value)
                if name in known and known[name][0] == serialised:
                    continue                                   # unchanged, no new version
                if name in known:
                    cur.execute("UPDATE facts SET superseded = true "
                                "WHERE case_id = %s AND name = %s AND NOT superseded",
                                (case.case_id, name))
                cur.execute("""
                    INSERT INTO facts (fact_id, case_id, name, value, status, source_kind,
                                       source_ref, excerpt, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (f.fact_id, case.case_id, name, _json(serialised), f.status.value,
                      f.source.kind.value, f.source.ref, f.source.excerpt, f.confidence))

            for question, raw in case.raw_answers.items():
                cur.execute("SELECT 1 FROM raw_answers WHERE case_id = %s AND question = %s",
                            (case.case_id, question))
                if cur.fetchone() is None:
                    cur.execute("INSERT INTO raw_answers (case_id, question, raw_text) VALUES (%s, %s, %s)",
                                (case.case_id, question, raw))

            for entry in case.audit:
                if entry.get("_persisted"):
                    continue
                cur.execute("INSERT INTO audit_log (case_id, actor, event, detail) VALUES (%s, %s, %s, %s)",
                            (case.case_id, entry.get("actor", "system"),
                             entry.get("event", "unknown"), _json(entry)))
                entry["_persisted"] = True
        conn.commit()


def load(case_id: str) -> CaseFile:
    with connect() as conn:
        row = conn.execute("SELECT state, driver_status FROM cases WHERE case_id = %s",
                           (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        case = CaseFile(case_id, state=CaseState(row[0]), driver_status=DriverStatus(row[1]))

        for label, kind, s3_key, ocr, filename in conn.execute(
                "SELECT label, kind, s3_key, ocr_text, filename FROM evidence WHERE case_id = %s",
                (case_id,)).fetchall():
            case.evidence[label] = EvidenceItem(label, kind, filename or label,
                                                text=ocr or "", storage_url=s3_key)

        for fact_id, name, value, status, kind, ref, excerpt, conf in conn.execute("""
                SELECT fact_id, name, value, status, source_kind, source_ref, excerpt, confidence
                FROM facts WHERE case_id = %s AND NOT superseded ORDER BY created_at
        """, (case_id,)).fetchall():
            case.facts[name] = Fact(fact_id, name, value, FactStatus(status),
                                    FactSource(SourceKind(kind), ref, excerpt), conf or 1.0)

        case.asked_questions = [r[0] for r in conn.execute(
            "SELECT question FROM raw_answers WHERE case_id = %s ORDER BY created_at",
            (case_id,)).fetchall() if r[0] != "narrative"]
    return case


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
            cur.execute("""
                INSERT INTO drafts (draft_id, case_id, attempt, drafter, model, prompt_version,
                                    structured, retrieval_pack)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (draft_id, case.case_id, out.draft.attempt, type(out.draft).__name__,
                  out.draft.model, out.draft.prompt_version,
                  _json(_jsonable([[asdict(s) for s in p] for p in out.draft.paragraphs])),
                  _json(_jsonable(asdict(out.pack)))))
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


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode()).hexdigest()


def _as_uuid(evidence_id: str, case_id: str) -> str:
    """Evidence ids from the API are short labels ("E1"); the schema wants a uuid.
    Derive one deterministically so re-uploading the same label updates its row."""
    try:
        return str(uuid.UUID(evidence_id))
    except ValueError:
        return str(uuid.uuid5(uuid.UUID(case_id), evidence_id))
