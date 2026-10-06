"""P17.8 — generic critical document identity integrity (categories, not cases)."""
from __future__ import annotations

import unittest
from datetime import date

from pcn_appeal.document_identity import (
    CRITICAL_FIELDS,
    STATUS_CONFLICT,
    STATUS_UNCERTAIN,
    STATUS_VERIFIED,
    authoritative_identity_values,
    build_identity_state,
    canonicalize,
    canonical_equal,
    draft_identity_mismatches,
    establish_document_identity,
    identity_blocks_claim_plan,
    identity_revision,
    load_identity_state,
    reconcile_field,
    timing_identity_ready,
    FieldObservation,
    assess_document_pair,
    apply_identity_to_facts,
    attach_identity_state,
)
from pcn_appeal.engines.extraction import ExtractionEngine
from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile, Draft, DraftSentence, EvidenceItem, Fact, FactSource, FactStatus,
    RetrievalPack, SourceKind,
)


def _obs(name, raw, method="initial_extraction", conf=0.95, ev="E1",
         read="VERIFIED"):
    return FieldObservation(
        raw_value=raw,
        canonical_value=canonicalize(name, raw),
        source_evidence_id=ev,
        page=1,
        confidence=conf,
        extraction_method=method,
        read_status=read,
    )


def _case_with_identity(**fields) -> CaseFile:
    case = CaseFile("C-ID")
    case.document_classes["E1"] = "PRIVATE_PARKING_NOTICE"
    case.evidence["E1"] = EvidenceItem(
        "E1", "PCN", "notice_front.txt",
        text="Operator Name: Acme Parking\nPCN Number: 1234567890\n"
             "Vehicle Registration: AB12CDE\nLocation: Retail Park\n"
             "Date of Contravention: 01/06/2026\nDate of Issue: 20/06/2026\n",
        images=[],
    )
    for name, value in fields.items():
        case.put(Fact(
            f"F-{name}", name, value, FactStatus.EXTRACTED,
            FactSource(SourceKind.DOCUMENT, "E1#p1"), confidence=0.95,
        ))
    return case


class CanonicalizationTests(unittest.TestCase):
    def test_I_vrm_formatting_only(self):
        self.assertEqual(canonicalize("vrm", "AB12 CDE"), "AB12CDE")
        self.assertTrue(canonical_equal("vrm", "AB12 CDE", "AB12CDE"))

    def test_no_character_repair(self):
        # Must not coerce O→0 etc.
        self.assertEqual(canonicalize("vrm", "AB12CDE"), "AB12CDE")
        self.assertNotEqual(canonicalize("pcn_number", "123456789O"), "1234567890")


class ReconcileTests(unittest.TestCase):
    def test_A_agree_verified(self):
        rows = [
            _obs("vrm", "AB12CDE", "initial_extraction"),
            _obs("vrm", "AB12 CDE", "independent_verify", conf=0.9),
        ]
        rec = reconcile_field("vrm", rows, revision=1)
        self.assertEqual(rec.status, STATUS_VERIFIED)
        self.assertEqual(rec.canonical_value, "AB12CDE")

    def test_B_vrm_conflict(self):
        rows = [
            _obs("vrm", "AB12CDE", "initial_extraction"),
            _obs("vrm", "AB12CDF", "independent_verify", conf=0.9),
        ]
        rec = reconcile_field("vrm", rows, revision=1)
        self.assertEqual(rec.status, STATUS_CONFLICT)

    def test_C_pcn_conflict(self):
        rows = [
            _obs("pcn_number", "1234567890", "initial_extraction"),
            _obs("pcn_number", "1234567891", "classifier_references", conf=0.9),
        ]
        rec = reconcile_field("pcn_number", rows, revision=1)
        self.assertEqual(rec.status, STATUS_CONFLICT)


