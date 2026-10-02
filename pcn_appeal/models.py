"""Core domain models.

Reference implementation uses stdlib dataclasses so it runs anywhere.
In production, mirror these as Pydantic v2 models at the FastAPI boundary
(request/response validation) and as SQLAlchemy tables in Postgres.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from collections.abc import Mapping
from typing import Any, Optional


# --------------------------------------------------------------------------- enums
class FactStatus(str, Enum):
    EXTRACTED = "EXTRACTED"      # read by AI from a document, not yet confirmed
    CONFIRMED = "CONFIRMED"      # customer confirmed on the confirmation screen
    CORRECTED = "CORRECTED"      # customer corrected an extracted value
    ANSWERED = "ANSWERED"        # came from an adaptive-question answer
    DERIVED = "DERIVED"          # computed deterministically (duration, PoFA timing)
    UNCERTAIN = "UNCERTAIN"      # low OCR/LLM confidence - never usable for a defect claim


class SourceKind(str, Enum):
    DOCUMENT = "DOCUMENT"
    ANSWER = "ANSWER"                      # closed-form / choice / bool answers
    CUSTOMER_FREE_TEXT = "CUSTOMER_FREE_TEXT"  # narrative / free-text (input only)
    CALCULATION = "CALCULATION"


class DriverStatus(str, Enum):
    UNIDENTIFIED = "UNIDENTIFIED"
    FORMALLY_IDENTIFIED = "FORMALLY_IDENTIFIED"


class Jurisdiction(str, Enum):
    ENGLAND_WALES = "ENGLAND_WALES"
    SCOTLAND = "SCOTLAND"
    NORTHERN_IRELAND = "NORTHERN_IRELAND"
    UNKNOWN = "UNKNOWN"


class CaseState(str, Enum):
    CREATED = "CREATED"
    EXTRACTED = "EXTRACTED"
    CONFIRMED = "CONFIRMED"
    QUESTIONING = "QUESTIONING"
    ANALYSED = "ANALYSED"
    DRAFTED = "DRAFTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    RELEASED = "RELEASED"
    # Debt-recovery / appeal-window closed: normal appeal drafting must not run.
    NO_APPEAL_RIGHT = "NO_APPEAL_RIGHT"
    # Our own document classifier returned nothing. Distinct from the above
    # because the customer did nothing wrong and the case is retryable, and
    # distinct from proceeding because an unclassified document must not enter
    # the appeal path at all.
    CLASSIFICATION_FAILED = "CLASSIFICATION_FAILED"


# --------------------------------------------------------------------------- facts
@dataclass
class FactSource:
    kind: SourceKind
    ref: str                        # document_id#page / answer_id / calculator name
    excerpt: Optional[str] = None   # verbatim text span (needed for lease quotes)


@dataclass(frozen=True)
class Fact:
    """Every value the drafter may use is a Fact with provenance.

    Facts are stored in keeper-safe neutral form. Raw customer wording is
    kept separately (audit only) and is NEVER shown to the drafter.
    """
    fact_id: str
    name: str
    value: Any
    status: FactStatus
    source: FactSource
    confidence: float = 1.0
    # Set by FactManager while a NEEDS_CONFIRMATION conflict is open: the value
    # is held, but nothing may rely on it until the customer confirms it.
    disputed: bool = False

    @property
    def usable(self) -> bool:
        return self.status != FactStatus.UNCERTAIN and not self.disputed


@dataclass
class EvidenceItem:
    evidence_id: str
    kind: str                      # PCN, NTK, RECEIPT, RECOVERY_REPORT, LEASE, PHOTO ...
    filename: str
    uploaded: bool = True
    text: str = ""                 # OCR / extracted text
    doc_date: Optional[date] = None
    images: list[bytes] = field(default_factory=list)   # JPEG pages for the vision model
    # Where the original file lives, when it was uploaded to object storage
    # rather than read straight from a request body. Without this the document is
    # gone the moment the process ends - the notice a customer's appeal rests on
    # would not be retrievable if they later disputed what we read.
    storage_url: Optional[str] = None


class RunAudit(list):
    """The case audit: a list that stamps each entry with the case's run."""

    def __init__(self, case: Optional["CaseFile"] = None, items=()):
        super().__init__(items)
        self._case = case

    def _stamp(self, entry):
        if isinstance(entry, dict):
            if "run_id" not in entry:
                entry["run_id"] = getattr(self._case, "run_id", 0)
            # P5.5: when it happened, so the execution trace can time each
            # stage. Entries loaded from the store already carry their own.
            if "at" not in entry:
                entry["at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return entry

    def append(self, entry) -> None:
        super().append(self._stamp(entry))

    def insert(self, index, entry) -> None:
        super().insert(index, self._stamp(entry))

    def extend(self, entries) -> None:
        super().extend(self._stamp(e) for e in entries)

    def __iadd__(self, entries):
        self.extend(entries)
        return self


class FactWriteError(TypeError):
    """A write to the fact graph that did not go through FactManager."""


class FactGraph(Mapping):
    """The case's facts: a read-only mapping of name -> Fact.

    Every engine reads through it; only FactManager (fact_graph.py) writes,
    through the underscore methods. Each fact is a node with a stable id
    (`node_id`), kept across updates and reloads, and created/updated times.
    """

    def __init__(self, items: Optional[dict] = None):
        self._data: dict[str, Fact] = {}
        self._ids: dict[str, str] = {}
        self._times: dict[str, list[str]] = {}
        self._gone: dict[str, Fact] = {}
        for f in (items or {}).values() if isinstance(items, dict) else (items or []):
            self._write(f)

    # Mapping ---------------------------------------------------------------
    def __getitem__(self, name: str) -> Fact:
        try:
            return self._data[name]
        except KeyError:
            from .fact_graph import canonical      # an alias reads its canonical fact
            if canonical(name) == name:
                raise
            return self._data[canonical(name)]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FactGraph({self._data!r})"

    # Refused writes ----------------------------------------------------------
    def _refuse(self, *_a, **_k):
        raise FactWriteError("facts are written through FactManager "
                             "(case.put / case.set_status / case.retract)")

    __setitem__ = __delitem__ = pop = popitem = clear = update = setdefault = _refuse

    # FactManager only --------------------------------------------------------
    def _write(self, fact: Fact, node_id: Optional[str] = None,
               updated_at: Optional[str] = None, created_at: Optional[str] = None) -> None:
        import uuid
        self._data[fact.name] = fact
        self._gone.pop(fact.name, None)
        self._ids.setdefault(fact.name, node_id or str(uuid.uuid4()))
        stamp = updated_at or _utc_now()
        self._times.setdefault(fact.name, [created_at or stamp, stamp])

    def _reserve(self, name: str, node_id: str) -> None:
        """A retracted fact's id, restored on load so a re-established fact
        keeps its node (and its history) rather than starting a new one."""
        self._ids.setdefault(name, node_id)

    def retracted(self) -> list[str]:
        """Names that had a node and no longer hold a value."""
        return [n for n in self._ids if n not in self._data]

    def _touch(self, name: str) -> None:
        if name in self._times:
            self._times[name][1] = _utc_now()

    def _remove(self, name: str) -> None:
        gone = self._data.pop(name, None)
        if gone is not None:
            self._gone[name] = gone

    def last_value(self, name: str) -> Optional[Fact]:
        """A retracted fact's last value (store.save keeps its row, inactive)."""
        return self._gone.get(name)

    def node_id(self, name: str) -> Optional[str]:
        return self._ids.get(name)

    def name_of(self, node_id: str) -> Optional[str]:
        """The fact a node id belongs to, held or retracted."""
        return next((n for n, i in self._ids.items() if i == node_id), None)

    def created_at(self, name: str) -> Optional[str]:
        return (self._times.get(name) or [None, None])[0]

    def updated_at(self, name: str) -> Optional[str]:
        return (self._times.get(name) or [None, None])[1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class CaseFile:
    case_id: str
    # The fact graph (FactGraph): read-only here, written by FactManager.
    facts: dict[str, Fact] = field(default_factory=dict)
    evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    # Raw customer text for audit. Material points are promoted into Facts by
    # engines.account.assess_material_account before analysis/drafting.
    raw_answers: dict[str, str] = field(default_factory=dict)
    state: CaseState = CaseState.CREATED
    driver_status: DriverStatus = DriverStatus.UNIDENTIFIED
    asked_questions: list[str] = field(default_factory=list)
    # Questions currently put to the customer, as asked. V2 questions are written
    # by the analysis engine, so this is the only record of a question's type and
    # options when the answer comes back.
    pending_questions: list[dict] = field(default_factory=list)
    # What the classifier itself said each document is, before anything
    # downstream reassigns EvidenceItem.kind. Empty means the classifier never
    # answered, which rules/scope.py must not mistake for "not a parking notice".
    document_classes: dict[str, str] = field(default_factory=dict)
    # Engine 0's verdict when it routed this case out of the service.
    scope_stop: Optional[str] = None
    # Intake (pcn_appeal/intake): the one service this case belongs to, and the
    # document and stage that decided it. None until the neutral classifier has
    # run - a case built directly against the private-parking pipeline (the
    # scenario suite) never sets them, and rules/scope.py treats None as "the
    # router did not see this case", not as a route.
    route: Optional[str] = None
    document_type: Optional[str] = None
    stage: Optional[str] = None
    # Per-document neutral classification (intake/classifier.py), keyed by
    # evidence id. Service-neutral by construction: no service's facts live here.
    classifications: dict[str, dict] = field(default_factory=dict)
    # Dated events read off the documents (document issued, classified ...),
    # oldest first. Shared by every route; a deadline engine reads it later.
    timeline: list[dict] = field(default_factory=list)
    # Grounds chosen by AI case analysis and already vetoed against the KB. The
    # drafter works from these; nothing re-derives them from a route.
    analysis_module_ids: list[str] = field(default_factory=list)
    # Latest automatic fact-recovery digest (documents → calculators → gaps).
    # Consumed by analysis/drafting; never contains raw customer narrative.
    recovery_report: dict = field(default_factory=dict)
    # Free-text extraction provenance: original → normalized fact → drafting
    # proposition (source=CUSTOMER_FREE_TEXT). Drafting uses propositions only.
    free_text_provenance: list = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)
    # P0.3: the current analysis run and whether it has finished. 0 = no run yet.
    run_id: int = 0
    run_status: str = "NONE"                 # NONE / OPEN / COMPLETED
    # P0.5: the frontend build the case was opened from (X-Frontend-Version).
    frontend_version: Optional[str] = None
    # P0.4: every fact write and removal, and the writes that were refused.
    fact_history: list[dict] = field(default_factory=list)
    fact_conflicts: list[dict] = field(default_factory=list)
    # P1: every reading of every fact, accepted or not (fact_graph.py).
    fact_sources: list[dict] = field(default_factory=list)
    # P2: possible values read from the customer's account. Not facts: never in
    # `facts`, so never in fact_view / the reasoning gate / drafting (hypotheses.py).
    fact_hypotheses: list[dict] = field(default_factory=list)
    # P5: every claim plan version for this case, oldest first
    # (engines/claim_plan_authority.FinalClaimPlan). The last LOCKED one is the
    # only authority over what a letter argues; earlier ones are SUPERSEDED and
    # never change.
    claim_plans: list = field(default_factory=list)
    # P5.5: every state transition {from, to, run_id, at} (integrity/trace.py
    # attaches the reason from the audit), and every model call made for this
    # case as integrity/ai_log.py records it - task, model, prompt version,
    # input/output digests, never the text itself.
    state_history: list[dict] = field(default_factory=list)
    ai_calls: list[dict] = field(default_factory=list)
    # P6: every draft written for this case, immutable, tied to the claim plan
    # it was written from (drafting/versions.py).
    draft_versions: list[dict] = field(default_factory=list)

    # convenience -----------------------------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        f = self.facts.get(name)
        return f.value if f and f.usable else default

    def has(self, name: str) -> bool:
        f = self.facts.get(name)
        return bool(f and f.usable and f.value not in (None, "", []))

    def put(self, fact: Fact, reason: str = "", changed_by: Optional[str] = None) -> bool:
        """Write a fact through FactManager (fact_graph.py). Returns False when
        the write was refused (a conflict) or ignored; the held value stands."""
        from .fact_graph import FactManager
        return FactManager.update_fact(self, fact, reason=reason, changed_by=changed_by).applied

    def set_status(self, name: str, status: "FactStatus", reason: str = "",
                   changed_by: str = "system") -> None:
        from .fact_graph import FactManager
        FactManager.set_status(self, name, status, reason=reason, changed_by=changed_by)

    def retract(self, name: str, reason: str, changed_by: str = "system") -> None:
        from .fact_graph import FactManager
        FactManager.retract(self, name, reason=reason, changed_by=changed_by)

    # runs ------------------------------------------------------------------
    def begin_run(self, trigger: str) -> int:
        """Start an analysis run. Everything recorded from here - extraction,
        reasoning, questions, the claim plan, validation and the outcome - is
        stamped with this run, and only this run decides the customer outcome."""
        self.run_id += 1
        self.run_status = "OPEN"
        self.audit.append({"event": "run_started", "trigger": trigger})
        return self.run_id

    def ensure_run(self, trigger: str) -> int:
        """The open run, or a new one when none is open (the last completed).
        Also binds this case as the one model calls are logged against
        (integrity/ai_log.py): every pipeline entry point passes through here."""
        from .integrity import ai_log
        ai_log.bind(self)
        if self.run_status != "OPEN":
            return self.begin_run(trigger)
        return self.run_id

    def complete_run(self, outcome: str) -> None:
        if self.run_status == "OPEN":
            self.audit.append({"event": "run_completed", "outcome": outcome})
            self.run_status = "COMPLETED"

    def current_run_audit(self) -> list[dict]:
        """The audit entries of the current run. A case that never started a
        run (built before runs existed) is one run: everything."""
        if not self.run_id:
            return list(self.audit)
        return [a for a in self.audit if a.get("run_id", 0) == self.run_id]

    def __setattr__(self, name: str, value: Any) -> None:
        # Every audit entry carries the run it belongs to, however the list was
        # assigned (the constructor, a store load, a test).
        if name == "audit" and not isinstance(value, RunAudit):
            value = RunAudit(self, value or [])
        # The facts are the fact graph, whatever was assigned (a dict in a
        # constructor or a test fixture is the graph's starting state).
        if name == "facts" and not isinstance(value, FactGraph):
            value = FactGraph(value or {})
        # P5.5: the case state machine is observable. Every change of state is
        # recorded, however it was made; the constructor's first value is the
        # starting state, not a transition.
        if name == "state" and "state" in self.__dict__ and value != self.__dict__["state"]:
            history = self.__dict__.get("state_history")
            if history is not None:
                history.append({
                    "from": getattr(self.__dict__["state"], "value", self.__dict__["state"]),
                    "to": getattr(value, "value", value),
                    "run_id": self.__dict__.get("run_id", 0),
                    # where in the audit this happened: the entry recorded next
                    # (or just before) is the reason for the transition
                    "audit_index": len(self.__dict__.get("audit") or ()),
                    "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")})
        object.__setattr__(self, name, value)

    def fact_view(self) -> dict[str, Any]:
        """Flat dict of usable facts for the rule DSL."""
        view = {k: f.value for k, f in self.facts.items() if f.usable}
        view["driver_status"] = self.driver_status.value
        view["evidence_kinds"] = sorted({e.kind for e in self.evidence.values() if e.uploaded})
        return view


