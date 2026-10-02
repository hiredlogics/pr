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



# =========================================================== P3 Question Authority
# Every question is approved or rejected by engines/question_authority.py before
# a customer sees it. The classes below are the P3 spec tests and the rules
# behind them: dedupe, R1 (a supported module), R2-R4, R5 (materiality),
# customer-safe output, zero questions, one question at a time, admin trace.

from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import customer_safe
from pcn_appeal.engines import question_authority as qa
from pcn_appeal.engines.question_authority import APPROVED, REJECTED, QuestionAuthority
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import Fact, FactSource, SourceKind

KG = KnowledgeGraph()
CUSTOMER_FIELDS = {"fact", "text", "type", "options"}


def q(fact, text, qtype="bool", **kw):
    return {"fact": fact, "text": text, "type": qtype,
            "material_because": "internal", **kw}


PAYMENT = q("payment_made", "Was a parking payment made for this visit?")
PERMIT = q("permit_held", "Was a permit or permission to park held for this location?")
GENUINE = q("genuine_customer",
            "Was the visit connected with genuine use of the premises at this location?")


def decisions(case):
    return {(r["target_fact"], r["decision"]): r for r in qa.trace(case)}


def rejected_because(case, fact):
    rows = [r for r in qa.trace(case) if r["target_fact"] == fact and r["decision"] == REJECTED]
    return rows[-1]["reason"] if rows else None


def answer_fact(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.ANSWERED,
                  FactSource(SourceKind.ANSWER, f"answer:{name}")))


class P3SpecTests(unittest.TestCase):

    def test_1_left_and_came_back_asks_leave_and_return_not_genuine_visit(self):
        case, pipe = case_with(ask=[GENUINE])
        _, questions = run(case, pipe,
                           "I went shopping, forgot my purse, left and came back.")
        self.assertEqual([h["hypothesis"] for h in case.fact_hypotheses],
                         ["possible_multiple_visits"])
        self.assertEqual(questions, [{
            "fact": "multiple_visits", "type": "bool",
            "text": "Did the vehicle leave the car park and return later that day?"}])
        self.assertIn("visited_premises", rejected_because(case, "genuine_customer"))

    def test_2_a_stated_visit_is_not_asked_back(self):
        case, pipe = case_with(ask=[GENUINE])
        case.evidence["R1"] = EvidenceItem("R1", "RECEIPT", "receipt.jpg", text="Receipt")
        _, questions = run(case, pipe, "I went to Sainsbury's to shop.")
        self.assertIs(case.get("visited_premises"), True)
        self.assertNotIn("genuine_customer", [x["fact"] for x in questions])
        self.assertEqual(questions, [])
        self.assertEqual(rejected_because(case, "genuine_customer"),
                         "already established by what the customer said (visited_premises=true)")

    def test_3_no_payment_question_for_a_no_parking_allegation(self):
        for breach in ("Parked in a No Parking Area", "Stopped in a no stopping zone",
                       "Parked on yellow hatched lines"):
            with self.subTest(breach=breach):
                case, pipe = case_with(extra={"alleged_breach": breach}, ask=[PAYMENT])
                _, questions = run(case, pipe, "I want to challenge this charge")
                self.assertEqual([x for x in questions if "pay" in x["fact"]], [])
                self.assertRegex(rejected_because(case, "payment_made"),
                                 r"^R1: the allegation \(prohibition\) cannot be answered")

    def test_3_control_the_same_question_is_asked_for_an_overstay(self):
        case, pipe = case_with(ask=[PAYMENT])          # "Overstayed paid time"
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertEqual([x["fact"] for x in questions], ["payment_made"])

    def test_4_confirmed_children_present_is_not_asked(self):
        for asked_as in ("children_present", "child_occupant_present"):
            with self.subTest(asked_as=asked_as):
                case, pipe = case_with(
                    extra={"alleged_breach": "Parked in a Parent and Child bay without a child"},
                    ask=[q(asked_as, "Were any children in the vehicle?")])
                pipe.ingest(case)
                answer_fact(case, "children_present", True)
                confirmable = [n for n, f in case.facts.items()
                               if f.status == FactStatus.EXTRACTED]
                questions = pipe.confirm(case, {}, confirmable, "I want to appeal")
                self.assertEqual([x for x in questions if "child" in x["fact"]], [])
                # Rejected as known, whichever name it was asked under (the
                # analysis pre-check or the authority; both are in the trace).
                rows = [r for r in qa.trace(case) if "child" in r["target_fact"]]
                self.assertTrue(rows)
                self.assertTrue(all(r["decision"] == REJECTED for r in rows))
                self.assertRegex(rows[-1]["reason"], r"already (confirmed|known)")

    def test_5_no_supported_module_means_zero_questions(self):
        case, pipe = case_with(ask=[
            q("saw_the_signs", "Did you see the signs when you arrived?"),
            q("weather_conditions", "What was the weather like?", "text")])
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertEqual(questions, [])
        self.assertEqual(case.pending_questions, [])
        for fact in ("saw_the_signs", "weather_conditions"):
            self.assertEqual(rejected_because(case, fact),
                             "R1: no in-force module depends on this fact")


