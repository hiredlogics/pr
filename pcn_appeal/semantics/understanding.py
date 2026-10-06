"""Customer understanding: free text in, one semantic packet out (Phase 2).

    customer account + notice context + earlier clarification answers
        -> one model reading
        -> UNDERSTOOD            -> ready for knowledge
        -> NEEDS_CLARIFICATION   -> a material ambiguity, one question to ask
        -> UNRESOLVED            -> a material ambiguity no question can settle

The model decides what the account means and whether any ambiguity is material.
This module decides only what code must decide, and never from the customer's
wording:

  * the packet has the contract's shape, however the model answered;
  * a clarification exists only with status NEEDS_CLARIFICATION, and only one;
  * a clarification is never asked twice, never after the cap, and never if it
    asks about who was driving, asks for legal interpretation, or carries an
    internal id;
  * a material ambiguity that cannot be asked about is UNRESOLVED, and stays
    open in the packet. It is never read as understood: the question limit
    stops a loop, it does not certify meaning;
  * an answer is processed together with the original account, never instead.

Nothing here names a knowledge-base module, a legal ground or a claim decision.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

STATE_KEY = "_understanding"

# The three states of a reading.
#   UNDERSTOOD           the meaning is sufficiently clear. Any ambiguity left is
#                        NON_MATERIAL: kept in `uncertainties`, and it changes
#                        nothing about what happened.
#   NEEDS_CLARIFICATION  a MATERIAL ambiguity exists and one useful question can
#                        still be asked.
#   UNRESOLVED           a MATERIAL ambiguity remains and no question can safely
#                        be asked: the question was refused, was a repeat, the
#                        limit was reached, or the customer could not answer.
# Only UNDERSTOOD is ready for knowledge. The question limit stops a loop; it
# never certifies an account the system could not read.
UNDERSTOOD = "UNDERSTOOD"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
UNRESOLVED = "UNRESOLVED"
STATUSES = (UNDERSTOOD, NEEDS_CLARIFICATION, UNRESOLVED)

MATERIAL, NON_MATERIAL, NO_AMBIGUITY = "MATERIAL", "NON_MATERIAL", "NONE"

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


def _non_answer(text: str) -> bool:
    """The customer could not or would not say. Uses the answer layer's own
    definition of an answer that carries no value ("not sure", "don't know"),
    so there is one definition of it, and no wording specific to any case."""
    from ..engines.questioning import _UNCERTAIN
    t = str(text or "").strip()
    return not t or bool(_UNCERTAIN.search(t))


# Why a reading is not ready. Internal codes: never shown to a customer.
NOT_READY_REASONS = (
    "CLARIFICATION_REQUIRED", "CLARIFICATION_EXHAUSTED", "MATERIAL_AMBIGUITY",
    "AMBIGUITY_NOT_ASSESSED", "INVALID_MODEL_RESPONSE",
)


def is_ready(packet: dict) -> bool:
    """The one definition of customer_semantics_ready.

    All three must hold: the account is UNDERSTOOD, a semantic model actually
    assessed it for ambiguity, and no material ambiguity is open. Anything else
    is not ready - including an UNDERSTOOD packet nobody assessed, which says
    only that no question was raised, not that none was needed.

    It governs the customer-account stream alone (see customer_stream_blocked).
    """
    return bool(packet) and packet.get("status") == UNDERSTOOD \
        and packet.get("ambiguity_assessed") is True \
        and not packet.get("open_material_ambiguities")


def not_ready_reason(packet: dict) -> Optional[str]:
    """The diagnostic reason, recomputed from the packet. None when ready."""
    if is_ready(packet):
        return None
    if not packet:
        return "AMBIGUITY_NOT_ASSESSED"
    status = packet.get("status")
    if status == NEEDS_CLARIFICATION:
        return "CLARIFICATION_REQUIRED"
    open_ = packet.get("open_material_ambiguities") or []
    if open_ or status == UNRESOLVED:
        if any(o.get("code") == "CLARIFICATION_EXHAUSTED" for o in open_):
            return "CLARIFICATION_EXHAUSTED"
        return "MATERIAL_AMBIGUITY"
    # UNDERSTOOD, nothing open, but never assessed.
    return "INVALID_MODEL_RESPONSE" if packet.get("semantic_mode") == "LIVE" \
        else "AMBIGUITY_NOT_ASSESSED"


def build_packet(product: dict, history: list[dict], revision: int = 0) -> dict:
    """The semantic packet for this reading. `product` is the normalised model
    product (extract._normalize_product plus the helpers' merge); `history` is
    the clarifications the customer has already ANSWERED. A question still
    waiting for its answer is not history: replaying the same reading must
    reach the same verdict, not call its own pending question a repeat.

    The model says whether an ambiguity is material (NEEDS_CLARIFICATION /
    UNRESOLVED) or not (UNDERSTOOD, with the doubt in `uncertainties`). Code
    only decides whether the question can be asked. If it cannot, the ambiguity
    is still material: the status becomes UNRESOLVED, not UNDERSTOOD.
    """
    notes: list[str] = []
    uncertainties = _uncertainties(product.get("uncertainties"))
    live = product.get("semantic_mode") == "LIVE"
    open_material: list[dict] = []

    said = str(product.get("status") or "").strip().upper()
    assessed = live and said in STATUSES
    if said not in STATUSES:
        if live:
            notes.append(f"model gave status {said or 'none'!r}, outside the contract; "
                         f"read as {UNDERSTOOD}")
        status = UNDERSTOOD
    else:
        status = said
    clarification = None
    raw = product.get("clarification")
    raw_ambiguity = str((raw or {}).get("ambiguity") or "").strip() if isinstance(raw, dict) else ""

    if status in (NEEDS_CLARIFICATION, UNRESOLVED):
        if status == NEEDS_CLARIFICATION:
            clarification, why = vet_clarification(raw, history)
        else:
            why = "the model reported the ambiguity as unresolved"
        if clarification is not None:
            open_material.append({"ambiguity": clarification["ambiguity"],
                                  "reason": "awaiting the customer's answer",
                                  "code": "CLARIFICATION_REQUIRED"})
        else:
            notes.append(f"clarification not asked: {why}")
            status = UNRESOLVED
            open_material.append({
                "ambiguity": raw_ambiguity or str(product.get("summary") or "").strip()[:300]
                or "the model reported a material ambiguity it did not describe",
                "reason": why,
                "code": "CLARIFICATION_EXHAUSTED"
                if why in ("clarification cap reached", "already asked") else "MATERIAL_AMBIGUITY"})
    elif raw not in (None, "", {}, []):
        # The model called the account understood; its own doubt is kept.
        notes.append("a clarification came with status UNDERSTOOD and was dropped")
        if raw_ambiguity:
            uncertainties.append({"about": raw_ambiguity[:300], "source_text": "",
                                  "origin": "non_material_ambiguity"})

    # An answer that carries no value does not settle what it was asked to settle.
    done = answered(history)
    if done and _non_answer(done[-1]["answer"]):
        carried = str(done[-1].get("ambiguity") or "").strip() or "an earlier ambiguity"
        if not any(same_enquiry(carried, o["ambiguity"]) for o in open_material):
            open_material.append({"ambiguity": carried[:300],
                                  "reason": "the customer could not answer",
                                  "code": "MATERIAL_AMBIGUITY"})
        if status == UNDERSTOOD:
            status = UNRESOLVED
            notes.append("the last clarification was not answered, so its ambiguity is still open")

    for o in open_material:
        if not any(same_enquiry(o["ambiguity"], u["about"]) for u in uncertainties):
            uncertainties.append({"about": o["ambiguity"][:300], "source_text": "",
                                  "origin": "material_ambiguity_open"})

    if not live:
        # A reading that never reached a model cannot judge ambiguity. Say so.
        notes.append("read without a semantic model; ambiguity not assessed")

    packet = {
        "status": status,
        "summary": str(product.get("summary") or "").strip()[:600],
        "concepts": [c.as_dict() if hasattr(c, "as_dict") else c
                     for c in product.get("concepts") or []],
        "events": list(product.get("events") or []),
        "narrative_atoms": list(product.get("narrative_atoms") or []),
        "relationships": list(product.get("relationships") or []),
        "uncertainties": uncertainties,
        "clarification": clarification,
        "open_material_ambiguities": open_material,
        "ambiguity": (MATERIAL if open_material
                      else NON_MATERIAL if uncertainties else NO_AMBIGUITY),
        "ambiguity_assessed": assessed,
        "semantic_mode": product.get("semantic_mode"),
        "clarification_rounds": len(done),
        "revision": revision,
        "notes": notes,
    }
    packet["customer_semantics_ready"] = is_ready(packet)
    packet["ready_for_knowledge"] = packet["customer_semantics_ready"]    # earlier name
    packet["not_ready_reason"] = not_ready_reason(packet)
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
    _stamp(packet)   # never trust a stored flag
    state["packet"] = packet
    _save(case, state)
    case.audit.append({
        "event": "customer_understanding",
        "status": packet["status"],
        "ambiguity": packet["ambiguity"],
        "ready_for_knowledge": packet["ready_for_knowledge"],
        "semantic_mode": packet.get("semantic_mode"),
        "clarification_rounds": packet["clarification_rounds"],
        "asked": bool(clar),
        "notes": list(packet.get("notes") or [])[:6],
        "counts": {k: len(packet.get(k) or []) for k in PACKET_CHANNELS},
    })
    return packet


def _stamp(packet: dict) -> dict:
    packet["customer_semantics_ready"] = is_ready(packet)
    packet["ready_for_knowledge"] = packet["customer_semantics_ready"]
    packet["not_ready_reason"] = not_ready_reason(packet)
    return packet


def load_packet(case) -> Optional[dict]:
    return _state(case).get("packet")


def ready_for_knowledge(case) -> bool:
    """Whether the knowledge phase may read this case's account. Recomputed from
    the stored packet, never read from its flag."""
    return is_ready(load_packet(case) or {})


# ------------------------------------------------- the customer-stream gate
def customer_stream_blocked(case) -> Optional[str]:
    """Why the CUSTOMER-ACCOUNT semantic stream may not be used, or None.

    This gate governs one stream only: the concepts, events, atoms and
    relationships read from what the customer wrote, and the facts promoted
    from them. It does not touch the notice's own facts, document analysis,
    verified PoFA findings, timing calculations or any other independently
    verified finding. Those are a second stream, additive and independent.

    Recomputed from the stored packet on every call. A case with no semantic
    reading has nothing to gate.
    """
    packet = load_packet(case)
    if packet is None:
        return None
    return not_ready_reason(packet)


def customer_semantic_raw(case, key: str):
    """`case.raw_answers[key]` for a customer-semantic channel, or None when the
    customer-account stream is blocked. The one read the knowledge, analysis and
    drafting-context consumers use for those channels."""
    if customer_stream_blocked(case):
        return None
    return (getattr(case, "raw_answers", None) or {}).get(key)


def stream_status(case) -> dict:
    """What a trace or an outcome may say about the customer stream."""
    packet = load_packet(case)
    reason = customer_stream_blocked(case)
    return {"applicable": packet is not None,
            "customer_semantics_ready": packet is not None and reason is None,
            "reason": reason,
            "status": (packet or {}).get("status")}


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
