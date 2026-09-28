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

15 tests cover Dev Pack scenarios A–D plus: Scotland/byelaw PoFA exclusion, PoFA boundary cases,
bank-holiday working days, driver-safe questions, validator blocking, LLM-failure fallback,
prompt-injection flagging, low-confidence dates, and the LLM route-classifier's confidence
threshold + hallucinated-route guard.

## Run it live

```bash
pip install fastapi 'uvicorn[standard]' pydantic
./scripts/live_demo.sh                       # whole journey against a throwaway server
```

Or serve it and drive the endpoints yourself (`/docs` for the OpenAPI UI):

```bash
uvicorn pcn_appeal.api:app --reload --port 8077
```

`api.py` keeps cases in a process dict and has no auth; it is a dev surface, not a deployment.

## Choosing a model provider

`LLM_PROVIDER` selects `openai`, `anthropic` or `demo`; with it unset, whichever API key is present
wins, and with no key at all you get `DemoLLM` — a label matcher that reads only explicit
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
| `pcn_appeal/data/*.yaml` | KB modules, blocks, routes, questions, Code versions (admin-editable data) |
| `pcn_appeal/orchestrator.py` | State machine + regenerate/fallback/review loop |
| `pcn_appeal/api.py` | FastAPI surface (production) |
| `infra/` | Postgres + pgvector schema, Neo4j schema, docker-compose |

## Before production

1. Legal sign-off on `legal/pofa.py` rules and every `code_versions.yaml` value (all marked placeholders).
2. Convert **all** KB-* records into `kb_modules.yaml` (this repo contains a representative subset).
3. ~~Replace keyword route hints with the LLM classifier; keep the regex as the floor.~~ Done —
   `questioning.py` now calls an LLM classifier (task `questioning`) that returns a calibrated
   confidence per route; hints below `ROUTE_CONFIDENCE_THRESHOLD` or naming an unknown route are
   dropped, every call is logged to `case.audit` (event `route_classification`), and the regex
   floor still runs unconditionally so a bad/missing LLM response degrades to the old behaviour,
   never to zero hints. Swap `AnthropicClient` in for `FakeLLM` to go live; still only wire a real
   model where budget/latency allows, since the fallback path already covers outages.
4. Load bank holidays from gov.uk on a schedule instead of the fallback table.
5. Add the LLM judge to Engine 4 using a different prompt (ideally a different model) from drafting.
6. DPIA for special-category data (health/disability evidence) and a retention schedule.
