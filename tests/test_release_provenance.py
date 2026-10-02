"""Release control: can a released letter be traced back to what produced it?

The gap these cover is not a crash, it is a silent lie. Before them the store
recorded `model=None` and the literal string "VAL-1" for every case ever
validated, the released prompt versions were fetched and then ignored, and a KB
release that failed to load was replaced by the authored YAML with a printed
warning - so "is my fix deployed?" had no answer the running app could give.
"""
import unittest

from fastapi.testclient import TestClient

from pcn_appeal import prompts, version
from pcn_appeal.api import app
from pcn_appeal.drafting.drafter import LLMDrafter
from pcn_appeal.engines import validation
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (CaseFile, Draft, DraftSentence, RetrievalPack, ValidationIssue,
                               ValidationResult)
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.store import kb_source


class _Out:
    """The AppealOutput fields save_output reads."""

    def __init__(self, draft, validation_result, state):
        self.draft = draft
        self.validation = validation_result
        self.state = state
        self.pack = _pack()


def _pack() -> RetrievalPack:
    return RetrievalPack(
        primary_route="BAY", secondary_routes=[], module_ids=["KB-BAY-02"],
        verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
        prohibited_claims=[], code_version=None, pofa_route="UNRESOLVED", pofa_findings=[],
        driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[], lease_clauses=[], case_context={})


def _llm(cls=FakeLLM):
    return cls({"drafting": [{"paragraphs": [[{
        "text": "A sentence.", "fact_refs": [], "module_refs": ["KB-BAY-02"],
        "evidence_refs": []}]]}]})


class NamedModelLLM(FakeLLM):
    """A client that resolved a real model per task, as OpenAIClient does."""
    models = {"drafting": "gpt-x-drafting"}


class DraftProvenance(unittest.TestCase):
    def test_draft_records_the_model_and_prompt_version_that_wrote_it(self):
        draft = LLMDrafter(_llm(NamedModelLLM)).draft("C1", _pack())
        self.assertEqual(draft.model, "gpt-x-drafting")
        self.assertEqual(draft.prompt_version, prompts.version("drafting"))

    def test_a_stand_in_is_named_rather_than_recorded_as_no_model(self):
        """A demo/stub letter must not read back as a real provider's work."""
        draft = LLMDrafter(_llm()).draft("C1", _pack())
        self.assertEqual(draft.model, "FakeLLM")
        self.assertIsNotNone(draft.model)

    def test_dropping_blocked_sentences_keeps_the_draft_provenance(self):
        """The trimmed draft is the one that gets released, so it carries it too."""
        draft = Draft("C1", [[DraftSentence("Keep this."), DraftSentence("Drop this.")]],
                      attempt=2, model="gpt-x-drafting", prompt_version=10)
        trimmed, dropped = AppealPipeline._without_failing_sentences(
            draft, ValidationResult(False, [
                ValidationIssue("VAL-FACT", "BLOCK", "unsupported", "Drop this.")]))
        self.assertEqual(dropped, ["Drop this."])
        self.assertEqual([s.text for p in trimmed.paragraphs for s in p], ["Keep this."])
        self.assertEqual(trimmed.model, "gpt-x-drafting")
        self.assertEqual(trimmed.prompt_version, 10)


class _Cursor:
    """Records the SQL a write path actually sends, with its parameters."""

    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self.calls)

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return _Cursor(self.calls)

    def commit(self):
        pass


class StoredProvenance(unittest.TestCase):
    """What the store actually writes, without needing a database.

    `model` was a hardcoded None and `validator_version` a hardcoded "VAL-1", so
    these assert the values now come from the draft and the validator.
    """

    def _calls(self, out):
        from pcn_appeal.store import cases as case_store
        calls: list = []
        original = case_store.connect
        case_store.connect = lambda *a, **k: _Conn(calls)
        self.addCleanup(lambda: setattr(case_store, "connect", original))
        case_store.save_output(CaseFile("11111111-1111-1111-1111-111111111111"), out)
        return calls

    def test_the_draft_row_records_the_model_and_prompt_version(self):
        from pcn_appeal.models import CaseState
        draft = Draft("C1", [[DraftSentence("A sentence.")]], attempt=1,
                      model="gpt-x-drafting", prompt_version=10)
        out = _Out(draft, ValidationResult(True, []), CaseState.RELEASED)
        insert = next(c for c in self._calls(out) if "INSERT INTO drafts" in c[0])
        self.assertIn("gpt-x-drafting", insert[1])
        self.assertIn(10, insert[1])

    def test_the_validation_row_follows_the_validator_own_version(self):
        """Patched rather than compared to the current value: while VERSION still
        reads "VAL-1", asserting that string would pass against the hardcoded
        literal this replaced and prove nothing."""
        from pcn_appeal.models import CaseState
        original = validation.VERSION
        validation.VERSION = "VAL-TEST-ONLY"
        self.addCleanup(lambda: setattr(validation, "VERSION", original))
        out = _Out(Draft("C1", [[DraftSentence("A sentence.")]]),
                   ValidationResult(True, []), CaseState.RELEASED)
        insert = next(c for c in self._calls(out) if "INSERT INTO validations" in c[0])
        self.assertIn("VAL-TEST-ONLY", insert[1])


