-- P11.1: persist immutable release identity on each case.
-- Rollback: ALTER TABLE cases DROP COLUMN IF EXISTS release_metadata;

ALTER TABLE cases
  ADD COLUMN IF NOT EXISTS release_metadata jsonb;

COMMENT ON COLUMN cases.release_metadata IS
  'P11.1 immutable release identity (commit, kb_release, ontology, prompts, models).';
