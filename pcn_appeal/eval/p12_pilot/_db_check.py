from pathlib import Path
import os

for name in (".env.staging.local", ".env.local", ".env"):
    p = Path(name)
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()

from pcn_appeal.store import db

with db.connect() as conn:
    rows = conn.execute("select distinct outcome from fact_history").fetchall()
    print("outcomes", [r[0] for r in rows])
    c = conn.execute(
        "select pg_get_constraintdef(c.oid) from pg_constraint c "
        "where c.conname='fact_history_outcome_check'"
    ).fetchone()
    print("constraint", c[0] if c else None)
    bad = conn.execute(
        "select outcome, count(*) from fact_history "
        "where outcome not in ('APPLIED','CONFLICT','IGNORED','RETRACTED') "
        "group by outcome"
    ).fetchall()
    print("bad", list(bad))
