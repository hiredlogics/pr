-- Align fact_history outcome check with rows already written as IGNORED_DUPLICATE.
ALTER TABLE fact_history DROP CONSTRAINT IF EXISTS fact_history_outcome_check;
ALTER TABLE fact_history ADD CONSTRAINT fact_history_outcome_check
  CHECK (outcome IN ('APPLIED', 'CONFLICT', 'IGNORED', 'IGNORED_DUPLICATE', 'RETRACTED'));
