-- P1 Fact Graph: facts (one current node per case and fact), fact_history,
-- fact_sources, fact_conflicts. Requires 0001_p0_system_integrity.sql.
--
-- Idempotent. On a database that still has the append-only facts table
-- (column `superseded`), that table is renamed facts_v1 - kept, never dropped -
-- and backfilled into the new tables: the current (non-superseded) row of each
-- fact becomes its node, and every v1 row becomes a fact_history entry.
--
-- Rollback (loses fact writes made after the migration; history is kept):
--   DROP TABLE IF EXISTS fact_conflicts, fact_sources;
--   DROP TABLE IF EXISTS facts;  ALTER TABLE facts_v1 RENAME TO facts;
-- An older build reads and writes the v1 shape only, so it needs that rollback.

-- fact_history gains the graph node, who changed it, the source type and the
-- IGNORED outcome (a placeholder that did not replace a customer's value).
ALTER TABLE fact_history ADD COLUMN IF NOT EXISTS fact_id uuid;
ALTER TABLE fact_history ADD COLUMN IF NOT EXISTS changed_by text;
ALTER TABLE fact_history ADD COLUMN IF NOT EXISTS source_type text;
ALTER TABLE fact_history DROP CONSTRAINT IF EXISTS fact_history_outcome_check;
ALTER TABLE fact_history ADD CONSTRAINT fact_history_outcome_check
  CHECK (outcome IN ('APPLIED', 'CONFLICT', 'IGNORED', 'IGNORED_DUPLICATE', 'RETRACTED'));

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema() AND table_name = 'facts'
               AND column_name = 'superseded') THEN
    ALTER TABLE facts RENAME TO facts_v1;
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS facts (
  fact_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id       uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  fact_name     varchar NOT NULL,
  fact_value    jsonb,
  source_type   varchar NOT NULL CHECK (source_type IN ('DOCUMENT', 'CUSTOMER_ANSWER',
                  'CUSTOMER_FREE_TEXT', 'EVIDENCE', 'CALCULATION', 'SYSTEM_DERIVED')),
  status        varchar NOT NULL CHECK (status IN ('CONFIRMED', 'EXTRACTED', 'DERIVED',
                  'UNKNOWN', 'DISPUTED', 'CONFLICT')),
  confidence    numeric,
  -- What the engines need to rebuild the Fact exactly (models.Fact).
  engine_status varchar NOT NULL,                   -- FactStatus: EXTRACTED/CONFIRMED/CORRECTED/ANSWERED/DERIVED/UNCERTAIN
  source_kind   varchar NOT NULL,                   -- SourceKind
  source_ref    text,
  excerpt       text,
  fact_ref      text NOT NULL,                      -- "F-<name>": what letter sentences cite
  disputed      boolean NOT NULL DEFAULT false,     -- a NEEDS_CONFIRMATION conflict is open
  active        boolean NOT NULL DEFAULT true,      -- false once retracted; never deleted
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, fact_name)
);

-- Every reading of a fact from every source, accepted or not.
CREATE TABLE IF NOT EXISTS fact_sources (
  id          bigserial PRIMARY KEY,
  case_id     uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  fact_id     uuid,
  fact_name   varchar NOT NULL,
  source_type varchar NOT NULL,
  source_ref  text,
  excerpt     text,
  value       jsonb,
  confidence  numeric,
  accepted    boolean NOT NULL,
  run_id      int,
  observed_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS fact_sources_case ON fact_sources (case_id, fact_name);

-- Writes FactManager refused, and how each was settled.
CREATE TABLE IF NOT EXISTS fact_conflicts (
  conflict_id          uuid PRIMARY KEY,
  case_id              uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  fact_id              uuid,
  fact_name            varchar NOT NULL,
  held_value           jsonb,
  held_status          varchar,
  held_source_type     varchar,
  held_source          text,
  proposed_value       jsonb,
  proposed_status      varchar,
  proposed_source_type varchar,
  proposed_source      text,
  rule                 varchar NOT NULL,
  status               varchar NOT NULL CHECK (status IN ('NEEDS_CONFIRMATION',
                         'KEPT_EXISTING', 'RESOLVED')),
  resolution           jsonb,
  resolved_by          text,
  run_id               int,
  created_at           timestamptz NOT NULL,
  resolved_at          timestamptz
);
CREATE INDEX IF NOT EXISTS fact_conflicts_open ON fact_conflicts (case_id)
  WHERE status <> 'RESOLVED';

-- Backfill from facts_v1 (only when it exists and the new table is empty for it).
DO $$
BEGIN
  IF to_regclass('facts_v1') IS NOT NULL THEN
    INSERT INTO facts (case_id, fact_name, fact_value, source_type, status, confidence,
                       engine_status, source_kind, source_ref, excerpt, fact_ref,
                       created_at, updated_at)
    SELECT DISTINCT ON (case_id, name)
           case_id, name, value,
           CASE source_kind WHEN 'ANSWER' THEN 'CUSTOMER_ANSWER'
                            WHEN 'CUSTOMER_FREE_TEXT' THEN 'CUSTOMER_FREE_TEXT'
                            WHEN 'CALCULATION' THEN 'CALCULATION'
                            ELSE 'DOCUMENT' END,
           CASE status WHEN 'CONFIRMED' THEN 'CONFIRMED' WHEN 'CORRECTED' THEN 'CONFIRMED'
                       WHEN 'ANSWERED' THEN 'CONFIRMED' WHEN 'EXTRACTED' THEN 'EXTRACTED'
                       WHEN 'UNCERTAIN' THEN 'UNKNOWN' ELSE 'DERIVED' END,
           confidence, status, source_kind, source_ref, excerpt,
           COALESCE(fact_id, 'F-' || name), created_at, created_at
    FROM facts_v1 WHERE NOT superseded
    ORDER BY case_id, name, created_at DESC
    ON CONFLICT (case_id, fact_name) DO NOTHING;

    INSERT INTO fact_history (case_id, fact_id, fact, new, status, source_kind,
                              source_ref, changed_by, reason, outcome, at)
    SELECT v.case_id, f.fact_id, v.name, v.value, v.status, v.source_kind, v.source_ref,
           'migration', 'backfill_facts_v1', 'APPLIED', v.created_at
    FROM facts_v1 v LEFT JOIN facts f ON f.case_id = v.case_id AND f.fact_name = v.name
    WHERE NOT EXISTS (SELECT 1 FROM fact_history h WHERE h.reason = 'backfill_facts_v1'
                      AND h.case_id = v.case_id);
  END IF;
END
$$;
