"""A SQLite stand-in for the Postgres case store.

Runs the real pcn_appeal.store.cases SQL (save / load / save_output /
load_output / new_case) without a database server, so "a case survives being
saved and reloaded by another process" is tested on every run rather than only
where Postgres is available (tests/test_pg_integration.py).

It covers only the tables and columns the case store touches, with Postgres
semantics where they matter: jsonb comes back decoded, bytea as bytes, and
created_at increases strictly with each insert (Postgres stamps each
transaction; SQLite's clock is too coarse for tests that save twice in a
millisecond).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from unittest import mock

from psycopg.types.json import Jsonb

_MARK = "\x00jsonb:"
sqlite3.register_adapter(Jsonb, lambda j: _MARK + json.dumps(j.obj, default=str))
sqlite3.register_adapter(date, lambda d: d.isoformat())

# created_at is a per-table counter, set by trigger, so ORDER BY created_at is
# insertion order exactly as it is across Postgres transactions.
_ORDERED = ("raw_answers", "drafts", "validations", "evidence_pages")

DDL = """
CREATE TABLE cases (case_id PRIMARY KEY, customer_id, state, driver_status, kb_release_id,
  commit_sha, llm_provider, retention_until, route, document_type, stage, scope_stop,
  document_classes, classifications, timeline, asked_questions, pending_questions,
  current_run_id DEFAULT 0, run_status DEFAULT 'NONE', frontend_version, created_at);
CREATE TABLE evidence (evidence_id PRIMARY KEY, case_id, label, kind, filename, s3_key, sha256,
  ocr_text);
CREATE TABLE evidence_pages (case_id, label, page_no, sha256, image, created_at,
  PRIMARY KEY (case_id, label, page_no));
CREATE TABLE facts (fact_id PRIMARY KEY, case_id, fact_name, fact_value, source_type, status,
  confidence, engine_status, source_kind, source_ref, excerpt, fact_ref, disputed DEFAULT 0,
  active DEFAULT 1, created_at, updated_at, UNIQUE (case_id, fact_name));
CREATE TABLE fact_sources (id INTEGER PRIMARY KEY, case_id, fact_id, fact_name, source_type,
  source_ref, excerpt, value, confidence, accepted, run_id, observed_at);
CREATE TABLE fact_conflicts (conflict_id PRIMARY KEY, case_id, fact_id, fact_name, held_value,
  held_status, held_source_type, held_source, proposed_value, proposed_status,
  proposed_source_type, proposed_source, rule, status, resolution, resolved_by, run_id,
  created_at, resolved_at);
CREATE TABLE raw_answers (case_id, question, raw_text, created_at);
CREATE TABLE drafts (draft_id PRIMARY KEY, case_id, attempt, drafter, model, prompt_version,
  structured, retrieval_pack, state, letter, evidence_list, outcome, no_ground_reason,
  run_id, manifest, created_at);
CREATE TABLE validations (draft_id, passed, issues, validator_version, created_at);
CREATE TABLE review_queue (case_id, reason, assigned_to, sla_due, resolution, resolved_at);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, case_id, actor, event, detail, run_id, at);
CREATE TABLE fact_hypotheses (hypothesis_id PRIMARY KEY, case_id, fact_name, possible_value,
  source_text, confidence, signals, rule, required_confirmation_question, reason, possible_impact,
  status, asked_at, answer, resolved_fact_id, resolved_by, run_id, created_at, updated_at,
  resolved_at, UNIQUE (case_id, fact_name, possible_value));
CREATE TABLE fact_history (id INTEGER PRIMARY KEY, case_id, run_id, fact, fact_id, previous,
  new, previous_status, status, source_kind, source_ref, source_type, changed_by, reason,
  outcome, at, recorded_at);
CREATE TABLE knowledge_release (release_id PRIMARY KEY, source_document, source_document_hash,
  compiled_digest, relations_version, parser_version, document_version, parent_release_id,
  created_at, created_by, reason, module_count, relationship_count, manifest, drift);
CREATE TABLE knowledge_modules (knowledge_id PRIMARY KEY, module_id UNIQUE, name, category, version,
  status, effective_from, effective_to, source_document, source_reference, source_hash, release_id,
  metadata, created_at, updated_at, updated_by);
CREATE TABLE knowledge_rules (rule_id PRIMARY KEY, knowledge_id, rule_type, rule_definition,
  created_at);
