"""P8/P10 non-regression checks run during P10.4 evaluation (read-only)."""
from __future__ import annotations

import unittest
from io import StringIO
from typing import Optional


def run_unittest_modules(module_names: list[str]) -> dict:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in module_names:
        try:
            suite.addTests(loader.loadTestsFromName(name))
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "error": f"load failed for {name}: {exc}",
                "tests_run": 0,
            }
    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    failures = [
        {"test": str(t), "message": str(m).splitlines()[-1][:240]}
        for t, m in (result.failures + result.errors)
    ]
    return {
        "passed": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "failures": failures,
        "failures_count": len(result.failures),
        "errors_count": len(result.errors),
        "output_tail": stream.getvalue()[-3000:],
    }


def run_p8_p10_invariants() -> dict:
    modules = [
        "tests.test_p8_architecture",
        "tests.test_p10_3_semantic_layer",
        "tests.test_master_case_object",
    ]
    # Optional remediation suite if present
    try:
        import tests.test_p10_remediation  # noqa: F401
        modules.append("tests.test_p10_remediation")
    except Exception:
        pass

    detail = {}
    all_pass = True
    total = 0
    for name in modules:
        short = name.split(".")[-1]
        row = run_unittest_modules([name])
        detail[short] = row
        total += row.get("tests_run") or 0
        all_pass = all_pass and bool(row.get("passed"))

    checks = {
        "Master Case": detail.get("test_master_case_object", {}).get("passed"),
        "FactManager / P8 architecture": detail.get("test_p8_architecture", {}).get("passed"),
        "P10.3 semantic + roles": detail.get("test_p10_3_semantic_layer", {}).get("passed"),
        "P10 remediation": detail.get("test_p10_remediation", {}).get("passed"),
    }
    return {
        "passed": all_pass,
        "tests_run": total,
        "checks": checks,
        "detail": detail,
        "required_pass": [
            "Master Case",
            "FactManager authority",
            "fact lineage",
            "verified legal findings",
            "additive grounds",
            "Claim Plan persistence",
            "SupportBundle completeness",
            "DraftContext completeness",
            "same-value stability",
            "outcome authority",
            "repeatability",
        ],
        "note": (
            "Covered via existing unittest modules (test_p8_architecture, "
            "test_master_case_object, test_p10_3_semantic_layer, test_p10_remediation)."
        ),
    }


def repeatability_check(run_fn, spec: dict, n: int = 3) -> dict:
    """Same-case semantic-state stability across n runs."""
    states = []
    for _ in range(n):
        actual = run_fn(spec)
        actual.pop("_case", None)
        actual.pop("_out", None)
        actual.pop("_pipe", None)
        states.append({
            "supported_grounds": list(actual.get("supported_grounds") or []),
            "state": actual.get("state"),
            "plan_digest": actual.get("plan_digest"),
            "concepts": sorted(
                (c.get("concept"), c.get("polarity"))
                for c in (actual.get("concepts") or [])
            ),
        })
    first = states[0]
    stable = all(s == first for s in states[1:])
    return {"passed": stable, "n": n, "states": states}
