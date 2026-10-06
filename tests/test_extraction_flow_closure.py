"""The verification loop: a customer's confirmation must become authoritative,
and an unchanged page must not be read again.

Reproduced on staging as "the same question keeps coming back" and a slower and
slower journey. The first defective boundary was two layers apart:

1. FactManager treated a customer's explicit confirmation of an UNCERTAIN
   reading as a duplicate of itself ("existing fact has higher authority") and
   dropped it, so the fact stayed UNCERTAIN whatever the customer said.
2. The identity state was rebuilt from document readings alone on every
   confirm, so even the identity-level VERIFIED the answer stamped was undone on
   the next Continue.
3. Each rebuild re-sent the unchanged page images to the vision model: three
   byte-identical identity reads in one journey.

The two notices below are the CP Plus and Smart Parking reference fixtures.
Nothing here is specific to either: one function drives both, from the values
each fixture already carries.

What this does NOT measure: network latency. The model is scripted, so these
tests count calls and check state, which is where the regression was. Wall-clock
time per provider call needs the live provider.

Run:  python -m unittest discover -s tests -p test_extraction_flow_closure.py -v
"""
from __future__ import annotations

import json
import time
import unittest
from datetime import date
from pathlib import Path

from pcn_appeal import document_identity as DI
from pcn_appeal.fact_graph import APPLIED, IGNORED_DUPLICATE, FactManager
from pcn_appeal.models import (
    CaseFile, EvidenceItem, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "p9_v1" / "notice_only"
NOTICES = {"CP Plus": "REF_00347261120013", "Smart Parking": "REF_sp62712518"}
PAGE = b"\x89PNG-front-of-notice"


def _fixture(ref: str) -> dict:
    case = json.loads((DATASET / f"{ref}.json").read_text())
    return {k: v for k, v in case["expected"]["extraction"]["values"].items()
            if k in ("operator_name", "pcn_number", "vrm", "parking_event_date",
                     "notice_issue_date", "parking_location", "entry_time",
                     "exit_time", "alleged_breach", "charge_amount", "site_postcode")}


class _Model(ReferenceAnalysisLLM):
    """Scripted reads. `pcn_conf` is the extractor's confidence in the charge
    number; `verify` says what the independent re-read does: "agree",
    "timeout", or "fail_once" (times out on its first call only)."""
    models = {"identity_verification": "scripted", "extraction": "scripted"}

    def __init__(self, values, pcn_conf=0.97, verify="agree"):
        fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
                  for k, v in values.items()}
        fields["pcn_number"]["confidence"] = pcn_conf
        super().__init__({"extraction": [{"fields": fields,
                                          "doc_types": {"E1": "PCN"}}]})
        self.values, self.verify, self.log = values, verify, []

    def complete_json(self, *, task, system, user, images=None):
        self.log.append(task)
        if task == "identity_verification":
            n = self.log.count("identity_verification")
            if self.verify == "timeout" or (self.verify == "fail_once" and n == 1):
                raise TimeoutError("Request timed out.")
            # "disagree": a second, equally confident reading of the same page
            # that differs from the first - the OCR-vs-OCR conflict
            read = (self.values["pcn_number"][:-1] + "9"
                    if self.verify == "disagree" else self.values["pcn_number"])
            return {"fields": {"pcn_number": {
                "candidate_value": read,
                "read_status": "VERIFIED", "confidence": 0.95,
                "evidence_id": "E1", "page": 1}}}
        if task == "extraction":
            return self.responses["extraction"][0]
        return super().complete_json(task=task, system=system, user=user, images=images)

    def count(self, task):
        return self.log.count(task)


def _journey(ref, *, pcn_conf=0.97, verify="agree", text=""):
    llm = _Model(_fixture(ref), pcn_conf, verify)
    case = CaseFile("flow", evidence={"E1": EvidenceItem(
        "E1", "PCN", "notice.png", text=text, images=[PAGE])})
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    return llm, case, pipe


def _confirm(pipe, case):
    shown = [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]
    return [q["fact"] for q in pipe.confirm(case, {}, shown, "")]


def _identity(case, name="pcn_number"):
    return (DI.load_identity_state(case) or {}).get("field_status", {}).get(name)


class _EachNotice:
    """Every test runs once per notice."""

    def each(self, fn):
        for operator, ref in NOTICES.items():
            with self.subTest(notice=operator):
                fn(ref)


