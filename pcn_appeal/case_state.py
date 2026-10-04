"""The Master Case Object (P8.1): one authoritative state for a case.

Before this there were several places a stage could learn what a case "is": the
fact graph, the analysis audit entry, the knowledge matcher's in-memory result,
the claim plan, and whatever each engine recomputed for itself. Two stages
reading two of those could disagree, and the disagreement was invisible because
neither was wrong about its own source.

`MasterCase` is that single object. It is bound to a `CaseFile` and projects the
case's one set of nodes into the seven sections every stage works from:

    notice_facts        operator, PCN, VRM, dates, location, allegation, evidence
    customer_facts      stated, confirmed, inferred, hypotheses, provenance
    derived_facts       every derived value with `derived_from: [fact ids]`
    legal_findings      calculation, rule used, evidence, confidence, status
    knowledge_matches   module, relationship, support / block reason
    grounds             origin, supporting facts, evidence, findings, particulars
    claim_plan          the final authority

Three properties hold:

  * Projection, not a copy. `notice_facts`, `customer_facts`, `derived_facts`
    and `legal_findings` are views over the fact graph and the findings the
    Legal Calculation Engine wrote. There is nothing to fall out of step with.

  * Additive. The sections stages write - knowledge matches and grounds - are
    append-only, stamped with the run and the plan version they belong to. A
    later run adds a generation; it never edits or drops an earlier one, so the
    lineage of a decision survives every rebuild.

  * Grounds come from the claim plan or they do not exist. `record_grounds`
    accepts a `FinalClaimPlan` that has been confirmed or locked, and nothing
    else: a downstream component cannot rebuild grounds of its own
    (`GroundsAuthorityError`).

Serialization is `to_dict` / `from_dict`, with `digest` over the content that
decides a letter. `from_dict` restores the sections that have no table of their
own (derivation lineage, knowledge matches, grounds); facts, findings and claim
plans come back through theirs, because restoring them twice would be the
second authority this object exists to remove.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from .models import CaseFile, FactStatus, SourceKind

SCHEMA_VERSION = 1

SECTIONS = ("notice_facts", "customer_facts", "derived_facts", "legal_findings",
            "knowledge_matches", "grounds", "claim_plan")

# The notice as the documents state it. Grouped the way a reader of the notice
# would group it; a field is in the section only when the graph holds it.
NOTICE_FIELDS: dict[str, tuple[str, ...]] = {
    "operator": ("operator_name", "operator_ata", "creditor_name"),
    "pcn": ("pcn_number", "charge_amount", "notice_type", "notice_route",
            "practice_code_version"),
    "vrm": ("vrm", "vehicle_make", "vehicle_colour"),
    "dates": ("parking_event_date", "notice_issue_date", "ntd_date",
              "notice_received_date", "entry_time", "exit_time", "payment_date"),
    "location": ("parking_location", "site_postcode", "landowner_name"),
    "allegation": ("alleged_breach", "contravention_code", "parking_duration_minutes"),
}

_CUSTOMER_SETTLED = (FactStatus.CONFIRMED, FactStatus.CORRECTED, FactStatus.ANSWERED)


class GroundsAuthorityError(RuntimeError):
    """A component other than the claim plan tried to decide the grounds."""


class MasterCaseStateError(ValueError):
    """Serialized state that does not belong to this case."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _plain(value: Any) -> Any:
    """JSON-safe, and unfrozen: the claim plan hands out read-only mappings,
    which have to come back from a reload as the same data, not as a different
    type that merely prints the same."""
    from .fact_graph import plain
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return plain(value)


def _sha(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# What a knowledge match says, without when it was said.
_MATCH_CONTENT = ("relationship", "reason", "supporting_conditions", "blocked_by",
                  "missing", "strength")


def _same_match(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in _MATCH_CONTENT)


# --------------------------------------------------------------- lineage
@dataclass(frozen=True)
class Derivation:
    """Where one derived value came from.

    `derived_from` holds fact ids - the graph's node ids, which survive a
    rebuild and a reload - so the chain can be walked back to the answers and
    readings the value rests on. A value computed straight off a document
    carries the evidence it was read from in `source_evidence` instead; both
    together are the lineage, and a derived fact with neither is a gap.
    """
    fact: str
    fact_id: Optional[str]
    derived_from: tuple[str, ...]
    source_facts: tuple[str, ...]
    rule: str
    source_evidence: tuple[str, ...] = ()
    run_id: int = 0
    at: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.derived_from or self.source_evidence)

    def as_dict(self) -> dict:
        return {"fact": self.fact, "fact_id": self.fact_id,
                "derived_from": list(self.derived_from),
                "source_facts": list(self.source_facts),
                "source_evidence": list(self.source_evidence),
                "rule": self.rule, "run_id": self.run_id, "at": self.at}

    @classmethod
    def from_dict(cls, d: dict) -> "Derivation":
        return cls(d["fact"], d.get("fact_id"),
                   tuple(d.get("derived_from") or ()), tuple(d.get("source_facts") or ()),
                   d.get("rule") or "", tuple(d.get("source_evidence") or ()),
                   int(d.get("run_id") or 0), d.get("at") or "")


