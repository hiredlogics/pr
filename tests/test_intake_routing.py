"""Phase 2: one neutral classifier, one route, the route's own gates.

Every uploaded document is classified once, before any service reads it, and
the case goes to exactly one service. These tests drive the customer API with a
scripted classifier and assert, per document type:

  * exactly one classification per document and exactly one route;
  * the completeness policy that route applies;
  * the customer outcome (stop wording, or the private journey continuing);
  * that no other service's engine ran - for every non-private route the
    private-parking extractor is never called and no private fact is created.

Nothing here names a real operator's rules: "Civil Enforcement Ltd" appears only
as the company-name false positive the old debt regex matched.

Run:  python -m unittest discover -s tests -p test_intake_routing.py -v
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pcn_appeal import api
from pcn_appeal.intake import document_types as T
from pcn_appeal.intake import router, run_intake
from pcn_appeal.intake.classifier import DocumentClassification
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.rules import scope
from pcn_appeal.services import ServiceNotAvailable, engine_for, routes, stop_by_code
from pcn_appeal.services import validators
from pcn_appeal.store import cases as case_store
from support import ReferenceAnalysisLLM, patch_client

TWO_PAGES = "page one\f page two"           # a multipage text notice: passes front+back


def doc(ev_id, document_type, stage=None, confidence=0.95, family=None, **extra):
    return {"evidence_id": ev_id, "document_type": document_type,
            "service_family": family or T.FIXED_FAMILY.get(document_type, "UNKNOWN"),
            "stage": stage or T.default_stage(document_type),
            "confidence": confidence, **extra}


def classified(*docs):
    return {"documents": list(docs)}


NOTICE_FIELDS = {
    k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
    for k, v in dict(operator_name="Civil Enforcement Ltd", pcn_number="PCN123456",
                     vrm="AB12CDE", parking_location="Retail Park", site_postcode="M1 1AA",
                     parking_event_date="01/06/2026", notice_issue_date="05/06/2026",
                     charge_amount="£100", alleged_breach="Overstayed paid time",
                     operator_ata="BPA").items()}

# One row per document type the spec lists:
# (name, classification, expected route, expected stop code or None, completeness policy)
CASES = [
    ("debt recovery letter", doc("E1", T.DEBT_RECOVERY), "DEBT_RECOVERY", "DEBT_RECOVERY", "NONE"),
    ("council PCN", doc("E1", T.COUNCIL_PCN), "COUNCIL_PCN", "COUNCIL_PCN", "NONE"),
    ("charge certificate", doc("E1", T.CHARGE_CERTIFICATE), "CHARGE_CERTIFICATE",
     "CHARGE_CERTIFICATE", "FRONT_AND_BACK_OR_MULTIPAGE"),
    ("order for recovery", doc("E1", T.ORDER_FOR_RECOVERY), "ORDER_FOR_RECOVERY",
     "ORDER_FOR_RECOVERY", "ORDER_FOR_RECOVERY_DOCUMENT"),
    ("letter before claim", doc("E1", T.LETTER_BEFORE_CLAIM, family="PRIVATE_PARKING"), "CLAIMS",
     "CLAIMS", "NONE"),
    ("county claim", doc("E1", T.COUNTY_CLAIM, family="PRIVATE_PARKING"), "CLAIMS", "CLAIMS",
     "NONE"),
    ("bailiff letter", doc("E1", T.BAILIFF_ENFORCEMENT, family="COUNCIL_STATUTORY"), "BAILIFF",
     "BAILIFF", "NONE"),
    ("CCJ", doc("E1", T.CCJ), "CCJ_REMOVAL", "CCJ_REMOVAL", "NONE"),
    ("unknown document", doc("E1", T.UNKNOWN, confidence=0.9), "UNSUPPORTED_REVIEW",
     "UNSUPPORTED_REVIEW", "NONE"),
]


def upload(client, files):
    case_id = client.post("/cases").json()["case_id"]
    res = client.post(f"/cases/{case_id}/files",
                      files=[("files", (name, text.encode(), "text/plain")) for name, text in files])
    return case_id, res


class EachDocumentTypeRoutesOnce(unittest.TestCase):

    def run_case(self, classification):
        llm = FakeLLM({"classification": [classified(classification)]})
        patch_client(self, llm)
        client = TestClient(api.app)
        # A single page: no route below may answer it with "upload both sides".
        case_id, res = upload(client, [("letter.txt", "one page")])
        return llm, case_id, res

    def test_every_non_private_document_type(self):
        for name, classification, route, stop_code, policy in CASES:
            with self.subTest(name):
                llm, case_id, res = self.run_case(classification)
                self.assertEqual(res.status_code, 200, res.text)
                body = res.json()
                case = api.CASES[case_id]["case"]

                self.assertEqual(len(case.classifications), 1, "one classification per document")
                self.assertEqual(case.route, route)
                self.assertEqual(body["route"], route)
                self.assertEqual(engine_for(route).completeness.name, policy)

                self.assertEqual(body["state"], CaseState.NO_APPEAL_RIGHT.value)
                self.assertEqual(body["stop_code"], stop_code)
                self.assertTrue(body["stop_reason"])
                self.assertTrue(body["recommendation"])
                self.assertEqual(body["questions"], [])

                tasks = [c["task"] for c in llm.calls]
                self.assertEqual(tasks, ["classification"],
                                 "no service engine may run on a redirected case")
                self.assertEqual(case.facts, {}, "no private-parking fact may be created")
                self.assertEqual(case.document_classes, {})

    def test_debt_recovery_outcome_wording(self):
        _, _, res = self.run_case(doc("E1", T.DEBT_RECOVERY))
        body = res.json()
        self.assertEqual(body["stop_title"], "Debt recovery document identified")
        self.assertEqual(body["stop_reason"],
                         "You have uploaded a debt recovery letter. Our appeal service does "
                         "not currently support cases at this stage.")
        self.assertEqual(body["recommendation"],
                         "You may wish to use our free debt recovery response template.")
        self.assertEqual(body["cta"]["action"], "DEBT_RECOVERY_TEMPLATE")
        shown = " ".join(str(body.get(k) or "") for k in
                         ("stop_title", "stop_reason", "recommendation")).lower()
        shown += " " + (body["cta"]["label"] or "").lower()
        for banned in ("upload failed", "try again", "original pcn"):
            self.assertNotIn(banned, shown)

    def test_cta_link_is_added_only_when_configured(self):
        from unittest import mock
        _, _, res = self.run_case(doc("E1", T.DEBT_RECOVERY))
        self.assertNotIn("href", res.json()["cta"])
        with mock.patch.dict("os.environ",
                             {"CTA_URL_DEBT_RECOVERY_TEMPLATE": "/resources/debt-template"}):
            _, _, res = self.run_case(doc("E1", T.DEBT_RECOVERY))
        self.assertEqual(res.json()["cta"]["href"], "/resources/debt-template")

    def test_customer_wording_carries_no_internal_codes(self):
        for route in routes():
            stop = engine_for(route).outcome(CaseFile("C", route=route,
                                                      stage="APPEAL_WINDOW_CLOSED"))
            if stop is None:
                continue
            for text in (stop.title or "", stop.message, stop.recommendation, stop.cta_label or ""):
                self.assertNotRegex(text, r"KB-|VAL-|SCOP-|[A-Z]{3,}_[A-Z]{3,}")


class PrivateParkingStillReachesItsEngine(unittest.TestCase):

    def test_ntk_routes_to_private_parking_and_extracts(self):
        llm = ReferenceAnalysisLLM({"classification": [classified(doc("E1", T.PRIVATE_PARKING_NOTICE))],
                                    "extraction": [{"fields": NOTICE_FIELDS, "doc_types": {"E1": "NTK"}}]})
        patch_client(self, llm)
        case_id, res = upload(TestClient(api.app), [("ntk.txt", "Notice to Keeper\f reverse")])
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["route"], "PRIVATE_PARKING")
        self.assertEqual(body["state"], CaseState.EXTRACTED.value)
        self.assertNotIn("stop_code", body)
        self.assertEqual([c["task"] for c in llm.calls], ["classification", "extraction"])
        self.assertEqual(engine_for("PRIVATE_PARKING").completeness.name,
                         "FRONT_AND_BACK_OR_MULTIPAGE")

    def test_private_notice_still_needs_both_sides(self):
        llm = FakeLLM({"classification": [classified(doc("E1", T.PRIVATE_PARKING_NOTICE))]})
        patch_client(self, llm)
        case_id, res = upload(TestClient(api.app), [("front.txt", "front only")])
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"]["code"], "NOTICE_SIDES_REQUIRED")
        case = api.CASES[case_id]["case"]
        self.assertEqual(case.state, CaseState.CREATED, "the customer can retry the same case")
        self.assertIsNone(case.route, "a retry is classified afresh")
        self.assertNotIn("extraction", [c["task"] for c in llm.calls])

    def test_civil_enforcement_ltd_notice_is_not_debt_recovery(self):
        """The company name used to match the debt regex. Classified as a notice,
        it reaches the private engine, and the engine's own gate lets it through."""
        text = ("Civil Enforcement Ltd\nNOTICE TO KEEPER\nParking Charge Notice PCN123456\n"
                "If unpaid, this charge may be passed to debt recovery.\f Terms on reverse")
        llm = ReferenceAnalysisLLM({
            "classification": [classified(doc("E1", T.PRIVATE_PARKING_NOTICE,
                                              issuer={"name": "Civil Enforcement Ltd",
                                                      "kind": "PRIVATE_OPERATOR"}))],
            "extraction": [{"fields": NOTICE_FIELDS, "doc_types": {"E1": "NTK"}}]})
        patch_client(self, llm)
        client = TestClient(api.app)
        case_id, res = upload(client, [("ntk.txt", text)])
        self.assertEqual(res.json()["route"], "PRIVATE_PARKING")
        case = api.CASES[case_id]["case"]
        self.assertEqual(case.document_classes, {"E1": "NTK"},
                         "routine debt-recovery warning must not relabel a notice")
        self.assertIsNone(scope.decide(case))
        self.assertFalse(scope.DEBT_SIGNALS.search("Civil Enforcement Ltd"))


