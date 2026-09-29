"""Regression cover for three defects found by running real notices through the
pipeline: a module that fired on any notice with an entry time, a validation rule
that only blocked one direction of a payment contradiction, and a legal ground
that depended on the customer retyping a value printed on the notice.

Extraction responses are injected with FakeLLM so the rules are what is under
test rather than a model's reading of a document.
"""
import unittest

from pcn_appeal.engines.extraction import PAYMENT_RECORDED, RESTRICTED_BAY, _hhmm, _keying_error
from pcn_appeal.legal import code_versions
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.rules.dsl import evaluate
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, RetrievalPack
from pcn_appeal.orchestrator import AppealPipeline

KG = KnowledgeGraph()

BASE = dict(operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12 CDE",
            parking_location="Retail Park", site_postcode="M1 1AA", parking_event_date="01/06/2026",
            notice_issue_date="05/06/2026", charge_amount="£100", alleged_breach="Overstayed paid time",
            operator_ata="BPA", entry_time="10:00", exit_time="12:47")


def make_case(extra_fields=None, case_analysis=None):
    f = dict(BASE, **(extra_fields or {}))
    fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1} for k, v in f.items()}
    responses = {"extraction": [{"fields": fields, "doc_types": {"E1": "PCN"}}]}
    if case_analysis is not None:
        # The V2 path has the analysis engine choose the grounds, so a test of a
        # KB gate has to supply the choice a model would make; DemoLLM proposes
        # none at all and every case would route to manual review.
        responses["case_analysis"] = list(case_analysis)
    llm = FakeLLM(responses)
    case = CaseFile("C-1", evidence={"E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                                                        text="Parking Charge Notice ...")})
    return case, AppealPipeline(llm)


def run(case, pipe, narrative, answers):
    pipe.ingest(case)
    pipe.confirm(case, {}, list(case.facts), narrative)
    pipe.answer(case, answers)
    return pipe.generate(case)


class KeyingErrorClassification(unittest.TestCase):
    """EX-09. MINOR and DIFFERENT_VEHICLE carry different legal consequences and
    KB-KEY-02 is barred from the minor-error outcome, so the distinction is
    computed here rather than left to a model's idea of "close enough"."""

    def test_one_substituted_character_is_minor(self):
        self.assertEqual(_keying_error("AB12CDE", "AB12CDF"), "MINOR")

    def test_one_dropped_or_inserted_character_is_minor(self):
        self.assertEqual(_keying_error("AB12CDE", "AB12CD"), "MINOR")
        self.assertEqual(_keying_error("AB12CDE", "AB12CDEX"), "MINOR")

    def test_a_different_plate_is_not_a_keying_error(self):
        self.assertEqual(_keying_error("AB12CDE", "XY99ZZZ"), "DIFFERENT_VEHICLE")

    def test_identical_registrations_are_no_error(self):
        self.assertEqual(_keying_error("AB12CDE", "AB12CDE"), "NONE")

    def test_an_absent_registration_yields_no_finding(self):
        # Not "NONE": nothing is known either way, and NONE would silently
        # satisfy a do_not_use_when gate that is meant to stay unresolved.
        self.assertIsNone(_keying_error("AB12CDE", None))
        self.assertIsNone(_keying_error(None, "AB12CDF"))

    def test_spacing_in_an_answered_registration_does_not_change_the_finding(self):
        # Answers bypass EX-08, so the comparison has to normalise for itself.
        self.assertEqual(_keying_error("AB12CDE", "AB12 CDF"), "MINOR")
        self.assertEqual(_keying_error("AB12 CDE", "AB12CDE"), "NONE")

    def test_normalisation_covers_both_registrations(self):
        case, pipe = make_case({"vrm": "ab12 cde", "vrm_entered": "ab12 cdf"})
        pipe.ingest(case)
        self.assertEqual(case.get("vrm_entered"), "AB12CDF")      # EX-08 both fields
        self.assertEqual(case.get("keying_error_type"), "MINOR")  # EX-09 derived

    def test_no_second_registration_leaves_the_ground_unasserted(self):
        case, pipe = make_case()
        pipe.ingest(case)
        self.assertIsNone(case.get("keying_error_type"))


class PaymentRecordedInDocument(unittest.TestCase):
    """EX-10. What the operator's own notice says about the money."""

    def test_a_recorded_payment_is_detected_through_a_decimal_amount(self):
        # The full stop in "4.50" is a decimal point, not a sentence end. The
        # first version of this pattern treated it as one and matched nothing,
        # which is the wording these notices actually use.
        self.assertTrue(PAYMENT_RECORDED.search("Payment of GBP 4.50 was recorded at 14:11."))

    def test_the_match_does_not_cross_a_sentence_boundary(self):
        self.assertFalse(PAYMENT_RECORDED.search(
            "Payment was not made. The charge was recorded on our system."))

    def test_absence_of_payment_is_not_a_recorded_payment(self):
        self.assertFalse(PAYMENT_RECORDED.search("No record of payment was found."))

    def test_the_fact_is_derived_from_the_evidence_text(self):
        case, pipe = make_case()
        case.evidence["E1"].text = "Parking Charge Notice. Payment of £4.50 was recorded at 14:11."
        pipe.ingest(case)
        self.assertTrue(case.get("payment_recorded_in_document"))


class PaymentContradictionIsBlocked(unittest.TestCase):
    """VAL-CONFLICT. Its docstring promised "payment status contradicting source
    facts" but only the not-paid direction was implemented, so a letter could
    argue the transaction never completed against a notice recording it as taken.
    """

    def _pack(self, **facts):
        return RetrievalPack(primary_route="PAYMENT", secondary_routes=[], module_ids=["STRUCTURAL"],
                             verified_facts=facts, fact_refs={}, missing_facts=[], evidence_refs=[],
                             prohibited_claims=[], code_version=None, pofa_route="POSTAL",
                             pofa_findings=[], driver_status="UNIDENTIFIED",
                             jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[])

    def _issues(self, text, **facts):
        draft = Draft("C-1", [[DraftSentence(text, [], ["STRUCTURAL"])]])
        return [i for i in ValidationEngine().validate(draft, self._pack(**facts)).issues
                if i.rule == "VAL-CONFLICT"]

    def test_claiming_the_payment_failed_against_the_notice_is_blocked(self):
        issues = self._issues("The transaction failed and no charge is properly due.",
                              payment_recorded_in_document=True)
        self.assertTrue(issues)
        self.assertEqual(issues[0].severity, "BLOCK")
        self.assertIn("records it as taken", issues[0].message)   # this check, not a neighbour

    def test_reporting_the_operator_s_allegation_is_not_a_contradiction(self):
        self.assertFalse(self._issues(
            "The operator claims the transaction failed.", payment_recorded_in_document=True))

    def test_the_same_sentence_stands_where_the_notice_records_no_payment(self):
        self.assertFalse(self._issues("The transaction failed and no charge is properly due."))

    def test_the_original_direction_still_blocks(self):
        self.assertTrue(self._issues("No payment was made for this visit.", payment_made=True))


class AnprDurationModuleIsGated(unittest.TestCase):
    """KB-TIME-01 ("ANPR presence is not parking time") had no triggering fact of
    its own, unlike every other module in the ANPR family, so it fired on the
    mere presence of an entry time - telling keepers of a parent-and-child bay
    notice that their "alleged parking duration is disputed"."""

    NARRATIVE = "The cameras recorded the vehicle entering and leaving"
    PROPOSED = {"grounds": [{"module_id": "KB-TIME-01"}], "questions": [], "not_supported": []}

    def _gate(self):
        return next(m for m in KG.modules.values() if m.module_id == "KB-TIME-01").use_when

    def test_the_gate_needs_the_duration_to_be_in_dispute(self):
        # Asserted against the gate itself, not through whichever selection path
        # is live: an entry and an exit time alone used to be enough.
        times = {"entry_time": "10:00", "exit_time": "12:47"}
        self.assertFalse(evaluate(self._gate(), times))
        self.assertTrue(evaluate(self._gate(), {**times, "anpr_duration_disputed": True}))

    def test_the_module_is_vetoed_when_the_duration_is_not_disputed(self):
        # The analysis engine may propose it; the KB gate still decides.
        case, pipe = make_case(case_analysis=[self.PROPOSED] * 3)
        out = run(case, pipe, self.NARRATIVE, {})
        self.assertNotIn("KB-TIME-01", out.pack.module_ids)

    def test_the_module_survives_the_veto_once_the_duration_is_disputed(self):
        case, pipe = make_case(case_analysis=[self.PROPOSED] * 3)
        out = run(case, pipe, self.NARRATIVE, {"anpr_duration_disputed": "yes"})
        self.assertIn("KB-TIME-01", out.pack.module_ids)


BAY = dict(alleged_breach="Your vehicle was parked in a Parent and Child bay without being "
                          "accompanied by a child",
           observation_time="19/09/2026 12:23", event_time="19/09/2026 12:23")


class RestrictedBayGround(unittest.TestCase):
    """KB-BAY-01. The KB had no ground at all for a bay-eligibility allegation,
    so such a notice produced a letter carrying only the unconditional
    landowner-authority paragraph."""

    def test_the_allegation_type_is_recognised(self):
        for breach in ("parked in a Parent and Child bay without being accompanied by a child",
                       "Vehicle parked in a disabled bay", "Parked in a permit holder bay",
                       "parked in a family space", "the bay was reserved"):
            self.assertTrue(RESTRICTED_BAY.search(breach), breach)

    def test_an_overstay_is_not_a_bay_allegation(self):
        for breach in ("Overstayed paid time", "Parked without payment",
                       "Exceeded maximum stay of 2 hours"):
            self.assertFalse(RESTRICTED_BAY.search(breach), breach)

    def test_times_are_normalised_out_of_a_combined_date_and_time(self):
        # Otherwise the raw "19/09/2026 12:23" is what the letter quotes back.
        self.assertEqual(_hhmm("19/09/2026 12:23"), "12:23")
        self.assertEqual(_hhmm("9:05"), "09:05")
        self.assertIsNone(_hhmm("no time here"))

    def test_a_single_instant_is_recorded_as_a_zero_minute_window(self):
        case, pipe = make_case(BAY)
        pipe.ingest(case)
        self.assertTrue(case.get("restricted_bay_alleged"))
        self.assertEqual(case.get("observation_window_min"), 0)

    def test_the_gate_accepts_a_zero_minute_window(self):
        # The whole point of the ground, and the case `is` would silently drop.
        gate = {"all": [{"is": "restricted_bay_alleged"}, {"exists": "observation_window_min"},
                        {"lte": ["observation_window_min", 5]}]}
        self.assertTrue(evaluate(gate, {"restricted_bay_alleged": True, "observation_window_min": 0}))
        self.assertFalse(evaluate(gate, {"restricted_bay_alleged": True, "observation_window_min": 45}))
        self.assertFalse(evaluate(gate, {"observation_window_min": 0}))

    def test_the_ground_reaches_the_letter_without_inventing_occupancy(self):
        analysis = {"grounds": [{"module_id": "KB-BAY-01"}], "questions": [], "not_supported": []}
        case, pipe = make_case(BAY, case_analysis=[analysis] * 3)
        out = run(case, pipe, "parent and child bay at Sainsburys", {})
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertIn("KB-BAY-01", out.pack.module_ids)
        self.assertIn("observation time of 12:23", out.letter)
        # No material occupancy account → do not invent one.
        self.assertNotRegex(out.letter, r"(?i)\ba child (was|had been|remained)\b")

    def test_material_account_contradicting_bay_allegation_reaches_the_letter(self):
        """System-wide rule: free-text that contradicts the allegation is drafted professionally."""
        analysis = {"grounds": [{"module_id": "KB-BAY-01"}], "questions": [], "not_supported": []}
        case, pipe = make_case(BAY, case_analysis=[analysis] * 3)
        out = run(case, pipe, "Left Kidd in car with their brother and ran into Sainsbury's.", {})
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertTrue(case.get("account_contradicts_allegation"))
        self.assertIn("KB-BAY-01", out.pack.module_ids)
        self.assertRegex(out.letter, r"(?i)(child remained in the vehicle|presence of children)")
        self.assertRegex(out.letter, r"(?i)inconsistent with the factual premise")
        # Customer free text must not be pasted.
        self.assertNotRegex(out.letter, r"(?i)left kidd")
        self.assertNotRegex(out.letter, r"(?i)ran into sainsbury")

    def test_bay_case_does_not_ask_ata_or_postcode_when_irrelevant(self):
        from pcn_appeal.engines.analysis import AnalysisEngine, CaseAnalysis
        analysis = {
            "grounds": [{"module_id": "KB-BAY-01"}],
            "questions": [
                {"fact": "operator_ata", "text": "Which trade association?",
                 "type": "choice", "options": ["BPA", "IPC", "NOT_SHOWN"],
                 "material_because": "code"},
                {"fact": "site_postcode", "text": "What is the site postcode?",
                 "type": "text", "material_because": "jurisdiction"},
            ],
            "not_supported": [],
        }
        case, pipe = make_case(BAY, case_analysis=[analysis] * 3)
        pipe.ingest(case)
        case.facts.pop("operator_ata", None)
        case.facts.pop("site_postcode", None)
        # Jurisdiction already known from fixture postcode before we popped it —
        # re-derive as UNKNOWN so the postcode question would otherwise look useful.
        case.facts.pop("jurisdiction", None)
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        case.put(Fact("F-jur", "jurisdiction", "UNKNOWN", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "t")))
        eng = AnalysisEngine(KG, FakeLLM({}))
        result = CaseAnalysis(module_ids=["KB-BAY-01"])
        # restricted_bay + window already on case after ingest
        kept = eng._safe_questions(case, analysis["questions"], result)
        asked = {q["fact"] for q in kept}
        self.assertNotIn("operator_ata", asked)
        self.assertNotIn("site_postcode", asked)

    def test_invented_ata_and_postcode_fact_names_are_also_dropped(self):
        """Model wording must not bypass the materiality gate via new snake_case."""
        from pcn_appeal.engines.analysis import AnalysisEngine, CaseAnalysis
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        proposed = [
            {"fact": "euro_car_parks_trade_association",
             "text": "Which trade association does Euro Car Parks belong to, if this is known (for example, BPA or IPC)?",
             "type": "choice", "options": ["BPA", "IPC", "NOT_SHOWN"]},
            {"fact": "sainsburys_cromwell_road_postcode",
             "text": "What is the postcode of the Sainsbury's Cromwell Road site where this notice was issued, if this is known?",
             "type": "text"},
        ]
        case, pipe = make_case(BAY, case_analysis=[{"grounds": [{"module_id": "KB-BAY-01"}],
                                                    "questions": [], "not_supported": []}] * 3)
        pipe.ingest(case)
        case.facts.pop("operator_ata", None)
        case.facts.pop("site_postcode", None)
        case.put(Fact("F-jur", "jurisdiction", "UNKNOWN", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "t")))
        eng = AnalysisEngine(KG, FakeLLM({}))
        kept = eng._safe_questions(case, proposed, CaseAnalysis(module_ids=["KB-BAY-01"]))
        self.assertEqual(kept, [])


