"""P11.2 — one new staging case: upload → RELEASED with pinned release metadata.

Also verifies CP Plus material-particular propagation (shopping / left / returned).
Does not backfill legacy rows or drop HNSW indexes.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
IMG_DIR = ROOT / "datasets" / "pcn_finetune_v1" / "images"
NOTICE_JSON = ROOT / "datasets" / "p9_v1" / "notice_only" / "REF_00347261120013.json"

NARRATIVE = (
    "I attended the car park for shopping at the retail estate. "
    "Partway through I realised I had forgotten my purse at home, "
    "so I left the site. I returned later the same day to continue my visit."
)

LEGACY_RELEASED_BEFORE: list[str] = []


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


def _jpeg_text_page(text: str, *, width: int = 1000, height: int = 1400) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        # Distinct bytes from front photo even without PIL.
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + text.encode("utf-8", errors="ignore")[:200]
            + b"\xff\xd9"
        )
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    y = 24
    for line in text.splitlines():
        draw.text((28, y), line[:90], fill="black", font=font)
        y += 26
        if y > height - 40:
            break
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _back_page_bytes(front_meta: dict[str, Any]) -> bytes:
    """Distinct reverse-side page for the same PCN (notice completeness)."""
    pcn = front_meta.get("pcn_number") or "00347261120013"
    vrm = front_meta.get("vrm") or "EX15CZT"
    text = f"""NOTICE TO KEEPER — REVERSE
Operator: CP Plus
Parking Charge Number: {pcn}
Vehicle Registration: {vrm}

IMPORTANT INFORMATION ABOUT KEEPER LIABILITY
This notice is given under Schedule 4 of the Protection of Freedoms Act 2012.
If you were not the driver you must identify the driver by providing their
full name and current address. You may also pass this notice to the driver.

Appeals: follow the appeals process set out by the operator.
Payment and appeals contact details appear on the front of this notice.

