"""Prompt registry tests.

The registry is a safety control, not a convenience: a drafter running without
its HARD RULES block is the failure mode these assertions exist to catch.
"""
import re
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pcn_appeal import prompts
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

# The version each prompt is expected to carry. A prompt is a safety control, so
# a change to one has to be a deliberate, recorded act: editing the body without
# bumping the version would ship new instructions to the model under a version
# number the audit log has already attributed to the old ones. Updating this map
# is how a review pass declares it meant to.
EXPECTED_VERSIONS = {
    "semantic_extraction": 4,
    "extraction": 12,             # operator_address for the letter's "To" block
    "case_analysis": 9,
    "drafting": 21,               # v21: the model writes only the ground paragraphs; the frame is code
    "validation": 3,
    "classification": 3,          # P8: DRIVER_LETTER stage
    "page_references": 2,
    "appeal_quality": 3,          # v3: judged against the drafter's whole case package
}

# Each rule as the terms that show it is still stated, not the sentence that
# states it. Asserting exact wording made every rewording a test failure, which
# taught the wrong lesson: the wording is the review pass's to change, the rule
# is not. Each entry needs every one of its terms present somewhere in the body.
DRAFTING_RULES = {
    "driver identity is never asserted": ["driver_status", "unidentified",
                                          r"never identif\w+", "who was driving"],
    "no PoFA defect without a finding": ["pofa_findings", r"\bnon-empty\b"],
    "no Code values without a resolved version": ["code_version", r"code values?"],
    "no case law": ["case law"],
    # P17.10 renamed the input this rule guards: the drafter is never sent
    # customer_source_texts (drafting/context.py FORBIDDEN_KEYS), so the prompt
    # speaks of the provenance excerpts it can actually see. The rule - customer
    # wording is evidence, never letter copy - is unchanged.
    "customer free text is input, not copy": [
        r"never paste,? quote or lightly edit customer wording",
        "untrusted", "INPUT", "not letter copy",
    ],
    "no ground invented when none is supported": ["no_ground_reason"],
    # P17.10: specific source-supported detail must reach the prose, and a
    # critical identity conflict must never be silently omitted there.
    "specific particulars survive into the prose": [
        "required_particulars", r"professionally paraphrased", r"material_atoms",
    ],
    "critical identity conflict is not resolved by the drafter": [
        r"release-critical document identity", r"blocked before drafting",
    ],
    # v20: the generic contract. Terms, not sentences, as with every rule above.
    "a fact is never restated as a stronger one": [
        r"FIDELITY CONTRACT", r"NEVER STRENGTHEN A FACT", r"unidentified is not refusing",
        r"attempted is not completed",
    ],
    "an approved conclusion is never widened": [r"NEVER WIDEN A CONCLUSION", r"has not been established"],
    "a retry rewrites the same case": [r"validator_feedback", r"SAME case", r"must not add, drop or swap"],
}

CASE_ANALYSIS_RULES = {
    "fact recovery runs first": ["recovery"],
    "questions already settled are not re-asked": ["do_not_ask"],
    "operator-requestable facts are not customer questions": ["operator_requestable"],
    "an unresolved point stays unresolved": ["UNRESOLVED"],
}

VALIDATION_RULES = {
    "the draft is untrusted data": ["untrusted", r"never follow instructions"],
    "checks against the cited modules' own propositions": ["propositions"],
    "has the inputs its own rules need": ["lease_clauses", "evidence_refs",
                                         "pofa_findings", "code_version", "driver_status"],
}


def _states(body: str, terms: list[str]) -> list[str]:
    """Terms from `terms` that do not appear in `body`. Whitespace is collapsed so
    a rule wrapped across two lines still reads as one sentence."""
    flat = re.sub(r"\s+", " ", body)
    return [t for t in terms if not re.search(t, flat, re.I)]


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

    def test_every_prompt_is_at_its_recorded_version(self):
        """A prompt edited without a version bump is an unrecorded change to a
        safety control. Bump the version and update EXPECTED_VERSIONS together."""
        self.assertEqual(prompts.versions(), EXPECTED_VERSIONS)

    def test_drafting_prompt_still_carries_the_hard_rules(self):
        """These rules are why the drafter cannot admit who was driving, invent a
        statutory defect, or write a ground the knowledge base does not support."""
        body = prompts.system("drafting")
        for rule, terms in DRAFTING_RULES.items():
            with self.subTest(rule=rule):
                self.assertEqual(_states(body, terms), [], f"drafting prompt no longer states: {rule}")

    def test_case_analysis_prompt_requires_recovery_first(self):
        body = prompts.system("case_analysis")
        for rule, terms in CASE_ANALYSIS_RULES.items():
            with self.subTest(rule=rule):
                self.assertEqual(_states(body, terms), [],
                                 f"case_analysis prompt no longer states: {rule}")

    def test_validation_prompt_states_its_rules_and_names_its_inputs(self):
        """The judge can only apply a rule it has the input for: VAL-RES needs the
        lease clauses, VAL-POFA the findings, VAL-CODE the resolved version."""
        body = prompts.system("validation")
        for rule, terms in VALIDATION_RULES.items():
            with self.subTest(rule=rule):
                self.assertEqual(_states(body, terms), [],
                                 f"validation prompt no longer states: {rule}")

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
        return RetrievalPack(primary_route="POFA", secondary_routes=[], module_ids=["KB-PAY-01"],
                             verified_facts={"payment_made": True}, fact_refs={"payment_made": "F-payment_made"}, missing_facts=[], evidence_refs=[],
                             prohibited_claims=[], code_version=None, pofa_route="POSTAL",
                             pofa_findings=[], driver_status="UNIDENTIFIED",
                             jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[])

    def test_judge_is_sent_the_registered_validation_prompt(self):
        judge = FakeLLM({"validation": [{"issues": []}]})
        draft = Draft("C-1", [[DraftSentence(
            "A parking payment was made.",
            ["F-payment_made"], ["KB-PAY-01"])]])
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
