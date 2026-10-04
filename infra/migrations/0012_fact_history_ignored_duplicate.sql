-- P11: allow IGNORED_DUPLICATE on fact_history (same-value stability writes).
-- The FactManager records IGNORED_DUPLICATE when a later write carries the same
-- value; the P1 check constraint omitted it.
--
-- Rollback:
--   ALTER TABLE fact_history DROP CONSTRAINT IF EXISTS fact_history_outcome_check;
--   ALTER TABLE fact_history ADD CONSTRAINT fact_history_outcome_check
--     CHECK (outcome IN ('APPLIED', 'CONFLICT', 'IGNORED', 'RETRACTED'));

ALTER TABLE fact_history DROP CONSTRAINT IF EXISTS fact_history_outcome_check;
ALTER TABLE fact_history ADD CONSTRAINT fact_history_outcome_check
  CHECK (outcome IN ('APPLIED', 'CONFLICT', 'IGNORED', 'RETRACTED', 'IGNORED_DUPLICATE'));
