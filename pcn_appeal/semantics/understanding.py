"""Customer understanding: free text in, one semantic packet out (Phase 2).

    customer account + notice context + earlier clarification answers
        -> one model reading
        -> UNDERSTOOD            -> a packet the knowledge phase can consume
        -> NEEDS_CLARIFICATION   -> exactly one plain question, asked once

The model decides what the account means and whether that meaning is genuinely
ambiguous. This module decides only what code must decide, and never from the
customer's wording:

  * the packet has the contract's shape, however the model answered;
  * a clarification exists only with status NEEDS_CLARIFICATION, and only one;
  * a clarification is never asked twice, never after the cap, and never if it
    asks about who was driving, asks for legal interpretation, or carries an
    internal id - those become a recorded uncertainty and the account stands as
    understood;
  * an answer is processed together with the original account, never instead.

Nothing here names a knowledge-base module, a legal ground or a claim decision.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

STATE_KEY = "_understanding"

UNDERSTOOD = "UNDERSTOOD"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
STATUSES = (UNDERSTOOD, NEEDS_CLARIFICATION)

# The question source the question authority recognises (see
# engines/question_authority.py): an account that cannot yet be represented is a
# case-integrity matter, not a knowledge-gap one.
SOURCE = "customer_understanding"

# A fact key prefix. Every clarification is answered under `account_clarification_<n>`;
# the answer is read back through `clarification_history`, never promoted to a fact.
FACT_PREFIX = "account_clarification_"

# One clarification is the aim; a second is allowed only for an answer that
# itself opened a new ambiguity. Beyond that the account stands, with what is
# still unclear recorded as uncertainty rather than asked for a third time.
MAX_CLARIFICATIONS = 2

# A question whose words overlap this much with one already asked is the same
# question. Measured on content words, so rephrasing the same enquiry is caught
# and an unrelated question is not.
SAME_QUESTION_OVERLAP = 0.6

_WORD = re.compile(r"[a-z0-9]{3,}")
_FILLER = frozenset({
    "the", "and", "that", "this", "was", "were", "you", "your", "did", "does",
    "with", "for", "from", "have", "has", "had", "are", "not", "can", "could",
    "would", "please", "what", "which", "when", "where", "who", "how", "why",
    "there", "they", "them", "then", "than", "about", "any", "into", "also",
    "mean", "meant", "say", "said", "tell", "clarify",
})

PACKET_CHANNELS = ("concepts", "events", "narrative_atoms", "relationships", "uncertainties")


def _words(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall((text or "").lower()) if w not in _FILLER)


def same_enquiry(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return (a or "").strip().lower() == (b or "").strip().lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= SAME_QUESTION_OVERLAP


# ----------------------------------------------------------------- state
def _state(case) -> dict:
    try:
        held = json.loads((case.raw_answers or {}).get(STATE_KEY) or "{}")
    except Exception:
        return {"asked": []}
    if not isinstance(held, dict):
        return {"asked": []}
    held.setdefault("asked", [])
    return held


def _save(case, state: dict) -> None:
    case.raw_answers[STATE_KEY] = json.dumps(state, default=str)


def clarification_history(case) -> list[dict]:
    """Every clarification put to the customer so far, with the answer if given.
    An answer is read from where the answer endpoint stored it."""
    out = []
    for row in _state(case)["asked"]:
        answer = str((case.raw_answers or {}).get(row.get("fact")) or "").strip()
        out.append({"fact": row.get("fact"), "question": row.get("question"),
                    "ambiguity": row.get("ambiguity"), "answer": answer or None})
    return out


def answered(history: list[dict]) -> list[dict]:
    return [h for h in history if h.get("answer")]


def is_clarification_fact(fact: str) -> bool:
    return str(fact or "").startswith(FACT_PREFIX)


def clarification_answer_texts(case) -> set[str]:
    return {h["answer"] for h in answered(clarification_history(case))}


# --------------------------------------------------------------- the veto
def _banned_reason(question: str) -> Optional[str]:
    """Why a question may not be put to a customer, whatever the model meant."""
    from ..customer_safe import INTERNAL_ID
    from ..engines.question_authority import DRIVER_IDENTITY, LEGAL_INTERPRETATION
    if INTERNAL_ID.search(question):
        return "carries an internal id"
    if DRIVER_IDENTITY.search(question):
        return "asks who was driving"
    if LEGAL_INTERPRETATION.search(question):
        return "asks for a legal interpretation"
    return None


def vet_clarification(raw: Any, history: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """The one clarification the customer may be asked, or why there is none.

    Returns (clarification, None) when it may be asked, otherwise (None, reason).
    """
    if not isinstance(raw, dict):
        return None, "no clarification object"
    question = str(raw.get("question") or "").strip()
    ambiguity = str(raw.get("ambiguity") or "").strip()
    if not question or not ambiguity:
        return None, "clarification lacked a question or the ambiguity it resolves"
    banned = _banned_reason(question)
    if banned:
        return None, f"question {banned}"
    done = answered(history)
    if len(done) >= MAX_CLARIFICATIONS:
        return None, "clarification cap reached"
    for h in history:
        if same_enquiry(question, h.get("question") or "") or (
                h.get("ambiguity") and same_enquiry(ambiguity, h["ambiguity"])):
            return None, "already asked"
    return {"question": question[:400], "ambiguity": ambiguity[:400]}, None


# ---------------------------------------------------------------- packet
def _uncertainties(raw: Any) -> list[dict]:
    out = []
    for row in raw if isinstance(raw, list) else []:
        if isinstance(row, str) and row.strip():
            out.append({"about": row.strip()[:300], "source_text": ""})
        elif isinstance(row, dict):
            about = str(row.get("about") or row.get("description") or row.get("proposition") or "").strip()
            if about:
                out.append({"about": about[:300],
                            "source_text": str(row.get("source_text") or "")[:240]})
    return out


def build_packet(product: dict, history: list[dict], revision: int = 0) -> dict:
    """The semantic packet for this reading. `product` is the normalised model
    product (extract._normalize_product plus the helpers' merge); `history` is
    the clarifications the customer has already ANSWERED. A question still
    waiting for its answer is not history: replaying the same reading must
    reach the same verdict, not call its own pending question a repeat."""
    notes: list[str] = []
    uncertainties = _uncertainties(product.get("uncertainties"))
    live = product.get("semantic_mode") == "LIVE"

    status = str(product.get("status") or "").strip().upper()
    if status not in STATUSES:
        if live:
            notes.append(f"model gave status {status or 'none'!r}, outside the contract; "
                         f"read as {UNDERSTOOD}")
        status = UNDERSTOOD
    clarification = None
    raw = product.get("clarification")

    if status == NEEDS_CLARIFICATION:
        clarification, why = vet_clarification(raw, history)
        if clarification is None:
            notes.append(f"clarification not asked: {why}")
            # What was ambiguous is not lost because it cannot be asked.
            ambiguity = str((raw or {}).get("ambiguity") or "").strip() if isinstance(raw, dict) else ""
            if ambiguity:
                uncertainties.append({"about": ambiguity[:300], "source_text": "",
                                      "origin": "ambiguity_not_asked"})
            status = UNDERSTOOD
    elif raw not in (None, "", {}, []):
        notes.append("a clarification came with status UNDERSTOOD and was dropped")

    if not live:
        # A reading that never reached a model cannot judge ambiguity. Say so.
        notes.append("read without a semantic model; ambiguity not assessed")

    packet = {
        "status": status,
        "ready_for_knowledge": status == UNDERSTOOD,
        "summary": str(product.get("summary") or "").strip()[:600],
        "concepts": [c.as_dict() if hasattr(c, "as_dict") else c
                     for c in product.get("concepts") or []],
        "events": list(product.get("events") or []),
        "narrative_atoms": list(product.get("narrative_atoms") or []),
        "relationships": list(product.get("relationships") or []),
        "uncertainties": uncertainties,
        "clarification": clarification,
        "semantic_mode": product.get("semantic_mode"),
        "clarification_rounds": len(answered(history)),
        "revision": revision,
        "notes": notes,
    }
    return packet


def remember(case, packet: dict) -> dict:
    """Record the packet, and the question if there is one. A question already
    pending for this account keeps its fact key, so what the customer is shown
    does not change underneath them."""
    state = _state(case)
    asked = [row for row in state["asked"]
             if (case.raw_answers or {}).get(row.get("fact"))]  # keep answered ones
    clar = packet.get("clarification")
    if clar:
        fact = f"{FACT_PREFIX}{len(asked) + 1}"
        asked.append({"fact": fact, "question": clar["question"],
                      "ambiguity": clar["ambiguity"]})
        clar["fact"] = fact
    state["asked"] = asked
    state["packet"] = packet
    _save(case, state)
    case.audit.append({
        "event": "customer_understanding",
        "status": packet["status"],
        "ready_for_knowledge": packet["ready_for_knowledge"],
        "semantic_mode": packet.get("semantic_mode"),
        "clarification_rounds": packet["clarification_rounds"],
        "asked": bool(clar),
        "notes": list(packet.get("notes") or [])[:6],
        "counts": {k: len(packet.get(k) or []) for k in PACKET_CHANNELS},
    })
    return packet


def load_packet(case) -> Optional[dict]:
    return _state(case).get("packet")


def pending_question(case) -> list[dict]:
    """The clarification awaiting an answer, as a question candidate, or []."""
    packet = load_packet(case)
    if not packet or packet.get("status") != NEEDS_CLARIFICATION:
        return []
    clar = packet.get("clarification") or {}
    fact = clar.get("fact")
    if not fact or (case.raw_answers or {}).get(fact):
        return []
    return [{"fact": fact, "text": clar["question"], "type": "text", "source": SOURCE}]