class PrecedenceAndTrust(unittest.TestCase):

    def resolve(self, *rows):
        return router.resolve({r["evidence_id"]: DocumentClassification(
            r["evidence_id"], r["document_type"], r["service_family"], r["stage"],
            confidence=r["confidence"]) for r in rows})

    def test_most_advanced_document_decides(self):
        d = self.resolve(doc("E1", T.PRIVATE_PARKING_NOTICE), doc("E2", T.DEBT_RECOVERY))
        self.assertEqual((d.route, d.decided_by), ("DEBT_RECOVERY", "E2"))
        d = self.resolve(doc("E1", T.COUNCIL_PCN), doc("E2", T.ORDER_FOR_RECOVERY))
        self.assertEqual(d.route, "ORDER_FOR_RECOVERY")
        d = self.resolve(doc("E1", T.PRIVATE_PARKING_NOTICE),
                         doc("E2", T.COUNTY_CLAIM, family="PRIVATE_PARKING"))
        self.assertEqual(d.route, "CLAIMS")

    def test_supporting_evidence_never_routes(self):
        d = self.resolve(doc("E1", T.PRIVATE_PARKING_NOTICE), doc("E2", T.SUPPORTING_EVIDENCE))
        self.assertEqual(d.route, "PRIVATE_PARKING")
        d = self.resolve(doc("E1", T.SUPPORTING_EVIDENCE))
        self.assertEqual(d.route, "UNSUPPORTED_REVIEW")

    def test_low_confidence_goes_to_review_not_to_a_guess(self):
        d = self.resolve(doc("E1", T.ORDER_FOR_RECOVERY, confidence=0.4))
        self.assertEqual(d.route, "UNSUPPORTED_REVIEW")
        self.assertEqual(d.document_type, T.ORDER_FOR_RECOVERY)

    def test_family_contradicting_the_type_goes_to_review(self):
        d = self.resolve(doc("E1", T.CHARGE_CERTIFICATE, family="PRIVATE_PARKING"))
        self.assertEqual(d.route, "UNSUPPORTED_REVIEW")

    def test_every_routing_type_has_exactly_one_route(self):
        for t in T.ROUTING_TYPES:
            self.assertIn(router.ServiceRouteRegistry.route_for(t), router.ROUTES)
        self.assertEqual(set(routes()), set(router.ROUTES))


