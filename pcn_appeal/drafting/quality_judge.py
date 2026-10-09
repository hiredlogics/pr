"""Appeal QUALITY judge (client brief 2026-10-09 §8).

Runs after the deterministic validators have passed, and answers a question
they cannot: is this letter actually an argument about THIS charge, or is it a
template that would suit any parking charge?

The division of authority is the point:

  deterministic validators   decide what may be said (law, facts, evidence).
                             Strict, and this judge never relaxes them.
  this judge                 decides how well the approved case was written.
                             It cannot add, remove or substitute a ground - if
                             it thinks one is missing it can only name an id
                             already in the claim plan.

So a low score never changes the legal case; it asks for the SAME facts and the
SAME claim plan to be written better. `verdict()` reports PASS / REWRITE /
WARNING against the brief's thresholds, and the caller decides what to do with
that - `blocking` is False until the client has seen agreement rates, the same
way the shadow judge was introduced.

Off by default. On with QUALITY_JUDGE=1 or AppealPipeline(quality_judge=True).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .. import prompts
from ..models import Draft, RetrievalPack

PASS, REWRITE, WARNING, ERROR = "PASS", "REWRITE", "WARNING", "ERROR"

#: Release thresholds from the brief. A letter below one of these is worth
#: rewriting from the same approved case, not releasing.
THRESHOLDS = {
    "case_specificity": 8,
    "factual_fidelity": 9,
    "legal_ground_alignment": 9,
}

SCORES = ("factual_fidelity", "legal_ground_alignment", "case_specificity",
          "persuasiveness", "clarity", "conciseness", "customer_fact_preservation")


def enabled(flag: Optional[bool] = None) -> bool:
    if flag is not None:
        return bool(flag)
    return (os.getenv("QUALITY_JUDGE") or "").strip().lower() in ("1", "true", "yes", "on")


def _customer_sourced(case) -> dict[str, Any]:
    """Facts that came from the customer's own words.

    The judge needs these separately to tell whether a specific account was
    preserved or flattened into generic wording (brief §7): "I left to get my
    purse and came back" must not become "there may have been more than one
    visit".
    """
    from ..models import SourceKind
    out: dict[str, Any] = {}
    for name, f in (getattr(case, "facts", None) or {}).items():
        if not getattr(f, "usable", False) or name.startswith("_"):
            continue
        kind = getattr(getattr(f, "source", None), "kind", None)
        if kind in (SourceKind.ANSWER, SourceKind.CUSTOMER_FREE_TEXT):
            out[name] = f.value
    return out


def verdict(scores: dict, flags: dict) -> str:
    """PASS, or REWRITE when the brief's release thresholds are not met."""
    if flags.get("sendable_for_any_pcn") is True:
        return REWRITE
    if flags.get("explains_why_cancelled") is False:
        return REWRITE
    if flags.get("unsupported_statements"):
        return REWRITE
    for name, floor in THRESHOLDS.items():
        try:
            if int(scores.get(name, 0)) < floor:
                return REWRITE
        except (TypeError, ValueError):
            return WARNING
    return PASS


class QualityJudge:
    mode = "QUALITY"

    def __init__(self, llm):
        self.llm = llm

    def review(self, draft: Draft, pack: RetrievalPack, case=None) -> dict:
        plan = dict(pack.claim_plan or {})
        approved = list(plan.get("approved") or pack.module_ids or [])
        user = json.dumps({
            "claim_plan": {"approved": approved,
                           "labels": dict(plan.get("labels") or {})},
            "module_propositions": {
                c.get("module_id"): c.get("text")
                for c in pack.context_chunks or [] if c.get("kind") == "module"},
            "verified_facts": pack.verified_facts,
            "customer_account_facts": _customer_sourced(case) if case is not None else {},
            "prohibited_claims": list(pack.prohibited_claims or []),
            "sentences": [{"text": s.text, "module_refs": s.module_refs,
                           "fact_refs": s.fact_refs} for s in draft.sentences()],
        }, default=str)
        try:
            out = self.llm.complete_json(task="appeal_quality",
                                         system=prompts.system("appeal_quality"),
                                         user=user) or {}
        except Exception as exc:
            return {"mode": self.mode, "status": ERROR, "blocking": False,
                    "error": f"{type(exc).__name__}: {exc}"[:200]}
        raw = out.get("scores") or {}
        scores = {}
        for name in SCORES:
            try:
                scores[name] = max(0, min(10, int(raw.get(name))))
            except (TypeError, ValueError):
                scores[name] = None
        flags = {
            "explains_why_cancelled": out.get("explains_why_cancelled"),
            "sendable_for_any_pcn": out.get("sendable_for_any_pcn"),
            "unsupported_statements": [str(x)[:200] for x in
                                       (out.get("unsupported_statements") or [])],
            # A ground the judge says is missing is only meaningful if the plan
            # actually approved it; the judge may not invent one.
            "omitted_grounds": [g for g in (out.get("omitted_grounds") or [])
                                if g in approved],
            "weakened_customer_facts": [str(x)[:200] for x in
                                        (out.get("weakened_customer_facts") or [])],
        }
        return {"mode": self.mode, "status": verdict(scores, flags), "blocking": False,
                "scores": scores, **flags,
                "summary": str(out.get("summary") or "")[:300]}


__all__ = ["QualityJudge", "enabled", "verdict", "THRESHOLDS", "SCORES",
           "PASS", "REWRITE", "WARNING", "ERROR"]