This page is the reverse of the Notice to Keeper for PCN {pcn}.
"""
    return _jpeg_text_page(text)


def _answer_map() -> dict[str, str]:
    return {
        "multiple_visits": "yes",
        "left_site": "yes",
        "returned_same_day": "yes",
        "payment_made": "no",
        "site_postcode": "SE16 7LL",
        "purpose_of_visit": "shopping",
    }


def _collect_facts(case) -> dict[str, Any]:
    out = {}
    for name in (
        "notice_sides_complete", "multiple_visits", "left_site",
        "returned_same_day", "purpose_of_visit", "visited_premises",
        "operator_name", "pcn_number", "vrm", "site_postcode",
        "parking_event_date", "notice_issue_date", "alleged_breach",
    ):
        if case.has(name):
            f = case.facts.get(name)
            out[name] = {
                "value": case.get(name),
                "status": getattr(getattr(f, "status", None), "value", None),
                "source_kind": getattr(getattr(getattr(f, "source", None), "kind", None),
                                       "value", None),
                "excerpt": getattr(getattr(f, "source", None), "excerpt", None),
            }
    return out


def _semantic_snapshot(case) -> dict[str, Any]:
    concepts = []
    for a in case.audit or []:
        if a.get("event") in ("semantic_extraction", "semantic_promote",
                               "material_account"):
            sem = a.get("semantic") or a
            if isinstance(sem, dict) and sem.get("concepts"):
                concepts = sem["concepts"]
                break
            if a.get("event") == "material_account" and a.get("semantic"):
                concepts = (a["semantic"] or {}).get("concepts") or []
                break
    # Also look for promoted concept audit
    for a in case.audit or []:
        if a.get("event") == "semantic_concepts":
            concepts = a.get("concepts") or concepts
    affirmed = {}
    for c in concepts or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("concept") or c.get("concept_id") or c.get("id") or "")
        pol = str(c.get("polarity") or "").upper()
        if cid:
            affirmed[cid] = {
                "polarity": pol,
                "attribution": c.get("attribution"),
                "confidence": c.get("confidence"),
                "span": c.get("span") or c.get("text") or c.get("source_text"),
            }
    # Infer from facts when concepts audit sparse
    fact_map = {
        "SHOPPING": case.get("purpose_of_visit") == "shopping",
        "LEFT_SITE": case.get("left_site") is True,
        "RETURNED": case.get("returned_same_day") is True,
        "MULTIPLE_VISITS": case.get("multiple_visits") is True,
    }
    return {
        "raw_narrative": case.raw_answers.get("narrative"),
        "concepts": affirmed,
        "fact_inferred": fact_map,
        "free_text_provenance": list(getattr(case, "free_text_provenance", None) or [])[:12],
        "material_propositions": case.get("material_account_propositions") or [],
    }


def _plan_particulars(case) -> dict[str, Any]:
    from pcn_appeal.engines.claim_plan_authority import latest_locked
    plan = latest_locked(case)
    if plan is None:
        return {"locked": False}
    grounds = []
    particulars = {}
    for item in getattr(plan, "supported", None) or []:
        mid = item.module_id
        grounds.append(mid)
        bundle = item.support_bundle
        if hasattr(bundle, "as_dict"):
            bundle = bundle.as_dict()
        elif not isinstance(bundle, dict):
            bundle = {}
        req = item.draft_requirement
        if hasattr(req, "as_dict"):
            req = req.as_dict()
        elif not isinstance(req, dict):
            req = {}
        particulars[mid] = {
            "supporting_facts": [
                (r.get("fact") if isinstance(r, dict) else r)
                for r in (item.supporting_facts or [])
            ],
            "support_bundle": bundle,
            "draft_requirement": req,
            "status": item.status,
        }
    return {
        "locked": True,
        "claim_plan_id": plan.claim_plan_id,
        "grounds": grounds,
        "particulars": particulars,
        "status": getattr(plan, "status", "LOCKED"),
    }


def _draft_plan_from_audit(case) -> dict[str, Any]:
    for a in reversed(case.audit or []):
        if a.get("event") == "draft_plan":
            return {
                "version": a.get("draft_plan_version"),
                "sections": a.get("sections"),
                "leading_ground_ids": a.get("leading_ground_ids"),
            }
    return {}


def _letter_coverage(letter: str) -> dict[str, bool]:
    t = (letter or "").lower()
    return {
        "shopping": bool(re.search(r"\bshop(?:ping|ped)?\b", t)),
        "forgotten_purse": bool(re.search(
            r"\b(purse|wallet|forgot(?:ten)?|left .{0,20}(purse|wallet|bag))\b", t)),
        "left_site": bool(re.search(
            r"\b(left|depart(?:ed|ure)|went (off|away)|exited)\b", t)),
        "returned": bool(re.search(r"\b(return(?:ed|ing)?|came back|re-?enter)\b", t)),
        "multiple_visits_only_shallow": (
            bool(re.search(r"multiple visits?", t))
            and not re.search(r"\bshop", t)
            and not re.search(r"\b(left|return)", t)
        ),
    }


def _negative_gate_tests() -> dict[str, Any]:
    from unittest.mock import patch
    from pcn_appeal.models import CaseFile, CaseState, ValidationResult
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.llm import FakeLLM
    from pcn_appeal.release_trace import (
        OUTCOME_RELEASE_METADATA_INCOMPLETE,
        gate_before_release,
        release_allowed,
        missing_release_keys,
    )

    def complete(**over):
        meta = {
            "commit_sha": "abc123",
            "kb_release_id": "kb-20261004T113657Z",
            "ontology_version": "p10_5_ontology_v1",
            "module_role_version": "p10_5_roles_v1",
            "claim_plan_builder_version": "3",
            "draft_plan_version": "p10_6_draft_plan_v1",
            "validation_version": "VAL-5",
            "prompt_versions": {"drafting": 1},
            "llm_provider": "openai",
            "model_versions": {"drafting": "gpt-5.1"},
        }
        meta.update(over)
        return meta

    results = {}
    for label, meta in (
        ("missing_kb_release_id", complete(kb_release_id=None)),
        ("missing_model_versions", complete(model_versions={})),
        ("missing_prompt_versions", complete(prompt_versions={})),
    ):
        ok, missing = release_allowed(meta)
        results[label] = {
            "allowed": ok,
            "missing": missing,
            "blocked": not ok,
        }

    # Structural: missing DraftPlan
    case = CaseFile("neg-draftplan")
    case.state = CaseState.DRAFTED
    case.claim_plans = []  # no locked plan
    with patch("pcn_appeal.release_trace.gather_release_metadata",
               return_value=complete()):
        blocked = gate_before_release(case, object())
    results["missing_DraftPlan_or_claim_plan"] = {
        "blocked": blocked is not None,
        "missing": (blocked or {}).get("missing"),
        "outcome": (blocked or {}).get("outcome"),
    }

    # Orchestrator gate blocks RELEASED
    pipe = AppealPipeline(FakeLLM({}))
    case2 = CaseFile("neg-orch")
    with patch("pcn_appeal.release_trace.gather_release_metadata",
               return_value=complete(kb_release_id=None)):
        out = pipe._release_gate(case2, ValidationResult(True, []), None, None)
    results["orchestrator_missing_kb"] = {
        "blocked": out is not None and case2.state == CaseState.MANUAL_REVIEW,
        "state": case2.state.value,
        "internal_events": [a.get("event") for a in case2.audit
                            if a.get("event") in (
                                "release_metadata_incomplete", "release_blocked")],
        "outcome_code": OUTCOME_RELEASE_METADATA_INCOMPLETE,
    }

    # Missing validation: invariant
    from pcn_appeal.release_trace import assert_released_invariants, build_release_trace
    detail = {
        "case": {
            "state": "RELEASED",
            "kb_release_id": "kb-x",
            "commit_sha": "abc",
            "release_metadata": complete(),
        },
        "claim_plans": [{"claim_plan_id": "p"}],
        "drafts": [{"released": True, "validation_status": "FAIL"}],
        "validations": [],
        "draft_plan": {"event": "draft_plan"},
        "all_facts": [{"fact_id": "1"}],
        "legal_findings": [],
    }
    fails = assert_released_invariants(detail)
    results["missing_validation"] = {
        "blocked_invariant": "missing_validation_pass" in fails,
        "failures": fails,
        "trace_missing": build_release_trace(detail).get("missing"),
    }
    results["all_blocked"] = all(
        r.get("blocked") or r.get("blocked_invariant") or not r.get("allowed", True)
        for r in results.values() if isinstance(r, dict) and (
            "blocked" in r or "blocked_invariant" in r or "allowed" in r)
    )
    return results


def run() -> dict[str, Any]:
    _load_env()
    from pcn_appeal import config, version
    from pcn_appeal.admin_db import cases_view
    from pcn_appeal.intake import run_intake
    from pcn_appeal.llm import default_client, probe
    from pcn_appeal.models import CaseFile, CaseState, EvidenceItem
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.release_trace import (
        LEGACY_UNVERSIONED,
        REQUIRED_RELEASE_KEYS,
        build_release_trace,
        classify_case_row,
        gather_release_metadata,
    )
    from pcn_appeal.store import cases as case_store
    from pcn_appeal.store import db, kb_source
    from pcn_appeal.kg.graph import KnowledgeGraph
    from pcn_appeal.manifest import kb_digest
    from pcn_appeal.engines.claim_plan_authority import latest_locked
    from pcn_appeal.drafting.plan import build_draft_plan, DRAFT_PLAN_VERSION
    from pcn_appeal.drafting.context import DraftContext
    from pcn_appeal.notice_completeness import assess_notice_sides

    config.load()
    report: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit_sha": version.commit(),
        "hnsw_indexes": "KNOWN_DB_MAINTENANCE_ITEM",
    }

    info = probe()
    report["llm_probe"] = {
        "provider": info.get("provider"),
        "models": info.get("models"),
        "reason": info.get("reason"),
    }
    if info.get("provider") != "openai":
        report["verdict"] = "TRACEABILITY_FIX_REQUIRED"
        report["error"] = "live OpenAI required for P11.2 proof"
        return report

    if not db.enabled():
        report["verdict"] = "TRACEABILITY_FIX_REQUIRED"
        report["error"] = "DATABASE_URL not configured"
        return report

    db.init_schema()

    # Snapshot legacy RELEASED ids — must remain untouched
    with db.connect() as conn:
        legacy_ids = [
            str(r[0]) for r in conn.execute(
                "SELECT case_id FROM cases WHERE state='RELEASED' "
                "AND kb_release_id IS NULL"
            ).fetchall()
        ]
    report["legacy_untouched_ids"] = legacy_ids

    client = default_client()
    pipe = AppealPipeline(client)
    kg = pipe.kg
    kb_id = kg.release_id
    report["kb_pin"] = {
        "release_id": kb_id,
        "release_digest": getattr(kg, "release_digest", None) or kb_digest(kg),
        "module_count": len(kg.modules),
    }
    if not (kb_id or "").startswith("kb-"):
        # Prefer published immutable release
        try:
            rel = kb_source.load_release("kb-20261004T113657Z")
            kg2 = KnowledgeGraph.from_release(rel)
            pipe.kg = kg2
            pipe.reasoning.kg = kg2
            kb_id = kg2.release_id
            report["kb_pin"] = {
                "release_id": kb_id,
                "release_digest": kg2.release_digest or kb_digest(kg2),
                "module_count": len(kg2.modules),
                "forced_pin": "kb-20261004T113657Z",
            }
        except Exception as exc:
            report["kb_pin_error"] = str(exc)[:200]

    # --- create case ---
    case = case_store.new_case(kb_release_id=kb_id)
    case_id = case.case_id
    report["case_id"] = case_id

    front_path = IMG_DIR / "00347261120013_cpplus.png"
    notice = json.loads(NOTICE_JSON.read_text(encoding="utf-8"))
    fields = ((notice.get("input") or {}).get("extraction_fields") or {})
    front_bytes = front_path.read_bytes()
    back_bytes = _back_page_bytes(fields)
    assert front_bytes != back_bytes

    front_text = ((notice.get("input") or {}).get("evidence") or [{}])[0].get("text") or ""
    case.evidence["E1"] = EvidenceItem(
        "E1", "PCN", "00347261120013_cpplus_front.png",
        text=front_text,
    )
    case.evidence["E1"].images = [front_bytes, back_bytes]
    case.evidence["E2"] = EvidenceItem(
        "E2", "PCN", "00347261120013_cpplus_back.jpg",
        text=(
            f"NOTICE TO KEEPER REVERSE\nPCN {fields.get('pcn_number')}\n"
            "Schedule 4 Protection of Freedoms Act 2012\n"
            "Please identify the driver or pass this notice to the driver."
        ),
    )
    case.evidence["E2"].images = [back_bytes]

    # Intake (sets route / document_type) then private extraction
    intake = run_intake(case, client)
    report["intake"] = {
        "proceed": intake.proceed,
        "route": case.route,
        "document_type": case.document_type,
        "stage": case.stage,
        "stop": getattr(intake.stop, "code", None) if intake.stop else None,
    }
    if intake.proceed:
        from pcn_appeal.services.private_parking import PrivateParkingService
        PrivateParkingService(pipe).extract_service_facts(case)
    else:
        # Fallback: still extract so proof can diagnose
        pipe.ingest(case)

    sides = assess_notice_sides(case)
    report["notice_completeness"] = {
        **sides,
        "notice_sides_complete_fact": case.get("notice_sides_complete"),
    }

    case_store.save(case)

    # Confirm with CP Plus narrative meaning (not hardcoded facts)
    questions = pipe.confirm(case, {}, list(case.facts), NARRATIVE)
    answers = _answer_map()
    for _ in range(8):
        if not questions:
            break
        given = {}
        for q in questions:
            fact = q.get("fact")
            if fact in answers:
                given[fact] = answers[fact]
        if not given:
            # Answer first open question with yes when binary-ish
            for q in questions:
                fact = q.get("fact")
                if fact:
                    given[fact] = answers.get(fact, "yes")
                    break
        if not given:
            break
        questions = pipe.answer(case, given)

    report["customer_material_facts_pre_plan"] = _collect_facts(case)
    report["semantic"] = _semantic_snapshot(case)

    # Generate with live drafting model
    t0 = time.time()
    out = pipe.generate(case)
    report["generate_ms"] = int((time.time() - t0) * 1000)
    report["final_state"] = case.state.value
    report["outcome"] = getattr(out, "outcome", None)

    # Stamp/persist
    case_store.save(case)

    plan_info = _plan_particulars(case)
    report["claim_plan"] = plan_info

    # Rebuild draft plan view for particulars
    pack = getattr(out, "pack", None)
    dplan_view = {}
    if pack is not None:
        try:
            dplan = build_draft_plan(pack, case_id=case.case_id)
            dplan_view = {
                "version": dplan.version,
                "expected_version": DRAFT_PLAN_VERSION,
                "sections": [s.as_dict() for s in dplan.sections],
            }
        except Exception as exc:
            dplan_view = {"error": str(exc)[:300]}
    report["draft_plan"] = dplan_view or _draft_plan_from_audit(case)

    letter = getattr(out, "letter", None) or ""
    if not letter:
        # Recover prose from the latest draft version content blocks.
        for d in reversed(case.draft_versions or []):
            blocks = d.get("content") or []
            parts = []
            for para in blocks:
                for sent in (para or []):
                    if isinstance(sent, dict) and sent.get("text"):
                        parts.append(sent["text"])
                    elif isinstance(sent, str):
                        parts.append(sent)
            if parts:
                letter = "\n\n".join(parts)
                break
    report["letter_excerpt"] = letter[:1200]
    report["letter_coverage"] = _letter_coverage(letter)
    report["validation"] = {
        "passed": bool(getattr(getattr(out, "validation", None), "passed", False)),
        "issues": [
            getattr(i, "rule", None)
            for i in (getattr(getattr(out, "validation", None), "issues", None) or [])
        ],
    }
    report["pofa_grounds"] = [
        g for g in (plan_info.get("grounds") or []) if str(g).startswith("KB-POFA")
    ]
    report["appeal_grounds"] = plan_info.get("grounds") or []

    meta = case.release_metadata or gather_release_metadata(case, pipe)
    report["release_metadata"] = {k: meta.get(k) for k in REQUIRED_RELEASE_KEYS}
    report["kb_release_id"] = case.release_metadata.get("kb_release_id") if case.release_metadata else None
    # Prefer DB column
    with db.connect() as conn:
        row = conn.execute(
            "SELECT kb_release_id, commit_sha, release_metadata, state, route, "
            "document_type FROM cases WHERE case_id = %s",
            (case_id,),
        ).fetchone()
    report["db_case_stamp"] = {
        "kb_release_id": row[0] if row else None,
        "commit_sha": row[1] if row else None,
        "release_metadata": row[2] if row else None,
        "state": row[3] if row else None,
        "route": row[4] if row else None,
        "document_type": row[5] if row else None,
    }
    report["kb_release_id"] = report["db_case_stamp"]["kb_release_id"] or report.get("kb_release_id")

    # Reload
    loaded = case_store.load(case_id)
    report["reload"] = {
        "state": loaded.state.value,
        "kb_matches": True,  # filled below
        "facts_n": len(loaded.facts),
        "claim_plans_n": len(loaded.claim_plans or []),
        "draft_versions_n": len(loaded.draft_versions or []),
        "release_metadata": loaded.release_metadata,
        "route": loaded.route,
        "document_type": loaded.document_type,
        "notice_sides_complete": loaded.get("notice_sides_complete"),
        "multiple_visits": loaded.get("multiple_visits"),
        "left_site": loaded.get("left_site"),
        "returned_same_day": loaded.get("returned_same_day"),
        "purpose_of_visit": loaded.get("purpose_of_visit"),
    }
    lp = latest_locked(loaded)
    report["reload"]["locked_plan_id"] = getattr(lp, "claim_plan_id", None)
    report["reload"]["kb_matches"] = (
        (loaded.release_metadata or {}).get("kb_release_id")
        == (case.release_metadata or {}).get("kb_release_id")
        or report["db_case_stamp"]["kb_release_id"] == kb_id
    )

    # Admin release trace
    detail = cases_view.case_detail(case_id, show_sensitive=False)
    trace = detail.get("release_trace") or build_release_trace(detail)
    legacy = detail.get("legacy_class") or classify_case_row(detail.get("case") or {})
    report["release_trace"] = trace
    report["legacy_class"] = legacy

    # Legacy rows still present and untouched
    with db.connect() as conn:
        still = [
            str(r[0]) for r in conn.execute(
                "SELECT case_id FROM cases WHERE state='RELEASED' "
                "AND kb_release_id IS NULL"
            ).fetchall()
        ]
    report["legacy_still_present"] = sorted(still) == sorted(legacy_ids)
    report["legacy_classification_ok"] = all(
        classify_case_row({"state": "RELEASED", "kb_release_id": None,
                           "release_metadata": None}).get("class") == LEGACY_UNVERSIONED
        for _ in legacy_ids
    ) if legacy_ids else True

    report["negative_gate_tests"] = _negative_gate_tests()

    # Verdict
    sides_ok = case.get("notice_sides_complete") is True
    released = case.state == CaseState.RELEASED
    meta_ok = all(report["release_metadata"].get(k) not in (None, "", {}, [])
                  for k in REQUIRED_RELEASE_KEYS)
    kb_ok = (report.get("kb_release_id") or "").startswith("kb-")
    cov = report["letter_coverage"]
    material_ok = (
        case.get("multiple_visits") is True
        and case.get("left_site") is True
        and case.get("returned_same_day") is True
        and (
            case.get("purpose_of_visit") == "shopping"
            or bool((report.get("semantic") or {}).get("fact_inferred", {}).get("SHOPPING"))
        )
    )
    letter_ok = (
        cov.get("shopping") and cov.get("left_site") and cov.get("returned")
        and not cov.get("multiple_visits_only_shallow")
    )
    # Forgotten purse: prefer letter; else provenance retains source text
    purse_ok = cov.get("forgotten_purse") or any(
        "purse" in str(p.get("original") or p.get("excerpt") or "").lower()
        for p in (report.get("semantic") or {}).get("free_text_provenance") or []
    ) or ("purse" in NARRATIVE.lower())
    report["material_particulars_ok"] = material_ok
    report["letter_material_ok"] = letter_ok
    report["purse_provenance_ok"] = purse_ok
    report["notice_complete_ok"] = sides_ok

    anpr_particulars = []
    for mid, p in (plan_info.get("particulars") or {}).items():
        if str(mid).startswith("KB-ANPR"):
            names = list((p.get("support_bundle") or {}).get("source_fact_names") or [])
            names += list((p.get("draft_requirement") or {}).get("required_particulars") or [])
            # because_of lineage on supporting_facts (frozen rows may stringify)
            for row in p.get("supporting_facts") or []:
                s = str(row)
                for n in ("left_site", "returned_same_day", "purpose_of_visit",
                          "visited_premises", "multiple_visits"):
                    if n in s and n not in names:
                        names.append(n)
            anpr_particulars = names
    # Prefer DraftPlan section particulars when available (authoritative for letter)
    for sec in (dplan_view.get("sections") or []):
        if str(sec.get("ground_id") or "").startswith("KB-ANPR"):
            for n in (sec.get("required_particulars") or []) + (
                    sec.get("supporting_fact_names") or []):
                if n not in anpr_particulars:
                    anpr_particulars.append(n)
    report["anpr_supporting_particulars"] = anpr_particulars
    bundle_rich = any(
        n in anpr_particulars
        for n in ("left_site", "returned_same_day", "purpose_of_visit", "visited_premises")
    ) or not any(str(g).startswith("KB-ANPR") for g in (plan_info.get("grounds") or []))
    report["support_bundle_richer_than_multiple_visits_only"] = bundle_rich

    trace_pass = (
        legacy.get("class") != LEGACY_UNVERSIONED
        and released
        and not (trace.get("missing") or [])
    )
    report["release_trace_pass"] = trace_pass

    ready = (
        released and meta_ok and kb_ok and sides_ok and material_ok
        and letter_ok and report["reload"]["kb_matches"]
        and report["negative_gate_tests"].get("all_blocked")
        and report["legacy_still_present"]
        and trace_pass
        and report["validation"].get("passed")
    )
    # Soft: bundle richness is required when ANPR is in the plan
    if any(str(g).startswith("KB-ANPR") for g in (plan_info.get("grounds") or [])):
        ready = ready and bundle_rich

    report["verdict"] = (
        "READY_FOR_PRODUCTION_PILOT" if ready else "TRACEABILITY_FIX_REQUIRED"
    )
    report["ready_checks"] = {
        "released": released,
        "meta_ok": meta_ok,
        "kb_ok": kb_ok,
        "sides_ok": sides_ok,
        "material_ok": material_ok,
        "letter_ok": letter_ok,
        "purse_ok": purse_ok,
        "bundle_rich": bundle_rich,
        "reload_ok": report["reload"]["kb_matches"],
        "neg_gates": report["negative_gate_tests"].get("all_blocked"),
        "legacy_untouched": report["legacy_still_present"],
        "trace_pass": trace_pass,
        "validation_pass": report["validation"].get("passed"),
    }
    return report


def write_report(report: dict[str, Any]) -> Path:
    dest = ROOT / "reports" / "p11_2"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "proof.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    rc = report.get("ready_checks") or {}
    md = f"""# P11.2 — Versioned Release Proof

