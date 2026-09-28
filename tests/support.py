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
            return self._analyse(user)
        queued = self.responses.get(task)
        if not queued:
            raise RuntimeError(f"no response queued for task {task!r}")
        return queued.pop(0)

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
        ]
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
