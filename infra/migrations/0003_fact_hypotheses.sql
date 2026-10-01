-- P2: narrative hypotheses (pcn_appeal/hypotheses.py).
--
-- A hypothesis is a possible value for a fact read from the customer's
-- account. It is NOT a fact: it is never in `facts`, so it never reaches the
-- reasoning gate, drafting or validation. Its only effect is one question;
-- the answer is written to `facts` as a confirmed fact.
--
-- Additive and idempotent. Apply after 0002_fact_graph.sql.
-- Rollback:
--   DROP TABLE IF EXISTS fact_hypotheses;

CREATE TABLE IF NOT EXISTS fact_hypotheses (
  hypothesis_id                  uuid PRIMARY KEY,
  case_id                        uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  fact_name                      varchar NOT NULL,
  possible_value                 jsonb,
  source_text                    text,
  confidence                     numeric,
  signals                        jsonb,
  rule                           varchar,
  required_confirmation_question jsonb NOT NULL,
  reason                         text,
  possible_impact                text,
  status                         varchar NOT NULL CHECK (status IN ('UNCONFIRMED', 'CONFIRMED',
                                   'REJECTED', 'SUPERSEDED', 'WITHDRAWN')),
  asked_at                       timestamptz,
  answer                         jsonb,
  resolved_fact_id               uuid,
  resolved_by                    text,
  run_id                         int,
  created_at                     timestamptz NOT NULL,
  updated_at                     timestamptz NOT NULL,
  resolved_at                    timestamptz,
  UNIQUE (case_id, fact_name, possible_value)
);
CREATE INDEX IF NOT EXISTS fact_hypotheses_open ON fact_hypotheses (case_id)
  WHERE status = 'UNCONFIRMED';
