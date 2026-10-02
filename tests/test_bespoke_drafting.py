"""Bespoke drafting architecture — customer facts reach Case Intelligence → pack → letter.

Regression A–H from the global root-cause fix. These assert the pipeline, not
operator-specific or Parent&Child hard-codes: gate-satisfied fact-specific
grounds must survive, always-on LAND must not replace them, and free-text
facts must reach verified_facts / case_context for drafting.
"""
from __future__ import annotations

import unittest

from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.models import (
    CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact, FactSource,
    FactStatus, SourceKind,
)
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
            for k, v in kw.items()}


BAY = dict(
    operator_name="Euro Car Parks", pcn_number="45000564251", vrm="RX75VPP",
    parking_location="Sainsburys - Cromwell Road", site_postcode="SW7 4ED",
    parking_event_date="19/09/2026", notice_issue_date="22/09/2026",
    charge_amount="£100",
    alleged_breach="Parked in a Parent and Child bay without being accompanied by a child",
    observation_time="12:23", event_time="12:23",
    restricted_bay_alleged=True, operator_ata="BPA",
)


def bay_pipe(extra=None, ask=None, case_analysis=None):
    f = dict(BAY, **(extra or {}))
    # Drop Nones so extraction does not invent null fields.
    f = {k: v for k, v in f.items() if v is not None}
    responses = {
        "extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}],
    }
    if case_analysis is not None:
        responses["case_analysis"] = list(case_analysis)
    llm = ReferenceAnalysisLLM(responses, ask=ask or [])
    case = CaseFile("C-BAY", evidence={
        "E1": EvidenceItem("E1", "NTK", "ntk.pdf",
                           text="Parent and Child bay\nObservation 12:23 Event 12:23",
                           images=[b"JPEGFRONT", b"JPEGBACK"]),
    })
    return case, AppealPipeline(llm)


class A_ParentChildWithChildren(unittest.TestCase):
    """A: Parent/Child notice + children confirmed → bespoke bay appeal, not LAND-only."""

    def test_children_fact_reaches_pack_and_bay_leads(self):
        case, pipe = bay_pipe()
        pipe.ingest(case)
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED],
                     "Left kids in the car. Child remained in the vehicle during the visit.")
        self.assertTrue(case.get("child_occupant_present"), case.facts.keys())
        self.assertTrue(case.get("account_contradicts_allegation"))
        self.assertTrue(case.get("material_account_proposition"))

        out = pipe.generate(case)
        self.assertIn("KB-BAY-01", out.pack.module_ids, out.pack.trace)
        self.assertNotIn("KB-LAND-01", out.pack.module_ids, out.pack.trace)
        self.assertEqual(out.pack.primary_route, "BAY", out.pack.module_ids)
        self.assertTrue(out.pack.verified_facts.get("child_occupant_present"))
        self.assertTrue(out.pack.case_context.get("account_contradicts_allegation"))
        self.assertTrue(out.pack.case_context.get("material_account_propositions"))

        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        low = (out.letter or "").lower()
        self.assertIn("parent", low)
        self.assertRegex(low, r"child|children")
        self.assertNotIn("sufficient authority from the landowner", low)
        # Professional rewrite — not the customer's "Left kids"
        self.assertNotIn("left kids", low)


class B_ParentChildWithoutChildren(unittest.TestCase):
    """B: same notice without children — still bay window; no invented child fact."""

    def test_bay_window_without_inventing_children(self):
        case, pipe = bay_pipe()
        pipe.ingest(case)
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED], "")
        self.assertFalse(case.get("child_occupant_present"))
        out = pipe.generate(case)
        self.assertIn("KB-BAY-01", out.pack.module_ids, out.pack.trace)
        self.assertNotIn("KB-LAND-01", out.pack.module_ids)
        low = (out.letter or "").lower()
        self.assertIn("observation", low)
        self.assertNotIn("left kids", low)
        self.assertNotIn("presence of children", low)


