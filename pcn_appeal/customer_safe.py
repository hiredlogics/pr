"""The customer-safe serializer: the one gate every customer response passes.

The customer surface is the questions, holds, results, refusals and errors the
public journey returns, plus the letter and its PDF. None of it may carry the
machinery behind it: knowledge-module ids (KB-POFA-02), block ids (PP-END-001),
validator ids and versions (VAL-RES, VAL-1), why a question was asked, model
confidence or reasoning, or routing logic. Those belong to the admin trace.

Two layers, deliberately redundant:

  * the endpoints build customer payloads from customer fields only
    (`customer_question`, `_outcome_fields`, `_customer_flags` in api.py);
  * `scrub` runs over every customer JSON response in middleware, drops any
    internal key that slipped in, and redacts any internal id left in a
    string. A scrub that fires is a leak in the first layer, so it is logged.

`reason` is not an internal key here: `rejected[].reason` is the customer's
own "why this file could not be read". The routing reason of an upload
refusal is withheld where that payload is built.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("pcn_appeal.customer_safe")

# Keys that are internal wherever they appear in a customer payload.
INTERNAL_KEYS = frozenset({
    "material_because", "why_asked", "material_reason", "target_fact",
    "module_id", "module_ids", "module_refs", "fact_refs", "evidence_refs",
    "issues", "blocking_issues", "rule", "rule_id", "validator", "validator_version",
    "confidence", "reasoning", "trace", "audit", "claim_plan", "pack",
    "related_claim", "unlocks", "kb_version", "kb_release", "prompt_versions",
    "manifest", "execution_manifest", "facts", "pofa_findings", "pofa_route",
    "legal_findings", "finding_id", "finding_type", "calculation_result", "supporting_facts",
    "primary_route", "secondary_routes", "code_version", "missing_facts",
    "prohibited_claims", "policy", "differed", "run_id", "analysis_run_id",
    "possible_impact", "hypothesis_id", "hypotheses", "fact_hypotheses", "hypothesis_trace",
    # P3 Question Authority: the question object beyond what is answered.
    "question_id", "related_module", "impact_if_yes", "impact_if_no", "question_trace",
    # Phase 4: the question contract beyond what is answered.
    "fact_key", "source_module_ids", "materiality_reason", "possible_effect",
    "priority", "kb_gated",
})

# Internal identifiers. Shapes, not a list, so a new module is covered the day
# it is added: KB-POFA-02, KB-LAND-01, PP-END-001, PP-POFA-005B, AI-XXX-001,
# VAL-RES, VAL-1, RULE-*, MODULE-*, SCOP-*.
INTERNAL_ID = re.compile(
    r"\b(?:"
    r"KB-[A-Z]+(?:-[A-Z0-9]+)+"
    r"|(?:PP|AI)-[A-Z]+-\d+[A-Z]?"
    r"|VAL-[A-Z0-9]+(?:-[A-Z0-9]+)*"
    r"|RULE-[A-Z0-9]+(?:-[A-Z0-9]+)*"
    r"|MODULE-[A-Z0-9]+(?:-[A-Z0-9]+)*"
    r"|SCOP-[A-Z0-9]+(?:-[A-Z0-9]+)*"
    r")\b"
)

# The four fields a question may show a customer.
QUESTION_FIELDS = ("fact", "text", "type", "options")


def customer_question(q: dict) -> dict:
    """A question as the customer sees it: what to answer and how, never why."""
    return {k: q[k] for k in QUESTION_FIELDS if k in q}


def customer_questions(questions) -> list[dict]:
    return [customer_question(q) for q in (questions or []) if isinstance(q, dict)]


def internal_ids(text: str) -> list[str]:
    return INTERNAL_ID.findall(text or "")


def scrub(payload: Any, *, where: str = "") -> Any:
    """`payload` with internal keys removed and internal ids redacted.

    Questions are cut down to `QUESTION_FIELDS`. Returns a new structure; the
    input is not modified. Every removal is logged with `where`, because each
    one is a field that should never have been put in the payload.
    """
    found: list[str] = []
    out = _scrub(payload, found, parent="")
    if found:
        log.warning("customer_safe removed internal data from %s: %s", where or "response",
                    sorted(set(found)))
    return out


def _scrub(value: Any, found: list[str], parent: str) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in INTERNAL_KEYS:
                found.append(k)
                continue
            out[k] = _scrub(v, found, parent=k)
        if parent in ("questions", "skipped_questions") and "fact" in out:
            dropped = set(out) - set(QUESTION_FIELDS)
            if dropped:
                found.extend(sorted(dropped))
                out = customer_question(out)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(v, found, parent=parent) for v in value]
    if isinstance(value, str):
        ids = INTERNAL_ID.findall(value)
        if not ids:
            return value
        found.extend(ids)
        cleaned = INTERNAL_ID.sub("", value)
        # Tidy what the removal leaves: "apply , " / "( )" / doubled spaces.
        cleaned = re.sub(r"\(\s*[,;]?\s*\)", "", cleaned)
        cleaned = re.sub(r"\s*,\s*(?=[,.;)]|$)", "", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        return re.sub(r"[ \t]+([,.;:)])", r"\1", cleaned).strip()
    return value


def leaks(payload: Any) -> list[str]:
    """What in `payload` a customer must not see: internal keys at any depth
    and internal ids in any string. Empty means clean. Used by the tests and
    by `assert_clean`."""
    found: list[str] = []

    def walk(v: Any, parent: str) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in INTERNAL_KEYS:
                    found.append(f"key:{k}")
                walk(x, k)
            if parent in ("questions", "skipped_questions") and "fact" in v:
                found.extend(f"question_key:{k}" for k in set(v) - set(QUESTION_FIELDS))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, parent)
        elif isinstance(v, str):
            found.extend(f"id:{i}" for i in INTERNAL_ID.findall(v))

    walk(payload, "")
    return found
