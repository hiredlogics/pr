"""python -m pcn_appeal.eval.p11_2"""
from __future__ import annotations

import json
import sys

from .proof import run, write_report


def main() -> int:
    report = run()
    path = write_report(report)
    summary = {
        "verdict": report.get("verdict"),
        "case_id": report.get("case_id"),
        "final_state": report.get("final_state"),
        "kb_release_id": report.get("kb_release_id"),
        "ready_checks": report.get("ready_checks"),
        "report": str(path),
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if report.get("verdict") == "READY_FOR_PRODUCTION_PILOT" else 1


if __name__ == "__main__":
    sys.exit(main())