class DuplicatePrevention(unittest.TestCase):

    def test_a_previous_question_is_not_asked_again(self):
        case, pipe = case_with(ask=[PAYMENT])
        run(case, pipe, "I want to challenge this charge")
        again = pipe.answer(case, {})                       # skipped, re-analysed
        self.assertNotIn("payment_made", [x["fact"] for x in again])
        self.assertRegex(rejected_because(case, "payment_made"), r"already asked")
        # The authority holds the line on its own, whatever reaches it.
        r = QuestionAuthority(KG).review(case, [PAYMENT])
        self.assertEqual(r.rows[-1]["reason"], "question already asked")

    def test_a_previous_answer_is_not_asked_again(self):
        case, pipe = case_with(ask=[PAYMENT])
        case.raw_answers["payment_made"] = "not sure"
        run(case, pipe, "I want to challenge this charge")
        self.assertEqual(rejected_because(case, "payment_made"),
                         "customer already answered this")

    def test_the_same_wording_under_two_names_is_asked_once(self):
        case, pipe = case_with(ask=[PAYMENT, q("paid_for_parking", PAYMENT["text"])])
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertEqual([x["fact"] for x in questions], ["payment_made"])
        self.assertEqual(decisions(case)[("paid_for_parking", REJECTED)]["decision"], REJECTED)

    def test_the_authority_rejects_repeated_wording_and_a_repeated_fact(self):
        case, pipe = case_with()
        pipe.ingest(case)
        r = QuestionAuthority(KG).review(case, [
            PERMIT, q("visitor_authorised", PERMIT["text"]), dict(PERMIT, text="Did you hold a permit?")])
        self.assertEqual([x["fact"] for x in r.approved], ["permit_held"])
        self.assertEqual([x["reason"] for x in r.rows[1:]],
                         ["duplicate: the same question was already asked",
                          "duplicate: another question for this fact was approved this round"])
        # Shown last round: the same wording is not shown again under a new name.
        again = QuestionAuthority(KG).review(case, [q("visitor_authorised", PERMIT["text"])])
        self.assertEqual(again.rows[-1]["reason"], "duplicate: the same question was already asked")

    def test_a_hypothesis_owns_its_fact(self):
        """The model asking the same thing in its own words is a duplicate."""
        case, pipe = case_with(ask=[q("multiple_visits", "Did you visit twice?")])
        _, questions = run(case, pipe, "I left and came back.")
        self.assertEqual([x["text"] for x in questions],
                         ["Did the vehicle leave the car park and return later that day?"])
        rows = [r for r in qa.trace(case) if r["target_fact"] == "multiple_visits"]
        self.assertEqual({(r["source"], r["decision"]) for r in rows},
                         {("hypothesis", APPROVED), ("case_analysis", REJECTED)})


class ModuleRequirement(unittest.TestCase):
    """R1: a question must serve an in-force module the case can raise."""

    def setUp(self):
        self.case, pipe = case_with()
        pipe.ingest(self.case)
        self.auth = QuestionAuthority(KG)

    def review(self, *cands):
        return self.auth.review(self.case, [dict(c) for c in cands])

    def test_a_named_module_must_depend_on_the_fact(self):
        r = self.review(dict(PAYMENT, related_module="KB-SIGN-01"))
        self.assertEqual(r.shown, [])
        self.assertEqual(r.rows[-1]["reason"], "R1: KB-SIGN-01 does not depend on this fact")

    def test_the_related_module_is_recorded(self):
        [shown] = self.review(PAYMENT).shown
        self.assertEqual((shown["related_module"], shown["target_fact"]),
                         ("KB-PAY-01", "payment_made"))


