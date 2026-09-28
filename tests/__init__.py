"""Test-suite setup.

The app loads `.env` at import time so `uvicorn pcn_appeal.api:app` picks up a
real key. That must not leak into the tests: importing `pcn_appeal.api` would
otherwise build a live OpenAI client and every case would make paid network
calls - slow, non-deterministic, and dependent on someone's quota.

Set here rather than in each module because unittest imports this package
before any test module, and `config.load` never overwrites a variable that is
already set.
"""
import os

os.environ.setdefault("LLM_PROVIDER", "demo")

# The store is opt-in, but be explicit: a stray DATABASE_URL must not point the
# in-memory suite at a real database.
os.environ.pop("DATABASE_URL", None)
