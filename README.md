# PCN Appeal AI — reference implementation (V2)

Python reference build of the architecture in the review document: four engines, a legal
knowledge graph, restricted hybrid RAG, sentence-level provenance and a hard validation gate.

```
UPLOAD → E1 Extraction → customer confirm → E2 Questions → E3 Reasoning (rules + KG + RAG)
      → Drafter (LLM, structured) → E4 Validation ──pass──→ PDF + CRM
                                       └─fail→ regenerate ×2 → template fallback → manual review
```

## Run the tests (no external services needed)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml networkx numpy
python -m unittest discover -s tests -v
```

38 tests cover Dev Pack scenarios A–D plus: Scotland/byelaw PoFA exclusion, PoFA boundary cases,
bank-holiday working days, driver-safe questions, validator blocking, LLM-failure fallback,
prompt-injection flagging, low-confidence dates, the LLM route-classifier's confidence threshold +
hallucinated-route guard, the provider clients, the one-click appeal's pause/complete behaviour, and
the YAML↔Postgres-release equivalence. A further 6 need a live database and skip without one.

## Run it live

```bash
pip install fastapi 'uvicorn[standard]' pydantic
./scripts/live_demo.sh                       # whole journey against a throwaway server
```

Or serve it and drive the endpoints yourself (`/docs` for the OpenAPI UI):

```bash
uvicorn pcn_appeal.api:app --reload --port 8077
```

`api.py` has no auth and runs extraction inline; it is a dev surface, not a deployment. Cases live in
a process dict unless `DATABASE_URL` is set (see below).

### One click: `POST /appeal`

Documents plus the customer's account in, finished letter out:

```bash
curl -s localhost:8077/appeal -H 'content-type: application/json' -d '{
  "documents": [{"evidence_id": "E1", "filename": "pcn.pdf", "text": "Parking Charge Notice\nOperator: Acme Parking Ltd\nPCN Number: PCN123456\n..."}],
  "narrative": "the car broke down, flat battery, RAC attended"}'
```

The call runs extract → auto-confirm → reason → draft → validate unattended. It pauses **only** when
a missing fact gates a ground strong enough to lead or support the letter (`strength >= 50`); answer
those with `POST /appeal/{case_id}` and it finishes itself. A question that would unlock only a weak
point is reported in `skipped_questions` and never interrupts the customer.

Two things it deliberately does not do. Facts the extractor read below the confidence threshold are
**not** auto-confirmed — they stay `UNCERTAIN`, are listed in `flags`, and rule EX-02 keeps them from
grounding a defect, because auto-confirming a value the model was unsure of is how a fabricated
defect reaches a letter. And it does not infer gating facts from the narrative: an inferred fact is
not a customer-confirmed fact, which is what VAL-FACT and KB-GOV-04 exist to stop.

### Real files: `POST /appeal/files`

`POST /appeal` takes text a caller has already extracted. Customers have a photo of a paper notice
or a PDF from an email, so `POST /appeal/files` takes the files themselves (multipart: repeated
`files`, plus `narrative`):

```bash
curl -s localhost:8077/appeal/files -F files=@pcn.pdf -F files=@receipt.jpg \
  -F narrative='the machine would not take my card'