class IdentityPipelineTests(unittest.TestCase):
    def test_A_release_allowed_when_identity_passes(self):
        case = _case_with_identity(
            operator_name="Acme Parking",
            pcn_number="1234567890",
            vrm="AB12CDE",
            parking_event_date=date(2026, 6, 1),
            notice_issue_date=date(2026, 6, 20),
            parking_location="Retail Park",
        )
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"pcn_number": "1234567890", "vrm": "AB12CDE"},
        }
        state = establish_document_identity(case, llm=FakeLLM({}))
        self.assertTrue(state.complete)
        self.assertIsNone(identity_blocks_claim_plan(case))
        self.assertEqual(state.field_status["vrm"], STATUS_VERIFIED)
        self.assertEqual(state.field_status["pcn_number"], STATUS_VERIFIED)

    def test_B_vrm_ocr_conflict_blocks(self):
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "AB12CDF", "pcn_number": "1234567890"},
        }
        state = establish_document_identity(case, llm=FakeLLM({
            "identity_verification": [{"fields": {
                "vrm": {"candidate_value": "AB12CDF", "confidence": 0.95,
                        "read_status": "VERIFIED", "evidence_id": "E1"},
                "pcn_number": {"candidate_value": "1234567890", "confidence": 0.95,
                               "read_status": "VERIFIED", "evidence_id": "E1"},
            }}],
        }))
        self.assertEqual(state.field_status["vrm"], STATUS_CONFLICT)
        self.assertIsNotNone(identity_blocks_claim_plan(case))
        self.assertEqual(case.facts["vrm"].status, FactStatus.UNCERTAIN)

    def test_C_pcn_digit_conflict_blocks(self):
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "AB12CDE", "pcn_number": "1234567899"},
        }
        state = establish_document_identity(case, llm=FakeLLM({}))
        self.assertEqual(state.field_status["pcn_number"], STATUS_CONFLICT)
        block = identity_blocks_claim_plan(case)
        self.assertTrue(block and "CONFLICT" in block)

    def test_D_E_timing_blocked_on_date_conflict(self):
        case = _case_with_identity(
            vrm="AB12CDE", pcn_number="1234567890",
            parking_event_date=date(2026, 6, 1),
            notice_issue_date=date(2026, 6, 20),
        )
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "AB12CDE", "pcn_number": "1234567890"},
        }
        state = establish_document_identity(case, llm=FakeLLM({
            "identity_verification": [{"fields": {
                "parking_event_date": {
                    "candidate_value": "02/06/2026", "confidence": 0.95,
                    "read_status": "VERIFIED", "evidence_id": "E1",
                },
                "notice_issue_date": {
                    "candidate_value": "20/06/2026", "confidence": 0.95,
                    "read_status": "VERIFIED", "evidence_id": "E1",
                },
                "vrm": {"candidate_value": "AB12CDE", "confidence": 0.95,
                        "read_status": "VERIFIED"},
                "pcn_number": {"candidate_value": "1234567890", "confidence": 0.95,
                               "read_status": "VERIFIED"},
            }}],
        }))
        self.assertEqual(state.field_status["parking_event_date"], STATUS_CONFLICT)
        ok, deps = timing_identity_ready(case)
        self.assertFalse(ok)
        self.assertTrue(any("parking_event_date" in d for d in deps))

    def test_F_location_conflict_dependency(self):
        case = _case_with_identity(
            vrm="AB12CDE", pcn_number="1234567890",
            parking_location="Retail Park North",
        )
        state = establish_document_identity(case, llm=FakeLLM({
            "identity_verification": [{"fields": {
                "parking_location": {
                    "candidate_value": "Retail Park South", "confidence": 0.95,
                    "read_status": "VERIFIED", "evidence_id": "E1",
                },
                "vrm": {"candidate_value": "AB12CDE", "confidence": 0.95,
                        "read_status": "VERIFIED"},
                "pcn_number": {"candidate_value": "1234567890", "confidence": 0.95,
                               "read_status": "VERIFIED"},
            }}],
        }))
        self.assertEqual(state.field_status["parking_location"], STATUS_CONFLICT)

    def test_G_reverse_from_other_notice_pair_conflict(self):
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        case.evidence["E2"] = EvidenceItem(
            "E2", "PCN", "notice_back.txt", text="reverse page", images=[],
        )
        case.document_classes["E2"] = "PRIVATE_PARKING_NOTICE"
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "AB12CDE", "pcn_number": "1234567890"},
            "pages": [{"page": 1, "side": "FRONT"}],
        }
        case.classifications["E2"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "ZZ99ZZZ", "pcn_number": "9999999999"},
            "pages": [{"page": 1, "side": "REVERSE"}],
        }
        pair = assess_document_pair(case)
        self.assertTrue(pair["document_pair_conflict"])
        state = establish_document_identity(case, llm=FakeLLM({}))
        self.assertTrue(state.document_pair_conflict)
        self.assertEqual(identity_blocks_claim_plan(case), "DOCUMENT_PAIR_CONFLICT")

    def test_H_reverse_without_identifier_ok(self):
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        case.evidence["E2"] = EvidenceItem(
            "E2", "PCN", "notice_back.txt",
            text="How to appeal. Pass this notice to the driver. Schedule 4.",
            images=[],
        )
        case.document_classes["E2"] = "PRIVATE_PARKING_NOTICE"
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "AB12CDE", "pcn_number": "1234567890"},
            "pages": [{"page": 1, "side": "FRONT"}],
        }
        case.classifications["E2"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {},  # no identifiers on reverse
            "pages": [{"page": 1, "side": "REVERSE"}],
        }
        pair = assess_document_pair(case)
        self.assertFalse(pair["document_pair_conflict"])

    def test_J_revision_increments_on_change(self):
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        case.classifications["E1"] = {
            "document_type": "PRIVATE_PARKING_NOTICE",
            "references": {"vrm": "AB12CDE", "pcn_number": "1234567890"},
        }
        establish_document_identity(case, llm=FakeLLM({}))
        r1 = identity_revision(case)
        # Correct identity value via new extraction fact
        case.put(Fact(
            "F-vrm", "vrm", "AB12CDX", FactStatus.EXTRACTED,
            FactSource(SourceKind.DOCUMENT, "E1#p1"), confidence=0.95,
        ))
        case.classifications["E1"]["references"]["vrm"] = "AB12CDX"
        establish_document_identity(case, llm=FakeLLM({}))
        self.assertGreater(identity_revision(case), r1)
        events = {a.get("event") for a in case.audit}
        self.assertIn("document_identity_revision", events)

    def test_K_L_draft_mismatch_blocks(self):
        auth = {"pcn_number": "1234567890", "vrm": "AB12CDE"}
        letter_bad_pcn = "I appeal PCN 1234567891 for vehicle AB12CDE."
        issues = draft_identity_mismatches(letter_bad_pcn, auth, required={"pcn_number", "vrm"})
        self.assertTrue(any(i["field"] == "pcn_number" and i["result"] == "MISMATCH"
                            for i in issues))
        letter_bad_vrm = "I appeal PCN 1234567890 for vehicle AB12CDF."
        issues2 = draft_identity_mismatches(letter_bad_vrm, auth, required={"pcn_number", "vrm"})
        self.assertTrue(any(i["field"] == "vrm" for i in issues2))

    def test_K_validation_engine_rule(self):
        pack = RetrievalPack(
            primary_route=None, secondary_routes=[], module_ids=["KB-POFA-02"],
            verified_facts={"pcn_number": "1234567890", "vrm": "AB12CDE"},
            fact_refs={"pcn_number": "F1", "vrm": "F2"},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=["POFA_POSTAL_LATE"],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
            case_context={
                "authoritative_identity": {
                    "pcn_number": "1234567890", "vrm": "AB12CDE",
                },
                "identity_revision": "1",
                "identity_required_in_draft": ["pcn_number", "vrm"],
            },
        )
        draft = Draft("c1", [[DraftSentence(
            "I appeal PCN 1234567899 for vehicle AB12CDE.",
            ["F1", "F2"], ["KB-POFA-02"], [],
        )]])
        result = ValidationEngine().validate(draft, pack)
        rules = [i.rule for i in result.issues]
        self.assertIn("VAL-CRITICAL-DOCUMENT-IDENTITY", rules)

    def test_auto_confirm_skips_unverified_critical(self):
        from pcn_appeal.orchestrator import AppealPipeline
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        # Force uncertain identity on vrm
        case.set_status("vrm", FactStatus.EXTRACTED, reason="test")
        from pcn_appeal.document_identity import DocumentIdentityState, FieldRecord
        st = DocumentIdentityState(
            vrm=FieldRecord("vrm", status=STATUS_UNCERTAIN, canonical_value="AB12CDE"),
            pcn_number=FieldRecord("pcn_number", status=STATUS_VERIFIED,
                                   canonical_value="1234567890"),
            field_status={"vrm": STATUS_UNCERTAIN, "pcn_number": STATUS_VERIFIED},
            identity_revision=1,
            complete=False,
        )
        attach_identity_state(case, st)
        names = AppealPipeline._auto_confirmable(case)
        self.assertNotIn("vrm", names)
        self.assertIn("pcn_number", names)

    def test_identity_questions_and_confirm_clear_block(self):
        from pcn_appeal.document_identity import (
            DocumentIdentityState, FieldRecord, confirm_identity_field,
            identity_blocks_claim_plan, identity_customer_questions,
        )
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        st = DocumentIdentityState(
            vrm=FieldRecord("vrm", status=STATUS_UNCERTAIN, canonical_value="AB12CDE"),
            pcn_number=FieldRecord("pcn_number", status=STATUS_VERIFIED,
                                   canonical_value="1234567890"),
            field_status={"vrm": STATUS_UNCERTAIN, "pcn_number": STATUS_VERIFIED},
            identity_revision=1,
            complete=False,
        )
        attach_identity_state(case, st)
        self.assertTrue(identity_blocks_claim_plan(case))
        qs = identity_customer_questions(case)
        self.assertTrue(qs)
        self.assertEqual(qs[0]["fact"], "vrm")
        self.assertTrue(confirm_identity_field(case, "vrm", "AB12CDE"))
        self.assertIsNone(identity_blocks_claim_plan(case))
        self.assertEqual(case.get("vrm"), "AB12CDE")

    def test_pair_conflict_requires_an_upload_instead_of_a_text_answer(self):
        from pcn_appeal.document_identity import (
            DocumentIdentityState, identity_customer_questions,
        )
        case = _case_with_identity(vrm="AB12CDE", pcn_number="1234567890")
        st = DocumentIdentityState(
            document_pair_conflict=True,
            field_status={"vrm": STATUS_VERIFIED, "pcn_number": STATUS_VERIFIED},
            identity_revision=1,
            complete=False,
        )
        attach_identity_state(case, st)
        qs = identity_customer_questions(case)
        self.assertEqual(qs, [])
        self.assertEqual(identity_blocks_claim_plan(case), "DOCUMENT_PAIR_CONFLICT")


class NoOperatorSpecificTests(unittest.TestCase):
    def test_module_has_no_named_operators(self):
        import pathlib
        text = pathlib.Path("pcn_appeal/document_identity.py").read_text(encoding="utf-8")
        for banned in ("Euro Car Parks", "CP Plus", "ParkingEye", "Canada Water",
                       "EX15CZT", "00347261120013"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
