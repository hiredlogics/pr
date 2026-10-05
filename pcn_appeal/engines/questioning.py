"""Answer handling and keeper-safe normalisation.

This used to be ENGINE 2: a regex mapped the customer's words to a ground, the
ground's gating facts were looked up, and a preset question was returned for
each. That chain is gone - `engines/analysis.py` decides what is worth asking,
and writes the question itself.

What remains here are the safeguards that have to hold however a question was
produced:

  Q-01 No question may ask, imply or invite who was driving. Enforced on
       generated text in analysis.py; the banned list still lives in
       questions.yaml.
  Q-06 Every answer becomes a keeper-safe Fact immediately. The customer's raw
       wording stays in the audit store and never reaches the drafter, which is
       why a first-person admission cannot be copied into a letter.
  Q-07 A question is asked at most once.
"""
from __future__ import annotations

import re
from typing import Any

from ..kg.graph import KnowledgeGraph
from ..models import CaseFile, CaseState, Fact, FactSource, FactStatus, SourceKind
from .extraction import normalise_operator_ata

# Minimum calibrated confidence for an LLM-only hint (one the regex floor did
# not also find) to be trusted. Hints only ever widen which QUESTIONS get
# asked (Q-03) - they never let a legal module through the door, since every
# module still has to clear its own use_when/do_not_use_when gate against
# CONFIRMED facts (rules/dsl.py). A false-positive hint costs one extra
# question; it can never produce a wrong legal conclusion.


FIRST_PERSON = [
    (re.compile(r"\bI (paid|was paying)\b", re.I), "a payment was made"),
    (re.compile(r"\bI (broke down|couldn'?t move)\b", re.I), "the vehicle became immobilised"),
    # Specific occupancy phrasing before the generic "I left …" rewrite, so
    # material bay-eligibility accounts survive keeper-safe normalisation.
    (re.compile(
        r"\bI left (?:the )?(kids|children|child|toddler|baby|infant) in (?:the )?(?:car|vehicle)\b",
        re.I), "a child remained in the vehicle"),
    (re.compile(
        r"\b(?:the )?(kids|children|child|toddler|baby|infant) (?:were|was) (?:left )?in (?:the )?(?:car|vehicle)\b",
        re.I), "a child remained in the vehicle"),
    (re.compile(r"\b(I|we) (drove|parked|returned|came back)\b", re.I), "the vehicle"),
    (re.compile(r"\b(I|we) left\b", re.I), "the vehicle left"),
    (re.compile(r"\bmy car\b", re.I), "the vehicle"),
]


def _is_prose_answer(text: str) -> bool:
    """Free-form customer prose vs short closed-form text (codes, labels)."""
    words = str(text or "").strip().split()
    if len(words) >= 6:
        return True
    return len(str(text or "").strip()) > 40 and " " in str(text)


_UNCERTAIN = re.compile(
    r"^\W*(i'?m |i am )?(not sure|unsure|not certain|uncertain|no idea|"
    r"(i )?(don'?t|do not|can'?t|cannot|can not) (know|remember|recall|say)|"
    r"maybe|possibly|perhaps|unknown|n/?a)\b", re.I)
_LEADING_YES = re.compile(r"^\W*(yes|yeah|yep|yup|correct|true|that'?s right|it was|they were)\b", re.I)
_LEADING_NO = re.compile(r"^\W*(no|nope|nah|not|never|none|negative|false)\b", re.I)


def answer_polarity(text: str) -> Any:
    """How a written answer opens: True ("Yes, ..."), False ("No, ..."), or
    None (uncertain, or no stated polarity). Uncertainty is checked first so
    "Not sure" is never read as "No"."""
    t = str(text or "").strip()
    if _UNCERTAIN.search(t):
        return None
    if _LEADING_YES.search(t):
        return True
    if _LEADING_NO.search(t):
        return False
    return None


def keeper_safe_text(s: str) -> str:
    """Deterministic first-pass neutraliser for free-text answers (Q-06).
    Production: LLM rewrite + this regex as a floor + validator as backstop."""
    out = s
    for pat, repl in FIRST_PERSON:
        out = pat.sub(repl, out)
    return out


