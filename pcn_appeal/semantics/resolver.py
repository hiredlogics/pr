"""P17.9 — SemanticCaseResolver: single boundary for meaning understanding.

Observational only. FactManager remains the sole authoritative fact store.
LLM may propose concepts/events/atoms; it never owns legal eligibility or grounds.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .input_contract import CaseUnderstandingInput, from_case
from .state import handoff_ready, open_material_fact_conflicts
from .understanding import stream_status


def _load_semantic_state(case) -> Optional[dict]:
    raw = (case.raw_answers or {}).get("_semantic_case_state")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


@dataclass
class SemanticResolveResult:
    """Outcome of SemanticCaseResolver.resolve — observational + handoff status."""
    input: CaseUnderstandingInput
    semantic_state: Optional[dict] = None
    account: dict = field(default_factory=dict)
    handoff_ready: bool = False
    handoff_reasons: list[str] = field(default_factory=list)
    material_conflicts: list[dict] = field(default_factory=list)
    revision: int = 0
    trace: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "input": self.input.as_dict(),
            "semantic_state": self.semantic_state,
            "account": {
                k: self.account.get(k)
                for k in ("contradicts", "propositions", "semantic")
                if k in self.account
            },
            "extractions_n": len(self.account.get("extractions") or []),
            "handoff_ready": self.handoff_ready,
            "handoff_reasons": list(self.handoff_reasons),
            "material_conflicts": list(self.material_conflicts),
            "revision": self.revision,
            "trace": list(self.trace),
        }


class SemanticCaseResolver:
    """UNDERSTAND MEANING — consolidate narrative → ontology → SemanticCaseState → FactManager."""

    @staticmethod
    def resolve(case, *, llm=None, narrative: Optional[str] = None,
                texts: Optional[list[str]] = None) -> SemanticResolveResult:
        """Run the authoritative semantic handoff into FactManager.

        Wraps existing:
          narrative.understand → extract_and_promote → CircumstanceRule (non-owned)
          → material propositions → handoff_ready
        """
        if narrative is not None:
            case.raw_answers["narrative"] = narrative

        inp = from_case(case, narrative=narrative, texts=texts)
        trace = [
            "INPUT",
            f"documents={len(inp.documents)}",
            f"doc_observations={len(inp.extracted_document_observations)}",
            f"customer_texts={len(inp.texts)}",
            "DOCUMENT_OBSERVATIONS",
            "SEMANTIC_UNDERSTANDING",
        ]

        # Existing material-account pipeline (already routes ontology via FactManager).
        from ..engines.account import assess_material_account
        account = assess_material_account(case, llm=llm)
        trace.append("FACTMANAGER_RECONCILE")

        state_dict = _load_semantic_state(case)
        ready, reasons = handoff_ready(case, texts=inp.texts or None)
        conflicts = open_material_fact_conflicts(case)
        try:
            revision = int((case.raw_answers or {}).get("_semantic_revision") or 0)
        except Exception:
            revision = 0

        trace.append("FACT_VIEW")
        trace.append(f"handoff_ready={ready}")
        if conflicts:
            trace.append(f"material_conflicts={len(conflicts)}")

        result = SemanticResolveResult(
            input=inp,
            semantic_state=state_dict,
            account=account or {},
            handoff_ready=ready,
            handoff_reasons=list(reasons or []),
            material_conflicts=list(conflicts or []),
            revision=revision,
            trace=trace,
        )
        # Persist joined diagnostic for console / Claim Plan observability.
        case.raw_answers["_semantic_resolve_trace"] = json.dumps(
            result.as_dict(), default=str)[:16000]
        case.audit.append({
            "event": "semantic_case_resolver",
            "revision": revision,
            "handoff_ready": ready,
            "handoff_reasons": list(reasons or []),
            "customer_stream": stream_status(case),
            "concepts_n": len((state_dict or {}).get("concepts") or []),
            "events_n": len((state_dict or {}).get("events") or []),
            "atoms_n": len((state_dict or {}).get("narrative_atoms") or []),
            "conflicts_n": len(conflicts or []),
            "trace": trace,
        })
        return result


def resolve(case, *, llm=None, narrative: Optional[str] = None,
            texts: Optional[list[str]] = None) -> SemanticResolveResult:
    """Module-level entry point."""
    return SemanticCaseResolver.resolve(
        case, llm=llm, narrative=narrative, texts=texts)