# What each deterministic calculator reads, for the writes that do not declare
# their own sources with `derives`. One table rather than a guess per call site:
# a calculator that is not here records its name and no sources, and
# `MasterCase.lineage_gaps` says so.
CALCULATOR_INPUTS: dict[str, tuple[str, ...]] = {
    "pofa.assess": ("jurisdiction", "relevant_land", "notice_route", "parking_event_date",
                    "notice_issue_date", "ntd_date", "notice_received_date",
                    "delivery_date_proven"),
    "pofa.content": ("notice_route", "notice_sides_complete", "ntk_invites_name_driver",
                     "ntk_invites_pass_to_driver", "driver_disclosure_to_operator"),
    "pofa.scan_ntk_invitations": ("notice_route", "notice_sides_complete",
                                  "ntk_invites_name_driver", "ntk_invites_pass_to_driver"),
    "pofa.keeper_warning": ("notice_route", "notice_sides_complete"),
    "pofa.deadline": ("parking_event_date", "notice_issue_date", "ntd_date"),
    "lease_clause_finder": ("lease_clauses",),
    "receipt_not_validation": ("shopping_purchase_confirmed",),
    "duration": ("entry_time", "exit_time"),
    "duration_calc": ("entry_time", "exit_time"),
}


def calculator_inputs(ref: str) -> tuple[str, ...]:
    """The declared inputs of the calculator behind a fact source reference.
    `recovery:method` and `pofa.assess#2` resolve to their calculator."""
    ref = str(ref or "")
    for key in (ref, ref.split("#", 1)[0], ref.split(":", 1)[0]):
        if key in CALCULATOR_INPUTS:
            return CALCULATOR_INPUTS[key]
    return ()


# The derivation a calculator has declared for the writes it is about to make.
# A plain stack, so nested calculators nest their lineage rather than fight over
# one slot; `derives` is the only thing that pushes to it.
_DERIVING: list[dict] = []


@contextmanager
def derives(case: CaseFile, *source_facts: str, rule: str,
            evidence: Iterable[str] = ()) -> Iterator[None]:
    """Declare the facts the values written inside this block are computed from.

    Every DERIVED write that lands while this is open records its sources, so
    the lineage of a derived fact is written by the calculator that knows it
    rather than guessed afterwards:

        with case_state.derives(case, "parking_event_date", "notice_issue_date",
                                rule="pofa.schedule4.para9"):
            case.put(Fact(..., FactStatus.DERIVED, ...))
    """
    _DERIVING.append({"case_id": getattr(case, "case_id", None),
                      "sources": tuple(dict.fromkeys(s for s in source_facts if s)),
                      "evidence": tuple(dict.fromkeys(e for e in evidence if e)),
                      "rule": rule})
    try:
        yield
    finally:
        _DERIVING.pop()


def _declared(case: CaseFile) -> Optional[dict]:
    for frame in reversed(_DERIVING):
        if frame["case_id"] in (None, getattr(case, "case_id", None)):
            return frame
    return None


def note_derivation(case: CaseFile, fact) -> None:
    """Record the lineage of a derived fact. Called by FactManager for every
    applied DERIVED write, so no derived fact can exist without a lineage row -
    an undeclared one records its calculator and an empty source list, which
    `lineage_gaps` reports rather than hides."""
    if fact.status != FactStatus.DERIVED:
        return
    frame = _declared(case)
    ref = fact.source.ref or ""
    if frame is None:
        frame = {"sources": calculator_inputs(ref), "evidence": (), "rule": ref or "calculation"}
    sources = tuple(n for n in frame.get("sources", ()) if n != fact.name)
    evidence = tuple(frame.get("evidence") or ())
    if not evidence and fact.source.kind == SourceKind.DOCUMENT:
        # Read straight off a document: the document is the lineage.
        evidence = (ref.split("#", 1)[0],) if ref else ()
    master(case).record_derivation(
        fact.name,
        source_facts=sources,
        evidence=evidence,
        rule=frame.get("rule") or ref or "calculation")


