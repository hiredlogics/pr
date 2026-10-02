-- Postgres 16 + pgvector. System of record for cases, facts, KB versions, prompts, audit.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- cases
CREATE TABLE cases (
  case_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id    uuid NOT NULL,
  state          text NOT NULL,                      -- CaseState
  driver_status  text NOT NULL DEFAULT 'UNIDENTIFIED',
  kb_release_id  text,                               -- KB version used (reproducibility)
  -- Which code and which provider handled this case. The KB release alone does
  -- not identify a run: the same release under a different build or on the demo
  -- stand-in produces a different letter, and a case that cannot name its build
  -- cannot confirm that a deployed fix applied to it.
  commit_sha     text,
  llm_provider   text,                               -- openai / demo
  appeal_deadline date,                              -- from notice; drives reminders
  created_at     timestamptz DEFAULT now(),
  retention_until date NOT NULL                      -- UK GDPR retention policy
);

-- Added after the table shipped. init_schema() skips a CREATE TABLE that already
-- exists, so a populated database never sees the columns above; these are how it
-- gets them. IF NOT EXISTS makes both paths idempotent.
ALTER TABLE cases ADD COLUMN IF NOT EXISTS commit_sha text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS llm_provider text;
-- Phase 2 intake: the route a case was sent to and the classification behind it.
ALTER TABLE cases ADD COLUMN IF NOT EXISTS route text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS document_type text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS stage text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS scope_stop text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS document_classes jsonb NOT NULL DEFAULT '{}';
ALTER TABLE cases ADD COLUMN IF NOT EXISTS classifications jsonb NOT NULL DEFAULT '{}';
ALTER TABLE cases ADD COLUMN IF NOT EXISTS timeline jsonb NOT NULL DEFAULT '[]';
-- The question state a rehydrated case needs to carry on where it stopped.
-- asked_questions was rebuilt from raw_answers, which also holds internal keys
-- (_material_source_texts), so those came back as "already asked" questions.
ALTER TABLE cases ADD COLUMN IF NOT EXISTS asked_questions jsonb;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS pending_questions jsonb;

CREATE TABLE evidence (
  evidence_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id      uuid REFERENCES cases ON DELETE CASCADE,
  label        text NOT NULL,                        -- in-case id ("E1"); facts cite <label>#p<n>
  kind         text NOT NULL,
  filename     text,                                 -- what the customer called it
  -- Where the file IS: a blob/object URL or key. Nullable because a document can
  -- be read straight from a request body and never stored, but a null here means
  -- the original is unrecoverable - which is a retention and evidence problem, not
  -- a cosmetic one. Never put the filename in this column.
  s3_key       text,
  sha256       text NOT NULL,                        -- tamper evidence
  ocr_text     text,
  special_category boolean DEFAULT false,            -- medical/disability docs: restricted access
  uploaded_at  timestamptz DEFAULT now()
);

-- facts: see the P1 Fact Graph section at the end (one node per case and fact,
-- with fact_history, fact_sources and fact_conflicts). The append-only v1 table
-- that stood here is renamed facts_v1 by that section on an existing database.

