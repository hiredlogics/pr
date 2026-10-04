"""P10.4 semantic / KB / ground / drafting metrics (evaluation-only)."""
from __future__ import annotations

import re
from typing import Any, Optional

from pcn_appeal.module_roles import (
    EVIDENCE_REQUIREMENT, LEGAL_CONCLUSION, STRUCTURAL,
    SUBSTANTIVE_GROUND, SUPPORTING_PROPOSITION, role_of,
)
from pcn_appeal.semantics.ontology import CONCEPT_TO_FACTS

NA = "N/A"


def _truthy(value: Any) -> Optional[bool]:
    if value is True or value == "yes" or str(value).lower() in ("true", "1"):
        return True
    if value is False or value == "no" or str(value).lower() in ("false", "0"):
        return False
    return None


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return _truthy(expected) == _truthy(actual)
    return _norm(expected) == _norm(actual) or (
        _norm(expected).replace(" ", "") == _norm(actual).replace(" ", "")
    )


def _affirmed(concepts: list[dict]) -> set[str]:
    return {c["concept"] for c in concepts
            if c.get("polarity") == "AFFIRMED" and c.get("concept")}


def _negated(concepts: list[dict]) -> set[str]:
    return {c["concept"] for c in concepts
            if c.get("polarity") == "NEGATED" and c.get("concept")}


def _uncertain(concepts: list[dict]) -> set[str]:
    return {c["concept"] for c in concepts
            if c.get("polarity") == "UNCERTAIN" and c.get("concept")}


def _rate(num: int, den: int):
    if den == 0:
        return NA
    return num / den


