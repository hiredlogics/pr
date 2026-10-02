# P4 — Knowledge Graph Relationship Engine

Branch `feature/p4-knowledge-relations` (from P3 `3ed9fd1`). Not pushed.

## WHAT

Verified facts and evidence now reach KB modules only through explicit, typed
relationships. For every in-force module, the system records:

- whether it applies;
- the fact (and that fact's source) that supports it or blocks it;
- for a module that does not apply, the reason.

Case Intelligence is offered only the modules those relationships leave open,
with the relationship attached to each one.

## WHY

Before P4, the analysis payload was the top 24 modules by text similarity, and
nothing stated why a module was relevant. Two consequences:

- A photo/attendant notice was argued with ANPR grounds (ANPR-01 was selected
  on an observation notice).
- A No Parking allegation was argued as "I paid" (PAY-01 was selected).

The only safeguard was the model's judgement.

## ROOT CAUSE

Module relevance was inferred from text similarity plus each module's own gate.
Neither captures:

- **Evidence type.** An ANPR argument needs ANPR records.
- **Allegation type.** No payment answers a prohibition.

An answered `children_present` also never counted as a contradiction of a
parent-and-child allegation. Only free text or a confirmed hypothesis did
(`engines/account.py`).

## Deliverables

### 1. DB migrations

`infra/migrations/0004_knowledge_graph.sql`, also appended to
`infra/postgres_schema.sql`. Additive, idempotent, with rollback in the header.

| Table | Columns |
|---|---|
| `knowledge_nodes` | knowledge_id UUID PK, module_id UNIQUE, name, category, version, status (CHECK), effective_from, effective_to, metadata JSONB, plus updated_by and updated_at |
| `knowledge_edges` | edge_id UUID PK, source_type, source_id, relationship_type (CHECK: SUPPORTS, BLOCKS, REQUIRES, CONFLICTS_WITH, DEPENDS_ON, EVIDENCE_SUPPORTS), target_type, target_id, weight, metadata JSONB, plus origin (DERIVED / CURATED), status (ACTIVE / REVIEW / REMOVED), version, updated_by, updated_at |
| `knowledge_changes` | Append-only: entity, action, version, changed_by, changed_at, reason, before, after |

### 2. Knowledge node model

`kg/relations.py` defines `KnowledgeNode`. It holds the spec fields plus a
metadata object containing:

- use_when
- required_facts
- supporting_evidence
- blocked_conditions (= do_not_use_when)
- prohibited_claims
- drafting_guidance
- core_proposition, legal_basis, route, topic, strength, source_reference
- building_block_ids

**Drafting paragraphs are not copied.** Building blocks are referenced by id
only, and a test asserts that no block text appears in any node.

Category is taken from the route tier: LEGAL_RULE, FACTUAL_GROUND,
EVIDENCE_CHALLENGE or SECONDARY_POINT.

### 3. Relationship model

`KnowledgeEdge` comes from `edge()`, which refuses an unknown relationship or
node type. Node types are MODULE, FACT, EVIDENCE and SIGNAL. Ids are UUIDv5 of
the content, so they are stable from build to build.

**DERIVED edges** are computed from each module's own predicates:

| Predicate leaf | Edge |
|---|---|
| use_when, positive | SUPPORTS (EVIDENCE_SUPPORTS for `has_evidence`) |
| use_when, under `not` | BLOCKS |
| do_not_use_when, positive | BLOCKS |
| do_not_use_when, under `not` | SUPPORTS |
| required_facts | REQUIRES |
| `conflicts_with` | CONFLICTS_WITH |

They are read from the same predicates the reasoning gate enforces, so they
cannot drift from them; a test checks this leaf by leaf.

**CURATED edges** live in `data/kb_relations.yaml` (version 1.0). They cover
what a gate cannot express:

- **Signals** are computed with the KB's own DSL:
  - `evidence_method`: ANPR, ATTENDANT_PHOTO or UNKNOWN;
  - `allegation_class`: PROHIBITION, RESTRICTED_BAY, OVERSTAY, PERMIT, PAYMENT or UNCLASSIFIED.
- **Signal → module edges:**
  - `evidence_method=ATTENDANT_PHOTO` BLOCKS ANPR-01/02/03 and TIME-01;
  - `allegation_class=PROHIBITION` BLOCKS PAY-01/02/03, KEY-01/02 and GRACE-01/02;
  - relevance SUPPORTS edges (weight 0.5).
- **FACT DEPENDS_ON FACT** for derived facts. Example:
  `account_contradicts_allegation` depends on `child_occupant_present`.

All curated content is generic: allegation and evidence shapes only, with no
operator, site or PCN.

Current graph: 55 nodes and about 338 edges.

### 4. KB migration plan

1. Apply `0004`.
2. Run `python -m pcn_appeal.store knowledge-seed`. This seeds one node per
   module from `kb_modules.yaml`, every derived edge, and the curated edges
   from `kb_relations.yaml`. It is idempotent: a second run writes 0 rows
   (tested).
3. `kb_modules.yaml` and `building_blocks.yaml` stay the authored source.
   - The relationship graph controls which modules reasoning considers.
   - Building blocks stay drafting-only.
4. Admin edits are staged: status REVIEW, soft-removed edges, and a version
   bump on every change.
   - **They do not reach live reasoning.** Live reasoning keeps reading
     YAML or the published release, exactly as before.
   - A test proves this: disabling KB-PAY-01 in the store leaves the live
     matcher unchanged.
   - Making staged edits live needs the KB release gate. `/admin/kb/releases`
     still returns 501 deliberately, because it must run the scenario suite
     before publishing. **That gate is the next step, not part of P4.**

### 5. Matcher

`engines/knowledge_matcher.py`, `KnowledgeMatcher(kg).match(case)`. It is
deterministic, reads facts only, writes nothing, and assigns each in-force
module one status:

| Status | Meaning | Offered to model |
|---|---|---|
| SUPPORTED | Gate holds exactly as R-03 checks it, and nothing blocks the module | yes, first |
| RELEVANT | Gate could still hold, and a fact, evidence item or signal connects to it | yes, second |
| OPEN | Could still hold, but nothing points at it | yes, by existing order |
| REJECTED | Gate cannot hold on what is known | no |
| BLOCKED | A BLOCKS relationship fires | no; a proposal of it is suppressed before the claim plan |

Each candidate carries:

- `selected_because`: each condition with fact, value, source, source_kind and
  fact_id, plus `because_of` taken from DEPENDS_ON;
- `missing`;
- `blocked_by` (signal, basis, edge_id);
- `relevant_because`;
- `evidence`;
- `reason`.

Wired into `engines/analysis.py`:

- Candidates come from the matcher, with SUPPORTED modules ordered before the
  24-module cap, so the cap can never cut a supported module. This also fixes
  the no-retriever path, which previously cut at 24 in KB order.
- Each candidate in the payload carries `relation`, and the payload carries
  `case_signals`.
- The full match is written to the audit log as the `knowledge_match` event.
- The `case_analysis` prompt is now v8 and describes the new fields.

### 6. Admin changes

- Store: `store/knowledge.py`.
- API: every route is `_require_admin`, and every write body needs
  `changed_by` and `reason`. Without a user or a reason the change is refused
  with 422.

| Endpoint | Purpose |
|---|---|
| GET `/admin/knowledge/nodes`, `/edges`, `/changes` | Read nodes, edges, change log |
| POST `/admin/knowledge/modules` | Create a module (status REVIEW, predicates validated) |
| PATCH `/admin/knowledge/modules/{id}` | Update: version bump, REVIEW, derived edges recomputed when the gate changes |
| POST `/admin/knowledge/modules/{id}/disable` | Disable a module |
| POST `/admin/knowledge/edges` | Create an edge (CURATED only) |
| POST `/admin/knowledge/edges/{edge_id}/remove` | Soft-delete an edge (CURATED only) |

- A DERIVED edge cannot be removed directly; the error tells you to change the
  module's gate instead.
- Every change writes a `knowledge_changes` row with user, timestamp, reason,
  version, before and after.
- Without a database these routes return 503.
- `/cases/{id}/trace` and `/cases/{id}/facts` now include `knowledge`: signals,
  selected (facts, relationships, evidence), relevant, and rejected with
  reason.

Account engine fix: a true closed-form ANSWER to a fact a circumstance rule
knows (for example `children_present` → `child_occupant_present`) now counts
towards `account_contradicts_allegation`, the same way a confirmed hypothesis
does.

- The answer fact itself is unchanged (source ANSWER, owned by the customer).
- A "no" answer contributes nothing.
- On a non-bay allegation it does not contradict.

### 7. Tests

`tests/test_p4_knowledge_relations.py`, 43 tests.

- **Spec 1:** a parent-and-child allegation with the children_present answer
  selects KB-BAY-02, and the trace names `answer:children_present`. Also covered:
  - the narrative variant selects it;
  - "no" does not;
  - a non-bay allegation does not.
- **Spec 2:** a photo/attendant notice (observation_time, or a WINDSCREEN route)
  blocks ANPR-01/02/03 and TIME-01 with "wrong evidence type". An ANPR notice
  is not blocked.
- **Spec 3:** payment made selects KB-PAY-01, with `payment_made` sourced from
  the answer.
- **Spec 4:** for a No Parking allegation, every payment, keying and grace
  module is BLOCKED ("payment allegation absent"), even when a payment was
  made. With no payment fact, PAY-01 is not selected.
- **Spec 5:**
  - the same case matched repeatedly gives an identical trace;
  - fresh cases with the same facts give identical results apart from the
    per-case fact ids;
  - edge and node ids are stable across builds.
- **Graph shape:**
  - every module is a node with the reasoning fields;
  - no block text appears in any node;
  - only the 6 relationship types exist;
  - derived edges equal the gate leaves;
  - the spec examples exist as edges;
  - curated edges are generic.
- **Trace:**
  - every rejection has a reason;
  - selected facts carry their source;
  - every module is accounted for;
  - the matcher writes nothing.
- **Case Intelligence input:**
  - blocked modules are never in the payload;
  - the payload is never the full KB and holds only offerable modules;
  - SUPPORTED modules come first;
  - a model that insists on a blocked module has it suppressed ("blocked by
    relation");
  - the match appears in the audit log.
- **Store and API:**
  - seed is idempotent;
  - a user and a reason are required;
  - update bumps the version, stages the module and logs before and after;
  - a gate change recomputes derived edges;
  - bad predicates are refused;
  - create, disable, and curated create/remove edge all work;
  - derived edges cannot be removed;
  - staged edits do not reach live reasoning;
  - routes are admin-only (401 without a token);
  - a missing reason gives 422;
  - without a database the routes return 503;
  - the case trace includes the knowledge match.

The SQLite shim (`tests/sqlite_store.py`) now has the three knowledge tables,
and `install()` patches the knowledge store's connection.

### 8. Before/after reasoning flow

```
P3: facts -> top-24 by text similarity -> model proposes -> claim plan vetoes
P4: facts + evidence -> signals -> relationships -> SUPPORTED / RELEVANT / OPEN offered
    (with the relation) ; REJECTED / BLOCKED never offered ; blocked proposal suppressed
    -> model proposes -> claim plan vetoes (unchanged)
```

Same six scenarios, P3 (`3ed9fd1`) vs P4, reference analysis model, through
`AppealPipeline.analysis_of`:

| Scenario | P3 selected | P4 selected |
|---|---|---|
| Parent & child + child answer | POFA-01 | **BAY-02**, POFA-01 |
| Photo notice + multiple visits | **ANPR-01** (wrong evidence type), POFA-01 | POFA-01 (ANPR blocked, not offered) |
| Payment made | PAY-01, POFA-01 | PAY-01, POFA-01 |
| No Parking + payment made | **PAY-01** (cannot answer a prohibition), POFA-01 | POFA-01 (payment blocked, not offered) |
| Overstay, nothing said | POFA-01 | POFA-01 |
| ANPR overstay + multiple visits | ANPR-01, POFA-01 | ANPR-01, POFA-01 |

The payload stays at the 24-module cap: OPEN modules fill the remaining slots.
What changes is which modules fill it and the order they come in.

## BLAST RADIUS

- Case analysis input: which modules are offered, in what order, plus two new
  fields. The prompt is now v8.
- `account_contradicts_allegation` can now be derived from a closed-form
  answer, not only from free text.
- New tables and admin routes, all additive.

## NOT CHANGING

Unchanged from P1–P3:

- fact ownership;
- question generation rules (the P3 authority);
- drafting wording and building blocks;
- Claim Plan finalisation (its vetoes are untouched; P4 only removes blocked
  proposals before it);
- service routing;
- the KB release gate (still returns 501);
- driver identification (the tri-state is unchanged, and the narrative still
  never sets it);
- PROCESSING_ERROR vs NO_SUPPORTED_GROUNDS.

## TEST PLAN / EVIDENCE

- Full suite: 672 run, the same 29 baseline failures (23 F + 6 E), none new,
  none fixed.
- P4 file: 43/43 pass.
- Conflict probe: unchanged.
- Before/after: the table above.
- **No live (OpenAI) run was done for P4.**

## DEPLOYMENT

1. Apply `0004` (additive).
2. Optionally run `knowledge-seed`.
3. Deploy the code.

Live reasoning does not depend on the new tables, because the matcher builds
its graph in memory from the loaded KB. Rollback is the code revert plus the
DROP statements in the migration header.

## Risks / follow-ups

1. **Store vs live drift.** Staged admin edits are not live until a release
   publisher exists, and that publisher must rebuild the relation graph from
   the published nodes and edges.
2. **P3 authority vs relation engine.** The P3 Question Authority still
   evaluates modules that the relation engine blocks. P3's own
   PROHIBITION → payment rule covers the main case, but its allegation table
   duplicates the P4 `allegation_class` signal and should be unified into one
   table.
3. **Signal keyword lists.** The `allegation_class` and `evidence_method`
   signals use generic keyword and fact-shape lists.
   - An unclassified allegation blocks nothing, which fails open to the P3
     behaviour.
   - A misclassification could block a valid ground. Every block is visible in
     the trace with its basis.
4. **No live run.** Recommended before merge: one parent-and-child case and
   one windscreen case.