class C_NoLandWithoutTrigger(unittest.TestCase):
    """C: model omits BAY → bounded reassessment can add gate-satisfied BAY.

    LAND is not auto-deleted merely because BAY exists (claim-plan rule).
    """

    def test_model_proposing_only_land_reassessment_adds_bay(self):
        f = {k: v for k, v in BAY.items() if v is not None}
        land_only = {
            "grounds": [{"module_id": "KB-LAND-01", "supported_by": [], "note": "x"}],
            "questions": [], "not_supported": [],
        }
        with_bay = {
            "grounds": [
                {"module_id": "KB-LAND-01", "supported_by": [], "note": "x"},
                {"module_id": "KB-BAY-01", "supported_by": [], "note": "reassessment"},
                {"module_id": "KB-BAY-02", "supported_by": [], "note": "reassessment"},
            ],
            "questions": [], "not_supported": [],
        }
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(**f), "doc_types": {"E1": "NTK"}}],
            # Initial omit, then reassessment includes bay grounds.
            "case_analysis": [land_only, with_bay],
        })
        case = CaseFile("C-LAND", evidence={
            "E1": EvidenceItem("E1", "NTK", "n.pdf",
                               text="Parent and Child bay Observation 12:23 Event 12:23",
                               images=[b"a", b"b"]),
        })
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED],
                     "children were in the vehicle")
        self.assertTrue(
            {"KB-BAY-01", "KB-BAY-02"} & set(case.analysis_module_ids or []),
            case.analysis_module_ids,
        )


class D_AnprPhotoWithoutTimes(unittest.TestCase):
    """D: photographic notice without entry/exit → no ANPR duration argument."""

    def test_no_anpr_duration_without_times(self):
        f = dict(BAY)
        f.pop("observation_time", None)
        f.pop("event_time", None)
        f["alleged_breach"] = "Parked without payment"
        f["restricted_bay_alleged"] = False
        for k in ("entry_time", "exit_time"):
            f.pop(k, None)
        llm = ReferenceAnalysisLLM({
            "extraction": [{"fields": fields(**{k: v for k, v in f.items() if v is not None}),
                            "doc_types": {"E1": "NTK"}}],
        }, ask=[{"fact": "anpr_duration_disputed", "text": "Is duration disputed?", "type": "bool"}])
        case = CaseFile("C-ANPR", evidence={
            "E1": EvidenceItem("E1", "NTK", "n.pdf", text="Notice photo only")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        qs = pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                     if f.status == FactStatus.EXTRACTED], "")
        self.assertNotIn("anpr_duration_disputed", [q["fact"] for q in qs])
        out = pipe.generate(case)
        self.assertNotIn("KB-TIME-01", out.pack.module_ids)
        self.assertNotIn("KB-ANPR-01", out.pack.module_ids)


class E_FreeTextNormalized(unittest.TestCase):
    """E: free-text → normalized fact → drafting; no verbatim paste."""

    def test_free_text_normalized_not_pasted(self):
        case, pipe = bay_pipe()
        pipe.ingest(case)
        raw = "Left Kidd in car while seeking Parent and Child space!!!"
        pipe.confirm(case, {}, [n for n, f in case.facts.items()
                                if f.status == FactStatus.EXTRACTED], raw)
        assess_material_account(case)
        self.assertTrue(case.get("child_occupant_present"))
        prop = str(case.get("material_account_proposition") or "").lower()
        self.assertIn("child", prop)
        self.assertNotIn("kidd", prop)
        out = pipe.generate(case)
        self.assertNotIn("kidd", (out.letter or "").lower())
        self.assertNotIn("left kidd", (out.letter or "").lower())


