"""Which code is actually running.

A fix that cannot be shown to be deployed is not a fix. Without this, confirming
a release meant comparing a letter's wording against a guess: the deployed
commit was reconstructed by eye during a live incident because nothing in the
running process reported it.

Each platform injects the commit under its own name, so all of them are read
before falling back to the working tree (local dev) and finally to "unknown".
"unknown" is a truthful answer and never a crash: the app must still serve when
it cannot identify itself, but it must not claim an identity it does not have.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Checked in order. GIT_COMMIT is the explicit override a container sets itself.
COMMIT_VARS = (
    "GIT_COMMIT",
    "RAILWAY_GIT_COMMIT_SHA",
    "SOURCE_COMMIT",
    "VERCEL_GIT_COMMIT_SHA",
    "GITHUB_SHA",
)

UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def commit() -> str:
    """Full commit sha of the running code, or "unknown"."""
    for name in COMMIT_VARS:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    # Dockerfile writes /app/COMMIT_SHA at image build (Railway/GitHub builds).
    for path in (Path("/app/COMMIT_SHA"), ROOT / "COMMIT_SHA"):
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value and value != UNKNOWN:
                return value
        except Exception:
            pass
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=2)
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    except Exception:
        pass                    # no git, no .git directory, or a build image
    return UNKNOWN


def short() -> str:
    c = commit()
    return c if c == UNKNOWN else c[:12]
