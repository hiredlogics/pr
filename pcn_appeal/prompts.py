"""Prompt registry.

Prompts are content, not code: changing one changes what the system says to a
customer, so they are authored in `data/prompts.yaml`, versioned, and pinned per
release exactly like the KB modules (Dev Pack Part 13, Phase 10).

    system("drafting")   -> the prompt body
    version("drafting")  -> the version a case should record

`use_release()` swaps in the prompts pinned by a published KB release, so an
appeal can be replayed against the prompt that actually produced it.

There is no built-in fallback text. An unknown or missing prompt raises rather
than calling a model with no instructions - a drafter running without its HARD
RULES block is exactly the failure this registry exists to prevent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

DATA = Path(__file__).resolve().parent / "data" / "prompts.yaml"
# No "questioning" task: routing a customer's words to a route, and a route to a
# preset question, was the V1 question engine. Case analysis decides now.
TASKS = ("extraction", "case_analysis", "drafting", "validation")

_registry: Optional[dict[str, dict[str, Any]]] = None


class PromptError(KeyError):
    pass


def load(path: Path = DATA) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text()) or {}
    prompts = raw.get("prompts") or {}
    missing = [t for t in TASKS if t not in prompts]
    if missing:
        raise PromptError(f"{path} is missing a prompt for: {missing}")
    out: dict[str, dict[str, Any]] = {}
    for task, entry in prompts.items():
        body = (entry or {}).get("body")
        if not body or not str(body).strip():
            raise PromptError(f"prompt {task!r} has an empty body")
        out[task] = {"body": str(body).rstrip(), "version": int((entry or {}).get("version", 1))}
    return out


def registry() -> dict[str, dict[str, Any]]:
    global _registry
    if _registry is None:
        _registry = load()
    return _registry


def use_release(prompts: dict[str, dict[str, Any]]) -> None:
    """Serve the prompts pinned by a published release instead of the YAML."""
    global _registry
    missing = [t for t in TASKS if t not in prompts]
    if missing:
        raise PromptError(f"release pins no prompt for: {missing}")
    _registry = {t: {"body": str(p["body"]).rstrip(), "version": int(p.get("version", 1))}
                 for t, p in prompts.items()}


def reset() -> None:
    """Drop the cache so the next read reloads from YAML (tests, admin reload)."""
    global _registry
    _registry = None


def _get(task: str) -> dict[str, Any]:
    try:
        return registry()[task]
    except KeyError:
        raise PromptError(f"no prompt registered for task {task!r}") from None


def system(task: str) -> str:
    return _get(task)["body"]


def version(task: str) -> int:
    return _get(task)["version"]


def versions() -> dict[str, int]:
    return {t: p["version"] for t, p in registry().items()}
