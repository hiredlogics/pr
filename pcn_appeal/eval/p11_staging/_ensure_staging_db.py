"""Create an isolated Neon database for P11 (do not reuse foreign app schema)."""
from __future__ import annotations

import os
import re
from pathlib import Path

STAGING_DB = "pcn_appeal_p11"


def _read_url() -> str:
    for line in Path(".env.staging.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("missing .env.staging.local")


def _rewrite_db(url: str, dbname: str) -> str:
    # Replace path database name: .../<db>?<query>
    return re.sub(r"/([^/?]+)(\?|$)", rf"/{dbname}\2", url, count=1)


def main() -> None:
    import psycopg

    base = _read_url()
    # Prefer unpooled host for CREATE DATABASE if present in original env file
    src = Path(r"C:\Users\Abdul\Downloads\Meme\parking-\.env.local")
    unpooled = None
    if src.exists():
        for line in src.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            if line.strip().startswith("DATABASE_URL_UNPOOLED="):
                unpooled = line.split("=", 1)[1].strip().strip("\"'")
                break
    admin_url = unpooled or base
    # Connect to maintenance DB
    admin_neondb = _rewrite_db(admin_url, "neondb")
    staging_url = _rewrite_db(base, STAGING_DB)

    print("admin_host", admin_neondb.split("@")[-1].split("/")[0])
    with psycopg.connect(admin_neondb, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (STAGING_DB,)
        ).fetchone()
        if exists:
            print("staging_db_exists", STAGING_DB)
        else:
            conn.execute(f'CREATE DATABASE "{STAGING_DB}"')
            print("staging_db_created", STAGING_DB)

    # Verify connect + vector
    with psycopg.connect(staging_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("staging_select", conn.execute("SELECT 1").fetchone()[0])
        print("staging_db", conn.execute("SELECT current_database()").fetchone()[0])

    Path(".env.staging.local").write_text(
        f"DATABASE_URL={staging_url}\n", encoding="utf-8"
    )
    print("updated .env.staging.local ->", STAGING_DB)


if __name__ == "__main__":
    main()
