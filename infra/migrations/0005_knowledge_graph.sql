-- P4b: knowledge base ingestion + knowledge graph metadata layer
-- (pcn_appeal/knowledge_ingestion/).
--
-- The controlled document (Private_Parking_AI_Legal_Knowledge_Base_COMPLETE_V2.docx)
-- is imported into structured rows; each import whose inputs differ from the
-- last one is a new knowledge_release.
--
--   knowledge_modules               one row per module (current state)
--   knowledge_rules                 USE_WHEN (prose + compiled predicate),
--                                   DO_NOT_USE_WHEN, CORE_PROPOSITION,
--                                   AI_MUST_CHECK, LEGAL_BASIS, DRAFTING_GUIDANCE
--   knowledge_required_facts        module -> Fact Graph fact names
--   knowledge_evidence_requirements module -> evidence types / kinds
--   knowledge_restrictions          prohibited claims, drafting rules
--   graph_nodes / graph_edges       the knowledge graph
--   knowledge_release               one row per distinct import
--   knowledge_release_items         what each release contained (for compare)
--
-- NOT live: reasoning still reads kb_modules.yaml (or a published kb_release).
-- A case's manifest records kb.digest; knowledge_release.compiled_digest is the
-- same digest, so a case can be joined to the knowledge release it ran on.
--
-- Supersedes 0004's knowledge_nodes / knowledge_edges, which were never
-- deployed and are rebuilt by ingestion: they are dropped here. 0004's
-- knowledge_changes (the governance log) is kept and widened.
--
-- Apply after 0004_knowledge_graph.sql. Idempotent.
-- Rollback:
--   DROP TABLE IF EXISTS knowledge_release_items, graph_edges, graph_nodes,
--     knowledge_restrictions, knowledge_evidence_requirements,
--     knowledge_required_facts, knowledge_rules, knowledge_modules, knowledge_release;
--   (and re-apply 0004 to restore its two tables)

DROP TABLE IF EXISTS knowledge_edges;
DROP TABLE IF EXISTS knowledge_nodes;

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