# -------------------------------------------------- A / B: when to ask
class AHighConfidenceIsNotQuestioned(_EachNotice, unittest.TestCase):

    def test_a_confident_read_whose_verification_times_out_is_not_questioned(self):
        def run(ref):
            _llm, case, pipe = _journey(ref, pcn_conf=0.97, verify="timeout")
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            self.assertNotIn("pcn_number", _confirm(pipe, case))
        self.each(run)

    def test_the_successful_extraction_survives_the_failed_verification(self):
        """§6: a later step failing must not take back what was already read."""
        def run(ref):
            _llm, case, _pipe = _journey(ref, pcn_conf=0.97, verify="timeout")
            self.assertEqual(case.facts["pcn_number"].value, _fixture(ref)["pcn_number"])
            self.assertTrue(case.facts["pcn_number"].usable)
            self.assertTrue(case.facts["parking_location"].usable)
        self.each(run)


class BALowConfidenceIsQuestionedOnce(_EachNotice, unittest.TestCase):

    def test_exactly_one_question_for_the_unverifiable_field(self):
        def run(ref):
            _llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
            asked = _confirm(pipe, case)
            self.assertEqual(asked.count("pcn_number"), 1, asked)
        self.each(run)

    def test_the_photo_has_no_text_layer_to_corroborate_it(self):
        """The reason it is unverifiable: with a text layer the same read agrees
        with it and is not questioned (§A's other half)."""
        def run(ref):
            ref_values = _fixture(ref)
            text = f"Parking Charge Number: {ref_values['pcn_number']}"
            _llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout", text=text)
            self.assertNotIn("pcn_number", _confirm(pipe, case))
        self.each(run)


# ----------------------------------- C / D: the answer becomes authoritative
class CConfirmationBecomesAuthoritative(_EachNotice, unittest.TestCase):

    def _answered(self, ref, value=None):
        llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
        _confirm(pipe, case)
        shown = _fixture(ref)["pcn_number"] if value is None else value
        pipe.answer(case, {"pcn_number": shown})
        return llm, case, pipe

    def test_the_fact_is_settled_by_the_customer(self):
        def run(ref):
            _llm, case, _pipe = self._answered(ref)
            fact = case.facts["pcn_number"]
            self.assertTrue(fact.usable, fact.status)
            self.assertEqual(fact.source.kind, SourceKind.ANSWER)
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
        self.each(run)

    def test_it_stays_settled_after_continue(self):
        """The regression: Continue rebuilt the identity from the document
        readings and put the field back to UNCERTAIN."""
        def run(ref):
            _llm, case, pipe = self._answered(ref)
            _confirm(pipe, case)
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            self.assertTrue(case.facts["pcn_number"].usable)
        self.each(run)

    def test_the_question_never_comes_back(self):
        def run(ref):
            _llm, case, pipe = self._answered(ref)
            for _ in range(3):
                self.assertNotIn("pcn_number", _confirm(pipe, case))
        self.each(run)

    def test_the_identity_hold_on_the_claim_plan_is_cleared(self):
        def run(ref):
            _llm, case, pipe = self._answered(ref)
            _confirm(pipe, case)
            self.assertIsNone(DI.identity_blocks_claim_plan(case))
        self.each(run)


class DACorrectedValueSupersedesTheRead(_EachNotice, unittest.TestCase):

    def test_the_corrected_value_is_authoritative(self):
        def run(ref):
            _llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
            # the confirmation screen is where a value is corrected; the identity
            # question that follows is a closed choice between the shown value
            # and "not readable"
            shown = [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]
            pipe.confirm(case, {"pcn_number": "ZZ99887766"}, shown, "")
            _confirm(pipe, case)
            self.assertEqual(case.facts["pcn_number"].value, "ZZ99887766")
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            fields = (DI.load_identity_state(case) or {})["fields"]
            self.assertEqual(fields["pcn_number"]["canonical_value"], "ZZ99887766")
        self.each(run)


