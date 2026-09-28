"""What a customer is allowed to see, and what must never reach them.

These encode client requirements from live testing of an earlier system:

  * no ground/route label ("AUTHORISATION") shown to the customer
  * no legal rationale under a question ("Permission to park defeats the
    alleged breach outright...")
  * no question asked without a factual reason for it

The first two are about the surface; the third is about the gate. They are
tested together because the complaint was really one complaint: the customer
was being shown the machinery instead of being asked a question.
"""
import unittest

import yaml
from fastapi.testclient import TestClient

from pcn_appeal.api import app
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from support import ReferenceAnalysisLLM
from pcn_appeal.models import CaseFile, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline

QUESTIONS_YAML = "pcn_appeal/data/questions.yaml"

# A Euro Car Parks style Parent-and-Child bay notice: photographs of a
# stationary vehicle, one observation time, no entry/exit pair, no duration.
PHOTO_NOTICE = dict(
    operator_name="Euro Car Parks", pcn_number="ECP123456", vrm="AB12 CDE",
    parking_location="Supermarket", site_postcode="LS1 1AA",
    parking_event_date="20/03/2026", notice_issue_date="27/03/2026",
    charge_amount="100", operator_ata="BPA",
    alleged_breach="Parked in a Parent and Child bay without a child",
)


def load_questions() -> dict:
    with open(QUESTIONS_YAML) as fh:
        return yaml.safe_load(fh)["questions"]


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


def ask(narrative: str, extra: dict | None = None):
    """-> (route hints, facts asked about)"""
    f = dict(PHOTO_NOTICE, **(extra or {}))
    llm = FakeLLM({"extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}]})
    case = CaseFile("C-1", evidence={"E1": EvidenceItem("E1", "NTK", "ntk.pdf", text="Notice to Keeper")})
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    questions = pipe.confirm(case, {}, list(case.facts), narrative)
    return set(case.get("route_hints") or []), [q["fact"] for q in questions]


class QuestionsCarryNoReasoning(unittest.TestCase):
    def test_a_question_is_only_text_type_and_options(self):
        """No `why`, `rationale` or `ground` field can exist to be rendered."""
        qs = load_questions()
        allowed = {"text", "type", "options"}
        for fact, q in qs.items():
            with self.subTest(fact=fact):
                self.assertEqual(set(q) - allowed, set(), f"{fact} carries extra fields")

    def test_the_api_returns_only_text_type_options_and_fact(self):
        """A question reaching the customer carries no rationale field.

        The model is asked for `material_because` so the audit log can record why
        a question was put; analysis.py strips it. This proves the stripping.
        """
        asked = [{"fact": "permit_held", "text": "Was a permit held for this location?",
                  "type": "bool", "material_because": "internal: decides the authorisation ground"}]
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**PHOTO_NOTICE), "doc_types": {"E1": "NTK"}}]},
            ask=asked)
        case = CaseFile("C-2", evidence={"E1": EvidenceItem("E1", "NTK", "n.pdf")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        questions = pipe.confirm(case, {}, list(case.facts), "I had a permit from the store")
        self.assertTrue(questions)
        for q in questions:
            self.assertEqual(set(q) - {"text", "type", "options", "fact"}, set())
            self.assertNotIn("material_because", q)

    def test_no_question_text_explains_its_own_legal_purpose(self):
        """The client's example: 'Permission to park defeats the alleged breach
        outright, so whether it existed is the primary fact.'"""
        qs = load_questions()
        banned = ("defeats", "alleged breach", "primary fact", "this ground",
                  "we are looking at", "we're looking at", "because this")
        for fact, q in qs.items():
            low = q["text"].lower()
            for phrase in banned:
                with self.subTest(fact=fact, phrase=phrase):
                    self.assertNotIn(phrase, low)


class NoInternalLabelsOnTheCustomerSurface(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_route_codes_never_appear_in_the_customer_page(self):
        body = self.c.get("/").text
        for route in KnowledgeGraph().routes:
            with self.subTest(route=route):
                self.assertNotIn(route, body, f"route code {route} is rendered to customers")

    def test_no_engine_or_rule_vocabulary_in_the_customer_page(self):
        body = self.c.get("/").text
        for leak in ("KB-", "PP-", "AI-", "use_when", "RetrievalPack", "UNCERTAIN",
                     "Engine ", "module_id", "strength"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)

    def test_grounds_reaching_the_customer_are_labels_not_codes(self):
        """`grounds` is what a customer may see; `primary_route` is internal."""
        from pcn_appeal.api import KG
        for route, meta in KG.routes.items():
            with self.subTest(route=route):
                self.assertNotEqual(meta.get("label"), route)
                self.assertTrue(meta.get("label"))


class QuestionsNeedAFactualReason(unittest.TestCase):
    """The client's case: "Kids were in the car" must not produce a permission
    question, and a photographic notice must not produce ANPR questions."""

    def test_an_unrelated_answer_triggers_nothing(self):
        hints, asked = ask("Kids were in the car")
        self.assertEqual(hints, set())
        self.assertEqual(asked, [])

    def test_a_photographic_notice_does_not_ask_about_entry_and_exit(self):
        _, asked = ask("Kids were in the car")
        for fact in asked:
            self.assertNotIn("visit", fact)
            self.assertNotIn("duration", fact)
            self.assertNotIn("exit", fact)

    def test_permission_is_not_asked_without_a_reason(self):
        """The client's case: "Kids were in the car" must not reach a permission
        question. Only the negative is asserted here - that a permission question
        DOES appear when permission is in play is the model's judgement, verified
        against the live model rather than a stand-in.
        """
        _, unrelated = ask("Kids were in the car")
        self.assertFalse([f for f in unrelated if "permit" in f or "authoris" in f])

    def test_anpr_questions_need_an_anpr_shaped_notice(self):
        """Entry/exit times are what make a duration question meaningful."""
        _, without = ask("I was only there a few minutes")
        self.assertFalse([f for f in without if "visit" in f])


if __name__ == "__main__":
    unittest.main()
