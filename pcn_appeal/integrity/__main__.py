"""CLI for the integrity layer.

  python -m pcn_appeal.integrity journeys journeys/ --out reports/
        in-process: the API in this process, with the configured provider
        (demo when no key is set), no database unless DATABASE_URL is set.

  python -m pcn_appeal.integrity journeys journeys/ --base-url https://staging... \
        --admin-token "$ADMIN_TOKEN" --out reports/
        against a deployed API: real model, real database.

  python -m pcn_appeal.integrity db-checks [--case CASE_ID]
        the database invariants (needs DATABASE_URL).

Exit status 1 when any journey or check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _journeys(args) -> int:
    from .journeys import JourneyRunner, run_directory, summary
    token = args.admin_token or os.getenv("ADMIN_TOKEN") or os.getenv("ADMIN_TRACE_TOKEN")
    if args.base_url:
        import httpx
        client = httpx.Client(base_url=args.base_url, timeout=300)
    else:
        from fastapi.testclient import TestClient
        from ..api import app
        client = TestClient(app)
    runner = JourneyRunner(client, token, Path(args.out) if args.out else None,
                           Path(args.golden) if args.golden else None, args.update_golden)
    results = run_directory(runner, Path(args.directory))
    out = summary(results)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["passed"] == out["journeys"] else 1


def _db_checks(args) -> int:
    from ..store.db import connect, enabled
    from .checks import check_store
    if not enabled():
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    results = check_store(connect, args.case)
    print(json.dumps(results, indent=2, default=str))
    return 0 if all(r["status"] == "PASS" for r in results) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pcn_appeal.integrity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("journeys", help="run the journey regression harness")
    j.add_argument("directory")
    j.add_argument("--out", help="write <case>_CASE_REPORT.md files here")
    j.add_argument("--base-url", help="deployed API to drive instead of in-process")
    j.add_argument("--admin-token")
    j.add_argument("--golden", help="directory of golden snapshots to compare each run against")
    j.add_argument("--update-golden", action="store_true", help="write the snapshots instead of comparing")
    j.set_defaults(fn=_journeys)
    d = sub.add_parser("db-checks", help="database integrity checks")
    d.add_argument("--case")
    d.set_defaults(fn=_db_checks)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
