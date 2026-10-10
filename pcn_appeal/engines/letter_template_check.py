"""VAL-TEMPLATE: the letter keeps its fixed frame and its style rules.

The frame is written by code (letter_frame.py), so these checks are the safety net
for the cases the frame cannot prevent by itself: a model sentence that repeats the
request to cancel, a registration re-typed with different spacing inside a ground,
a postcode that is not one the case holds, a banned phrase, or a ground that talks
about ANPR cameras on a windscreen ticket.

Each check returns (message, sentence-or-None). A sentence is named when one
sentence is the problem (so it can be dropped or rewritten); a document-level
failure (the driver sentence missing, two requests to cancel) names none.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .. import allegation
from ..letter_frame import (BANNED_PHRASES, CANCEL_REQUEST, DRIVER_SENTENCE,
                            THIRD_PERSON_KEEPER, display_reg, loose_pattern)

_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")
_ANPR_WORDS = re.compile(r"\b(?:ANPR|automatic\s+number\s+plate|number\s+plate\s+recognition|"
                         r"camera|cameras|VRM\s+matching)\b", re.I)
_PERMIT_WORDS = re.compile(r"\bpermits?\b", re.I)


_loose = loose_pattern


def _postcodes(*values) -> set[str]:
    out: set[str] = set()
    for v in values:
        out |= {m.group(0) for m in _POSTCODE.finditer(str(v or "").upper())}
    return out


def _norm_pc(pc: str) -> str:
    return re.sub(r"\s+", "", pc).upper()


def check(draft, pack) -> list[tuple[str, Optional[str]]]:
    out: list[tuple[str, Optional[str]]] = []
    facts = getattr(pack, "verified_facts", None) or {}
    sentences = [s.text for s in draft.sentences() if (s.text or "").strip()]
    text = " ".join(sentences)

    # 1. the driver sentence, word for word
    if DRIVER_SENTENCE not in text:
        out.append(("The opening paragraph does not carry the fixed sentence about the "
                    "identity of the driver, word for word", None))

    # 2. exactly one request to cancel
    asks = [t for t in sentences if CANCEL_REQUEST.search(t)]
    if len(asks) != 1:
        out.append((f"The letter makes {len(asks)} requests to cancel; it makes exactly one "
                    "(the closing's)", asks[1] if len(asks) > 1 else None))

    # 3. the PCN number and the registration exactly as displayed
    pcn = str(facts.get("pcn_number") or "").strip()
    reg = display_reg(facts)
    for label, shown, pattern in (("PCN number", pcn, _loose(pcn)),
                                  ("registration", reg, _loose(reg or "", r"\s*"))):
        if not shown or pattern is None:
            continue
        if shown not in text:
            out.append((f"The {label} is not printed exactly as displayed ({shown})", None))
        for t in sentences:
            for m in pattern.finditer(t):
                if m.group(0) != shown:
                    out.append((f"The {label} is re-typed as '{m.group(0)}'; it is copied exactly "
                                f"as displayed ({shown})", t))
                    break

    # 4. any postcode in the letter is one the case holds, exactly as held
    held = _postcodes(facts.get("site_postcode"), facts.get("parking_location"),
                      facts.get("keeper_address"), facts.get("operator_address"))
    held_exact = {pc: pc for pc in held}
    held_norm = {_norm_pc(pc) for pc in held}
    for t in sentences:
        for m in _POSTCODE.finditer(t.upper()):
            pc = m.group(0)
            if pc in held_exact:
                continue
            kind = ("re-typed" if _norm_pc(pc) in held_norm else "not one this case holds")
            out.append((f"Postcode {pc} is {kind}; postcodes are copied exactly as given", t))

    # 5. style: banned phrases and the keeper in the third person
    for t in sentences:
        m = BANNED_PHRASES.search(t)
        if m:
            out.append((f"Banned phrase '{m.group(0)}'", t))
        m = THIRD_PERSON_KEEPER.search(t)
        if m:
            out.append((f"The letter is in the first person; '{m.group(0)}' is the keeper "
                        "written about in the third person", t))

    # 6. no wording that does not match the type of notice or the allegation
    route = str(getattr(pack, "pofa_route", "") or "")
    wording = " ".join(str(c.get("text") or "") for c in (getattr(pack, "context_chunks", None) or [])
                       if isinstance(c, dict)).lower()
    cls = allegation.classify(facts.get("alleged_breach"), bool(facts.get("restricted_bay_alleged")))
    for t in sentences:
        if route == "WINDSCREEN" and _ANPR_WORDS.search(t):
            out.append(("Camera / ANPR wording on a windscreen-ticket notice", t))
        if (_PERMIT_WORDS.search(t) and cls not in (None, "PERMIT")
                and not facts.get("permit_held") and "permit" not in wording):
            out.append((f"Permit wording on a notice alleging '{(cls or '').lower()}'", t))
    return out
