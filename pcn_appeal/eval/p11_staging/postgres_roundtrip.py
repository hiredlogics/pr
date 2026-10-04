"""Real PostgreSQL Master Case persistence round-trip for staging."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "tests"
for p in (str(ROOT), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)


def run() -> dict[str, Any]:
    url = os.getenv("DATABASE_URL") or os.getenv("STAGING_DATABASE_URL")
    if not url and Path(".env.staging.local").exists():
        for line in Path(".env.staging.local").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
                break
    if not url:
        return {
            "passed": False,
            "ran": False,
            "reason": "DATABASE_URL not set — refusing SQLite substitute for P11",
        }
    os.environ["DATABASE_URL"] = url
    try:
        from pcn_appeal.case_state import master
        from pcn_appeal.engines.claim_plan_authority import latest_locked
        from pcn_appeal.models import CaseFile, CaseState, EvidenceItem
        from pcn_appeal.orchestrator import AppealPipeline
        from pcn_appeal.store import cases as case_store
        from pcn_appeal.store import db
        from pcn_appeal import config
        from support import ReferenceAnalysisLLM
        from test_scenarios import fields as extraction_fields
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "ran": False, "reason": f"import: {exc}"[:200]}

    config.load()
    if not db.enabled():
        return {"passed": False, "ran": False, "reason": "db.enabled() false"}

    steps: dict[str, Any] = {}
    try:
        db.init_schema()
        llm = ReferenceAnalysisLLM({
            "extraction": [{
                "fields": extraction_fields(
                    operator_name="Staging Park Ltd",
                    pcn_number="ST900001",
                    vrm="ST11AAA",
                    parking_location="Staging Site",
                    site_postcode="ST1 1AA",
                    parking_event_date="01/09/2026",
                    notice_issue_date="05/09/2026",
                    alleged_breach="No valid payment",
                    operator_ata="BPA",
                ),
                "doc_types": {"E1": "PCN"},
            }],
        })
        case = case_store.new_case()
        case.evidence["E1"] = EvidenceItem(
            "E1", "PCN", "notice.txt",
            text="PARKING CHARGE NOTICE\nPCN: ST900001\nVRM: ST11AAA",
        )
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        steps["create_ingest"] = case.state.value if hasattr(case.state, "value") else str(case.state)
        case_store.save(case)
        steps["save_1"] = True
        loaded = case_store.load(case.case_id)
        steps["reload_1"] = loaded is not None and loaded.case_id == case.case_id
        questions = pipe.confirm(
            loaded, {}, list(loaded.facts),
            "I paid on the app but mistyped one character of the plate.",
        )
        answers = {"payment_made": "yes", "payment_method": "APP", "keying_error_type": "MINOR"}
        for _ in range(4):
            if not questions:
                break
            given = {q["fact"]: answers[q["fact"]] for q in questions if q.get("fact") in answers}
            if not given:
                break
            questions = pipe.answer(loaded, given)
        steps["reassess"] = True
        out = pipe.generate(loaded)
        steps["draft_state"] = getattr(getattr(out, "state", None), "value", str(getattr(out, "state", None)))
        case_store.save(loaded)
        again = case_store.load(case.case_id)
        plan1 = latest_locked(loaded)
        plan2 = latest_locked(again)
        m1 = master(loaded).digest()
        m2 = master(again).digest()
        steps["reload_after_draft"] = again is not None
        steps["plan_ids_match"] = (
            getattr(plan1, "claim_plan_id", None) == getattr(plan2, "claim_plan_id", None)
        )
        steps["master_digest_match"] = m1 == m2
        # Presence checks
        with db.connect() as conn:
            for label, q in (
                ("facts", "SELECT count(*) FROM facts WHERE case_id = %s"),
                ("claim_plans", "SELECT count(*) FROM claim_plans WHERE case_id = %s"),
                ("draft_versions", "SELECT count(*) FROM draft_versions WHERE case_id = %s"),
            ):
                try:
                    steps[f"db_{label}"] = conn.execute(q, (case.case_id,)).fetchone()[0]
                except Exception as exc:  # noqa: BLE001
                    steps[f"db_{label}"] = f"ERR {type(exc).__name__}"
        passed = bool(
            steps.get("reload_1")
            and steps.get("reload_after_draft")
            and steps.get("master_digest_match")
            and steps.get("plan_ids_match")
        )
        return {"passed": passed, "ran": True, "case_id": case.case_id, "steps": steps}
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": False,
            "ran": True,
            "steps": steps,
            "error": f"{type(exc).__name__}: {exc}"[:400],
        }
