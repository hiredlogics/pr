"""LLM client abstraction.

Every LLM call in the system goes through `LLMClient.complete_json`, which
forces structured JSON output. System prompts come from the versioned registry
in `prompts.py`, never from a string literal at the call site.

OpenAI is the only provider. `DemoLLM` exists solely so the app is runnable
before a key is configured; it is not a model and says so in the UI.

Model routing is config, not code:
    extraction  -> vision-capable (PCN photos / scans)
    questioning -> small fast model (classification only)
    drafting    -> strongest writer
    validation  -> a DIFFERENT model from drafting, enforced below
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol


class LLMClient(Protocol):
    def complete_json(self, *, task: str, system: str, user: str,
                      images: list[bytes] | None = None) -> dict[str, Any]: ...


JSON_ONLY = "\nRespond with a single JSON object only. No prose, no markdown fences."

# Best-first preference per task. `OpenAIClient` keeps the first entry the key
# can actually see, so a project without access to the newest model quietly
# lands on the next one instead of 404-ing mid-case. Override a task outright
# with OPENAI_MODEL_<TASK>.
OPENAI_PREFERENCES = {
    # vision-capable: PCN photos and scanned notices
    "extraction":  ["gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o"],
    # classification only - cheapest model that can follow a schema
    "questioning": ["gpt-5.1-mini", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"],
    # case analysis decides which grounds the evidence supports and what is
    # still worth asking - the most consequential judgement in the system, so it
    # gets the strongest model available.
    "case_analysis": ["gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o"],
    # strongest writer
    "drafting":    ["gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o"],
    # deliberately NOT the drafting model - an independent checker should not
    # share the writer's blind spots (see engines/validation.py)
    "validation":  ["gpt-5.1-mini", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini",
                    "gpt-4o", "gpt-4.1"],
}

# Tasks that must not resolve to the same model as the key, for the reason above.
DISTINCT_FROM = {"validation": "drafting"}


class OpenAIClient:
    """OpenAI client. Requires `pip install openai` and OPENAI_API_KEY.

    SUPPORTS_IMAGES is what lets the API tell a customer that a photographed
    notice cannot be read, instead of silently producing an empty case.

    Resolves each task to the best model the key can list, once, at startup -
    so an unavailable model fails loudly here rather than part-way through a
    customer's case.
    """

    SUPPORTS_IMAGES = True

    def __init__(self, api_key: str | None = None, preferences: dict | None = None):
        from openai import OpenAI          # imported lazily so tests run without the SDK
        self._c = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self._prefs = preferences or OPENAI_PREFERENCES
        self.models = self._resolve_models()

    def _resolve_models(self) -> dict[str, str]:
        available = {m.id for m in self._c.models.list().data}
        overrides = {t: os.getenv(f"OPENAI_MODEL_{t.upper()}") for t in self._prefs}
        chosen: dict[str, str] = {t: m for t, m in overrides.items() if m}

        def pick(task: str, exclude: set[str]) -> str:
            prefs = self._prefs[task]
            found = next((m for m in prefs if m in available and m not in exclude), None)
            if found is None:
                raise RuntimeError(
                    f"no model available for task {task!r}: tried {prefs}"
                    + (f" excluding {sorted(exclude)}" if exclude else "")
                    + f". Set OPENAI_MODEL_{task.upper()} to a model this key can use.")
            return found

        # resolve independent tasks first so the dependent ones can avoid them
        for task in self._prefs:
            if task not in chosen and task not in DISTINCT_FROM:
                chosen[task] = pick(task, set())
        for task, other in DISTINCT_FROM.items():
            if task in chosen:                       # explicit override: trust but verify below
                continue
            chosen[task] = pick(task, {chosen.get(other)} - {None})

        for task, other in DISTINCT_FROM.items():
            if chosen.get(task) and chosen[task] == chosen.get(other):
                raise RuntimeError(
                    f"{task} and {other} both resolved to {chosen[task]!r}; the release gate "
                    f"must not share the drafter's blind spots. Set OPENAI_MODEL_{task.upper()} "
                    "to a different model.")
        return chosen

    def complete_json(self, *, task, system, user, images=None):
        import base64
        content: list[dict] = [{"type": "text", "text": user}]
        for img in images or []:
            b64 = base64.b64encode(img).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        resp = self._c.chat.completions.create(
            model=self.models[task],
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system + JSON_ONLY},
                      {"role": "user", "content": content}])
        text = (resp.choices[0].message.content or "").strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
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


# --------------------------------------------------------------------------- demo only
_DEMO_LABELS = {
    "operator_name": r"operator(?:\s+name)?",
    "pcn_number": r"(?:pcn|parking charge notice|notice)\s*(?:number|no\.?|ref(?:erence)?)",
    "vrm": r"(?:vrm|vehicle registration(?:\s+mark)?|registration)",
    "parking_location": r"(?:location|site|car park)",
    "site_postcode": r"(?:postcode|post code)",
    "parking_event_date": r"(?:date of (?:the )?(?:parking )?(?:event|contravention)|parking date)",
    "notice_issue_date": r"(?:date (?:of )?issue(?:d)?|issue date)",
    "notice_received_date": r"date received",
    "ntd_date": r"notice to driver date",
    "entry_time": r"(?:entry|arrival)\s*time",
    "exit_time": r"(?:exit|departure)\s*time",
    "charge_amount": r"(?:charge|amount)(?:\s+due)?",
    "alleged_breach": r"(?:alleged\s+)?(?:breach|contravention|reason)",
    "operator_ata": r"(?:ata|accredited trade association|trade association)",
}
# Order matters: the notice types are checked before the supporting-evidence
# kinds, because a notice quotes the terms it alleges were breached and would
# otherwise match those keywords ("permitted period", "no valid receipt").
_DEMO_DOC_HINTS = [
    ("NTD", r"notice to driver|windscreen"),
    ("NTK", r"notice to keeper"),
    ("PCN", r"parking charge notice|\bpcn\b"),
    ("RECOVERY_REPORT", r"\b(rac|recovery|roadside)\b"),
    ("GARAGE_INVOICE", r"\bgarage\b"),
    ("TENANCY", r"\btenancy\b"),
    ("LEASE", r"\blease(hold)?\b"),
    ("BANK_STATEMENT", r"bank statement"),
    ("APP_SCREENSHOT", r"screenshot"),
    ("RECEIPT", r"\breceipt\b"),
    ("PERMIT", r"\bpermit\b"),
]


class DemoLLM:
    """Label-matching stand-in so the API is runnable with no ANTHROPIC_API_KEY.

    NOT an extractor. It reads only explicitly labelled `Field: value` lines and
    reports nothing for anything it cannot find, so absent fields stay absent
    rather than becoming guesses. Real extraction needs OpenAIClient.
    """

    SUPPORTS_IMAGES = False        # it matches labels in text; it cannot see

    def __init__(self):
        self.calls: list[dict] = []
        self._kg = None

    def _reference_analysis(self, payload: str) -> dict[str, Any]:
        """Grounds whose own KB gate is satisfied by the confirmed facts.

        This is NOT case analysis. It cannot read a notice, weigh evidence or
        judge whether an allegation is denied - which is exactly why production
        uses a model. It exists so the app remains usable without a key: with no
        proposal at all, every demo case would reach manual review with no letter.

        It never runs when a provider is configured. `/health` reports
        `provider: demo` and the UI says so, so a letter produced this way cannot
        be mistaken for one the model reasoned about.
        """
        from .rules.dsl import evaluate

        if self._kg is None:
            from .kg.graph import KnowledgeGraph
            self._kg = KnowledgeGraph()

        data = json.loads(payload)
        facts = data.get("facts") or {}
        offered = {c.get("module_id") for c in (data.get("candidates") or [])}
        supported = [
            m for m in self._kg.active_modules()
            if m.module_id in offered
            and evaluate(m.use_when, facts)
            and not evaluate(m.do_not_use_when, facts)
        ]
        supported.sort(key=lambda m: (-m.strength, m.module_id))
        return {
            "grounds": [{"module_id": m.module_id,
                         "supported_by": sorted(m.required_facts or []),
                         "note": "demo: use_when satisfied"} for m in supported],
            # It has no way to judge what is material, so it asks nothing.
            "questions": [],
            "not_supported": [],
        }

    def complete_json(self, *, task, system, user, images=None):
        self.calls.append({"task": task, "user": user})
        if task == "case_analysis":
            return self._reference_analysis(user)
        if task != "extraction":
            raise RuntimeError(f"DemoLLM only supports extraction, not {task!r}")
        docs = re.findall(r"<document id='([^']+)' filename='([^']*)'>\n(.*?)\n</document>", user, re.S)
        fields: dict[str, dict] = {}
        doc_types: dict[str, str] = {}
        for ev_id, filename, text in docs:
            blob = f"{filename}\n{text}"
            for kind, pat in _DEMO_DOC_HINTS:
                if re.search(pat, blob, re.I):
                    doc_types[ev_id] = kind
                    break
            else:
                doc_types[ev_id] = "OTHER"
            for name, label in _DEMO_LABELS.items():
                if name in fields:
                    continue
                m = re.search(rf"^\s*{label}\s*[:\-]\s*(.+?)\s*$", text, re.I | re.M)
                if m:
                    fields[name] = {"value": m.group(1), "confidence": 0.95,
                                    "evidence_id": ev_id, "page": 1}
        return {"fields": fields, "doc_types": doc_types}


def default_client():
    """OpenAI when a usable key is configured, otherwise the demo stand-in.

    A key that is present but rejected (revoked, wrong project, no quota) falls
    back rather than leaving the app dead - but `probe()` and the UI both report
    which one is actually in use, so a demo run is never mistaken for a real one.
    Set LLM_PROVIDER=openai to make an unusable key a hard startup failure.
    """
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider not in ("", "openai", "demo"):
        raise RuntimeError(f"unknown LLM_PROVIDER {provider!r}: use openai or demo")
    if provider == "demo":
        return DemoLLM()
    if provider == "openai":
        return OpenAIClient()                      # let auth errors surface
    if os.getenv("OPENAI_API_KEY"):
        try:
            return OpenAIClient()
        except Exception as exc:
            print(f"[llm] OpenAI unavailable ({type(exc).__name__}); using the demo stand-in")
            _note(str(exc))
    return DemoLLM()


_last_error: str = ""


def _note(msg: str) -> None:
    global _last_error
    _last_error = msg.splitlines()[0][:200] if msg else ""


def probe() -> dict[str, Any]:
    """What the app is really running on - surfaced by /health and the UI."""
    client = default_client()
    if isinstance(client, DemoLLM):
        return {"provider": "demo", "models": {},
                "reason": _last_error or ("no OPENAI_API_KEY set" if not os.getenv("OPENAI_API_KEY")
                                          else "OpenAI unavailable")}
    return {"provider": "openai", "models": client.models, "reason": ""}
