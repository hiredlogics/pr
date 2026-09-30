"""Test doubles for the case-analysis model.

V2 has one substantive authority: a model that reads the notice and decides which
approved grounds the evidence supports. That is the right production design and
the wrong thing to put inside a deterministic test suite - the scenarios would
then assert what a model happened to say on the day.

`ReferenceAnalysisLLM` stands in for it by evaluating the KB's own `use_when`
gates. This is explicitly NOT how production behaves. It exists so the scenario
suite tests everything downstream of the decision - the deterministic vetoes,
KB-GOV-07 ordering, block evidence gates, drafting, Engine 4 - against a stable,
defensible choice of grounds. Whether the real model chooses well is a different
question, answered by running it against real notices.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.rules.dsl import evaluate


class ReferenceAnalysisLLM:
    """Queued responses per task, with `case_analysis` computed from the KB.

    Behaves like FakeLLM for every other task, so existing fixtures keep working.
    """

    def __init__(self, responses: Optional[dict[str, list[dict]]] = None,
                 kg: Optional[KnowledgeGraph] = None,
                 ask: Optional[list[dict]] = None):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.kg = kg or KnowledgeGraph()
        # Questions the stand-in should claim are material, when a scenario is
        # about the asking rather than the letter.
        self.ask = list(ask or [])
        self.calls: list[dict] = []

    def complete_json(self, *, task, system, user, images=None):
        self.calls.append({"task": task, "user": user})
        if task == "case_analysis":
            queued = self.responses.get("case_analysis")
            if queued:
                return queued.pop(0)
            return self._analyse(user)
        if task == "drafting":
            queued = self.responses.get(task)
            if queued:
                return queued.pop(0)
            return self._draft(user)
        if task == "validation":
            queued = self.responses.get(task)
            if queued:
                return queued.pop(0)
            return {"issues": []}
        queued = self.responses.get(task)
        if not queued:
            raise RuntimeError(f"no response queued for task {task!r}")
        return queued.pop(0)

    def _draft(self, payload: str) -> dict:
        """Deterministic letter from the pack the pipeline already finalized.

        Not a TemplateDrafter substitute after AI failure: this is the test
        double's drafting response so Demo/Fake paths exercise validation.
        """
        data = json.loads(payload) if isinstance(payload, str) else payload
        facts = data.get("verified_facts") or {}
        ctx = data.get("case_context") or {}
        modules = list(data.get("module_ids") or [])
        refs = data.get("fact_refs") or {}
        chunks = data.get("context_chunks") or []

        paras: list[list[dict]] = []
        vrm = facts.get("vrm") or ctx.get("vrm") or "the vehicle"
        pcn = facts.get("pcn_number") or ctx.get("pcn_number") or "the notice"
        paras.append([{
            "text": (f"I write as the registered keeper of vehicle {vrm} in respect of "
                     f"Parking Charge Notice {pcn}. I dispute liability for this parking "
                     f"charge and require the operator to consider this appeal."),
            "fact_refs": [refs[k] for k in ("vrm", "pcn_number") if k in refs],
            "module_refs": ["STRUCTURAL"], "evidence_refs": [], "quote_of": None,
        }])

        breach = str(facts.get("alleged_breach") or ctx.get("alleged_breach") or "").strip()
        location = str(facts.get("parking_location") or ctx.get("parking_location") or "").strip()
        if breach:
            where = f" at {location}" if location else ""
            paras.append([{
                "text": f"The Parking Charge Notice alleges: {breach}{where}.",
                "fact_refs": [refs[k] for k in ("alleged_breach",) if k in refs],
                "module_refs": ["STRUCTURAL"], "evidence_refs": [], "quote_of": None,
            }])

        # Timing / module blocks from retrieved wording when present.
        for mid in modules:
            if mid == "KB-LAND-01":
                # Only emit when CI selected it (already in module_ids).
                paras.append([{
                    "text": ("The operator is requested to establish that it had sufficient "
                             "authority from the landowner or other entitled party to operate "
                             "and enforce the parking scheme at the location on the material date."),
                    "fact_refs": [], "module_refs": [mid], "evidence_refs": [], "quote_of": None,
                }])
                continue
            if mid == "KB-BAY-01":
                obs = facts.get("observation_time")
                evt = facts.get("event_time")
                if obs is not None and evt is not None:
                    paras.append([{
                        "text": (
                            f"The operator's records show an observation time of {obs} and an "
                            f"event time of {evt}. A restriction of this kind turns on who was "
                            f"using the bay over the course of the visit, so a record spanning "
                            f"only that interval does not of itself establish the alleged breach. "
                            f"The operator is requested to produce the evidence on which it "
                            f"concluded that the conditions of use for the bay were not met."
                        ),
                        "fact_refs": [refs[k] for k in ("observation_time", "event_time",
                                                       "restricted_bay_alleged") if k in refs],
                        "module_refs": [mid], "evidence_refs": [], "quote_of": None,
                    }])
                continue
            if mid == "KB-BAY-02":
                prop = (ctx.get("material_account_proposition")
                        or (ctx.get("material_account_propositions") or [None])[0]
                        or "the keeper's account is inconsistent with the allegation")
                paras.append([{
                    "text": (
                        f"Further, the information available to the registered keeper indicates "
                        f"that {prop}. That is inconsistent with the factual premise of the "
                        f"allegation that the bay's conditions of use were unmet. The operator "
                        f"is requested to identify the evidence relied upon to conclude otherwise."
                    ),
                    "fact_refs": [refs[k] for k in (
                        "account_contradicts_allegation", "material_account_proposition",
                        "child_occupant_present") if k in refs],
                    "module_refs": [mid], "evidence_refs": [], "quote_of": None,
                }])
                continue
            block_texts = [
                c.get("text") for c in chunks
                if c.get("kind") == "block" and c.get("module_id") == mid and c.get("text")
            ]
            for text in block_texts[:2]:
                paras.append([{
                    "text": text, "fact_refs": [], "module_refs": [mid],
                    "evidence_refs": [], "quote_of": None,
                }])

        # Factual rebuttal if account contradicts but BAY-02 was not selected
        # (still must not paste customer free text — professional proposition only).
        if "KB-BAY-02" not in modules:
            rebuttal = ctx.get("factual_rebuttal") or {}
            props = list(ctx.get("material_account_propositions") or [])
            if not props and ctx.get("material_account_proposition"):
                props = [ctx.get("material_account_proposition")]
            if rebuttal.get("account_contradicts_allegation") and props:
                prop = props[0]
                paras.append([{
                    "text": (
                        f"Further, the information available to the registered keeper indicates "
                        f"that {prop}. That is inconsistent with the factual premise of the "
                        f"allegation that the bay's conditions of use were unmet. The operator "
                        f"is requested to identify the evidence relied upon to conclude otherwise."
                    ),
                    "fact_refs": [refs[k] for k in (
                        "account_contradicts_allegation", "material_account_proposition",
                        "child_occupant_present") if k in refs],
                    "module_refs": ["STRUCTURAL"],
                    "evidence_refs": [], "quote_of": None,
                }])

        if len(paras) <= 2 and not modules:
            return {"paragraphs": [], "no_ground_reason": "no finalized claims to draft"}

        paras.append([{
            "text": ("For the reasons set out above, the parking charge is disputed and the "
                     "operator is requested to cancel the Parking Charge Notice."),
            "fact_refs": [], "module_refs": ["STRUCTURAL"], "evidence_refs": [], "quote_of": None,
        }])
        return {"paragraphs": paras, "no_ground_reason": None}


    # ------------------------------------------------------------------ analysis
    def _analyse(self, payload: str) -> dict[str, Any]:
        data = json.loads(payload)
        facts: dict[str, Any] = data.get("facts") or {}
        offered = {c["module_id"] for c in (data.get("candidates") or [])}

        supported = [
            m for m in self.kg.active_modules()
            if m.module_id in offered
            and evaluate(m.use_when, facts)
            and not evaluate(m.do_not_use_when, facts)
            # Always-on fillers are eligible candidates but not auto-selected by
            # this stand-in — production CI must choose them deliberately.
            and not (isinstance(m.use_when, dict) and m.use_when.get("always") is True)
        ]
        # Bounded reassessment: include omitted gate-satisfied candidates the
        # claim plan asked the model to reconsider.
        reassessment = data.get("reassessment") or {}
        for mid in reassessment.get("omitted_gate_satisfied") or []:
            m = self.kg.modules.get(mid)
            if not m or m.status != "ACTIVE" or m.module_id not in offered:
                continue
            if any(x.module_id == mid for x in supported):
                continue
            if evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts):
                supported.append(m)
        # Strongest first. Production orders by KB-GOV-07 afterwards regardless,
        # so this only has to be stable, not clever.
        supported.sort(key=lambda m: (-m.strength, m.module_id))

        return {
            "grounds": [{"module_id": m.module_id,
                         "supported_by": sorted(m.required_facts or []),
                         "note": "use_when satisfied"} for m in supported],
            "questions": list(self.ask),
            "not_supported": [],
        }


def analysis_llm(extraction: dict, ask: Optional[list[dict]] = None,
                 **other: list[dict]) -> ReferenceAnalysisLLM:
    """The common shape: one extraction response, analysis computed, questions optional."""
    responses = {"extraction": [extraction]}
    responses.update(other)
    return ReferenceAnalysisLLM(responses, ask=ask)


# ------------------------------------------------------- modules under review
# A module's `status` is the KB's own switch: ACTIVE means approved for use,
# REVIEW means awaiting legal sign-off. `active_modules()` filters to ACTIVE, so
# a module at REVIEW cannot be offered to case analysis, selected, retrieved or
# drafted - deliberately, since an unapproved proposition in a customer's letter
# is exactly the failure the status field exists to prevent.
#
# Some scenarios below are about a ground that is currently at REVIEW. They
# assert both halves of that, rather than being deleted or pinned to one status:
# while it is under review it must appear nowhere, and once it is approved it
# must reach the letter. So neither the approval nor the test has to remember
# the other.

def is_approved(module_id: str, kg: Optional[KnowledgeGraph] = None) -> bool:
    """Whether the KB currently approves this module for use in a letter."""
    mod = (kg or KnowledgeGraph()).modules.get(module_id)
    return bool(mod and mod.status == "ACTIVE")


def assert_absent_while_under_review(test, module_id: str, *, module_ids,
                                     draft=None, letter=None) -> None:
    """A module at REVIEW reached nothing: not the selection, not the draft."""
    test.assertNotIn(module_id, list(module_ids or []),
                     f"{module_id} is status REVIEW and must not be selected")
    if draft is not None:
        test.assertFalse(any(module_id in s.module_refs for s in draft.sentences()),
                         f"{module_id} is status REVIEW and must not be cited in a draft")
    if letter:
        test.assertNotIn(module_id, letter)


# --------------------------------------------------------------------- API tests
# Tests that drive the HTTP surface get whatever `default_client()` returns, which
# is the demo reader. It reads labelled text well enough, but it cannot judge what
# is material so it never asks anything - and a test about the question flow needs
# questions. This subclass keeps the demo extractor and supplies them.

def demo_asking(questions: list[dict], grounds: Optional[list[str]] = None):
    """A demo client that asks `questions` once, then stops.

    Asking on every round would loop forever: the pipeline re-analyses after each
    answer, so a client that always asks would never finish.
    """
    from pcn_appeal.llm import DemoLLM

    class _Asking(DemoLLM):
        def __init__(self):
            super().__init__()
            self._asked = False

        def _reference_analysis(self, payload: str) -> dict[str, Any]:
            out = super()._reference_analysis(payload)
            if grounds is not None:
                out["grounds"] = [{"module_id": g, "supported_by": [], "note": "fixture"}
                                  for g in grounds]
            if not self._asked:
                self._asked = True
                asked = json.loads(payload).get("facts") or {}
                out["questions"] = [q for q in questions if q["fact"] not in asked]
            return out

    return _Asking()


def patch_client(test, client) -> None:
    """Point the API at `client` for the duration of one test."""
    from unittest import mock
    patcher = mock.patch("pcn_appeal.api.default_client", return_value=client)
    patcher.start()
    test.addCleanup(patcher.stop)