class ReleaseDriftDetection(unittest.TestCase):
    """A release serving different law from the deployed YAML must be visible."""

    def setUp(self):
        prompts.reset()
        self.addCleanup(prompts.reset)
        self.kg = KnowledgeGraph()
        self.modules = [{"module_id": m.module_id, "version": m.version}
                        for m in self.kg.modules.values()]
        self.prompts = {t: {"body": p["body"], "version": p["version"]}
                        for t, p in prompts.load().items()}

    def _release(self, modules=None, prom=None):
        # P7 B5: a release also pins each block's text digest and the curated
        # relations; a faithful fixture carries both, since their absence is
        # itself reported as drift (test_kb_release_discipline covers that).
        from pcn_appeal.store.kb_sync import DATA, _read, release_manifest
        pinned = release_manifest(_read(DATA), embedder_id="test")
        return {"kb_modules": {"modules": modules if modules is not None else self.modules},
                "prompts": prom if prom is not None else self.prompts,
                "block_texts": pinned["block_texts"], "relations": pinned["relations"],
                "release_id": "R-test"}

    def test_a_release_cut_from_this_yaml_reports_no_drift(self):
        self.assertEqual(kb_source.release_differs_from_yaml(self._release()), [])

    def test_a_stale_pinned_module_version_is_reported(self):
        stale = [dict(m) for m in self.modules]
        stale[0] = {**stale[0], "version": "0.9"}
        drift = kb_source.release_differs_from_yaml(self._release(modules=stale))
        self.assertEqual(len(drift), 1)
        self.assertIn(self.modules[0]["module_id"], drift[0])

    def test_a_stale_pinned_prompt_version_is_reported(self):
        """The case that was silently ignored: the release pinned one drafting
        prompt and the app ran another."""
        stale = {**self.prompts,
                 "drafting": {**self.prompts["drafting"],
                              "version": self.prompts["drafting"]["version"] - 1}}
        drift = kb_source.release_differs_from_yaml(self._release(prom=stale))
        self.assertEqual(len(drift), 1)
        self.assertIn("prompt drafting", drift[0])

    def test_a_module_missing_from_the_release_is_reported(self):
        drift = kb_source.release_differs_from_yaml(self._release(modules=self.modules[:-1]))
        self.assertTrue(any("authored but not in the release" in d for d in drift))

    def test_a_module_the_yaml_no_longer_has_is_reported(self):
        drift = kb_source.release_differs_from_yaml(
            self._release(modules=self.modules + [{"module_id": "KB-GONE-99", "version": "1.0"}]))
        self.assertTrue(any("KB-GONE-99" in d for d in drift))


class HealthReportsWhatIsRunning(unittest.TestCase):
    def setUp(self):
        self.health = TestClient(app).get("/health").json()

    def test_it_names_the_deployed_commit(self):
        """"Is my fix deployed?" must be answerable from the running app."""
        self.assertIn("commit", self.health)
        self.assertTrue(self.health["commit"])

    def test_it_says_whether_the_law_came_from_a_release_or_the_yaml(self):
        self.assertIn(self.health["kb_source"], ("yaml", "postgres-release"))
        self.assertIn("kb_drift", self.health)

    def test_it_names_the_prompt_and_validator_versions_in_use(self):
        self.assertEqual(self.health["prompt_versions"], prompts.versions())
        self.assertEqual(self.health["validator_version"], validation.VERSION)

    def test_it_still_distinguishes_the_demo_stand_in_from_a_real_provider(self):
        self.assertIn(self.health["provider"], ("demo", "openai"))


class BuildIdentity(unittest.TestCase):
    def test_commit_is_a_sha_or_the_honest_unknown(self):
        c = version.commit()
        self.assertTrue(c == version.UNKNOWN or len(c) >= 7, c)

    def test_short_does_not_truncate_unknown_into_nonsense(self):
        if version.commit() == version.UNKNOWN:
            self.assertEqual(version.short(), version.UNKNOWN)
        else:
            self.assertEqual(version.short(), version.commit()[:12])


if __name__ == "__main__":
    unittest.main()
