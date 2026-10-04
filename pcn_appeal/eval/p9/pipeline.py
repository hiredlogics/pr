"""Run a GoldenCase through the real AppealPipeline.

Extraction values are injected via ReferenceAnalysisLLM because this baseline
has no live vision model. That injection is recorded and is not scored as
extraction accuracy.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pcn_appeal.case_state import SCHEMA_VERSION as MASTER_SCHEMA, master
from pcn_appeal.drafting.context import DraftContext
from pcn_appeal.engines.claim_plan_authority import BUILDER_VERSION, latest_locked
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.prompts import versions as prompt_versions
from pcn_appeal.version import commit
from support import ReferenceAnalysisLLM
from test_scenarios import fields as extraction_fields

from .schema import COMPLETE, GoldenCase

MAX_ROUNDS = 6


def _answer_for(q: dict, answers: dict, policy: str) -> Any:
    fact = q.get("fact")
    if fact in answers:
        return answers[fact]
    if policy == "yes":
        return 18 if q.get("type") == "int" else "yes"
    if policy == "no":
        return 0 if q.get("type") == "int" else "no"
    if policy == "dont_know":
        return "I don't know"
    return None


def _evidence(case: GoldenCase) -> dict[str, EvidenceItem]:
    items: dict[str, EvidenceItem] = {}
    if case.input.notice_front or case.input.notice_back:
        text = case.input.notice_front
        if case.input.notice_back:
            text = f"{text}\n--- page 2 ---\n{case.input.notice_back}"
        items["E1"] = EvidenceItem("E1", "PCN", "notice.txt", text=text or "NOTICE")
    for raw in case.input.evidence or []:
        eid = raw.get("evidence_id") or f"E{len(items) + 1}"
        items[eid] = EvidenceItem(
            eid, raw.get("kind") or "OTHER", raw.get("filename") or f"{eid}.txt",
            text=raw.get("text") or "",
        )
    if not items:
        items["E1"] = EvidenceItem("E1", "OTHER", "empty.txt", text="")
    return items


def _llm(case: GoldenCase) -> tuple[ReferenceAnalysisLLM, str]:
    ext = dict(case.input.extraction_fields or {})
    if ext:
        method = "INJECTED_FROM_DOCUMENT_GOLD"
        payload = {
            "extraction": [{
                "fields": extraction_fields(**ext),
                "doc_types": dict(case.input.doc_types or {"E1": "PCN"}),
            }],
        }
    else:
        method = "INJECTED_EMPTY"
        payload = {
            "extraction": [{
                "fields": {},
                "doc_types": dict(case.input.doc_types or {"E1": "OTHER"}),
            }],
        }
    return ReferenceAnalysisLLM(payload), method


def collect_actual(case: CaseFile, out, asked: list[str], extraction_method: str,
                   error: Optional[str] = None) -> dict:
    plan = latest_locked(case)
    pack = getattr(out, "pack", None) if out is not None else None
    draft = getattr(out, "draft", None) if out is not None else None
    letter = (getattr(out, "letter", None) if out is not None else None) or ""
    validation = getattr(out, "validation", None) if out is not None else None
    m = master(case)
    ctx_payload = {}
    ctx_error = None
    if pack is not None:
        try:
            ctx_payload = DraftContext.from_pack(pack).to_payload()
        except Exception as exc:  # noqa: BLE001 — baseline must record, not raise
            ctx_error = str(exc)[:300]
    facts = {}
    for name, node in (case.facts or {}).items():
        src = getattr(node.source, "kind", None)
        facts[name] = {
            "value": node.value,
            "status": getattr(node.status, "value", node.status),
            "source": getattr(src, "value", src),
            "fact_id": getattr(node, "fact_id", None),
            "authority": getattr(node, "authority", None),
        }
    findings = []
    for rec in getattr(case, "legal_findings", None) or []:
        if isinstance(rec, dict):
            findings.append(rec)
    if pack is not None and not findings:
        findings = list(getattr(pack, "legal_findings", None) or [])
    supported = list(plan.supported_ids) if plan is not None else list(
        getattr(pack, "module_ids", None) or [])
    items = []
    if plan is not None:
        for item in plan.items:
            items.append({
                "module_id": item.module_id,
                "status": item.status,
                "origin": getattr(item, "decision", None),
                "supporting_facts": list(getattr(item, "supporting_facts", None) or []),
                "derived_facts": list(getattr(item, "derived_facts", None) or []),
                "findings": list(getattr(item, "findings", None) or []),
                "evidence_refs": list(getattr(item, "evidence_refs", None) or []),
                "required_particulars": list(
                    getattr(item, "required_particulars", None) or []),
                "reason": getattr(item, "reason", None),
            })
    retrieved = []
    for ev in reversed(getattr(case, "audit", None) or []):
        if ev.get("event") in ("analysis_round", "case_analysis"):
            retrieved = list(ev.get("grounds") or ev.get("module_ids") or [])
            if retrieved:
                break
    if pack is not None and not retrieved:
        retrieved = list(pack.module_ids or [])
    issues = []
    if validation is not None:
        issues = [
            {"rule": i.rule, "severity": i.severity, "message": i.message}
            for i in (validation.issues or [])
        ]
    return {
        "extraction_method": extraction_method,
        "error": error,
        "state": getattr(getattr(out, "state", None) or case.state, "value",
                         getattr(out, "state", None) or case.state),
        "outcome": getattr(out, "outcome", None) if out is not None else None,
        "scope_stop": getattr(case, "scope_stop", None),
        "document_classes": dict(getattr(case, "document_classes", None) or {}),
        "asked_questions": list(asked),
        "pending_questions": [
            q.get("fact") for q in (getattr(case, "pending_questions", None) or [])
        ],
        "facts": facts,
        "findings": findings,
        "pofa_findings": list(getattr(pack, "pofa_findings", None) or []),
        "pofa_route": getattr(pack, "pofa_route", None),
        "primary_route": getattr(pack, "primary_route", None),
        "retrieved_modules": retrieved,
        "supported_grounds": supported,
        "claim_plan_items": items,
        "plan_digest": getattr(plan, "plan_digest", None) if plan is not None else None,
        "plan_id": getattr(plan, "claim_plan_id", None) if plan is not None else None,
        "draft_context": ctx_payload,
        "draft_context_error": ctx_error,
        "letter": letter,
        "validation_passed": bool(getattr(validation, "passed", False)) if validation else None,
        "validation_issues": issues,
        "placeholders": _placeholders(letter),
        "master_digest": m.digest(),
        "versions": versions_block(),
    }


def versions_block() -> dict:
    from pcn_appeal.kg.graph import KnowledgeGraph
    kg = KnowledgeGraph()
    return {
        "git_commit": commit(),
        "kb_release": getattr(kg, "release_id", None),
        "prompt_versions": prompt_versions(),
        "model_provider": "ReferenceAnalysisLLM",
        "claim_plan_builder_version": BUILDER_VERSION,
        "master_case_object_version": MASTER_SCHEMA,
        "extractor": "INJECTED_FROM_DOCUMENT_GOLD",
    }


def _placeholders(letter: str) -> list[str]:
    import re
    if not letter:
        return []
    return re.findall(r"\{[A-Z][A-Z0-9_]+\}|\[(?:TODO|PLACEHOLDER|TBD)[^\]]*\]", letter)


def run_once(golden: GoldenCase) -> dict:
    llm, method = _llm(golden)
    case = CaseFile(golden.case_id, evidence=_evidence(golden))
    pipe = AppealPipeline(llm)
    asked: list[str] = []
    out = None
    try:
        pipe.ingest(case)
        if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
            return {"ok": True, **collect_actual(case, None, asked, method)}
        confirmed = list(case.facts)
        questions = pipe.confirm(
            case, golden.input.corrections or {}, confirmed,
            golden.input.customer_narrative or "",
        )
        if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
            return {"ok": True, **collect_actual(case, None, asked, method)}
        policy = golden.input.answer_policy or "skip"
        answers = dict(golden.input.customer_answers or {})
        for _ in range(MAX_ROUNDS):
            if not questions:
                break
            asked += [q.get("fact") for q in questions if q.get("fact")]
            given = {q["fact"]: _answer_for(q, answers, policy) for q in questions}
            given = {k: v for k, v in given.items() if v is not None}
            if not given:
                break
            questions = pipe.answer(case, given)
        out = pipe.generate(case)
        return {"ok": True, **collect_actual(case, out, asked, method),
                "_case": case, "_out": out, "_pipe": pipe}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            **collect_actual(case, out, asked, method, error=f"{type(exc).__name__}: {exc}"),
            "traceback": traceback.format_exc()[-2000:],
            "_case": case,
        }


def persist_reload_check(case: CaseFile) -> dict:
    """Master Case + claim-plan save/load through the SQLite store stand-in."""
    from unittest import mock

    from pcn_appeal.store import cases as case_store
    from sqlite_store import make_store

    plan = latest_locked(case)
    before = list(plan.supported_ids) if plan is not None else []
    digest = getattr(plan, "plan_digest", None) if plan is not None else None
    blob = json.dumps(master(case).to_dict(), default=str)
    db, connect = make_store()
    try:
        with mock.patch("pcn_appeal.store.cases.connect", connect), \
             mock.patch("pcn_appeal.store.db.enabled", return_value=True):
            stored = case_store.new_case()
            stored.case_id  # created
            # Re-key is not possible; save the live case under a fresh row by
            # swapping id for the store round-trip only.
            original_id = case.case_id
            case.case_id = stored.case_id
            try:
                case_store.save(case)
                loaded = case_store.load(stored.case_id)
            finally:
                case.case_id = original_id
            loaded_plan = latest_locked(loaded)
            after = list(loaded_plan.supported_ids) if loaded_plan is not None else []
            restored = master(case)
            restored.load_dict(json.loads(blob))
            return {
                "passed": before == after,
                "before": before,
                "after": after,
                "digest_before": digest,
                "digest_after": getattr(loaded_plan, "plan_digest", None)
                if loaded_plan is not None else None,
                "master_roundtrip_ok": True,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": False,
            "before": before,
            "after": [],
            "error": f"{type(exc).__name__}: {exc}",
            "master_roundtrip_ok": False,
        }
    finally:
        db.close()


def semantic_state(actual: dict) -> dict:
    """Authoritative semantic state for repeatability (wording excluded)."""
    return {
        "facts": {n: [r.get("value"), r.get("status"), r.get("source")]
                  for n, r in (actual.get("facts") or {}).items()},
        "findings": sorted(
            (f.get("finding_type"), f.get("status"))
            for f in (actual.get("findings") or [])
            if isinstance(f, dict)
        ),
        "supported_grounds": list(actual.get("supported_grounds") or []),
        "plan_digest": actual.get("plan_digest"),
        "state": actual.get("state"),
        "outcome": actual.get("outcome"),
    }