class QuestionEngine:
    """Answer normalisation only. It asks nothing - see AnalysisEngine."""

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    # ------------------------------------------------------------ answers
    def record_answer(self, case: CaseFile, fact: str, raw: Any) -> None:
        # The question's shape comes from the question that was actually asked.
        # V2 questions are written by the analysis engine, so there is no preset
        # bank to look it up in; the bank is consulted only as a fallback for a
        # fact that came from somewhere else.
        q = next((x for x in case.pending_questions if x.get("fact") == fact), None) \
            or self.kg.question_for(fact) or {"type": "text"}
        case.raw_answers[fact] = str(raw)                       # audit only (Q-06)
        if fact not in case.asked_questions:
            case.asked_questions.append(fact)
        t = q.get("type")
        if t == "bool":
            if isinstance(raw, bool):
                value = raw
            elif str(raw).strip().lower() in ("y", "yes", "true", "1"):
                value = True
            elif _UNCERTAIN.search(str(raw)):
                # "Not sure" is not "No": the fact stays unknown rather than
                # asserting a negative the customer never gave.
                case.state = CaseState.QUESTIONING
                return
            else:
                # Unclear bool answers must not become False by default.
                pol = answer_polarity(raw)
                if pol is None:
                    case.state = CaseState.QUESTIONING
                    return
                value = pol is True
        elif t == "int":
            value = int(raw)
        elif t == "choice":
            options = [str(o) for o in (q.get("options") or [])]
            value = self._match_choice(fact, raw, options)
            if value is None:
                raise ValueError(f"{fact}: {raw!r} not in {options}")
        else:
            # Free-text answers: raw stays in raw_answers for provenance.
            # Prose is INPUT only — engines.account extracts structured facts and
            # professional propositions. Short closed-form text (ATA codes, labels)
            # may remain as the fact value.
            text = str(raw).strip()
            if _UNCERTAIN.search(text):
                # "Not sure" is kept as the raw answer only; stored as a value
                # it would read as a known fact.
                case.state = CaseState.QUESTIONING
                return
            if _is_prose_answer(text):
                # Prose is INPUT only. Do not map UI/raw polarity onto an
                # authoritative fact — semantic understanding → FactManager
                # (assess_material_account) promotes meaning. Unanswered /
                # default UI state must never become False here.
                case.audit.append({
                    "event": "prose_answer_deferred_to_semantic",
                    "fact": fact,
                    "excerpt": text[:240],
                })
                case.state = CaseState.QUESTIONING
                return
            value = keeper_safe_text(text)
            case.put(Fact(
                f"F-{fact}", fact, value, FactStatus.ANSWERED,
                FactSource(SourceKind.ANSWER, f"answer:{fact}"),
            ))
            case.state = CaseState.QUESTIONING
            return
        case.put(Fact(f"F-{fact}", fact, value, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, f"answer:{fact}")))
        # Answering which charge number is the right one settles the conflict the
        # extractor found, exactly as confirming it on the confirmation screen
        # does. Without this the gate stayed shut on an answered question.
        if fact == "pcn_number" and case.get("pcn_conflict"):
            case.put(Fact("F-pcn_conflict", "pcn_conflict", False, FactStatus.DERIVED,
                          FactSource(SourceKind.ANSWER, "answer:pcn_number")))
        case.state = CaseState.QUESTIONING

    @staticmethod
    def _match_choice(fact: str, raw: Any, options: list[str]) -> Optional[str]:
        """Accept exact option, case-insensitive match, or known ATA aliases."""
        if not options:
            return None
        text = str(raw).strip()
        upper = text.upper()
        for opt in options:
            if upper == str(opt).upper():
                return str(opt)
        if fact == "operator_ata":
            mapped = normalise_operator_ata(text)
            if mapped and mapped in options:
                return mapped
            # Options may themselves be long-form; map both sides to codes.
            for opt in options:
                if normalise_operator_ata(opt) and normalise_operator_ata(opt) == mapped:
                    return mapped
        return None
