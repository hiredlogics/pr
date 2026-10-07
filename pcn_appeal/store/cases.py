"""Case persistence: cases, evidence, facts, raw answers, drafts, validations, audit.

Replaces the process dict in api.py when DATABASE_URL is set, so a restart or a
second worker does not lose a customer's case.

Two schema properties are load-bearing and this module preserves them:

  * `facts` is the Fact Graph (P1): one row per fact node, updated only
    through FactManager, never deleted (a retracted fact is `active = false`).
    Every write, applied or refused, is in the append-only `fact_history`, so
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
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
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
    """Write the whole case through. Fact nodes and evidence and state are
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
                                 current_run_id = %s, run_status = %s, frontend_version = %s,
                                 release_metadata = %s,
                                 kb_release_id = COALESCE(%s, kb_release_id)
                WHERE case_id = %s
            """, (case.state.value, case.driver_status.value, *routing_columns(case),
                  *question_columns(case), case.run_id, case.run_status,
                  case.frontend_version,
                  _json(case.release_metadata) if case.release_metadata is not None else None,
                  (case.release_metadata or {}).get("kb_release_id")
                  if isinstance(case.release_metadata, dict) else None,
                  case.case_id))

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

            _save_fact_graph(cur, case)
            _save_claim_plans(cur, case)
            _save_draft_versions(cur, case)
            _save_legal_findings(cur, case)
            _save_master_case_state(cur, case)
            _save_document_baselines(cur, case)
            _save_case_analysis_state(cur, case)
            _save_integrity(cur, case)

            from ..fact_lifecycle import persist_versions
            persist_versions(case)

            latest = _latest_raw_answers(cur, case.case_id)
            for question, raw in case.raw_answers.items():
                # "_" keys are working values derived from the answers on every
                # analysis round; storing them made them look like questions.
                # Lineage lives in fact_history / audit / fact_versions (rebuilt on load).
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

            # Every fact write, applied, refused, ignored or retracted. Append-only.
            for h in case.fact_history:
                if h.get("_persisted"):
                    continue
                cur.execute("""
                    INSERT INTO fact_history (case_id, run_id, fact, fact_id, previous, new,
                                              previous_status, status, source_kind,
                                              source_ref, source_type, changed_by, reason,
                                              outcome, at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (case.case_id, h.get("run_id"), h["fact"], h.get("fact_id"),
                      _json(_jsonable(h.get("previous"))), _json(_jsonable(h.get("new"))),
                      h.get("previous_status"), h.get("status"), h.get("source_kind"),
                      h.get("source_ref"), h.get("source_type"), h.get("changed_by"),
                      h.get("reason") or None, h["outcome"], h["at"]))
                h["_persisted"] = True
        conn.commit()


