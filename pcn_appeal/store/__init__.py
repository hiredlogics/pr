"""Postgres + pgvector persistence.

The KB is AUTHORED in `data/*.yaml` (git-versioned, legal-reviewable) and
SERVED from Postgres. `kb_sync` pushes the YAML into `kb_modules`/`kb_blocks`/
`kb_embeddings` and freezes a `kb_releases` snapshot; `kb_source` reads a
release back out in the exact shape `kg.graph.KnowledgeGraph` expects.

Everything here is optional. With no DATABASE_URL the system runs entirely
from YAML and in-memory state, which is how the test suite stays offline.
"""
from .db import DATABASE_URL, connect, enabled, init_schema

__all__ = ["DATABASE_URL", "connect", "enabled", "init_schema"]