-- raw customer wording: audit only, never readable by the drafting service role
CREATE TABLE raw_answers (
  case_id uuid REFERENCES cases ON DELETE CASCADE,
  question text, raw_text text, created_at timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------- knowledge base
CREATE TABLE kb_modules (
  module_id text, version text,
  route text NOT NULL, topic text NOT NULL,
  use_when jsonb NOT NULL, do_not_use_when jsonb NOT NULL,
  core_proposition text NOT NULL, required_facts text[], evidence_helpful text[],
  legal_basis text[], drafting_notes text, prohibited_claims text[], building_blocks text[],
  strength int DEFAULT 50,
  status text NOT NULL CHECK (status IN ('DRAFT','REVIEW','ACTIVE','DISABLED')),
  effective_from date, effective_to date,
  last_legal_review date, reviewed_by text, change_notes text,
  PRIMARY KEY (module_id, version)
);

CREATE TABLE kb_blocks (
  block_id text, version text, text text NOT NULL,
  requires_evidence_any text[], requires_facts text[],
  status text NOT NULL, PRIMARY KEY (block_id, version)
);

CREATE TABLE kb_embeddings (                         -- RAG index over modules + blocks
  item_id text, version text, kind text,
  embedding vector(1024), tsv tsvector,
  PRIMARY KEY (item_id, version)
);
CREATE INDEX ON kb_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kb_embeddings USING gin (tsv);

CREATE TABLE kb_releases (                           -- immutable snapshot an admin publishes
  kb_release_id text PRIMARY KEY, published_at timestamptz, published_by text,
  manifest jsonb NOT NULL                            -- {module_id: version, block_id: version, ...}
);

CREATE TABLE code_versions (
  version_id text PRIMARY KEY, label text, effective_from date, effective_to date,
  applies_to_ata text[], provisions jsonb, verified boolean DEFAULT false, verified_by text
);

CREATE TABLE prompts (                               -- admin-editable prompts (Phase 10)
  prompt_id text, version int, task text, body text, active boolean,
  PRIMARY KEY (prompt_id, version)
);

-- ---------------------------------------------------------------- outputs
CREATE TABLE drafts (
  draft_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id uuid REFERENCES cases ON DELETE CASCADE,
  attempt int, drafter text, model text, prompt_version int,
  structured jsonb NOT NULL,                         -- sentences + refs
  retrieval_pack jsonb NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE validations (
  draft_id uuid REFERENCES drafts ON DELETE CASCADE,
  passed boolean, issues jsonb, validator_version text, created_at timestamptz DEFAULT now()
);

CREATE TABLE review_queue (
  case_id uuid REFERENCES cases, reason text, assigned_to text, sla_due timestamptz,
  resolution text, resolved_at timestamptz
);

CREATE TABLE audit_log (                             -- append-only; revoke UPDATE/DELETE
  id bigserial PRIMARY KEY, case_id uuid, actor text, event text, detail jsonb,
  at timestamptz DEFAULT now()
);

-- Page images the vision model read (JPEG, as ingest.py normalised them).
-- Without them a case reloaded by another process has documents with no pages:
-- the both-sides gate then counts 0 pages and asks for a notice the customer
-- already uploaded. Same retention as the case (cascade).
CREATE TABLE IF NOT EXISTS evidence_pages (
  case_id   uuid REFERENCES cases ON DELETE CASCADE,
  label     text NOT NULL,                           -- EvidenceItem id ("E1")
  page_no   int NOT NULL,
  sha256    text NOT NULL,
  image     bytea NOT NULL,
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (case_id, label, page_no)
);

-- What the customer was given, so the letter and its PDF survive a restart:
-- the structured draft alone did not record the released state or the text.
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS letter text;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS evidence_list jsonb;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS outcome jsonb;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS no_ground_reason text;

-- ---------------------------------------------------------------- P0 system integrity
-- infra/migrations/0001_p0_system_integrity.sql (keep the two in step)

ALTER TABLE cases ADD COLUMN IF NOT EXISTS current_run_id int NOT NULL DEFAULT 0;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS run_status text NOT NULL DEFAULT 'NONE';
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS run_id int;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS run_id int;
CREATE INDEX IF NOT EXISTS audit_log_case_run ON audit_log (case_id, run_id);

ALTER TABLE cases ADD COLUMN IF NOT EXISTS frontend_version text;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS manifest jsonb;

CREATE TABLE IF NOT EXISTS fact_history (
  id              bigserial PRIMARY KEY,
  case_id         uuid REFERENCES cases ON DELETE CASCADE,
  run_id          int,
  fact            text NOT NULL,
  previous        jsonb,
  new             jsonb,
  previous_status text,
  status          text,
  source_kind     text,
  source_ref      text,
  reason          text,
  outcome         text NOT NULL CHECK (outcome IN ('APPLIED', 'CONFLICT', 'RETRACTED')),
  at              timestamptz NOT NULL,
  recorded_at     timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fact_history_case ON fact_history (case_id, id);
CREATE INDEX IF NOT EXISTS fact_history_conflicts ON fact_history (case_id) WHERE outcome = 'CONFLICT';

-- ---------------------------------------------------------------- P1 fact graph
-- infra/migrations/0002_fact_graph.sql (keep the two in step)
ALTER TABLE fact_history ADD COLUMN IF NOT EXISTS fact_id uuid;
ALTER TABLE fact_history ADD COLUMN IF NOT EXISTS changed_by text;
ALTER TABLE fact_history ADD COLUMN IF NOT EXISTS source_type text;
ALTER TABLE fact_history DROP CONSTRAINT IF EXISTS fact_history_outcome_check;
ALTER TABLE fact_history ADD CONSTRAINT fact_history_outcome_check
  CHECK (outcome IN ('APPLIED', 'CONFLICT', 'IGNORED', 'RETRACTED'));

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

-- 0003_fact_hypotheses.sql (P2)

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

-- 0004_knowledge_graph.sql (P4): governance log. Its knowledge_nodes /
-- knowledge_edges are superseded by 0005 and not created here.

CREATE TABLE IF NOT EXISTS knowledge_changes (
  change_id    uuid PRIMARY KEY,
  entity_type  varchar NOT NULL CHECK (entity_type IN ('NODE', 'EDGE')),
  entity_id    varchar NOT NULL,
  action       varchar NOT NULL CHECK (action IN ('SEED', 'CREATE', 'UPDATE', 'DISABLE', 'REMOVE')),
  version      varchar NOT NULL,
  changed_by   text NOT NULL,
  changed_at   timestamptz NOT NULL,
  reason       text NOT NULL,
  before       jsonb,
  after        jsonb
);
CREATE INDEX IF NOT EXISTS knowledge_changes_entity ON knowledge_changes (entity_type, entity_id);

-- 0005_knowledge_graph.sql (P4b)

CREATE TABLE IF NOT EXISTS knowledge_release (
  release_id            uuid PRIMARY KEY,
  source_document       text NOT NULL,
  source_document_hash  varchar(64) NOT NULL,
  compiled_digest       varchar(64) NOT NULL,
  relations_version     varchar NOT NULL,
  parser_version        varchar NOT NULL,
  document_version      varchar,
  parent_release_id     uuid REFERENCES knowledge_release,
  created_at            timestamptz NOT NULL,
  created_by            text NOT NULL,
  reason                text NOT NULL,
  module_count          int NOT NULL,
  relationship_count    int NOT NULL,
  manifest              jsonb NOT NULL,
  drift                 jsonb NOT NULL,
  UNIQUE (source_document_hash, compiled_digest, relations_version, parser_version)
);

CREATE TABLE IF NOT EXISTS knowledge_modules (
  knowledge_id     uuid PRIMARY KEY,
  module_id        varchar(100) UNIQUE NOT NULL,
  name             text NOT NULL,
  category         varchar(100) NOT NULL,
  version          varchar(50) NOT NULL,
  status           varchar(50) NOT NULL CHECK (status IN ('DRAFT', 'REVIEW', 'ACTIVE',
                     'DISABLED', 'RETIRED')),
  effective_from   date,
  effective_to     date,
  source_document  text NOT NULL,
  source_reference text,
  source_hash      varchar(64) NOT NULL,
  release_id       uuid REFERENCES knowledge_release,
  metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  updated_by       text
);

CREATE TABLE IF NOT EXISTS knowledge_rules (
  rule_id          uuid PRIMARY KEY,
  knowledge_id     uuid NOT NULL REFERENCES knowledge_modules ON DELETE CASCADE,
  rule_type        varchar(50) NOT NULL CHECK (rule_type IN ('USE_WHEN', 'DO_NOT_USE_WHEN',
                     'CORE_PROPOSITION', 'AI_MUST_CHECK', 'LEGAL_BASIS', 'DRAFTING_GUIDANCE',
                     'DOCUMENT_FIELD')),
  rule_definition  jsonb NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS knowledge_rules_module ON knowledge_rules (knowledge_id);

CREATE TABLE IF NOT EXISTS knowledge_required_facts (
  id                uuid PRIMARY KEY,
  knowledge_id      uuid NOT NULL REFERENCES knowledge_modules ON DELETE CASCADE,
  fact_name         varchar(200) NOT NULL,
  requirement_type  varchar(50) NOT NULL CHECK (requirement_type IN ('GATE', 'REQUIRED', 'CHECK')),
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS knowledge_required_facts_fact ON knowledge_required_facts (fact_name);

CREATE TABLE IF NOT EXISTS knowledge_evidence_requirements (
  id             uuid PRIMARY KEY,
  knowledge_id   uuid NOT NULL REFERENCES knowledge_modules ON DELETE CASCADE,
  evidence_type  varchar(100) NOT NULL,
  requirement    jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_restrictions (
  id                uuid PRIMARY KEY,
  knowledge_id      uuid NOT NULL REFERENCES knowledge_modules ON DELETE CASCADE,
  restriction_type  varchar(100) NOT NULL CHECK (restriction_type IN ('PROHIBITED_CLAIM',
                      'DRAFTING_RULE')),
  content           text NOT NULL,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS graph_nodes (
  node_id    uuid PRIMARY KEY,
  node_type  varchar(50) NOT NULL CHECK (node_type IN ('FACT', 'KNOWLEDGE', 'EVIDENCE',
               'CLAIM', 'QUESTION', 'RULE')),
  entity_id  varchar(200) NOT NULL,
  metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (node_type, entity_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
  edge_id            uuid PRIMARY KEY,
  source_node        uuid NOT NULL REFERENCES graph_nodes,
  relationship_type  varchar(100) NOT NULL CHECK (relationship_type IN ('SUPPORTS', 'BLOCKS',
                       'REQUIRES', 'CONFLICTS_WITH', 'DEPENDS_ON', 'EVIDENCE_SUPPORTS')),
  target_node        uuid NOT NULL REFERENCES graph_nodes,
  confidence         numeric NOT NULL DEFAULT 1.0,
  origin             varchar(20) NOT NULL CHECK (origin IN ('YAML_COMPILED', 'CURATED', 'DOCX', 'ADMIN')),
  status             varchar(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVIEW', 'REMOVED')),
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by         text,
  updated_at         timestamptz
);
CREATE INDEX IF NOT EXISTS graph_edges_source ON graph_edges (source_node, relationship_type);
CREATE INDEX IF NOT EXISTS graph_edges_target ON graph_edges (target_node, relationship_type);

CREATE TABLE IF NOT EXISTS knowledge_release_items (
  release_id    uuid NOT NULL REFERENCES knowledge_release ON DELETE CASCADE,
  item_type     varchar(20) NOT NULL CHECK (item_type IN ('MODULE', 'EDGE')),
  item_id       varchar(200) NOT NULL,
  content_hash  varchar(64) NOT NULL,
  content       jsonb NOT NULL,
  PRIMARY KEY (release_id, item_type, item_id)
);

-- The governance log (0004) now also records module status changes, imports
-- and graph edge changes.
ALTER TABLE knowledge_changes DROP CONSTRAINT IF EXISTS knowledge_changes_entity_type_check;
ALTER TABLE knowledge_changes ADD CONSTRAINT knowledge_changes_entity_type_check
  CHECK (entity_type IN ('NODE', 'EDGE', 'MODULE', 'RELEASE'));
ALTER TABLE knowledge_changes DROP CONSTRAINT IF EXISTS knowledge_changes_action_check;
ALTER TABLE knowledge_changes ADD CONSTRAINT knowledge_changes_action_check
  CHECK (action IN ('SEED', 'CREATE', 'UPDATE', 'DISABLE', 'REMOVE', 'ACTIVATE', 'RETIRE',
                    'IMPORT'));

-- ---------------------------------------------------------------- P5 claim plan authority
-- 0006_claim_plan_authority.sql: one LOCKED claim plan decides what a letter argues.
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
