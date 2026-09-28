#!/usr/bin/env python3
"""Check the configured LLM provider and show the model chosen per task.

    python scripts/check_llm.py [--env PATH] [--call]

--call additionally sends one tiny real request per task, so a key that lists
models but cannot invoke them (wrong project, no quota) fails here rather than
mid-case. Nothing is printed that could reveal key material.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pcn_appeal import config  # noqa: E402
from pcn_appeal.llm import OPENAI_PREFERENCES, default_client  # noqa: E402

KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def load_env(path: Path) -> None:
    if not path.exists():
        print(f"  no env file at {path}")
        return
    applied = config.load(root=path.parent, files=(path.name,))
    print(f"  loaded {len(applied)} vars from {path}")


# Values that are obviously a copied example rather than a credential. Catching
# these here turns a confusing provider 401 into a message that says what to do.
def placeholder_reason(value: str) -> str | None:
    if value.endswith("...") or value.rstrip(".") != value:
        return "ends in '...' - looks like a copied example, not a real key"
    if value.startswith("<") or value.endswith(">"):
        return "wrapped in angle brackets - looks like a fill-in-the-blank"
    if len(value) < 40:
        return f"only {len(value)} characters - real provider keys are far longer"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".venv/.env", help="env file to load first")
    ap.add_argument("--call", action="store_true", help="also send one real request per task")
    args = ap.parse_args()

    print("environment")
    load_env(Path(args.env))
    bad = False
    for var in KEY_VARS:
        value = os.getenv(var)
        if not value:
            print(f"  {var}: absent")
            continue
        reason = placeholder_reason(value)
        if reason:
            print(f"  {var}: PLACEHOLDER - {reason}")
            bad = True
        else:
            print(f"  {var}: present")
    print(f"  LLM_PROVIDER: {os.getenv('LLM_PROVIDER') or '(auto)'}")
    if bad:
        print(f"\nEdit {args.env} and replace the placeholder with a real key from\n"
              "  https://platform.openai.com/api-keys\n"
              "Paste it in your editor rather than echoing it, to keep it out of shell history.")
        return 2

    print("\nclient")
    try:
        client = default_client()
    except Exception as exc:
        print(f"  FAILED to construct: {type(exc).__name__}: {exc}")
        return 1
    print(f"  {type(client).__name__}")

    models = getattr(client, "models", None) or {}
    print("\nmodel per task")
    for task in OPENAI_PREFERENCES:
        print(f"  {task:12} -> {models.get(task, '(n/a)')}")
    if models.get("drafting") and models.get("drafting") == models.get("validation"):
        print("  WARNING: validation shares the drafting model")

    if args.call:
        print("\nlive call per task")
        for task in OPENAI_PREFERENCES:
            try:
                out = client.complete_json(
                    task=task, system='Reply with JSON {"ok": true} and nothing else.',
                    user="ping")
                print(f"  {task:12} ok  <- {out}")
            except Exception as exc:
                print(f"  {task:12} FAILED  {type(exc).__name__}: "
                      f"{str(exc).splitlines()[0][:120]}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
