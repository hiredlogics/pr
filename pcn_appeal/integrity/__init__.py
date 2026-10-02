"""P5.5 - AI System Integrity Audit Layer.

Observability over the whole pipeline, adding no intelligence:

    ai_log     every model call, logged against its case (hashes, never text)
    trace      one execution trace per run: stages, timing, counts, versions,
               state transitions with reasons
    checks     automated integrity checks (in memory and over the database)
    report     CASE_REPORT.md per run
    journeys   the regression harness: input-only case files -> full customer
               journey -> audit report -> PASS / FAIL

`record(case, out, pipeline)` runs at the end of every generate(): it puts the
checks and the trace on the output, in the audit and (through the store) in
`case_execution_trace`.
"""
from __future__ import annotations

from .checks import check_case, check_store, passed
from .report import case_report
from .trace import execution_trace


def record(case, out, pipeline) -> None:
    """Checks + trace for the run just completed. Never fails the run."""
    try:
        kg = getattr(pipeline, "kg", None)
        checks = check_case(case, out, kg)
        case.audit.append({"event": "integrity_check", "passed": passed(checks),
                           "failed": [c["check"] for c in checks if c["status"] != "PASS"],
                           "checks": checks})
        trace = execution_trace(case)
        out.integrity = {"passed": passed(checks), "checks": checks, "trace": trace,
                         "report": case_report(case, out, kg, checks)}
    except Exception as exc:                     # pragma: no cover - defensive
        case.audit.append({"event": "integrity_check_failed",
                           "error": f"{type(exc).__name__}: {exc}"[:200]})


__all__ = ["record", "check_case", "check_store", "passed", "case_report", "execution_trace"]