class Materiality(unittest.TestCase):
    """R5: if every answer leads to the same result, the question is rejected."""

    def setUp(self):
        self.case, pipe = case_with()
        pipe.ingest(self.case)
        self.auth = QuestionAuthority(KG)

    def test_yes_and_no_give_the_same_result(self):
        # Genuine-customer ground needs a receipt or bank statement: without one,
        # yes and no both leave it closed.
        r = self.auth.review(self.case, [GENUINE])
        self.assertEqual(r.rows[-1]["reason"], "R5: every possible answer leads to the same result")

    def test_the_same_question_is_material_once_the_evidence_exists(self):
        self.case.evidence["R1"] = EvidenceItem("R1", "RECEIPT", "receipt.jpg", text="Receipt")
        [shown] = self.auth.review(self.case, [GENUINE]).shown
        self.assertEqual(shown["related_module"], "KB-CUST-01")

    def test_a_settled_fact_elsewhere_closes_the_gate(self):
        # A keying-error ground needs a payment; "no payment" closes it whatever
        # the error type.
        answer_fact(self.case, "payment_made", False)
        r = self.auth.review(self.case, [q("keying_error_type", "What kind of mistake was it?",
                                           "choice", options=["MINOR", "DIFFERENT_VEHICLE"])])
        self.assertEqual(r.shown, [])
        self.assertIn("R5", r.rows[-1]["reason"])

    def test_impacts_are_stated(self):
        [shown] = self.auth.review(self.case, [PAYMENT]).shown
        self.assertEqual(shown["impact_if_no"], "KB-PAY-01 does not apply")
        self.assertIn("KB-PAY-01", shown["impact_if_yes"])


class CustomerCanAnswer(unittest.TestCase):
    """R3 / R4."""

    def setUp(self):
        self.case, pipe = case_with()
        pipe.ingest(self.case)
        self.auth = QuestionAuthority(KG)

    def reason(self, cand):
        r = self.auth.review(self.case, [cand])
        self.assertEqual(r.shown, [])
        return r.rows[-1]["reason"]

    def test_no_legal_interpretation(self):
        self.assertEqual(self.reason(q("permit_held", "Is the car park relevant land under PoFA?")),
                         "R4: asks the customer for a legal interpretation")

    def test_nothing_only_the_operator_holds(self):
        self.assertRegex(self.reason(q("landowner_contract_dates", "When did the contract start?",
                                       "text")), r"^R4: only the operator holds this")

    def test_no_driver_identity(self):
        self.assertEqual(self.reason(q("permit_held", "Who was driving the car?", "text")),
                         "R4: asks about driver identity")

    def test_the_documents_answer_notice_fields(self):
        case, pipe = case_with(drop=("pcn_number",))
        pipe.ingest(case)
        r = QuestionAuthority(KG).review(case, [q("pcn_number", "What is the charge number?",
                                                  "text")])
        self.assertEqual(r.rows[-1]["reason"], "R3: a notice field; the documents answer it")

    def test_engine_derived_facts_are_not_asked(self):
        self.assertEqual(self.reason(q("visited_premises", "Did you visit the premises?")),
                         "R3: derived by the engines, not asked")


class OneQuestionAtATime(unittest.TestCase):

    def test_one_is_shown_and_the_rest_wait(self):
        case, pipe = case_with(ask=[PERMIT, PAYMENT])
        _, questions = run(case, pipe, "I want to challenge this charge")
        # Highest impact first: payment (strength 85) outranks a permit (70).
        self.assertEqual([x["fact"] for x in questions], ["payment_made"])
        self.assertNotIn("permit_held", case.asked_questions)
        row = decisions(case)[("permit_held", APPROVED)]
        self.assertEqual((row["shown"], row["priority"]), (False, 2))
        nxt = pipe.answer(case, {"payment_made": "no"})
        self.assertEqual([x["fact"] for x in nxt], ["permit_held"])

    def test_least_effort_breaks_a_tie(self):
        case, pipe = case_with()
        pipe.ingest(case)
        r = QuestionAuthority(KG).review(case, [
            q("payment_method", "How was the payment made?", "text", related_module="KB-PAY-01"),
            PAYMENT])
        self.assertEqual([x["fact"] for x in r.approved][:1], ["payment_made"])

    def test_case_integrity_comes_first(self):
        case, pipe = case_with()
        pipe.ingest(case)
        r = QuestionAuthority(KG).review(case, [
            PAYMENT,
            {"fact": "pcn_number", "text": "Which charge number is on the notice?",
             "type": "choice", "options": ["A1", "B2"], "source": qa.CONFLICT}])
        self.assertEqual([x["fact"] for x in r.shown], ["pcn_number"])


