# P4b — Knowledge Base Ingestion + Knowledge Graph Metadata Layer

Branch `feature/p4b-kb-ingestion`, built on P4 (`09915ff`). Not pushed.

Two decisions shaped this phase:

- **Build on P4.** The P4 relationship engine and its Case Intelligence
  integration stay. P4b adds the controlled-document ingestion layer
  underneath them.
- **Report drift; the YAML stays live.** The document is stored as validated,
  versioned releases. Where it differs from `kb_modules.yaml`, the difference
  is reported. Live reasoning does not change.

## WHAT

`Private_Parking_AI_Legal_Knowledge_Base_COMPLETE_V2.docx` is now imported into
structured Postgres knowledge objects and a knowledge graph by a repeatable
pipeline:

```
DOCX -> parser -> extract (+ compiled YAML gates, joined by module id) -> graph -> drift
     -> Postgres (one release per distinct import) -> graph queries / governance
```

## WHY

Before this phase, knowledge lived in three places: the DOCX, the YAML and code.
That made four things hard:

- versioning;
- seeing relationships;
- reading required facts and evidence by machine;
- tracing prohibited claims back to the controlled source.

## ROOT CAUSE

The YAML was derived from the document by hand. Nothing tied the YAML back to
the document, so nothing could say which version a case ran on, or whether the
two had drifted apart.

## Deliverables

### 1. Database migration

`infra/migrations/0005_knowledge_graph.sql`, mirrored in `infra/postgres_schema.sql`.

| Table | Purpose |
|---|---|
| `knowledge_modules` | One row per module: id, name, category, version, status, effective dates, source document / reference / hash, release, metadata |
| `knowledge_rules` | USE_WHEN (the prose plus the compiled predicate), DO_NOT_USE_WHEN, CORE_PROPOSITION, AI_MUST_CHECK, LEGAL_BASIS, DRAFTING_GUIDANCE, DOCUMENT_FIELD (any table field the parser does not know) |
| `knowledge_required_facts` | Each fact with a requirement_type: GATE (read by the compiled gate), REQUIRED (compiled `required_facts`) or CHECK (inferred from AI MUST CHECK, confidence 0.6) |
| `knowledge_evidence_requirements` | DOCX evidence list, mapped to system evidence kinds, plus YAML `evidence_helpful` |
| `knowledge_restrictions` | PROHIBITED_CLAIM / DRAFTING_RULE, from DOCX "DO NOT" and YAML `prohibited_claims` |
| `graph_nodes` | Node types FACT, KNOWLEDGE, EVIDENCE, RULE (CLAIM and QUESTION are reserved for P5) |
| `graph_edges` | The 6 allowed relationships, with confidence, origin (YAML_COMPILED / CURATED / DOCX / ADMIN) and status |
| `knowledge_release` | One row per distinct import: document hash, compiled digest, relations version, parser version, parent, created_by/at, reason, counts, manifest, drift |
| `knowledge_release_items` | What each release contained, so two releases can be compared |

**Supersedes P4's `knowledge_nodes` / `knowledge_edges`.** Those two tables are
dropped by 0005. They were never deployed, and ingestion rebuilds their
contents. P4's `knowledge_changes` governance log is kept, and its CHECK
constraints are widened.

**Rollback:** the DROP statements in the migration header.

### 2. Knowledge ingestion service

Location: `pcn_appeal/knowledge_ingestion/`. The spec said
`services/knowledge_ingestion/`, but `pcn_appeal/services/` is the
one-package-per-route service registry, and this phase must not touch service
routing.

| File | What it does |
|---|---|
| `parser.py` | Reads document *structure*: Heading 1 = section; Heading 2 `KB-…` + 2-column table = module (every field kept, known or not); Heading 2 `PP-…` = drafting block (id and name only, the paragraph is never stored); standalone tables = document-wide rule sets; bullets = checklists. No module id appears in the code. A synthetic module "KB-ZZZ-01" in a new section is read like any other (tested). |
| `extract.py` | Joins each DOCX module to its compiled YAML form by id. Maps prose to system vocabulary generically: an item names a fact when every word of the fact's name or alias appears in it, and a one-word name only when it is the whole item (so "operator photos" is not mapped to `operator_name`). Evidence kinds are matched by their leading word. |
| `graph.py` | Builds nodes and edges from the records plus the P4 relation graph. Ids are UUIDv5 of the content. |
| `drift.py` | Reports where DOCX and YAML differ. |
| `store.py` | Import, governance and releases. |
| `queries.py` | Fact → knowledge retrieval and example SQL. |
| `__main__.py` | CLI. |