class ClassifierContract(unittest.TestCase):

    def case(self, n=1):
        return CaseFile("C-K", evidence={f"E{i}": EvidenceItem(f"E{i}", "OTHER", f"{i}.txt",
                                                              text="Order for Recovery")
                                         for i in range(1, n + 1)})

    def test_unrecognised_label_is_unknown_and_unclassified_document_is_kept(self):
        case = self.case(2)
        llm = FakeLLM({"classification": [classified(
            {"evidence_id": "E1", "document_type": "PARKING_THING", "confidence": 0.99})]})
        run_intake(case, llm)
        self.assertEqual(case.classifications["E1"]["document_type"], T.UNKNOWN)
        self.assertEqual(case.classifications["E2"]["ambiguity_reason"], "not classified")
        self.assertEqual(case.route, "UNSUPPORTED_REVIEW")

    def test_stage_outside_the_type_falls_back_to_its_default(self):
        case = self.case()
        run_intake(case, FakeLLM({"classification": [classified(
            doc("E1", T.ORDER_FOR_RECOVERY, stage="APPEAL_WINDOW_CLOSED"))]}))
        self.assertEqual(case.stage, "ORDER_FOR_RECOVERY")

    def test_quoted_spans_are_checked_against_the_text(self):
        case = self.case()
        run_intake(case, FakeLLM({"classification": [classified(doc(
            "E1", T.ORDER_FOR_RECOVERY,
            evidence_spans=[{"page": 1, "text": "order for  recovery"},
                            {"page": 1, "text": "invented words"}]))]}))
        spans = case.classifications["E1"]["evidence_spans"]
        self.assertEqual([s["found_in_text"] for s in spans], [True, False])

    def test_classifier_failure_is_a_technical_stop_not_a_route(self):
        llm = FakeLLM({})                                 # raises on every call
        patch_client(self, llm)
        case_id, res = upload(TestClient(api.app), [("x.txt", TWO_PAGES)])
        body = res.json()
        self.assertEqual(body["state"], CaseState.CLASSIFICATION_FAILED.value)
        self.assertEqual(body["stop_code"], "CLASSIFICATION_FAILED")
        self.assertIsNone(body["route"])
        self.assertEqual([c["task"] for c in llm.calls], ["classification"])