# ------------------------------------------------------------- sections
@dataclass(frozen=True)
class KnowledgeMatch:
    """What the knowledge relationships said about one module, for one run."""
    module_id: str
    relationship: str          # SUPPORTED / RELEVANT / OPEN / REJECTED / BLOCKED
    reason: str                # the support or the block reason, as matched
    supporting_conditions: tuple = ()
    blocked_by: tuple = ()
    missing: tuple = ()
    strength: int = 0
    run_id: int = 0
    at: str = ""

    @property
    def blocks(self) -> bool:
        return self.relationship == "BLOCKED"

    def as_dict(self) -> dict:
        return {"module_id": self.module_id, "relationship": self.relationship,
                "reason": self.reason,
                "supporting_conditions": [_plain(c) for c in self.supporting_conditions],
                "blocked_by": [_plain(b) for b in self.blocked_by],
                "missing": list(self.missing), "strength": self.strength,
                "run_id": self.run_id, "at": self.at}

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeMatch":
        return cls(d["module_id"], d.get("relationship") or "", d.get("reason") or "",
                   tuple(d.get("supporting_conditions") or ()),
                   tuple(d.get("blocked_by") or ()), tuple(d.get("missing") or ()),
                   int(d.get("strength") or 0), int(d.get("run_id") or 0), d.get("at") or "")


@dataclass(frozen=True)
class Ground:
    """One argument the case may make, as the claim plan decided it."""
    module_id: str
    origin: str                # the plan's decision: SELECTED / VERIFIED_FINDING / ...
    status: str                # SUPPORTED / REJECTED / UNRESOLVED
    reason: str
    supporting_facts: tuple = ()
    evidence: tuple = ()
    findings: tuple = ()
    required_particulars: tuple = ()
    plan_version: int = 0
    plan_id: str = ""
    run_id: int = 0
    at: str = ""

    @property
    def argued(self) -> bool:
        return self.status == "SUPPORTED"

    def as_dict(self) -> dict:
        return {"module_id": self.module_id, "origin": self.origin, "status": self.status,
                "reason": self.reason,
                "supporting_facts": [_plain(f) for f in self.supporting_facts],
                "evidence": [_plain(e) for e in self.evidence],
                "findings": list(self.findings),
                "required_particulars": [_plain(p) for p in self.required_particulars],
                "plan_version": self.plan_version, "plan_id": self.plan_id,
                "run_id": self.run_id, "at": self.at}

    @classmethod
    def from_dict(cls, d: dict) -> "Ground":
        return cls(d["module_id"], d.get("origin") or "", d.get("status") or "",
                   d.get("reason") or "", tuple(d.get("supporting_facts") or ()),
                   tuple(d.get("evidence") or ()), tuple(d.get("findings") or ()),
                   tuple(d.get("required_particulars") or ()),
                   int(d.get("plan_version") or 0), d.get("plan_id") or "",
                   int(d.get("run_id") or 0), d.get("at") or "")


