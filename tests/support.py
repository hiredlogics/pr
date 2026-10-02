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
import re
from typing import Any, Optional

from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.legal import findings as legal_findings
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
        if not queued and task == "classification":
            # Intake's neutral classifier. A fixture that scripts no
            # classification is routed exactly as its extraction labels say.
            from pcn_appeal.llm import legacy_classification
            legacy = legacy_classification(self.responses)
            if legacy is not None:
                return legacy
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

        # A residential case leads with the agreement's own words, quoted
        # verbatim and attributed (quote_of), like the approved TemplateDrafter
        # wording - whichever residential module carries the ground.
        lease_clauses = list(data.get("lease_clauses") or [])
        if data.get("primary_route") == "RESIDENTIAL" and lease_clauses and modules:
            c = lease_clauses[0]
            paras.append([{
                "text": (f'Clause {c["clause_ref"]} of the uploaded agreement '
                         f'provides: "{c["text"]}"'),
                "fact_refs": [], "module_refs": [modules[0]],
                "evidence_refs": [c["evidence_id"]], "quote_of": c["evidence_id"],
            }])

        # Timing / module blocks from retrieved wording when present.
        evidence_ids = list(data.get("evidence_refs") or [])
        seen_text: set[str] = set()
        findings_by_module: dict[str, dict] = {}
        for f in data.get("verified_legal_findings") or []:
            m = f.get("legal_module_id")
            if m and m not in findings_by_module:
                findings_by_module[m] = f
        for mid in modules:
            # P6.2: a verified timed finding is argued on its calculation -
            # the exact dates and day count - before any retrieved wording.
            lf = findings_by_module.get(mid)
            if lf:
                sentence = legal_findings.particularised_sentence(lf)
                if sentence:
                    paras.append([{
                        "text": sentence,
                        "fact_refs": [refs[k] for k in ("parking_event_date",
                                                        "notice_issue_date",
                                                        "ntd_date") if k in refs],
                        "module_refs": [mid], "evidence_refs": [], "quote_of": None,
                    }])
            if mid == "KB-LAND-01":
                # Only emit when CI selected it (already in module_ids).
                paras.append([{
                    "text": ("The operator is requested to establish that it had sufficient "
                             "authority from the landowner or other entitled party to operate "
                             "and enforce the parking scheme at the location on the material date."),
                    "fact_refs": [], "module_refs": [mid], "evidence_refs": [], "quote_of": None,
                }])
                continue
            clauses = data.get("lease_clauses") or []
            if mid == "KB-RES-06":
                ref = next((c.get("clause_ref") for c in clauses
                            if c.get("has_regulations_power")), None)
                paras.append([{
                    "text": (f"The agreement also contains a provision concerning parking "
                             f"regulations (clause {ref}). The operator is requested to explain "
                             f"whether any permit requirement relied upon was validly made and "
                             f"notified under that provision, and how it is said to qualify the "
                             f"parking right set out above."),
                    "fact_refs": [], "module_refs": [mid], "evidence_refs": [], "quote_of": None,
                }])
                continue
            if mid == "KB-BAY-01":
                obs = facts.get("observation_time")
                evt = facts.get("event_time")
                if obs is not None and evt is not None:
                    paras.append([{
                        "text": (
                            f"The notice records an observation time of {obs} and an event "
                            f"time of {evt}. Whether the conditions of use for this bay were met "
                            f"depends on how the bay was used during the visit, which those "
                            f"recorded times do not themselves show. The operator is requested "
                            f"to produce every photograph and record it relies on, with their "
                            f"timestamps, showing that the conditions of use for the bay were "
                            f"not met."
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
                key = " ".join(str(text).lower().split())
                if key in seen_text:
                    continue
                seen_text.add(key)
                refs_e = list(evidence_ids) if evidence_ids and re.search(
                    r"\b(enclosed|attached|supplied)\b", str(text), re.I) else []
                # If the block claims enclosure but the pack has no evidence refs,
                # drop the enclosure wording so VAL-EVIDENCE does not fire.
                out_text = text
                if not refs_e and re.search(r"\b(enclosed|attached|supplied)\b", str(text), re.I):
                    out_text = re.sub(
                        r"\s*(to the enclosed evidence|the enclosed evidence and|"
                        r"enclosed evidence[, ]*)",
                        " ", str(text), flags=re.I,
                    )
                    out_text = re.sub(r"\s{2,}", " ", out_text).strip()
                from pcn_appeal.drafting.drafter import _SENT
                paras.append([{
                    "text": s, "fact_refs": [], "module_refs": [mid],
                    "evidence_refs": refs_e, "quote_of": None,
                } for s in _SENT.split(out_text) if s.strip()])

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

        # Routes that argue one case theory are drafted as one argument with
        # the shared point stated once - the merge the drafting prompt requires
        # of the model and the TemplateDrafter performs itself.
        from pcn_appeal.drafting.drafter import ONE_ARGUMENT, _restated
        theory_of = {r: i for i, routes in enumerate(ONE_ARGUMENT) for r in routes}

        def theory(para: list[dict]):
            for s in para:
                for m in s.get("module_refs") or []:
                    mod = self.kg.modules.get(m)
                    if mod is not None and theory_of.get(mod.route) is not None:
                        return theory_of[mod.route]
            return None

        merged: list[list[dict]] = []
        at: dict[int, int] = {}
        made: dict[int, set[int]] = {}
        for para in paras:
            t = theory(para)
            if t is None:
                merged.append(para)
                continue
            keep = [s for s in para
                    if not _restated(s["text"], made.setdefault(t, set()))]
            if t in at:
                merged[at[t]] += keep
            elif keep:
                at[t] = len(merged)
                merged.append(keep)
        paras = [p for p in merged if p]

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

        # Near-miss grounds (P7 B1): the production model proposes a ground the
        # ACCOUNT points at even when its gate is not yet satisfied; analysis
        # then suppresses it as UNLOCKABLE and asks for the gate's missing
        # facts. The stand-in approximates that reading deterministically with
        # the account rules themselves: a candidate (already relevance-filtered
        # by the retriever) whose gate is not met but whose gating facts
        # include one the customer's words implicate. A wrong proposal costs a
        # suppressed ground, never a wrong letter - exactly as in production.
        from pcn_appeal.engines.account import _RULES
        circumstances = str(data.get("circumstances") or "")
        implied = {r.fact_name for r in _RULES if r.pattern.search(circumstances)}
        implied |= {f for f, v in facts.items()
                    if v is True and f in {r.fact_name for r in _RULES}}
        chosen = {m.module_id for m in supported}
        near_miss = []
        for m in self.kg.active_modules():
            if m.module_id not in offered or m.module_id in chosen or not implied:
                continue
            if isinstance(m.use_when, dict) and m.use_when.get("always") is True:
                continue
            if evaluate(m.do_not_use_when, facts) or evaluate(m.use_when, facts):
                continue
            if not (self.kg.gating_facts(m.module_id) & implied):
                continue
            near_miss.append(m)
        near_miss.sort(key=lambda m: (-m.strength, m.module_id))

        return {
            "grounds": [{"module_id": m.module_id,
                         "supported_by": sorted(m.required_facts or []),
                         "note": "use_when satisfied"} for m in supported]
            + [{"module_id": m.module_id,
                "supported_by": sorted(m.required_facts or []),
                "note": "account points at this ground; gate facts missing"}
               for m in near_miss],
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

def demo_asking(questions: list[dict], grounds: Optional[list[str]] = None,
                once: bool = True):
    """A demo client that asks `questions` once, then stops.

    Asking on every round would loop forever: the pipeline re-analyses after each
    answer, so a client that always asks would never finish. `once=False` keeps
    re-asking the still-unanswered ones instead - the real model's behaviour
    when a question it wants was shown later than another (one at a time) -
    which terminates because every round filters out answered facts.
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
                if once:
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