CREATE TABLE knowledge_required_facts (id PRIMARY KEY, knowledge_id, fact_name, requirement_type,
  metadata);
CREATE TABLE knowledge_evidence_requirements (id PRIMARY KEY, knowledge_id, evidence_type,
  requirement);
CREATE TABLE knowledge_restrictions (id PRIMARY KEY, knowledge_id, restriction_type, content,
  metadata);
CREATE TABLE graph_nodes (node_id PRIMARY KEY, node_type, entity_id, metadata,
  UNIQUE (node_type, entity_id));
CREATE TABLE graph_edges (edge_id PRIMARY KEY, source_node, relationship_type, target_node,
  confidence, origin, status, metadata, updated_by, updated_at);
CREATE TABLE knowledge_release_items (release_id, item_type, item_id, content_hash, content,
  PRIMARY KEY (release_id, item_type, item_id));
CREATE TABLE knowledge_changes (change_id PRIMARY KEY, entity_type, entity_id, action, version,
  changed_by, changed_at, reason, before, after);
CREATE TABLE claim_plans (claim_plan_id PRIMARY KEY, case_id NOT NULL, analysis_run_id NOT NULL,
  run_number, version NOT NULL, status NOT NULL, created_at, confirmed_at, locked_at,
  superseded_at, superseded_by REFERENCES claim_plans, inputs_digest, plan_digest,
  code_version, kb_version,
  prompt_version, model_version, facts_used, relationships_used, trust,
  material_fact_accounting, UNIQUE (case_id, version));
CREATE UNIQUE INDEX claim_plans_one_locked ON claim_plans (case_id) WHERE status = 'LOCKED';
CREATE TABLE claim_plan_items (item_id PRIMARY KEY,
  claim_plan_id NOT NULL REFERENCES claim_plans, ordinal NOT NULL,
  knowledge_id NOT NULL, module_id NOT NULL, claim_type, status NOT NULL, decision NOT NULL,
  reason NOT NULL, supporting_facts, evidence_refs, relationships, priority, topic,
  UNIQUE (claim_plan_id, module_id));
CREATE TABLE case_state_history (id INTEGER PRIMARY KEY, case_id NOT NULL, run_id,
  from_state NOT NULL, to_state NOT NULL, reason, at NOT NULL, recorded_at);
CREATE TABLE ai_execution_logs (id INTEGER PRIMARY KEY, case_id NOT NULL, run_id, task NOT NULL,
  provider, model, prompt_version, prompt_sha256, input_sha256 NOT NULL, input_chars,
  input_sources, images, output_sha256, output_chars, output_keys, duration_ms,
  status NOT NULL, error, at NOT NULL, recorded_at);
CREATE TABLE case_execution_trace (case_id NOT NULL, run_id NOT NULL, execution_id NOT NULL,
  passed NOT NULL, failed_checks, checks NOT NULL, trace NOT NULL, report, created_at,
  PRIMARY KEY (case_id, run_id));
CREATE TABLE draft_versions (draft_id PRIMARY KEY, case_id NOT NULL, claim_plan_id REFERENCES claim_plans,
  run_id, version NOT NULL, attempt, parent_draft_id, model, prompt_version, content_hash NOT NULL,
  content NOT NULL, validation_status NOT NULL, issues, grounding, judge, released NOT NULL DEFAULT 0,
  created_at, UNIQUE (case_id, version), UNIQUE (case_id, claim_plan_id, content_hash));
CREATE TABLE legal_findings (finding_id PRIMARY KEY, case_id NOT NULL, run_id,
  finding_type NOT NULL, status NOT NULL CHECK (status IN ('VERIFIED', 'NOT_SUPPORTED', 'UNRESOLVED')),
  supporting_facts, calculation_result, legal_module_id, created_at, updated_at,
  UNIQUE (case_id, finding_type));
CREATE TRIGGER legal_findings_immutable BEFORE UPDATE ON legal_findings
  WHEN NEW.finding_id IS NOT OLD.finding_id OR NEW.case_id IS NOT OLD.case_id
    OR NEW.finding_type IS NOT OLD.finding_type OR NEW.created_at IS NOT OLD.created_at
  BEGIN SELECT RAISE(ABORT, 'legal_findings: finding identity is immutable'); END;
