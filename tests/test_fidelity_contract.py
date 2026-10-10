"""The drafting fidelity contract: facts + grounds + approved wording = one meaning.

correct facts + correct supported grounds  ->  the model sometimes adds, changes,
exaggerates, omits or misattributes meaning. These tests hold the generic layer that
stops that for EVERY ground, so none of them is about one operator, site, ground or
wording: each rule is exercised with several unrelated operators, places and
phrasings, and the same meaning must get the same answer however it is worded.

  VAL-STRENGTH        a sentence may not assert more than the case state establishes
  quality judge       hard findings are rewritten over the SAME case, then hold release
  rewrite contract    a retry is handed the same package and told it may not re-plan
  unseen cases        new operators / places / allegations / stories, through the pipeline
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from pcn_appeal.drafting import quality_judge
from pcn_appeal.drafting.drafter import LLMDrafter, rewrite_contract
from pcn_appeal.drafting.quality_judge import (HARD_FLAGS, PASS, REWRITE, SCORES,
                                               QualityJudge, hard_findings,
                                               rewrite_feedback, verdict)
from pcn_appeal.engines import assertion_strength as strength
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.models import (CaseState, Draft, DraftSentence, EvidenceItem,
                               RetrievalPack)

GOOD = {name: 10 for name in SCORES}


def pack(facts=None, wording="", approved=("KB-X-01",), **over):
    base = dict(
        primary_route="X", secondary_routes=[], module_ids=list(approved),
        verified_facts=dict(facts or {}), fact_refs={}, missing_facts=[],
        evidence_refs=[], prohibited_claims=[], code_version=None,
        pofa_route="POSTAL", pofa_findings=[], driver_status="UNIDENTIFIED",
        jurisdiction="ENGLAND_WALES",
        context_chunks=([{"module_id": approved[0], "kind": "module", "text": wording}]
                        if wording else []),
        lease_clauses=[], claim_plan={"status": "LOCKED", "approved": list(approved)})
    base.update(over)
    return RetrievalPack(**base)


# --------------------------------------------------------------------------- 1
class AFactIsNeverRestatedAsAStrongerOne(unittest.TestCase):
    """The weaker fact in, the stronger claim out - blocked, in any wording."""

    # (sentence, the unlicensed claim kind). Operators, places and phrasing differ on
    # purpose: the guard is a claim kind, not a case.
    STRONGER = [
        ("Having been asked, the keeper refuses to identify the driver of the vehicle.", "driver-refusal"),
        ("Northgate Bays Ltd's letter was answered; the keeper declined to say who was driving.", "driver-refusal"),
        ("Payment was made for the stay at Harbour Quay.", "payment-completed"),
        ("The fee had been successfully paid before the vehicle left.", "payment-completed"),
        ("The vehicle was authorised to park at Lyndale Retail Park.", "authorisation-established"),
        ("Permission existed for the vehicle to be on the land.", "authorisation-established"),
        ("The keeper held a valid permit for the bay at the time.", "valid-permit"),
        ("This is a customers-only car park.", "customer-only-site"),
        ("The overstay was proven by the operator's own images.", "allegation-adopted"),
        ("The Parking Charge Notice is therefore unlawful.", "conclusion-widened"),
        ("The charge was unenforceable from the outset.", "conclusion-widened"),
        ("The operator deliberately ignored the information it held.", "intention-attributed"),
    ]

    def test_each_stronger_claim_is_blocked_when_the_case_state_does_not_license_it(self):
        p = pack({"pcn_number": "X1"}, wording="Keeper liability has not been established.")
        for text, kind in self.STRONGER:
            with self.subTest(text=text):
                self.assertEqual([g.id for g in strength.violations(text, p)], [kind])

    def test_the_same_claim_is_allowed_once_a_verified_fact_establishes_it(self):
        licensed = {
            "driver-refusal": ("driver_refused_to_identify", True),
            "payment-completed": ("payment_made", True),
            "authorisation-established": ("authorisation", "PERMIT"),
            "valid-permit": ("permit_held", True),
            "customer-only-site": ("customer_only_site", True),
        }
        for text, kind in self.STRONGER:
            if kind not in licensed:
                continue
            with self.subTest(kind=kind):
                name, value = licensed[kind]
                self.assertEqual(strength.violations(text, pack({name: value})), [])

    def test_approved_wording_licenses_the_conclusion_it_actually_states(self):
        """The KB is the legal authority: a conclusion it states may be paraphrased,
        one it does not state may not be added."""
        says = pack(wording="Where this is shown the charge is unenforceable against the keeper.")
        silent = pack(wording="Keeper liability has not been established.")
        text = "The charge is unenforceable."
        self.assertEqual(strength.violations(text, says), [])
        self.assertEqual([g.id for g in strength.violations(text, silent)], ["conclusion-widened"])

    def test_a_denial_or_a_put_to_proof_is_not_an_assertion(self):
        p = pack()
        for text in (
            "The receipt does not establish that the vehicle was authorised to park.",
            "There is no evidence that payment was made by the keeper's account alone.",
            "The operator has not shown that the overstay was proven.",
            "The driver will not be identified, and no inference may be drawn from that.",
            "An attempt was made to pay, but the payment could not be completed.",
            "The keeper's account is that a permit was displayed.",
            "The operator has not established that the notice was lawful.",
        ):
            with self.subTest(text=text):
                self.assertEqual(strength.violations(text, p), [])

    def test_it_is_a_registry_so_a_future_ground_adds_a_claim_kind_without_new_logic(self):
        import re
        guard = strength.Guard(
            "signage-seen", re.compile(r"\bthe\s+driver\s+saw\s+the\s+signs?\b", re.I),
            "States the driver saw the signage", facts=("signage_seen",))
        strength.register(guard)
        try:
            self.assertEqual([g.id for g in strength.violations("The driver saw the sign.", pack())],
                             ["signage-seen"])
            self.assertEqual(strength.violations("The driver saw the sign.",
                                                 pack({"signage_seen": True})), [])
        finally:
            strength.GUARDS[:] = [g for g in strength.GUARDS if g.id != "signage-seen"]

    def test_the_validation_engine_refuses_it_as_VAL_STRENGTH(self):
        p = pack({"pcn_number": "X1"})
        sentence = DraftSentence("The keeper refuses to identify the driver.", [], ["KB-X-01"], [])
        issues = ValidationEngine(None).validate(Draft("C", [[sentence]]), p).issues
        self.assertIn("VAL-STRENGTH", [i.rule for i in issues])


# --------------------------------------------------------------------------- 2
class _Judge:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def complete_json(self, *, task, system, user, images=None):
        self.calls += 1
        out = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


def reading(**over):
    base = {"scores": GOOD, "explains_why_cancelled": True, "sendable_for_any_pcn": False,
            **{k: [] for k in HARD_FLAGS}, "omitted_grounds": [], "summary": ""}
    base.update(over)
    return base


def sentences(text="The vehicle left after three minutes."):
    return Draft("C-Q", [[DraftSentence(text, [], ["KB-X-01"])]])


class TheJudgeEnforcesTheBriefWithoutBecomingAnAuthor(unittest.TestCase):

    BRIEF_FAILURES = {
        "unsupported_statements": "The signage was inadequate.",
        "ungrounded_arguments": "Landowner authority is not shown.",
        "changed_conclusions": "Says the charge is unlawful; the approved point is 'not established'.",
        "certainty_inflation": "Writes 'payment was made' for an attempted payment.",
        "strengthened_facts": "Writes 'refuses to identify' for an unidentified driver.",
        "misattributed_statements": "Presents the keeper's account as the notice's finding.",
        "removed_supported_facts": "Leaves out the date the notice was posted.",
        "weakened_customer_facts": "Turns a specific errand into 'may have left'.",
    }

    def test_every_failure_the_brief_lists_is_a_hard_finding_that_blocks(self):
        self.assertEqual(set(self.BRIEF_FAILURES), set(HARD_FLAGS))
        for flag, text in self.BRIEF_FAILURES.items():
            with self.subTest(flag=flag):
                review = QualityJudge(_Judge(reading(**{flag: [text]}))).review(sentences(), pack())
                self.assertEqual(review["status"], REWRITE)
                self.assertTrue(review["blocking"])
                self.assertTrue(any(text in f for f in review["feedback"]), review["feedback"])

    def test_a_generic_letter_is_a_hard_failure_however_well_it_scores(self):
        review = QualityJudge(_Judge(reading(sendable_for_any_pcn=True))).review(sentences(), pack())
        self.assertTrue(review["blocking"])

    def test_the_same_meaning_gets_the_same_verdict_whatever_the_words(self):
        """The judge's verdict is a function of its findings, not of the prose: two
        letters it reads as equivalent can not land on opposite sides of the gate."""
        for wording in ("The vehicle left after three minutes.",
                        "Within three minutes the car had gone from the site.",
                        "Barely three minutes passed before the vehicle departed."):
            with self.subTest(wording=wording):
                review = QualityJudge(_Judge(reading())).review(sentences(wording), pack())
                self.assertEqual(review["status"], PASS)
                self.assertFalse(review["blocking"])

    def test_it_cannot_add_a_ground_to_the_case(self):
        review = QualityJudge(_Judge(reading(omitted_grounds=["KB-EQ-02", "KB-X-01"]))).review(
            sentences(), pack())
        self.assertEqual(review["omitted_grounds"], ["KB-X-01"])

    def test_rewrite_feedback_asks_for_the_same_case_written_better_and_names_no_module(self):
        review = QualityJudge(_Judge(reading(
            scores={**GOOD, "case_specificity": 4},
            certainty_inflation=["Writes 'payment was made' for an attempted payment."],
            summary="Generic in the second paragraph."))).review(sentences(), pack())
        joined = " ".join(review["feedback"])
        self.assertIn("certainty inflation", joined)
        self.assertIn("case specificity scored 4/10", joined)
        self.assertNotRegex(joined, r"KB-[A-Z]+-\d+")
        self.assertEqual(rewrite_feedback({"scores": GOOD}), [])

    def test_an_unreachable_judge_neither_blocks_nor_passes_silently(self):
        review = QualityJudge(_Judge(RuntimeError("provider down"))).review(sentences(), pack())
        self.assertEqual(review["status"], "ERROR")
        self.assertFalse(review["blocking"])

    def test_hard_findings_is_the_only_thing_that_blocks(self):
        self.assertEqual(hard_findings(reading()), [])
        self.assertEqual(verdict({**GOOD, "factual_fidelity": 5}, reading()), REWRITE)
        self.assertEqual(hard_findings({**reading(), "factual_fidelity": 5}), [])


# --------------------------------------------------------------------------- 3
class _Writer:
    """Records exactly what the drafter model is sent."""

    def __init__(self):
        self.sent = []
        self.models = {"drafting": "recorder"}

    def complete_json(self, *, task, system, user, images=None):
        self.sent.append(json.loads(user))
        return {"opening": "I am appealing this Parking Charge Notice.", "sections": [],
                "closing": "I invite the operator to cancel it."}


class ARetryRewritesTheSameCase(unittest.TestCase):

    def _payload(self, feedback):
        llm = _Writer()
        p = pack({"pcn_number": "X1"}, wording="Keeper liability has not been established.")
        LLMDrafter(llm).draft("C-R", p, feedback, attempt=2 if feedback else 1)
        return llm.sent[0]

    def test_a_retry_carries_the_contract_and_the_feedback(self):
        sent = self._payload(["VAL-STRENGTH: States that payment was completed."])
        contract = sent["rewrite_contract"]
        self.assertTrue(contract["same_case"])
        for fixed in ("which grounds are argued", "which facts are stated",
                      "what any fact or approved conclusion means"):
            self.assertIn(fixed, contract["may_not_change"])
        self.assertEqual(sent["validator_feedback"], ["VAL-STRENGTH: States that payment was completed."])

    def test_a_first_attempt_has_no_contract_and_a_retry_changes_nothing_else(self):
        first, retry = self._payload(None), self._payload(["fix it"])
        self.assertNotIn("rewrite_contract", first)
        for key in first:
            self.assertEqual(first[key], retry[key], key)      # same facts, same plan, same wording

    def test_the_locked_sections_come_from_the_plan_not_from_the_feedback(self):
        contract = rewrite_contract({"draft_plan": {"sections": [
            {"section_id": "S01", "ground_ids": ["KB-A-01"]},
            {"section_id": "S02", "ground_ids": ["KB-B-02", "KB-A-01"]}]}})
        self.assertEqual(contract["locked_section_ids"], ["S01", "S02"])
        self.assertEqual(contract["locked_ground_ids"], ["KB-A-01", "KB-B-02"])


# --------------------------------------------------------------------------- 4
class _ReleaseIdentity(unittest.TestCase):
    """Release identity (provider and model versions) comes from the process-wide
    client probe, which a test run without a provider key cannot answer. These
    tests are about what is drafted and judged, not about that stamp, so it is
    given a fixed answer rather than left to the machine the suite runs on."""

    def setUp(self):
        patcher = mock.patch("pcn_appeal.llm.probe", return_value={
            "provider": "test", "models": {"drafting": "reference", "appeal_quality": "reference"}})
        patcher.start()
        self.addCleanup(patcher.stop)


def _scenario(extra, evidence, doc_types, narrative, answers):
    """A case through the real pipeline (real KB, validators and Claim Plan)."""
    import test_scenarios as ts
    case, pipe = ts.make_case(extra, evidence, doc_types)
    return case, pipe, lambda: ts.run(case, pipe, narrative, answers)


class _Recorder:
    """Wraps the pipeline's drafter to record what each attempt was told."""

    def __init__(self, drafter):
        self.inner, self.feedback = drafter, []

    def draft(self, case_id, pack, feedback=None, attempt=1):
        self.feedback.append(list(feedback or []))
        return self.inner.draft(case_id, pack, feedback, attempt)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class TheOrchestratorHonoursTheJudge(_ReleaseIdentity):
    CASE = dict(
        extra={"operator_name": "Westbourne Estates Ltd", "pcn_number": "WE88120045",
               "parking_location": "Kingsmead Shopping Centre", "alleged_breach": "Overstayed the free period"},
        evidence={"E2": EvidenceItem("E2", "RECOVERY_REPORT", "recovery.pdf", text="Recovery job 4471")},
        doc_types={"E2": "RECOVERY_REPORT"},
        narrative="The engine would not turn over, so I called the breakdown service and waited.",
        answers={"vehicle_immobilised": "yes", "immobilisation_prevented_departure": "yes",
                 "recovery_attended": "yes", "permitted_period_ended": "yes", "exit_delay_min": 52})

    def run_with(self, *payloads):
        case, pipe, go = _scenario(**self.CASE)
        judge = _Judge(*payloads)
        pipe.quality = QualityJudge(judge)
        pipe.quality_enforcing = True
        pipe.drafter = _Recorder(pipe.drafter)
        return case, pipe, judge, go()

    def events(self, case, name):
        return [a for a in case.audit if a.get("event") == name]

    def test_a_clean_read_releases_without_a_rewrite(self):
        case, pipe, judge, out = self.run_with(reading())
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertEqual(judge.calls, 1)
        self.assertEqual(self.events(case, "quality_rewrite"), [])

    def test_a_hard_finding_sends_the_same_case_back_and_a_clean_second_read_releases(self):
        finding = "Writes 'payment was made' for an attempted payment."
        case, pipe, judge, out = self.run_with(reading(certainty_inflation=[finding]), reading())
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertEqual(judge.calls, 2)
        self.assertEqual(len(self.events(case, "quality_rewrite")), 1)
        told = pipe.drafter.feedback
        self.assertEqual(told[0], [])
        self.assertTrue(any(finding in f for f in told[1]), told)

    def test_a_hard_finding_that_survives_every_rewrite_holds_the_letter(self):
        finding = "Presents the keeper's account as the notice's finding."
        case, pipe, judge, out = self.run_with(reading(misattributed_statements=[finding]))
        self.assertEqual(out.state, CaseState.VALIDATION_FAILED)
        self.assertIsNone(out.letter)
        self.assertIn("VAL-QUALITY", [i.rule for i in out.validation.issues])
        self.assertEqual(judge.calls, quality_judge.MAX_REWRITES + 1)
        held = self.events(case, "appeal_quality")[-1]
        self.assertTrue(held["blocking"])

    def test_a_score_short_of_its_floor_is_rewritten_but_never_withholds_a_letter(self):
        case, pipe, judge, out = self.run_with(reading(scores={**GOOD, "persuasiveness": 9,
                                                              "case_specificity": 7}))
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertEqual(judge.calls, quality_judge.MAX_REWRITES + 1)

    def test_a_judge_that_cannot_be_reached_does_not_hold_a_sound_letter(self):
        case, pipe, judge, out = self.run_with(RuntimeError("provider down"))
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertEqual(self.events(case, "appeal_quality")[-1]["status"], "ERROR")

    def test_enforcement_can_be_turned_back_to_recording_only(self):
        case, pipe, go = _scenario(**self.CASE)
        pipe.quality = QualityJudge(_Judge(reading(strengthened_facts=["x"])))
        pipe.quality_enforcing = False
        out = go()
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)


