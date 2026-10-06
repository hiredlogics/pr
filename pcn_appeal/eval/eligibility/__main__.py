"""python -m pcn_appeal.eval.eligibility [--out file.json]

Runs the operator truth table, the module matrix and the named scenarios against
the code as it stands, through all three paths (matcher, standalone API, reference).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import harness as H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    ops = H.operator_table()
    mm_matcher = H.module_matrix(H.production_status)
    mm_api = H.module_matrix(H.eligibility_status)
    sc_case = H.run_scenarios(H.case_scenario_status)
    sc_api = H.run_scenarios(H.eligibility_scenario_status)
    report = {
        "operator_table": {"cells": len(ops["rows"]), "bad": ops["bad"]},
        "module_matrix_matcher": mm_matcher, "module_matrix_api": mm_api,
        "scenarios_case_path": sc_case, "scenarios_api": sc_api,
    }
    print(f"operator cells {len(ops['rows'])}, mismatches {len(ops['bad'])}")
    for name, mm in (("matcher", mm_matcher), ("api", mm_api)):
        print(f"module matrix [{name}]: {mm['cases']} cases, {mm['disagree']} disagreements, "
              f"modules: {mm['modules_with_disagreement']}")
    for name, sc in (("case path", sc_case), ("api", sc_api)):
        print(f"scenarios [{name}]: {len(sc['rows'])} run, {len(sc['bad'])} mismatches, {sc['counts']}")
        for r in sc["bad"]:
            print("   ", r["id"], r["module"], "expected", r["expected"], "got", r["got"])
    if a.out:
        json.dump(report, open(a.out, "w"), indent=1, default=str)
    bad = len(ops["bad"]) + mm_matcher["disagree"] + mm_api["disagree"] + len(sc_case["bad"]) + len(sc_api["bad"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
