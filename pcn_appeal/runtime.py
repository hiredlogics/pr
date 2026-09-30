"""Which environment and which build this process is.

Two questions every client retest depends on, and neither was answerable from
the running app: "is this production?" (which decides whether a demo stand-in
may ever run) and "which deployment is this?" (which decides whether a fix is
live). Each platform names both under its own variable, so all of them are read.

Production is whatever the platform says it is, or an explicit APP_ENV. Nothing
here infers it from the presence of a key or a database: a guess in either
direction is exactly the ambiguity this module exists to remove.
"""
from __future__ import annotations

import os

# Checked in order. APP_ENV is the explicit override.
ENV_VARS = ("APP_ENV", "RAILWAY_ENVIRONMENT_NAME", "VERCEL_ENV")
BUILD_VARS = ("BUILD_ID", "RAILWAY_DEPLOYMENT_ID", "VERCEL_DEPLOYMENT_ID")

DEVELOPMENT = "development"
PRODUCTION = "production"
UNKNOWN = "unknown"


def _first(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def environment() -> str:
    """"production", "staging", ... as the platform names it; else "development"."""
    return _first(ENV_VARS).lower() or DEVELOPMENT


def is_production() -> bool:
    return environment() == PRODUCTION


def build_id() -> str:
    """The platform's deployment id, or "unknown". Never raises."""
    return _first(BUILD_VARS) or UNKNOWN
