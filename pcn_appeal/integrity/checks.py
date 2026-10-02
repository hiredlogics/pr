"""Automated integrity checks over a case and its output (P5.5 §7).

Each check returns PASS or FAIL with the evidence. They assert the invariants
the architecture promises, so a regression anywhere upstream shows up here:

  DRAFT_REQUIRES_LOCKED_PLAN   no draft without a LOCKED claim plan for its run
  NO_CLAIM_OUTSIDE_PLAN        every argument in the draft is in the plan
  FACTS_HAVE_SOURCES           every fact has a source, and a write record
  NO_CUSTOMER_LEAKAGE          no internal ids, prompt text or trace data in
                               what the customer gets
  STATE_MACHINE_CONSISTENT     the recorded transitions end at the case's state
  AI_CALLS_LOGGED              every model call has model + prompt version
  QUESTIONS_HAVE_REASONS       every approved / rejected question says why
  MANIFEST_RECORDED            a generated run has its execution manifest
  DRIVER_NOT_IDENTIFIED        an unidentified-driver letter never identifies one
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

PASS, FAIL = "PASS", "FAIL"

# Trace / engine vocabulary that has no business in customer output.
TRACE_WORDS = re.compile(
    r"\b(use_when|do_not_use_when|RetrievalPack|module_id|claim_plan|fact_id|run_id|"
    r"building_block|knowledge_match|case_analysis|supporting_facts|inputs_digest)\b")
PLACEHOLDER = re.compile(r"\{\{|\}\}|\[image \d+\]|<document id=")
DRIVER = re.compile(r"\bI (drove|was driving|parked|left the (car|vehicle)|arrived|"
                    r"came back|returned to the (car|vehicle))\b", re.I)


def _check(name: str, ok: bool, detail: Any = None) -> dict:
    return {"check": name, "status": PASS if ok else FAIL, "detail": detail}


def _prompt_fragments() -> list[str]:
    """Distinctive lines from every system prompt, to detect prompt text in output."""
    from .. import prompts
    out = []
    for task, spec in prompts.registry().items():
        for line in (spec.get("body") or "").splitlines():
            line = line.strip(" -*")
            if len(line) >= 40 and line[0].isupper():
                out.append(line[:60])
    return out


def customer_surface(case, out) -> list[str]:
    """Everything the customer can see for this case: the letter, pending and
    held questions, the outcome messages."""
    texts = []
    if out is not None:
        if getattr(out, "letter", None):
            texts.append(out.letter)
        for k in ("outcome_title", "outcome_message", "outcome_next", "cta_label"):
            v = getattr(out, k, None)
            if v:
                texts.append(str(v))
    for q in getattr(case, "pending_questions", None) or []:
        texts.append(str(q.get("text") or ""))
        for opt in q.get("options") or []:
            texts.append(str(opt.get("label") if isinstance(opt, dict) else opt))
    return texts


def check_case(case, out=None, kg=None) -> list[dict]:
    from ..customer_safe import internal_ids
    from ..engines.claim_plan_authority import LOCKED, SUPERSEDED
    from .trace import run_audit
    results: list[dict] = []
    audit = run_audit(case)
    draft = getattr(out, "draft", None)
    has_draft = bool(draft is not None and getattr(draft, "paragraphs", None))
    plans = [p for p in getattr(case, "claim_plans", []) or [] if p.run_number == case.run_id]
    locked = [p for p in getattr(case, "claim_plans", []) or [] if p.status == LOCKED]
    decided = any(a.get("event") in ("claim_plan_locked", "claim_plan_reused") for a in audit)

    # 1. no draft without a locked plan
    results.append(_check(
        "DRAFT_REQUIRES_LOCKED_PLAN",
        (not has_draft) or (decided and len(locked) == 1),
        {"draft": has_draft, "plan_decided_this_run": decided,
         "locked_plans": [p.claim_plan_id for p in locked],
         "run_plans": [(p.version, p.status) for p in plans]}))

    # 2. no claim outside the plan
    outside: list[dict] = []
    if has_draft and locked:
        plan = locked[0]
        allowed = set(plan.supported_ids) | {"STRUCTURAL"}
        for s in draft.sentences():
            bad = [m for m in s.module_refs if m not in allowed]
            if bad:
                outside.append({"modules": bad, "sentence": s.text[:80]})
        pack = getattr(out, "pack", None)
        if pack is not None and kg is not None:
            from ..engines.validation import ValidationEngine
            for i in ValidationEngine(kg=kg)._plan_conformance(draft, pack):
                outside.append({"rule": i.rule, "message": i.message,
                                "sentence": (i.sentence or "")[:80]})
    results.append(_check("NO_CLAIM_OUTSIDE_PLAN", not outside, outside or None))

    # 3. every fact has a source and a write record
    recorded = {h.get("fact") for h in getattr(case, "fact_history", []) or []}
    unsourced = [name for name, f in case.facts.items()
                 if not getattr(f.source, "ref", None) or f.source.kind is None]
    unrecorded = [name for name in case.facts if name not in recorded]
    results.append(_check("FACTS_HAVE_SOURCES", not unsourced and not unrecorded,
                          {"no_source": unsourced, "no_write_record": unrecorded} if
                          (unsourced or unrecorded) else None))

    # 4. customer leakage
    leaks: list[dict] = []
    fragments = _prompt_fragments()
    for text in customer_surface(case, out):
        ids = internal_ids(text)
        if ids:
            leaks.append({"internal_ids": sorted(set(ids)), "text": text[:80]})
        if PLACEHOLDER.search(text):
            leaks.append({"placeholder": PLACEHOLDER.search(text).group(0), "text": text[:80]})
        if TRACE_WORDS.search(text):
            leaks.append({"trace_word": TRACE_WORDS.search(text).group(0), "text": text[:80]})
        hit = next((f for f in fragments if f in text), None)
        if hit:
            leaks.append({"prompt_text": hit, "text": text[:80]})
    results.append(_check("NO_CUSTOMER_LEAKAGE", not leaks, leaks or None))

    # 5. state machine
    history = getattr(case, "state_history", []) or []
    final = getattr(case.state, "value", str(case.state))
    consistent = (not history) or history[-1]["to"] == final
    chain_ok = all(history[i]["to"] == history[i + 1]["from"] for i in range(len(history) - 1))
    results.append(_check("STATE_MACHINE_CONSISTENT", consistent and chain_ok,
                          None if consistent and chain_ok else
                          {"final": final, "history": history[-5:]}))

    # 6. AI calls logged with model + prompt version
    calls = [c for c in getattr(case, "ai_calls", []) or [] if c.get("run_id") == case.run_id]
    bad_calls = [c for c in calls if not c.get("model") or c.get("prompt_version") is None
                 or not c.get("input_sha256")]
    results.append(_check("AI_CALLS_LOGGED", not bad_calls,
                          {"calls": len(calls), "incomplete": bad_calls[:3]} if bad_calls else
                          {"calls": len(calls)}))

    # 7. questions have reasons
    missing: list[dict] = []
    for a in audit:
        if a.get("event") != "question_review":
            continue
        if not str(a.get("reason") or "").strip() or a.get("decision") not in ("APPROVED",
                                                                                 "REJECTED"):
            missing.append({"fact": a.get("fact"), "decision": a.get("decision")})
    results.append(_check("QUESTIONS_HAVE_REASONS", not missing, missing or None))

    # 8. manifest recorded for a generated run
    generated = any(a.get("event") in ("retrieval_pack", "validation") for a in audit)
    manifest = any(a.get("event") == "execution_manifest" for a in audit)
    results.append(_check("MANIFEST_RECORDED", (not generated) or manifest,
                          {"generated": generated, "manifest": manifest}))

    # 9a. a draft that exists has a recorded version tied to the claim plan
    versions_this_run = [v for v in getattr(case, "draft_versions", []) or []
                         if v.get("run_id") == case.run_id]
    if has_draft:
        from ..drafting.versions import content_hash
        digest = content_hash(draft)
        match = [v for v in versions_this_run if v["content_hash"] == digest]
        plan_ids = {p.claim_plan_id for p in locked}
        ok = bool(match) and all(v.get("claim_plan_id") in plan_ids for v in match)
        results.append(_check("DRAFT_VERSION_RECORDED", ok,
                              None if ok else {"versions": len(versions_this_run),
                                               "content_hash": digest[:12]}))
    # 9c. a legal defect in the letter maps to a VERIFIED legal finding (P6.1)
    from ..legal import findings as legal_findings
    letter_text = getattr(out, "letter", None) or ""
    verified = legal_findings.verified_types(getattr(case, "legal_findings", []) or [])
    unproven = []
    for sentence in re.split(r"(?<=[.!?])\s+", letter_text):
        # Assertion-only types are policed at draft time with the approved-wording
        # exemption (P7 B6); this check keeps to calculable defects.
        asserted = legal_findings.asserted_types(sentence) - legal_findings.ASSERTION_ONLY
        if asserted and not legal_findings.PUT_TO_PROOF.search(sentence) \
                and not (asserted & verified):
            unproven.append({"asserted": sorted(asserted), "sentence_sha":
                             hashlib.sha256(sentence.encode()).hexdigest()[:12]})
    results.append(_check("LEGAL_DEFECTS_VERIFIED", not unproven,
                          {"unproven": unproven, "verified": sorted(verified)}
                          if unproven else None))

    # 9b. driver never identified
    letter = getattr(out, "letter", None) or ""
    unidentified = getattr(case.driver_status, "value", str(case.driver_status)) == "UNIDENTIFIED"
    hit = DRIVER.search(letter) if (letter and unidentified) else None
    results.append(_check("DRIVER_NOT_IDENTIFIED", hit is None,
                          None if hit is None else {"match": hit.group(0)}))

    # 10. every supported claim carries its supporting facts (P7 B4: the
    # in-memory twin of the database check of the same name)
    unsupported = [i.module_id for p in locked for i in p.items
                   if i.status == "SUPPORTED" and not i.supporting_facts]
    results.append(_check("SUPPORTED_ITEMS_HAVE_SUPPORT", not unsupported,
                          {"unsupported": unsupported} if unsupported else None))
    return results


def passed(results: list[dict]) -> bool:
    return all(r["status"] == PASS for r in results)


# P7 B4: the checks whose failure refuses a release. Belt over the inline
# gates (VAL-*/DV-* and the claim-plan refusals): any of these failing while a
# letter is about to be released routes the case to MANUAL_REVIEW instead.
# The rest of the checks stay observational (bookkeeping, not legal integrity).
CRITICAL = ("NO_CLAIM_OUTSIDE_PLAN", "FACTS_HAVE_SOURCES", "NO_CUSTOMER_LEAKAGE",
            "DRIVER_NOT_IDENTIFIED", "LEGAL_DEFECTS_VERIFIED",
            "STATE_MACHINE_CONSISTENT", "SUPPORTED_ITEMS_HAVE_SUPPORT")


def critical_failures(results: list[dict]) -> list[str]:
    return [r["check"] for r in results if r["check"] in CRITICAL and r["status"] != PASS]


# ------------------------------------------------------------- database
DB_CHECKS = {
    # A released or drafted output whose run has no LOCKED / SUPERSEDED plan.
    "DRAFT_REQUIRES_LOCKED_PLAN": """
        SELECT d.draft_id, d.case_id, d.run_id, d.state
        FROM drafts d
        WHERE d.state IN ('RELEASED', 'DRAFTED', 'VALIDATION_FAILED')
          AND NOT EXISTS (SELECT 1 FROM claim_plans p
                          WHERE p.case_id = d.case_id AND p.run_number = d.run_id
                            AND p.status IN ('LOCKED', 'SUPERSEDED'))""",
    # Two LOCKED plans for one case.
    "ONE_LOCKED_PLAN_PER_CASE": """
        SELECT case_id, count(*) AS locked FROM claim_plans WHERE status = 'LOCKED'
        GROUP BY case_id HAVING count(*) > 1""",
    # A supported item without supporting facts or a priority.
    "SUPPORTED_ITEMS_HAVE_SUPPORT": """
        SELECT i.item_id, i.module_id, p.case_id FROM claim_plan_items i
        JOIN claim_plans p ON p.claim_plan_id = i.claim_plan_id
        WHERE i.status = 'SUPPORTED' AND (i.priority IS NULL OR i.supporting_facts IS NULL
              OR i.reason IS NULL OR i.reason = '')""",
    # An active fact with no source reference.
    "FACTS_HAVE_SOURCES": """
        SELECT fact_id, case_id, fact_name FROM facts
        WHERE active AND (source_ref IS NULL OR source_ref = '' OR source_kind IS NULL)""",
    # A fact row with no history entry at all.
    "FACTS_HAVE_WRITE_RECORDS": """
        SELECT f.fact_id, f.case_id, f.fact_name FROM facts f
        WHERE NOT EXISTS (SELECT 1 FROM fact_history h
                          WHERE h.case_id = f.case_id AND h.fact = f.fact_name)""",
    # A released letter carrying an internal id.
    "NO_CUSTOMER_LEAKAGE": """
        SELECT draft_id, case_id FROM drafts
        WHERE state = 'RELEASED' AND letter IS NOT NULL
          AND (letter LIKE '%KB-%' OR letter LIKE '%VAL-%' OR letter LIKE '%PP-%'
               OR letter LIKE '%{{%' OR letter LIKE '%module_id%')""",
}


def check_store(connect, case_id: Optional[str] = None) -> list[dict]:
    """The same invariants over the database (all cases, or one)."""
    results = []
    with connect() as conn:
        for name, sql in DB_CHECKS.items():
            if case_id is not None:
                sql = f"SELECT * FROM ({sql}) q WHERE q.case_id = %s"
                rows = conn.execute(sql, (case_id,)).fetchall()
            else:
                rows = conn.execute(sql).fetchall()
            results.append(_check(name, not rows,
                                  [tuple(str(v) for v in r) for r in rows[:10]] or None))
    return results


__all__ = ["check_case", "check_store", "passed", "customer_surface", "DB_CHECKS", "PASS", "FAIL"]
