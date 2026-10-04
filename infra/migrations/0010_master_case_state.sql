-- P8.1: the Master Case Object - master_case_state.
--
-- The sections of pcn_appeal/case_state.py that have no other home:
--
--   derivations        where each derived fact came from (source fact ids)
--   knowledge_matches  module, relationship, support / block reason
--   grounds            the grounds of each decided claim plan version
--
-- Facts, legal findings and claim plans keep their own tables; storing them
-- here as well would recreate the second authority this object removes.
--
-- Append-only by construction and by trigger: a case's state grows, and an
-- earlier generation of a section is never edited or deleted, so the lineage
-- of a decision survives every rebuild and every reload.
--
-- Additive and idempotent. Apply after 0009_legal_findings.sql.
-- Rollback: DROP TABLE IF EXISTS master_case_state;

CREATE TABLE IF NOT EXISTS master_case_state (
  entry_id    bigserial PRIMARY KEY,
  case_id     uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  section     text NOT NULL CHECK (section IN ('derivations', 'knowledge_matches', 'grounds')),
  run_id      int,
  payload     jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS master_case_state_case ON master_case_state (case_id, entry_id);
CREATE INDEX IF NOT EXISTS master_case_state_section
  ON master_case_state (case_id, section, entry_id);

-- Append-only: what the case recorded is what it recorded.
CREATE OR REPLACE FUNCTION master_case_state_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'master_case_state is append-only: record a new entry';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS master_case_state_append_only ON master_case_state;
CREATE TRIGGER master_case_state_append_only BEFORE UPDATE OR DELETE ON master_case_state
  FOR EACH ROW EXECUTE FUNCTION master_case_state_append_only();
