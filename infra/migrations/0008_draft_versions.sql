-- P6: Drafting and Validation Intelligence - draft_versions.
--
-- Every draft the system wrote for a case, immutable, tied to the Claim Plan it
-- was written from. draft_id is uuid5(case, claim plan, content hash): the same
-- content under the same plan is the same row however often it is regenerated or
-- reloaded; a trimmed draft (failing sentences removed) is its own row with
-- parent_draft_id set.
--
-- What is immutable: the draft itself (content, hash, plan, model, prompt
-- version, case). What moves: how it fared (validation status, issues, grounding,
-- shadow-judge verdict, released).
--
-- Additive and idempotent. Apply after 0007_system_integrity_audit.sql.
-- Rollback: DROP TABLE IF EXISTS draft_versions;

CREATE TABLE IF NOT EXISTS draft_versions (
  draft_id          uuid PRIMARY KEY,
  case_id           uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  claim_plan_id     uuid REFERENCES claim_plans,
  run_id            int,
  version           int NOT NULL,
  attempt           int,
  parent_draft_id   uuid,
  model             text,
  prompt_version    int,
  content_hash      varchar(64) NOT NULL,
  content           jsonb NOT NULL,
  validation_status text NOT NULL CHECK (validation_status IN ('PASSED', 'FAILED', 'NOT_RUN')),
  issues            jsonb NOT NULL DEFAULT '[]'::jsonb,
  grounding         jsonb NOT NULL DEFAULT '[]'::jsonb,
  judge             jsonb,
  released          boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, version),
  UNIQUE (case_id, claim_plan_id, content_hash)
);
CREATE INDEX IF NOT EXISTS draft_versions_case ON draft_versions (case_id, version);
CREATE INDEX IF NOT EXISTS draft_versions_plan ON draft_versions (claim_plan_id);

CREATE OR REPLACE FUNCTION draft_versions_immutable() RETURNS trigger AS $$
BEGIN
  IF NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.case_id IS DISTINCT FROM OLD.case_id
     OR NEW.claim_plan_id IS DISTINCT FROM OLD.claim_plan_id
     OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.model IS DISTINCT FROM OLD.model
     OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
     OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
     OR NEW.content IS DISTINCT FROM OLD.content
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'a draft version is immutable: write a new version';
  END IF;
  -- a released draft stays released
  IF OLD.released AND NOT NEW.released THEN
    RAISE EXCEPTION 'a released draft stays released';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS draft_versions_immutable ON draft_versions;
CREATE TRIGGER draft_versions_immutable BEFORE UPDATE ON draft_versions
  FOR EACH ROW EXECUTE FUNCTION draft_versions_immutable();
