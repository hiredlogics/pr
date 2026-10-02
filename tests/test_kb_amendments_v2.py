"""Client-directed KB amendments (2026-10-02 review).

Three decisions, each a rule about WHY a module activates:

  KB-BAY-01  the put-to-proof principle is approved, but a five-minute
             observation window must not be treated as though the figure had
             independent legal significance. Relevance is assessed against what
             the alleged contravention requires proving.
  KB-BAY-02  the professional-restatement mechanism is approved, tightened so a
             material fact asserted in the customer's name must have been
             expressly stated or confirmed by them, never inferred from prose.
  KB-REC-01  keyword-only activation is not approved. The allegation may flag
             the module as potentially relevant; a case fact must establish that
             the validation/permit mechanism is genuinely material.

The common thread, and the reason these are one test module: a module activates
because the case facts make it relevant, not because a word appeared.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines import account
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.extraction import _validation_mechanism_material
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (CaseFile, EvidenceItem, Fact, FactSource, FactStatus,
                               SourceKind)
from pcn_appeal.rules.dsl import evaluate

KG = KnowledgeGraph()


def _case(cid: str = "C-AMEND", **facts) -> CaseFile:
    case = CaseFile(cid)
    for name, value in facts.items():
        case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
    return case


def _gate(module_id: str, facts: dict) -> bool:
    """Does this module's own gate hold on these facts?"""
    m = KG.modules[module_id]
    return bool(evaluate(m.use_when, facts)
                and not evaluate(m.do_not_use_when, facts))


# ---------------------------------------------------------------- KB-BAY-01
class NoObservationDurationThreshold(unittest.TestCase):
    """The five-minute cut-off is gone, and nothing may reintroduce one."""

    def test_the_gate_does_not_mention_an_observation_duration(self):
        m = KG.modules["KB-BAY-01"]
        gate = str(m.use_when) + str(m.do_not_use_when)
        self.assertNotIn("observation_window_min", gate)
        for n in ("lte", "lt", "gt", "gte"):
            self.assertNotIn(f"'{n}'", gate,
                             f"KB-BAY-01 compares a quantity ({n}) in its gate again")

    def test_no_module_anywhere_thresholds_the_observation_window(self):
        # The figure was arbitrary wherever it appeared, not only here.
        for mid, m in KG.modules.items():
            gate = str(m.use_when) + str(m.do_not_use_when)
            if "observation_window_min" in gate:
                self.assertNotRegex(
                    gate, r"'(lte|lt|gt|gte)'",
                    f"{mid} applies a duration threshold to the observation window")

    def test_a_wide_observation_window_still_warrants_the_request(self):
        # The old gate refused this (window 45 > 5). The element the operator
        # must prove is who was using the bay, and a longer record does not
        # establish it either - so the request stands.
        facts = {"restricted_bay_alleged": True, "bay_eligibility_unevidenced": True,
                 "observation_window_min": 45}
        self.assertTrue(_gate("KB-BAY-01", facts))

    def test_a_narrow_window_is_not_what_creates_the_ground(self):
        # A two-minute window with the eligibility element already evidenced:
        # under the old rule this activated, and now it correctly does not.
        facts = {"restricted_bay_alleged": True, "observation_window_min": 2,
                 "bay_eligibility_evidenced": True}
        self.assertFalse(_gate("KB-BAY-01", facts))

    def test_a_non_eligibility_allegation_is_untouched(self):
        self.assertFalse(_gate("KB-BAY-01", {"bay_eligibility_unevidenced": True}))

    def test_the_wording_never_calls_the_observation_brief(self):
        # The request is about what the record can establish, not its length.
        text = KG.blocks["PP-BAY-001"].text.lower()
        for phrase in ("brief", "too short", "only a few", "instant", "moment",
                       "five minute", "5 minute", "narrow"):
            self.assertNotIn(phrase, text)

    def test_the_wording_still_asks_for_the_evidence(self):
        text = KG.blocks["PP-BAY-001"].text.lower()
        self.assertIn("requested to produce", text)
        self.assertIn("photograph", text)
        self.assertIn("timestamp", text)


