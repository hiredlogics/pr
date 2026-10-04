-- P8.3: DocumentBaseline and case_analysis_state.
--
-- Document truth (notice facts, calculations, verified findings) is recorded
-- before Case Intelligence runs. Each CI proposal run is stored against the
-- baseline version it saw. Append-only: a later customer delta adds a
-- generation; it never edits an earlier baseline or analysis row.
--
-- Additive and idempotent. Apply after 0010_master_case_state.sql.
-- Rollback: DROP TABLE IF EXISTS case_analysis_state; DROP TABLE IF EXISTS document_baselines;

CREATE TABLE IF NOT EXISTS document_baselines (
  entry_id    bigserial PRIMARY KEY,
  case_id     uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  version     int NOT NULL,
  digest      text NOT NULL,
  payload     jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, version)
);
CREATE INDEX IF NOT EXISTS document_baselines_case ON document_baselines (case_id, version);

CREATE OR REPLACE FUNCTION document_baselines_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'document_baselines is append-only: record a new version';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS document_baselines_append_only ON document_baselines;
CREATE TRIGGER document_baselines_append_only BEFORE UPDATE OR DELETE ON document_baselines
  FOR EACH ROW EXECUTE FUNCTION document_baselines_append_only();

CREATE TABLE IF NOT EXISTS case_analysis_state (
  entry_id                   bigserial PRIMARY KEY,
  case_id                    uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  analysis_version           int NOT NULL,
  document_baseline_version  int,
  customer_analysis_version  int,
  candidate_ground_ids       jsonb,
  selected_ground_ids        jsonb,
  verified_ground_ids        jsonb,
  rejected_ground_ids        jsonb,
  created_by                 text,
  timestamp                  timestamptz NOT NULL DEFAULT now(),
  payload                    jsonb NOT NULL,
  UNIQUE (case_id, analysis_version)
);
CREATE INDEX IF NOT EXISTS case_analysis_state_case ON case_analysis_state (case_id, analysis_version);

CREATE OR REPLACE FUNCTION case_analysis_state_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'case_analysis_state is append-only: record a new version';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS case_analysis_state_append_only ON case_analysis_state;
CREATE TRIGGER case_analysis_state_append_only BEFORE UPDATE OR DELETE ON case_analysis_state
  FOR EACH ROW EXECUTE FUNCTION case_analysis_state_append_only();
