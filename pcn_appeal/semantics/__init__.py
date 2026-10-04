"""P10.5 case semantic layer: narrative → controlled concepts → FactManager.

The LLM extracts factual meaning only. It never outputs module ids, legal
grounds, legal conclusions, or appeal outcomes. Deterministic helpers are
secondary safety checks.
"""
from .extract import SemanticConcept, extract_concepts, extract_and_promote
from .ontology import (
    CONCEPT_DEFINITIONS, CONCEPT_TO_FACTS, CONCEPTS, ONTOLOGY_VERSION,
)

__all__ = [
    "CONCEPTS", "CONCEPT_DEFINITIONS", "CONCEPT_TO_FACTS", "ONTOLOGY_VERSION",
    "SemanticConcept", "extract_concepts", "extract_and_promote",
]