class EligibilityAssessmentIsAboutTheAllegation(unittest.TestCase):
    """EX-19: the derived fact encodes why the observation cannot settle it."""

    def _derive(self, **kw) -> CaseFile:
        from pcn_appeal.engines.extraction import RESTRICTED_BAY  # noqa: F401
        case = _case(**kw)
        # EX-19 runs inside the extraction pass; exercise it through the same
        # conditions rather than reimplementing the rule here.
        return case

    def test_an_unevidenced_eligibility_element_with_a_record_is_put_to_proof(self):
        facts = {"restricted_bay_alleged": True, "bay_eligibility_unevidenced": True}
        self.assertTrue(_gate("KB-BAY-01", facts))

    def test_an_accounted_for_bay_condition_withholds_the_request(self):
        # Nothing left to put to proof.
        facts = {"restricted_bay_alleged": True, "bay_eligibility_unevidenced": True,
                 "bay_conditions_met_accounted": True}
        self.assertFalse(_gate("KB-BAY-01", facts))


# ---------------------------------------------------------------- KB-BAY-02
def _bay_case(narrative: str) -> CaseFile:
    case = CaseFile("C-BAY2")
    case.put(Fact("F-br", "alleged_breach", "Parked in a parent and child bay "
                  "without meeting the conditions of use", FactStatus.CONFIRMED,
                  FactSource(SourceKind.DOCUMENT, "E1")))
    case.put(Fact("F-rb", "restricted_bay_alleged", True, FactStatus.DERIVED,
                  FactSource(SourceKind.CALCULATION, "breach_classify")))
    case.raw_answers["narrative"] = narrative
    return case


class AMaterialFactMustComeFromTheCustomer(unittest.TestCase):
    """KB-BAY-02 restates the proposition TO THE OPERATOR in the keeper's name,
    so an inferred read of prose must not supply it."""

    def test_an_inferred_read_does_not_establish_the_contradiction(self):
        # The system's reading of prose, with no question answered. The fact
        # may still be extracted; what it may not do is become the assertion.
        case = _bay_case("I had my children in the car with me the whole time.")
        assess_material_account(case)
        self.assertIsNot(case.get("account_contradicts_allegation"), True)
        self.assertFalse(_gate("KB-BAY-02", case.fact_view()))

    def test_an_explicit_answer_does_establish_it(self):
        case = _bay_case("I was in the bay.")
        case.put(Fact("F-child", "child_occupant_present", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "q:child_occupant_present")))
        assess_material_account(case)
        self.assertTrue(case.get("account_contradicts_allegation"))
        self.assertTrue(case.get("material_account_proposition"))
        self.assertTrue(_gate("KB-BAY-02", case.fact_view()))

    def test_the_headline_proposition_is_never_an_inferred_one(self):
        # Both present: an inferred child read and an answered badge. The
        # restated assertion must be the one the customer actually gave.
        case = _bay_case("The kids were in the car.")
        case.put(Fact("F-bb", "blue_badge_displayed", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "q:blue_badge_displayed")))
        assess_material_account(case)
        headline = case.get("material_account_proposition")
        self.assertIsNotNone(headline)
        self.assertIn("badge", headline.lower())

    def test_the_module_needs_a_proposition_to_restate(self):
        facts = {"restricted_bay_alleged": True, "account_contradicts_allegation": True}
        self.assertFalse(_gate("KB-BAY-02", facts),
                         "KB-BAY-02 must not activate with nothing to restate")

    def test_provenance_is_recorded_on_every_extraction(self):
        case = _bay_case("My children were in the car.")
        assess_material_account(case)
        for row in case.free_text_provenance:
            self.assertIn("fact_name", row)
        # and the dataclass itself distinguishes the three kinds
        self.assertFalse(account.FreeTextExtraction(provenance="INFERRED").customer_asserted)
        self.assertTrue(account.FreeTextExtraction(provenance="STATED").customer_asserted)
        self.assertTrue(account.FreeTextExtraction(provenance="CONFIRMED").customer_asserted)

    def test_the_child_entitlement_guardrail_is_unchanged(self):
        m = KG.modules["KB-BAY-02"]
        joined = " ".join(m.prohibited_claims).lower()
        self.assertIn("entitlement", joined)
        self.assertIn("free text", joined)


