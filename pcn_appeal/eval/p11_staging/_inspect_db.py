from __future__ import annotations

import os
from pathlib import Path


def _load():
    for line in Path(".env.staging.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()


def main() -> None:
    _load()
    from pcn_appeal.store import db

    with db.connect() as c:
        rows = c.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
            ORDER BY 1,2
            """
        ).fetchall()
        print("tables", len(rows))
        for s, t in rows:
            print(f"{s}.{t}")
        if any(t == "cases" for _, t in rows):
            cols = c.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='cases'
                ORDER BY ordinal_position
                """
            ).fetchall()
            print("cases_cols", cols)
        # Can we create a dedicated staging database?
        try:
            dbname = c.execute("SELECT current_database()").fetchone()[0]
            print("current_database", dbname)
        except Exception as exc:
            print("db_error", exc)


if __name__ == "__main__":
    main()
