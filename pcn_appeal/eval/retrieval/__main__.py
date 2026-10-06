"""python -m pcn_appeal.eval.retrieval [--view window|connected] [--out FILE]"""
from __future__ import annotations

import argparse
import json

from .harness import run_and_report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="current", choices=["current", "new"])
    ap.add_argument("--view", default="window", choices=["window", "connected"])
    ap.add_argument("--out")
    a = ap.parse_args()
    rep = run_and_report(a.mode, a.view)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1)
    print(json.dumps(rep["metrics"], indent=1))
    for r in rep["rows"]:
        flag = "ok  " if r["pass"] else "FAIL"
        print(f"{flag} {r['id']:<18} n={r['n']:<3} missed={r['missed']} bad={r['must_not_hit']}"
              f"{' LEAK=' + str(r['leak']) if r['leak'] else ''}"
              f"{' ZERO!' if r['zero_violation'] else ''}")
    print(json.dumps(rep["groups"].get("equivalent_meaning"), indent=1))
    if "breakdown" in rep:
        print(json.dumps(rep["breakdown"], indent=1))


if __name__ == "__main__":
    main()
