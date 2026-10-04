"""DraftContext - the one object the drafter is given (P6 §1).

The drafter converts an approved Claim Plan into customer language. It does
not select claims, add arguments, invent facts or decide who was driving, so
it is given exactly what it needs to do that and nothing else:

    allowed    the LOCKED claim plan (approved claims only), verified facts
               (each with where it came from), approved evidence, the
               knowledge guidance for the approved claims, case metadata,
               the driver rule

    never      the full knowledge base, rejected / blocked / candidate
               modules, the customer's raw narrative or free text, internal
               trace, confidence scores

`DraftContext.from_pack` is the only way a payload is built (drafting.drafter
.drafting_payload delegates to it). It is built from the RetrievalPack the
Claim Plan produced, filtered again here, so a leak upstream does not reach the
model: guidance for a module the plan did not approve is dropped, forbidden
keys are stripped at any depth, and `audit()` records what was sent as ids and
a digest, never text.

The payload keeps the keys the drafting prompt and its test doubles already
read (module_ids, verified_facts, context_chunks, case_context ...); it adds
`fact_basis` (where each fact came from) and `driver_rule`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from ..models import RetrievalPack
from .support_contract import (
    DraftContextError, DraftRequirement, SupportBundle,
)

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def _kind_list(facts: dict, evidence_kinds: Any = None) -> list:
    if evidence_kinds:
        return list(evidence_kinds) if isinstance(evidence_kinds, (list, tuple, set)) else [evidence_kinds]
    raw = (facts or {}).get("evidence_kinds")
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    return [raw] if raw else []


def fill_placeholder_text(text: str, facts: Optional[dict] = None,
                          placeholder_map: Optional[dict] = None,
                          evidence_kinds: Any = None) -> str:
    """Replace {{token}} from verified facts. Unresolved tokens stay.

    Generic ontology: placeholder_map renames a wording token to a fact
    ({{bay_reference}} → allocated_bay); lease_or_tenancy is the evidence
    kind, not an invented identifier.
    """
    facts = facts or {}
    kinds = _kind_list(facts, evidence_kinds)

    def sub(m):
        token = m.group(1)
        name = (placeholder_map or {}).get(token, token)
        if name == "lease_or_tenancy" or token == "lease_or_tenancy":
            return "tenancy agreement" if "TENANCY" in kinds else "lease"
        value = facts.get(name)
        if value in (None, ""):
            return m.group(0)
        if name == "vrm" and isinstance(value, str):
            compact = value.replace(" ", "")
            if len(compact) == 7 and " " not in value:
                value = f"{compact[:4]} {compact[4:]}"
        return str(value)

    return _PLACEHOLDER.sub(sub, text or "")

DOCUMENT = "DOCUMENT"
CUSTOMER_ACCOUNT = "CUSTOMER_ACCOUNT"
DERIVED = "DERIVED"

STRUCTURAL = "STRUCTURAL"

# Keys that never reach the model, at any depth.
FORBIDDEN_KEYS = frozenset({
    "customer_source_texts",      # the customer's own wording (narrative, free text)
    "narrative", "raw_answers", "original", "free_text_provenance",
    "confidence", "confidence_score", "score", "relevance", "similarity",
    "trace", "audit", "candidates", "candidate_modules", "rejected", "rejected_modules",
    "blocked", "not_supported", "recovery", "inputs_digest",
})

# case_context keys the drafter may read. Everything else is withheld.
CASE_CONTEXT_ALLOWED = (
    "operator_name", "parking_location", "alleged_breach", "parking_event_date",
    "pcn_number", "vrm", "evidence", "unresolved_topics", "validation_status",
    "shopping_receipt_enclosed", "material_account_propositions",
    "material_account_proposition", "account_contradicts_allegation",
    "child_occupant_present", "customer_reported_facts", "document_established_facts",
    "customer_quotations", "factual_rebuttal", "timing_argument", "claim_plan",
    "supported_grounds", "support_bundles", "draft_requirements", "particulars",
)

PACK_KEYS = (
    "primary_route", "secondary_routes", "verified_facts", "fact_refs", "evidence_refs",
    "prohibited_claims", "code_version", "pofa_route", "pofa_findings", "driver_status",
    "context_chunks", "lease_clauses", "module_ids", "evidence_index",
)

# Facts derived from the keeper's own account (the material-account engine turns
# what the customer said into a proposition). They are calculated, so their
# source kind is not ANSWER - but they are still only the keeper's word.
ACCOUNT_DERIVED_FACTS = frozenset({
    "material_account_proposition", "material_account_propositions",
    "account_contradicts_allegation",
})

# What the keeper may say about who was driving.
DRIVER_RULE = {
    "UNIDENTIFIED": ("Write about the keeper and the vehicle. Never say or imply who parked, "
                     "drove, left or returned. Never write 'I parked' or 'the driver parked'."),
    "FORMALLY_IDENTIFIED": ("The driver has been formally identified to the operator; the letter "
                            "may refer to 'the driver' but must not add facts about them."),
}


def _strip(value: Any) -> Any:
    """Recursively drop FORBIDDEN_KEYS."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in FORBIDDEN_KEYS}
    if isinstance(value, (list, tuple)):
        return [_strip(v) for v in value]
    return value


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class DraftContext:
    claim_plan: dict                     # approved claims only
    facts: dict                          # verified_facts: name -> value
    fact_refs: dict                      # name -> fact id
    fact_basis: dict                     # name -> DOCUMENT | CUSTOMER_ACCOUNT | DERIVED
    evidence: tuple                      # approved (uploaded) evidence ids
    evidence_index: dict
    guidance: dict                       # approved wording + routes + lease clauses
    case: dict                           # case metadata
    driver_status: str
    approved: tuple                      # approved module ids, in plan order
    support_bundles: dict = None         # module_id -> SupportBundle
    draft_requirements: dict = None      # module_id -> DraftRequirement
    draft_plan: dict = None              # P10.6 structured rendering plan

    # ------------------------------------------------------------ build
    @classmethod
    def from_pack(cls, pack: RetrievalPack) -> "DraftContext":
        ctx = dict(pack.case_context or {})
        plan = dict(ctx.get("claim_plan") or {})
        overlay = dict(pack.claim_plan or {}) if isinstance(pack.claim_plan, dict) else {}
        for key in ("status", "support_bundles", "draft_requirements", "approved",
                    "module_ids", "claim_plan_id", "version"):
            if overlay.get(key) and not plan.get(key):
                plan[key] = overlay[key]
        approved = tuple(m for m in (plan.get("module_ids") or pack.module_ids or []))
        if plan.get("approved") and not approved:
            approved = tuple(plan["approved"])
        approved_set = set(approved)
        bundles = dict(plan.get("support_bundles") or {})
        reqs = dict(plan.get("draft_requirements") or {})
        if plan.get("status") and plan["status"] != "LOCKED":
            raise DraftContextError(
                "DraftContext is generated only from a LOCKED claim plan")
        for mid in approved:
            req = DraftRequirement.from_dict(reqs.get(mid))
            bundle = SupportBundle.from_dict(bundles.get(mid))
            if req.required_particulars and not bundle.complete():
                raise DraftContextError(
                    f"{mid} has DraftRequirement but an incomplete SupportBundle")
        reported = set(ctx.get("customer_reported_facts") or []) | ACCOUNT_DERIVED_FACTS
        established = set(ctx.get("document_established_facts") or []) - ACCOUNT_DERIVED_FACTS
        basis = {}
        for name in (pack.verified_facts or {}):
            basis[name] = (CUSTOMER_ACCOUNT if name in reported and name not in established
                           else DOCUMENT if name in established else DERIVED)
        case = {k: ctx.get(k) for k in CASE_CONTEXT_ALLOWED if k in ctx}
        if "supported_grounds" in case:
            case["supported_grounds"] = [g for g in case["supported_grounds"] or []
                                         if g.get("module_id") in approved_set]
        evidence = tuple(pack.evidence_refs or ())
        facts = dict(pack.verified_facts or {})
        particulars: dict[str, Any] = {}
        kept_bundles, kept_reqs = {}, {}
        for mid in approved:
            raw = bundles.get(mid) or {}
            bundle = SupportBundle.from_dict(raw)
            kept_bundles[mid] = bundle.as_dict()
            kept_reqs[mid] = DraftRequirement.from_dict(reqs.get(mid)).as_dict()
            for name, value in (bundle.values or {}).items():
                particulars[name] = value
                if name not in facts and value not in (None, ""):
                    facts[name] = value
                    basis.setdefault(name, (CUSTOMER_ACCOUNT if name in reported
                                            else DOCUMENT if name in established else DERIVED))
        if particulars:
            case["particulars"] = particulars
        # LOCKED plan → DraftContext: fill approved wording from verified facts.
        # Do not leave {{tokens}} for the model to invent or leak.
        kinds = list((pack.evidence_index or {}).values())
        chunks = []
        for c in pack.context_chunks or []:
            if c.get("module_id") not in approved_set and c.get("module_id") not in (None, STRUCTURAL):
                continue
            chunk = dict(c)
            raw = chunk.get("text")
            if raw and "{{" in str(raw):
                chunk["text"] = fill_placeholder_text(
                    str(raw), facts, chunk.get("placeholder_map"), kinds)
            chunks.append(chunk)
        guidance = {
            "primary_route": pack.primary_route, "secondary_routes": list(pack.secondary_routes or []),
            "context_chunks": chunks, "lease_clauses": list(pack.lease_clauses or []),
            "prohibited_claims": list(pack.prohibited_claims or []),
            "code_version": pack.code_version, "pofa_route": pack.pofa_route,
            "pofa_findings": list(pack.pofa_findings or []),
            # P6.1: VERIFIED legal findings only. The pack is built that way
            # (legal/findings.for_pack); this filter holds even if a caller
            # hands over a wider list.
            "legal_findings": [dict(f) for f in (pack.legal_findings or [])
                               if f.get("status") == "VERIFIED"],
        }
        ctx_obj = cls(claim_plan=_strip(plan), facts=_strip(facts),
                      fact_refs=dict(pack.fact_refs or {}), fact_basis=basis, evidence=evidence,
                      evidence_index=dict(pack.evidence_index or {}), guidance=_strip(guidance),
                      case=_strip(case), driver_status=str(pack.driver_status), approved=approved,
                      support_bundles=_strip(kept_bundles), draft_requirements=_strip(kept_reqs),
                      draft_plan=None)
        try:
            from .plan import build_draft_plan
            draft_plan = build_draft_plan(pack, case_id=str(getattr(pack, "case_id", "") or ""))
            return cls(claim_plan=ctx_obj.claim_plan, facts=ctx_obj.facts,
                       fact_refs=ctx_obj.fact_refs, fact_basis=ctx_obj.fact_basis,
                       evidence=ctx_obj.evidence, evidence_index=ctx_obj.evidence_index,
                       guidance=ctx_obj.guidance, case=ctx_obj.case,
                       driver_status=ctx_obj.driver_status, approved=ctx_obj.approved,
                       support_bundles=ctx_obj.support_bundles,
                       draft_requirements=ctx_obj.draft_requirements,
                       draft_plan=_strip(draft_plan.as_dict()))
        except Exception:
            return ctx_obj

    # ---------------------------------------------------------- payload
    def to_payload(self) -> dict:
        """The drafting prompt's input: the pack-shaped keys it already reads."""
        payload: dict[str, Any] = {
            "primary_route": self.guidance["primary_route"],
            "secondary_routes": self.guidance["secondary_routes"],
            "verified_facts": self.facts, "fact_refs": self.fact_refs,
            "evidence_refs": list(self.evidence),
            "prohibited_claims": self.guidance["prohibited_claims"],
            "code_version": self.guidance["code_version"],
            "pofa_route": self.guidance["pofa_route"],
            "pofa_findings": self.guidance["pofa_findings"],
            "driver_status": self.driver_status,
            "context_chunks": self.guidance["context_chunks"],
            "lease_clauses": self.guidance["lease_clauses"],
            "case_context": self.case, "module_ids": list(self.approved),
            "evidence_index": self.evidence_index,
            # P6
            "fact_basis": self.fact_basis,
            "driver_rule": DRIVER_RULE.get(self.driver_status, DRIVER_RULE["UNIDENTIFIED"]),
            # P6.1: the only legal defects the letter may state, each with the
            # deterministic calculation that proved it.
            "verified_legal_findings": self.guidance["legal_findings"],
            "support_bundles": dict(self.support_bundles or {}),
            "draft_requirements": dict(self.draft_requirements or {}),
        }
        if self.draft_plan:
            payload["draft_plan"] = dict(self.draft_plan)
        return payload

    # ------------------------------------------------------------ audit
    def audit(self) -> dict:
        """What was sent, as ids and a digest. No text, no values."""
        return {"claim_plan_id": self.claim_plan.get("claim_plan_id"),
                "claim_plan_version": self.claim_plan.get("version"),
                "claim_plan_status": self.claim_plan.get("status"),
                "approved": list(self.approved), "facts": sorted(self.facts),
                "customer_account_facts": sorted(k for k, v in self.fact_basis.items()
                                                 if v == CUSTOMER_ACCOUNT),
                "evidence": list(self.evidence),
                "chunks": len(self.guidance["context_chunks"]),
                "legal_findings": sorted(str(f.get("finding_type"))
                                         for f in self.guidance["legal_findings"]),
                "support_bundles": sorted(self.support_bundles or {}),
                "draft_plan_sections": len((self.draft_plan or {}).get("sections") or []),
                "context_sha256": _digest(self.to_payload())}

    def violations(self) -> list[str]:
        """Anything in the payload that must never be there (a self-check the
        tests and the integrity layer run against what is actually sent)."""
        return find_forbidden(self.to_payload())


def find_forbidden(payload: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in FORBIDDEN_KEYS:
                out.append(f"{path}.{k}".lstrip("."))
            out += find_forbidden(v, f"{path}.{k}")
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            out += find_forbidden(v, f"{path}[{i}]")
    return out


def draft_payload(pack: RetrievalPack, feedback: Optional[list[str]] = None) -> dict:
    payload = DraftContext.from_pack(pack).to_payload()
    if feedback:
        payload["validator_feedback"] = feedback
    return payload


__all__ = ["DraftContext", "DraftContextError", "ACCOUNT_DERIVED_FACTS", "draft_payload",
           "find_forbidden", "FORBIDDEN_KEYS", "CASE_CONTEXT_ALLOWED", "CUSTOMER_ACCOUNT",
           "DOCUMENT", "DERIVED"]