# --------------------------------------------------------------------------- knowledge base
@dataclass
class KBModule:
    module_id: str
    route: str
    topic: str
    use_when: dict                 # machine-evaluable predicate (rules/dsl.py)
    do_not_use_when: dict
    core_proposition: str
    required_facts: list[str]
    evidence_helpful: list[str]
    legal_basis: list[str]         # LegalSource ids resolved against version tables
    drafting_notes: str
    prohibited_claims: list[str]
    building_blocks: list[str]     # PP-/AI- block ids that express this module
    version: str = "1.0"
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    status: str = "ACTIVE"         # ACTIVE / REVIEW / DISABLED
    strength: int = 50             # admin-set base weight 0..100
    # Provenance for legal review. Carried so a reviewer can trace a proposition
    # back to the source document without leaving the KB, and so the audit log
    # can record which review pass a ground came from.
    source_reference: str = ""
    legal_basis_origin: str = ""
    last_legal_review: Optional[date] = None
    change_notes: str = ""


# Sentences inside approved block text that address the DRAFTER, not the
# operator: "Use only where the actual sign evidence supports this factual
# proposition." Three Appendix A blocks carry one, and a drafter that renders the
# block verbatim posts it to the operator as part of the customer's appeal.
_DRAFTER_NOTE = re.compile(r"^\s*(use\s+(only|where|when)|only\s+use|do\s+not\s+use)\b", re.I)