def _save_fact_graph(cur, case: CaseFile) -> None:
    """One row per fact node, updated in place; every change is in fact_history.

    A retracted fact keeps its row (active = false) and its id, so the history
    that points at it stays resolvable. Nothing here deletes.
    """
    from .. import fact_graph as fg
    held = [(n, f, True) for n, f in case.facts.items()]
    gone = [(n, case.facts.last_value(n), False) for n in case.facts.retracted()]
    for name, f, active in held + [g for g in gone if g[1] is not None]:
        cur.execute("""
            INSERT INTO facts (fact_id, case_id, fact_name, fact_value, source_type, status,
                               confidence, engine_status, source_kind, source_ref, excerpt,
                               fact_ref, disputed, active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (case_id, fact_name) DO UPDATE SET
              fact_value = excluded.fact_value, source_type = excluded.source_type,
              status = excluded.status, confidence = excluded.confidence,
              engine_status = excluded.engine_status, source_kind = excluded.source_kind,
              source_ref = excluded.source_ref, excerpt = excluded.excerpt,
              fact_ref = excluded.fact_ref, disputed = excluded.disputed, active = excluded.active,
              updated_at = excluded.updated_at
        """, (case.facts.node_id(name), case.case_id, name, _json(_jsonable(f.value)),
              fg.source_type(case, f).value, fg.graph_status(case, f).value, f.confidence,
              f.status.value, f.source.kind.value, f.source.ref, f.source.excerpt, f.fact_id,
              bool(f.disputed), active, case.facts.created_at(name),
              case.facts.updated_at(name)))
    for s in case.fact_sources:
        if s.get("_persisted"):
            continue
        cur.execute("""
            INSERT INTO fact_sources (case_id, fact_id, fact_name, source_type, source_ref,
                                      excerpt, value, confidence, accepted, run_id, observed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (case.case_id, s.get("fact_id"), s["fact"], s["source_type"], s.get("source_ref"),
              s.get("excerpt"), _json(_jsonable(s.get("value"))), s.get("confidence"),
              bool(s["accepted"]), s.get("run_id"), s["at"]))
        s["_persisted"] = True
    _save_hypotheses(cur, case)
    for c in case.fact_conflicts:
        cur.execute("""
            INSERT INTO fact_conflicts (conflict_id, case_id, fact_id, fact_name, held_value,
                                        held_status, held_source_type, held_source,
                                        proposed_value, proposed_status, proposed_source_type,
                                        proposed_source, rule, status, resolution, resolved_by,
                                        run_id, created_at, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (conflict_id) DO UPDATE SET
              status = excluded.status, resolution = excluded.resolution,
              resolved_by = excluded.resolved_by, resolved_at = excluded.resolved_at
        """, (c["conflict_id"], case.case_id, c.get("fact_id"), c["fact"],
              _json(_jsonable(c.get("_held", c.get("held_value")))), c.get("held_status"),
              c.get("held_source_type"), c.get("held_source"),
              _json(_jsonable(c.get("_proposed", c.get("proposed_value")))),
              c.get("proposed_status"), c.get("proposed_source_type"), c.get("proposed_source"),
              c["rule"], c["status"], _json(_jsonable(c.get("resolution"))),
              c.get("resolved_by"), c.get("run_id"), c["at"], c.get("resolved_at")))


def _save_hypotheses(cur, case: CaseFile) -> None:
    """P2: one row per hypothesis, upserted by id (status moves on; nothing is
    deleted)."""
    for h in case.fact_hypotheses:
        cur.execute("""
            INSERT INTO fact_hypotheses (hypothesis_id, case_id, fact_name, possible_value,
                                         source_text, confidence, signals, rule,
                                         required_confirmation_question, reason, possible_impact,
                                         status, asked_at, answer, resolved_fact_id, resolved_by,
                                         run_id, created_at, updated_at, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hypothesis_id) DO UPDATE SET
              source_text = excluded.source_text, confidence = excluded.confidence,
              signals = excluded.signals, status = excluded.status,
              asked_at = excluded.asked_at, answer = excluded.answer,
              resolved_fact_id = excluded.resolved_fact_id, resolved_by = excluded.resolved_by,
              updated_at = excluded.updated_at, resolved_at = excluded.resolved_at
        """, (h["hypothesis_id"], case.case_id, h["fact_name"], _json(_jsonable(h["possible_value"])),
              h.get("source_text"), h.get("confidence"), _json(h.get("signals") or []),
              h.get("rule"), _json(h["required_confirmation_question"]), h.get("reason"),
              h.get("possible_impact"), h["status"], h.get("asked_at"),
              _json(_jsonable(h.get("answer"))), h.get("resolved_fact_id"), h.get("resolved_by"),
              h.get("run_id"), h["created_at"], h["updated_at"], h.get("resolved_at")))


def _load_hypotheses(conn, case: CaseFile) -> None:
    from ..hypotheses import _key
    for (hid, fact, value, text, conf, signals, rule, question, reason, impact, status, asked,
         answer, resolved_fact, resolved_by, run, created, updated, resolved_at) in conn.execute("""
            SELECT hypothesis_id, fact_name, possible_value, source_text, confidence, signals,
                   rule, required_confirmation_question, reason, possible_impact, status,
                   asked_at, answer, resolved_fact_id, resolved_by, run_id, created_at,
                   updated_at, resolved_at
            FROM fact_hypotheses WHERE case_id = %s ORDER BY created_at, hypothesis_id
    """, (case.case_id,)).fetchall():
        h = {"hypothesis_id": str(hid), "case_id": case.case_id, "fact_name": fact,
             "hypothesis": f"possible_{fact}", "possible_value": value, "source_text": text,
             "confidence": None if conf is None else float(conf), "signals": signals or [],
             "rule": rule, "required_confirmation_question": question, "reason": reason,
             "possible_impact": impact, "status": status, "asked_at": _stamp(asked),
             "answer": answer, "resolved_fact_id": None if resolved_fact is None
             else str(resolved_fact), "run_id": run, "created_at": _stamp(created),
             "updated_at": _stamp(updated), "_key": _key(fact, value)}
        if resolved_by is not None or resolved_at is not None:
            h.update(resolved_by=resolved_by, resolved_at=_stamp(resolved_at))
        case.fact_hypotheses.append(h)


def _save_claim_plans(cur, case: CaseFile) -> None:
    """P5: each claim plan version once, items with it; afterwards only its
    LOCKED -> SUPERSEDED transition. The database refuses items for a locked
    plan and any other change to one (0006_claim_plan_authority.sql), so:

      1. a new plan is written CONFIRMED with its items;
      2. in version order, plans now SUPERSEDED are locked (if new) and
         superseded - superseded_by then names a row that exists, and the
         case's previous LOCKED plan is gone before another is locked;
      3. the plan that is LOCKED is locked (at most one per case).
    """
    stored = {str(r[0]): r[1] for r in cur.execute(
        "SELECT claim_plan_id, status FROM claim_plans WHERE case_id = %s",
        (case.case_id,)).fetchall()}
    plans = sorted((p.as_dict() for p in case.claim_plans), key=lambda d: d["version"])
    for d in plans:
        if d["claim_plan_id"] in stored:
            continue
        trust = d["trust"]
        cur.execute("""
            INSERT INTO claim_plans (claim_plan_id, case_id, analysis_run_id, run_number, version,
                                     status, created_at, confirmed_at, inputs_digest, plan_digest,
                                     code_version, kb_version, prompt_version, model_version,
                                     facts_used, relationships_used, trust,
                                     material_fact_accounting)
            VALUES (%s, %s, %s, %s, %s, 'CONFIRMED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (d["claim_plan_id"], case.case_id, d["analysis_run_id"], d["run_number"],
              d["version"], d["created_at"], d["confirmed_at"], d["inputs_digest"],
              d["plan_digest"], trust.get("code_version"), _json(trust.get("kb_version") or {}),
              _json(trust.get("prompt_versions") or {}), _json(trust.get("model_versions") or {}),
              _json(_jsonable(trust.get("facts_used") or {})),
              _json(_jsonable(trust.get("relationships_used") or [])),
              _json(_jsonable(trust)), _json(_jsonable(d["material_fact_accounting"]))))
        for n, item in enumerate(d["items"]):
            cur.execute("""
                INSERT INTO claim_plan_items (item_id, claim_plan_id, ordinal, knowledge_id,
                                              module_id, claim_type, status, decision, reason,
                                              supporting_facts, evidence_refs, relationships,
                                              priority, topic)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (item["item_id"], d["claim_plan_id"], n, item["knowledge_id"], item["module_id"],
                  item["claim_type"], item["status"], item["decision"], item["reason"],
                  _json(_jsonable(item["supporting_facts"])),
                  _json(_jsonable(item["evidence_refs"])),
                  _json(_jsonable(item["relationships"])), item["priority"], item["topic"]))
        stored[d["claim_plan_id"]] = "CONFIRMED"

    def lock(d):
        cur.execute("UPDATE claim_plans SET status = 'LOCKED', locked_at = %s "
                    "WHERE claim_plan_id = %s", (d["locked_at"], d["claim_plan_id"]))

    for d in plans:
        if d["status"] == "SUPERSEDED" and stored[d["claim_plan_id"]] != "SUPERSEDED":
            if stored[d["claim_plan_id"]] == "CONFIRMED":
                lock(d)
            cur.execute("UPDATE claim_plans SET status = 'SUPERSEDED', superseded_at = %s, "
                        "superseded_by = %s WHERE claim_plan_id = %s",
                        (d["superseded_at"], d["superseded_by"], d["claim_plan_id"]))
    for d in plans:
        if d["status"] == "LOCKED" and stored[d["claim_plan_id"]] == "CONFIRMED":
            lock(d)


def _save_draft_versions(cur, case: CaseFile) -> None:
    """P6: drafts are immutable; what changes is how they fared (validation,
    grounding, shadow-judge verdict, released)."""
    for r in case.draft_versions:
        if r.get("_persisted") and not r.get("_dirty"):
            continue
        cur.execute("""
            INSERT INTO draft_versions (draft_id, case_id, claim_plan_id, run_id, version, attempt,
                                        parent_draft_id, model, prompt_version, content_hash,
                                        content, validation_status, issues, grounding, judge,
                                        released, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (draft_id) DO UPDATE SET
              validation_status = excluded.validation_status, issues = excluded.issues,
              grounding = excluded.grounding, judge = excluded.judge,
              released = excluded.released, attempt = excluded.attempt, run_id = excluded.run_id
        """, (r["draft_id"], case.case_id, r.get("claim_plan_id"), r.get("run_id"), r["version"],
              r.get("attempt"), r.get("parent_draft_id"), r.get("model"), r.get("prompt_version"),
              r["content_hash"], _json(r["content"]), r["validation_status"],
              _json(r.get("issues") or []), _json(r.get("grounding") or []),
              _json(r.get("judge")) if r.get("judge") is not None else None,
              bool(r.get("released")), r["created_at"]))
        r["_persisted"] = True
        r.pop("_dirty", None)


def _save_legal_findings(cur, case: CaseFile) -> None:
    """P6.1: one row per (case, defect type); the assessment may move, the
    identity may not (DB trigger)."""
    for r in case.legal_findings:
        if r.get("_persisted") and not r.get("_dirty"):
            continue
        cur.execute("""
            INSERT INTO legal_findings (finding_id, case_id, run_id, finding_type, status,
                                        supporting_facts, calculation_result, legal_module_id,
                                        created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (finding_id) DO UPDATE SET
              status = excluded.status, supporting_facts = excluded.supporting_facts,
              calculation_result = excluded.calculation_result,
              legal_module_id = excluded.legal_module_id, run_id = excluded.run_id,
              updated_at = excluded.updated_at
        """, (r["finding_id"], case.case_id, r.get("run_id"), r["finding_type"], r["status"],
              _json(r.get("supporting_facts") or []), _json(r.get("calculation_result") or {}),
              r.get("legal_module_id"), r["created_at"],
              datetime.now(timezone.utc).isoformat()))
        r["_persisted"] = True
        r.pop("_dirty", None)


MASTER_SECTIONS = ("derivations", "knowledge_matches", "grounds")


def _save_master_case_state(cur, case: CaseFile) -> None:
    """P8.1: the Master Case Object's own sections - derivation lineage,
    knowledge matches, and the grounds of each decided claim plan version.

    Append-only, in both directions: an entry is written once and never
    rewritten, and the table refuses an update (0010_master_case_state.sql).
    """
    for section in MASTER_SECTIONS:
        for entry in getattr(case, f"master_{section}", []) or []:
            if entry.get("_persisted"):
                continue
            cur.execute("""
                INSERT INTO master_case_state (case_id, section, run_id, payload, recorded_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (case.case_id, section, entry.get("run_id"),
                  _json(_jsonable({k: v for k, v in entry.items()
                                   if not k.startswith("_")})),
                  entry.get("at") or datetime.now(timezone.utc).isoformat()))
            entry["_persisted"] = True


def _load_master_case_state(conn, case: CaseFile) -> None:
    """Restore the sections in the order they were recorded, so the case comes
    back holding the same generations it was saved with."""
    rows = conn.execute(
        "SELECT section, payload FROM master_case_state WHERE case_id = %s ORDER BY entry_id",
        (case.case_id,)).fetchall()
    restored: dict[str, list] = {s: [] for s in MASTER_SECTIONS}
    for section, payload in rows:
        if section not in restored:
            continue
        entry = _loaded(payload) or {}
        entry["_persisted"] = True
        restored[section].append(entry)
    for section, entries in restored.items():
        setattr(case, f"master_{section}", entries)


def _save_document_baselines(cur, case: CaseFile) -> None:
    """P8.3: append-only document belt snapshots."""
    for entry in getattr(case, "document_baselines", []) or []:
        if entry.get("_persisted"):
            continue
        cur.execute("""
            INSERT INTO document_baselines (case_id, version, digest, payload, recorded_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (case.case_id, int(entry.get("version") or 1), entry.get("digest") or "",
              _json(_jsonable({k: v for k, v in entry.items() if not k.startswith("_")})),
              entry.get("at") or datetime.now(timezone.utc).isoformat()))
        entry["_persisted"] = True


def _load_document_baselines(conn, case: CaseFile) -> None:
    rows = conn.execute(
        "SELECT payload FROM document_baselines WHERE case_id = %s ORDER BY version, entry_id",
        (case.case_id,)).fetchall()
    case.document_baselines = []
    for (payload,) in rows:
        entry = _loaded(payload) or {}
        entry["_persisted"] = True
        case.document_baselines.append(entry)


def _save_case_analysis_state(cur, case: CaseFile) -> None:
    """P8.3: one row per Case Intelligence proposal run."""
    for entry in getattr(case, "case_analysis_states", []) or []:
        if entry.get("_persisted"):
            continue
        cur.execute("""
            INSERT INTO case_analysis_state (
                case_id, analysis_version, document_baseline_version,
                customer_analysis_version, candidate_ground_ids, selected_ground_ids,
                verified_ground_ids, rejected_ground_ids, created_by, timestamp, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (case.case_id, int(entry.get("analysis_version") or 1),
              entry.get("document_baseline_version"),
              entry.get("customer_analysis_version"),
              _json(list(entry.get("candidate_ground_ids") or [])),
              _json(list(entry.get("selected_ground_ids") or [])),
              _json(list(entry.get("verified_ground_ids") or [])),
              _json(list(entry.get("rejected_ground_ids") or [])),
              entry.get("created_by") or "case_intelligence",
              entry.get("timestamp") or datetime.now(timezone.utc).isoformat(),
              _json(_jsonable({k: v for k, v in entry.items() if not k.startswith("_")}))))
        entry["_persisted"] = True


def _load_case_analysis_state(conn, case: CaseFile) -> None:
    rows = conn.execute(
        "SELECT payload FROM case_analysis_state WHERE case_id = %s "
        "ORDER BY analysis_version, entry_id",
        (case.case_id,)).fetchall()
    case.case_analysis_states = []
    for (payload,) in rows:
        entry = _loaded(payload) or {}
        entry["_persisted"] = True
        case.case_analysis_states.append(entry)


def _load_legal_findings(conn, case: CaseFile) -> None:
    for (fid, run, ftype, status, facts, calc, module, created) in conn.execute("""
            SELECT finding_id, run_id, finding_type, status, supporting_facts,
                   calculation_result, legal_module_id, created_at
            FROM legal_findings WHERE case_id = %s ORDER BY finding_type""",
            (case.case_id,)).fetchall():
        case.legal_findings.append({
            "finding_id": str(fid), "case_id": case.case_id, "run_id": run,
            "finding_type": ftype, "status": status,
            "supporting_facts": _loaded(facts) or [],
            "calculation_result": _loaded(calc) or {}, "legal_module_id": module,
            "created_at": _stamp(created), "_persisted": True})


def _load_draft_versions(conn, case: CaseFile) -> None:
    for (did, plan_id, run, version, attempt, parent, model, pv, digest, content, status, issues,
         grounding, judge, released, at) in conn.execute("""
            SELECT draft_id, claim_plan_id, run_id, version, attempt, parent_draft_id, model,
                   prompt_version, content_hash, content, validation_status, issues, grounding,
                   judge, released, created_at
            FROM draft_versions WHERE case_id = %s ORDER BY version""", (case.case_id,)).fetchall():
        case.draft_versions.append({
            "draft_id": str(did), "case_id": case.case_id,
            "claim_plan_id": None if plan_id is None else str(plan_id), "run_id": run,
            "version": version, "attempt": attempt,
            "parent_draft_id": None if parent is None else str(parent), "model": model,
            "prompt_version": pv, "content_hash": digest, "content": _loaded(content) or [],
            "validation_status": status, "issues": _loaded(issues) or [],
            "grounding": _loaded(grounding) or [], "judge": _loaded(judge),
            "released": bool(released), "created_at": _stamp(at), "_persisted": True})


def _save_integrity(cur, case: CaseFile) -> None:
    """P5.5: state transitions and model calls, append-only."""
    from ..integrity.trace import transition_reason
    audit = list(case.audit)
    for t in case.state_history:
        if t.get("_persisted"):
            continue
        # the reason is resolved against the audit as it stands at save time
        t["reason"] = t.get("reason") or transition_reason(t, audit)
        cur.execute("INSERT INTO case_state_history (case_id, run_id, from_state, to_state, "
                    "reason, at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (case.case_id, t.get("run_id"), t["from"], t["to"], t.get("reason"), t["at"]))
        t["_persisted"] = True
    for c in case.ai_calls:
        if c.get("_persisted"):
            continue
        cur.execute("""
            INSERT INTO ai_execution_logs (case_id, run_id, task, provider, model, prompt_version,
                                           prompt_sha256, input_sha256, input_chars, input_sources,
                                           images, output_sha256, output_chars, output_keys,
                                           duration_ms, status, error, at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (case.case_id, c.get("run_id"), c["task"], c.get("provider"), c.get("model"),
              c.get("prompt_version"), c.get("prompt_sha256"), c["input_sha256"],
              c.get("input_chars"), _json(c.get("input_sources") or []), c.get("images") or 0,
              c.get("output_sha256"), c.get("output_chars"), _json(c.get("output_keys") or []),
              c.get("duration_ms"), c.get("status") or "SUCCESS", c.get("error"), c["at"]))
        c["_persisted"] = True


def _load_integrity(conn, case: CaseFile) -> None:
    for (run, frm, to, reason, at) in conn.execute(
            "SELECT run_id, from_state, to_state, reason, at FROM case_state_history "
            "WHERE case_id = %s ORDER BY id", (case.case_id,)).fetchall():
        case.state_history.append({"from": frm, "to": to, "run_id": run, "reason": reason,
                                   "at": _stamp(at), "_persisted": True})
    for (run, task, provider, model, pv, psha, isha, ichars, sources, images, osha, ochars,
         okeys, dur, status, error, at) in conn.execute("""
            SELECT run_id, task, provider, model, prompt_version, prompt_sha256, input_sha256,
                   input_chars, input_sources, images, output_sha256, output_chars, output_keys,
                   duration_ms, status, error, at
            FROM ai_execution_logs WHERE case_id = %s ORDER BY id""", (case.case_id,)).fetchall():
        case.ai_calls.append({
            "case_id": case.case_id, "run_id": run, "task": task, "provider": provider,
            "model": model, "prompt_version": pv, "prompt_sha256": psha, "input_sha256": isha,
            "input_chars": ichars, "input_sources": _loaded(sources) or [], "images": images,
            "output_sha256": osha, "output_chars": ochars, "output_keys": _loaded(okeys) or [],
            "duration_ms": dur, "status": status, "error": error, "at": _stamp(at),
            "_persisted": True})


def save_execution_trace(case: CaseFile, out) -> None:
    """P5.5: the run's integrity result (checks, trace, report), one row per run."""
    integrity = getattr(out, "integrity", None)
    if not integrity:
        return
    trace = integrity.get("trace") or {}
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM case_execution_trace WHERE case_id = %s AND run_id = %s",
                        (case.case_id, case.run_id))
            cur.execute("""
                INSERT INTO case_execution_trace (case_id, run_id, execution_id, passed,
                                                  failed_checks, checks, trace, report)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (case.case_id, case.run_id, trace.get("execution_id"),
                  bool(integrity.get("passed")),
                  _json([c["check"] for c in integrity.get("checks") or []
                         if c["status"] != "PASS"]),
                  _json(_jsonable(integrity.get("checks") or [])), _json(_jsonable(trace)),
                  integrity.get("report")))
        conn.commit()


def load_execution_trace(case_id: str, run_id: Optional[int] = None) -> Optional[dict]:
    with connect() as conn:
        if run_id is None:
            row = conn.execute("SELECT run_id, execution_id, passed, checks, trace, report "
                               "FROM case_execution_trace WHERE case_id = %s "
                               "ORDER BY run_id DESC LIMIT 1", (case_id,)).fetchone()
        else:
            row = conn.execute("SELECT run_id, execution_id, passed, checks, trace, report "
                               "FROM case_execution_trace WHERE case_id = %s AND run_id = %s",
                               (case_id, run_id)).fetchone()
    if row is None:
        return None
    run, execution_id, passed, checks, trace, report = row
    return {"run_id": run, "execution_id": str(execution_id), "passed": bool(passed),
            "checks": _loaded(checks), "trace": _loaded(trace), "report": report}


def _load_claim_plans(conn, case: CaseFile) -> None:
    from ..engines.claim_plan_authority import FinalClaimPlan
    for (plan_id, run_uuid, run_number, version, status, created, confirmed, locked,
         superseded, superseded_by, inputs_digest, plan_digest, trust,
         accounting) in conn.execute("""
            SELECT claim_plan_id, analysis_run_id, run_number, version, status, created_at,
                   confirmed_at, locked_at, superseded_at, superseded_by, inputs_digest,
                   plan_digest, trust, material_fact_accounting
            FROM claim_plans WHERE case_id = %s ORDER BY version
    """, (case.case_id,)).fetchall():
        items = []
        for (item_id, knowledge, module_id, claim_type, istatus, decision, reason, facts,
             evidence, rels, priority, topic) in conn.execute("""
                SELECT item_id, knowledge_id, module_id, claim_type, status, decision, reason,
                       supporting_facts, evidence_refs, relationships, priority, topic
                FROM claim_plan_items WHERE claim_plan_id = %s ORDER BY ordinal
        """, (str(plan_id),)).fetchall():
            items.append({"item_id": str(item_id), "knowledge_id": str(knowledge),
                          "module_id": module_id, "claim_type": claim_type, "status": istatus,
                          "decision": decision, "reason": reason,
                          "supporting_facts": _loaded(facts) or [],
                          "evidence_refs": _loaded(evidence) or [],
                          "relationships": _loaded(rels) or [], "priority": priority,
                          "topic": topic or ""})
        case.claim_plans.append(FinalClaimPlan.from_dict({
            "claim_plan_id": str(plan_id), "case_id": case.case_id,
            "analysis_run_id": str(run_uuid), "run_number": run_number, "version": version,
            "status": status, "created_at": _stamp(created), "confirmed_at": _stamp(confirmed),
            "locked_at": _stamp(locked), "superseded_at": _stamp(superseded),
            "superseded_by": None if superseded_by is None else str(superseded_by),
            "inputs_digest": inputs_digest, "plan_digest": plan_digest,
            "trust": _loaded(trust) or {}, "material_fact_accounting": _loaded(accounting) or [],
            "items": items}))


def _conflict_authority(source_type: Optional[str], status: Optional[str]) -> str:
    """Reload the P8.7 authority label that FactManager stored on the conflict."""
    if source_type in ("DOCUMENT", "EVIDENCE"):
        return "DOCUMENT_CONFIRMED"
    if source_type == "CALCULATION":
        return "INFERRED"
    if status in ("CONFIRMED", "CORRECTED"):
        return "CUSTOMER_CONFIRMED"
    if source_type in ("CUSTOMER_ANSWER", "CUSTOMER_FREE_TEXT"):
        return "CUSTOMER_ASSERTED"
    return "INFERRED"


def _stamp(at) -> Optional[str]:
    """A stored time as FactGraph holds it: UTC ISO, milliseconds."""
    if at is None or isinstance(at, str):
        return at
    from datetime import timezone
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).isoformat(timespec="milliseconds")


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
                           "frontend_version, release_metadata "
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

        from ..fact_graph import FactManager
        for (node_id, name, value, status, kind, ref, excerpt, conf, fact_ref, disputed,
             active, created, updated) in conn.execute("""
                SELECT fact_id, fact_name, fact_value, engine_status, source_kind, source_ref,
                       excerpt, confidence, fact_ref, disputed, active, created_at, updated_at
                FROM facts WHERE case_id = %s ORDER BY created_at, fact_name
        """, (case_id,)).fetchall():
            node_id = str(node_id)
            if not active:
                FactManager.hydrate(case, None, node_id=node_id, name=name)
                continue
            conf = 1.0 if conf is None else float(conf)
            FactManager.hydrate(case, Fact(fact_ref, name, _revived(value), FactStatus(status),
                                           FactSource(SourceKind(kind), ref, excerpt), conf,
                                           bool(disputed)),
                                node_id=node_id, created_at=_stamp(created),
                                updated_at=_stamp(updated))

        # The customer's own wording comes back so the account is re-read from
        # it on the next analysis round; without it that round found no text and
        # cleared every fact the customer's account had established. It still
        # reaches the drafter only as normalised Facts (rule Q-06), exactly as
        # in the process that first received it.
        case.raw_answers = _latest_raw_answers(conn, case_id)
        apply_question_columns(case, row[2 + n:2 + n + len(QUESTION_COLUMNS)])
        run_id, run_status, frontend_version, release_metadata = (
            row[2 + n + len(QUESTION_COLUMNS):])

        case.audit = [{**(_loaded(detail) or {}), "_persisted": True} for (detail,) in conn.execute(
            "SELECT detail FROM audit_log WHERE case_id = %s ORDER BY id", (case_id,)).fetchall()]
        # The run this case is in. Set after the audit, which stamps nothing on
        # load: each stored entry already carries its own run.
        case.run_id = int(run_id or 0)
        case.run_status = run_status or "NONE"
        case.frontend_version = frontend_version
        case.release_metadata = _loaded(release_metadata)

        for (run, fact, fact_id, previous, new, previous_status, status, source_kind,
             source_ref, source_type, changed_by, reason, outcome, at) in conn.execute("""
                SELECT run_id, fact, fact_id, previous, new, previous_status, status,
                       source_kind, source_ref, source_type, changed_by, reason, outcome, at
                FROM fact_history WHERE case_id = %s ORDER BY id
        """, (case_id,)).fetchall():
            # jsonb comes back decoded (as the facts table's value does): a string
            # value is already the value, so it must not be parsed again.
            case.fact_history.append({
                "fact": fact, "fact_id": None if fact_id is None else str(fact_id),
                "previous": previous, "new": new, "previous_status": previous_status,
                "status": status, "source_kind": source_kind, "source_ref": source_ref,
                "source_type": source_type, "changed_by": changed_by, "reason": reason or "",
                "outcome": outcome, "run_id": run, "at": _stamp(at), "_persisted": True})

        for (fact_id, fact, source_type, ref, excerpt, value, conf, accepted, run,
             at) in conn.execute("""
                SELECT fact_id, fact_name, source_type, source_ref, excerpt, value,
                       confidence, accepted, run_id, observed_at
                FROM fact_sources WHERE case_id = %s ORDER BY id
        """, (case_id,)).fetchall():
            case.fact_sources.append({
                "fact": fact, "fact_id": None if fact_id is None else str(fact_id),
                "source_type": source_type, "source_ref": ref, "excerpt": excerpt,
                "value": value, "confidence": None if conf is None else float(conf),
                "accepted": bool(accepted), "run_id": run, "at": _stamp(at),
                "_persisted": True})

        for (conflict_id, fact_id, fact, held, held_status, held_type, held_source, proposed,
             proposed_status, proposed_type, proposed_source, rule, status, resolution,
             resolved_by, run, created, resolved_at) in conn.execute("""
                SELECT conflict_id, fact_id, fact_name, held_value, held_status,
                       held_source_type, held_source, proposed_value, proposed_status,
                       proposed_source_type, proposed_source, rule, status, resolution,
                       resolved_by, run_id, created_at, resolved_at
                FROM fact_conflicts WHERE case_id = %s ORDER BY created_at
        """, (case_id,)).fetchall():
            case.fact_conflicts.append({
                "conflict_id": str(conflict_id), "fact": fact, "fact_name": fact,
                "fact_id": None if fact_id is None else str(fact_id),
                "held_value": held, "proposed_value": proposed,
                "previous_value": held, "new_value": proposed,
                "held_status": held_status, "proposed_status": proposed_status,
                "held_source_type": held_type, "held_source": held_source,
                "proposed_source_type": proposed_type, "proposed_source": proposed_source,
                "sources": [
                    {"kind": held_type, "ref": held_source,
                     "authority": _conflict_authority(held_type, held_status)},
                    {"kind": proposed_type, "ref": proposed_source,
                     "authority": _conflict_authority(proposed_type, proposed_status)},
                ],
                "timestamps": {"previous": _stamp(created), "new": _stamp(created)},
                "rule": rule, "status": status, "resolution_status": status,
                "resolution": resolution,
                "resolved_by": resolved_by, "run_id": run, "at": _stamp(created),
                "resolved_at": _stamp(resolved_at),
                "_held": _revived(held), "_proposed": _revived(proposed)})

        _load_hypotheses(conn, case)
        _load_claim_plans(conn, case)
        _load_draft_versions(conn, case)
        _load_legal_findings(conn, case)
        _load_master_case_state(conn, case)
        _load_document_baselines(conn, case)
        _load_case_analysis_state(conn, case)
        _load_integrity(conn, case)
        # P8.3: analysis state is the proposal record; restore after those rows
        # are loaded. Audit `kept` is the fallback when no state row exists.
        if not case.analysis_module_ids:
            states = list(getattr(case, "case_analysis_states", None) or [])
            if states and states[-1].get("selected_ground_ids"):
                case.analysis_module_ids = [m for m in states[-1]["selected_ground_ids"] if m]
            else:
                for a in reversed(case.audit):
                    if a.get("event") == "case_analysis" and a.get("kept"):
                        case.analysis_module_ids = [m for m in a["kept"] if m]
                        break
        from ..fact_lifecycle import restore_lineage
        restore_lineage(case)
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
                  _json(_jsonable(out.draft.paragraphs)),
                  _json(_jsonable(out.pack)),
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
                  _json(_jsonable(out.validation.issues)),
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
    # Dataclasses are walked field by field rather than through asdict(): asdict
    # deep-copies every leaf, and the locked claim plan in a retrieval pack is
    # frozen into read-only mappings that cannot be copied. That made saving a
    # drafted case to Postgres fail with "cannot pickle 'mappingproxy'".
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
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
