"""The case execution manifest: everything that produced a run's result.

One manifest per completed run, released or held. It names the code (backend
commit and build, frontend version), the knowledge base (release id, a digest of
the modules and blocks actually loaded, and the version of each module the
letter argued), every prompt version, the provider and model per task, the
drafter's model and prompt, the validator version and judge, and digests of
the inputs (facts, evidence) and of the letter itself.

With the stored retrieval pack (drafts.retrieval_pack) a letter can be
reproduced from its manifest: the same code, KB, prompts and models over the
same inputs, and `letter_sha256` says whether the result is the same letter.

Admin only. The customer serializer drops `manifest` wherever it appears.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

MANIFEST_VERSION = 1

# The frontend build that sent the current request (X-Frontend-Version, set by
# the Next proxy). A context variable so the pipeline needs no request object.
FRONTEND_VERSION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "frontend_version", default="unknown")


def _sha(data: Any) -> str:
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode()
    else:
        raw = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def kb_digest(kg) -> str:
    """A fingerprint of the knowledge base as loaded: each module's id,
    version, status, strength and gates, and each block's text. The YAML has no
    release id, so this is what tells two YAML builds apart."""
    cached = getattr(kg, "_manifest_digest", None)
    if cached:
        return cached
    modules = sorted((m.module_id, m.version, m.status, m.strength, m.route,
                      json.dumps(m.use_when, sort_keys=True, default=str),
                      json.dumps(m.do_not_use_when, sort_keys=True, default=str))
                     for m in kg.modules.values())
    blocks = sorted((b.block_id, b.status, _sha(b.text)) for b in kg.blocks.values())
    digest = _sha({"modules": modules, "blocks": blocks})
    try:
        kg._manifest_digest = digest
    except Exception:
        pass
    return digest


def provider_of(llm) -> str:
    llm = getattr(llm, "inner", llm)         # P5.5: through the audit wrapper
    name = type(llm).__name__
    return {"OpenAIClient": "openai", "DemoLLM": "demo"}.get(name, name)


def build(case, out, pipeline) -> dict:
    """The manifest for `out`, the result of the case's current run."""
    from . import prompts, runtime, version
    from .engines import validation

    kg = pipeline.kg
    llm = pipeline.extraction.llm
    pack = out.pack
    draft = out.draft
    judge = getattr(pipeline.validation, "judge", None)
    module_ids = list(getattr(pack, "module_ids", None) or [])
    facts = {name: {"value": f.value, "status": f.status.value, "source": f.source.ref}
             for name, f in sorted(case.facts.items())}
    return {
        "manifest_version": MANIFEST_VERSION,
        "case_id": case.case_id,
        "run_id": case.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": version.commit(),
        "build_id": runtime.build_id(),
        "environment": runtime.environment(),
        "frontend_version": getattr(case, "frontend_version", None) or FRONTEND_VERSION.get(),
        "kb": {
            "release_id": getattr(kg, "release_id", None),
            "digest": kb_digest(kg),
            # P4b: with `digest`, names the knowledge release this run matches
            # (knowledge_release.compiled_digest / relations_version).
            "relations_version": getattr(getattr(kg, "relations", None), "version", None),
            "modules": {mid: kg.modules[mid].version for mid in module_ids if mid in kg.modules},
        },
        "prompts": prompts.versions(),
        "provider": provider_of(llm),
        "models": dict(getattr(llm, "models", None) or {}),
        "drafter": {
            "name": type(draft).__name__ if draft is not None else None,
            "model": getattr(draft, "model", None),
            "prompt_version": getattr(draft, "prompt_version", None),
            "attempt": getattr(draft, "attempt", None),
        },
        "validator": {
            "version": validation.VERSION,
            "judge": None if judge is None else provider_of(judge),
        },
        "inputs": {
            "facts_sha256": _sha(facts),
            "evidence": {e.evidence_id: _sha(e.text or "") if not e.images
                         else _sha(b"".join(e.images))
                         for e in case.evidence.values()},
        },
        "result": {
            "state": out.state.value,
            "outcome": getattr(out, "outcome", None),
            "module_ids": module_ids,
            "letter_sha256": _sha(out.letter) if out.letter else None,
        },
    }


def attach(case, out, pipeline) -> Optional[dict]:
    """Build the manifest, put it on the output and in the audit. Never fails
    the run: a manifest that cannot be built is recorded as such."""
    try:
        m = build(case, out, pipeline)
    except Exception as exc:                     # pragma: no cover - defensive
        case.audit.append({"event": "execution_manifest_failed",
                           "error": f"{type(exc).__name__}: {exc}"[:200]})
        return None
    out.manifest = m
    case.audit.append({"event": "execution_manifest", "manifest": m})
    return m