# ---------------------------------------------------------- master object
class MasterCase:
    """The one case state object. Bound to a CaseFile; `case.master` is it."""

    def __init__(self, case: CaseFile):
        self._case = case

    @property
    def case(self) -> CaseFile:
        return self._case

    @property
    def case_id(self) -> str:
        return self._case.case_id

    # -------------------------------------------------- 1. notice facts
    @property
    def notice_facts(self) -> dict:
        """The notice as the documents state it, with the fact id behind every
        value so a letter can cite it."""
        case = self._case
        out: dict[str, Any] = {}
        for group, names in NOTICE_FIELDS.items():
            held = {n: self._entry(n) for n in names if self._holds(n)}
            if held:
                out[group] = held
        out["evidence_references"] = [
            {"evidence_id": e.evidence_id, "kind": e.kind, "filename": e.filename,
             "storage_url": e.storage_url, "pages": len(e.images)}
            for e in sorted(case.evidence.values(), key=lambda e: e.evidence_id)
            if e.uploaded]
        return out

    # ------------------------------------------------ 2. customer facts
    @property
    def customer_facts(self) -> dict:
        """What the customer said, what they settled, what was read out of
        their account, and what is still only possible."""
        case = self._case
        stated = {q: t for q, t in (case.raw_answers or {}).items()
                  if not q.startswith("_")}
        confirmed, inferred = {}, {}
        for name, f in sorted(case.facts.items()):
            if f.source.kind == SourceKind.CUSTOMER_FREE_TEXT:
                inferred[name] = self._entry(name)
            elif f.source.kind == SourceKind.ANSWER and f.status in _CUSTOMER_SETTLED:
                confirmed[name] = self._entry(name)
            elif f.status in (FactStatus.CONFIRMED, FactStatus.CORRECTED):
                confirmed[name] = self._entry(name)
        generations = []
        from . import fact_lifecycle
        for row in fact_lifecycle.generations(case):
            generations.append(_plain(row))
        return {"stated": stated, "confirmed": confirmed, "inferred": inferred,
                "hypotheses": [dict(h) for h in (case.fact_hypotheses or [])],
                "provenance": [_plain(p) for p in (case.free_text_provenance or [])],
                "generations": generations}

    # ------------------------------------------------- 3. derived facts
    @property
    def derived_facts(self) -> dict:
        """Every derived value with the facts it was derived from.

        The lineage is keyed by fact name and carries fact ids, so a value can
        be walked back to the notice or the answer it rests on even after the
        graph has been rebuilt.
        """
        out: dict[str, Any] = {}
        lineage = self._lineage_by_name()
        for name, f in sorted(self._case.facts.items()):
            if f.status != FactStatus.DERIVED:
                continue
            row = self._entry(name)
            d = lineage.get(name)
            row["derived_from"] = list(d.derived_from) if d else []
            row["source_facts"] = list(d.source_facts) if d else []
            row["source_evidence"] = list(d.source_evidence) if d else []
            row["rule"] = d.rule if d else (f.source.ref or "")
            missing = [s for s in row["source_facts"] if not self._holds(s)]
            if missing and not row["source_evidence"]:
                row["lineage_status"] = "UNSUPPORTED_DERIVATION"
                row["missing_sources"] = missing
            else:
                row["lineage_status"] = "COMPLETE"
            out[name] = row
        return out

    def lineage_gaps(self) -> list[str]:
        """Derived facts with neither a source fact nor a source document: the
        calculator that wrote them never declared what it read. Reported, never
        silently filled."""
        return [n for n, row in self.derived_facts.items()
                if not row["derived_from"] and not row["source_evidence"]]

    # ----------------------------------------------- 4. legal findings
    @property
    def legal_findings(self) -> list[dict]:
        """One record per defect family, as the Legal Calculation Engine wrote
        it: the calculation, the rule it applied, the facts it read, and the
        status that licenses (or refuses) a ground."""
        from .legal import findings as lf
        out = []
        for r in self._case.legal_findings or []:
            ftype = str(r.get("finding_type") or "")
            spec = lf.REGISTRY.get(ftype)
            out.append({
                "finding_id": r.get("finding_id"),
                "finding_type": ftype,
                "status": r.get("status"),
                "calculation": dict(r.get("calculation_result") or {}),
                "rule": spec.description if spec is not None else "",
                "timed": bool(getattr(spec, "timed", False)),
                "evidence": [e.get("fact") for e in (r.get("supporting_facts") or [])],
                "supporting_facts": [dict(e) for e in (r.get("supporting_facts") or [])],
                # A finding is a calculation, not an estimate: it is certain
                # when it is VERIFIED or NOT_SUPPORTED, and carries no
                # confidence at all while the inputs are missing.
                "confidence": 1.0 if r.get("status") in (lf.VERIFIED, lf.NOT_SUPPORTED)
                else 0.0,
                "legal_module_id": r.get("legal_module_id"),
                "run_id": r.get("run_id"),
            })
        return sorted(out, key=lambda r: r["finding_type"])

    def verified_findings(self) -> list[str]:
        from .legal import findings as lf
        return sorted(r["finding_type"] for r in self.legal_findings
                      if r["status"] == lf.VERIFIED)

    # -------------------------------------------- 5. knowledge matches
    @property
    def knowledge_matches(self) -> list[KnowledgeMatch]:
        """The latest relationship recorded for each module."""
        latest: dict[str, KnowledgeMatch] = {}
        for m in self.knowledge_match_history:
            latest[m.module_id] = m
        return sorted(latest.values(), key=lambda m: m.module_id)

    @property
    def knowledge_match_history(self) -> list[KnowledgeMatch]:
        """Every generation, oldest first. Additive: nothing is edited away."""
        return [KnowledgeMatch.from_dict(d) for d in self._store("knowledge_matches")]

    def record_knowledge_matches(self, match) -> list[KnowledgeMatch]:
        """Record what the knowledge matcher found, from a `Match`.

        Additive: a module whose relationship changed gets a new row, and the
        row that said what it used to be stays. A module the matcher reached
        the same conclusion about writes nothing, so re-running the matcher
        does not grow the case state.
        """
        store = self._store("knowledge_matches")
        held: dict[str, dict] = {}
        for d in store:
            held[d.get("module_id")] = d
        rows, now = [], _now()
        run_id = int(getattr(self._case, "run_id", 0) or 0)
        for mid, c in sorted(getattr(match, "candidates", {}).items()):
            row = KnowledgeMatch(
                module_id=mid,
                relationship=getattr(c, "status", "") or "",
                reason=getattr(c, "reason", "") or "",
                supporting_conditions=tuple(s.get("condition", "") for s in
                                            getattr(c, "selected_because", []) or []),
                blocked_by=tuple(_plain(b) for b in getattr(c, "blocked_by", []) or []),
                missing=tuple(getattr(c, "missing", []) or []),
                strength=int(getattr(c, "strength", 0) or 0),
                run_id=run_id, at=now)
            previous = held.get(mid)
            if previous is not None and _same_match(previous, row.as_dict()):
                continue
            store.append(row.as_dict())
            rows.append(row)
        return rows

    def blocked_modules(self) -> dict[str, str]:
        return {m.module_id: m.reason for m in self.knowledge_matches if m.blocks}

    # ------------------------------------------------------ 6. grounds
    @property
    def grounds(self) -> list[Ground]:
        """The grounds of the current claim plan. Empty until a plan decided
        them: no stage derives a ground of its own."""
        history = self.ground_history
        if not history:
            return []
        current = max(g.plan_version for g in history)
        return sorted((g for g in history if g.plan_version == current),
                      key=lambda g: g.module_id)

    @property
    def argued_grounds(self) -> list[Ground]:
        return [g for g in self.grounds if g.argued]

    @property
    def ground_history(self) -> list[Ground]:
        return [Ground.from_dict(d) for d in self._store("grounds")]

    def record_grounds(self, plan) -> list[Ground]:
        """Take the grounds from a decided claim plan. The only way grounds
        enter the case state.

        Raises GroundsAuthorityError for anything that is not a claim plan that
        has been confirmed or locked - a stage that wants to change the grounds
        has to change the plan, which is versioned, digested and immutable once
        locked.
        """
        from .engines.claim_plan_authority import CONFIRMED, LOCKED, FinalClaimPlan
        if not isinstance(plan, FinalClaimPlan):
            raise GroundsAuthorityError(
                "grounds are taken from a FinalClaimPlan, not from "
                f"{type(plan).__name__}")
        if plan.status not in (CONFIRMED, LOCKED):
            raise GroundsAuthorityError(
                f"claim plan v{plan.version} is {plan.status}: grounds are recorded "
                "from a confirmed or locked plan only")
        if plan.case_id != self.case_id:
            raise MasterCaseStateError(
                f"claim plan belongs to case {plan.case_id}, not {self.case_id}")
        now = _now()
        findings_by_module = self._findings_by_module()
        rows = []
        for item in plan.items:
            codes = tuple(findings_by_module.get(item.module_id, ()))
            rows.append(Ground(
                module_id=item.module_id,
                origin=item.decision,
                status=item.status,
                reason=item.reason,
                supporting_facts=tuple(_plain(f) for f in item.supporting_facts),
                evidence=tuple(_plain(e) for e in item.evidence_refs),
                findings=codes,
                required_particulars=tuple(self._particulars(codes)),
                plan_version=int(plan.version),
                plan_id=plan.claim_plan_id,
                run_id=int(plan.run_number or 0),
                at=now))
        existing = self._store("grounds")
        # Additive: a plan version is recorded once, and an earlier version's
        # grounds stay exactly as that version decided them.
        if any(int(g.get("plan_version") or 0) == int(plan.version) for g in existing):
            return [g for g in self.ground_history if g.plan_version == int(plan.version)]
        existing.extend(r.as_dict() for r in rows)
        return rows

    # --------------------------------------------------- 7. claim plan
    @property
    def claim_plan(self):
        """The final authority: the latest LOCKED plan, else the latest plan."""
        from .engines.claim_plan_authority import LOCKED
        plans = list(self._case.claim_plans or [])
        return next((p for p in reversed(plans) if p.status == LOCKED),
                    plans[-1] if plans else None)

    @property
    def claim_plan_history(self) -> list:
        return list(self._case.claim_plans or [])

    def lock_claim_plan(self, plan) -> list[Ground]:
        """Lock a plan and take its grounds in one step, so a locked plan and
        the grounds of record can never be two different decisions."""
        from .engines.claim_plan_authority import LOCKED
        if plan.status != LOCKED:
            plan.lock()
        return self.record_grounds(plan)

    @property
    def active_facts(self) -> dict:
        """The held graph: one active value per name, with authority."""
        return {n: self._entry(n) for n in self._case.facts}

    @property
    def fact_write_history(self) -> list:
        """Every write attempt, applied or ignored. Never rebuilt from latest."""
        return list(self._case.fact_history or [])

    # ------------------------------------------------- serialization
    def to_dict(self) -> dict:
        """The whole case state, as JSON-safe data."""
        plan = self.claim_plan
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "state": getattr(self._case.state, "value", self._case.state),
            "run_id": int(getattr(self._case, "run_id", 0) or 0),
            "notice_facts": _plain(self.notice_facts),
            "customer_facts": _plain(self.customer_facts),
            "derived_facts": _plain(self.derived_facts),
            "derivations": [d.as_dict() for d in self.derivations],
            "legal_findings": _plain(self.legal_findings),
            "knowledge_matches": [m.as_dict() for m in self.knowledge_match_history],
            "grounds": [g.as_dict() for g in self.ground_history],
            "claim_plan": plan.as_dict() if plan is not None else None,
            "active_facts": _plain(self.active_facts),
            "fact_history": _plain(list(self._case.fact_history or [])),
            "fact_conflicts": _plain([
                {k: v for k, v in c.items() if not str(k).startswith("_")}
                for c in (self._case.fact_conflicts or [])
            ]),
        }

    def digest(self) -> str:
        """Over what decides a letter: the facts a ground rests on, the
        findings, and the plan. Not over timestamps or run numbers, so the same
        case in two processes digests the same."""
        return _sha({
            "notice_facts": _plain(self.notice_facts),
            "derived": {n: [row["value"], sorted(row["derived_from"])]
                        for n, row in self.derived_facts.items()},
            "findings": [[r["finding_type"], r["status"], r["calculation"]]
                         for r in self.legal_findings],
            "grounds": [[g.module_id, g.status, g.origin, list(g.findings)]
                        for g in self.grounds],
            "claim_plan": getattr(self.claim_plan, "plan_digest", None),
        })

    def load_dict(self, data: dict) -> "MasterCase":
        """Restore the sections the store does not persist on their own, and
        check the ones it does against this case.

        Facts, findings and claim plans come back through their own tables;
        bringing them back a second time here would be the competing authority
        this object exists to remove. What is restored is the state that has no
        other home: derivation lineage, knowledge matches and recorded grounds.
        """
        if not isinstance(data, dict):
            raise MasterCaseStateError("master case state must be a mapping")
        if data.get("case_id") and data["case_id"] != self.case_id:
            raise MasterCaseStateError(
                f"state belongs to case {data['case_id']}, not {self.case_id}")
        if int(data.get("schema_version") or 0) > SCHEMA_VERSION:
            raise MasterCaseStateError(
                f"master case state schema {data.get('schema_version')} is newer than "
                f"this build understands ({SCHEMA_VERSION})")
        self._replace("derivations", [Derivation.from_dict(d).as_dict()
                                      for d in data.get("derivations") or []])
        self._replace("knowledge_matches", [KnowledgeMatch.from_dict(d).as_dict()
                                            for d in data.get("knowledge_matches") or []])
        self._replace("grounds", [Ground.from_dict(d).as_dict()
                                  for d in data.get("grounds") or []])
        return self

    @classmethod
    def from_dict(cls, case: CaseFile, data: dict) -> "MasterCase":
        return master(case).load_dict(data)

    # ------------------------------------------------------- internals
    @property
    def derivations(self) -> list[Derivation]:
        return [Derivation.from_dict(d) for d in self._store("derivations")]

    def record_derivation(self, name: str, *, source_facts: Iterable[str],
                          rule: str, evidence: Iterable[str] = ()) -> Derivation:
        """Append one lineage row. Additive: a fact recomputed from different
        inputs keeps the row that says what it used to rest on."""
        case = self._case
        sources = tuple(dict.fromkeys(str(s) for s in source_facts if s))
        ids = tuple(i for i in (case.facts.node_id(s) for s in sources) if i)
        held = {e.evidence_id for e in case.evidence.values()}
        evidence_ids = tuple(dict.fromkeys(str(e) for e in evidence if e and e in held))
        row = Derivation(fact=name, fact_id=case.facts.node_id(name),
                         derived_from=ids, source_facts=sources, rule=rule or "",
                         source_evidence=evidence_ids,
                         run_id=int(getattr(case, "run_id", 0) or 0), at=_now())
        rows = self._store("derivations")
        previous = next((d for d in reversed(rows) if d.get("fact") == name), None)
        if previous is not None \
                and tuple(previous.get("derived_from") or ()) == ids \
                and tuple(previous.get("source_evidence") or ()) == evidence_ids \
                and previous.get("rule") == row.rule:
            return Derivation.from_dict(previous)     # same derivation, same inputs
        rows.append(row.as_dict())
        return row

    def _lineage_by_name(self) -> dict[str, Derivation]:
        out: dict[str, Derivation] = {}
        for d in self.derivations:
            if d.complete or d.fact not in out:
                out[d.fact] = d
        return out

    def _store(self, name: str) -> list:
        """The list on the CaseFile this section lives in. One home per
        section; the object is the only writer."""
        store = getattr(self._case, f"master_{name}", None)
        if store is None:
            store = []
            setattr(self._case, f"master_{name}", store)
        return store

    def _replace(self, name: str, rows: list) -> None:
        store = self._store(name)
        store[:] = rows

    def _holds(self, name: str) -> bool:
        f = self._case.facts.get(name)
        return bool(f is not None and f.usable and f.value not in (None, "", []))

    def _entry(self, name: str) -> dict:
        from .fact_graph import fact_authority
        f = self._case.facts[name]
        return {"value": _plain(f.value), "fact_id": self._case.facts.node_id(name),
                "fact_ref": f.fact_id, "status": f.status.value,
                "source": {"kind": f.source.kind.value, "ref": f.source.ref},
                "confidence": f.confidence, "authority": fact_authority(f)}

    def _findings_by_module(self) -> dict[str, list[str]]:
        """Which verified findings license which module. Read from the findings
        the Legal Calculation Engine wrote, never from a module list."""
        from .legal import findings as lf
        out: dict[str, list[str]] = {}
        for r in self._case.legal_findings or []:
            if r.get("status") != lf.VERIFIED:
                continue
            mid = r.get("legal_module_id")
            if mid:
                out.setdefault(mid, []).append(str(r.get("finding_type")))
        return {k: sorted(v) for k, v in out.items()}

    def _particulars(self, codes: Iterable[str]) -> list[dict]:
        """What a letter arguing these findings has to set out. Read from the
        calculation (legal/findings.particulars), so a timed defect carries its
        dates and day count and a content defect carries none."""
        from .legal import findings as lf
        by_type = {str(r.get("finding_type")): r for r in self._case.legal_findings or []}
        out = []
        for code in codes:
            rec = by_type.get(code)
            if rec is None:
                continue
            p = lf.particulars(rec)
            if p:
                out.append({"finding_type": code, **_plain(p)})
        return out


def master(case: CaseFile) -> MasterCase:
    """The case's master object. One per CaseFile, created on first use."""
    obj = case.__dict__.get("_master")
    if obj is None or obj.case is not case:
        obj = MasterCase(case)
        object.__setattr__(case, "_master", obj)
    return obj


__all__ = ["MasterCase", "Ground", "KnowledgeMatch", "Derivation", "SECTIONS",
           "SCHEMA_VERSION", "GroundsAuthorityError", "MasterCaseStateError",
           "derives", "master", "note_derivation"]
