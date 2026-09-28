"""LLM client abstraction.

Every LLM call in the system goes through `LLMClient.complete_json`, which
forces structured JSON output. Prompts are loaded by id+version from the
prompt registry (Postgres `prompts` table) so admins can change them without
a deploy (Dev Pack Part 13, Phase 10).

Model routing is config, not code. Suggested defaults:
    extraction  -> a vision-capable model (PCN photos / PDFs)
    questioning -> small fast model (classification only)
    drafting    -> strongest writing model
    validation  -> DIFFERENT prompt, ideally a different model than drafting
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol


class LLMClient(Protocol):
    def complete_json(self, *, task: str, system: str, user: str,
                      images: list[bytes] | None = None) -> dict[str, Any]: ...


MODEL_ROUTING = {
    "extraction": os.getenv("MODEL_EXTRACTION", "claude-sonnet-5"),
    "questioning": os.getenv("MODEL_QUESTIONING", "claude-haiku-4-5-20251001"),
    "drafting": os.getenv("MODEL_DRAFTING", "claude-opus-5-5"),
    "validation": os.getenv("MODEL_VALIDATION", "claude-sonnet-5"),
}


class AnthropicClient:
    """Production client. Requires `pip install anthropic` and ANTHROPIC_API_KEY."""

    def __init__(self):
        import anthropic  # imported lazily so tests run without the SDK
        self._c = anthropic.Anthropic()

    def complete_json(self, *, task, system, user, images=None):
        import base64
        content: list[dict] = []
        for img in images or []:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(img).decode()}})
        content.append({"type": "text", "text": user})
        resp = self._c.messages.create(
            model=MODEL_ROUTING[task], max_tokens=4000, temperature=0,
            system=system + "\nRespond with a single JSON object only. No prose, no markdown fences.",
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(text)


class FakeLLM:
    """Deterministic stand-in for tests: returns queued responses per task."""

    def __init__(self, responses: dict[str, list[dict]] | None = None):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.calls: list[dict] = []

    def complete_json(self, *, task, system, user, images=None):
        self.calls.append({"task": task, "user": user})
        q = self.responses.get(task)
        if not q:
            raise RuntimeError(f"FakeLLM has no response queued for task {task!r}")
        return q.pop(0)
