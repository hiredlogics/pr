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
  appeal_deadline date,                              -- from notice; drives reminders
  created_at     timestamptz DEFAULT now(),
  retention_until date NOT NULL                      -- UK GDPR retention policy
);

CREATE TABLE evidence (
  evidence_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id      uuid REFERENCES cases ON DELETE CASCADE,
  kind         text NOT NULL,
  s3_key       text NOT NULL,                        -- encrypted object store (SSE-KMS)
  sha256       text NOT NULL,                        -- tamper evidence
  ocr_text     text,
  special_category boolean DEFAULT false,            -- medical/disability docs: restricted access
  uploaded_at  timestamptz DEFAULT now()
);

CREATE TABLE facts (
  fact_id     text,
  case_id     uuid REFERENCES cases ON DELETE CASCADE,
  name        text NOT NULL,
  value       jsonb,
  status      text NOT NULL,                         -- EXTRACTED/CONFIRMED/.../UNCERTAIN
  source_kind text NOT NULL,
  source_ref  text NOT NULL,
  excerpt     text,
  confidence  real,
  superseded  boolean DEFAULT false,                 -- facts are append-only; never updated in place
  created_at  timestamptz DEFAULT now(),
  PRIMARY KEY (case_id, fact_id, created_at)
);

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
