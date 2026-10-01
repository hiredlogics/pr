"""Core domain models.

Reference implementation uses stdlib dataclasses so it runs anywhere.
In production, mirror these as Pydantic v2 models at the FastAPI boundary
(request/response validation) and as SQLAlchemy tables in Postgres.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
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


@dataclass
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

    @property
    def usable(self) -> bool:
        return self.status != FactStatus.UNCERTAIN


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


@dataclass
class CaseFile:
    case_id: str
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

    # convenience -----------------------------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        f = self.facts.get(name)
        return f.value if f and f.usable else default

    def has(self, name: str) -> bool:
        f = self.facts.get(name)
        return bool(f and f.usable and f.value not in (None, "", []))

    def put(self, fact: Fact) -> None:
        self.facts[fact.name] = fact
        self.audit.append({"event": "fact_set", "name": fact.name,
                           "status": fact.status.value, "source": fact.source.ref})

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
