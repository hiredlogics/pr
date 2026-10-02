"""The fact -> ground propagation contract.

A confirmed or explicitly asserted customer fact that contradicts what the
notice alleges must become a ground in its own right, reach the locked claim
plan carrying its fact ids, reach the drafter as REQUIRED content, and appear
in the letter as the PRIMARY proposition. An evidential put-to-proof argument
may support it; it may never replace it.

These tests are deliberately written against the generic mechanism rather than
the bay code path: a permit, payment or continuity allegation must travel the
same route as a parent-and-child one. Nothing here may be satisfied by a
bay-specific drafting rule.
"""
import unittest

from pcn_appeal.allegation import (
    AllegationProposition, CONTRADICTS, PARTIALLY_ADDRESSES, SUPPORTS,
    UNRELATED, derive_allegation_propositions, relate,
)
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.models import (
    CaseFile, CaseState, EvidenceItem, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline

from support import ReferenceAnalysisLLM

BASE = dict(operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12 CDE",
            parking_location="Retail Park", site_postcode="M1 1AA",
            parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
            charge_amount="£100", operator_ata="BPA")

BAY_BREACH = ("Your vehicle was parked in a Parent and Child bay without being "
              "accompanied by a child")
PERMIT_BREACH = "Parked without a valid permit displayed"
PAYMENT_BREACH = "Parked without making the required payment"
CONTINUITY_BREACH = "Vehicle remained parked continuously for 4 hours 12 minutes"


def make_case(extra=None, analysis=None, notice_text="Parking Charge Notice ..."):
    f = dict(BASE, **(extra or {}))
    fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
              for k, v in f.items()}
    responses = {"extraction": [{"fields": fields, "doc_types": {"E1": "PCN"}}]}
    if analysis is not None:
        responses["case_analysis"] = list(analysis)
    llm = ReferenceAnalysisLLM(responses)
    case = CaseFile("C-1", evidence={
        "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text=notice_text)})
    return case, AppealPipeline(llm)


def run(case, pipe, narrative, answers=None):
    pipe.ingest(case)
    pipe.confirm(case, {}, list(case.facts), narrative)
    if answers:
        pipe.answer(case, answers)
    return pipe.generate(case)


def plan_of(out):
    cp = getattr(out.pack, "claim_plan", None) or {}
    return cp if isinstance(cp, dict) else {}


def grounds_of(out):
    return plan_of(out).get("grounds") or []


def rebuttals_of(out):
    return [g for g in grounds_of(out) if g.get("ground_type") == "FACTUAL_REBUTTAL"]


# --------------------------------------------------------------- provenance
class ExplicitAssertionIsNotAnInference(unittest.TestCase):
    """Client correction: an unambiguous statement in the narrative IS the
    customer providing the fact. It must not be demoted to the system's own
    reading merely because it arrived as free text, and it must not trigger a
    question asking the customer to repeat themselves."""

    def _assert(self, narrative, breach=BAY_BREACH):
        case, _ = make_case({"alleged_breach": breach})
        case.put(Fact("F-alleged_breach", "alleged_breach", breach,
                      FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case.put(Fact("F-restricted_bay_alleged", "restricted_bay_alleged", True,
                      FactStatus.DERIVED, FactSource(SourceKind.CALCULATION, "t")))
        case.raw_answers["narrative"] = narrative
        assess_material_account(case)
        return {e["fact_name"]: e for e in (case.free_text_provenance or [])}

    def test_children_in_the_car_is_an_explicit_assertion(self):
        got = self._assert("My children were in the car with me the whole time.")
        e = got.get("child_occupant_present")
        self.assertIsNotNone(e, got)
        self.assertEqual(e["provenance"], "ASSERTED", e)
        self.assertTrue(e["customer_asserted"], e)

    def test_an_explicit_assertion_asks_no_confirming_question(self):
        case, pipe = make_case(
            {"alleged_breach": BAY_BREACH},
            analysis=[{"grounds": [], "questions": [], "not_supported": []}] * 6)
        pipe.ingest(case)
        r = pipe.confirm(case, {}, list(case.facts),
                         "My children were in the car with me the whole time.")
        asked = [q.get("fact") for q in ((getattr(r, "review", None) or {})
                                         .get("shown") or [])]
        self.assertNotIn("child_occupant_present", asked)
        self.assertNotIn("child_occupant_present", case.asked_questions)

    def test_travelling_with_family_is_ambiguous_and_not_promoted(self):
        """"I was with my family" does not say a CHILD was present."""
        got = self._assert("I was travelling with my family that afternoon.")
        e = got.get("child_occupant_present")
        if e is not None:
            self.assertNotEqual(e["provenance"], "ASSERTED", e)
            self.assertFalse(e["customer_asserted"], e)

    def test_an_ambiguous_narrative_does_not_assert_the_fact_as_true(self):
        case, pipe = make_case(
            {"alleged_breach": BAY_BREACH},
            analysis=[{"grounds": [], "questions": [], "not_supported": []}] * 6)
        out = run(case, pipe, "I was travelling with my family that afternoon.")
        self.assertFalse(rebuttals_of(out), plan_of(out))


# ------------------------------------------------------- allegation layer
class TheAllegationCarriesTypedPropositions(unittest.TestCase):
    """The notice's assertion must be a typed proposition, not only raw text
    plus a category boolean. Without it there is nothing for a confirmed fact
    to be matched against."""

    def test_a_bay_allegation_asserts_the_occupant_is_absent(self):
        props = derive_allegation_propositions(BAY_BREACH, {})
        by_fact = {p.subject: p for p in props}
        self.assertIn("child_occupant_present", by_fact)
        self.assertIs(by_fact["child_occupant_present"].asserted_value, False)

    def test_a_permit_allegation_asserts_no_valid_permit(self):
        props = derive_allegation_propositions(PERMIT_BREACH, {})
        by_fact = {p.subject: p for p in props}
        self.assertIn("permit_held", by_fact)
        self.assertIs(by_fact["permit_held"].asserted_value, False)

    def test_a_payment_allegation_asserts_no_payment(self):
        props = derive_allegation_propositions(PAYMENT_BREACH, {})
        by_fact = {p.subject: p for p in props}
        self.assertIn("payment_made", by_fact)
        self.assertIs(by_fact["payment_made"].asserted_value, False)

    def test_a_continuity_allegation_asserts_a_single_visit(self):
        props = derive_allegation_propositions(CONTINUITY_BREACH, {})
        by_fact = {p.subject: p for p in props}
        self.assertIn("multiple_visits", by_fact)
        self.assertIs(by_fact["multiple_visits"].asserted_value, False)

    def test_every_proposition_is_traceable(self):
        for p in derive_allegation_propositions(BAY_BREACH, {}):
            self.assertTrue(p.proposition_id)
            self.assertTrue(p.allegation_type)
            self.assertTrue(p.predicate)

    def test_an_unrecognised_allegation_yields_no_invented_proposition(self):
        self.assertEqual(derive_allegation_propositions("Something else entirely", {}), [])


class TheRelationshipModelIsGeneric(unittest.TestCase):
    """One reusable fact-vs-proposition relationship, not four branches."""

    def _prop(self, subject, value=False):
        return AllegationProposition(
            proposition_id="A-1", allegation_type="TEST", subject=subject,
            predicate="is", asserted_value=value, source_document_ref="E1")

    def _fact(self, name, value, *, kind=SourceKind.ANSWER,
              status=FactStatus.CONFIRMED):
        return Fact(f"F-{name}", name, value, status, FactSource(kind, "t"))

    def test_an_opposite_confirmed_value_contradicts(self):
        self.assertEqual(
            relate(self._prop("permit_held", False),
                   self._fact("permit_held", True), customer_asserted=True),
            CONTRADICTS)

    def test_the_same_value_supports_the_allegation(self):
        self.assertEqual(
            relate(self._prop("permit_held", False),
                   self._fact("permit_held", False), customer_asserted=True),
            SUPPORTS)

    def test_a_different_fact_is_unrelated(self):
        self.assertEqual(
            relate(self._prop("permit_held", False),
                   self._fact("payment_made", True), customer_asserted=True),
            UNRELATED)

    def test_a_hypothesis_cannot_contradict(self):
        """Condition D: only verified or explicitly asserted facts may create a
        direct rebuttal. An unconfirmed reading may not."""
        self.assertNotEqual(
            relate(self._prop("permit_held", False),
                   self._fact("permit_held", True,
                              kind=SourceKind.CUSTOMER_FREE_TEXT,
                              status=FactStatus.ANSWERED),
                   customer_asserted=False),
            CONTRADICTS)

    def test_an_uncertain_fact_cannot_contradict(self):
        """Low-confidence readings are never usable for a defect claim."""
        f = self._fact("permit_held", True, status=FactStatus.UNCERTAIN)
        self.assertNotEqual(
            relate(self._prop("permit_held", False), f, customer_asserted=True),
            CONTRADICTS)


# ------------------------------------------------------- the rebuttal ground
class AConfirmedFactBecomesAGround(unittest.TestCase):
    """The contract: the fact reaches the locked plan as its own ground,
    carrying the fact id and the allegation reference."""

    def _bay(self, narrative="My children were in the car with me the whole time.",
             grounds=("KB-BAY-01", "KB-BAY-02")):
        analysis = {"grounds": [{"module_id": m} for m in grounds],
                    "questions": [], "not_supported": []}
        case, pipe = make_case({"alleged_breach": BAY_BREACH,
                                "observation_time": "19/09/2026 12:23",
                                "event_time": "19/09/2026 12:23"},
                               analysis=[analysis] * 6)
        return case, pipe, run(case, pipe, narrative)

    def test_the_rebuttal_reaches_the_locked_claim_plan(self):
        case, _, out = self._bay()
        reb = rebuttals_of(out)
        self.assertTrue(reb, plan_of(out))

    def test_the_ground_carries_the_fact_id_and_allegation_ref(self):
        case, _, out = self._bay()
        g = rebuttals_of(out)[0]
        self.assertEqual(g.get("relationship"), CONTRADICTS, g)
        self.assertTrue(g.get("allegation_ref"), g)
        ids = g.get("supporting_fact_ids") or []
        self.assertTrue(ids, g)
        self.assertIn(case.facts.node_id("child_occupant_present"), ids)

    def test_the_ground_carries_required_particulars(self):
        _, _, out = self._bay()
        parts = rebuttals_of(out)[0].get("required_particulars") or []
        kinds = {p.get("type") for p in parts}
        self.assertIn("STATE_FACT", kinds)
        self.assertIn("CONNECT_FACT_TO_ALLEGATION", kinds)

    def test_the_material_fact_appears_in_the_letter(self):
        _, _, out = self._bay()
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertRegex(out.letter or "", r"(?i)child")

    def test_the_factual_rebuttal_leads_the_evidential_point(self):
        """Primary vs supporting. The evidence request may not come first."""
        _, _, out = self._bay()
        letter = out.letter or ""
        fact_at = letter.lower().find("child")
        eviden = [m for m in
                  (letter.lower().find("requested to produce"),
                   letter.lower().find("evidence relied upon"),
                   letter.lower().find("photograph")) if m >= 0]
        self.assertGreaterEqual(fact_at, 0, letter)
        self.assertTrue(eviden, letter)
        self.assertLess(fact_at, min(eviden), letter)

    def test_the_evidence_challenge_survives_as_support(self):
        """Test D: both must appear - the rebuttal does not swallow the
        evidential ground either."""
        _, _, out = self._bay()
        self.assertRegex(out.letter or "", r"(?i)child")
        self.assertRegex(out.letter or "",
                         r"(?i)(photograph|record|evidence relied upon)")

    def test_an_evidence_request_alone_is_not_enough(self):
        _, _, out = self._bay()
        letter = (out.letter or "").lower()
        self.assertNotEqual(
            ("child" not in letter) and ("photograph" in letter), True, letter)


class TheMechanismIsNotBaySpecific(unittest.TestCase):
    """Tests E, F, G. The same relationship mechanism must carry a permit,
    payment and continuity contradiction. If any of these needs its own code
    path, the fix was not generic."""

    def _case(self, breach, narrative, facts=None, grounds=()):
        analysis = {"grounds": [{"module_id": m} for m in grounds],
                    "questions": [], "not_supported": []}
        case, pipe = make_case({"alleged_breach": breach},
                               analysis=[analysis] * 6)
        pipe.ingest(case)
        for name, value in (facts or {}).items():
            case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                          FactSource(SourceKind.ANSWER, f"q:{name}")))
        pipe.confirm(case, {}, list(case.facts), narrative)
        return case, pipe.generate(case)

    def test_a_permit_contradiction_creates_a_rebuttal(self):
        case, out = self._case(PERMIT_BREACH, "I had a valid permit on display.",
                               {"permit_held": True})
        reb = rebuttals_of(out)
        self.assertTrue(reb, plan_of(out))
        self.assertIn(case.facts.node_id("permit_held"),
                      reb[0].get("supporting_fact_ids") or [])

    def test_a_payment_contradiction_creates_a_rebuttal(self):
        case, out = self._case(PAYMENT_BREACH, "I paid for my parking.",
                               {"payment_made": True})
        reb = rebuttals_of(out)
        self.assertTrue(reb, plan_of(out))
        self.assertIn(case.facts.node_id("payment_made"),
                      reb[0].get("supporting_fact_ids") or [])

    def test_a_continuity_contradiction_creates_a_rebuttal(self):
        case, out = self._case(CONTINUITY_BREACH,
                               "I left the car park and came back later.",
                               {"multiple_visits": True})
        reb = rebuttals_of(out)
        self.assertTrue(reb, plan_of(out))
        self.assertIn(case.facts.node_id("multiple_visits"),
                      reb[0].get("supporting_fact_ids") or [])


class RankingOrdersItDoesNotDelete(unittest.TestCase):
    """Test L. Two independently supported grounds both survive."""

    def test_a_factual_rebuttal_and_a_legal_defect_coexist(self):
        analysis = {"grounds": [{"module_id": "KB-BAY-01"},
                                {"module_id": "KB-BAY-02"}],
                    "questions": [], "not_supported": []}
        case, pipe = make_case({"alleged_breach": BAY_BREACH,
                                "observation_time": "19/09/2026 12:23",
                                "event_time": "19/09/2026 12:23"},
                               analysis=[analysis] * 6)
        out = run(case, pipe, "My children were in the car with me the whole time.")
        self.assertTrue(rebuttals_of(out), plan_of(out))
        self.assertTrue([g for g in grounds_of(out)
                         if g.get("ground_type") != "FACTUAL_REBUTTAL"],
                        plan_of(out))


if __name__ == "__main__":
    unittest.main()