@dataclass
class BuildingBlock:
    block_id: str
    text: str
    requires_evidence: list[str] = field(default_factory=list)
    requires_facts: list[str] = field(default_factory=list)
    status: str = "ACTIVE"         # ACTIVE / REVIEW / DISABLED, as for a module
    # Placeholder name in `text` -> fact name that fills it, for the cases where
    # the approved wording reads better with its own noun ({{bay_reference}})
    # than with the fact's name (allocated_bay).
    placeholder_map: dict[str, str] = field(default_factory=dict)
    source_reference: str = ""

    @property
    def letter_text(self) -> str:
        """`text` with the drafter's own instructions removed.

        `text` stays verbatim, because that is what the KB approved and what a
        reviewer compares against Appendix A. This is the same wording with the
        sentences that are addressed to whoever is writing taken out, and it is
        what any letter is built from.
        """
        keep = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", self.text)
                if not _DRAFTER_NOTE.match(s)]
        return " ".join(keep).strip() or self.text


# --------------------------------------------------------------------------- reasoning output
@dataclass
class RetrievalPack:
    """Schema from KB section 18 plus the fields production needs."""
    primary_route: Optional[str]
    secondary_routes: list[str]
    module_ids: list[str]
    verified_facts: dict[str, Any]
    fact_refs: dict[str, str]          # fact name -> fact_id (for sentence citations)
    missing_facts: list[str]
    evidence_refs: list[str]
    prohibited_claims: list[str]
    code_version: Optional[str]
    pofa_route: str                    # POSTAL / WINDSCREEN / NOT_APPLICABLE / UNRESOLVED
    pofa_findings: list[str]           # verified defect codes only
    driver_status: str
    jurisdiction: str
    context_chunks: list[dict]         # module text + block text given to drafter
    lease_clauses: list[dict]          # verbatim clauses {clause_ref, text, evidence_id}
    trace: list[str] = field(default_factory=list)
    evidence_index: dict[str, str] = field(default_factory=dict)   # evidence_id -> kind
    # Keeper-safe case digest for the drafter: allegation, evidence summary,
    # unresolved topics. Never includes raw narrative wording that could leak
    # driver identity.
    case_context: dict = field(default_factory=dict)
    # P5: the LOCKED claim plan this pack was built from (claim_plan_authority
    # .FinalClaimPlan.for_validation). Validation checks every argument against
    # it. Never sent to the drafter; None only outside the pipeline.
    claim_plan: Optional[dict] = None


