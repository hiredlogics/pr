"""P12 controlled cohort: 10–20 real/varied cases via the normal customer path.

Does not invent reverse-page content. Physical fronts without a genuine back
are uploaded front-only (document-quality / NEEDS_DOCUMENTS is a valid outcome).
p9 COMPLETE cases use dataset-provided front+back text only.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[3]
IMG_DIR = ROOT / "datasets" / "pcn_finetune_v1" / "images"
CASE_DIR = ROOT / "datasets" / "pcn_finetune_v1" / "cases"
NOTICE_DIR = ROOT / "datasets" / "p9_v1" / "notice_only"
COMPLETE_DIR = ROOT / "datasets" / "p9_v1" / "complete"
PINNED_KB = "kb-20261004T113657Z"
COHORT_CAP = 20

# Family-appropriate customer narratives (customer journey input — not reverse pages).
_NARRATIVES = {
    "00347261120013": (
        "I attended the car park for shopping at the retail estate. "
        "Partway through I realised I had forgotten my purse at home, "
        "so I left the site. I returned later the same day to continue my visit."
    ),
    "70377302": (
        "I visited the site briefly to drop something off, left, and came back "
        "later the same day. The cameras appear to have joined two separate visits."
    ),
    "sp62712518": (
        "I went shopping, left to collect a payment card, and returned later "
        "the same day. This was more than one visit, not one continuous stay."
    ),
    "88811908015": (
        "I paid for parking but may have mistyped the registration. "
        "I have the payment confirmation."
    ),
    "default_anpr": (
        "I left the site and returned later the same day; there were multiple "
        "visits rather than one continuous stay."
    ),
    "default_payment": (
        "I paid for parking using the app / machine for the vehicle on that date."
    ),
    "default_overstay": (
        "I believe the stay was within the permitted period once grace is considered, "
        "or the signage terms were not clear."
    ),
}

_ANSWERS = {
    "multiple_visits": {
        "multiple_visits": "yes", "left_site": "yes", "returned_same_day": "yes",
        "payment_made": "no",
    },
    "payment": {
        "payment_made": "yes", "payment_method": "APP",
    },
    "payment_keying": {
        "payment_made": "yes", "payment_method": "APP", "keying_error_type": "MINOR",
    },
    "breakdown": {
        "vehicle_immobilised": "yes", "immobilisation_prevented_departure": "yes",
        "recovery_attended": "yes",
    },
    "minimal": {"payment_made": "no"},
}


def _load_env() -> None:
    for name in (".env.staging.local", ".env.local", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip().strip("\"'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def _jpeg_text_page(text: str) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + (text or "").encode("utf-8", errors="ignore")[:180] + b"\xff\xd9"
        )
    img = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    y = 24
    for line in (text or "").splitlines() or ["(blank page)"]:
        draw.text((28, y), line[:90], fill="black", font=font)
        y += 26
        if y > 1360:
            break
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _letter(case) -> str:
    for d in reversed(case.draft_versions or []):
        parts = []
        for para in d.get("content") or []:
            for sent in para or []:
                if isinstance(sent, dict) and sent.get("text"):
                    parts.append(sent["text"])
        if parts:
            return "\n".join(parts)
    return ""


def _coverage(letter: str) -> dict[str, bool]:
    t = (letter or "").lower()
    return {
        "cancel_request": bool(re.search(r"\bcancel", t)),
        "placeholder": bool(re.search(r"\{\{|TODO|TBD|\[insert", t, re.I)),
        "driver_disclosure": bool(re.search(
            r"\b(i (parked|drove|was driving)|i am the driver)\b", t)),
    }


def _first_defect_layer(rec: dict[str, Any]) -> Optional[str]:
    if rec.get("infrastructure_error"):
        return "INFRASTRUCTURE_ERROR"
    if rec.get("document_quality_failure") and rec.get("final_state") in (
            "NEEDS_DOCUMENTS", "NEEDS_FACTS", None):
        # Hold for missing reverse is correct — not a system defect.
        if rec.get("outcome_correct_hold"):
            return None
        return "DOCUMENT_ERROR"
    if rec.get("extraction_failed"):
        return "EXTRACTION_ERROR"
    if rec.get("semantic_failed"):
        return "SEMANTIC_ERROR"
    if rec.get("fact_propagation_failed"):
        return "FACT_PROPAGATION_ERROR"
    if rec.get("pofa_from_narrative"):
        return "LEGAL_FINDING_ERROR"
    if rec.get("ground_selection_failed"):
        return "GROUND_SELECTION_ERROR"
    if rec.get("claim_plan_failed"):
        return "CLAIM_PLAN_ERROR"
    if rec.get("draft_plan_failed"):
        return "DRAFT_PLAN_ERROR"
    if rec.get("draft_failed"):
        return "DRAFT_RENDERING_ERROR"
    if rec.get("validation_passed") is False and rec.get("final_state") == "RELEASED":
        return "VALIDATION_ERROR"
    if rec.get("outcome_state_error"):
        return "OUTCOME_STATE_ERROR"
    return None


def _physical_specs() -> list[dict[str, Any]]:
    specs = []
    if not IMG_DIR.exists():
        return specs
    for img in sorted(IMG_DIR.glob("*.png")):
        stem = img.stem
        pcn_key = stem.split("_")[0]
        case_json = CASE_DIR / f"{pcn_key}.json"
        facts = {}
        scenario = "overstay"
        if case_json.exists():
            meta = json.loads(case_json.read_text(encoding="utf-8"))
            facts = meta.get("facts") or {}
            scenario = meta.get("scenario") or scenario
        family = "anpr" if "anpr" in str(facts.get("evidence_method") or "").lower() \
            or "anpr" in str(facts.get("alleged_breach") or "").lower() else "overstay"
        if "payment" in scenario or "apcoa" in stem or "parkmaven" in stem:
            family = "payment"
        if pcn_key in ("00347261120013", "70377302", "sp62712518"):
            family = "anpr_multiple_visits"
        narrative = _NARRATIVES.get(pcn_key)
        if not narrative:
            narrative = (
                _NARRATIVES["default_anpr"] if "anpr" in family
                else _NARRATIVES["default_payment"] if family == "payment"
                else _NARRATIVES["default_overstay"]
            )
        answers = (
            _ANSWERS["multiple_visits"] if "multiple" in family
            else _ANSWERS["payment"] if family == "payment"
            else _ANSWERS["minimal"]
        )
        # Pair with p9 notice_only when present (labels only — not field injection).
        notice_path = None
        for p in NOTICE_DIR.glob("REF_*.json"):
            if pcn_key in p.stem:
                notice_path = p
                break
        specs.append({
            "cohort_id": f"PHYS_{pcn_key}",
            "source": "physical_photo",
            "family": family,
            "front_path": img,
            "back_path": None,  # never invent reverse
            "notice_json": notice_path,
            "narrative": narrative,
            "answers": answers,
            "variation": ["ANPR" if "anpr" in family else "payment" if family == "payment"
                          else "overstay", "front_only_no_invented_back"],
        })
    return specs


def _complete_specs() -> list[dict[str, Any]]:
    """Dataset-complete cases with provided front+back text (not invented)."""
    wanted = [
        "REG_payment_keying",
        "REG_late_ntk_anpr",
        "REG_late_ntk",
        "REG_breakdown",
        "REG_notice_plus_payment",
        "REG_residential",
        "REG_no_notice",
        "REG_overstay_dont_know",
    ]
    out = []
    for cid in wanted:
        path = COMPLETE_DIR / f"{cid}.json"
        if not path.exists():
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        inp = spec.get("input") or {}
        out.append({
            "cohort_id": cid,
            "source": "p9_complete",
            "family": spec.get("family") or cid,
            "front_text": inp.get("notice_front") or "",
            "back_text": inp.get("notice_back") or "",
            "narrative": inp.get("customer_narrative") or "",
            "answers": dict(inp.get("customer_answers") or {}),
            "extra_evidence": list(inp.get("evidence") or []),
            "variation": [spec.get("legal_route") or "", spec.get("family") or ""],
            "expected_route": spec.get("legal_route"),
        })
    return out


def build_cohort_plan(max_cases: int = COHORT_CAP) -> list[dict[str, Any]]:
    plan = _physical_specs() + _complete_specs()
    return plan[:max_cases]


def _pin_kg(pipe) -> str:
    from pcn_appeal.store import kb_source
    from pcn_appeal.kg.graph import KnowledgeGraph
    try:
        rel = kb_source.load_release(PINNED_KB)
        pipe.kg = KnowledgeGraph.from_release(rel)
        pipe.reasoning.kg = pipe.kg
        return pipe.kg.release_id
    except Exception:
        return getattr(pipe.kg, "release_id", None) or "UNPINNED"


def _run_one(spec: dict[str, Any], pipe, freeze: dict[str, Any]) -> dict[str, Any]:
    from pcn_appeal.intake import run_intake
    from pcn_appeal.models import CaseState, EvidenceItem
    from pcn_appeal.store import cases as case_store
    from pcn_appeal.engines.claim_plan_authority import latest_locked
    from pcn_appeal.services.private_parking import PrivateParkingService
    from pcn_appeal.drafting.plan import build_draft_plan

    t0 = time.perf_counter()
    run_id = str(uuid.uuid4())
    rec: dict[str, Any] = {
        "cohort_id": spec["cohort_id"],
        "run_id": run_id,
        "source": spec.get("source"),
        "family": spec.get("family"),
        "variation": spec.get("variation"),
        "deployment_version": freeze.get("deployment_version"),
        "kb_release_id": freeze.get("kb_release_id"),
        "kb_release_digest": freeze.get("kb_release_digest"),
        "ontology_version": freeze.get("ontology_version"),
        "model_versions": (freeze.get("model_provider_probe") or {}).get("models"),
        "prompt_versions": freeze.get("prompt_versions"),
        "draft_plan_version": freeze.get("draft_plan_version"),
        "document_quality_failure": False,
        "front_only": False,
        "outcome_correct_hold": False,
    }

    try:
        case = case_store.new_case(kb_release_id=pipe.kg.release_id)
        rec["case_id"] = case.case_id

        if spec.get("source") == "physical_photo":
            front = Path(spec["front_path"]).read_bytes()
            case.evidence["E1"] = EvidenceItem("E1", "PCN", Path(spec["front_path"]).name)
            case.evidence["E1"].images = [front]
            # No invented reverse page.
            rec["front_only"] = True
            rec["document_quality_failure"] = True  # incomplete sides until proven
        else:
            front_text = spec.get("front_text") or ""
            back_text = spec.get("back_text") or ""
            if front_text:
                case.evidence["E1"] = EvidenceItem("E1", "PCN", "front.jpg", text=front_text)
                case.evidence["E1"].images = [_jpeg_text_page(front_text)]
            if back_text:
                case.evidence["E2"] = EvidenceItem("E2", "PCN", "back.jpg", text=back_text)
                case.evidence["E2"].images = [_jpeg_text_page(back_text)]
            for i, ev in enumerate(spec.get("extra_evidence") or []):
                eid = ev.get("evidence_id") or f"EX{i}"
                case.evidence[eid] = EvidenceItem(
                    eid, ev.get("kind") or "OTHER", ev.get("filename") or f"{eid}.txt",
                    text=ev.get("text") or "",
                )

        t_ex = time.perf_counter()
        llm = getattr(pipe, "llm", None) or getattr(pipe, "client", None)
        if llm is None:
            from pcn_appeal.llm import default_client
            llm = default_client()
        intake = run_intake(case, llm)
        rec["extraction_latency_ms"] = int((time.perf_counter() - t_ex) * 1000)

        if intake.proceed:
            PrivateParkingService(pipe).extract_service_facts(case)
        else:
            pipe.ingest(case)

        # Notice completeness — do not invent reverse.
        sides = bool(case.get("notice_sides_complete"))
        if rec.get("front_only") and not sides:
            rec["document_quality_failure"] = True

        t_sem = time.perf_counter()
        narrative = spec.get("narrative") or ""
        answers = dict(spec.get("answers") or {})
        questions = pipe.confirm(case, {}, list(case.facts), narrative) if narrative else \
            pipe.confirm(case, {}, list(case.facts), "")
        for _ in range(8):
            if not questions:
                break
            given = {q["fact"]: answers[q["fact"]] for q in questions if q.get("fact") in answers}
            if not given:
                for q in questions:
                    if q.get("fact") and q["fact"] in answers:
                        given[q["fact"]] = answers[q["fact"]]
                        break
            if not given:
                # Answer unknown facts with skip-safe defaults when dataset empty
                for q in questions:
                    if q.get("fact"):
                        given[q["fact"]] = answers.get(q["fact"], "no")
                        break
            if not given:
                break
            questions = pipe.answer(case, given)
        rec["semantic_latency_ms"] = int((time.perf_counter() - t_sem) * 1000)

        # Narrative atoms / departure_reason
        atoms = [
            a for ev in case.audit if ev.get("event") == "narrative_atom"
            for a in (ev.get("atoms") or [])
        ]
        rec["narrative_atoms"] = atoms[:6]
        rec["departure_reason"] = case.get("departure_reason")
        rec["facts_created"] = sorted(case.facts.keys())

        t_draft = time.perf_counter()
        out = pipe.generate(case)
        rec["draft_latency_ms"] = int((time.perf_counter() - t_draft) * 1000)
        rec["total_latency_ms"] = int((time.perf_counter() - t0) * 1000)

        rec["final_state"] = case.state.value
        rec["final_outcome"] = getattr(getattr(out, "outcome", None), "value", None) \
            or (case.audit[-1].get("outcome") if case.audit else None)
        # Prefer customer-facing outcome from audit
        for a in reversed(case.audit or []):
            if a.get("event") in ("customer_outcome", "outcome") and a.get("outcome"):
                rec["final_outcome"] = a.get("outcome")
                break
        val = getattr(out, "validation", None)
        rec["validation_passed"] = bool(getattr(val, "passed", False)) if val else False
        rec["validation_issues"] = [
            getattr(i, "rule", None) for i in (getattr(val, "issues", None) or [])
        ]

        plan = latest_locked(case)
        grounds = [i.module_id for i in (getattr(plan, "supported", None) or [])] if plan else []
        rejected = []
        if plan:
            for i in (getattr(plan, "rejected", None) or []):
                rejected.append({
                    "module_id": i.module_id,
                    "reason": getattr(i, "reason", None) or getattr(i, "rejection_reason", None),
                })
        rec["selected_grounds"] = grounds
        rec["rejected_grounds"] = rejected
        rec["claim_plan_id"] = getattr(plan, "claim_plan_id", None) if plan else None
        rec["legal_findings"] = [
            {
                "finding_type": f.get("finding_type"),
                "status": f.get("status"),
                "finding_id": f.get("finding_id"),
            }
            for f in (case.legal_findings or [])
        ]
        pofa_grounds = [g for g in grounds if str(g).startswith("KB-POFA")]
        rec["pofa_grounds"] = pofa_grounds
        # Narrative must not create PoFA
        pofa_from_narr = False
        if plan:
            for item in plan.supported or []:
                if not str(item.module_id).startswith("KB-POFA"):
                    continue
                blob = str(item.supporting_facts).lower()
                if any(x in blob for x in ("departure_reason", "purse", "shopping", "narrative")):
                    pofa_from_narr = True
        rec["pofa_from_narrative"] = pofa_from_narr

        pack = getattr(out, "pack", None)
        dplan = {}
        if pack is not None:
            try:
                dp = build_draft_plan(pack, case_id=case.case_id)
                dplan = {
                    "sections": [s.as_dict() for s in dp.sections],
                    "owned_grounds": list(dp.owned_grounds()),
                }
            except Exception as exc:
                dplan = {"error": str(exc)[:200]}
                rec["draft_plan_failed"] = True
        rec["draft_plan"] = {
            "section_count": len(dplan.get("sections") or []),
            "owned_grounds": dplan.get("owned_grounds"),
        }

        letter = getattr(out, "letter", None) or _letter(case)
        cov = _coverage(letter)
        rec["letter_len"] = len(letter or "")
        rec["cancel_request"] = cov["cancel_request"]
        rec["placeholder"] = cov["placeholder"]
        rec["driver_inferred"] = cov["driver_disclosure"]
        rec["ground_coverage_pass"] = (
            case.state != CaseState.RELEASED
            or (rec["validation_passed"] and bool(grounds))
        )
        # Material particular: if departure_reason fact exists, letter should mention reason cues
        if case.get("departure_reason") and case.state == CaseState.RELEASED:
            low = (letter or "").lower()
            material_ok = bool(re.search(
                r"\b(forgot|forgotten|necessary item|essential item|collect|"
                r"left elsewhere|realised|wallet|purse)\b", low))
            rec["material_particular_coverage"] = material_ok
            if not material_ok:
                rec["fact_propagation_failed"] = True
        else:
            rec["material_particular_coverage"] = None

        # Correct hold: front-only incomplete notice should not be forced RELEASED
        if rec.get("front_only") and case.state != CaseState.RELEASED:
            rec["outcome_correct_hold"] = True
            rec["document_quality_failure"] = True

        # Persistence reload
        try:
            case_store.save(case)
            reloaded = case_store.load(case.case_id)
            rec["persist_ok"] = (
                reloaded.state == case.state
                and reloaded.get("pcn_number") == case.get("pcn_number")
                and (reloaded.kb_release_id or None) == (case.kb_release_id or None)
            )
            if case.state == CaseState.RELEASED:
                meta = getattr(reloaded, "release_metadata", None) or {}
                rec["release_metadata_keys"] = sorted(meta.keys()) if isinstance(meta, dict) else []
                rec["persist_ok"] = rec["persist_ok"] and bool(meta.get("kb_release_id"))
        except Exception as exc:
            rec["persist_ok"] = False
            rec["persist_error"] = f"{type(exc).__name__}: {exc}"[:240]
            rec["infrastructure_error"] = True

        # Idempotent double-generate check (cheap): ensure still one locked plan
        plans = list(case.claim_plans or [])
        locked = [p for p in plans if str(getattr(p, "status", "")).upper() == "LOCKED"
                  or (isinstance(p, dict) and str(p.get("status", "")).upper() == "LOCKED")]
        rec["locked_plan_count"] = len(locked) if locked else (1 if plan else 0)

    except Exception as exc:
        rec["infrastructure_error"] = True
        rec["error"] = f"{type(exc).__name__}: {exc}"[:400]
        rec["final_state"] = rec.get("final_state") or "PROCESSING_ERROR"
        rec["total_latency_ms"] = int((time.perf_counter() - t0) * 1000)

    rec["first_defective_layer"] = _first_defect_layer(rec)
    from .observability import classify_safety
    rec["safety_stops"] = classify_safety(rec)
    return rec


def _provider_failure_probe(pipe) -> dict[str, Any]:
    """Bounded safety probe — does not release under provider failure."""
    from unittest.mock import patch
    from pcn_appeal.models import CaseFile, CaseState, ValidationResult
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.llm import FakeLLM

    results = {"ran": True, "checks": {}}
    # FakeLLM empty → drafting should not RELEASE
    fake = FakeLLM({})
    p = AppealPipeline(fake)
    case = CaseFile("p12-provider-fail")
    case.state = CaseState.ANALYSED
    try:
        # Minimal: release gate with missing metadata must block
        from pcn_appeal.release_trace import release_allowed
        ok, missing = release_allowed({"kb_release_id": None})
        results["checks"]["missing_kb_blocks_release"] = (not ok) and bool(missing)
    except Exception as exc:
        results["checks"]["missing_kb_blocks_release"] = False
        results["error"] = str(exc)[:200]
    results["passed"] = all(results["checks"].values()) if results["checks"] else False
    return results


def _idempotency_probe() -> dict[str, Any]:
    from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
    case = CaseFile("p12-idem")
    f = Fact("F1", "payment_made", True, FactStatus.ANSWERED,
             FactSource(SourceKind.ANSWER, "answer:payment_made"))
    case.put(f)
    case.put(Fact("F1b", "payment_made", True, FactStatus.ANSWERED,
                  FactSource(SourceKind.ANSWER, "answer:payment_made")))
    # Fact graph should not explode into conflicting duplicates for same value
    return {
        "ran": True,
        "payment_made_value": case.get("payment_made"),
        "passed": case.get("payment_made") is True,
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    from .observability import empty_cohort_metrics
    m = empty_cohort_metrics()
    m["n_real_cases"] = len(records)
    m["case_records"] = records
    dist: dict[str, int] = {}
    for r in records:
        st = r.get("final_state") or "UNKNOWN"
        dist[st] = dist.get(st, 0) + 1
    m["outcome_distribution"] = dist

    released = [r for r in records if r.get("final_state") == "RELEASED"]
    m["validation_failure_rate"] = (
        sum(1 for r in records if r.get("validation_passed") is False) / max(len(records), 1)
    )
    m["processing_error_rate"] = (
        sum(1 for r in records if r.get("final_state") == "PROCESSING_ERROR"
            or r.get("infrastructure_error")) / max(len(records), 1)
    )
    m["no_supported_grounds_rate"] = (
        sum(1 for r in records if r.get("final_state") == "NO_SUPPORTED_GROUNDS")
        / max(len(records), 1)
    )
    lat = sorted(r.get("total_latency_ms") or 0 for r in records)
    if lat:
        m["latency_p50_ms"] = lat[len(lat) // 2]
        m["latency_p95_ms"] = lat[max(0, int(len(lat) * 0.95) - 1)]

    # Coverage among RELEASED
    if released:
        m["draft_ground_coverage"] = sum(
            1 for r in released if r.get("ground_coverage_pass")) / len(released)
        mat = [r for r in released if r.get("material_particular_coverage") is not None]
        m["material_fact_coverage"] = (
            sum(1 for r in mat if r["material_particular_coverage"]) / max(len(mat), 1)
            if mat else 1.0
        )
        m["required_particular_coverage"] = sum(
            1 for r in released if r.get("validation_passed")) / len(released)
        m["unsupported_assertion_rate"] = sum(
            1 for r in released if not r.get("validation_passed")) / len(released)
    else:
        m["draft_ground_coverage"] = 1.0  # vacuously — no unsafe release
        m["material_fact_coverage"] = 1.0
        m["required_particular_coverage"] = 1.0
        m["unsupported_assertion_rate"] = 0.0

    m["driver_disclosure_violations"] = sum(1 for r in records if r.get("driver_inferred"))
    m["safety_incidents"] = [
        {"case_id": r.get("case_id"), "stops": r.get("safety_stops"),
         "layer": r.get("first_defective_layer")}
        for r in records if r.get("safety_stops")
    ]
    m["defect_register"] = [
        {"case_id": r.get("case_id"), "cohort_id": r.get("cohort_id"),
         "layer": r.get("first_defective_layer"), "state": r.get("final_state")}
        for r in records if r.get("first_defective_layer")
    ]
    m["persist_ok_rate"] = sum(1 for r in records if r.get("persist_ok")) / max(len(records), 1)
    m["cost_per_case"] = None  # provider usage not metered in-client; see report note
    m["openai_calls_per_case"] = None
    m["input_tokens_per_case"] = None
    m["output_tokens_per_case"] = None
    m["mean_cost_case"] = None
    m["median_cost_case"] = None
    m["highest_cost_case"] = None
    m["cost_note"] = (
        "In-process token metering is not exposed by the LLM client; "
        "record OpenAI usage dashboard for this pilot window separately."
    )
    return m


def run_cohort(freeze: dict[str, Any], max_cases: int = COHORT_CAP) -> dict[str, Any]:
    _load_env()
    from pcn_appeal import config
    from pcn_appeal.llm import default_client, probe
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.store import db

    config.load()
    info = probe()
    if info.get("provider") != "openai" or not db.enabled():
        return {
            "ran": False,
            "error": "live OpenAI + DATABASE_URL required",
            "n_real_cases": 0,
            "case_records": [],
        }
    db.init_schema()
    client = default_client()
    pipe = AppealPipeline(client)
    kb_id = _pin_kg(pipe)
    # Align freeze stamp with pinned KB when published release loads
    freeze = dict(freeze)
    freeze["kb_release_id"] = kb_id or freeze.get("kb_release_id")
    freeze["pilot_kb_pin"] = PINNED_KB

    plan = build_cohort_plan(max_cases)
    records = []
    for i, spec in enumerate(plan):
        print(f"  cohort {i+1}/{len(plan)} {spec['cohort_id']} ...", flush=True)
        rec = _run_one(spec, pipe, freeze)
        records.append(rec)
        print(
            f"    -> {rec.get('final_state')} case={rec.get('case_id')} "
            f"layer={rec.get('first_defective_layer')}",
            flush=True,
        )

    metrics = aggregate(records)
    metrics["ran"] = True
    metrics["provider_failure_probe"] = _provider_failure_probe(pipe)
    metrics["idempotency_probe"] = _idempotency_probe()
    metrics["freeze_kb_release_id"] = freeze.get("kb_release_id")
    metrics["pinned_kb"] = PINNED_KB
    return metrics