class CConflictingReadsAreSettledByTheCustomerToo(_EachNotice, unittest.TestCase):
    """Two confident readings of one page that disagree can only be settled by
    the person holding the notice, and their answer must outlive the rebuild."""

    def _conflicted(self, ref):
        # in doubt on the first read, so the targeted second read happens - and
        # disagrees with it
        llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="disagree")
        return llm, case, pipe

    def test_the_disagreement_is_put_to_the_customer_once(self):
        def run(ref):
            _llm, case, pipe = self._conflicted(ref)
            self.assertEqual(_identity(case), DI.STATUS_CONFLICT)
            self.assertEqual(_confirm(pipe, case).count("pcn_number"), 1)
        self.each(run)

    def test_their_answer_survives_the_next_continue(self):
        def run(ref):
            _llm, case, pipe = self._conflicted(ref)
            _confirm(pipe, case)
            pipe.answer(case, {"pcn_number": _fixture(ref)["pcn_number"]})
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            _confirm(pipe, case)
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            self.assertNotIn("pcn_number", _confirm(pipe, case))
            self.assertIsNone(DI.identity_blocks_claim_plan(case))
        self.each(run)


# --------------------------------------- E / F / G: refresh, resume, double-click
class EFGTheJourneyIsIdempotent(_EachNotice, unittest.TestCase):

    def test_resuming_on_a_fresh_pipeline_asks_nothing_and_reads_nothing(self):
        """E/F: a refresh or a later resume builds a new pipeline over the stored
        case; the pipeline holds no case state."""
        def run(ref):
            llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
            _confirm(pipe, case)
            pipe.answer(case, {"pcn_number": _fixture(ref)["pcn_number"]})
            reads = llm.count("identity_verification")
            resumed = AppealPipeline(llm)
            self.assertNotIn("pcn_number", _confirm(resumed, case))
            self.assertEqual(llm.count("identity_verification"), reads)
        self.each(run)

    def test_double_click_continue_repeats_no_read_and_changes_no_identity(self):
        def run(ref):
            llm, case, pipe = _journey(ref)
            _confirm(pipe, case)
            state = dict(DI.load_identity_state(case)["field_status"])
            values = {n: f.value for n, f in case.facts.items()}
            reads, extractions = llm.count("identity_verification"), llm.count("extraction")
            _confirm(pipe, case)
            self.assertEqual(DI.load_identity_state(case)["field_status"], state)
            self.assertEqual({n: f.value for n, f in case.facts.items()
                              if n in values}, values)
            self.assertEqual(llm.count("identity_verification"), reads)
            self.assertEqual(llm.count("extraction"), extractions)
        self.each(run)

    def test_an_identity_question_shown_but_not_answered_is_shown_again(self):
        """A refresh or a second click must not retire a question nobody answered."""
        def run(ref):
            _llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
            first = _confirm(pipe, case)
            second = _confirm(pipe, case)
            self.assertIn("pcn_number", first)
            self.assertIn("pcn_number", second)
        self.each(run)

    def test_a_declined_identity_question_is_not_asked_again(self):
        """"Different / not readable" is an answer: it retires the question
        rather than looping on it."""
        def run(ref):
            _llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
            _confirm(pipe, case)
            pipe.answer(case, {"pcn_number": "Different / not readable"})
            self.assertNotIn("pcn_number", _confirm(pipe, case))
        self.each(run)


# ------------------------------------- H / I / J: failure and unchanged pages
class HIJNothingIsRepeatedWithoutReason(_EachNotice, unittest.TestCase):

    def test_h_a_failed_read_then_a_good_one_persists(self):
        def run(ref):
            llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="fail_once")
            self.assertEqual(_identity(case), DI.STATUS_UNCERTAIN)    # first read failed
            _confirm(pipe, case)                                       # retried: agrees
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            reads = llm.count("identity_verification")
            _confirm(pipe, case)
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
            self.assertEqual(llm.count("identity_verification"), reads)
        self.each(run)

    def test_i_a_later_failure_does_not_restart_extraction(self):
        def run(ref):
            llm, case, pipe = _journey(ref)
            _confirm(pipe, case)
            _confirm(pipe, case)
            self.assertEqual(llm.count("extraction"), 1)
        self.each(run)

    def test_j_an_unchanged_page_is_read_once_in_a_whole_journey(self):
        """A confident notice costs one classification and one extraction, and
        no second read at all: nothing is in doubt."""
        def run(ref):
            llm, case, pipe = _journey(ref)
            for _ in range(3):
                _confirm(pipe, case)
            self.assertEqual(llm.count("extraction"), 1)
            self.assertEqual(llm.count("identity_verification"), 0)
        self.each(run)

    def test_j_a_doubtful_field_is_read_a_second_time_once(self):
        def run(ref):
            llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="agree")
            for _ in range(3):
                _confirm(pipe, case)
            self.assertEqual(llm.count("identity_verification"), 1)
            self.assertEqual(_identity(case), DI.STATUS_VERIFIED)
        self.each(run)

    def test_j_running_extraction_again_on_unchanged_pages_replays_it(self):
        def run(ref):
            llm, case, pipe = _journey(ref)
            pipe.extraction.run(case)
            self.assertEqual(llm.count("extraction"), 1)
        self.each(run)

    def test_j_a_changed_page_is_read_again(self):
        def run(ref):
            llm, case, pipe = _journey(ref)
            case.evidence["E1"].images = [PAGE + b"-a-different-photo"]
            pipe.extraction.run(case)
            self.assertEqual(llm.count("extraction"), 2)
        self.each(run)