def score_semantics(expected: dict, actual: dict) -> dict:
    concepts = actual.get("concepts") or []
    aff = _affirmed(concepts)
    neg = _negated(concepts)
    unc = _uncertain(concepts)

    # Hard set (all required) preferred; *_any is soft (at least one).
    want_all = set(expected.get("semantic_concepts_affirmed") or [])
    want_any = set(expected.get("semantic_concepts_any") or [])
    must_not = set(expected.get("must_not_affirm_concepts") or [])
    want_neg = set(expected.get("negated_concepts_any") or [])
    want_unc = set(expected.get("uncertain_concepts_any") or [])

    # Near-equivalent ontology pairs for scoring (not promotion).
    _EQUIV = {
        "KEYING_ERROR": "REGISTRATION_MISMATCH",
        "REGISTRATION_MISMATCH": "KEYING_ERROR",
        "LOADING": "DELIVERY",
        "DELIVERY": "LOADING",
        "COLLECTION": "PICK_UP",
        "PICK_UP": "COLLECTION",
    }

    def _covered(want: set[str], have: set[str]) -> set[str]:
        hit = set()
        for w in want:
            if w in have or _EQUIV.get(w) in have:
                hit.add(w)
        return hit

    if want_all:
        hit = _covered(want_all, aff)
        tp = sorted(hit)
        fn = sorted(want_all - hit)
        misses = list(fn)
        scored_want = want_all
    elif want_any:
        # Soft case pass, but confusion still records every listed miss (P10.5 §9).
        hit = _covered(want_any, aff)
        tp = sorted(hit)
        fn = [] if tp else sorted(want_any)
        misses = sorted(want_any - hit)
        scored_want = want_any
    else:
        tp, fn, misses, scored_want = [], [], [], set()

    fp = sorted(must_not & aff)
    unexpected = sorted(aff - scored_want - must_not) if scored_want else []

    concept_p = _rate(len(tp), len(tp) + len(fp)) if (scored_want or must_not) else NA
    concept_r = _rate(len(tp), len(tp) + len(misses)) if scored_want else NA

    # Narrative fact P/R from facts_any / must_not_promote
    facts = actual.get("facts") or {}
    fact_tp, fact_fp, fact_fn = [], [], []
    for name, want in (expected.get("facts_any") or {}).items():
        got = facts.get(name)
        if got is None:
            fact_fn.append(name)
        elif _values_equal(want, got.get("value")):
            fact_tp.append(name)
        else:
            fact_fn.append(name)
    for name in expected.get("must_not_promote_facts") or []:
        got = facts.get(name)
        if got is not None and _truthy(got.get("value")) is True:
            fact_fp.append(name)
        elif got is not None and name == "keying_error_type" and got.get("value"):
            fact_fp.append(name)

    # Negation / uncertainty / attribution accuracy
    neg_ok = True
    if want_neg:
        neg_ok = bool(want_neg & neg) and not bool(want_neg & aff)
    if must_not:
        neg_ok = neg_ok and not bool(must_not & aff)

    unc_ok = True
    if want_unc:
        unc_ok = bool(want_unc & unc) and not bool(want_unc & aff)
    if expected.get("uncertain_or_negated_ok"):
        # Soft holdout: uncertain or absent promotion is enough
        unc_ok = True
    for name in expected.get("must_not_promote_facts") or []:
        # uncertain concepts mapped to this fact must not promote
        mapped = [c for c, (f, _) in CONCEPT_TO_FACTS.items() if f == name]
        if any(c in aff for c in mapped) and name in (expected.get("must_not_promote_facts") or []):
            # already in fact_fp path
            pass

    attrib_ok = True
    for c in concepts:
        if c.get("attribution") not in (None, "CUSTOMER", "DOCUMENT", "DERIVED"):
            attrib_ok = False
            break
        if c.get("polarity") == "AFFIRMED" and not c.get("attribution"):
            attrib_ok = False

    # Fact-promotion accuracy: among affirmed concepts with mapping, fact present
    promo_ok, promo_total = 0, 0
    for c in concepts:
        if c.get("polarity") != "AFFIRMED":
            continue
        mapped = CONCEPT_TO_FACTS.get(c["concept"])
        if not mapped:
            continue
        promo_total += 1
        fname, fval = mapped
        got = facts.get(fname)
        if got is not None and _values_equal(fval, got.get("value")):
            promo_ok += 1

    # Derived-lineage completeness for derived facts
    lineage_ok, lineage_total = 0, 0
    for name, row in facts.items():
        if str(row.get("source") or "").upper() == "DERIVED" or row.get("lineage"):
            lineage_total += 1
            if row.get("lineage") or row.get("authority"):
                lineage_ok += 1

    # Soft *_any: case passes if ≥1 hit; hard affirmed: all required.
    concept_case_ok = True
    if want_all:
        concept_case_ok = not fn and not fp
    elif want_any:
        concept_case_ok = bool(tp) and not fp
    elif must_not:
        concept_case_ok = not fp

    passed = (
        concept_case_ok and (not fact_fn) and (not fact_fp)
        and neg_ok and unc_ok and attrib_ok
    ) if (want_all or want_any or must_not or want_neg or want_unc
          or expected.get("facts_any") or expected.get("must_not_promote_facts")) else True

    return {
        "status": "SCORED",
        "semantic_concept_precision": concept_p,
        "semantic_concept_recall": concept_r,
        "concept_tp": tp,
        "concept_fp": fp,
        "concept_fn": fn,
        "concept_misses_partial": misses,
        "concept_unexpected_affirmed": unexpected,
        "narrative_fact_precision": _rate(len(fact_tp), len(fact_tp) + len(fact_fp)),
        "narrative_fact_recall": _rate(len(fact_tp), len(fact_tp) + len(fact_fn)),
        "fact_tp": fact_tp, "fact_fp": fact_fp, "fact_fn": fact_fn,
        "negation_accuracy": 1.0 if neg_ok else 0.0,
        "uncertainty_accuracy": 1.0 if unc_ok else 0.0,
        "attribution_accuracy": 1.0 if attrib_ok else 0.0,
        "fact_promotion_accuracy": _rate(promo_ok, promo_total),
        "derived_lineage_completeness": _rate(lineage_ok, lineage_total),
        "passed": passed,
    }