class ZeroQuestions(unittest.TestCase):

    def test_a_thin_pack_asks_nothing(self):
        """Nothing gates a ground and the model asks nothing: silence."""
        case, pipe = case_with()
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertEqual(questions, [])

    def test_the_thin_pack_rule_is_gone_from_the_prompt(self):
        from pcn_appeal import prompts
        body = prompts.system("case_analysis")
        self.assertNotRegex(body, r"(?i)thin packs?:|MUST ask 1|zero questions and only")
        self.assertIn("Zero questions is a valid outcome", body)

    def test_no_situation_bank_in_the_code(self):
        src = Path(__file__).resolve().parents[1].joinpath(
            "pcn_appeal", "engines", "analysis.py").read_text()
        self.assertNotIn("SITUATION_FALLBACK", src)
        self.assertNotIn("_ensure_situation_questions", src)


class CustomerSafeOutput(unittest.TestCase):

    def test_the_question_object_has_every_field_internally(self):
        case, pipe = case_with()
        pipe.ingest(case)
        [shown] = QuestionAuthority(KG).review(case, [PAYMENT]).shown
        for k in ("question_id", "text", "target_fact", "related_module", "material_reason",
                  "impact_if_yes", "impact_if_no"):
            self.assertTrue(shown.get(k), k)

    def test_the_customer_sees_only_the_question(self):
        case, pipe = case_with(ask=[PAYMENT])
        _, questions = run(case, pipe, "I want to challenge this charge")
        self.assertTrue(questions)
        for x in questions + list(case.pending_questions):
            self.assertLessEqual(set(x), CUSTOMER_FIELDS)
            self.assertEqual(customer_safe.leaks(x), [])
            self.assertEqual(customer_safe.internal_ids(x["text"]), [])

    def test_internal_keys_are_scrubbed_if_they_ever_reach_a_payload(self):
        case, pipe = case_with()
        pipe.ingest(case)
        [shown] = QuestionAuthority(KG).review(case, [PAYMENT]).shown
        clean = customer_safe.scrub({"questions": [shown]})
        self.assertLessEqual(set(clean["questions"][0]), CUSTOMER_FIELDS)
        self.assertNotRegex(str(clean), r"KB-|R5|impact")


class AdminTrace(unittest.TestCase):

    def test_every_generated_question_has_a_row(self):
        case, pipe = case_with(ask=[PAYMENT, GENUINE,
                                    q("pcn_number", "What is the charge number?", "text")])
        run(case, pipe, "I went to the shop")
        rows = qa.trace(case)
        got = {(r["target_fact"], r["decision"]) for r in rows}
        self.assertIn(("payment_made", APPROVED), got)
        self.assertIn(("genuine_customer", REJECTED), got)
        self.assertIn(("pcn_number", REJECTED), got)      # dropped by the pre-checks
        for r in rows:
            for k in ("candidate_question", "related_module", "target_fact", "decision", "reason"):
                self.assertIn(k, r)

    def test_the_trace_is_admin_only(self):
        from pcn_appeal import api
        case, pipe = case_with(ask=[PAYMENT])
        run(case, pipe, "I want to challenge this charge")
        api.CASES[case.case_id] = {"case": case, "pipe": None, "flags": [], "questions": [],
                                   "output": None}
        self.addCleanup(api.CASES.pop, case.case_id, None)
        client = TestClient(api.app)
        with mock.patch.dict("os.environ", {"ADMIN_TRACE_TOKEN": "", "ADMIN_TOKEN": "",
                                            "APP_ENV": "development"}):
            body = client.get(f"/cases/{case.case_id}/facts").json()
        self.assertEqual(body["question_trace"][0]["related_module"], "KB-PAY-01")
        with mock.patch.dict("os.environ", {"ADMIN_TRACE_TOKEN": "t"}):
            self.assertEqual(client.get(f"/cases/{case.case_id}/facts").status_code, 401)
        customer = client.get(f"/cases/{case.case_id}").json()
        self.assertNotRegex(str(customer), "question_trace|related_module|KB-PAY")

if __name__ == "__main__":
    unittest.main()