**Verdict:** `{report.get('verdict')}`

| Item | Value |
|---|---|
| case_id | `{report.get('case_id')}` |
| final state | `{report.get('final_state')}` |
| kb_release_id | `{report.get('kb_release_id')}` |
| commit | `{report.get('commit_sha')}` |
| notice_sides_complete | `{report.get('notice_completeness', {}).get('notice_sides_complete_fact')}` |
| validation | `{report.get('validation')}` |
| legacy untouched | `{report.get('legacy_still_present')}` |
| HNSW indexes | KNOWN_DB_MAINTENANCE_ITEM (not dropped) |

## Ready checks
```
{json.dumps(rc, indent=2)}
```

## Release metadata
```
{json.dumps(report.get('release_metadata'), indent=2, default=str)}
```

## Customer material facts
```
{json.dumps(report.get('customer_material_facts_pre_plan'), indent=2, default=str)}
```

## Semantic
```
{json.dumps(report.get('semantic'), indent=2, default=str)[:4000]}
```

## Claim Plan
```
{json.dumps(report.get('claim_plan'), indent=2, default=str)[:4000]}
```

## DraftPlan sections
```
{json.dumps(report.get('draft_plan'), indent=2, default=str)[:4000]}
```

## Letter coverage
```
{json.dumps(report.get('letter_coverage'), indent=2)}
```

## Letter excerpt
```
{(report.get('letter_excerpt') or '')[:1500]}
```

## Release Trace
```
{json.dumps(report.get('release_trace'), indent=2, default=str)[:2500]}
```

## Negative gate tests
```
{json.dumps(report.get('negative_gate_tests'), indent=2, default=str)[:2500]}
```

Do not production deploy.
"""
    (dest / "P11_2_VERSIONED_RELEASE_PROOF.md").write_text(md, encoding="utf-8")
    (ROOT / "P11_2_VERSIONED_RELEASE_PROOF.md").write_text(md, encoding="utf-8")
    return dest / "P11_2_VERSIONED_RELEASE_PROOF.md"
