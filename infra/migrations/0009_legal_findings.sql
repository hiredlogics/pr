-- P6.1: Legal Claim Evidence Gate - legal_findings.
--
-- One row per (case, defect type), written only by the deterministic Legal
-- Calculation Engine (pcn_appeal/legal/findings.py). A specific legal defect
-- can appear in a letter only when its row is VERIFIED; the Claim Plan rejects
-- a defect ground otherwise ("Legal defect not verified").
--
-- status moves as facts arrive (UNRESOLVED -> VERIFIED when a missing date is
-- answered); the identity of the finding (case, type) never does.
--
-- Additive and idempotent. Apply after 0008_draft_versions.sql.
-- Rollback: DROP TABLE IF EXISTS legal_findings;

CREATE TABLE IF NOT EXISTS legal_findings (
  finding_id         uuid PRIMARY KEY,
  case_id            uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  run_id             int,
  finding_type       text NOT NULL,
  status             text NOT NULL CHECK (status IN ('VERIFIED', 'NOT_SUPPORTED', 'UNRESOLVED')),
  supporting_facts   jsonb NOT NULL DEFAULT '[]'::jsonb,
  calculation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
  legal_module_id    text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, finding_type)
);
CREATE INDEX IF NOT EXISTS legal_findings_case ON legal_findings (case_id);

-- The identity of a finding is immutable; only its assessment may move.
CREATE OR REPLACE FUNCTION legal_findings_immutable() RETURNS trigger AS $$
BEGIN
  IF NEW.finding_id IS DISTINCT FROM OLD.finding_id
     OR NEW.case_id IS DISTINCT FROM OLD.case_id
     OR NEW.finding_type IS DISTINCT FROM OLD.finding_type
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'legal_findings: finding identity is immutable';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS legal_findings_immutable ON legal_findings;
CREATE TRIGGER legal_findings_immutable BEFORE UPDATE ON legal_findings
  FOR EACH ROW EXECUTE FUNCTION legal_findings_immutable();
