"""Load `.env` into the process environment.

Without this, `uvicorn pcn_appeal.api:app` never sees OPENAI_API_KEY unless it
was exported by hand, so the app silently falls back to the demo reader with a
perfectly good key sitting in a file next to it.

A real environment variable always beats the file, so container and CI settings
are never overridden by a stray local `.env`.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILES = (".env.local", ".env")

# Only settings this application actually understands are taken from a file.
#
# A local `.env` is often a pull from somewhere else - the one in this repo came
# from `vercel env pull` for a different project - so treating every name in it
# as authoritative is how you end up writing to another service's database.
# DATABASE_URL is deliberately absent: turning on persistence is a decision,
# so export it yourself when you mean it.
APP_SETTINGS = (
    "OPENAI_API_KEY",
    "LLM_PROVIDER",
    "OPENAI_MODEL_EXTRACTION",
    "OPENAI_MODEL_QUESTIONING",
    "OPENAI_MODEL_DRAFTING",
    "OPENAI_MODEL_VALIDATION",
    "EMBED_DIM",
    "CASE_RETENTION_DAYS",
    "DEV_CUSTOMER_ID",
)

_loaded = False


def parse(text: str) -> dict[str, str]:
    """Minimal dotenv: `KEY=value`, optional quotes, `#` comments, later wins."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            out[name] = value
    return out


def load(*, root: Path = ROOT, files: tuple[str, ...] = DEFAULT_FILES,
         only: tuple[str, ...] | None = APP_SETTINGS, override: bool = False) -> list[str]:
    """Layer every env file that exists, earliest in `files` winning.

    Layered rather than first-file-only because a `vercel env pull` writes a
    partial `.env.local`: stopping at it shadowed the OPENAI_API_KEY sitting in
    `.env`, which silently demoted every case to the demo reader.

    `only` restricts which names are taken; pass None to accept everything.
    """
    applied: list[str] = []
    for name in files:
        path = root / name
        if not path.is_file():
            continue
        # utf-8-sig strips a leading BOM so OPENAI_API_KEY is not read as
        # \ufeffOPENAI_API_KEY (which silently skips APP_SETTINGS filtering).
        for key, value in parse(path.read_text(encoding="utf-8-sig")).items():
            if only is not None and key not in only:
                continue
            if key in applied:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
                applied.append(key)
    return applied


def load_once() -> list[str]:
    """Idempotent: safe to call from every entry point."""
    global _loaded
    if _loaded:
        return []
    _loaded = True
    return load()
