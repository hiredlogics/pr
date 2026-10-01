"""Narrative hypotheses: what the customer's account might mean, kept apart
from what is known.

A hypothesis is a possible value for a fact, read from the customer's own
words ("I left and came back" -> possibly `multiple_visits = True`). It is not
a fact. It lives in `case.fact_hypotheses`, never in the Fact Graph, so it
cannot reach `case.get`, `case.fact_view()`, the reasoning gate (R-03), the
retrieval pack, drafting or validation. The only thing a hypothesis can do is
ask the customer one question. The answer is written to the graph as a
confirmed fact through FactManager, like any other answer, and that fact - not
the hypothesis - is what reasoning sees.

  UNCONFIRMED  proposed from the account; may be asked about
  CONFIRMED    the customer's answer matched the possible value
  REJECTED     the customer's answer contradicted it
  SUPERSEDED   a confirmed, document or evidence value for the fact already
               exists: the hypothesis is recorded, never asked, never used
  WITHDRAWN    the account no longer supports it (the customer re-wrote it)

Priority (spec P2.5): a hypothesis is below every fact. It never overrides
one, because it is never written to the graph at all.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .models import CaseFile, Fact, SourceKind

UNCONFIRMED, CONFIRMED, REJECTED = "UNCONFIRMED", "CONFIRMED", "REJECTED"
SUPERSEDED, WITHDRAWN = "SUPERSEDED", "WITHDRAWN"
OPEN = (UNCONFIRMED,)
STATUSES = (UNCONFIRMED, CONFIRMED, REJECTED, SUPERSEDED, WITHDRAWN)


@dataclass(frozen=True)
class Kind:
    """What a hypothesis about one fact asks, and why (admin trace only).

    `reason` and `possible_impact` are internal: the customer sees only the
    question's `fact`, `text` and `type` (customer_safe.customer_question).
    They carry no KB ids or module names.
    """
    fact_name: str
    question: str
    qtype: str
    reason: str
    possible_impact: str
    # What the account says once the customer has confirmed it, for the drafter
    # (the proposition the free-text rule used to assert unconfirmed).
    proposition: str = ""


KINDS: dict[str, Kind] = {
    "multiple_visits": Kind(
        "multiple_visits",
        "Did the vehicle leave the car park and return later that day?",
        "bool",
        "Could change ANPR interpretation",
        "Determines whether continuous stay is valid",
        "the vehicle attended the site more than once on the material date",
    ),
}


def view_name(fact_name: str) -> str:
    """How a hypothesis is named in the trace: possible_<fact>."""
    return f"possible_{fact_name}"


def _key(fact_name: str, value: Any) -> str:
    return f"{fact_name}={json.dumps(value, sort_keys=True, default=str)}"


def _settled_fact(case: CaseFile, fact_name: str) -> Optional[Fact]:
    """A usable fact the hypothesis must not be asked over: anything but an
    unconfirmed reading of the same free text."""
    f = case.facts.get(fact_name)
    if f is None or not f.usable or f.value in (None, "", "UNKNOWN"):
        return None
    if f.source.kind == SourceKind.CUSTOMER_FREE_TEXT:
        return None
    return f


class Hypotheses:
    """All hypothesis writes. Stateless: the records live on the case."""

    # ------------------------------------------------------------ propose
    @staticmethod
    def propose(case: CaseFile, fact_name: str, value: Any, *, source_text: str,
                confidence: float, signals: list[str], rule: str) -> dict:
        """Record a possible value. Idempotent: the same fact and value is the
        same hypothesis (same id), however often the account is re-read."""
        from .fact_graph import now, plain
        kind = KINDS[fact_name]
        key = _key(fact_name, plain(value))
        h = next((x for x in case.fact_hypotheses if x["_key"] == key), None)
        settled = _settled_fact(case, fact_name)
        if h is None:
            h = {
                "hypothesis_id": str(uuid.uuid4()), "case_id": case.case_id,
                "fact_name": fact_name, "hypothesis": view_name(fact_name),
                "possible_value": plain(value), "source_text": source_text[:300],
                "confidence": round(float(confidence), 2), "signals": list(signals),
                "rule": rule,
                "required_confirmation_question": {
                    "fact": fact_name, "text": kind.question, "type": kind.qtype},
                "reason": kind.reason, "possible_impact": kind.possible_impact,
                "status": UNCONFIRMED, "asked_at": None, "answer": None,
                "resolved_fact_id": None, "run_id": case.run_id,
                "created_at": now(), "updated_at": now(), "_key": key,
            }
            case.fact_hypotheses.append(h)
            case.audit.append({"event": "hypothesis_created", "hypothesis": h["hypothesis"],
                               "hypothesis_id": h["hypothesis_id"],
                               "possible_value": h["possible_value"],
                               "confidence": h["confidence"], "signals": h["signals"]})
        elif h["status"] == WITHDRAWN:
            h.update(status=UNCONFIRMED, updated_at=now())
            case.audit.append({"event": "hypothesis_reopened",
                               "hypothesis_id": h["hypothesis_id"]})
        if h["status"] == UNCONFIRMED and settled is not None:
            Hypotheses._close(case, h, SUPERSEDED, settled)
        return h

    @staticmethod
    def withdraw_unsupported(case: CaseFile, supported: set[str]) -> None:
        """The account was re-read and no longer supports these."""
        from .fact_graph import now
        for h in case.fact_hypotheses:
            if h["status"] == UNCONFIRMED and h["_key"] not in supported:
                h.update(status=WITHDRAWN, updated_at=now())
                case.audit.append({"event": "hypothesis_withdrawn",
                                   "hypothesis_id": h["hypothesis_id"]})

    # ------------------------------------------------------------ settle
    @staticmethod
    def settle(case: CaseFile, fact: Fact) -> None:
        """Called by FactManager after a fact is written. A customer's answer
        confirms or rejects the open hypothesis about that fact; a document,
        evidence or calculated value supersedes it. Free text settles nothing."""
        if fact.source.kind == SourceKind.CUSTOMER_FREE_TEXT:
            return
        for h in case.fact_hypotheses:
            if h["fact_name"] != fact.name or h["status"] not in OPEN:
                continue
            if fact.value in (None, "", "UNKNOWN"):
                continue
            if fact.source.kind == SourceKind.ANSWER:
                from .fact_graph import same_value
                status = CONFIRMED if same_value(h["possible_value"], fact.value) else REJECTED
            else:
                status = SUPERSEDED
            Hypotheses._close(case, h, status, fact)

    @staticmethod
    def _close(case: CaseFile, h: dict, status: str, fact: Fact) -> None:
        from .fact_graph import now, plain
        h.update(status=status, answer=plain(fact.value),
                 resolved_fact_id=case.facts.node_id(fact.name),
                 resolved_by=fact.source.ref, updated_at=now(), resolved_at=now())
        case.audit.append({"event": f"hypothesis_{status.lower()}",
                           "hypothesis_id": h["hypothesis_id"], "fact": fact.name,
                           "value": plain(fact.value), "source": fact.source.ref})

    # ------------------------------------------------------------ ask
    @staticmethod
    def open(case: CaseFile) -> list[dict]:
        return [h for h in case.fact_hypotheses if h["status"] in OPEN]

    @classmethod
    def questions(cls, case: CaseFile, material: Callable[[str], bool]) -> list[dict]:
        """One question per open hypothesis that could change a ground, asked
        once (Q-07: a question shown is never asked again under any name).
        An unanswered hypothesis stays a hypothesis and is never used."""
        from .fact_graph import now
        out, seen = [], set()
        for h in cls.open(case):
            fact = h["fact_name"]
            if fact in seen or fact in case.asked_questions or not material(fact):
                continue
            seen.add(fact)
            q = dict(h["required_confirmation_question"])
            q.update(target_fact=fact, reason=h["reason"], possible_impact=h["possible_impact"],
                     hypothesis_id=h["hypothesis_id"])
            out.append(q)
            if h["asked_at"] is None:
                h["asked_at"] = now()
                case.audit.append({"event": "hypothesis_question_asked",
                                   "hypothesis_id": h["hypothesis_id"], "fact": fact,
                                   "text": q["text"]})
        return out

    # ------------------------------------------------------------ trace
    @staticmethod
    def trace(case: CaseFile) -> list[dict]:
        """Narrative -> hypothesis -> question -> answer -> final fact, per
        hypothesis. Admin only (/trace, /cases/{id}/facts)."""
        rows = []
        for h in case.fact_hypotheses:
            final = case.facts.get(h["fact_name"])
            used = final is not None and final.usable and \
                final.source.kind != SourceKind.CUSTOMER_FREE_TEXT
            rows.append({
                "hypothesis_id": h["hypothesis_id"],
                "narrative": h["source_text"],
                "hypothesis": f"{h['hypothesis']}={json.dumps(h['possible_value'])}",
                "signals": h["signals"], "confidence": h["confidence"],
                "question_asked": h["required_confirmation_question"]["text"]
                if h["asked_at"] else None,
                "answer": (case.raw_answers or {}).get(h["fact_name"])
                if h["status"] in (CONFIRMED, REJECTED) else None,
                "status": h["status"],
                "final_fact": f"{h['fact_name']}={json.dumps(final.value, default=str)}"
                if used else None,
                "final_fact_source": final.source.ref if used else None,
                "used_for_grounds": False,          # a hypothesis never is; the fact may be
            })
        return rows


def public(h: dict) -> dict:
    return {k: v for k, v in h.items() if not k.startswith("_")}
