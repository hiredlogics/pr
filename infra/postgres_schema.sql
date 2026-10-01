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
