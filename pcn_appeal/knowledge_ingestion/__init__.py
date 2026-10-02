"""Knowledge base ingestion and knowledge graph metadata layer (P4b).

    controlled DOCX -> parser -> extract (+ compiled YAML gates) -> graph -> drift
                    -> Postgres (store.ingest) -> graph queries / governance

A controlled, versioned copy of the knowledge base for review and retrieval.
It does not change what live reasoning reads (kb_modules.yaml or a published
kb_release): see store.py.

    python -m pcn_appeal.knowledge_ingestion parse   [docx]   # dry run, no database
    python -m pcn_appeal.knowledge_ingestion ingest  [docx] --by USER --reason TEXT
    python -m pcn_appeal.knowledge_ingestion releases
    python -m pcn_appeal.knowledge_ingestion compare RELEASE_A RELEASE_B
"""
from .parser import PARSER_VERSION, parse

__all__ = ["parse", "PARSER_VERSION"]
