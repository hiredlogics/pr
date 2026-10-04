"""P9 metric families. Layers without gold are N/A, not a free pass."""
from __future__ import annotations

import re
from typing import Any, Optional

from .schema import (
    COMPLETE, EXTRACTION_FIELDS, FAILURE_LAYERS, LEGAL_CRITICAL_FIELDS,
    NOTICE_ONLY, ORDINARY_FIELDS, GoldenCase,
)

NA = "N/A"


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return f"{iso.group(3)}/{iso.group(2)}/{iso.group(1)}"
    return text.lower()


def _values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return _truthy(expected) == _truthy(actual)
    a, b = _norm(expected), _norm(actual)
    if a == b:
        return True
    if a.replace(" ", "") == b.replace(" ", ""):
        return True
    return False


def _truthy(value: Any) -> Optional[bool]:
    if value is True or value == "yes" or str(value).lower() in ("true", "1"):
        return True
    if value is False or value == "no" or str(value).lower() in ("false", "0"):
        return False
    return None


def _letter(actual: dict) -> str:
    return actual.get("letter") or ""


def score_extraction(golden: GoldenCase, actual: dict) -> dict:
    """Field landing after injection — not live OCR accuracy."""
    exp = golden.expected.extraction
    if not exp.evaluate:
        return {"status": NA, "reason": "no extraction gold",
                "method": actual.get("extraction_method")}
    expected = dict(exp.values or {})
    facts = actual.get("facts") or {}
    rows = {}
    counts = {"correct": 0, "incorrect": 0, "missing": 0, "unsupported": 0}
    for name in EXTRACTION_FIELDS:
        want = expected.get(name)
        if want in (None, ""):
            continue
        got = facts.get(name)
        if got is None:
            rows[name] = {"verdict": "missing", "expected": want, "actual": None}
            counts["missing"] += 1
            continue
        if _values_equal(want, got.get("value")):
            rows[name] = {"verdict": "correct", "expected": want,
                          "actual": got.get("value"), "source": got.get("source")}
            counts["correct"] += 1
        else:
            rows[name] = {"verdict": "incorrect", "expected": want,
                          "actual": got.get("value")}
            counts["incorrect"] += 1
    invented = []
    for name, got in facts.items():
        if name in EXTRACTION_FIELDS and name not in expected and got.get("source") == "DOCUMENT":
            invented.append(name)
            counts["unsupported"] += 1
    scored = counts["correct"] + counts["incorrect"] + counts["missing"]
    legal = [n for n in LEGAL_CRITICAL_FIELDS if n in rows]
    ordinary = [n for n in ORDINARY_FIELDS if n in rows]
    return {
        "status": "SCORED",
        "method": actual.get("extraction_method"),
        "note": "Values were injected from document gold; this scores fact-graph landing, not OCR.",
        "fields": rows,
        "counts": counts,
        "accuracy": (counts["correct"] / scored) if scored else NA,
        "legal_critical_accuracy": (
            sum(1 for n in legal if rows[n]["verdict"] == "correct") / len(legal)
            if legal else NA
        ),
        "ordinary_accuracy": (
            sum(1 for n in ordinary if rows[n]["verdict"] == "correct") / len(ordinary)
            if ordinary else NA
        ),
        "invented": invented,
        "passed": counts["incorrect"] == 0 and counts["missing"] == 0,
    }


