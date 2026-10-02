-- P4: knowledge graph relationship engine (pcn_appeal/kg/relations.py,
-- pcn_appeal/engines/knowledge_matcher.py, pcn_appeal/store/knowledge.py).
--
-- knowledge_nodes   one row per KB module as reasoning sees it: gate, required
--                   facts, supporting evidence, blocked conditions, prohibited
--                   claims, drafting guidance. NOT the drafting paragraphs:
--                   building blocks stay in kb_blocks and are referenced by id.
-- knowledge_edges   typed relationships between facts, evidence, case signals
--                   and modules. DERIVED edges are recomputed from the module
--                   gates on every seed and cannot be edited; CURATED edges are
--                   the admin-managed ones.
-- knowledge_changes append-only log of every admin change: who, when, why,
--                   which version, before and after.
--
-- Admin changes are STAGED (status REVIEW / soft-removed edges). Nothing here
-- is read by live reasoning until it is published through the existing KB
-- release gate (kb_releases), which is unchanged by this migration.
--
-- Additive and idempotent. Apply after 0003_fact_hypotheses.sql.
-- Rollback:
--   DROP TABLE IF EXISTS knowledge_changes;
--   DROP TABLE IF EXISTS knowledge_edges;
--   DROP TABLE IF EXISTS knowledge_nodes;

CREATE TABLE IF NOT EXISTS knowledge_nodes (
  knowledge_id    uuid PRIMARY KEY,
  module_id       varchar NOT NULL UNIQUE,
  name            text NOT NULL,
  category        varchar NOT NULL,
  version         varchar NOT NULL,
  status          varchar NOT NULL CHECK (status IN ('DRAFT', 'REVIEW', 'ACTIVE', 'DISABLED')),
  effective_from  date,
  effective_to    date,
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by      text,
  updated_at      timestamptz
);

CREATE TABLE IF NOT EXISTS knowledge_edges (
  edge_id            uuid PRIMARY KEY,
  source_type        varchar NOT NULL CHECK (source_type IN ('MODULE', 'FACT', 'EVIDENCE', 'SIGNAL')),
  source_id          varchar NOT NULL,
  relationship_type  varchar NOT NULL CHECK (relationship_type IN ('SUPPORTS', 'BLOCKS',
                       'REQUIRES', 'CONFLICTS_WITH', 'DEPENDS_ON', 'EVIDENCE_SUPPORTS')),
  target_type        varchar NOT NULL CHECK (target_type IN ('MODULE', 'FACT', 'EVIDENCE', 'SIGNAL')),
  target_id          varchar NOT NULL,
  weight             numeric NOT NULL DEFAULT 1.0,
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
  origin             varchar NOT NULL DEFAULT 'DERIVED' CHECK (origin IN ('DERIVED', 'CURATED')),
  status             varchar NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVIEW', 'REMOVED')),
  version            int NOT NULL DEFAULT 1,
  updated_by         text,
  updated_at         timestamptz
);
CREATE INDEX IF NOT EXISTS knowledge_edges_target ON knowledge_edges (target_type, target_id);
CREATE INDEX IF NOT EXISTS knowledge_edges_source ON knowledge_edges (source_type, source_id);

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
