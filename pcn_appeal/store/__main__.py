"""Database CLI.

    export DATABASE_URL=postgresql://postgres:dev@localhost:5432/postgres
    python -m pcn_appeal.store init     # apply infra/postgres_schema.sql
    python -m pcn_appeal.store sync     # push data/*.yaml -> tables + pgvector, publish a release
    python -m pcn_appeal.store status    # what is actually in there
    python -m pcn_appeal.store knowledge-seed   # KB modules + relationships -> knowledge_* (P4)
"""
from __future__ import annotations

import sys

from . import db


def _init() -> int:
    db.init_schema()
    print("schema applied")
    return 0


def _sync() -> int:
    from .kb_sync import sync
    result = sync()
    print(f"release   {result['release_id']}")
    print(f"modules   {result['modules']}")
    print(f"blocks    {result['blocks']}")
    print(f"embedded  {result['embeddings']} rows  ({result['embedder']}, dim {result['dim']})")
    return 0


def _status() -> int:
    with db.connect() as conn:
        for label, q in (("modules", "SELECT count(*) FROM kb_modules"),
                         ("blocks", "SELECT count(*) FROM kb_blocks"),
                         ("embeddings", "SELECT count(*) FROM kb_embeddings"),
                         ("code_versions", "SELECT count(*) FROM code_versions"),
                         ("releases", "SELECT count(*) FROM kb_releases"),
                         ("cases", "SELECT count(*) FROM cases")):
            print(f"{label:<14}{conn.execute(q).fetchone()[0]}")
        row = conn.execute("SELECT kb_release_id, published_at FROM kb_releases "
                           "ORDER BY published_at DESC LIMIT 1").fetchone()
        print(f"latest        {row[0]} ({row[1]:%Y-%m-%d %H:%M})" if row else "latest        none")
        active = conn.execute("SELECT route, count(*) FROM kb_modules WHERE status = 'ACTIVE' "
                              "GROUP BY route ORDER BY 2 DESC").fetchall()
        print("routes        " + ", ".join(f"{r}={c}" for r, c in active))
    return 0


def _knowledge_seed() -> int:
    from ..kg.graph import KnowledgeGraph
    from .knowledge import seed
    result = seed(KnowledgeGraph())
    print(f"relations {result['relations_version']}")
    print(f"nodes     {result['nodes']} written")
    print(f"edges     {result['edges']} written")
    return 0


COMMANDS = {"init": _init, "sync": _sync, "status": _status, "knowledge-seed": _knowledge_seed}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    if not db.enabled():
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