def score_kb_grounds(expected: dict, actual: dict) -> dict:
    retrieved = list(actual.get("retrieved_modules") or [])
    supported = list(actual.get("supported_grounds") or [])
    items = actual.get("claim_plan_items") or []
    roles = actual.get("role_partition") or {}

    want_cand = set(expected.get("kb_candidates_any") or [])
    want_sub = set(expected.get("substantive_grounds_any") or [])
    forbid = set(expected.get("must_not_include_grounds") or [])

    # Candidate P/R: *_any means at least one listed candidate must be retrieved.
    # Role-filtered companions in retrieval are not candidate FPs (measured under role-filter).
    retrieved_set = set(retrieved)
    if want_cand:
        hit = want_cand & retrieved_set
        cand_tp = sorted(hit)
        cand_fn = [] if hit else sorted(want_cand)
        cand_fp = sorted(m for m in retrieved_set if m not in want_cand and m not in forbid
                         and role_of(m) == SUBSTANTIVE_GROUND)
    else:
        cand_tp, cand_fn, cand_fp = [], [], []

    # Role-filter: forbidden LEGAL_CONCLUSION / SUPPORT must not be in supported
    support_in_plan = [m for m in supported if role_of(m) == SUPPORTING_PROPOSITION]
    legal_in_plan = [m for m in supported if role_of(m) == LEGAL_CONCLUSION]
    structural_in_plan = [m for m in supported if role_of(m) == STRUCTURAL]
    evidence_in_plan = [m for m in supported if role_of(m) == EVIDENCE_REQUIREMENT]

    role_filter_fp = legal_in_plan + [
        m for m in support_in_plan
        if expected.get("supporting_cannot_independently_lead")
        and not roles.get("substantive")
    ]
    # Role-filter recall: expected forbidden companions rejected from plan
    role_filtered_ok = sorted(forbid & retrieved_set - set(supported))
    rejected_roles = [
        i for i in items
        if i.get("status") in ("REJECTED", "rejected")
        or str(i.get("origin") or "") == "ROLE_INELIGIBLE"
    ]
    role_tn = sorted(set(role_filtered_ok) | {
        i["module_id"] for i in rejected_roles
        if role_of(i["module_id"]) in (LEGAL_CONCLUSION, SUPPORTING_PROPOSITION)
    })

    # Ground *_any: at least one expected substantive ground in supported.
    supported_set = set(supported)
    if want_sub:
        hit = want_sub & supported_set
        ground_tp = sorted(hit)
        ground_fn = [] if hit else sorted(want_sub)
    else:
        ground_tp, ground_fn = [], []
    ground_fp = sorted(supported_set & forbid)

    orphan = bool(roles.get("orphan_support"))
    orphan_letter = orphan and not actual.get("letter_empty") and actual.get("state") == "RELEASED"
    unsupported_leading = 0
    for mid in supported:
        role = role_of(mid)
        if role in (SUPPORTING_PROPOSITION, LEGAL_CONCLUSION, STRUCTURAL):
            unsupported_leading += 1
        # EVIDENCE without explicit lead flag counted in role audit, not here as hard fail
        # unless gold forbids

    # Expected: 0 SUPPORTING incorrectly in claim plan as independent grounds when orphan
    supporting_incorrect_claim = len(support_in_plan) if orphan else 0
    # Expected: 0 LEGAL_CONCLUSION as independent grounds
    legal_incorrect = len(legal_in_plan)

    primary_ok = True
    if expected.get("primary_route_any"):
        primary_ok = actual.get("primary_route") in expected["primary_route_any"]

    # Candidate recall for *_any: pass if ≥1 hit (cand_fn empty)
    cand_ok = (not want_cand) or bool(cand_tp)
    ground_ok = (not want_sub) or bool(ground_tp)

    passed = (
        ground_ok and (not ground_fp)
        and cand_ok
        and legal_incorrect == 0
        and (not expected.get("orphan_support_letter_forbidden") or not orphan_letter)
        and primary_ok
    )

    return {
        "status": "SCORED",
        "kb_candidate_precision": _rate(len(cand_tp), len(cand_tp) + len(cand_fp))
        if want_cand or cand_fp else NA,
        "kb_candidate_recall": _rate(len(cand_tp), len(cand_tp) + len(cand_fn))
        if want_cand else NA,
        "candidate_tp": cand_tp, "candidate_fp": cand_fp, "candidate_fn": cand_fn,
        "role_filter_precision": _rate(
            len(supported) - len(role_filter_fp), max(len(supported), 1)
        ) if supported else (1.0 if not role_filter_fp else 0.0),
        "role_filter_recall": _rate(len(role_tn), len(role_tn) + legal_incorrect)
        if (role_tn or legal_incorrect) else NA,
        "ground_precision": _rate(len(ground_tp), len(ground_tp) + len(ground_fp))
        if (want_sub or ground_fp) else NA,
        "ground_recall": _rate(len(ground_tp), len(ground_tp) + len(ground_fn))
        if want_sub else NA,
        "ground_tp": ground_tp, "ground_fp": ground_fp, "ground_fn": ground_fn,
        "orphan_support": orphan,
        "orphan_support_letter": orphan_letter,
        "unsupported_leading_ground_rate": _rate(
            unsupported_leading, len(supported)) if supported else 0.0,
        "supporting_incorrectly_in_claim_plan": supporting_incorrect_claim,
        "legal_conclusion_independent_grounds": legal_incorrect,
        "legal_in_plan": legal_in_plan,
        "support_in_plan": support_in_plan,
        "structural_in_plan": structural_in_plan,
        "evidence_in_plan": evidence_in_plan,
        "primary_route_ok": primary_ok,
        "passed": passed,
    }


