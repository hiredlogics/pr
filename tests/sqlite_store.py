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
CREATE TABLE fact_history (id INTEGER PRIMARY KEY, case_id, run_id, fact, fact_id, previous,
  new, previous_status, status, source_kind, source_ref, source_type, changed_by, reason,
  outcome, at, recorded_at);
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
                    mock.patch("pcn_appeal.store.db.enabled", return_value=True)):
        patcher.start()
        test.addCleanup(patcher.stop)
    test.addCleanup(db.close)
    return db


def count(db: sqlite3.Connection, table: str, where: str = "", params=()) -> int:
    sql = f"SELECT count(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return db.execute(sql, params).fetchone()[0]