**Idempotent:** an import whose inputs (document hash, compiled digest,
relations version, parser version) match an existing release writes nothing.
Any change produces a new release that has the previous release as its parent.

### 3. Extracted module count (real document)

- **51** modules in the document, all extracted.
- **55** modules stored. The 4 extra are live YAML modules the document does
  not contain: KB-BAY-01, KB-BAY-02, KB-POFA-06 and KB-REC-01. They are
  flagged HIGH in the drift report.
- Rows created:
  - 325 rules
  - 248 required facts (GATE + REQUIRED + 26 inferred CHECK)
  - 140 evidence requirements
  - 91 restrictions
- 70 drafting block ids are recorded, with no block text.

**Drift found:**

| Severity | Count | What it is |
|---|---|---|
| HIGH | 4 | The live modules missing from the controlled document |
| LOW | 1 | The document lists evidence the YAML does not |
| INFO | 15 | AI MUST CHECK names a fact the compiled gate never reads |

There is no name or core-proposition drift: the YAML matches the document
wording exactly.

### 4. Relationship count

- **506 edges:**
  - by type: SUPPORTS 144, EVIDENCE_SUPPORTS 149, REQUIRES 138, BLOCKS 36, DEPENDS_ON 21, CONFLICTS_WITH 18;
  - by origin: YAML_COMPILED 381, CURATED 62, DOCX 63.
- **250 nodes:** FACT 86, RULE 68, KNOWLEDGE 55, EVIDENCE 41.

The spec examples exist as edges:

- `FACT:payment_made SUPPORTS KB-PAY-01`
- `EVIDENCE:ATTENDANT_PHOTO BLOCKS KB-ANPR-01`
- `FACT:account_contradicts_allegation DEPENDS_ON child_occupant_present` and `SUPPORTS KB-BAY-02`

### 5. Example graph queries

The full SQL is in `queries.EXAMPLES`:

- knowledge supported by a fact;
- what blocks a module;
- the facts a module needs, by source;
- a recursive query: derived fact → its inputs → the knowledge it supports;
- evidence that supports a module;
- the release a case ran on, by `manifest.kb.digest`.

`knowledge_for_facts({"children_present": True})` returns KB-BAY-02 via
`child_occupant_present → account_contradicts_allegation`.

### 6. Admin design

All routes are `_require_admin`. Each change needs `changed_by` and `reason`
(422 without them) and is logged to `knowledge_changes` with the timestamp and
version. Without a database the routes return 503.