def score_drafting(expected: dict, actual: dict) -> dict:
    """Draft metrics only when upstream Claim Plan + Support + DraftContext complete."""
    from pcn_appeal.drafting.plan import (
        particular_expressed, section_expresses_ground,
    )
    from pcn_appeal.module_roles import LEGAL_CONCLUSION, SUPPORTING_PROPOSITION, role_of

    ctx = actual.get("draft_context") or {}
    plan = ctx.get("draft_plan") or {}
    sections = list(plan.get("sections") or [])
    letter = actual.get("letter") or ""
    letter_l = letter.lower()

    # Leading / substantive grounds from DraftPlan (excludes support-only)
    plan_grounds = []
    for sec in sections:
        for mid in sec.get("ground_ids") or []:
            if mid not in plan_grounds:
                plan_grounds.append(mid)
    if not plan_grounds:
        plan_grounds = [
            m for m in (actual.get("supported_grounds") or [])
            if role_of(m) not in (SUPPORTING_PROPOSITION, LEGAL_CONCLUSION)
        ]

    clean = (
        bool(plan_grounds or actual.get("supported_grounds"))
        and bool(actual.get("draft_context_complete"))
        and bool(actual.get("support_bundle_complete"))
        and actual.get("state") == "RELEASED"
        and not actual.get("letter_empty")
    )
    if not clean:
        return {
            "status": "N/A",
            "reason": "upstream incomplete or not released",
            "clean_upstream": False,
            "passed": None,
        }

    # P10.6: section-level semantic ground coverage
    covered = 0
    for sec in sections:
        # lightweight DraftSection-like duck for expression check
        class _S:
            pass
        s = _S()
        s.ground_ids = list(sec.get("ground_ids") or [])
        s.particular_values = dict(sec.get("particular_values") or {})
        s.required_particulars = list(sec.get("required_particulars") or [])
        if section_expresses_ground(letter, s):
            covered += 1
    ground_denom = len(sections) if sections else len(plan_grounds)
    if not sections and plan_grounds:
        # fallback: topic cues per ground family
        from pcn_appeal.drafting.plan import _TOPIC_CUES, _route_family
        for mid in plan_grounds:
            cue = _TOPIC_CUES.get(_route_family(mid))
            if cue and cue.search(letter):
                covered += 1

    facts = actual.get("facts") or {}
    mat_need = list((expected.get("facts_any") or {}).keys())
    mat_hit = 0
    for name in mat_need:
        got = facts.get(name)
        if got and str(got.get("value")) and str(got.get("value")).lower() not in ("none",):
            if name in str(ctx).lower() or str(got.get("value")).lower() in letter_l:
                mat_hit += 1

    # Required particulars from DraftPlan sections (values present only)
    req_total = 0
    req_hit = 0
    for sec in sections:
        values = sec.get("particular_values") or {}
        for name in sec.get("required_particulars") or []:
            val = values.get(name)
            if val in (None, "", False) and name not in ("days_late", "days"):
                continue
            req_total += 1
            if particular_expressed(letter, name, val):
                req_hit += 1
    if req_total == 0:
        req = []
        for item in actual.get("claim_plan_items") or []:
            if item.get("module_id") in plan_grounds:
                req.extend(item.get("required_particulars") or [])
        req = sorted(set(req))
        req_total = len(req)
        req_hit = sum(1 for r in req if str(r).lower().replace("_", " ") in letter_l
                      or r in str(ctx))

    unsupported = 0
    issues = actual.get("validation_issues") or []
    for i in issues:
        rule = str(i.get("rule") or "").upper()
        if "UNSUPPORTED" in rule or rule == "VAL-INVENTED":
            unsupported += 1

    ground_cov = _rate(covered, ground_denom) if ground_denom else NA
    return {
        "status": "SCORED",
        "clean_upstream": True,
        "draft_ground_coverage": ground_cov,
        "material_fact_coverage": _rate(mat_hit, len(mat_need)) if mat_need else NA,
        "required_particular_coverage": _rate(req_hit, req_total) if req_total else NA,
        "unsupported_assertion_rate": _rate(unsupported, max(len(issues), 1)),
        "validation_passed": actual.get("validation_passed"),
        "draft_plan_sections": len(sections),
        "passed": bool(actual.get("validation_passed")) and (
            ground_cov == 1.0 if isinstance(ground_cov, float) else True
        ),
    }


