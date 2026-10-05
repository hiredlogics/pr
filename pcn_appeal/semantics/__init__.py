"""P10.5 / P17.9 case semantic layer: narrative → controlled concepts → FactManager.

The LLM extracts factual meaning only. It never outputs module ids, legal
grounds, legal conclusions, or appeal outcomes. Deterministic helpers are
secondary safety checks.

Handoff: SemanticCaseResolver → SemanticCaseState → FactManager (sole authority)
→ KnowledgeModuleResolver → Case Intelligence → Claim Plan.
"""
from .extract import SemanticConcept, extract_concepts, extract_and_promote
from .input_contract import CaseUnderstandingInput, from_case
from .ontology import (
    CONCEPT_DEFINITIONS, CONCEPT_EXTRA_FACTS, CONCEPT_TO_FACTS, CONCEPTS,
    ONTOLOGY_VERSION,
)
from .resolver import SemanticCaseResolver, SemanticResolveResult, resolve
from .state import (
    FACT_CONFLICT, SEMANTIC_OWNED_FACTS, SemanticCaseState,
    build_semantic_case_state, handoff_blocks_claim_plan, handoff_ready,
)

__all__ = [
    "CONCEPTS", "CONCEPT_DEFINITIONS", "CONCEPT_TO_FACTS", "CONCEPT_EXTRA_FACTS",
    "ONTOLOGY_VERSION",
    "SemanticConcept", "extract_concepts", "extract_and_promote",
    "SemanticCaseState", "SEMANTIC_OWNED_FACTS", "FACT_CONFLICT",
    "build_semantic_case_state", "handoff_ready", "handoff_blocks_claim_plan",
    "CaseUnderstandingInput", "from_case",
    "SemanticCaseResolver", "SemanticResolveResult", "resolve",
]
