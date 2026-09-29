"""Only case analysis may ask a customer anything.

A missing field on its own must never produce a question or a confirmation
request. The old chain - required-field list, then a preset question keyed to the
field - is gone, and these tests exist so it cannot come back quietly: each one
sets up a case with an obviously absent fact and asserts that absence alone
raises nothing, while leaving the analysis layer free to ask when it judges the
fact material.

Run:  python -m unittest tests.test_question_authority -v
"""
from __future__ import annotations

import unittest

from pcn_appeal.models import CaseFile, EvidenceItem, FactStatus
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM

FULL = dict(
    operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12CDE",
    parking_location="Retail Park", site_postcode="M1 1AA",
    parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
    charge_amount="£100", alleged_breach="Overstayed paid time",
    operator_ata="BPA",
)


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


def case_with(drop=(), extra=None, ask=None, doc_class="NTK",
              text="Notice to Keeper\nOperator: Acme Parking Ltd"):
    """A notice with `drop` fields absent, and whatever the model chooses to ask."""
    f = {k: v for k, v in FULL.items() if k not in drop}
    f.update(extra or {})
    llm = ReferenceAnalysisLLM(
        {"extraction": [{"fields": fields(**f), "doc_types": {"E1": doc_class}}]},
        ask=list(ask or []))
    case = CaseFile("C-QA", evidence={"E1": EvidenceItem("E1", "OTHER", "notice.pdf", text=text)})
    return case, AppealPipeline(llm)


def run(case, pipe, narrative=""):
    flags = pipe.ingest(case)
    confirmable = [n for n, fa in case.facts.items() if fa.status == FactStatus.EXTRACTED]
    questions = pipe.confirm(case, {}, confirmable, narrative)
    return flags, questions


class AbsenceAloneAsksNothing(unittest.TestCase):
    """Each of these fields is missing. None of them may generate a question."""

    ABSENT = ("site_postcode", "operator_ata", "operator_name", "pcn_number",
              "charge_amount", "notice_issue_date", "vrm", "alleged_breach")

    def test_no_field_raises_a_question_merely_by_being_absent(self):
        for name in self.ABSENT:
            with self.subTest(missing=name):
                case, pipe = case_with(drop=(name,))
                _, questions = run(case, pipe)
                self.assertEqual([q["fact"] for q in questions], [],
                                 f"{name} absent must not create a question by itself")

    def test_no_field_raises_a_confirmation_flag_merely_by_being_absent(self):
        for name in self.ABSENT:
            with self.subTest(missing=name):
                case, pipe = case_with(drop=(name,))
                flags, _ = run(case, pipe)
                self.assertEqual([f for f in flags if f.startswith(("missing:", "confirm:"))], [],
                                 f"{name} absent must not create a confirmation request")

    def test_an_entirely_unread_notice_asks_nothing_of_its_own_accord(self):
        case, pipe = case_with(drop=tuple(FULL), extra={"pcn_number": "PCN000001"})
        flags, questions = run(case, pipe)
        self.assertEqual(questions, [])
        self.assertEqual([f for f in flags if f.startswith(("missing:", "confirm:"))], [])


class JurisdictionIsNeverAskedAutomatically(unittest.TestCase):
    """Unresolved jurisdiction withholds the grounds that depend on it. It does
    not interrogate the customer about where they parked."""

    def test_missing_postcode_raises_no_flag_and_no_question(self):
        case, pipe = case_with(drop=("site_postcode",))
        flags, questions = run(case, pipe)
        self.assertNotIn("confirm:jurisdiction", flags)
        self.assertEqual([f for f in flags if "jurisdiction" in f], [])
        self.assertEqual([q for q in questions if "jurisdiction" in q["fact"]
                          or "postcode" in q["fact"]], [])

    def test_the_grounds_that_need_it_are_withheld_instead(self):
        """The safeguard is withholding, not asking: an unresolved Code version
        must not let a Code-based ground into the letter."""
        case, pipe = case_with(drop=("site_postcode", "operator_ata"))
        run(case, pipe)
        out = pipe.generate(case)
        self.assertNotEqual(out.pack.code_version, "SCOP-1.1")
        self.assertIsNone(out.pack.code_version)

    def test_the_site_is_not_asked_even_when_the_code_cannot_be_resolved(self):
        """Neither the missing postcode nor the missing trade body brings the
        question back: the grounds that need them are simply not argued."""
        case, pipe = case_with(drop=("site_postcode", "operator_ata"))
        _, questions = run(case, pipe)
        self.assertEqual([q for q in questions
                          if q["fact"] in ("site_postcode", "jurisdiction")], [])


