-- P5: Claim Plan Authority (pcn_appeal/engines/claim_plan_authority.py).
--
-- One claim plan per analysed version of a case: the single source of truth
-- for which claims a letter argues (SUPPORTED), which it does not (REJECTED,
-- with why) and what is still open (UNRESOLVED: facts or evidence missing).
-- Drafting reads only the LOCKED plan; validation refuses any argument that is
-- not in it.
--
-- LOCKED is immutable, enforced here as well as in code:
--   * claim_plan_items are never updated, and no item can be added to a plan
--     once it is LOCKED or SUPERSEDED;
--   * a LOCKED plan's row can change in one way only: LOCKED -> SUPERSEDED
--     (with superseded_at / superseded_by) when a new version replaces it.
-- At most one LOCKED plan per case.
--
-- Additive and idempotent. Apply after 0005_knowledge_graph.sql.
-- Rollback:
--   DROP TABLE IF EXISTS claim_plan_items;
--   DROP TABLE IF EXISTS claim_plans;
--   DROP FUNCTION IF EXISTS claim_plan_items_guard();
--   DROP FUNCTION IF EXISTS claim_plans_guard();

CREATE TABLE IF NOT EXISTS claim_plans (
  claim_plan_id      uuid PRIMARY KEY,
  case_id            uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
  analysis_run_id    uuid NOT NULL,
  run_number         integer NOT NULL DEFAULT 0,
  version            integer NOT NULL CHECK (version >= 1),
  status             varchar(20) NOT NULL CHECK (status IN ('DRAFT', 'CONFIRMED', 'LOCKED',
                                                            'SUPERSEDED')),
  created_at         timestamptz NOT NULL,
  confirmed_at       timestamptz,
  locked_at          timestamptz,
  superseded_at      timestamptz,
  superseded_by      uuid REFERENCES claim_plans,
  inputs_digest      varchar(64) NOT NULL,
  plan_digest        varchar(64),
  -- Client trust: what produced the plan, and what it was decided from.
  code_version       text,
  kb_version         jsonb NOT NULL DEFAULT '{}'::jsonb,
  prompt_version     jsonb NOT NULL DEFAULT '{}'::jsonb,
  model_version      jsonb NOT NULL DEFAULT '{}'::jsonb,
  facts_used         jsonb NOT NULL DEFAULT '{}'::jsonb,
  relationships_used jsonb NOT NULL DEFAULT '[]'::jsonb,
  trust              jsonb NOT NULL DEFAULT '{}'::jsonb,
  material_fact_accounting jsonb NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE (case_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS claim_plans_one_locked ON claim_plans (case_id)
  WHERE status = 'LOCKED';

CREATE TABLE IF NOT EXISTS claim_plan_items (
  item_id            uuid PRIMARY KEY,
  claim_plan_id      uuid NOT NULL REFERENCES claim_plans ON DELETE CASCADE,
  ordinal            integer NOT NULL,
  knowledge_id       uuid NOT NULL,
  module_id          varchar(50) NOT NULL,
  claim_type         varchar(50),
  status             varchar(20) NOT NULL CHECK (status IN ('SUPPORTED', 'REJECTED', 'UNRESOLVED')),
  decision           varchar(40) NOT NULL,
  reason             text NOT NULL,
  supporting_facts   jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence_refs      jsonb NOT NULL DEFAULT '[]'::jsonb,
  relationships      jsonb NOT NULL DEFAULT '[]'::jsonb,
  priority           integer,
  topic              text,
  UNIQUE (claim_plan_id, module_id),
  UNIQUE (claim_plan_id, ordinal)
);
CREATE INDEX IF NOT EXISTS claim_plan_items_module ON claim_plan_items (module_id, status);

CREATE OR REPLACE FUNCTION claim_plan_items_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'claim plan items are immutable (item %)', OLD.item_id;
  END IF;
  IF EXISTS (SELECT 1 FROM claim_plans p WHERE p.claim_plan_id = NEW.claim_plan_id
             AND p.status IN ('LOCKED', 'SUPERSEDED')) THEN
    RAISE EXCEPTION 'claim plan % is locked: create a new version', NEW.claim_plan_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS claim_plan_items_guard ON claim_plan_items;
CREATE TRIGGER claim_plan_items_guard BEFORE INSERT OR UPDATE ON claim_plan_items
  FOR EACH ROW EXECUTE FUNCTION claim_plan_items_guard();

CREATE OR REPLACE FUNCTION claim_plans_guard() RETURNS trigger AS $$
BEGIN
  IF OLD.status IN ('LOCKED', 'SUPERSEDED') THEN
    IF NOT (NEW.status = OLD.status OR (OLD.status = 'LOCKED' AND NEW.status = 'SUPERSEDED'))
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.case_id IS DISTINCT FROM OLD.case_id
       OR NEW.inputs_digest IS DISTINCT FROM OLD.inputs_digest
       OR NEW.plan_digest IS DISTINCT FROM OLD.plan_digest
       OR NEW.locked_at IS DISTINCT FROM OLD.locked_at
       OR NEW.facts_used IS DISTINCT FROM OLD.facts_used
       OR NEW.relationships_used IS DISTINCT FROM OLD.relationships_used
       OR NEW.trust IS DISTINCT FROM OLD.trust THEN
      RAISE EXCEPTION 'claim plan % is %: only LOCKED -> SUPERSEDED is allowed',
        OLD.claim_plan_id, OLD.status;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS claim_plans_guard ON claim_plans;
CREATE TRIGGER claim_plans_guard BEFORE UPDATE ON claim_plans
  FOR EACH ROW EXECUTE FUNCTION claim_plans_guard();
