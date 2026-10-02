"""Keeper status is not a PoFA ground.

Being the registered keeper, and the driver being unidentified, are the
circumstances in which Schedule 4 MIGHT matter. They are not themselves a
defect, and they must not authorise a substantive Schedule 4 proposition in a
letter. A substantive paragraph needs a specific authorised PoFA issue:
a verified timing failure, a verified statutory-content defect, an approved
hire/hirer documentary failure, or another approved specific finding.

Neutral framing ("I am appealing as the registered keeper") stays allowed.
"""
import unittest

from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.models import (
    CaseFile, Draft, DraftSentence, EvidenceItem, RetrievalPack,
)
from pcn_appeal.orchestrator import AppealPipeline

from support import ReferenceAnalysisLLM

BASE = dict(operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12 CDE",
            parking_location="Retail Park", site_postcode="M1 1AA",
            parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
            charge_amount="£100", operator_ata="BPA",
            alleged_breach="Overstayed paid time", entry_time="10:00",
            exit_time="12:47")

# A Schedule 4 compliant postal notice: both para 9(2)(e) invitation limbs and
# the 9(2)(f) keeper warning, so no content defect exists to find.
COMPLIANT_NOTICE = (
    "Parking Charge Notice\n"
    "\fNOTICE - REVERSE\n"
    "How to appeal: write to the operator within 28 days of this notice.\n"
    "Protection of Freedoms Act 2012, Schedule 4 applies to this charge.\n"
    "If you were not the driver, you should provide the full name and current "
    "address of the driver, or pass this notice to the driver. "
    "Warning: if, after 28 days, the creditor does not know both the full name and "
    "a current address for service of the driver, the creditor will have the right "
    "to recover the unpaid charge from the keeper of the vehicle.")

GENERIC_POFA = (
    "Where the operator seeks to recover the parking charge from the registered "
    "keeper rather than the driver, it must establish that the statutory conditions "
    "for keeper liability under Schedule 4 of the Protection of Freedoms Act 2012 "
    "have been satisfied. Keeper liability does not arise merely because an "
    "individual is the registered keeper.")

NEUTRAL_KEEPER = (
    "This appeal is submitted by the registered keeper. No admission is made as to "
    "the identity of the driver, and nothing contained within this appeal should be "
    "interpreted as identifying or inferring the identity of the driver.")


def make_case(extra=None, analysis=None, notice_text=COMPLIANT_NOTICE):
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


class KeeperStatusAloneIsNotAGround(unittest.TestCase):
    """Test H."""

    def test_a_plain_keeper_case_gets_no_substantive_pofa_module(self):
        analysis = {"grounds": [{"module_id": "KB-POFA-01"}],
                    "questions": [], "not_supported": []}
        case, pipe = make_case(analysis=[analysis] * 6)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts),
                     "I am the registered keeper. I was not driving.")
        out = pipe.generate(case)
        self.assertIsNone(case.get("pofa_finding"))
        self.assertNotIn("KB-POFA-01", out.pack.module_ids or [])

    def test_a_plain_keeper_case_produces_no_schedule_4_paragraph(self):
        analysis = {"grounds": [{"module_id": "KB-POFA-01"}],
                    "questions": [], "not_supported": []}
        case, pipe = make_case(analysis=[analysis] * 6)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts),
                     "I am the registered keeper. I was not driving.")
        out = pipe.generate(case)
        self.assertNotRegex(
            out.letter or "",
            r"(?i)(statutory conditions for keeper liability|keeper liability does not arise)")

    def test_the_gate_requires_more_than_an_unidentified_driver(self):
        from pcn_appeal.kg.graph import KnowledgeGraph
        from pcn_appeal.rules.dsl import evaluate
        kg = KnowledgeGraph()
        m = kg.modules.get("KB-POFA-01")
        self.assertIsNotNone(m)
        bare = {"driver_status": "UNIDENTIFIED", "jurisdiction": "ENGLAND_WALES",
                "relevant_land": True, "notice_route": "POSTAL",
                "parking_event_date": "01/06/2026", "notice_issue_date": "05/06/2026"}
        self.assertFalse(evaluate(m.use_when, bare),
                         "keeper status + unidentified driver must not open KB-POFA-01")


class ASpecificVerifiedDefectIsAllowed(unittest.TestCase):
    """Test I. The fix must not block a real PoFA ground."""

    def test_a_verified_finding_permits_the_pofa_ground(self):
        from pcn_appeal.kg.graph import KnowledgeGraph
        from pcn_appeal.rules.dsl import evaluate
        kg = KnowledgeGraph()
        m = kg.modules.get("KB-POFA-01")
        facts = {"driver_status": "UNIDENTIFIED", "jurisdiction": "ENGLAND_WALES",
                 "relevant_land": True, "notice_route": "POSTAL",
                 "parking_event_date": "01/06/2026", "notice_issue_date": "05/06/2026",
                 "pofa_finding": "POFA_POSTAL_LATE"}
        self.assertTrue(evaluate(m.use_when, facts),
                        "a verified PoFA finding must open KB-POFA-01")


class ValPofaAuthority(unittest.TestCase):
    """Test K. A substantive Schedule 4 proposition without a locked PoFA
    ground must block release."""

    def _validate(self, text, *, module_ids=(), pofa_findings=(),
                  modules_in_plan=(), plan_grounds=None):
        plan = {"status": "LOCKED", "approved": list(module_ids),
                "grounds": list(plan_grounds or [])}
        pack = RetrievalPack(
            primary_route="POFA" if module_ids else "BAY", secondary_routes=[],
            module_ids=list(module_ids) or ["KB-BAY-01"],
            verified_facts={"vrm": "AB12CDE"}, fact_refs={"vrm": "fid-vrm"},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL",
            pofa_findings=list(pofa_findings), driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            claim_plan=plan)
        mods = list(modules_in_plan) or (list(module_ids) or ["KB-BAY-01"])
        draft = Draft("C-1", [[DraftSentence(text, ["fid-vrm"], mods)]])
        return ValidationEngine().validate(draft, pack)

    def test_a_generic_schedule_4_paragraph_without_a_pofa_ground_is_blocked(self):
        res = self._validate(GENERIC_POFA)
        codes = [i.rule for i in res.issues]
        self.assertIn("VAL-POFA-AUTHORITY", codes, codes)

    def test_neutral_keeper_wording_is_allowed(self):
        res = self._validate(NEUTRAL_KEEPER)
        codes = [i.rule for i in res.issues]
        self.assertNotIn("VAL-POFA-AUTHORITY", codes, codes)

    def test_a_locked_pofa_ground_with_a_finding_permits_the_paragraph(self):
        res = self._validate(GENERIC_POFA, module_ids=["KB-POFA-01"],
                             modules_in_plan=["KB-POFA-01"],
                             pofa_findings=[{"code": "POFA_POSTAL_LATE"}])
        codes = [i.rule for i in res.issues]
        self.assertNotIn("VAL-POFA-AUTHORITY", codes, codes)


if __name__ == "__main__":
    unittest.main()