# --------------------------------------------------------------------------- drafting
@dataclass
class DraftSentence:
    text: str
    fact_refs: list[str] = field(default_factory=list)     # fact_ids
    module_refs: list[str] = field(default_factory=list)   # module_ids
    evidence_refs: list[str] = field(default_factory=list)
    quote_of: Optional[str] = None                         # evidence_id if verbatim quote


@dataclass
class Draft:
    case_id: str
    paragraphs: list[list[DraftSentence]]
    attempt: int = 1
    # Set when the drafter declined to write because the retrieved wording
    # supported no ground at all. An internal line: it is a hold for manual
    # review, never an error to retry and never shown to the customer.
    no_ground_reason: Optional[str] = None
    # What wrote this letter. Carried on the draft because the letter and the
    # thing that produced it are only auditable together: a stored draft with no
    # model and no prompt version cannot be replayed or explained afterwards,
    # and a demo stand-in's letter cannot be told from a real provider's.
    model: Optional[str] = None
    prompt_version: Optional[int] = None

    def sentences(self) -> list[DraftSentence]:
        return [s for p in self.paragraphs for s in p]

    def plain_text(self) -> str:
        return "\n\n".join(" ".join(s.text for s in p) for p in self.paragraphs)


@dataclass
class ValidationIssue:
    rule: str
    severity: str        # BLOCK / WARN
    message: str
    sentence: Optional[str] = None


@dataclass
class ValidationResult:
    passed: bool
    issues: list[ValidationIssue]