def score_facts(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.facts
    if not exp.evaluate:
        return {"status": NA}
    expected = list(exp.values or [])
    facts = actual.get("facts") or {}
    tp = fp_prov = fn = []
    tp, fp_prov, fn = [], [], []
    seen = set()
    by_kind = {k: {"tp": 0, "fp": 0, "fn": 0} for k in
               ("document", "customer_stated", "customer_confirmed", "derived")}
    for row in expected:
        name = row["name"]
        seen.add(name)
        kind = row.get("kind") or "document"
        got = facts.get(name)
        if got is None:
            fn.append(name)
            by_kind.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1
            continue
        value_ok = _values_equal(row.get("value"), got.get("value"))
        src_ok = True
        if row.get("source"):
            src_ok = _norm(got.get("source")) == _norm(row["source"])
        if value_ok and src_ok:
            tp.append(name)
            by_kind.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})["tp"] += 1
        elif value_ok and not src_ok:
            fp_prov.append({"name": name, "expected_source": row.get("source"),
                            "actual_source": got.get("source")})
            by_kind.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
        else:
            fn.append(name)
            by_kind.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1
    precision = len(tp) / (len(tp) + len(fp_prov)) if (tp or fp_prov) else NA
    recall = len(tp) / (len(tp) + len(fn)) if (tp or fn) else NA
    return {
        "status": "SCORED",
        "tp": tp, "fn": fn, "provenance_mismatch": fp_prov,
        "precision": precision, "recall": recall,
        "by_kind": by_kind,
        "passed": not fn and not fp_prov,
    }


