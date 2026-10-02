-- P5.5: AI System Integrity Audit Layer (pcn_appeal/integrity/).
--
-- Three append-only records per case:
--   case_state_history    every state transition (from, to, run, time)
--   ai_execution_logs     every model call: task, provider, model, prompt version,
--                         SHA-256 + size of input and output, timing, status.
--                         Never the prompt, payload or response text.
--   case_execution_trace  one assembled trace per completed run: stages with
--                         status / timing / counts, versions, the integrity check
--                         results and the CASE_REPORT.md text.
--
-- The fact write audit (fact_history, 0001), the question trace (audit_log
-- question_review, P3), the knowledge decision trace (audit_log knowledge_match,
-- P4) and the claim plan trace (claim_plans, 0006) already exist; the trace
-- joins them.
--
-- Additive and idempotent. Apply after 0006_claim_plan_authority.sql.
-- Rollback:
--   DROP TABLE IF EXISTS case_execution_trace;
--   DROP TABLE IF EXISTS ai_execution_logs;
--   DROP TABLE IF EXISTS case_state_history;

CREATE TABLE IF NOT EXISTS case_state_history (
  id          bigserial PRIMARY KEY,
  case_id     uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  run_id      int,
  from_state  text NOT NULL,
  to_state    text NOT NULL,
  reason      text,
  at          timestamptz NOT NULL,
  recorded_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS case_state_history_case ON case_state_history (case_id, id);

CREATE TABLE IF NOT EXISTS ai_execution_logs (
  id             bigserial PRIMARY KEY,
  case_id        uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  run_id         int,
  task           text NOT NULL,
  provider       text,
  model          text,
  prompt_version int,
  prompt_sha256  varchar(64),
  input_sha256   varchar(64) NOT NULL,
  input_chars    int,
  input_sources  jsonb NOT NULL DEFAULT '[]'::jsonb,
  images         int NOT NULL DEFAULT 0,
  output_sha256  varchar(64),
  output_chars   int,
  output_keys    jsonb NOT NULL DEFAULT '[]'::jsonb,
  duration_ms    int,
  status         text NOT NULL CHECK (status IN ('SUCCESS', 'ERROR')),
  error          text,
  at             timestamptz NOT NULL,
  recorded_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ai_execution_logs_case ON ai_execution_logs (case_id, run_id, id);
CREATE INDEX IF NOT EXISTS ai_execution_logs_task ON ai_execution_logs (task, model, at);

CREATE TABLE IF NOT EXISTS case_execution_trace (
  case_id       uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  run_id        int NOT NULL,
  execution_id  uuid NOT NULL,
  passed        boolean NOT NULL,
  failed_checks jsonb NOT NULL DEFAULT '[]'::jsonb,
  checks        jsonb NOT NULL,
  trace         jsonb NOT NULL,
  report        text,
  created_at    timestamptz DEFAULT now(),
  PRIMARY KEY (case_id, run_id)
);
CREATE INDEX IF NOT EXISTS case_execution_trace_failed ON case_execution_trace (created_at)
  WHERE NOT passed;