# ------------------------------------------------------- the full journey
class TheFullJourneyReachesAnAppeal(_EachNotice, unittest.TestCase):

    def test_upload_to_appeal_with_a_question_answered_once(self):
        def run(ref):
            llm, case, pipe = _journey(ref, pcn_conf=0.6, verify="timeout")
            started = time.perf_counter()
            asked = _confirm(pipe, case)
            self.assertEqual(asked.count("pcn_number"), 1)
            pipe.answer(case, {"pcn_number": _fixture(ref)["pcn_number"]})
            for _ in range(2):
                self.assertNotIn("pcn_number", _confirm(pipe, case))
            out = pipe.generate(case)
            self.assertIsNone(DI.identity_blocks_claim_plan(case))
            self.assertIsNotNone(out.draft)
            self.assertLess(time.perf_counter() - started, 60)
            # the whole journey: one extraction, no repeated page reads
            self.assertEqual(llm.count("extraction"), 1)
            self.assertLessEqual(llm.count("identity_verification"), 2)
        self.each(run)


# ------------------------------------------------- the boundary, unit level
def _fact(value, status, kind, ref):
    return Fact("F-x", "pcn_number", value, status, FactSource(kind, ref))


class FactManagerSettlesWhatTheCustomerConfirms(unittest.TestCase):

    def _case(self, held):
        case = CaseFile("fm")
        case.put(held)
        return case

    def test_confirming_an_uncertain_reading_applies(self):
        case = self._case(_fact("AB123456", FactStatus.UNCERTAIN, SourceKind.DOCUMENT, "E1#p1"))
        FactManager.update_fact(case, _fact("AB123456", FactStatus.CONFIRMED,
                                            SourceKind.ANSWER, "identity_confirm:pcn_number"))
        held = case.facts["pcn_number"]
        self.assertEqual(held.status, FactStatus.CONFIRMED)
        self.assertEqual(held.source.kind, SourceKind.ANSWER)

    def test_a_confident_document_reading_is_still_not_rewritten(self):
        """Protect what worked: same value over a usable reading is a duplicate."""
        case = self._case(_fact("AB123456", FactStatus.EXTRACTED, SourceKind.DOCUMENT, "E1#p1"))
        result = FactManager.update_fact(case, _fact(
            "AB123456", FactStatus.ANSWERED, SourceKind.ANSWER, "answer:pcn_number"))
        self.assertEqual(result.outcome, IGNORED_DUPLICATE)
        self.assertEqual(case.facts["pcn_number"].source.kind, SourceKind.DOCUMENT)

    def test_free_text_does_not_settle_an_uncertain_reading(self):
        """Only what the customer settled counts, never what was read from prose."""
        case = self._case(_fact("AB123456", FactStatus.UNCERTAIN, SourceKind.DOCUMENT, "E1#p1"))
        result = FactManager.update_fact(case, _fact(
            "AB123456", FactStatus.ANSWERED, SourceKind.CUSTOMER_FREE_TEXT, "semantic:x"))
        self.assertEqual(result.outcome, IGNORED_DUPLICATE)
        self.assertEqual(case.facts["pcn_number"].status, FactStatus.UNCERTAIN)

    def test_a_machine_reading_does_not_unsettle_what_the_customer_settled(self):
        case = self._case(_fact("AB123456", FactStatus.CONFIRMED, SourceKind.ANSWER, "answer:x"))
        FactManager.update_fact(case, _fact("AB123456", FactStatus.UNCERTAIN,
                                            SourceKind.DOCUMENT, "E1#p1"))
        self.assertEqual(case.facts["pcn_number"].status, FactStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
