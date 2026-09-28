"""AI case analysis - the V2 replacement for the decision tree.

V1 routed a customer through a fixed chain:

    regex(their words) -> ground -> that ground's gating facts -> preset question

Every step was a lookup, so "kids were in the car" could reach an authorisation
question with no permission anywhere in evidence, and a photographic notice could
reach an ANPR double-visit question with no entry/exit pair in the document.

V2 has one substantive authority: a model reading the notice's own facts and the
customer's words, choosing from the approved knowledge base, and asking only for
a fact that would change the outcome. There is no route list to classify into.

What is deliberately NOT delegated
----------------------------------
The model PROPOSES. Deterministic code still vetoes, because the guarantees the
product rests on cannot be a matter of judgement:

  * a ground the KB forbids for these facts      -> `do_not_use_when`
  * a statutory defect without the calculation   -> legal/pofa.py
  * a Code value without a verified Code version -> legal/code_versions.py
  * a module id that does not exist              -> the KB is closed
  * a question that touches driver identity      -> banned terms
  * anything in the letter itself                -> Engine 4

So a wrong proposal costs a suppressed ground, never a wrong letter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import prompts
from ..kg.graph import KnowledgeGraph
from ..models import CaseFile, KBModule
from ..rules.dsl import evaluate

# A question is a cost to the customer, so the ceiling is low and silence is the
# default. `questions.yaml` still supplies these caps and the banned terms; it no
# longer supplies the questions.
DEFAULT_MAX_QUESTIONS = 4
CANDIDATE_LIMIT = 24

QUESTION_TYPES = {"bool", "int", "choice", "text"}

# Rationale the customer must never see. The client's example was
# "Permission to park defeats the alleged breach outright, so whether it existed
# is the primary fact." A model told not to explain itself will occasionally
# explain itself anyway, so the text is checked rather than trusted.
RATIONALE_MARKERS = (
    "defeats", "alleged breach", "primary fact", "this ground", "the ground",
    "we are looking at", "we're looking at", "because this", "in order to establish",
    "legally", "statutory", "schedule 4", "pofa", "code of practice",
)


@dataclass
class CaseAnalysis:
    """Grounds and questions the pipeline may act on, after vetoes."""
    module_ids: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)

    @property
    def needs_answers(self) -> bool:
        return bool(self.questions)


class AnalysisEngine:
    def __init__(self, kg: KnowledgeGraph, llm, retriever=None,
                 max_questions: int = DEFAULT_MAX_QUESTIONS):
        self.kg = kg
        self.llm = llm
        self.retriever = retriever
        cfg = getattr(kg, "question_cfg", {}) or {}
        self.banned = [t.lower() for t in cfg.get("banned_question_terms", [])]
        self.max_questions = int(cfg.get("max_questions_v2", max_questions))

    # ------------------------------------------------------------------ main
    def analyse(self, case: CaseFile, circumstances: str = "",
                pofa: Any = None, code_version: Optional[str] = None) -> CaseAnalysis:
        facts = case.fact_view()
        candidates = self._candidates(case, circumstances, facts)
        result = CaseAnalysis()
        result.trace.append(f"candidates={len(candidates)} (semantic + metadata filter + rerank)")

        try:
            raw = self.llm.complete_json(
                task="case_analysis", system=prompts.system("case_analysis"),
                user=self._payload(case, circumstances, facts, candidates, pofa, code_version))
        except Exception as exc:
            # No proposal is a safe outcome: the pipeline drafts from what is
            # already proven rather than guessing, and asks nothing.
            case.audit.append({"event": "case_analysis_error", "error": str(exc)})
            result.trace.append(f"case analysis unavailable ({type(exc).__name__}); nothing proposed")
            return result

        proposed = raw.get("grounds") or []
        result.module_ids = self._veto(case, proposed, facts, pofa, code_version, result)
        result.questions = self._safe_questions(case, raw.get("questions") or [], result)

        case.audit.append({
            "event": "case_analysis",
            "proposed": [g.get("module_id") for g in proposed],
            "kept": result.module_ids,
            "suppressed": result.suppressed,
            "asked": [q["fact"] for q in result.questions],
            # Kept for audit only. `material_because` explains the system's
            # reasoning and is stripped before the question reaches a customer.
            "why_asked": {q.get("fact"): q.get("material_because")
                          for q in (raw.get("questions") or []) if q.get("fact")},
            "not_supported": raw.get("not_supported") or [],
        })
        return result

    # ------------------------------------------------------- candidate set
    def _candidates(self, case: CaseFile, circumstances: str,
                    facts: dict[str, Any]) -> list[KBModule]:
        """Approved grounds worth showing the model, by semantic relevance.

        Not filtered by route: that filtering was the V1 branch selector. The
        only exclusions are metadata ones - a module the KB has disabled, or one
        whose jurisdiction cannot apply to this case.
        """
        active = [m for m in self.kg.active_modules()]
        jurisdiction = facts.get("jurisdiction")
        filtered = [m for m in active if self._jurisdiction_ok(m, jurisdiction)]
        if len(filtered) < len(active):
            pass  # recorded by the caller's trace; kept quiet here

        if self.retriever is None:
            return filtered[:CANDIDATE_LIMIT]

        query = " ".join(str(x) for x in (
            case.get("alleged_breach", ""), circumstances,
            case.get("parking_location", ""),
        ) if x)
        allowed = {m.module_id for m in filtered}
        hits = self.retriever.search(query or "parking charge", allowed_ids=allowed, k=CANDIDATE_LIMIT * 2)

        order: list[str] = []
        for d in hits:
            mid = d.meta.get("module_id")
            if mid and mid not in order and mid in allowed:
                order.append(mid)
        by_id = {m.module_id: m for m in filtered}
        ranked = [by_id[mid] for mid in order if mid in by_id]
        ranked += [m for m in filtered if m.module_id not in order]
        return self._rerank(ranked, facts)[:CANDIDATE_LIMIT]

    @staticmethod
    def _jurisdiction_ok(module: KBModule, jurisdiction: Optional[str]) -> bool:
        """Metadata filter. PoFA grounds cannot apply outside England & Wales, so
        offering them in Scotland invites a proposal that must then be vetoed."""
        if not jurisdiction or jurisdiction == "UNKNOWN":
            return True
        pofa_only = any("PoFA" in str(s) for s in (module.legal_basis or []))
        return not (pofa_only and jurisdiction != "ENGLAND_WALES")

    def _rerank(self, modules: list[KBModule], facts: dict[str, Any]) -> list[KBModule]:
        """Metadata rerank: a ground whose required facts are already present is
        more useful to consider than one that would need everything asked."""
        def score(m: KBModule) -> tuple[float, str]:
            required = list(m.required_facts or [])
            have = sum(1 for f in required if facts.get(f) not in (None, "", []))
            coverage = (have / len(required)) if required else 0.5
            return (-coverage, m.module_id)       # id breaks ties deterministically
        return sorted(modules, key=score)

    # --------------------------------------------------------------- payload
    def _payload(self, case: CaseFile, circumstances: str, facts: dict[str, Any],
                 candidates: list[KBModule], pofa: Any, code_version: Optional[str]) -> str:
        return json.dumps({
            "facts": facts,
            "evidence": [{"evidence_id": e.evidence_id, "kind": e.kind, "filename": e.filename,
                          "has_text": bool((e.text or "").strip()),
                          "page_images": len(getattr(e, "images", []) or [])}
                         for e in case.evidence.values()],
            "circumstances": circumstances,
            "candidates": [{
                "module_id": m.module_id,
                "topic": m.topic,
                "proposition": m.core_proposition,
                "depends_on": sorted(set(list(m.required_facts or []))),
                "prohibited_claims": m.prohibited_claims or [],
            } for m in candidates],
            "pofa": {"route": getattr(pofa, "route", None),
                     "findings": list(getattr(pofa, "findings", []) or [])},
            "code_version": code_version,
            "driver_status": case.driver_status.value,
        }, default=str)

    # ------------------------------------------------------------------ veto
    def _veto(self, case: CaseFile, proposed: list[dict], facts: dict[str, Any],
              pofa: Any, code_version: Optional[str], result: CaseAnalysis) -> list[str]:
        findings = list(getattr(pofa, "findings", []) or [])
        kept: list[str] = []

        for entry in proposed:
            mid = (entry or {}).get("module_id")
            module = self.kg.modules.get(mid)

            if module is None or module.status != "ACTIVE":
                self._suppress(result, mid, "not an active ground in the approved knowledge base")
                continue
            if mid in kept:
                continue
            if evaluate(module.do_not_use_when, facts):
                self._suppress(result, mid, "the knowledge base forbids this ground on these facts")
                continue
            if self._needs_pofa_finding(module) and not findings:
                self._suppress(result, mid, "statutory defect not confirmed by the PoFA calculation")
                continue
            if self._needs_code_version(module) and not code_version:
                self._suppress(result, mid, "no verified Code of Practice version applies")
                continue
            kept.append(mid)

        if not kept:
            result.trace.append("no proposed ground survived the deterministic checks")
        return kept

    @staticmethod
    def _needs_pofa_finding(module: KBModule) -> bool:
        """A ground that alleges a Schedule 4 timing failure. The framing ground
        (keeper liability is not automatic) asserts no defect, so it is exempt."""
        bases = [str(s) for s in (module.legal_basis or [])]
        return any("para" in b and "PoFA" in b for b in bases)

    @staticmethod
    def _needs_code_version(module: KBModule) -> bool:
        return any(str(s).startswith("SCOP-") for s in (module.legal_basis or []))

    def _suppress(self, result: CaseAnalysis, module_id: Optional[str], why: str) -> None:
        result.suppressed.append({"module_id": module_id, "why": why})
        result.trace.append(f"suppressed {module_id}: {why}")

    # ------------------------------------------------------------- questions
    def _safe_questions(self, case: CaseFile, proposed: list[dict],
                        result: CaseAnalysis) -> list[dict]:
        """Keep the model's wording, enforce the rules it was told to follow.

        Only `fact`, `text`, `type` and `options` survive. `material_because` is
        the system's reasoning: it goes to the audit log, never to the customer.
        """
        out: list[dict] = []
        seen = set(case.asked_questions)

        for entry in proposed:
            fact = str((entry or {}).get("fact") or "").strip()
            text = str((entry or {}).get("text") or "").strip()
            qtype = str((entry or {}).get("type") or "text").strip().lower()

            if not fact or not text:
                continue
            if fact in seen or case.has(fact):
                result.trace.append(f"dropped question {fact}: already known or already asked")
                continue
            if qtype not in QUESTION_TYPES:
                qtype = "text"

            low = text.lower()
            if any(b in low for b in self.banned):
                result.trace.append(f"dropped question {fact}: touches driver identity")
                continue
            if any(marker in low for marker in RATIONALE_MARKERS):
                result.trace.append(f"dropped question {fact}: explains its own legal purpose")
                continue

            question: dict[str, Any] = {"fact": fact, "text": text, "type": qtype}
            if qtype == "choice":
                options = [str(o).strip() for o in ((entry or {}).get("options") or []) if str(o).strip()]
                if len(options) < 2:
                    result.trace.append(f"dropped question {fact}: choice with no options")
                    continue
                question["options"] = options

            out.append(question)
            seen.add(fact)
            if len(out) >= self.max_questions:
                break

        return out