def score_invariants(expected: dict, actual: dict) -> dict:
    roles = actual.get("role_partition") or {}
    supported = actual.get("supported_grounds") or []
    checks = {}
    wanted = set(expected.get("invariants") or [])

    legal = [m for m in supported if role_of(m) == LEGAL_CONCLUSION]
    support = [m for m in supported if role_of(m) == SUPPORTING_PROPOSITION]
    structural = [m for m in supported if role_of(m) == STRUCTURAL]
    orphan = bool(roles.get("orphan_support"))
    orphan_letter = orphan and actual.get("state") == "RELEASED" and not actual.get("letter_empty")

    checks["no_legal_conclusion_independent"] = len(legal) == 0
    checks["support_cannot_lead_alone"] = not (support and not roles.get("substantive"))
    checks["no_support_orphan_letter"] = not orphan_letter
    checks["no_structural_lead"] = len(structural) == 0
    checks["negation_accuracy"] = True  # filled by semantics
    checks["uncertainty_accuracy"] = True

    if not wanted:
        wanted = {
            "no_legal_conclusion_independent",
            "support_cannot_lead_alone",
            "no_support_orphan_letter",
            "no_structural_lead",
        }
    selected = {k: checks.get(k, True) for k in wanted if k in checks}
    return {
        "status": "SCORED",
        "checks": selected,
        "legal_conclusion_in_plan": legal,
        "supporting_in_plan": support,
        "passed": all(selected.values()) if selected else True,
    }


def score_case(expected: dict, actual: dict) -> dict:
    sem = score_semantics(expected, actual)
    kb = score_kb_grounds(expected, actual)
    inv = score_invariants(expected, actual)
    draft = score_drafting(expected, actual)
    # Wire negation/uncertainty into invariant view
    if "negation_accuracy" in (expected.get("invariants") or []):
        inv["checks"]["negation_accuracy"] = sem["negation_accuracy"] == 1.0
    if "uncertainty_accuracy" in (expected.get("invariants") or []):
        inv["checks"]["uncertainty_accuracy"] = sem["uncertainty_accuracy"] == 1.0
    inv["passed"] = all(inv["checks"].values()) if inv["checks"] else True

    layers = [
        ("SEMANTIC", sem),
        ("KB_GROUND", kb),
        ("INVARIANTS", inv),
        ("DRAFT", draft),
    ]
    first_fail = None
    for name, block in layers:
        if block.get("status") == "SCORED" and block.get("passed") is False:
            first_fail = name
            break
    e2e = first_fail is None and actual.get("ok", True) and not actual.get("error")
    return {
        "scores": {"semantic": sem, "kb_ground": kb, "invariants": inv, "draft": draft},
        "end_to_end_pass": e2e,
        "first_failed_layer": first_fail,
    }


def confusion_by_concept(case_scores: list[dict]) -> dict:
    """Aggregate FP/FN by ontology concept across cases."""
    table: dict[str, dict] = {}
    for row in case_scores:
        sem = (row.get("scores") or {}).get("semantic") or {}
        for c in sem.get("concept_fp") or []:
            table.setdefault(c, {"fp": 0, "fn": 0, "tp": 0})
            table[c]["fp"] += 1
        for c in sem.get("concept_fn") or []:
            table.setdefault(c, {"fp": 0, "fn": 0, "tp": 0})
            table[c]["fn"] += 1
        # Partial misses under *_any still surface in the confusion table
        for c in sem.get("concept_misses_partial") or []:
            if c not in (sem.get("concept_tp") or []):
                table.setdefault(c, {"fp": 0, "fn": 0, "tp": 0})
                table[c]["fn"] += 1
        for c in sem.get("concept_tp") or []:
            table.setdefault(c, {"fp": 0, "fn": 0, "tp": 0})
            table[c]["tp"] += 1
    return table


