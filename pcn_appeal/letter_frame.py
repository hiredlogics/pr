"""The fixed frame of every appeal letter, written by code and not by a model.

A letter has two kinds of text. The frame - who it is from and to, the date, the
reference line, the salutation, the opening paragraph, the request to cancel, the
sign-off and the name - is the same in every letter and says nothing about the
case; getting any of it slightly wrong (a dropped block, a re-typed registration,
a driver sentence reworded) is a defect a customer would be embarrassed to post.
The grounds are the other kind: they are about THIS charge, and the model writes
only those, from the locked Claim Plan.

So the frame is here, as constants and small functions. `letterhead.py` supplies
the address blocks, date, reference, salutation and sign-off around the validated
body; this module supplies the opening and closing paragraphs inside it, and the
style rules the validators hold the whole letter to.

A value that was not read is a visible placeholder, never a guess and never a
dropped block, so a letter that cannot be posted as it stands says so.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .models import Draft, DraftSentence

# ---------------------------------------------------------------- placeholders
PLACEHOLDER_NAME = "[YOUR FULL NAME]"
PLACEHOLDER_ADDRESS = "[YOUR ADDRESS]"
PLACEHOLDER_OPERATOR = "[PARKING COMPANY NAME]"
PLACEHOLDER_OPERATOR_ADDRESS = "[OPERATOR APPEALS ADDRESS]"
PLACEHOLDER_PCN = "[PCN NUMBER]"
PLACEHOLDER_REG = "[VEHICLE REGISTRATION]"

# ------------------------------------------------------------- the fixed text
#: In the opening paragraph of every letter, word for word.
DRIVER_SENTENCE = ("I make no admission as to the identity of the driver, "
                   "and I will not be naming the driver.")

#: The one request to cancel, and the one next step. Nothing else in the letter asks.
CLOSING_REQUEST = "I ask you to cancel Parking Charge Notice {pcn} and confirm this in writing."
CLOSING_NEXT_STEP = ("If you reject this appeal, please provide your reasons and the details "
                     "of the independent appeals service I can refer it to.")


def opening(pcn: Optional[str], reg: Optional[str], recipient: bool = False) -> list[str]:
    """The opening paragraph's sentences. `recipient`: the operator wrote to the
    person it says was the driver, who answers as the recipient, not the keeper."""
    pcn = pcn or PLACEHOLDER_PCN
    reg = reg or PLACEHOLDER_REG
    if recipient:
        first = (f"I am writing as the recipient of your letter about Parking Charge Notice "
                 f"{pcn}, vehicle {reg}.")
    else:
        first = (f"I am the registered keeper of vehicle {reg} and I am appealing Parking "
                 f"Charge Notice {pcn}.")
    return [first, DRIVER_SENTENCE]


def closing(pcn: Optional[str]) -> list[str]:
    return [CLOSING_REQUEST.format(pcn=pcn or PLACEHOLDER_PCN), CLOSING_NEXT_STEP]


# The only sentences allowed to name the independent appeals service (VAL-STAGE).
ALLOWED_NEXT_STEPS = (CLOSING_NEXT_STEP,)

# ------------------------------------------------------------------ style rules
#: Words and phrases that never appear in a letter: they are the pipeline talking.
BANNED_PHRASES = re.compile(
    r"\b(?:verified\s+facts?|the\s+system|our\s+records\s+show|rules\s+engine|"
    r"case\s+reference|VRM|material\s+time|in\s+light\s+of\s+the\s+above|"
    r"it\s+is\s+important\s+to\s+note)\b", re.I)

#: The keeper writing about themselves in the third person. The letter is in the
#: first person throughout; what the keeper says is attributed with "I", not "the
#: keeper". ("the registered keeper" in an approved Schedule 4 conclusion is not this.)
THIRD_PERSON_KEEPER = re.compile(
    r"\b(?:the\s+)?(?:registered\s+)?keeper(?:'s|’s)\s+"
    r"(?:account|case|position|understanding|recollection|evidence|information|instructions)\b"
    r"|\b(?:the\s+)?(?:registered\s+)?keeper\s+"
    r"(?:states?|says|reports?|has\s+reported|has\s+told|has\s+explained|explains|"
    r"understands|believes|recalls|contends|maintains|advises|asserts)\b"
    r"|\baccording\s+to\s+(?:the\s+)?(?:registered\s+)?keeper\b", re.I)

#: A request to cancel. Exactly one sentence in a letter may be one.
CANCEL_REQUEST = re.compile(
    r"\bI\s+(?:ask|invite|request|require)\b[^.]{0,80}\bcancel\w*"
    r"|\b(?:is|are)\s+requested\s+to\s+cancel\b"
    r"|\bplease\s+cancel\b", re.I)


def display_reg(facts: dict) -> Optional[str]:
    """The registration exactly as the notice prints it (`vrm_display`), else the
    stored value formatted the usual way. Never the normalised one by itself."""
    def norm(x: Any) -> str:
        return re.sub(r"\s+", "", str(x or "")).upper()

    shown = " ".join(str(facts.get("vrm_display") or "").split())
    v = str(facts.get("vrm") or "").strip()
    # A registration the customer later corrected is not the one the notice printed.
    if shown and (not v or norm(shown) == norm(v)):
        return shown
    return f"{v[:4]} {v[4:]}" if len(v) == 7 and " " not in v else (v or None)


def loose_pattern(display: Optional[str], sep: str = r"[\s\-]*") -> Optional[re.Pattern]:
    """A pattern for `display` with any spacing or dashes between its characters."""
    chars = [re.escape(c) for c in re.sub(r"[\s\-]+", "", display or "")]
    return (re.compile(r"(?<![A-Za-z0-9])" + sep.join(chars) + r"(?![A-Za-z0-9])", re.I)
            if chars else None)


def exact_identifiers(text: str, pcn: Optional[str], reg: Optional[str]) -> str:
    """Restore the PCN number and registration to exactly as displayed wherever a
    sentence re-typed them (different spacing, case or dashes). The displayed form is
    never normalised; a variant of it is put right, not argued with."""
    for shown, sep in ((pcn, r"[\s\-]*"), (reg, r"\s*")):
        pattern = loose_pattern(shown, sep)
        if pattern is not None:
            text = pattern.sub(lambda m: shown if m.group(0) != shown else m.group(0), text)
    return text


# --------------------------------------------------------------- the frame step
_STRUCTURAL = "STRUCTURAL"
_NO_ADMISSION = re.compile(r"\bno\s+admission\b[^.]{0,60}\bidentity\s+of\s+the\s+driver\b", re.I)


def _is_frame_paragraph(para) -> bool:
    return bool(para) and all(set(s.module_refs or []) <= {_STRUCTURAL} for s in para)


def apply_fixed_frame(draft: Draft, facts: dict, fact_refs: Optional[dict] = None,
                      recipient: bool = False) -> Draft:
    """Put the code-written opening first and the code-written closing last.

    What a model wrote as its own opening or closing (a structural paragraph with
    no ground behind it) is dropped, as is a request to cancel in a structural
    sentence anywhere else and any repeat of the driver sentence inside a ground:
    the frame says it once. Ground paragraphs are untouched.
    """
    fact_refs = fact_refs or {}
    pcn = str(facts.get("pcn_number") or "").strip() or None
    reg = display_reg(facts)
    paragraphs = [list(p) for p in (draft.paragraphs or [])]
    # The model's own opening is the first paragraph and its own closing the last, when
    # they are structural (no ground behind them). A structural paragraph in the middle,
    # such as one that names the allegation, is the letter's content and stays.
    if paragraphs and _is_frame_paragraph(paragraphs[0]):
        paragraphs.pop(0)
    while paragraphs and _is_frame_paragraph(paragraphs[-1]):
        paragraphs.pop()
    body: list[list[DraftSentence]] = []
    for para in paragraphs:
        # a driver disclaimer or a request to cancel inside the body is said once, in the frame
        kept = [s for s in para
                if not _NO_ADMISSION.search(s.text or "")
                and not (set(s.module_refs or []) <= {_STRUCTURAL} and CANCEL_REQUEST.search(s.text or ""))]
        if kept:
            for s in kept:
                s.text = exact_identifiers(s.text or "", pcn, reg)
            body.append(kept)

    def sentences(texts, names):
        refs = [fact_refs[n] for n in names if n in fact_refs]
        return [DraftSentence(t, list(refs), [_STRUCTURAL], []) for t in texts]

    paras = ([sentences(opening(pcn, reg, recipient), ("pcn_number", "vrm"))]
             + body
             + [sentences(closing(pcn), ("pcn_number",))])
    return Draft(draft.case_id, paras, draft.attempt,
                 no_ground_reason=draft.no_ground_reason, model=draft.model,
                 prompt_version=draft.prompt_version, section_ownership=draft.section_ownership)