# --------------------------------------------------------------------------- 5
class UnseenCasesGetTheSameSubstantiveTreatment(_ReleaseIdentity):
    """New operators, places, allegations and stories through the real pipeline: the
    same underlying meaning must reach the same grounds, and nothing stronger than
    the case state may appear, however the case is worded."""

    BREAKDOWNS = [
        ({"operator_name": "Marlow Gate Parking", "pcn_number": "MG5510293", "vrm": "KP71 XJD",
          "parking_location": "Tannery Wharf Car Park", "alleged_breach": "Stayed beyond the permitted period"},
         "My clutch failed on the ramp and a recovery truck took the car away."),
        ({"operator_name": "Oakfield Site Management", "pcn_number": "OSM30011872", "vrm": "LD19 RTB",
          "parking_location": "Oakfield Leisure Complex", "alleged_breach": "Exceeded the maximum stay"},
         "Dead battery. Roadside assistance came out but it took a good while."),
        ({"operator_name": "Brightwater Parking Services", "pcn_number": "BPS90042113", "vrm": "YN22 HGF",
          "parking_location": "Station Road West", "alleged_breach": "Remained beyond the time allowed"},
         "The car would not start when I returned; I had to wait for the garage."),
    ]
    ANSWERS = {"vehicle_immobilised": "yes", "immobilisation_prevented_departure": "yes",
               "recovery_attended": "yes", "permitted_period_ended": "yes", "exit_delay_min": 35}

    def test_the_same_meaning_reaches_the_same_ground_in_different_words(self):
        """Same factual meaning, different words: the same substantive Claim Plan.
        A support or evidence-request ground may differ only if a source fact or
        evidence differs - never because one synonym happened to trigger retrieval."""
        grounds = set()
        for extra, story in self.BREAKDOWNS:
            with self.subTest(operator=extra["operator_name"]):
                case, pipe, go = _scenario(
                    extra, {"E2": EvidenceItem("E2", "RECOVERY_REPORT", "r.pdf", text="Recovery job")},
                    {"E2": "RECOVERY_REPORT"}, story, self.ANSWERS)
                out = go()
                self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
                grounds.add(tuple(sorted(out.pack.module_ids)))
                self.assertNotIn("VAL-STRENGTH", [i.rule for i in out.validation.issues])
                # specific to THIS charge
                self.assertIn(extra["pcn_number"], out.letter)
                # nothing stronger than the case state
                for text in (s.text for s in out.draft.sentences()):
                    self.assertEqual(strength.violations(text, out.pack), [], text)
        self.assertEqual(len(grounds), 1, grounds)

    def test_a_stronger_sentence_on_a_real_case_is_refused_by_the_real_validator(self):
        extra, story = self.BREAKDOWNS[0]
        case, pipe, go = _scenario(
            extra, {"E2": EvidenceItem("E2", "RECOVERY_REPORT", "r.pdf", text="Recovery job")},
            {"E2": "RECOVERY_REPORT"}, story, self.ANSWERS)
        out = go()
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        tampered = Draft(out.draft.case_id, [list(p) for p in out.draft.paragraphs]
                         + [[DraftSentence("The keeper refuses to identify the driver.", [],
                                           ["STRUCTURAL"], [])]])
        issues = pipe.validation.validate(tampered, out.pack).issues
        self.assertIn("VAL-STRENGTH", [i.rule for i in issues])


