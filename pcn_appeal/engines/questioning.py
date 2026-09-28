"""ENGINE 2 - Adaptive Questioning.

Flow: one open "what happened?" question -> route hints -> ask only the facts
that unlock or rule out a hinted route, highest value first.

Rule pack
  Q-01 Never ask who was driving or invite a driver admission (banned-term check).
  Q-02 Target <= max_questions (6); hard cap 8 across the whole journey.
  Q-03 Only ask facts that gate a module in a hinted route (graph lookup).
  Q-04 Never ask for a fact already known from documents/answers.
  Q-05 Special-category data minimisation: disability/medical questions are
       yes/no only and only when a hint exists.
  Q-06 Every answer is normalised to a keeper-safe Fact immediately; raw text
       is kept only in the audit store and never reaches the drafter.
  Q-07 A question is asked at most once.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from ..kg.graph import KnowledgeGraph
from ..llm import LLMClient
from ..models import CaseFile, CaseState, Fact, FactSource, FactStatus, SourceKind
from ..rules.dsl import evaluate

# Deterministic floor. The LLM classifier (below) is the primary signal when
# available; this regex still runs unconditionally and its hits are always
# kept, so a bad/missing LLM response degrades to today's behaviour, never
# to zero hints.
HINTS = {
    "PAYMENT": r"\b(paid|pay|payment|app|ticket|machine|card)\b",
    "KEYING": r"\b(registration|reg|typo|wrong (number|reg)|keyed|entered)\b",
    "BREAKDOWN": r"\b(broke down|breakdown|battery|puncture|flat tyre|wouldn'?t start|recovery|aa|rac)\b",
    "RESIDENTIAL": r"\b(live|resident|tenant|lease|flat|my (space|bay)|allocated)\b",
    "ANPR": r"\b(twice|two visits|came back|left and|returned)\b",
    "CONSIDERATION": r"\b(looking for|couldn'?t find|read the sign|left without|didn'?t park)\b",
    "GRACE": r"\b(queue|traffic|barrier|few minutes (late|over)|overstay)\b",
    "AUTHORISATION": r"\b(permit|visitor|permission|whitelist|guest)\b",
    "SIGNAGE": r"\b(sign|signs|signage|hidden|no sign)\b",
    "EQUALITY": r"\b(disab|blue badge|mobility|wheelchair)\b",
}
# Minimum calibrated confidence for an LLM-only hint (one the regex floor did
# not also find) to be trusted. Hints only ever widen which QUESTIONS get
# asked (Q-03) - they never let a legal module through the door, since every
# module still has to clear its own use_when/do_not_use_when gate against
# CONFIRMED facts (rules/dsl.py). A false-positive hint costs one extra
# question; it can never produce a wrong legal conclusion.
ROUTE_CONFIDENCE_THRESHOLD = 0.6

QUESTION_HINT_SYSTEM = """You classify a short, untrusted customer account of a parking event.

Valid routes - use these exact names only, never invent or rename one:
PAYMENT, KEYING, BREAKDOWN, RESIDENTIAL, ANPR, CONSIDERATION, GRACE, AUTHORISATION, SIGNAGE, EQUALITY.

For each route that the text states or clearly implies, give a calibrated confidence in [0,1]:
  0.9-1.0  the account directly describes this route's fact pattern
  0.6-0.89 the account strongly suggests it but is not explicit
  <0.6     weak or speculative - omit these, do not pad the list