```

`ingest.py` decides per file how to read it, by measuring the text actually recovered rather than
trusting the extension — a PDF says nothing about whether it is born-digital or a photo of a letter:

| Upload | Read as |
|---|---|
| PDF with a text layer | text |
| PDF without one (scanned) | pages rasterised to JPEG → vision model |
| `image/*` | normalised to JPEG → vision model |
| `text/plain`, `.md` | decoded |

Images reach Engine 1 with an `<attached_images>` manifest naming each one's document and page,
because the provider clients append images positionally with no labels — without it the model cannot
cite which page a fact came from. A file that cannot be read is returned in `rejected` and the case
proceeds on the rest; one unreadable receipt should not lose the appeal. Only if *nothing* is
readable does the call fail (422). `read_as` reports the chars and image pages taken from each file,
so a photo that yielded nothing is visible rather than silently empty.

Nothing here interprets content: uploaded text is still untrusted data under EX-06, and every field
is still extracted with its own confidence.

## Postgres + pgvector

The KB is **authored** in `data/*.yaml` (git-versioned, legal-reviewable, no deploy needed to edit)
and **served** from Postgres. Nothing moves into the database except storage: `use_when` /
`do_not_use_when` go in as `jsonb` and come back out byte-identical, and `rules/dsl.py` still
evaluates them, so a rule decides the same thing either way. `tests/test_store_and_autoappeal.py`
asserts that the YAML-built and release-built graphs are identical.

```bash
docker compose -f infra/docker-compose.yml up -d db     # pgvector/pgvector:pg16, schema auto-applied
export DATABASE_URL=postgresql://postgres:dev@localhost:5432/postgres

python -m pcn_appeal.store init      # apply infra/postgres_schema.sql
python -m pcn_appeal.store sync      # data/*.yaml -> kb_modules/kb_blocks/kb_embeddings + a release
python -m pcn_appeal.store status    # counts, latest release, modules per route
```

`sync` upserts every module, block and code version, rebuilds the pgvector + tsvector retrieval
index, and publishes an immutable `kb_releases` snapshot pinning an exact version per item. A case
records the release that produced it (`cases.kb_release_id`), so any appeal can be replayed against
the rules as they stood (KB-GOV-01). With `DATABASE_URL` set, the API serves the latest release and
persists cases, facts, drafts and audit; with it unset everything runs from YAML in memory, which is
how the suite stays offline.

Embeddings come from `rag/embedder.py:HashingEmbedder` — deterministic, offline, no API key, so the
index works with nothing running but Postgres. It matches wording, not meaning: swap in a real
embedding model behind the same interface and **re-run `sync`**, since vectors from different models
are not comparable. Each release records which embedder built it.

`facts` is append-only: a correction inserts a new row and marks the previous one `superseded`, so the
confirmation screen's history survives and a letter can be audited against what was known when it was
written. `raw_answers` holds the customer's own wording and is never read back onto the drafting path.

To verify against a live database (6 tests, otherwise skipped):

```bash
python -m unittest tests.test_pg_integration -v
```

## Choosing a model provider

OpenAI is the only provider. `LLM_PROVIDER` selects `openai` or `demo`; with it unset, a usable
`OPENAI_API_KEY` wins and anything else falls back to `DemoLLM` — a label matcher that reads only explicit
`Field: value` lines and reports nothing for anything it cannot find, so the API stays runnable
without credentials.

```bash
export OPENAI_API_KEY=sk-...
python scripts/check_llm.py --call     # verify the key and show the model picked per task
```

`OpenAIClient` resolves each task to the best model the key can actually list (`OPENAI_PREFERENCES`,
best first), so a project without access to the newest model lands on the next one instead of
404-ing mid-case. It refuses to start if `validation` would resolve to the same model as `drafting`,
because the release gate must not share the drafter's blind spots. Override any task with
`OPENAI_MODEL_EXTRACTION`, `OPENAI_MODEL_QUESTIONING`, `OPENAI_MODEL_DRAFTING`,
`OPENAI_MODEL_VALIDATION`.

Keep keys in a `.env` at the repo root (gitignored), not inside `.venv/` — anything in the virtual
environment is lost the moment it is rebuilt.

## Layout

| Path | What it is |
|---|---|
| `pcn_appeal/engines/extraction.py` | **Engine 1** Document intelligence + rule pack EX-01..08 |
| `pcn_appeal/engines/questioning.py` | **Engine 2** Adaptive questions + keeper-safe normaliser, Q-01..07 |
| `pcn_appeal/engines/reasoning.py` | **Engine 3** Applicability, KG gating, conflicts, restricted RAG, R-01..10 |
| `pcn_appeal/engines/validation.py` | **Engine 4** Release gate, VAL-* rules + optional LLM judge |
| `pcn_appeal/drafting/drafter.py` | LLM drafter (structured, cited sentences) + deterministic template drafter |
| `pcn_appeal/kg/graph.py` | Knowledge graph (networkx; Neo4j schema in `infra/`) |
| `pcn_appeal/rag/retriever.py` | Hybrid lexical+dense retriever, lease clause finder |
| `pcn_appeal/legal/pofa.py` | Deterministic PoFA Sch 4 calculator |
| `pcn_appeal/legal/code_versions.py` | Versioned Code resolver |
| `pcn_appeal/rules/dsl.py` | Safe predicate language for `use_when` / `do_not_use_when` |
| `pcn_appeal/store/` | Postgres + pgvector: schema bootstrap, KB sync/release, case persistence |
| `pcn_appeal/rag/embedder.py` | Deterministic offline embedder for the pgvector index |
| `pcn_appeal/data/*.yaml` | KB modules, blocks, routes, questions, Code versions (admin-editable data) |
| `pcn_appeal/orchestrator.py` | State machine + regenerate/fallback/review loop |
| `pcn_appeal/api.py` | FastAPI surface (production) |
| `infra/` | Postgres + pgvector schema, Neo4j schema, docker-compose |

## Before production

1. Legal sign-off on `legal/pofa.py` rules and every `code_versions.yaml` value (all marked placeholders).
2. ~~Convert **all** KB-* records into `kb_modules.yaml`.~~ Done — all **51** KB-* modules and all
   **70** Appendix A blocks are converted (verified 1:1 against the source document, with the new
   block wordings byte-verbatim). 18 routes, 53 questions. Needs legal review of the judgement calls
   in the prose→predicate conversion, listed in item 7 below.
3. ~~Replace keyword route hints with the LLM classifier; keep the regex as the floor.~~ Done —
   `questioning.py` now calls an LLM classifier (task `questioning`) that returns a calibrated
   confidence per route; hints below `ROUTE_CONFIDENCE_THRESHOLD` or naming an unknown route are
   dropped, every call is logged to `case.audit` (event `route_classification`), and the regex
   floor still runs unconditionally so a bad/missing LLM response degrades to the old behaviour,
   never to zero hints. Swap `OpenAIClient` in for `FakeLLM` to go live; still only wire a real
   model where budget/latency allows, since the fallback path already covers outages.
4. Load bank holidays from gov.uk on a schedule instead of the fallback table.
5. Add the LLM judge to Engine 4 using a different prompt (ideally a different model) from drafting.
6. DPIA for special-category data (health/disability evidence) and a retention schedule.
7. **Three modules are currently unreachable** and need a design decision, not a mechanical fix.
   Rule Q-03 only asks facts that gate a module in a *narrative-hinted* route, but `POFA` and
   `LANDOWNER` have no narrative hints — correctly, since "the notice omits the creditor" is not
   something a customer's account reveals. So the facts gating `KB-POFA-04` (notice content defects,
   strength 90), `KB-LAND-02` and `KB-LAND-03` are never asked, and those modules can never fire.
   These facts are properties of the *documents*, not of the customer's story, so they belong on the
   confirmation screen or in a deterministic document check rather than the narrative-hinted flow.
   `KB-POFA-04` additionally cannot pass `VAL-POFA`, which requires a verified `pofa_findings` entry
   while `pofa.assess` only emits the three timing codes — a `POFA_CONTENT_DEFECT` code would be
   needed, and since that asserts a statutory defect it belongs with the item-1 legal sign-off.
   Confirmed by test: with the facts forced, the case blocks on `VAL-POFA` and fails safe to
   `MANUAL_REVIEW` rather than releasing an unverified defect.
8. Two pre-existing `do_not_use_when` gates reference facts nothing supplies
   (`KB-PAY-01`/`terms_rejected_left`, `KB-BREAK-01`/`fault_pre_existing_not_preventing`), so those
   suppressions never fire. `KB-PAY-01` is now covered by the `conflicts_with` edge to `KB-CON-02`
   instead; `KB-BREAK-01`'s is largely redundant given its `use_when`. Wire or remove them.
9. Three block wordings predate this work and diverge from the source document — `PP-ANPR-001` and
   `AI-BREAK-001` are truncated, `AI-RES-003` renames the source's `{{bay_reference}}` placeholder to
   `{{allocated_bay}}`. They are approved legal wordings, so reconcile them with the document.
