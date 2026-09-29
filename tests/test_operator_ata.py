"""operator_ata normalisation: full trade-body names -> BPA | IPC | NOT_SHOWN."""
from __future__ import annotations

import unittest

from pcn_appeal.engines.extraction import ExtractionEngine, normalise_operator_ata
from pcn_appeal.engines.questioning import QuestionEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind


class NormaliseOperatorAta(unittest.TestCase):
    def test_short_codes(self):
        self.assertEqual(normalise_operator_ata("IPC"), "IPC")
        self.assertEqual(normalise_operator_ata("bpa"), "BPA")
        self.assertEqual(normalise_operator_ata("NOT_SHOWN"), "NOT_SHOWN")

    def test_full_ipc_name(self):
        self.assertEqual(
            normalise_operator_ata("International Parking Community (IPC)"), "IPC")
        self.assertEqual(
            normalise_operator_ata("International Parking Community"), "IPC")

    def test_full_bpa_name(self):
        self.assertEqual(
            normalise_operator_ata("British Parking Association (BPA)"), "BPA")

    def test_unknown_returns_none(self):
        self.assertIsNone(normalise_operator_ata("Some Other Body"))


class AnswerAcceptsFullAtaName(unittest.TestCase):
    def test_record_answer_maps_ipc_full_name(self):
        kg = KnowledgeGraph()
        case = CaseFile("C-ATA")
        case.pending_questions = [{
            "fact": "operator_ata",
            "text": "Which trade association?",
            "type": "choice",
            "options": ["BPA", "IPC", "NOT_SHOWN"],
        }]
        QuestionEngine(kg).record_answer(
            case, "operator_ata", "International Parking Community (IPC)")
        self.assertEqual(case.get("operator_ata"), "IPC")

    def test_extraction_normalises_ata_field(self):
        llm = FakeLLM({"extraction": [{
            "fields": {
                "operator_name": {"value": "Euro Car Parks", "confidence": 0.97,
                                  "evidence_id": "E1", "page": 1},
                "pcn_number": {"value": "1234567890", "confidence": 0.97,
                               "evidence_id": "E1", "page": 1},
                "vrm": {"value": "AB12CDE", "confidence": 0.97,
                        "evidence_id": "E1", "page": 1},
                "parking_event_date": {"value": "01/06/2026", "confidence": 0.97,
                                       "evidence_id": "E1", "page": 1},
                "notice_issue_date": {"value": "05/06/2026", "confidence": 0.97,
                                      "evidence_id": "E1", "page": 1},
                "charge_amount": {"value": "£100", "confidence": 0.97,
                                  "evidence_id": "E1", "page": 1},
                "alleged_breach": {"value": "Overstay", "confidence": 0.97,
                                   "evidence_id": "E1", "page": 1},
                "operator_ata": {
                    "value": "International Parking Community (IPC)",
                    "confidence": 0.97, "evidence_id": "E1", "page": 1,
                },
            },
            "doc_types": {"E1": "PCN"},
        }]})
        from pcn_appeal.models import EvidenceItem
        case = CaseFile("C-X", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="IPC logo"),
        })
        ExtractionEngine(llm).run(case)
        self.assertEqual(case.get("operator_ata"), "IPC")


if __name__ == "__main__":
    unittest.main()
