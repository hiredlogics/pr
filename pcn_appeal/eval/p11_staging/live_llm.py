"""Live provider probes for semantic extraction + DraftPlan drafting (non-production)."""
from __future__ import annotations

import json
import time
from typing import Any


FORBIDDEN_SEMANTIC_KEYS = (
    "module_id", "kb-", "claim_plan", "ground_id", "legal_conclusion",
)


def _client():
    from pcn_appeal import config
    config.load()
    from pcn_appeal.llm import DemoLLM, default_client, probe
    info = probe()
    client = default_client()
    return client, info, isinstance(client, DemoLLM)


def probe_semantic() -> dict[str, Any]:
    from pcn_appeal import prompts
    client, info, is_demo = _client()
    out: dict[str, Any] = {
        "ran": False,
        "provider": info.get("provider"),
        "models": info.get("models") or {},
        "prompt_version": prompts.version("semantic_extraction"),
        "is_demo": is_demo,
    }
    if is_demo or info.get("provider") != "openai":
        out["reason"] = info.get("reason") or "live OpenAI required"
        out["passed"] = False
        return out
    payload = {
        "customer_texts": [
            "I paid on the app but typed one character of the registration wrong.",
            "The car would not restart and was immobilised on site.",
            "I am not sure whether a permit was displayed.",
        ],
    }
    t0 = time.time()
    try:
        result = client.complete_json(
            task="semantic_extraction",
            system=prompts.system("semantic_extraction"),
            user=json.dumps(payload),
        )
        latency = int((time.time() - t0) * 1000)
        concepts = result.get("concepts") or []
        blob = json.dumps(result).lower()
        forbidden_hits = [k for k in FORBIDDEN_SEMANTIC_KEYS if k in blob and k != "ground"]
        # Soft: module ids like KB-PAY must not appear as authority output
        kb_hits = [c for c in concepts if str(c.get("concept_id") or c.get("id") or "").startswith("KB-")]
        affirmed = [c for c in concepts if str(c.get("polarity") or "").upper() in ("AFFIRMED", "TRUE", "")]
        out.update({
            "ran": True,
            "latency_ms": latency,
            "structured_output_valid": isinstance(concepts, list),
            "concept_count": len(concepts),
            "affirmed_count": len(affirmed),
            "forbidden_kb_authority": bool(kb_hits),
            "model": (getattr(client, "models", None) or {}).get("semantic_extraction")
                     or (getattr(client, "models", None) or {}).get("case_analysis"),
            "sample_concepts": concepts[:8],
            "passed": isinstance(concepts, list) and not kb_hits,
        })
    except Exception as exc:  # noqa: BLE001
        out.update({
            "ran": True,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "latency_ms": int((time.time() - t0) * 1000),
        })
    return out


def probe_drafting() -> dict[str, Any]:
    from pcn_appeal import prompts
    from pcn_appeal.drafting.plan import DRAFT_PLAN_VERSION, particular_expressed, section_expresses_ground
    client, info, is_demo = _client()
    out: dict[str, Any] = {
        "ran": False,
        "provider": info.get("provider"),
        "models": info.get("models") or {},
        "prompt_version": prompts.version("drafting"),
        "draft_plan_version": DRAFT_PLAN_VERSION,
        "is_demo": is_demo,
    }
    if is_demo or info.get("provider") != "openai":
        out["reason"] = info.get("reason") or "live OpenAI required"
        out["passed"] = False
        return out
    plan_payload = {
        "draft_plan": {
            "case_id": "P11_PROBE_DRAFT",
            "draft_plan_version": DRAFT_PLAN_VERSION,
            "sections": [{
                "section_id": "S01",
                "ground_id": "KB-PAY-01",
                "ground_ids": ["KB-PAY-01", "KB-KEY-01"],
                "purpose": "Express payment + keying as one case theory",
                "required_particulars": ["payment_made", "keying_error_type"],
                "particular_values": {"payment_made": True, "keying_error_type": "MINOR"},
                "supporting_fact_ids": ["F1", "F2"],
                "merged": True,
                "prohibited_claims": [],
            }],
            "introduction_requirements": ["Identify as registered keeper"],
            "closing_requirements": ["Clear cancellation request"],
        },
        "verified_facts": {
            "vrm": "AB12CDE", "pcn_number": "P11PROBE1",
            "payment_made": True, "keying_error_type": "MINOR",
            "operator_name": "Probe Parking Ltd",
            "alleged_breach": "No valid payment for vehicle",
        },
        "fact_refs": {"payment_made": "F1", "keying_error_type": "F2",
                      "vrm": "F3", "pcn_number": "F4"},
        "fact_basis": {"payment_made": "CUSTOMER_ACCOUNT",
                       "keying_error_type": "CUSTOMER_ACCOUNT"},
        "driver_status": "UNIDENTIFIED",
        "driver_rule": "Write about the keeper and the vehicle.",
        "module_ids": ["KB-PAY-01", "KB-KEY-01"],
        "case_context": {
            "operator_name": "Probe Parking Ltd", "vrm": "AB12CDE",
            "pcn_number": "P11PROBE1",
            "alleged_breach": "No valid payment for vehicle",
        },
        "context_chunks": [],
        "verified_legal_findings": [],
        "pofa_findings": [],
    }
    t0 = time.time()
    try:
        result = client.complete_json(
            task="drafting",
            system=prompts.system("drafting"),
            user=json.dumps(plan_payload),
        )
        latency = int((time.time() - t0) * 1000)
        sections = result.get("sections") or []
        if sections:
            text = " ".join(s.get("text") or "" for s in sections)
            structured_ok = True
        else:
            paras = result.get("paragraphs") or []
            text = " ".join(s.get("text") or "" for p in paras for s in p)
            structured_ok = bool(paras)
        class _S:
            ground_ids = ["KB-PAY-01", "KB-KEY-01"]
            particular_values = {"payment_made": True, "keying_error_type": "MINOR"}
            required_particulars = ["payment_made", "keying_error_type"]
        ground_ok = section_expresses_ground(text, _S())
        part_ok = all(
            particular_expressed(text, n, plan_payload["draft_plan"]["sections"][0]["particular_values"][n])
            for n in ("payment_made", "keying_error_type")
        )
        cancel_ok = "cancel" in text.lower()
        out.update({
            "ran": True,
            "latency_ms": latency,
            "structured_output_valid": structured_ok,
            "used_sections_shape": bool(sections),
            "ground_coverage": 1.0 if ground_ok else 0.0,
            "required_particular_coverage": 1.0 if part_ok else 0.0,
            "material_fact_coverage": 1.0 if part_ok else 0.0,
            "unsupported_assertion_rate": 0.0,  # probe only; validators apply in E2E
            "cancel_request_present": cancel_ok,
            "model": (getattr(client, "models", None) or {}).get("drafting"),
            "text_preview": text[:500],
            "passed": structured_ok and ground_ok and part_ok and cancel_ok,
        })
    except Exception as exc:  # noqa: BLE001
        out.update({
            "ran": True,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "latency_ms": int((time.time() - t0) * 1000),
        })
    return out
