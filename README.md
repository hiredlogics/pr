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

14 tests cover Dev Pack scenarios A–D plus: Scotland/byelaw PoFA exclusion, PoFA boundary cases,
bank-holiday working days, driver-safe questions, validator blocking, LLM-failure fallback,
prompt-injection flagging and low-confidence dates.

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
3. Replace keyword route hints with the LLM classifier; keep the regex as the floor.
4. Load bank holidays from gov.uk on a schedule instead of the fallback table.
5. Add the LLM judge to Engine 4 using a different prompt (ideally a different model) from drafting.
6. DPIA for special-category data (health/disability evidence) and a retention schedule.