Base the score only on what is written. Never score a route higher because a stronger appeal
would need it, and never guess at facts not present in the text.
Omit routes with no support. Return JSON only: {"routes": [{"route": "NAME", "confidence": 0.0}, ...]}
Empty list if none apply. Never output names, driver identity, or anything else."""

FIRST_PERSON = [
    (re.compile(r"\bI (paid|was paying)\b", re.I), "a payment was made"),
    (re.compile(r"\bI (broke down|couldn'?t move)\b", re.I), "the vehicle became immobilised"),
    (re.compile(r"\b(I|we) (drove|parked|left|returned|came back)\b", re.I), "the vehicle"),
    (re.compile(r"\bmy car\b", re.I), "the vehicle"),
]


def keeper_safe_text(s: str) -> str:
    """Deterministic first-pass neutraliser for free-text answers (Q-06).
    Production: LLM rewrite + this regex as a floor + validator as backstop."""
    out = s
    for pat, repl in FIRST_PERSON:
        out = pat.sub(repl, out)
    return out


class QuestionEngine:
    def __init__(self, kg: KnowledgeGraph, llm: Optional[LLMClient] = None):
        self.kg = kg
        self.llm = llm
        cfg = kg.question_cfg
        self.banned = [t.lower() for t in cfg.get("banned_question_terms", [])]
        self.max_q = cfg.get("max_questions", 6)
        self.hard_cap = cfg.get("hard_cap_questions", 8)

    # ------------------------------------------------------------ hints
    def route_hints(self, case: CaseFile, narrative: str) -> set[str]:
        case.raw_answers["narrative"] = narrative            # audit only
        text = f"{narrative} {case.get('alleged_breach', '')}".lower()
        hints = {r for r, pat in HINTS.items() if re.search(pat, text)}  # deterministic floor
        if self.llm:
            try:
                raw = self.llm.complete_json(task="questioning", system=QUESTION_HINT_SYSTEM,
                                             user=narrative).get("routes", [])
                accepted, rejected = set(), []
                for r in raw:
                    name, conf = r.get("route"), float(r.get("confidence", 0))
                    if name not in HINTS:
                        rejected.append(r)                    # defensive: drop hallucinated route names
                    elif conf >= ROUTE_CONFIDENCE_THRESHOLD:
                        accepted.add(name)
                    else:
                        rejected.append(r)
                case.audit.append({"event": "route_classification", "regex": sorted(hints),
                                   "llm_accepted": sorted(accepted), "llm_rejected": rejected})
                hints |= accepted
            except Exception as exc:  # hints are advisory - never block the journey
                case.audit.append({"event": "route_classification_error", "error": str(exc)})
        # uploaded evidence is also a hint
        kinds = {e.kind for e in case.evidence.values()}
        if kinds & {"RECEIPT", "APP_SCREENSHOT", "BANK_STATEMENT"}:
            hints.add("PAYMENT")
        if kinds & {"RECOVERY_REPORT", "GARAGE_INVOICE"}:
            hints.add("BREAKDOWN")
        if kinds & {"LEASE", "TENANCY"}:
            hints.add("RESIDENTIAL")
        case.put(Fact("F-route_hints", "route_hints", sorted(hints), FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "route_hints")))
        return hints

    # ------------------------------------------------------------ selection
    def next_questions(self, case: CaseFile) -> list[dict]:
        hints = set(case.get("route_hints", []))
        budget = min(self.max_q, self.hard_cap - len(case.asked_questions))
        if budget <= 0:
            return []
        facts = case.fact_view()
        value: dict[str, float] = defaultdict(float)
        for m in self.kg.active_modules():
            if m.route not in hints:
                continue                                       # Q-03
            if evaluate(m.do_not_use_when, facts):
                continue
            missing = [f for f in self.kg.gating_facts(m.module_id)
                       if f not in facts and self.kg.question_for(f)]  # Q-04
            for f in missing:
                value[f] += m.strength / len(missing)           # information-value proxy
        ranked = sorted((f for f in value if f not in case.asked_questions), key=lambda f: -value[f])  # Q-07
        out = []
        for f in ranked[:budget]:
            q = dict(self.kg.question_for(f), fact=f)
            assert not any(b in q["text"].lower() for b in self.banned), "Q-01 violated"  # Q-01
            out.append(q)
        return out

    # ------------------------------------------------------------ answers
    def record_answer(self, case: CaseFile, fact: str, raw: Any) -> None:
        q = self.kg.question_for(fact) or {"type": "text"}
        case.raw_answers[fact] = str(raw)                       # audit only (Q-06)
        case.asked_questions.append(fact)
        t = q.get("type")
        if t == "bool":
            value = raw if isinstance(raw, bool) else str(raw).strip().lower() in ("y", "yes", "true", "1")
        elif t == "int":
            value = int(raw)
        elif t == "choice":
            value = str(raw).upper()
            if value not in q.get("options", []):
                raise ValueError(f"{fact}: {raw!r} not in {q['options']}")
        else:
            value = keeper_safe_text(str(raw))
        case.put(Fact(f"F-{fact}", fact, value, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, f"answer:{fact}")))
        case.state = CaseState.QUESTIONING
