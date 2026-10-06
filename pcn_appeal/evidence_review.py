"""What the case knows about a kind of evidence before it concludes anything from it.

A proposition derived from a document has three honest states, not two:

  NOT_PROVIDED   no such evidence is in the case          -> the proposition is UNKNOWN
  NOT_REVIEWED   it was uploaded but nothing was readable -> the proposition is UNKNOWN
  REVIEWED       it was read                              -> TRUE if present, FALSE if absent

Only REVIEWED lets a derivation write FALSE ("the evidence was read and does not
contain it"). A derivation that wrote FALSE for the other two states would turn
"we have not looked" into "it is not there", and a gate that tests the negation
(`do_not_use_when: {is: x}`, `{not: {is: x}}`) would read that as a finding.

What the case *can* state in every state is whether the evidence is in the supplied
set: a fact like `lease_evidence_provided` is a literal statement about the upload,
true or false, never about what the document says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import CaseFile, EvidenceItem, FactStatus

NOT_PROVIDED, NOT_REVIEWED, REVIEWED = "NOT_PROVIDED", "NOT_REVIEWED", "REVIEWED"


@dataclass
class EvidenceReview:
    state: str
    provided: list[EvidenceItem] = field(default_factory=list)
    readable: list[EvidenceItem] = field(default_factory=list)

    @property
    def reviewed(self) -> bool:
        return self.state == REVIEWED


def review(case: CaseFile, kinds: Iterable[str]) -> EvidenceReview:
    """The review state of the evidence of these kinds the case holds right now."""
    kinds = set(kinds)
    provided = [e for e in case.evidence.values() if e.kind in kinds and e.uploaded]
    if not provided:
        return EvidenceReview(NOT_PROVIDED)
    readable = [e for e in provided if (e.text or "").strip()]
    return EvidenceReview(REVIEWED if readable else NOT_REVIEWED, provided, readable)


def withdraw_derived(case: CaseFile, names: Iterable[str], reason: str) -> list[str]:
    """Take back a value this derivation wrote earlier when the evidence it was read
    from is no longer reviewed (removed, or never readable). Only DERIVED facts: a
    value a person gave or confirmed is not the derivation's to withdraw."""
    gone = []
    for name in names:
        held = case.facts.get(name)
        if held is not None and held.status == FactStatus.DERIVED:
            case.retract(name, reason)
            gone.append(name)
    return gone