class PrivateStagesTheServiceDoesNotTake(unittest.TestCase):

    def test_closed_appeal_window_and_operator_response_stop_inside_the_route(self):
        for stage_doc, code in (
                (doc("E1", T.PRIVATE_PARKING_NOTICE, stage="APPEAL_WINDOW_CLOSED"), "OUT_OF_STAGE"),
                (doc("E1", T.PRIVATE_PARKING_APPEAL_RESPONSE), "PRIVATE_APPEAL_RESPONSE")):
            with self.subTest(code):
                llm = FakeLLM({"classification": [classified(stage_doc)]})
                patch_client(self, llm)
                _, res = upload(TestClient(api.app), [("x.txt", "one page")])
                body = res.json()
                self.assertEqual(body["route"], "PRIVATE_PARKING")
                self.assertEqual(body["stop_code"], code)
                self.assertNotIn("extraction", [c["task"] for c in llm.calls])


class TheBoundaryHoldsDownstream(unittest.TestCase):

    def stopped_case(self):
        llm = FakeLLM({"classification": [classified(doc("E1", T.ORDER_FOR_RECOVERY))]})
        patch_client(self, llm)
        client = TestClient(api.app)
        case_id, _ = upload(client, [("ofr.txt", "Order for Recovery")])
        return llm, client, case_id

    def test_confirm_and_continue_return_the_stop_without_running_the_engine(self):
        llm, client, case_id = self.stopped_case()
        for res in (client.post(f"/cases/{case_id}/confirm", json={"narrative": "help"}),
                    client.post(f"/appeal/{case_id}", json={"answers": {}})):
            self.assertEqual(res.json()["stop_code"], "ORDER_FOR_RECOVERY")
        self.assertEqual([c["task"] for c in llm.calls], ["classification"])

    def test_case_status_reports_the_route(self):
        _, client, case_id = self.stopped_case()
        body = client.get(f"/cases/{case_id}").json()
        self.assertEqual((body["route"], body["document_type"], body["stage"]),
                         ("ORDER_FOR_RECOVERY", "ORDER_FOR_RECOVERY", "ORDER_FOR_RECOVERY"))
        self.assertEqual(body["stop_code"], "ORDER_FOR_RECOVERY")
        self.assertNotIn("facts", body)

    def test_private_pipeline_refuses_a_case_routed_elsewhere(self):
        """Defence in depth: called directly, the private engine still stops."""
        case = CaseFile("C-D", route="DEBT_RECOVERY", document_type=T.DEBT_RECOVERY,
                        stage="DEBT_RECOVERY", document_classes={"E1": "NTK"},
                        evidence={"E1": EvidenceItem("E1", "NTK", "n.txt", text="x")})
        out = AppealPipeline(FakeLLM({})).generate(case)
        self.assertIsNone(out.letter)
        self.assertEqual(case.scope_stop, "DEBT_RECOVERY")
        self.assertEqual(case.state, CaseState.NO_APPEAL_RIGHT)

    def test_unfinished_services_have_no_fake_engine(self):
        for route in routes():
            if route == "PRIVATE_PARKING":
                continue
            engine = engine_for(route)
            self.assertFalse(engine.live)
            for step in ("extract_service_facts", "analyse", "get_questions", "generate"):
                with self.assertRaises(ServiceNotAvailable):
                    getattr(engine, step)(CaseFile("C"))