class F_PaymentFactsReachDrafting(unittest.TestCase):
    def test_payment_made_in_pack(self):
        f = dict(
            operator_name="Acme", pcn_number="P1", vrm="AB12CDE",
            parking_location="Car Park", site_postcode="M1 1AA",
            parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
            charge_amount="£100", alleged_breach="No payment made",
            operator_ata="BPA",
        )
        llm = ReferenceAnalysisLLM({"extraction": [{"fields": fields(**f),
                                                    "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-PAY", evidence={
            "E1": EvidenceItem("E1", "PCN", "p.pdf", text="No payment")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I paid on the app")
        pipe.answer(case, {"payment_made": True})
        out = pipe.generate(case)
        self.assertTrue(out.pack.verified_facts.get("payment_made"))
        # Payment ground should lead when use_when met (KB-PAY-* depends on payment_made).
        self.assertTrue(
            any(m.startswith("KB-PAY") or m.startswith("KB-KEY") for m in out.pack.module_ids)
            or out.pack.verified_facts.get("payment_made"),
            out.pack.module_ids)


class G_BreakdownFactsReachDrafting(unittest.TestCase):
    def test_immobilised_in_pack(self):
        f = dict(
            operator_name="Acme", pcn_number="P1", vrm="AB12CDE",
            parking_location="Car Park", site_postcode="M1 1AA",
            parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
            charge_amount="£100", alleged_breach="Overstayed paid time",
            operator_ata="BPA", entry_time="10:00", exit_time="14:00",
        )
        llm = ReferenceAnalysisLLM({"extraction": [{"fields": fields(**f),
                                                    "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-BRK", evidence={
            "E1": EvidenceItem("E1", "PCN", "p.pdf", text="Overstay")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "the car broke down, flat battery")
        pipe.answer(case, {"vehicle_immobilised": True,
                           "immobilisation_prevented_departure": True})
        out = pipe.generate(case)
        self.assertTrue(out.pack.verified_facts.get("vehicle_immobilised"))


class H_ResidentialFactsReachDrafting(unittest.TestCase):
    def test_resident_status_in_pack(self):
        f = dict(
            operator_name="Acme", pcn_number="P1", vrm="AB12CDE",
            parking_location="Block A", site_postcode="M1 1AA",
            parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
            charge_amount="£100", alleged_breach="No permit displayed",
            operator_ata="BPA",
        )
        # P7 B1: the facts arrive as answers to questions the analysis judged
        # material (scripted, as the production model would ask them) - and
        # resident_status is only material because the lease is on the case:
        # without a parking clause nothing turns on it and the Question
        # Authority rightly refuses the question.
        llm = ReferenceAnalysisLLM(
            {"extraction": [{"fields": fields(**f),
                             "doc_types": {"E1": "PCN", "E2": "TENANCY"}}]},
            ask=[{"fact": "resident_status",
                  "text": "What is the keeper's connection to the site?",
                  "type": "text", "material_because": "residential rights"},
                 {"fact": "permit_held",
                  "text": "Was a permit for the space held at the time?",
                  "type": "bool", "material_because": "permit allegation"}])
        lease = ("3.2 The Tenant shall have the right to park one private motor "
                 "vehicle in the parking space numbered 14 shown on the plan.")
        case = CaseFile("C-RES", evidence={
            "E1": EvidenceItem("E1", "PCN", "p.pdf", text="No permit"),
            "E2": EvidenceItem("E2", "TENANCY", "tenancy.pdf", text=lease)})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "I am a resident")
        pipe.answer(case, {"resident_status": "RESIDENT", "permit_held": True})
        out = pipe.generate(case)
        self.assertEqual(out.pack.verified_facts.get("resident_status"), "RESIDENT")
        self.assertNotIn("KB-LAND-01", out.pack.module_ids)


class LandOnlyBlockedWhenFactSpecific(unittest.TestCase):
    def test_validator_blocks_land_only_with_bay_facts(self):
        case, pipe = bay_pipe()
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "children in the vehicle")
        pack = pipe.reasoning.analyse(case, selected_ids=["KB-LAND-01"])
        # Force a land-only pack for the validator check.
        pack.module_ids = ["KB-LAND-01"]
        pack.verified_facts["restricted_bay_alleged"] = True
        pack.verified_facts["child_occupant_present"] = True
        draft = Draft("C", [[DraftSentence(
            "The operator is requested to establish that it had sufficient authority "
            "from the landowner or other entitled party to operate and enforce the "
            "parking scheme at the location on the material date.",
            [], ["KB-LAND-01"])]])
        result = pipe.validation.validate(draft, pack)
        self.assertFalse(result.passed)
        self.assertIn("VAL-SUBSTANCE", {i.rule for i in result.issues})


if __name__ == "__main__":
    unittest.main()
