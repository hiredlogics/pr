"""Prompt registry tests.

The registry is a safety control, not a convenience: a drafter running without
its HARD RULES block is the failure mode these assertions exist to catch.
"""
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pcn_appeal import prompts
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack


class Registry(unittest.TestCase):
    def setUp(self):
        prompts.reset()
        self.addCleanup(prompts.reset)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write(self, body: str) -> Path:
        p = self.tmp / "prompts.yaml"
        p.write_text(textwrap.dedent(body))
        return p

    def test_every_task_has_a_non_empty_prompt(self):
        for task in prompts.TASKS:
            with self.subTest(task=task):
                self.assertTrue(prompts.system(task).strip())
                self.assertGreaterEqual(prompts.version(task), 1)

    def test_drafting_prompt_still_carries_the_hard_rules(self):
        """These lines are why the drafter cannot admit who was driving."""
        body = prompts.system("drafting")
        self.assertIn("Never identify, infer or imply who was driving", body)
        self.assertIn("Do not allege a PoFA defect unless pofa_findings is non-empty", body)
        self.assertIn("Do not state Code values unless code_version is set", body)

    def test_case_analysis_prompt_requires_recovery_first(self):
        body = prompts.system("case_analysis")
        self.assertIn("recovery", body.lower())
        self.assertIn("do_not_ask", body)
        self.assertIn("operator_requestable", body)
        self.assertIn("UNRESOLVED", body)

    def test_extraction_prompt_treats_documents_as_untrusted(self):
        self.assertIn("untrusted DATA", prompts.system("extraction"))

    def test_unknown_task_raises_rather_than_returning_empty(self):
        with self.assertRaises(prompts.PromptError):
            prompts.system("no_such_task")

    def test_missing_task_in_file_raises(self):
        p = self.write("""
            prompts:
              extraction: {version: 1, body: "x"}
        """)
        with self.assertRaises(prompts.PromptError) as ctx:
            prompts.load(p)
        self.assertIn("missing a prompt for", str(ctx.exception))

    def test_empty_body_raises(self):
        # Built from TASKS so adding a task cannot silently turn this into a
        # "missing prompt" test instead of an "empty body" one.
        body = "prompts:\n" + "".join(
            f'  {t}: {{version: 1, body: "{"   " if t == "extraction" else "x"}"}}\n'
            for t in prompts.TASKS)
        p = self.write(body)
        with self.assertRaises(prompts.PromptError) as ctx:
            prompts.load(p)
        self.assertIn("empty body", str(ctx.exception))

    def test_release_pinned_prompts_override_the_file(self):
        prompts.use_release({t: {"body": f"pinned-{t}", "version": 7} for t in prompts.TASKS})
        self.assertEqual(prompts.system("drafting"), "pinned-drafting")
        self.assertEqual(prompts.versions(), {t: 7 for t in prompts.TASKS})

    def test_release_missing_a_prompt_is_refused(self):
        with self.assertRaises(prompts.PromptError):
            prompts.use_release({"drafting": {"body": "x", "version": 1}})


class JudgeUsesTheRegistry(unittest.TestCase):
    def tearDown(self):
        prompts.reset()

    def _pack(self):
        return RetrievalPack(primary_route="POFA", secondary_routes=[], module_ids=["KB-LAND-01"],
                             verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
                             prohibited_claims=[], code_version=None, pofa_route="POSTAL",
                             pofa_findings=[], driver_status="UNIDENTIFIED",
                             jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[])

    def test_judge_is_sent_the_registered_validation_prompt(self):
        judge = FakeLLM({"validation": [{"issues": []}]})
        draft = Draft("C-1", [[DraftSentence(
            "The operator is requested to establish landowner authority.",
            [], ["KB-LAND-01"])]])
        result = ValidationEngine(judge).validate(draft, self._pack())

        self.assertTrue(result.passed, result.issues)
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(judge.calls[0]["task"], "validation")

    def test_judge_prompt_is_not_the_drafting_prompt(self):
        """Engine 4 must check with different instructions from those that wrote."""
        self.assertNotEqual(prompts.system("validation"), prompts.system("drafting"))
        self.assertIn("independent checker", prompts.system("validation"))


if __name__ == "__main__":
    unittest.main()
