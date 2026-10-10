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
WARNING against the brief's thresholds.

It enforces, within limits that keep it from becoming a second author:

  * A REWRITE verdict sends the draft back to the drafter once more over the SAME
    verified facts, the same locked Claim Plan and the same approved wording, with
    the judge's findings as feedback. A retry can fix the writing; it cannot open a
    new case theory, because nothing it is given has changed.
  * A HARD finding (HARD_FLAGS: invented or unsupported content, an argument outside
    the Claim Plan, an approved conclusion changed, uncertainty turned into
    certainty, a fact strengthened or misattributed, a supported fact dropped, a
    generic letter) that survives the rewrites blocks release. `blocking` is True
    exactly then. A score below its threshold with no hard finding is rewritten but
    never blocks on its own.
  * A judge that cannot be reached (ERROR) never blocks and never passes silently:
    the deterministic validators have already decided what may be said, and the
    audit records that the quality read did not happen.

On for a live provider, off for test doubles. QUALITY_JUDGE=1/0 overrides either
way, and AppealPipeline(quality_judge_enabled=...) overrides the environment.
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


#: Findings that are never a matter of style. Each is a list of short statements the
#: judge makes about specific sentences; any non-empty one fails the draft.
HARD_FLAGS = (
    "unsupported_statements",     # asserts something the inputs do not support
    "ungrounded_arguments",       # an argument the Claim Plan never approved
    "changed_conclusions",        # an approved conclusion widened, narrowed or replaced
    "certainty_inflation",        # uncertainty / allegation / attempt stated as established
    "strengthened_facts",         # a fact restated as a stronger or different fact
    "misattributed_statements",   # source or speaker changed (customer / notice / operator)
    "removed_supported_facts",    # a material supported fact left out
    "weakened_customer_facts",    # a specific customer fact flattened into generic wording
)

#: How many times a draft goes back to the drafter on the judge's say-so. These are
#: rewrites of the same case, so they do not spend the validation attempts.
MAX_REWRITES = 2

_LIVE_PROVIDERS = ("OpenAIClient", "GroqClient", "FallbackClient")


def _is_live(llm) -> bool:
    llm = getattr(llm, "inner", llm)         # through the audit wrapper
    return type(llm).__name__ in _LIVE_PROVIDERS


def enabled(flag: Optional[bool] = None, llm=None) -> bool:
    """Explicit flag, else QUALITY_JUDGE, else on exactly when `llm` is a live provider."""
    if flag is not None:
        return bool(flag)
    env = (os.getenv("QUALITY_JUDGE") or "").strip().lower()
    if env:
        return env in ("1", "true", "yes", "on")
    return llm is not None and _is_live(llm)


def _case_package(pack) -> dict:
    """The drafter's own input for this pack (raw customer wording is never in it)."""
    try:
        from .context import DraftContext
        return DraftContext.from_pack(pack).to_payload()
    except Exception:
        return {}


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


def hard_findings(flags: dict) -> list[str]:
    """The findings that block release, as plain statements."""
    out: list[str] = []
    if flags.get("sendable_for_any_pcn") is True:
        out.append("The letter could be sent unchanged for unrelated charges; it must "
                   "name what happened in this case and why that matters.")
    if flags.get("explains_why_cancelled") is False:
        out.append("The letter does not state the real reason this charge should be cancelled.")
    for name in HARD_FLAGS:
        for item in flags.get(name) or []:
            out.append(f"{name.replace('_', ' ')}: {item}")
    return out


def verdict(scores: dict, flags: dict) -> str:
    """PASS, or REWRITE when a hard finding or a release threshold is not met."""
    if hard_findings(flags):
        return REWRITE
    for name, floor in THRESHOLDS.items():
        try:
            if int(scores.get(name, 0)) < floor:
                return REWRITE
        except (TypeError, ValueError):
            return WARNING
    return PASS


def rewrite_feedback(review: dict) -> list[str]:
    """What the drafter is told, from one review. Findings and scores only: never a
    ground to add, never a module id, never a different case theory."""
    fb = list(hard_findings(review))
    for name, floor in THRESHOLDS.items():
        v = (review.get("scores") or {}).get(name)
        if isinstance(v, int) and v < floor:
            fb.append(f"{name.replace('_', ' ')} scored {v}/10 (needs {floor}+): rewrite the "
                      f"same approved case so this improves.")
    if review.get("summary"):
        fb.append(str(review["summary"]))
    return fb


class QualityJudge:
    mode = "QUALITY"

    def __init__(self, llm):
        self.llm = llm

    def review(self, draft: Draft, pack: RetrievalPack, case=None) -> dict:
        plan = dict(pack.claim_plan or {})
        approved = list(plan.get("approved") or pack.module_ids or [])
        user = json.dumps({
            # Exactly what the drafter was given (verified facts with their source,
            # verified legal findings and their calculations, the draft plan's
            # sections and required particulars, the customer's account as
            # keeper-attributed propositions, approved wording). A letter can only
            # be judged against the package it was written from.
            "case_package": _case_package(pack),
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
        def _list(key, cap=200):
            return [str(x)[:cap] for x in (out.get(key) or [])]

        flags = {
            "explains_why_cancelled": out.get("explains_why_cancelled"),
            "sendable_for_any_pcn": out.get("sendable_for_any_pcn"),
            **{name: _list(name) for name in HARD_FLAGS},
            # A ground the judge says is missing is only meaningful if the plan
            # actually approved it; the judge may not invent one.
            "omitted_grounds": [g for g in (out.get("omitted_grounds") or [])
                                if g in approved],
        }
        status = verdict(scores, flags)
        review = {"mode": self.mode, "status": status,
                  "blocking": bool(hard_findings(flags)),
                  "scores": scores, **flags,
                  "summary": str(out.get("summary") or "")[:300]}
        review["feedback"] = rewrite_feedback(review) if status == REWRITE else []
        return review


__all__ = ["QualityJudge", "enabled", "verdict", "hard_findings", "rewrite_feedback",
           "THRESHOLDS", "SCORES", "HARD_FLAGS", "MAX_REWRITES",
           "PASS", "REWRITE", "WARNING", "ERROR"]