# ---------------------------------------------------------------- KB-REC-01
class ARecordsRequestNeedsMoreThanAKeyword(unittest.TestCase):
    """A permit-shaped allegation is a candidate signal, not an activation."""

    def test_the_gate_no_longer_reads_the_allegation_text(self):
        m = KG.modules["KB-REC-01"]
        gate = str(m.use_when) + str(m.do_not_use_when)
        self.assertNotIn("alleged_breach", gate)
        self.assertNotIn("contains", gate)

    def test_the_gate_requires_established_materiality(self):
        m = KG.modules["KB-REC-01"]
        self.assertIn("validation_mechanism_material", str(m.use_when))

    def test_a_bare_permit_allegation_does_not_request_records(self):
        facts = {"alleged_breach": "Parked without a valid permit",
                 "validation_mechanism_alleged": True}
        self.assertFalse(_gate("KB-REC-01", facts))

    def test_a_material_mechanism_does_request_records(self):
        facts = {"validation_mechanism_alleged": True,
                 "validation_mechanism_material": True}
        self.assertTrue(_gate("KB-REC-01", facts))

    def test_materiality_is_not_established_by_the_allegation_alone(self):
        case = _case(alleged_breach="Parked without a valid permit")
        material, why = _validation_mechanism_material(case)
        self.assertFalse(material)
        self.assertEqual(why, "")

    def test_an_unresolved_validation_status_is_material(self):
        case = _case(parking_validation_status="UNKNOWN")
        material, why = _validation_mechanism_material(case)
        self.assertTrue(material)
        self.assertEqual(why, "parking_validation_status_unknown")

    def test_a_payment_asserted_but_not_on_the_notice_is_material(self):
        case = _case(payment_made=True)
        material, why = _validation_mechanism_material(case)
        self.assertTrue(material)
        self.assertEqual(why, "payment_asserted_not_recorded")

    def test_a_payment_the_notice_already_records_is_not_this_ground(self):
        case = _case(payment_made=True, payment_recorded_in_document=True)
        material, _ = _validation_mechanism_material(case)
        self.assertFalse(material)

    def test_an_asserted_permit_is_material(self):
        case = _case(permit_held=True)
        material, why = _validation_mechanism_material(case)
        self.assertTrue(material)
        self.assertEqual(why, "permit_asserted")

    def test_customer_supplied_transaction_evidence_is_material(self):
        case = CaseFile("C-REC", evidence={
            "E2": EvidenceItem("E2", "RECEIPT", "r.pdf", text="store receipt")})
        case.evidence["E2"].uploaded = True
        material, why = _validation_mechanism_material(case)
        self.assertTrue(material)
        self.assertEqual(why, "customer_supplied_transaction_evidence")

    def test_the_basis_is_always_named(self):
        # Auditability: every activation can say which fact let it in.
        for kw in ({"parking_validation_status": "UNKNOWN"}, {"permit_held": True},
                   {"payment_attempt_failed": True}, {"payment_made": True}):
            material, why = _validation_mechanism_material(_case(**kw))
            self.assertTrue(material)
            self.assertTrue(why, f"no basis recorded for {kw}")

    def test_the_request_wording_still_asserts_nothing(self):
        text = KG.blocks["PP-REC-001"].text.lower()
        self.assertNotIn("validation occurred", text)
        self.assertNotIn("invalid", text)
        self.assertIn("requested to review", text)


if __name__ == "__main__":
    unittest.main()