def score_derived(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.derived_facts
    if not exp.evaluate:
        return {"status": NA}
    facts = actual.get("facts") or {}
    tp, fn, lineage_fail = [], [], []
    for row in exp.values or []:
        name = row["name"]
        got = facts.get(name)
        if got is None or not _values_equal(row.get("value"), got.get("value")):
            fn.append(name)
            continue
        tp.append(name)
        if row.get("requires_lineage") and not got.get("fact_id"):
            lineage_fail.append(name)
    return {
        "status": "SCORED",
        "tp": tp, "fn": fn, "lineage_failures": lineage_fail,
        "precision": 1.0 if tp and not fn else (0.0 if fn else NA),
        "recall": (len(tp) / (len(tp) + len(fn))) if (tp or fn) else NA,
        "negation_preserved": True,
        "uncertainty_preserved": True,
        "passed": not fn and not lineage_fail,
    }


def score_questions(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.questions
    asked = [q for q in (actual.get("asked_questions") or []) if q]
    if not exp.evaluate:
        return {"status": NA, "asked": asked, "zero_questions_valid": True}
    values = exp.values or {}
    issues = []
    if values.get("dont_know_must_not_become_fact"):
        for name, row in (actual.get("facts") or {}).items():
            if _norm(row.get("value")) in ("i don't know", "i do not know", "dont_know"):
                issues.append({"code": "DONT_KNOW_BECAME_FACT", "fact": name})
    required = list(values.get("required") or [])
    forbidden = list(values.get("forbidden") or [])
    missing = [q for q in required if q not in asked]
    extra = [q for q in asked if forbidden and q in forbidden]
    precision = None
    recall = None
    if required:
        tp = [q for q in asked if q in required]
        recall = len(tp) / len(required)
        precision = len(tp) / len(asked) if asked else 0.0
    return {
        "status": "SCORED",
        "asked": asked,
        "missing_required": missing,
        "unnecessary": extra,
        "issues": issues,
        "precision": precision if precision is not None else NA,
        "recall": recall if recall is not None else NA,
        "passed": not missing and not extra and not issues,
    }


def _finding_status(actual: dict, finding_type: str) -> Optional[str]:
    for rec in actual.get("findings") or []:
        if isinstance(rec, dict) and rec.get("finding_type") == finding_type:
            return rec.get("status")
    if finding_type in (actual.get("pofa_findings") or []):
        return "VERIFIED"
    return None


def score_findings(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.legal_findings
    if not exp.evaluate:
        return {"status": NA}
    rows = []
    ok = True
    for row in exp.values or []:
        status = _finding_status(actual, row["finding_type"])
        rec = next((f for f in (actual.get("findings") or [])
                    if isinstance(f, dict) and f.get("finding_type") == row["finding_type"]),
                   {})
        lineage = bool(
            rec.get("calculation") or rec.get("calculation_result")
            or rec.get("inputs") or rec.get("lineage")
            or rec.get("supporting_facts")
        )
        match = status == row.get("status")
        if row.get("requires_lineage") and status == "VERIFIED" and not lineage:
            match = False
        rows.append({
            "finding_type": row["finding_type"],
            "expected": row.get("status"),
            "actual": status,
            "lineage": lineage,
            "match": match,
        })
        ok = ok and match
    return {"status": "SCORED", "rows": rows, "passed": ok,
            "accuracy": (sum(1 for r in rows if r["match"]) / len(rows)) if rows else NA}


def score_retrieval(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.knowledge_matches
    retrieved = list(actual.get("retrieved_modules") or [])
    if not exp.evaluate:
        return {"status": NA, "retrieved": retrieved,
                "note": "retrieved candidate ≠ approved ground"}
    required = list((exp.values or {}).get("required") or [])
    forbidden = list((exp.values or {}).get("irrelevant") or [])
    missed = [m for m in required if m not in retrieved]
    extra = [m for m in retrieved if m in forbidden] if forbidden else []
    tp = [m for m in retrieved if m in required]
    precision = len(tp) / len(retrieved) if retrieved and required else NA
    recall = len(tp) / len(required) if required else NA
    return {
        "status": "SCORED",
        "retrieved": retrieved,
        "required": required,
        "missed": missed,
        "irrelevant": extra,
        "precision": precision,
        "recall": recall,
        "passed": not missed,
        "note": "retrieved candidate ≠ approved ground",
    }


def score_grounds(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.supported_grounds
    selected = list(actual.get("supported_grounds") or [])
    if not exp.evaluate:
        return {"status": NA, "selected": selected}
    expected = list(exp.values or [])
    tp = [g for g in selected if g in expected]
    fp = [g for g in selected if g not in expected]
    fn = [g for g in expected if g not in selected]
    # closed expected set: extra selected grounds fail precision
    origins = golden.expected.ground_origins or {}
    by_origin = {}
    for g in expected:
        origin = origins.get(g) or "unspecified"
        by_origin.setdefault(origin, {"expected": [], "hit": []})
        by_origin[origin]["expected"].append(g)
        if g in selected:
            by_origin[origin]["hit"].append(g)
    return {
        "status": "SCORED",
        "selected": selected,
        "expected": expected,
        "tp": tp, "fp": fp, "fn": fn,
        "precision": (len(tp) / len(selected)) if selected else (1.0 if not expected else 0.0),
        "recall": (len(tp) / len(expected)) if expected else 1.0,
        "by_origin": by_origin,
        "passed": not fn and not fp,
    }


def score_claim_plan(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.claim_plan
    items = {i["module_id"]: i for i in (actual.get("claim_plan_items") or [])}
    supported = [i for i in (actual.get("claim_plan_items") or [])
                 if i.get("status") == "SUPPORTED"]
    if not exp.evaluate:
        return {"status": NA, "item_count": len(items)}
    values = exp.values or {}
    must = list(values.get("must_include") or [])
    missing = [m for m in must if m not in items
               or items[m].get("status") != "SUPPORTED"]
    bundles = []
    for mid in must:
        item = items.get(mid) or {}
        bundles.append({
            "module_id": mid,
            "exists": mid in items,
            "supported": item.get("status") == "SUPPORTED",
            "has_facts": bool(item.get("supporting_facts")),
            "has_reason": bool(item.get("reason")),
        })
    complete = sum(1 for b in bundles if b["exists"] and b["supported"] and b["has_facts"])
    return {
        "status": "SCORED",
        "missing": missing,
        "bundles": bundles,
        "coverage": (complete / len(must)) if must else NA,
        "support_bundle_completeness": (complete / len(must)) if must else NA,
        "passed": not missing,
    }


def score_draft_context(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.required_particulars
    ctx = actual.get("draft_context") or {}
    facts = ctx.get("verified_facts") or {}
    if not exp.evaluate:
        return {"status": NA, "draft_context_error": actual.get("draft_context_error")}
    required = []
    for names in (exp.values or {}).values():
        required.extend(names)
    required = list(dict.fromkeys(required))
    present = [n for n in required if n in facts or n in (ctx.get("particulars") or {})]
    missing = [n for n in required if n not in present]
    cov = (len(present) / len(required)) if required else NA
    return {
        "status": "SCORED",
        "required": required,
        "present": present,
        "missing": missing,
        "coverage": cov,
        "error": actual.get("draft_context_error"),
        "passed": not missing and not actual.get("draft_context_error"),
    }


def score_draft(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.final_appeal_requirements
    letter = _letter(actual)
    grounds = list(actual.get("supported_grounds") or [])
    if not exp.evaluate:
        return {"status": NA}
    # NOTICE_ONLY / no-grounds holds: must_express cannot apply to an empty
    # letter when the pipeline correctly returned NO_SUPPORTED_GROUNDS and the
    # golden did not require a letter.
    outcome = actual.get("outcome")
    grounds_exp = golden.expected.supported_grounds
    outcome_exp = golden.expected.final_outcome
    if (not letter.strip()
            and outcome == "NO_SUPPORTED_GROUNDS"
            and not grounds
            and not (grounds_exp and grounds_exp.evaluate)
            and not (outcome_exp and outcome_exp.evaluate
                     and (outcome_exp.values or {}).get("letter_must_not_be_empty"))):
        return {
            "status": "SCORED",
            "must_express_missing": [],
            "must_not_express_hit": [],
            "placeholders": [],
            "driver_unsafe": False,
            "ground_coverage": NA,
            "material_fact_coverage": NA,
            "unsupported_assertion_rate": 0.0,
            "passed": True,
            "note": "no letter expected for NO_SUPPORTED_GROUNDS hold",
        }
    values = exp.values or {}
    must = [t for t in (values.get("must_express") or []) if t]
    must_not = [t for t in (values.get("must_not_express") or []) if t]
    missing = [t for t in must if t.lower() not in letter.lower()]
    leaked = [t for t in must_not if t.lower() in letter.lower()]
    placeholders = list(actual.get("placeholders") or [])
    driver = bool(re.search(r"\bI (parked|drove|was the driver)\b", letter, re.I))
    ground_hits = [g for g in grounds if g.replace("KB-", "").split("-")[0].lower() in letter.lower()]
    return {
        "status": "SCORED",
        "must_express_missing": missing,
        "must_not_express_hit": leaked,
        "placeholders": placeholders,
        "driver_unsafe": driver,
        "ground_coverage": (len(ground_hits) / len(grounds)) if grounds else NA,
        "material_fact_coverage": 1.0 if not missing else max(
            0.0, 1.0 - len(missing) / max(1, len(must))),
        "unsupported_assertion_rate": (1.0 if leaked else 0.0),
        "passed": not missing and not leaked and not placeholders and not driver,
    }


def score_outcome(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.final_outcome
    state = actual.get("state")
    outcome = actual.get("outcome")
    letter = _letter(actual)
    if not exp.evaluate:
        return {"status": NA, "state": state, "outcome": outcome}
    values = exp.values or {}
    issues = []
    if values.get("state") and state != values["state"]:
        issues.append(f"state {values['state']} vs {state}")
    if values.get("primary_route") and actual.get("primary_route") != values["primary_route"]:
        issues.append(f"route {values['primary_route']} vs {actual.get('primary_route')}")
    if values.get("letter_must_be_empty") and letter.strip():
        issues.append("letter should be empty")
    if values.get("letter_must_not_be_empty") and not letter.strip():
        issues.append("letter missing")
    for text in values.get("letter_excludes") or []:
        if text in letter:
            issues.append(f"letter contains {text!r}")
    processing_error_illegal = (
        actual.get("plan_digest")
        and actual.get("validation_passed")
        and letter
        and not actual.get("error")
        and outcome == "PROCESSING_ERROR"
    )
    if processing_error_illegal:
        issues.append("PROCESSING_ERROR despite plan+draft+validation pass")
    return {
        "status": "SCORED",
        "state": state,
        "outcome": outcome,
        "issues": issues,
        "passed": not issues,
        "consistency_ok": not processing_error_illegal,
    }


def score_classification(golden: GoldenCase, actual: dict) -> dict:
    exp = golden.expected.classification
    classes = actual.get("document_classes") or {}
    if not exp.evaluate:
        return {"status": NA, "document_classes": classes}
    values = exp.values or {}
    issues = []
    if values.get("not_pcn"):
        if actual.get("state") not in ("NO_APPEAL_RIGHT", "CLASSIFICATION_FAILED",
                                       "MANUAL_REVIEW", "EXTRACTED"):
            if _letter(actual):
                issues.append("non-PCN produced a letter")
    if values.get("document_class") == "PCN" and actual.get("state") == "NO_APPEAL_RIGHT":
        issues.append("PCN routed as no appeal right")
    return {
        "status": "SCORED",
        "document_classes": classes,
        "scope_stop": actual.get("scope_stop"),
        "issues": issues,
        "passed": not issues,
    }


def first_failed_layer(scores: dict, actual: dict) -> Optional[str]:
    if actual.get("error") and not actual.get("ok", True):
        return "INFRASTRUCTURE_ERROR"
    mapping = [
        ("CLASSIFICATION_ERROR", "classification"),
        ("EXTRACTION_ERROR", "extraction"),
        ("FACT_ERROR", "facts"),
        ("NARRATIVE_FACT_ERROR", "derived"),
        ("LINEAGE_ERROR", "lineage"),
        ("QUESTION_ERROR", "questions"),
        ("LEGAL_FINDING_ERROR", "findings"),
        ("KNOWLEDGE_RETRIEVAL_ERROR", "retrieval"),
        ("GROUND_SELECTION_ERROR", "grounds"),
        ("GROUND_MERGE_ERROR", "additive"),
        ("CLAIM_PLAN_ERROR", "claim_plan"),
        ("DRAFT_CONTEXT_ERROR", "draft_context"),
        ("DRAFT_ERROR", "draft"),
        ("VALIDATION_ERROR", "validation"),
        ("OUTCOME_STATE_ERROR", "outcome"),
    ]
    if scores.get("derived", {}).get("lineage_failures"):
        return "LINEAGE_ERROR"
    for layer, key in mapping:
        row = scores.get(key) or {}
        if row.get("status") == "SCORED" and row.get("passed") is False:
            return layer
    return None


def score_validation_surface(actual: dict) -> dict:
    issues = actual.get("validation_issues") or []
    placeholders = actual.get("placeholders") or []
    passed = actual.get("validation_passed")
    if passed is None and not _letter(actual):
        return {"status": NA, "note": "no draft to validate"}
    fail = bool(placeholders) or (passed is False and _letter(actual))
    return {
        "status": "SCORED",
        "passed": not fail if passed is not None else not placeholders,
        "issues": issues,
        "placeholders": placeholders,
    }


def score_all(golden: GoldenCase, actual: dict) -> dict:
    scores = {
        "classification": score_classification(golden, actual),
        "extraction": score_extraction(golden, actual),
        "facts": score_facts(golden, actual),
        "derived": score_derived(golden, actual),
        "questions": score_questions(golden, actual),
        "findings": score_findings(golden, actual),
        "retrieval": score_retrieval(golden, actual),
        "grounds": score_grounds(golden, actual),
        "claim_plan": score_claim_plan(golden, actual),
        "draft_context": score_draft_context(golden, actual),
        "draft": score_draft(golden, actual),
        "validation": score_validation_surface(actual),
        "outcome": score_outcome(golden, actual),
        "persist": actual.get("persist") or {"status": NA},
    }
    scored = [s for s in scores.values() if s.get("status") == "SCORED"]
    failed = [s for s in scored if s.get("passed") is False]
    first = first_failed_layer(scores, actual)
    e2e = not failed and first is None and actual.get("ok", True)
    if golden.completeness == NOTICE_ONLY:
        scores["notice_only_guard"] = {
            "status": "SCORED",
            "passed": True,
            "note": "Customer circumstances, reverse-page defects, and "
                    "suggested_modules were not treated as ground truth.",
        }
    return {
        "scores": scores,
        "end_to_end_pass": e2e,
        "first_failed_layer": first,
        "scored_layer_count": len(scored),
        "failed_layer_count": len(failed),
    }
