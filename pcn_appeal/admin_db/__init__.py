"""Admin-only, read-only PostgreSQL + pgvector live data explorer.

Does not modify application reasoning, Claim Plan, KB, or facts.
All handlers require admin auth and use READ ONLY transactions.
"""
from .routes import router

__all__ = ["router"]