class FactsPrintedOnTheNoticeCanBeAnswered(unittest.TestCase):
    """An unresolved trade association withholds every Code-based ground (R-01)
    and an unknown jurisdiction removes the PoFA route entirely. Both were
    dropped in silence because neither fact had a question."""

    def test_an_answered_postcode_resolves_the_jurisdiction(self):
        # No postcode on the notice, which is the situation the question exists for.
        case, pipe = make_case({**BAY, "site_postcode": None})
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "parent and child bay")
        self.assertNotEqual(case.get("jurisdiction"), "ENGLAND_WALES")
        pipe.answer(case, {"site_postcode": "SW7 4RR"})
        pipe.generate(case)
        # Re-derived at reasoning time: confirm() promotes the UNKNOWN placeholder,
        # so treating a confirmed value as final would make it permanent.
        self.assertEqual(case.get("jurisdiction"), "ENGLAND_WALES")

    def test_an_answered_trade_association_resolves_the_code_version(self):
        case, pipe = make_case(BAY)
        out = run(case, pipe, "parent and child bay", {"operator_ata": "BPA"})
        self.assertIsNotNone(out.pack.code_version)

    def test_not_shown_is_reported_as_a_missing_ata_not_a_date_problem(self):
        from datetime import date
        _, status = code_versions.resolve(date(2026, 9, 19), "NOT_SHOWN")
        self.assertEqual(status, "UNRESOLVED:ata_unknown")

    def test_a_corrected_jurisdiction_is_not_overwritten_by_the_postcode(self):
        case, pipe = make_case(BAY)
        pipe.ingest(case)
        pipe.confirm(case, {"jurisdiction": "SCOTLAND"}, [], "parent and child bay")
        pipe.answer(case, {"site_postcode": "SW7 4RR"})     # England & Wales
        pipe.generate(case)
        self.assertEqual(case.get("jurisdiction"), "SCOTLAND")


if __name__ == "__main__":
    unittest.main()
