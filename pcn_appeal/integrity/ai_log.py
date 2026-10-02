"""Every model call, recorded against the case it served (P5.5 §5).

`audited(llm)` wraps any LLMClient. Each `complete_json` call appends one row
to `case.ai_calls` (and an `ai_call` audit event) for the case currently being
worked on - the case whose run is open (CaseFile.ensure_run binds it).

What is stored is enough to prove what ran and to detect a changed input or
output, and nothing more: task, provider, model, prompt version, the SHA-256
and size of the input and the output, the top-level keys of each (the
input_sources: "verified_facts", "candidates", ...), the number of images,
timing and status. Never the prompt, the payload or the response text - those
contain the customer's documents and answers.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

CURRENT_CASE: contextvars.ContextVar = contextvars.ContextVar("pcn_current_case", default=None)


def bind(case) -> None:
    """The case model calls are attributed to from now on (this context)."""
    CURRENT_CASE.set(case)


def current():
    return CURRENT_CASE.get()


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def _keys(text: Any) -> list[str]:
    if isinstance(text, dict):
        return sorted(str(k) for k in text.keys())
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    return sorted(str(k) for k in data.keys()) if isinstance(data, dict) else []


def provider_name(llm) -> str:
    from ..manifest import provider_of
    return provider_of(llm)


class AuditedLLM:
    """Transparent wrapper: every attribute of the inner client is reachable
    (`models`, `calls`, `SUPPORTS_IMAGES` ...); only `complete_json` is observed."""

    def __init__(self, inner):
        object.__setattr__(self, "inner", inner)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __setattr__(self, name, value):
        setattr(self.inner, name, value)

    def complete_json(self, *, task: str, system: str, user: str, images=None) -> dict:
        from .. import prompts
        case = current()
        started = time.perf_counter()
        at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        row: dict[str, Any] = {
            "task": task,
            "provider": provider_name(self.inner),
            "model": (getattr(self.inner, "models", None) or {}).get(task)
            or type(self.inner).__name__,
            "prompt_version": _prompt_version(prompts, task),
            "prompt_sha256": _sha(system),
            "input_sha256": _sha(user), "input_chars": len(user or ""),
            "input_sources": _keys(user), "images": len(images or []),
            "at": at,
        }
        try:
            out = self.inner.complete_json(task=task, system=system, user=user, images=images)
        except Exception as exc:
            row.update(status="ERROR", error=f"{type(exc).__name__}: {exc}"[:200],
                       duration_ms=int((time.perf_counter() - started) * 1000))
            _record(case, row)
            raise
        text = json.dumps(out, sort_keys=True, default=str) if out is not None else ""
        row.update(status="SUCCESS", output_sha256=_sha(text), output_chars=len(text),
                   output_keys=_keys(out), duration_ms=int((time.perf_counter() - started) * 1000))
        _record(case, row)
        return out


def _prompt_version(prompts, task: str) -> Optional[int]:
    try:
        return prompts.version(task)
    except Exception:
        return None


def _record(case, row: dict) -> None:
    if case is None:
        return
    row = dict(row, case_id=case.case_id, run_id=case.run_id)
    case.ai_calls.append(row)
    case.audit.append({"event": "ai_call", **{k: v for k, v in row.items()
                                              if k not in ("case_id", "run_id")}})


def audited(llm):
    return llm if isinstance(llm, AuditedLLM) else AuditedLLM(llm)


__all__ = ["AuditedLLM", "audited", "bind", "current"]
