"""P10.5 case semantic layer: narrative → controlled concepts → FactManager.

The LLM extracts factual meaning only. It never outputs module ids, legal
grounds, legal conclusions, or appeal outcomes. Deterministic helpers are
secondary safety checks.

Handoff: SemanticCaseState → FactManager (sole fact authority) → knowledge.
"""
from .extract import SemanticConcept, extract_concepts, extract_and_promote
from .ontology import (
    CONCEPT_DEFINITIONS, CONCEPT_TO_FACTS, CONCEPTS, ONTOLOGY_VERSION,
)
from .state import (
    FACT_CONFLICT, SEMANTIC_OWNED_FACTS, SemanticCaseState,
    build_semantic_case_state, handoff_blocks_claim_plan, handoff_ready,
)

__all__ = [
    "CONCEPTS", "CONCEPT_DEFINITIONS", "CONCEPT_TO_FACTS", "ONTOLOGY_VERSION",
    "SemanticConcept", "extract_concepts", "extract_and_promote",
    "SemanticCaseState", "SEMANTIC_OWNED_FACTS", "FACT_CONFLICT",
    "build_semantic_case_state", "handoff_ready", "handoff_blocks_claim_plan",
]