class ValidatorsStayOnTheirRoute(unittest.TestCase):

    def test_private_rules_never_apply_to_other_routes(self):
        self.assertIn("VAL-STAGE", validators.rules_for("PRIVATE_PARKING"))
        for route in routes():
            if route != "PRIVATE_PARKING":
                self.assertNotIn("VAL-STAGE", validators.rules_for(route))
                with self.assertRaises(ServiceNotAvailable):
                    validators.engine_for(route)

    def test_core_rules_apply_everywhere(self):
        for route in routes():
            for rule in ("VAL-FACT", "VAL-EVIDENCE", "VAL-SIGNATURE"):
                self.assertIn(rule, validators.rules_for(route))


class RoutingSurvivesRehydration(unittest.TestCase):

    def test_routing_columns_round_trip(self):
        from unittest import mock
        case = CaseFile("C-R")
        case.evidence["E1"] = EvidenceItem("E1", "OTHER", "cc.txt", text="Charge Certificate")
        run_intake(case, FakeLLM({"classification": [classified(
            doc("E1", T.CHARGE_CERTIFICATE, document_date="02/09/2026"))]}))
        case.document_classes = {"E1": "OTHER"}
        with mock.patch.object(case_store, "_json", lambda v: v):
            values = case_store.routing_columns(case)
        import json
        stored = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in values]

        reloaded = CaseFile("C-R", state=case.state)
        case_store.apply_routing_columns(reloaded, stored)
        for attr in ("route", "document_type", "stage", "scope_stop", "document_classes",
                     "classifications", "timeline"):
            self.assertEqual(getattr(reloaded, attr), getattr(case, attr), attr)
        self.assertEqual(reloaded.timeline[0]["date"], "2026-09-02")
        self.assertEqual(scope.decide(reloaded).code, "CHARGE_CERTIFICATE",
                         "a reloaded case stops exactly as it did before")


class StopCodesResolve(unittest.TestCase):

    def test_every_issued_stop_code_has_customer_wording(self):
        for route in routes():
            for stage in (None, "APPEAL_WINDOW_CLOSED", "OPERATOR_RESPONSE"):
                stop = engine_for(route).outcome(CaseFile("C", route=route, stage=stage))
                if stop is not None:
                    self.assertIs(stop_by_code(stop.code), stop)
        self.assertIsNotNone(stop_by_code("CLASSIFICATION_FAILED"))


if __name__ == "__main__":
    unittest.main()