| Endpoint | Purpose |
|---|---|
| GET `/admin/knowledge/modules?q&category&status` | View and search modules (id, name, metadata, rule text) |
| GET `/admin/knowledge/modules/{id}` | One module: rules, facts, evidence, restrictions, labelled relationships |
| POST `/admin/knowledge/modules/{id}/activate`, `/disable` | Change status (a disable survives re-imports of changed content) |
| GET/POST `/admin/knowledge/edges`, POST `/admin/knowledge/edges/{id}/remove` | ADMIN relationships only (staged REVIEW; an ingested relationship can't be removed by hand: change its source and re-import) |
| POST `/admin/knowledge/import` | Import (only `.docx` files in the data directory) |
| GET `/admin/knowledge/releases`, `/releases/compare?a&b` | List releases; compare two versions (modules added/removed/changed field by field, relationships added/removed) |
| GET `/admin/knowledge/relationship-changes` | Review relationship changes: vs the parent release, plus ADMIN edge changes |
| GET `/admin/knowledge/graph/facts?facts=` | Fact → knowledge query |
| GET `/admin/knowledge/changes` | The change log |

**Content changes go through the controlled document plus a re-import.** P4's
create-module and update-module routes are retired for that reason, and the
P4 `store/knowledge.py` is removed.

**A case can say which KB version it ran on.**

- The case manifest already records `kb.digest`, and now also
  `kb.relations_version`.
- The `knowledge_match` audit event records `kb_digest`.
- `release_for_digest()` returns the knowledge release built from that exact KB.

CLI:

- `python -m pcn_appeal.knowledge_ingestion parse` (dry run, no database)
- `python -m pcn_appeal.knowledge_ingestion ingest --by USER --reason TEXT`
- `python -m pcn_appeal.knowledge_ingestion releases`
- `python -m pcn_appeal.knowledge_ingestion compare RELEASE_A RELEASE_B`

### 7. Test results

`tests/test_knowledge_ingestion.py`: 34 tests, 0 skipped (the real document is
present locally).

- **Spec 1:** imports 55 modules.
- **Spec 2:** every module has an id, version and category. KB-POFA-04 is "Mandatory notice content", category POFA, version V1.
- **Spec 3:** required facts are stored (PAY-01: payment_made GATE, payment_method CHECK).
- **Spec 4:** evidence is stored, including bank_transaction → BANK_STATEMENT.
- **Spec 5:** restrictions are stored (the "automatically invalid" prohibited claim).
- **Spec 6:** relationships are created (ATTENDANT_PHOTO BLOCKS ANPR-01).
- **Spec 7:** a duplicate import writes 0 rows, checked table by table.
- **Spec 8:** a changed document is a new release with the right parent; compare shows the changed module, the retired module and the removed relationships. A changed compiled KB is also a new release.
- **Spec 9:** children_present → KB-BAY-02 (with the path), and payment_made → PAY-01.
- **Governance:**
  - search works;
  - activate and disable need a user and reason and are logged;
  - a disable survives a re-import;
  - ADMIN edges can be created and removed, and survive a re-import;
  - an ingested edge can't be removed by hand;
  - unknown relationships and modules are refused.
- **Isolation:** the store does not change live reasoning; the matcher trace is identical.
- **No drafting text** is stored in any table.
- **API:**
  - admin-only (401 without a token);
  - 422 on a missing reason;
  - the import refuses paths outside the data directory;
  - 503 with no database.

The parsing tests use a synthetic DOCX built in the test, so they run without
the real document. The real-document classes skip when it is absent.

P4 test file: its store and API classes moved here. It gains a test that the
audit records the KB digest.

**Full suite:** 692 run, the same 29 baseline failures (23 F + 6 E), no new
failures, none fixed.

**Reasoning before/after:** the six P4 scenarios give identical output on
`09915ff` and on this branch.

### 8. Rollback plan

1. Revert the branch.
2. Run the DROP statements in the 0005 header:
   `DROP TABLE IF EXISTS knowledge_release_items, graph_edges, graph_nodes,
   knowledge_restrictions, knowledge_evidence_requirements, knowledge_required_facts,
   knowledge_rules, knowledge_modules, knowledge_release;`
3. To restore P4's two tables, re-apply 0004.

Live reasoning does not read any of these tables, so rollback cannot change a
case outcome.

## BLAST RADIUS

- New package, tables and admin routes.
- `manifest.kb` gains `relations_version`.
- The `knowledge_match` audit event gains `kb_digest`.
- P4's knowledge store module and its create/update routes are removed.
- `python-docx` is added to `requirements.txt` and `requirements-api.txt`.

## NOT CHANGING

- Drafting engine and building blocks
- Claim Plan behaviour
- Question approval logic
- Legal reasoning outcomes (before/after identical)
- KB wording (YAML and DOCX untouched)
- Service routing
- The KB release gate (`/admin/kb/releases` still returns 501)

## DEPLOYMENT

1. Apply 0004 if not already applied, then 0005.
2. Install `python-docx`.
3. Run `python -m pcn_appeal.knowledge_ingestion ingest --by <user> --reason "initial import"`.

The import is not needed for anything live to work.

## Risks / follow-ups

1. **The controlled document is not in git.** `.gitignore` has `*.docx`, so a
   deployed import has no document unless it is shipped separately. Commit it
   (it is the controlled source), or store it in object storage with its
   hash. Copies also exist in the repo root and in `pcn_appeal/data/`; they
   are identical.
2. **Not run against real Postgres.** The migration and the store were only
   exercised through the SQLite shim. The example queries use Postgres syntax
   and have not been executed. Run 0005 and one import against a dev
   database before merge.
3. **`uv.lock` is not regenerated** for `python-docx`.
4. **The 4 live modules missing from the document** (BAY-01, BAY-02,
   POFA-06, REC-01) need a legal decision: add them to the document, or
   retire them.
5. **CHECK mappings are inferred** (confidence 0.6, source DOCX_CHECK). 26 of
   the AI MUST CHECK items map to facts and the rest are kept as prose. A
   reviewer should confirm them before P5 relies on them.
6. **P5 work:**
   - make the DB release the source the relationship engine reads, behind
     the release gate;
   - create CLAIM and QUESTION nodes;
   - unify P3's allegation table with the `allegation_class` signal.
