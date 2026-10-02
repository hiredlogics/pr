-- P0 system integrity: run isolation, fact provenance, execution manifests.
-- Idempotent: every statement is IF NOT EXISTS, so it is safe to re-run and
-- safe on a database that already has part of it. The same statements are at
-- the end of infra/postgres_schema.sql, which `python -m pcn_appeal.store init`
-- applies; this file is the reviewable unit for an existing database.
--
-- Rollback: the columns and table are additive and nothing older reads them.
-- A previous build runs against this schema unchanged. To remove:
--   DROP TABLE IF EXISTS fact_history;
--   ALTER TABLE drafts DROP COLUMN IF EXISTS manifest, DROP COLUMN IF EXISTS run_id;
--   ALTER TABLE audit_log DROP COLUMN IF EXISTS run_id;
--   ALTER TABLE cases DROP COLUMN IF EXISTS current_run_id,
--     DROP COLUMN IF EXISTS run_status, DROP COLUMN IF EXISTS frontend_version;

-- P0.3 run isolation: the case's current analysis run, and the run of every
-- audit entry and draft. Rows written before this have NULL run_id: one run.
ALTER TABLE cases ADD COLUMN IF NOT EXISTS current_run_id int NOT NULL DEFAULT 0;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS run_status text NOT NULL DEFAULT 'NONE';
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS run_id int;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS run_id int;
CREATE INDEX IF NOT EXISTS audit_log_case_run ON audit_log (case_id, run_id);

-- P0.5 versions: the frontend build a case was opened from, and each run's
-- execution manifest (commit, KB, prompts, models, validator, input digests).
ALTER TABLE cases ADD COLUMN IF NOT EXISTS frontend_version text;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS manifest jsonb;

-- P0.4 fact provenance: every write to a fact, applied, refused (CONFLICT) or
-- removed. Append-only, like audit_log: revoke UPDATE/DELETE from the app role.
CREATE TABLE IF NOT EXISTS fact_history (
  id              bigserial PRIMARY KEY,
  case_id         uuid REFERENCES cases ON DELETE CASCADE,
  run_id          int,
  fact            text NOT NULL,
  previous        jsonb,
  new             jsonb,
  previous_status text,
  status          text,
  source_kind     text,
  source_ref      text,
  reason          text,
  outcome         text NOT NULL CHECK (outcome IN ('APPLIED', 'CONFLICT', 'RETRACTED')),
  at              timestamptz NOT NULL,
  recorded_at     timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fact_history_case ON fact_history (case_id, id);
CREATE INDEX IF NOT EXISTS fact_history_conflicts ON fact_history (case_id) WHERE outcome = 'CONFLICT';