class AnalysisRemainsFreeToAsk(unittest.TestCase):
    """Cutting the automatic questions must not gag the analysis layer."""

    def test_a_material_fact_is_still_put_to_the_customer(self):
        case, pipe = case_with(ask=[{
            "fact": "payment_made", "text": "Was a parking payment made for this visit?",
            "type": "bool", "material_because": "permission defeats the alleged breach"}])
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertIn("payment_made", [q["fact"] for q in questions])

    def test_a_fact_the_narrative_already_settled_is_not_asked_again(self):
        """The same question, after the customer has already said it in their own
        words: materiality is judged against the case, not against a field list."""
        case, pipe = case_with(ask=[{
            "fact": "payment_made", "text": "Was a parking payment made for this visit?",
            "type": "bool", "material_because": "permission defeats the alleged breach"}])
        _, questions = run(case, pipe, "I paid at the machine before I walked off")
        self.assertTrue(case.has("payment_made"))
        self.assertEqual([q["fact"] for q in questions], [])


class NarrativeDoesNotSelectQuestions(unittest.TestCase):
    """What the customer typed must not be pattern-matched into a topic."""

    def test_a_child_in_the_car_does_not_raise_an_authorisation_question(self):
        case, pipe = case_with(
            extra={"alleged_breach": "Parked in a Parent and Child bay without a child"})
        _, questions = run(case, pipe, "The kids were in the car with me the whole time")
        for fact in [q["fact"] for q in questions]:
            self.assertNotRegex(fact, r"authoris|permission|permit",
                                "a narrative keyword must not summon a topic")

    def test_a_photographic_notice_raises_no_anpr_question(self):
        """No entry/exit times on the notice, so there is no ANPR duration to
        dispute - and the customer saying 'a few minutes' does not create one."""
        case, pipe = case_with(
            extra={"alleged_breach": "Parked without payment"},
            ask=[{"fact": "anpr_duration_disputed",
                  "text": "Is the recorded length of stay wrong?", "type": "bool",
                  "material_because": "anpr"}])
        _, questions = run(case, pipe, "I was only there a few minutes")
        self.assertNotIn("anpr_duration_disputed", [q["fact"] for q in questions])

    def test_anpr_reasoning_needs_the_times_on_the_notice(self):
        """With entry and exit recorded, the duration is a fact from the document
        rather than an inference from what the customer said."""
        case, pipe = case_with(extra={"entry_time": "10:00", "exit_time": "10:03"})
        run(case, pipe, "I drove in, found no space and left")
        self.assertEqual(int(case.get("total_recorded_duration_min")), 3)


class NoticeFactsAreNotReAsked(unittest.TestCase):

    def test_a_fact_already_on_the_notice_is_never_put_to_the_customer(self):
        case, pipe = case_with(ask=[
            {"fact": "pcn_number", "text": "What is the charge number?", "type": "text",
             "material_because": "already printed on the notice"},
            {"fact": "operator_name", "text": "Who issued it?", "type": "text",
             "material_because": "already printed on the notice"}])
        _, questions = run(case, pipe)
        asked = [q["fact"] for q in questions]
        self.assertNotIn("pcn_number", asked)
        self.assertNotIn("operator_name", asked)


class QuestionsCarryNothingInternal(unittest.TestCase):

    def test_a_question_shows_only_itself_and_its_answers(self):
        case, pipe = case_with(ask=[{
            "fact": "payment_made", "text": "Was a parking payment made for this visit?",
            "type": "bool",
            "material_because": "Permission to park defeats the alleged breach outright"}])
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertTrue(questions, "the fixture asks one question")
        for q in questions:
            self.assertEqual(set(q) - {"fact", "text", "type", "options"}, set())
            for value in (q["text"], *(q.get("options") or [])):
                self.assertNotRegex(str(value), r"KB-|SCOP-|POFA|ANPR|NOT_APPLICABLE",
                                    "no internal vocabulary in a question")


if __name__ == "__main__":
    unittest.main()
