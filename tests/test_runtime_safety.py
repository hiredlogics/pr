"""Phase 0: environment truth and safety.

Production must never run a case on the demo stand-in, must say which build and
environment it is, and must not serve operator routes (trace, console, the step
routes carrying module ids / validator ids / PoFA internals) to the public.

Nothing here touches appeal logic: grounds, questions, drafting, validation and
PoFA are exercised only as far as proving they are unreachable or unchanged.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api, llm, runtime

PROD = {"APP_ENV": "production"}
CLEAN = {k: "" for k in ("APP_ENV", "RAILWAY_ENVIRONMENT_NAME", "VERCEL_ENV", "LLM_PROVIDER",
                         "OPENAI_API_KEY", "ADMIN_TRACE_TOKEN", "ADMIN_TOKEN", "BUILD_ID",
                         "RAILWAY_DEPLOYMENT_ID", "VERCEL_DEPLOYMENT_ID")}


class _WorkingOpenAI:
    SUPPORTS_IMAGES = True

    def __init__(self, *a, **k):
        self.models = {"extraction": "m", "case_analysis": "m", "drafting": "m",
                       "validation": "v"}


class _RejectedKey(Exception):
    pass


def _rejecting_openai(*a, **k):
    raise _RejectedKey("Incorrect API key provided: sk-proj-abcdEFGH1234****wxyz")


def env(**overrides):
    return mock.patch.dict(os.environ, {**CLEAN, **overrides})


class Environment(unittest.TestCase):
    def test_defaults_to_development(self):
        with env():
            self.assertEqual(runtime.environment(), "development")
            self.assertFalse(runtime.is_production())
            self.assertEqual(runtime.build_id(), "unknown")

    def test_platform_names_are_read_and_app_env_wins(self):
        with env(RAILWAY_ENVIRONMENT_NAME="production", RAILWAY_DEPLOYMENT_ID="dep-1"):
            self.assertTrue(runtime.is_production())
            self.assertEqual(runtime.build_id(), "dep-1")
        with env(APP_ENV="staging", RAILWAY_ENVIRONMENT_NAME="production"):
            self.assertEqual(runtime.environment(), "staging")
            self.assertFalse(runtime.is_production())


class ProviderPolicy(unittest.TestCase):
    def test_production_requires_explicit_openai(self):
        for provider in ("", "demo"):
            with self.subTest(provider=provider), env(**PROD, LLM_PROVIDER=provider,
                                                      OPENAI_API_KEY="sk-x"):
                with self.assertRaises(llm.ProviderPolicyError):
                    llm.default_client()

    def test_production_never_falls_back_to_demo_on_a_rejected_key(self):
        with env(**PROD, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"), \
                mock.patch.object(llm, "OpenAIClient", _rejecting_openai):
            with self.assertRaises(_RejectedKey):
                llm.default_client()

    def test_production_with_openai_builds_the_real_client(self):
        with env(**PROD, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"), \
                mock.patch.object(llm, "OpenAIClient", _WorkingOpenAI):
            self.assertIsInstance(llm.default_client(), _WorkingOpenAI)

    def test_development_fallback_is_unchanged(self):
        with env():
            self.assertIsInstance(llm.default_client(), llm.DemoLLM)
        with env(OPENAI_API_KEY="sk-x"), mock.patch.object(llm, "OpenAIClient", _rejecting_openai):
            self.assertIsInstance(llm.default_client(), llm.DemoLLM)

    def test_probe_reports_unavailable_instead_of_raising(self):
        with env(**PROD):
            p = llm.probe()
        self.assertEqual(p["provider"], "unavailable")
        self.assertIn("LLM_PROVIDER=openai", p["reason"])

    def test_key_fragments_are_redacted(self):
        with env(**PROD, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"), \
                mock.patch.object(llm, "OpenAIClient", _rejecting_openai):
            p = llm.probe()
        self.assertNotIn("abcdEFGH", p["reason"])
        self.assertNotIn("wxyz", p["reason"])
        self.assertIn("sk-[redacted]", p["reason"])

    def test_startup_check_refuses_an_unusable_production_provider(self):
        with env(**PROD):
            with self.assertRaises(RuntimeError):
                api._verify_provider_at_startup()
        with env(**PROD, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"), \
                mock.patch.object(llm, "OpenAIClient", _WorkingOpenAI), \
                mock.patch.object(api, "default_client", llm.default_client):
            api._verify_provider_at_startup()
        with env():
            api._verify_provider_at_startup()           # development: no-op


class Health(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def test_reports_build_identity(self):
        with env(BUILD_ID="build-42"):
            body = self.client.get("/health").json()
        for key in ("app_version", "environment", "build_id", "commit", "provider", "models",
                    "kb_release", "kb_source", "prompt_versions", "validator_version", "store"):
            self.assertIn(key, body)
        self.assertEqual(body["app_version"], "version_2")
        self.assertEqual(body["environment"], "development")
        self.assertEqual(body["build_id"], "build-42")
        self.assertEqual(body["status"], "ok")

    def test_app_version_can_be_overridden(self):
        with env(APP_VERSION="version_2-staging"):
            body = self.client.get("/health").json()
        self.assertEqual(body["app_version"], "version_2-staging")

    def test_production_without_the_real_provider_is_unhealthy(self):
        with env(**PROD):
            res = self.client.get("/health")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["status"], "unhealthy")
        self.assertEqual(res.json()["provider"], "unavailable")
        self.assertFalse(res.json()["vision"])

    def test_health_carries_no_key_material(self):
        with env(**PROD, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-live-secret"), \
                mock.patch.object(llm, "OpenAIClient", _rejecting_openai):
            text = self.client.get("/health").text
        self.assertNotIn("sk-live-secret", text)
        self.assertNotIn("abcdEFGH", text)


class CaseCreationOnAnUnusableProvider(unittest.TestCase):
    def test_new_case_is_a_processing_error_not_a_demo_case(self):
        client = TestClient(api.app)
        before = len(api.CASES)
        with env(**PROD):
            res = client.post("/cases")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"]["code"], "PROCESSING_ERROR")
        self.assertNotIn("stand up", res.json()["detail"]["message"])
        self.assertEqual(len(api.CASES), before, "no case may be created on a 503")


OPERATOR_ROUTES = [
    ("get", "/console"),
    ("get", "/cases/C-X/trace"),
    ("get", "/cases/C-X/appeal"),
    ("post", "/cases/C-X/generate"),
    ("post", "/cases/C-X/answers"),
    ("post", "/cases/C-X/documents"),
    ("post", "/cases/C-X/disclosure"),
]
BODIES = {"/cases/C-X/answers": {"answers": {}}, "/cases/C-X/documents": {"documents": []},
          "/cases/C-X/disclosure": {"status": "UNKNOWN", "reason": "r"}}


class OperatorRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def call(self, method, path, headers=None):
        kw = {"headers": headers or {}}
        if method == "post":
            kw["json"] = BODIES.get(path, {})
        return getattr(self.client, method)(path, **kw)

    def test_closed_in_production_when_no_token_is_configured(self):
        with env(**PROD):
            for method, path in OPERATOR_ROUTES:
                with self.subTest(path=path):
                    self.assertEqual(self.call(method, path).status_code, 403)

    def test_token_is_required_when_configured(self):
        with env(ADMIN_TRACE_TOKEN="t0ken"):
            for method, path in OPERATOR_ROUTES:
                with self.subTest(path=path):
                    self.assertEqual(self.call(method, path).status_code, 401)
                    wrong = self.call(method, path, {"Authorization": "Bearer nope"})
                    self.assertEqual(wrong.status_code, 401)

    def test_either_header_form_passes_the_guard(self):
        with env(**PROD, ADMIN_TRACE_TOKEN="t0ken"):
            for headers in ({"Authorization": "Bearer t0ken"}, {"X-Admin-Token": "t0ken"}):
                with self.subTest(headers=headers):
                    # Past the guard: the unknown case is the next thing refused.
                    res = self.call("get", "/cases/C-X/trace", headers)
                    self.assertEqual(res.status_code, 404)
                    self.assertEqual(self.call("get", "/console", headers).status_code, 200)

    def test_development_without_a_token_is_unchanged(self):
        with env():
            self.assertEqual(self.call("get", "/console").status_code, 200)
            self.assertEqual(self.call("get", "/cases/C-X/trace").status_code, 404)

    def test_customer_routes_are_not_guarded(self):
        with env(**PROD, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x"), \
                mock.patch.object(llm, "OpenAIClient", _WorkingOpenAI), \
                mock.patch.object(api, "default_client", llm.default_client):
            self.assertEqual(self.client.post("/cases").status_code, 200)
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.get("/cases/C-X/confirmation").status_code, 404)


if __name__ == "__main__":
    unittest.main()