# --------------------------------------------------------------------------- 6
class EquivalentAllegationsAreTheSameEverywhere(_ReleaseIdentity):
    """The upstream consistency defect: "stayed beyond the permitted period" was classed
    as a PERMIT allegation (the substring "permit" inside "permitted") while "exceeded
    the maximum stay" was an overstay, so the first pulled in a records request about
    permits and the second did not. Same meaning must mean: same class, same canonical
    facts, same candidates, same grounds. Only the allegation's wording changes here."""

    SAME_MEANING = {
        "OVERSTAY": [
            "Exceeded the maximum stay", "Stayed beyond the permitted period",
            "Remained beyond the time allowed", "Parked longer than the time paid for",
            "Overstayed the free period", "Vehicle remained past the allowed duration",
            "Left after the end of the paid session"],
        "PERMIT": [
            "No valid permit displayed", "Parked without a permit",
            "Vehicle not authorised to park in this area", "Residents only bay used without a permit"],
        "PAYMENT": [
            "Failed to pay for parking", "No payment made for this stay",
            "Parked without a valid ticket", "Tariff not paid"],
    }

    def test_one_classifier_gives_each_meaning_one_class(self):
        from pcn_appeal import allegation
        for cls, wordings in self.SAME_MEANING.items():
            for text in wordings:
                with self.subTest(text=text):
                    self.assertEqual(allegation.classify(text), cls)

    def test_a_participle_is_not_a_noun_and_a_place_is_not_a_time(self):
        """The defect itself, and its mirror: "permitted" is not "permit", and
        "beyond the permitted area" is not excess against a time allowance."""
        from pcn_appeal import allegation
        self.assertFalse(allegation.has("Stayed beyond the permitted period", "PERMIT"))
        self.assertFalse(allegation.has("Parked beyond the permitted area", "OVERSTAY"))
        self.assertIsNone(allegation.classify(""))
        self.assertEqual(allegation.classify("Parked beyond the permitted area"), "UNCLASSIFIED")

    def test_the_derived_breach_type_agrees_with_the_class(self):
        from pcn_appeal.engines.derivation import classify_breach
        for text in self.SAME_MEANING["OVERSTAY"]:
            with self.subTest(text=text):
                self.assertEqual(classify_breach(text), "OVERSTAY")
        self.assertEqual(classify_breach("Parked without a permit"), "PERMIT")
        self.assertEqual(classify_breach("Stayed beyond the permitted period"), "OVERSTAY")

    def test_every_wording_of_an_overstay_reaches_the_same_candidates_and_grounds(self):
        base = {"operator_name": "Marlow Gate Parking", "pcn_number": "MG5510293",
                "vrm": "KP71 XJD", "parking_location": "Tannery Wharf Car Park"}
        story = "The engine would not turn over and I waited for the recovery truck."
        answers = UnseenCasesGetTheSameSubstantiveTreatment.ANSWERS
        outcomes = {}
        for text in self.SAME_MEANING["OVERSTAY"]:
            case, pipe, go = _scenario(
                dict(base, alleged_breach=text),
                {"E2": EvidenceItem("E2", "RECOVERY_REPORT", "r.pdf", text="Recovery job")},
                {"E2": "RECOVERY_REPORT"}, story, answers)
            out = go()
            view = case.fact_view()
            retrieved = [a for a in case.audit if a.get("event") == "knowledge_retrieval"][-1]
            outcomes[text] = (view.get("allegation_class"), view.get("alleged_breach_type"),
                              view.get("permitted_period_ended"),
                              tuple(sorted(retrieved["candidates"])),
                              tuple(sorted(out.pack.module_ids)))
        self.assertEqual(len(set(outcomes.values())), 1,
                         "; ".join(f"{k}: {v}" for k, v in outcomes.items()))

    def test_a_records_request_needs_a_records_reason_not_a_lucky_substring(self):
        """KB-REC-01 is for allegations that turn on a validation / permit step. An
        overstay worded with "permitted" has no such step."""
        for text in ("Stayed beyond the permitted period", "Exceeded the maximum stay"):
            with self.subTest(text=text):
                case, pipe, go = _scenario(
                    {"alleged_breach": text},
                    {"E2": EvidenceItem("E2", "RECOVERY_REPORT", "r.pdf", text="Recovery job")},
                    {"E2": "RECOVERY_REPORT"}, "The engine failed.", UnseenCasesGetTheSameSubstantiveTreatment.ANSWERS)
                self.assertNotIn("KB-REC-01", go().pack.module_ids)


if __name__ == "__main__":
    unittest.main()