def aggregate_metrics(case_scores: list[dict]) -> dict:
    def _avg(key_path):
        vals = []
        for row in case_scores:
            cur = row.get("scores") or {}
            for k in key_path:
                cur = (cur or {}).get(k)
            if isinstance(cur, (int, float)):
                vals.append(float(cur))
        return sum(vals) / len(vals) if vals else NA

    supporting_bad = sum(
        ((r.get("scores") or {}).get("kb_ground") or {}).get(
            "supporting_incorrectly_in_claim_plan", 0)
        for r in case_scores
    )
    legal_bad = sum(
        ((r.get("scores") or {}).get("kb_ground") or {}).get(
            "legal_conclusion_independent_grounds", 0)
        for r in case_scores
    )
    orphan_letters = sum(
        1 for r in case_scores
        if ((r.get("scores") or {}).get("kb_ground") or {}).get("orphan_support_letter")
    )
    clean_draft = [
        r for r in case_scores
        if ((r.get("scores") or {}).get("draft") or {}).get("clean_upstream")
    ]
    confusion = confusion_by_concept(case_scores)
    # Aggregate semantic P/R MUST equal confusion-table totals (P10.5 §9).
    tp = sum(v.get("tp", 0) for v in confusion.values())
    fp = sum(v.get("fp", 0) for v in confusion.values())
    fn = sum(v.get("fn", 0) for v in confusion.values())
    scored_concepts = sorted(confusion)
    return {
        "n_cases": len(case_scores),
        "e2e_pass_rate": _rate(
            sum(1 for r in case_scores if r.get("end_to_end_pass")),
            len(case_scores),
        ),
        "semantic_concept_precision": _rate(tp, tp + fp),
        "semantic_concept_recall": _rate(tp, tp + fn),
        "semantic_confusion_totals": {"tp": tp, "fp": fp, "fn": fn},
        "scored_concepts": scored_concepts,
        "unscored_concepts": [],
        "metric_note": (
            "semantic_concept P/R derived from confusion-table sums so "
            "aggregate equals per-concept TP/FP/FN totals"
        ),
        "narrative_fact_precision": _avg(["semantic", "narrative_fact_precision"]),
        "narrative_fact_recall": _avg(["semantic", "narrative_fact_recall"]),
        "negation_accuracy": _avg(["semantic", "negation_accuracy"]),
        "uncertainty_accuracy": _avg(["semantic", "uncertainty_accuracy"]),
        "attribution_accuracy": _avg(["semantic", "attribution_accuracy"]),
        "fact_promotion_accuracy": _avg(["semantic", "fact_promotion_accuracy"]),
        "derived_lineage_completeness": _avg(["semantic", "derived_lineage_completeness"]),
        "kb_candidate_precision": _avg(["kb_ground", "kb_candidate_precision"]),
        "kb_candidate_recall": _avg(["kb_ground", "kb_candidate_recall"]),
        "role_filter_precision": _avg(["kb_ground", "role_filter_precision"]),
        "role_filter_recall": _avg(["kb_ground", "role_filter_recall"]),
        "ground_precision": _avg(["kb_ground", "ground_precision"]),
        "ground_recall": _avg(["kb_ground", "ground_recall"]),
        "orphan_support_letter_count": orphan_letters,
        "supporting_incorrectly_reached_claim_plan": supporting_bad,
        "legal_conclusion_independent_grounds": legal_bad,
        "supporting_incorrect_expected": 0,
        "legal_conclusion_incorrect_expected": 0,
        "clean_upstream_draft_cases": len(clean_draft),
        "draft_ground_coverage": _avg(["draft", "draft_ground_coverage"]),
        "material_fact_coverage": _avg(["draft", "material_fact_coverage"]),
        "required_particular_coverage": _avg(["draft", "required_particular_coverage"]),
        "unsupported_assertion_rate": _avg(["draft", "unsupported_assertion_rate"]),
        "confusion_by_concept": confusion,
    }