-- The lock, as 0006_claim_plan_authority.sql enforces it in Postgres.
CREATE TRIGGER draft_versions_immutable BEFORE UPDATE ON draft_versions
  WHEN NEW.draft_id IS NOT OLD.draft_id OR NEW.case_id IS NOT OLD.case_id
    OR NEW.claim_plan_id IS NOT OLD.claim_plan_id OR NEW.version IS NOT OLD.version
    OR NEW.model IS NOT OLD.model OR NEW.prompt_version IS NOT OLD.prompt_version
    OR NEW.content_hash IS NOT OLD.content_hash OR NEW.content IS NOT OLD.content
    OR (OLD.released AND NOT NEW.released)
  BEGIN SELECT RAISE(ABORT, 'a draft version is immutable: write a new version'); END;
CREATE TRIGGER claim_plan_items_no_update BEFORE UPDATE ON claim_plan_items
  BEGIN SELECT RAISE(ABORT, 'claim plan items are immutable'); END;
CREATE TRIGGER claim_plan_items_locked BEFORE INSERT ON claim_plan_items
  WHEN (SELECT status FROM claim_plans WHERE claim_plan_id = NEW.claim_plan_id)
       IN ('LOCKED', 'SUPERSEDED')
  BEGIN SELECT RAISE(ABORT, 'claim plan is locked: create a new version'); END;
CREATE TRIGGER claim_plans_locked BEFORE UPDATE ON claim_plans
  WHEN OLD.status IN ('LOCKED', 'SUPERSEDED') AND (
       NOT (NEW.status = OLD.status OR (OLD.status = 'LOCKED' AND NEW.status = 'SUPERSEDED'))
       OR NEW.version IS NOT OLD.version OR NEW.inputs_digest IS NOT OLD.inputs_digest
       OR NEW.plan_digest IS NOT OLD.plan_digest OR NEW.locked_at IS NOT OLD.locked_at
       OR NEW.facts_used IS NOT OLD.facts_used OR NEW.trust IS NOT OLD.trust)
  BEGIN SELECT RAISE(ABORT, 'claim plan is locked: only LOCKED -> SUPERSEDED'); END;
""" + "".join(
    f"CREATE TRIGGER {t}_order AFTER INSERT ON {t} BEGIN UPDATE {t} SET created_at = "
    f"(SELECT COALESCE(MAX(created_at), 0) + 1 FROM {t}) WHERE rowid = NEW.rowid; END;\n"
    for t in _ORDERED)


def _decoded(value):
    if isinstance(value, str) and value.startswith(_MARK):
        return json.loads(value[len(_MARK):])
    return value


class _Cursor:
    def __init__(self, cur: sqlite3.Cursor):
        self._cur = cur

    def execute(self, sql: str, params=()):
        self._cur.execute(sql.replace("%s", "?"), tuple(params))
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return None if row is None else tuple(_decoded(v) for v in row)

    def fetchall(self):
        return [tuple(_decoded(v) for v in row) for row in self._cur.fetchall()]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def execute(self, sql: str, params=()):
        return _Cursor(self._db.cursor()).execute(sql, params)

    def cursor(self):
        return _Cursor(self._db.cursor())

    def commit(self):
        self._db.commit()


def make_store() -> tuple[sqlite3.Connection, object]:
    """A fresh in-memory database and a `connect()` drop-in for it."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.create_function("now", 0, lambda: "now")
    # Only the claim plan tables declare foreign keys: superseded_by must name
    # a stored plan, as it must in Postgres.
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(DDL)

    @contextmanager
    def connect(autocommit: bool = False):
        yield _Conn(db)

    return db, connect


def install(test) -> sqlite3.Connection:
    """Point the case store (and the API's "is there a database" check) at a
    fresh SQLite database for the duration of one test."""
    db, connect = make_store()
    for patcher in (mock.patch("pcn_appeal.store.cases.connect", connect),
                    mock.patch("pcn_appeal.knowledge_ingestion.store.connect", connect),
                    mock.patch("pcn_appeal.store.db.enabled", return_value=True)):
        patcher.start()
        test.addCleanup(patcher.stop)
    test.addCleanup(db.close)
    return db


def count(db: sqlite3.Connection, table: str, where: str = "", params=()) -> int:
    sql = f"SELECT count(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return db.execute(sql, params).fetchone()[0]
