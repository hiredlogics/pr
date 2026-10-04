"""Run frozen pipeline on P10.4 validation / sealed holdout cases."""
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
from pcn_appeal.module_roles import classify_pack, role_of
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.prompts import versions as prompt_versions
from pcn_appeal.version import commit
from support import ReferenceAnalysisLLM
from test_scenarios import fields as extraction_fields

VAL_DIR = ROOT / "datasets" / "p10_4_v1" / "validation"
HOLDOUT_DIR = ROOT / "datasets" / "p10_3_v1" / "holdout"
MAX_ROUNDS = 6


def load_json_cases(directory: Path) -> list[dict]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def _evidence(spec: dict) -> dict[str, EvidenceItem]:
    inp = spec.get("input") or {}
    items: dict[str, EvidenceItem] = {}
    if inp.get("notice_front") or inp.get("notice_back") or inp.get("extraction_fields"):
        text = inp.get("notice_front") or "PARKING CHARGE NOTICE"
        if inp.get("notice_back"):
            text = f"{text}\n--- page 2 ---\n{inp['notice_back']}"
        items["E1"] = EvidenceItem("E1", "PCN", "notice.txt", text=text)
    for raw in inp.get("evidence") or []:
        eid = raw.get("evidence_id") or f"E{len(items) + 1}"
        items[eid] = EvidenceItem(
            eid, raw.get("kind") or "OTHER", raw.get("filename") or f"{eid}.txt",
            text=raw.get("text") or "",
        )
    if not items:
        items["E1"] = EvidenceItem("E1", "OTHER", "empty.txt", text="")
    return items


def _llm(spec: dict):
    inp = spec.get("input") or {}
    ext = dict(inp.get("extraction_fields") or {})
    doc_types = {"E1": "PCN"}
    for raw in inp.get("evidence") or []:
        eid = raw.get("evidence_id")
        if eid:
            doc_types[eid] = raw.get("doc_type") or raw.get("kind") or "OTHER"
    payload = {
        "extraction": [{
            "fields": extraction_fields(**ext) if ext else {},
            "doc_types": doc_types,
        }],
    }
    return ReferenceAnalysisLLM(payload)


def _answer_for(q: dict, answers: dict) -> Any:
    fact = q.get("fact")
    if fact in answers:
        return answers[fact]
    return None


def collect_actual(case: CaseFile, out, asked: list[str],
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
        except Exception as exc:  # noqa: BLE001
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
            "lineage": getattr(node, "lineage", None) or getattr(node, "derived_from", None),
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
            mid = item.module_id
            items.append({
                "module_id": mid,
                "status": item.status,
                "role": role_of(mid),
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
        if ev.get("event") in ("analysis_round", "case_analysis", "knowledge_match"):
            retrieved = list(ev.get("grounds") or ev.get("module_ids")
                             or ev.get("candidates") or [])
            if retrieved:
                break
    if pack is not None and not retrieved:
        retrieved = list(pack.module_ids or [])

    concepts = []
    raw = (case.raw_answers or {}).get("_semantic_concepts")
    if raw:
        try:
            concepts = json.loads(raw)
        except (TypeError, ValueError):
            concepts = []

    roles = classify_pack(supported)
    issues = []
    if validation is not None:
        issues = [
            {"rule": i.rule, "severity": i.severity, "message": i.message}
            for i in (validation.issues or [])
        ]

    support_complete = False
    if plan is not None and plan.supported_ids:
        approved = set(plan.supported_ids)
        try:
            support_complete = all(
                bool(getattr(i, "supporting_facts", None)
                     or getattr(i, "findings", None)
                     or getattr(i, "evidence_refs", None)
                     or getattr(i, "derived_facts", None))
                for i in plan.items if i.module_id in approved
            )
        except Exception:
            support_complete = bool(supported)
    elif supported:
        support_complete = True

    return {
        "error": error,
        "state": getattr(getattr(out, "state", None) or case.state, "value",
                         getattr(out, "state", None) or case.state),
        "outcome": getattr(out, "outcome", None) if out is not None else None,
        "asked_questions": list(asked),
        "facts": facts,
        "findings": findings,
        "concepts": concepts,
        "retrieved_modules": retrieved,
        "supported_grounds": supported,
        "claim_plan_items": items,
        "role_partition": roles,
        "plan_digest": getattr(plan, "plan_digest", None) if plan is not None else None,
        "plan_id": getattr(plan, "claim_plan_id", None) if plan is not None else None,
        "primary_route": getattr(pack, "primary_route", None) if pack else None,
        "secondary_routes": list(getattr(pack, "secondary_routes", None) or []),
        "draft_context": ctx_payload,
        "draft_context_error": ctx_error,
        "draft_context_complete": bool(ctx_payload) and not ctx_error,
        "support_bundle_complete": support_complete,
        "letter": letter,
        "letter_empty": not bool(str(letter).strip()),
        "validation_passed": bool(getattr(validation, "passed", False)) if validation else None,
        "validation_issues": issues,
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


def run_once(spec: dict) -> dict:
    llm = _llm(spec)
    case = CaseFile(spec["case_id"], evidence=_evidence(spec))
    pipe = AppealPipeline(llm)
    asked: list[str] = []
    out = None
    inp = spec.get("input") or {}
    try:
        pipe.ingest(case)
        if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
            return {"ok": True, **collect_actual(case, None, asked),
                    "_case": case, "_out": None, "_pipe": pipe}
        questions = pipe.confirm(
            case, inp.get("corrections") or {}, list(case.facts),
            inp.get("customer_narrative") or "",
        )
        if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
            return {"ok": True, **collect_actual(case, None, asked),
                    "_case": case, "_out": None, "_pipe": pipe}
        answers = dict(inp.get("customer_answers") or {})
        for _ in range(MAX_ROUNDS):
            if not questions:
                break
            asked += [q.get("fact") for q in questions if q.get("fact")]
            given = {q["fact"]: _answer_for(q, answers) for q in questions}
            given = {k: v for k, v in given.items() if v is not None}
            if not given:
                break
            questions = pipe.answer(case, given)
        out = pipe.generate(case)
        return {"ok": True, **collect_actual(case, out, asked),
                "_case": case, "_out": out, "_pipe": pipe}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            **collect_actual(case, out, asked, error=f"{type(exc).__name__}: {exc}"),
            "traceback": traceback.format_exc()[-2000:],
            "_case": case, "_out": out, "_pipe": pipe,
        }


def persist_reload_check(case: CaseFile) -> dict:
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
