"""The Fact Graph: the one source of truth for a case's facts, and FactManager,
the one way to change it.

Every engine reads facts from the graph (`case.facts`, `case.get`,
`case.fact_view()` - all views of the same nodes). Nothing writes to it except
FactManager: `case.facts` is read-only (models.FactGraph), and `case.put`,
`case.set_status` and `case.retract` delegate here. A write is checked against
the fact's owner and the value already held before it lands, and every write -
applied or not - is recorded:

  fact_history    previous and new value, source, changed_by, reason, time
  fact_sources    every reading of the fact, from every source, and whether it
                  was accepted
  fact_conflicts  a write that was refused, and what happens next:
                    NEEDS_CONFIRMATION  a confident document reading and the
                                        customer disagree; the fact is unusable
                                        until the customer says which is on the
                                        document, and the case does not proceed
                    KEPT_EXISTING       the owner's value stands (the customer's
                                        answer over an AI reading, the evidence
                                        over the account); recorded, not blocking
                    RESOLVED            the customer chose; the chosen value is
                                        confirmed

Graph vocabulary (stored in `facts.status` / `facts.source_type`), mapped from
the engines' own enums so no engine has to change how it labels a write:

  status       CONFIRMED  the customer confirmed, corrected or answered it
               EXTRACTED  read from a document, not yet confirmed
               DERIVED    computed from other facts
               UNKNOWN    unreadable, uncertain, or a placeholder value
               DISPUTED   a contradicting reading was recorded; the owner's stands
               CONFLICT   awaiting the customer's confirmation (NEEDS_CONFIRMATION)
  source_type  DOCUMENT, CUSTOMER_ANSWER, CUSTOMER_FREE_TEXT, EVIDENCE,
               CALCULATION, SYSTEM_DERIVED
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from .fact_ownership import CUSTOMER, DOCUMENT, EVIDENCE, EVIDENCE_OWNED, owner_of
from .hypotheses import Hypotheses
from .models import CaseFile, Fact, FactSource, FactStatus, SourceKind


class GraphStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    EXTRACTED = "EXTRACTED"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"
    DISPUTED = "DISPUTED"
    CONFLICT = "CONFLICT"


class SourceType(str, Enum):
    DOCUMENT = "DOCUMENT"
    CUSTOMER_ANSWER = "CUSTOMER_ANSWER"
    CUSTOMER_FREE_TEXT = "CUSTOMER_FREE_TEXT"
    EVIDENCE = "EVIDENCE"
    CALCULATION = "CALCULATION"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"


NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
KEPT_EXISTING = "KEPT_EXISTING"
RESOLVED = "RESOLVED"

# Outcomes of a write, as fact_history records them.
APPLIED, CONFLICT, IGNORED, RETRACTED = "APPLIED", "CONFLICT", "IGNORED", "RETRACTED"
IGNORED_DUPLICATE = "IGNORED_DUPLICATE"

# P8.7 fact authority. Higher rank cannot be overwritten by a lower write.
DOCUMENT_CONFIRMED = "DOCUMENT_CONFIRMED"
VERIFIED_FINDING_AUTH = "VERIFIED_FINDING"
CUSTOMER_CONFIRMED = "CUSTOMER_CONFIRMED"
CUSTOMER_ASSERTED = "CUSTOMER_ASSERTED"
INFERRED = "INFERRED"
AUTHORITY_RANK = {
    DOCUMENT_CONFIRMED: 50,
    VERIFIED_FINDING_AUTH: 40,
    CUSTOMER_CONFIRMED: 30,
    CUSTOMER_ASSERTED: 20,
    INFERRED: 10,
}

# Values that hold no information.
PLACEHOLDERS = (None, "", "UNKNOWN", [])

# Supporting documents whose readings are EVIDENCE rather than the notice.
EVIDENCE_KINDS = frozenset({"RECEIPT", "LEASE", "PHOTO", "BANK_STATEMENT", "PAYMENT_RECORD",
                            "PERMIT", "TENANCY", "HIRE_AGREEMENT"})

# Derived by an AI reading rather than a deterministic calculator.
_AI_DERIVED_REFS = ("material_account", "analysis", "free_text")

# Names engines and callers use for the same fact. The graph holds one node;
# an alias is a way to read it, never a second copy.
ALIASES = {
    "children_present": "child_occupant_present",
    "driver_disclosure": "driver_disclosure_to_operator",
    "operator": "operator_name",
    "location": "parking_location",
    "event_date": "parking_event_date",
    "issue_date": "notice_issue_date",
    "allegation": "alleged_breach",
}

# How a document-owned fact is named when the customer is asked to confirm it.
LABELS = {
    "pcn_number": "the charge number", "vrm": "the vehicle registration",
    "operator_name": "the parking company", "parking_location": "the location",
    "parking_event_date": "the date of the parking event",
    "notice_issue_date": "the date the notice was issued",
    "charge_amount": "the amount", "alleged_breach": "the reason given for the charge",
    "site_postcode": "the car park postcode", "operator_ata": "the trade association",
}


def canonical(name: str) -> str:
    return ALIASES.get(name, name)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def same_value(a: Any, b: Any) -> bool:
    if a == b:
        return True
    return _norm(a) == _norm(b) if isinstance(a, (str, date)) and isinstance(b, (str, date)) else False


def _norm(v: Any) -> str:
    return "".join(str(plain(v)).split()).upper()


def is_explicit_customer(f: Fact) -> bool:
    """A value the customer settled: confirmed, corrected, or answered to a
    question. An ANSWERED value inferred from free text is a reading of the
    account, not an answer."""
    if f.status in (FactStatus.CONFIRMED, FactStatus.CORRECTED):
        return True
    return f.status == FactStatus.ANSWERED and f.source.kind != SourceKind.CUSTOMER_FREE_TEXT


def is_machine(f: Fact) -> bool:
    return f.status in (FactStatus.EXTRACTED, FactStatus.DERIVED, FactStatus.UNCERTAIN) \
        or f.source.kind == SourceKind.CUSTOMER_FREE_TEXT


def from_customer(f: Fact) -> bool:
    return f.source.kind in (SourceKind.ANSWER, SourceKind.CUSTOMER_FREE_TEXT)


def fact_authority(f: Fact) -> str:
    """P8.7 authority label for a held or incoming fact."""
    kind = f.source.kind
    status = f.status
    ref = (f.source.ref or "").lower()
    if kind == SourceKind.DOCUMENT:
        return DOCUMENT_CONFIRMED
    if kind == SourceKind.CALCULATION and any(
            tok in ref for tok in ("pofa", "legal.", "findings", "verified")):
        return VERIFIED_FINDING_AUTH
    if status in (FactStatus.CONFIRMED, FactStatus.CORRECTED):
        return CUSTOMER_CONFIRMED
    if kind in (SourceKind.ANSWER, SourceKind.CUSTOMER_FREE_TEXT):
        return CUSTOMER_ASSERTED
    return INFERRED


def authority_rank(label: str) -> int:
    return AUTHORITY_RANK.get(label, 0)


def _standing(f: Fact) -> int:
    """Numeric authority for two readings of the same value (P8.7)."""
    return authority_rank(fact_authority(f))


def _explicit_correction(fact: Fact, reason: str) -> bool:
    if fact.status == FactStatus.CORRECTED:
        return True
    low = (reason or "").lower()
    return any(tok in low for tok in ("correction", "conflict_resolved", "customer chose"))


def _may_replace(old: Fact, fact: Fact, reason: str) -> bool:
    """True only for an explicit correction, a customer answer change, a
    customer settling a machine value, or a derived recompute. Account /
    extraction restatements never overwrite."""
    if _explicit_correction(fact, reason):
        return True
    if is_explicit_customer(fact) and is_machine(old):
        return True
    if is_explicit_customer(fact) and fact.source.kind == SourceKind.ANSWER:
        return True
    if old.status == FactStatus.DERIVED and fact.status == FactStatus.DERIVED:
        return True
    return False


def from_document(f: Fact) -> bool:
    return f.source.kind in (SourceKind.DOCUMENT, SourceKind.CALCULATION)


def source_type(case: Optional[CaseFile], f: Fact) -> SourceType:
    kind, ref = f.source.kind, f.source.ref or ""
    if kind == SourceKind.ANSWER:
        return SourceType.CUSTOMER_ANSWER
    if kind == SourceKind.CUSTOMER_FREE_TEXT:
        return SourceType.CUSTOMER_FREE_TEXT
    if kind == SourceKind.CALCULATION:
        return SourceType.SYSTEM_DERIVED if any(t in ref for t in _AI_DERIVED_REFS) \
            else SourceType.CALCULATION
    evidence_id = ref.split("#", 1)[0]
    item = (case.evidence.get(evidence_id) if case is not None else None)
    if f.name in EVIDENCE_OWNED or (item is not None and item.kind in EVIDENCE_KINDS):
        return SourceType.EVIDENCE
    return SourceType.DOCUMENT


def graph_status(case: CaseFile, f: Fact) -> GraphStatus:
    open_states = {c["status"] for c in case.fact_conflicts
                   if c["fact"] == f.name and c["status"] != RESOLVED}
    if NEEDS_CONFIRMATION in open_states:
        return GraphStatus.CONFLICT
    if f.status == FactStatus.UNCERTAIN or f.value in PLACEHOLDERS:
        return GraphStatus.UNKNOWN
    if KEPT_EXISTING in open_states:
        return GraphStatus.DISPUTED
    if f.status in (FactStatus.CONFIRMED, FactStatus.CORRECTED, FactStatus.ANSWERED):
        return GraphStatus.CONFIRMED
    if f.status == FactStatus.EXTRACTED:
        return GraphStatus.EXTRACTED
    return GraphStatus.DERIVED


def default_changed_by(f: Fact) -> str:
    return {SourceKind.DOCUMENT: "extraction", SourceKind.ANSWER: "customer",
            SourceKind.CUSTOMER_FREE_TEXT: "account_engine",
            SourceKind.CALCULATION: "system"}.get(f.source.kind, "system")


@dataclass
class UpdateResult:
    outcome: str                          # APPLIED / CONFLICT / IGNORED / IGNORED_DUPLICATE
    conflict: Optional[dict] = None
    reason: str = ""
    existing_fact_id: Optional[str] = None

    @property
    def applied(self) -> bool:
        return self.outcome == APPLIED


class FactManager:
    """All fact writes. Stateless: the graph lives on the case."""

    # ------------------------------------------------------------ update
    @classmethod
    def update_fact(cls, case: CaseFile, fact: Fact, *, reason: str = "",
                    changed_by: Optional[str] = None) -> UpdateResult:
        """Write `fact` if its source may; otherwise keep the held value and
        record why. The checks, in order:

          1. no fact held, or a placeholder held          -> apply
          2. the customer resolving an open conflict      -> apply, RESOLVED
          3. the same value                               -> IGNORED_DUPLICATE
                                                             (keep source, status,
                                                             confidence; record
                                                             incoming provenance)
          4. a placeholder over a customer's value        -> IGNORED (keep)
          5. who owns the fact, and may this source override it:
               DOCUMENT  a confident reading is not overwritten by the customer:
                         NEEDS_CONFIRMATION
               customer-settled values are not overwritten by a machine
                         reading: KEPT_EXISTING
               EVIDENCE  the account does not overwrite the evidence:
                         KEPT_EXISTING
               CUSTOMER  a document reading does not overwrite what the
                         customer gave: KEPT_EXISTING
          6. otherwise                                    -> apply
        """
        if fact.name != canonical(fact.name):
            fact = Fact(fact.fact_id, canonical(fact.name), fact.value, fact.status,
                        fact.source, fact.confidence)
        changed_by = changed_by or default_changed_by(fact)
        old = case.facts.get(fact.name)

        if old is not None:
            pending = cls.open_conflict(case, fact.name, NEEDS_CONFIRMATION)
            if pending is not None and from_customer(fact) and is_explicit_customer(fact):
                chosen = cls._candidate(pending, fact.value)
                if chosen is not None:
                    return cls._resolve(case, pending, old, fact, chosen, changed_by, reason)
            if same_value(old.value, fact.value):
                # P8.7: same value is never a rewrite. Record the incoming
                # source as extra provenance and leave the held fact alone.
                cls._source(case, fact, accepted=True)
                Hypotheses.settle(case, fact)
                why = reason or "same value already exists"
                if authority_rank(fact_authority(fact)) < authority_rank(fact_authority(old)):
                    why = "Existing fact has higher authority."
                node_id = case.facts.node_id(fact.name) or old.fact_id
                cls._history(case, old, fact, IGNORED_DUPLICATE, why, changed_by)
                case.audit.append({
                    "event": "fact_ignored_duplicate",
                    "existing_fact_id": node_id,
                    "incoming_source": {
                        "kind": fact.source.kind.value, "ref": fact.source.ref,
                        "authority": fact_authority(fact),
                    },
                    "held_authority": fact_authority(old),
                    "reason": why, "name": fact.name,
                })
                return UpdateResult(IGNORED_DUPLICATE, reason=why,
                                    existing_fact_id=node_id)
            elif fact.value in PLACEHOLDERS and old.value not in PLACEHOLDERS \
                    and is_machine(fact) and (is_explicit_customer(old) or from_customer(old)):
                cls._history(case, old, fact, IGNORED, reason or "placeholder_over_customer_value",
                             changed_by)
                cls._source(case, fact, accepted=False)
                return UpdateResult(IGNORED, reason="placeholder_over_customer_value")
            elif old.value not in PLACEHOLDERS and fact.value not in PLACEHOLDERS:
                refused = cls._refusal(old, fact)
                if refused:
                    rule, state = refused
                    return cls._conflict(case, old, fact, rule, state, changed_by, reason)
                if not _may_replace(old, fact, reason):
                    return cls._conflict(case, old, fact, "no_silent_overwrite",
                                         KEPT_EXISTING, changed_by, reason)

        if old is not None and not same_value(old.value, fact.value):
            from .fact_lifecycle import SUPERSEDED, note_version
            note_version(case, old, lifecycle=SUPERSEDED,
                         reason=reason or "value_changed", created_by=changed_by,
                         supersedes=None)
        cls._apply(case, old, fact, changed_by, reason)
        if old is None or not same_value(old.value, fact.value):
            from .fact_lifecycle import ACTIVE, note_version
            prev = None
            if old is not None:
                prev = old.fact_id
            note_version(case, fact, lifecycle=ACTIVE, reason=reason or "fact_set",
                         created_by=changed_by, supersedes=prev)
        return UpdateResult(APPLIED)

    @staticmethod
    def _refusal(old: Fact, new: Fact) -> Optional[tuple[str, str]]:
        owner = owner_of(new.name)
        # A confident reading: an UNCERTAIN one is the customer's to correct. A
        # reading already in conflict stays protected - correcting it twice must
        # not get round the confirmation.
        if owner == DOCUMENT and from_customer(new) and from_document(old) \
                and old.status != FactStatus.UNCERTAIN:
            return "document_owned", NEEDS_CONFIRMATION
        if is_explicit_customer(new):
            return None
        if is_explicit_customer(old) and is_machine(new):
            return "customer_settled", KEPT_EXISTING
        if owner in (DOCUMENT, EVIDENCE) and new.source.kind == SourceKind.CUSTOMER_FREE_TEXT \
                and from_document(old):
            return f"{owner.lower()}_owned", KEPT_EXISTING
        if owner == CUSTOMER and new.source.kind == SourceKind.DOCUMENT and from_customer(old):
            return "customer_owned", KEPT_EXISTING
        return None

    # ------------------------------------------------------------ status / retract
    @classmethod
    def set_status(cls, case: CaseFile, name: str, status: FactStatus, *, reason: str = "",
                   changed_by: str = "system") -> None:
        """Change a held fact's status, not its value (the confirmation screen
        promoting a reading; a cross-check marking one UNCERTAIN)."""
        f = case.facts.get(canonical(name))
        if f is None or f.status == status:
            return
        new = Fact(f.fact_id, f.name, f.value, status, f.source, f.confidence, f.disputed)
        cls._history(case, f, new, APPLIED, reason or "status", changed_by)
        case.facts._write(new)
        case.facts._touch(f.name)

    @classmethod
    def retract(cls, case: CaseFile, name: str, *, reason: str,
                changed_by: str = "system") -> None:
        """Take a fact out of the case. The node and its history are kept (the
        facts row is marked inactive, never deleted)."""
        name = canonical(name)
        old = case.facts.get(name)
        if old is None:
            return
        case.fact_history.append({
            "fact": name, "fact_id": case.facts.node_id(name),
            "previous": plain(old.value), "new": None,
            "previous_status": old.status.value, "status": None,
            "source_kind": None, "source_ref": None, "source_type": None,
            "changed_by": changed_by,
            "reason": reason, "outcome": RETRACTED, "run_id": case.run_id, "at": now()})
        case.facts._remove(name)

    # ------------------------------------------------------------ conflicts
    @staticmethod
    def open_conflict(case: CaseFile, name: str, state: Optional[str] = None) -> Optional[dict]:
        for c in reversed(case.fact_conflicts):
            if c["fact"] == canonical(name) and c["status"] != RESOLVED \
                    and (state is None or c["status"] == state):
                return c
        return None

    @staticmethod
    def needs_confirmation(case: CaseFile) -> list[dict]:
        """Conflicts the case may not proceed past without the customer."""
        seen, out = set(), []
        for c in reversed(case.fact_conflicts):
            if c["status"] == NEEDS_CONFIRMATION and c["fact"] not in seen:
                seen.add(c["fact"])
                out.append(c)
        return list(reversed(out))

    @classmethod
    def confirmation_questions(cls, case: CaseFile) -> list[dict]:
        """One closed question per NEEDS_CONFIRMATION conflict: which of the two
        values is printed on the document. The options are the two values
        actually given, so the answer cannot introduce a third."""
        out = []
        for c in cls.needs_confirmation(case):
            label = LABELS.get(c["fact"], c["fact"].replace("_", " "))
            held, proposed = _shown(c["held_value"]), _shown(c["proposed_value"])
            out.append({
                "fact": c["fact"], "type": "choice", "options": [held, proposed],
                "text": (f"We read {label} on your notice as {held}, but you gave {proposed}. "
                         f"Which one is printed on the notice?"),
            })
        return out

    @classmethod
    def resolve_conflict(cls, case: CaseFile, conflict_id: str, value: Any, *,
                         changed_by: str, reason: str) -> UpdateResult:
        """Settle a conflict directly (admin console, POST /cases/{id}/facts)."""
        c = next((x for x in case.fact_conflicts if x["conflict_id"] == conflict_id), None)
        if c is None or c["status"] == RESOLVED:
            raise KeyError(conflict_id)
        chosen = cls._candidate(c, value)
        if chosen is None:
            raise ValueError(f"{value!r} is neither value in conflict {conflict_id}")
        old = case.facts.get(c["fact"])
        new = Fact(f"F-{c['fact']}", c["fact"], chosen, FactStatus.CONFIRMED,
                   FactSource(SourceKind.ANSWER, f"resolve:{conflict_id}"))
        return cls._resolve(case, c, old, new, chosen, changed_by, reason)

    # ------------------------------------------------------------ hydrate
    @staticmethod
    def hydrate(case: CaseFile, fact: Optional[Fact], node_id: Optional[str] = None,
                updated_at: Optional[str] = None, created_at: Optional[str] = None,
                name: Optional[str] = None) -> None:
        """Restore a stored fact (store.load). Not a write: no history.
        `fact=None` restores a retracted fact's node id only."""
        if fact is None:
            case.facts._reserve(name, node_id)
            return
        case.facts._write(fact, node_id=node_id, updated_at=updated_at, created_at=created_at)

    # ------------------------------------------------------------ internals
    @classmethod
    def _apply(cls, case, old, fact, changed_by, reason) -> None:
        if old is not None and old.disputed and cls.open_conflict(case, fact.name, NEEDS_CONFIRMATION):
            fact = Fact(fact.fact_id, fact.name, fact.value, fact.status, fact.source,
                        fact.confidence, True)
        case.facts._write(fact)
        case.facts._touch(fact.name)
        cls._history(case, old, fact, APPLIED, reason, changed_by)
        cls._source(case, fact, accepted=True)
        Hypotheses.settle(case, fact)
        # P8.1: a derived fact records what it was derived from, as it lands.
        # The calculator declares its inputs with case_state.derives(...); one
        # that declares nothing still gets a lineage row naming it, which
        # MasterCase.lineage_gaps reports.
        from .case_state import note_derivation
        note_derivation(case, fact)
        case.audit.append({"event": "fact_set", "name": fact.name,
                           "status": fact.status.value, "source": fact.source.ref})

    @classmethod
    def _conflict(cls, case, old, fact, rule, state, changed_by, reason) -> UpdateResult:
        stamp = now()
        held_type, proposed_type = source_type(case, old).value, source_type(case, fact).value
        conflict = {
            "conflict_id": str(uuid.uuid4()), "fact": fact.name,
            "fact_name": fact.name,
            "fact_id": case.facts.node_id(fact.name),
            "held_value": plain(old.value), "proposed_value": plain(fact.value),
            "previous_value": plain(old.value), "new_value": plain(fact.value),
            "held_status": old.status.value, "proposed_status": fact.status.value,
            "held_source_type": held_type, "held_source": old.source.ref,
            "proposed_source_type": proposed_type,
            "proposed_source": fact.source.ref,
            "sources": [
                {"kind": held_type, "ref": old.source.ref,
                 "authority": fact_authority(old)},
                {"kind": proposed_type, "ref": fact.source.ref,
                 "authority": fact_authority(fact)},
            ],
            "timestamps": {"previous": stamp, "new": stamp},
            "rule": rule, "status": state, "resolution_status": state,
            "resolution": None, "resolved_by": None,
            "run_id": case.run_id, "at": stamp, "resolved_at": None,
            # The typed values, so a resolution restores a date as a date.
            "_held": old.value, "_proposed": fact.value,
        }
        case.fact_conflicts.append(conflict)
        cls._history(case, old, fact, CONFLICT, reason or rule, changed_by)
        cls._source(case, fact, accepted=False)
        if state == NEEDS_CONFIRMATION and not old.disputed:
            case.facts._write(Fact(old.fact_id, old.name, old.value, old.status, old.source,
                                   old.confidence, True))
            case.facts._touch(old.name)
        case.audit.append({"event": "fact_conflict",
                           **{k: v for k, v in conflict.items() if not k.startswith("_")}})
        return UpdateResult(CONFLICT, conflict)

    @classmethod
    def _resolve(cls, case, conflict, old, fact, chosen, changed_by, reason) -> UpdateResult:
        status = FactStatus.CONFIRMED if old is not None and same_value(old.value, chosen) \
            else FactStatus.CORRECTED
        new = Fact(fact.fact_id, conflict["fact"], chosen, status, fact.source, fact.confidence)
        conflict.update(status=RESOLVED, resolution=plain(chosen), resolved_by=changed_by,
                        resolved_at=now())
        case.facts._write(new)
        case.facts._touch(new.name)
        cls._history(case, old, new, APPLIED, reason or "conflict_resolved", changed_by)
        cls._source(case, new, accepted=True)
        Hypotheses.settle(case, new)
        case.audit.append({"event": "fact_conflict_resolved", "fact": new.name,
                           "conflict_id": conflict["conflict_id"], "chosen": plain(chosen),
                           "by": changed_by})
        return UpdateResult(APPLIED, conflict)

    @staticmethod
    def _candidate(conflict: dict, value: Any) -> Any:
        for typed in (conflict.get("_held", conflict["held_value"]),
                      conflict.get("_proposed", conflict["proposed_value"])):
            # The confirmation question shows a date as "1 June 2026", so the
            # chosen option comes back in that form.
            if same_value(typed, value) or _norm(typed) == _norm(value) \
                    or _norm(_shown(typed)) == _norm(value):
                return typed
        return None

    @staticmethod
    def _history(case, old, new, outcome, reason, changed_by) -> None:
        case.fact_history.append({
            "fact": new.name, "fact_id": case.facts.node_id(new.name),
            "previous": None if old is None else plain(old.value),
            "new": plain(new.value),
            "previous_status": None if old is None else old.status.value,
            "status": new.status.value,
            "source_kind": new.source.kind.value, "source_ref": new.source.ref,
            "source_type": source_type(case, new).value,
            "changed_by": changed_by, "reason": reason, "outcome": outcome,
            "run_id": case.run_id, "at": now()})

    @staticmethod
    def _source(case, fact, *, accepted: bool) -> None:
        case.fact_sources.append({
            "fact": fact.name, "fact_id": case.facts.node_id(fact.name),
            "source_type": source_type(case, fact).value, "source_ref": fact.source.ref,
            "excerpt": fact.source.excerpt, "value": plain(fact.value),
            "confidence": fact.confidence, "accepted": accepted,
            "run_id": case.run_id, "at": now()})


def _shown(value: Any) -> str:
    d = None
    if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return value
    elif isinstance(value, date):
        d = value
    if d is not None:
        return f"{d.day} {d.strftime('%B %Y')}"
    return str(value)


# ---------------------------------------------------------------- graph view
def node(case: CaseFile, name: str) -> Optional[dict]:
    """A fact as the graph records it (GET /cases/{id}/facts)."""
    name = canonical(name)
    f = case.facts.get(name)
    if f is None:
        return None
    return {
        "fact_id": case.facts.node_id(name), "case_id": case.case_id, "fact_name": name,
        "fact_value": plain(f.value), "source_type": source_type(case, f).value,
        "source_ref": f.source.ref, "status": graph_status(case, f).value,
        "engine_status": f.status.value, "confidence": f.confidence,
        "owner": owner_of(name), "usable": f.usable,
        "created_at": case.facts.created_at(name), "updated_at": case.facts.updated_at(name),
    }


def nodes(case: CaseFile) -> list[dict]:
    return [node(case, n) for n in sorted(case.facts)]


def history_of(case: CaseFile, fact_id: str) -> list[dict]:
    return [h for h in case.fact_history if h.get("fact_id") == fact_id]
