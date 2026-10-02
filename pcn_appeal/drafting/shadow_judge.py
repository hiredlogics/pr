"""Second-model judge, SHADOW ONLY (P6 §6).

Reads the draft against the locked Claim Plan and says PASS or WARNING with a
reason ("Unsupported statement found"). Its verdict is recorded - on the draft
version, in the audit and in the case report - and never changes the outcome:
no release is held, no sentence dropped, no retry made because of it. Whether
it ever blocks is a later decision, taken on the agreement rate this records.

It uses the `validation` task: the model routed apart from the drafter's
(llm.DISTINCT_FROM), so the reviewer does not share the writer's blind spots.
The payload carries `review_mode: "shadow"` and the claim plan; the prompt
tells it to report any statement no approved claim supports.

Off by default. On with SHADOW_JUDGE=1 or `AppealPipeline(shadow_judge=True)`.
A judge failure is recorded as ERROR and otherwise ignored.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .. import prompts
from ..models import Draft, RetrievalPack

PASS, WARNING, ERROR = "PASS", "WARNING", "ERROR"


def enabled(flag: Optional[bool] = None) -> bool:
    if flag is not None:
        return bool(flag)
    return (os.getenv("SHADOW_JUDGE") or "").strip().lower() in ("1", "true", "yes", "on")


class ShadowJudge:
    mode = "SHADOW"

    def __init__(self, llm):
        self.llm = llm

    def review(self, draft: Draft, pack: RetrievalPack) -> dict:
        plan = dict(pack.claim_plan or {})
        approved = list(plan.get("approved") or pack.module_ids or [])
        labels = dict(plan.get("labels") or {})
        user = json.dumps({
            "review_mode": "shadow",
            "claim_plan": {"claim_plan_id": plan.get("claim_plan_id"),
                           "approved": approved,
                           "claims": [{"module_id": m, "label": labels.get(m)} for m in approved]},
            "sentences": [{"text": s.text, "module_refs": s.module_refs,
                           "fact_refs": s.fact_refs, "evidence_refs": s.evidence_refs}
                          for s in draft.sentences()],
            "facts": pack.verified_facts,
            "module_propositions": {c.get("module_id"): c.get("text")
                                    for c in pack.context_chunks or [] if c.get("kind") == "module"},
            "driver_status": pack.driver_status,
        }, default=str)
        try:
            out = self.llm.complete_json(task="validation", system=prompts.system("validation"),
                                         user=user)
        except Exception as exc:
            return {"mode": self.mode, "status": ERROR, "reasons": [],
                    "error": f"{type(exc).__name__}: {exc}"[:200]}
        issues = [i for i in (out or {}).get("issues") or [] if isinstance(i, dict)]
        reasons = [{"rule": i.get("rule"), "reason": str(i.get("message") or "")[:200]}
                   for i in issues]
        return {"mode": self.mode, "status": WARNING if reasons else PASS, "reasons": reasons,
                "blocking": False}


__all__ = ["ShadowJudge", "enabled", "PASS", "WARNING", "ERROR"]
