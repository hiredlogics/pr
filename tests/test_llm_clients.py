"""OpenAIClient wiring tests.

No network: a stub stands in for the SDK surface OpenAIClient touches, so the
request it builds and the response it parses are both asserted exactly.
"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from pcn_appeal import llm
from pcn_appeal.llm import OpenAIClient
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline

MODELS = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "whisper-1"]


class StubOpenAI:
    """Mimics the openai.OpenAI surface OpenAIClient uses."""

    def __init__(self, api_key=None, models=MODELS, reply='{"ok": true}'):
        self._models = models
        self._reply = reply if callable(reply) else (lambda _kw: reply)
        self.captured = []
        outer = self

        class _Models:
            def list(self):
                return SimpleNamespace(data=[SimpleNamespace(id=m) for m in outer._models])

        class _Completions:
            def create(self, **kw):
                outer.captured.append(kw)
                msg = SimpleNamespace(content=outer._reply(kw))
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        self.models = _Models()
        self.chat = SimpleNamespace(completions=_Completions())


def build(models=MODELS, reply='{"ok": true}', env=None):
    stub = StubOpenAI(models=models, reply=reply)
    with mock.patch.dict(os.environ, env or {}, clear=False), \
            mock.patch("openai.OpenAI", return_value=stub):
        return OpenAIClient(api_key="test-key"), stub


class ModelResolution(unittest.TestCase):
    def test_picks_best_available_per_task(self):
        client, _ = build()
        self.assertEqual(client.models["extraction"], "gpt-4.1")       # gpt-5* absent
        self.assertEqual(client.models["validation"], "gpt-4.1-mini")
        self.assertEqual(client.models["drafting"], "gpt-4.1")

    def test_validation_auto_resolves_to_a_different_model(self):
        client, _ = build()
        self.assertEqual(client.models["drafting"], "gpt-4.1")
        self.assertNotEqual(client.models["validation"], client.models["drafting"])

    def test_falls_back_when_preferred_model_absent(self):
        client, _ = build(models=["gpt-4o", "gpt-4o-mini"])
        self.assertEqual(client.models["drafting"], "gpt-4o")
        self.assertEqual(client.models["validation"], "gpt-4o-mini")

    def test_single_model_key_cannot_satisfy_distinct_validator(self):
        """One model only: the other tasks can be forced onto it, the validator cannot."""
        with self.assertRaises(RuntimeError) as ctx:
            build(models=["gpt-4o"], env={"OPENAI_MODEL_EXTRACTION": "gpt-4o",
                                          "OPENAI_MODEL_QUESTIONING": "gpt-4o",
                                          "OPENAI_MODEL_DRAFTING": "gpt-4o"})
        self.assertIn("no model available for task 'validation'", str(ctx.exception))

    def test_env_override_wins(self):
        client, _ = build(env={"OPENAI_MODEL_DRAFTING": "my-tuned-model"})
        self.assertEqual(client.models["drafting"], "my-tuned-model")

    def test_no_available_model_raises_at_startup(self):
        with self.assertRaises(RuntimeError) as ctx:
            build(models=["whisper-1"])
        self.assertIn("no model available", str(ctx.exception))

    def test_explicit_overrides_may_not_point_both_at_one_model(self):
        with self.assertRaises(RuntimeError) as ctx:
            build(env={"OPENAI_MODEL_DRAFTING": "same", "OPENAI_MODEL_VALIDATION": "same"})
        self.assertIn("both resolved to 'same'", str(ctx.exception))


class RequestShape(unittest.TestCase):
    def test_forces_json_object_and_routes_per_task(self):
        client, stub = build()
        client.complete_json(task="validation", system="SYS", user="USER")
        kw = stub.captured[0]
        self.assertEqual(kw["model"], "gpt-4.1-mini")
        self.assertEqual(kw["response_format"], {"type": "json_object"})
        self.assertIn("JSON object only", kw["messages"][0]["content"])
        self.assertEqual(kw["messages"][1]["content"][0], {"type": "text", "text": "USER"})

    def test_images_become_data_urls(self):
        client, stub = build()
        client.complete_json(task="extraction", system="S", user="U", images=[b"\xff\xd8jpeg"])
        parts = stub.captured[0]["messages"][1]["content"]
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_strips_markdown_fences(self):
        client, _ = build(reply='```json\n{"a": 1}\n```')
        self.assertEqual(client.complete_json(task="drafting", system="S", user="U"), {"a": 1})


class ProviderSelection(unittest.TestCase):
    def test_demo_when_no_keys(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
                                          "LLM_PROVIDER": ""}, clear=False):
            self.assertIsInstance(llm.default_client(), llm.DemoLLM)

    def test_openai_when_key_present(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "LLM_PROVIDER": ""}, clear=False), \
                mock.patch("openai.OpenAI", return_value=StubOpenAI()):
            self.assertIsInstance(llm.default_client(), OpenAIClient)

    def test_unknown_provider_refuses(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "llama-on-a-toaster"}, clear=False):
            with self.assertRaises(RuntimeError):
                llm.default_client()


class FullPipelineOverOpenAI(unittest.TestCase):
    """End to end through the OpenAI code path, with the network stubbed."""

    def test_case_reaches_released(self):
        extraction = {"fields": {n: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
                                 for n, v in [("operator_name", "Acme Parking Ltd"),
                                              ("pcn_number", "PCN900001"),
                                              ("vrm", "LT22 XYZ"),
                                              ("site_postcode", "M1 1AA"),
                                              ("parking_event_date", "12/06/2026"),
                                              ("notice_issue_date", "02/07/2026"),
                                              ("charge_amount", "100"),
                                              ("alleged_breach", "Overstay"),
                                              ("operator_ata", "BPA")]},
                      "doc_types": {"E1": "PCN"}}
        hints = json.dumps({"routes": [{"route": "GRACE", "confidence": 0.9}]})

        # V2: grounds come from the case_analysis task, so the stub has to answer
        # it. Without this the run drafts nothing and VAL-SUBSTANCE blocks.
        analysis = json.dumps({"grounds": [{"module_id": "KB-POFA-02",
                                            "supported_by": ["notice_issue_date"],
                                            "note": "postal notice served late"}],
                               "questions": [], "not_supported": []})

        def reply(kw):
            """Dispatch on the system prompt, the way the real API sees it."""
            system = kw["messages"][0]["content"]
            if system.startswith("You extract"):
                return json.dumps(extraction)
            if system.startswith("You analyse"):
                return analysis
            return hints

        stub = StubOpenAI(reply=reply)
        with mock.patch("openai.OpenAI", return_value=stub):
            client = OpenAIClient(api_key="test-key")
        case = CaseFile("C-OAI", evidence={"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="PCN")})
        pipe = AppealPipeline(client)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "letter arrived weeks late, queue at the barrier")
        pipe.answer(case, {"permitted_period_ended": "yes", "exit_delay_min": 12})
        out = pipe.generate(case)

        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertEqual(out.pack.pofa_findings, ["POFA_POSTAL_LATE"])
        self.assertIn("PCN900001", out.letter)
        self.assertNotRegex(out.letter, r"\bI (drove|parked)\b")


if __name__ == "__main__":
    unittest.main()
