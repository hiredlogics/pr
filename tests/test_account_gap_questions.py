"""Empty/thin narrative must still ask material KB-gated account questions."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.engines.analysis import AnalysisEngine, CaseAnalysis
from pcn_appeal.engines.question_materiality import annotate
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind


def _notice_case() -> CaseFile:
    case = CaseFile("gap-q")
    for name, value in {
        "operator_name": "Northbridge Parking Ltd",
        "pcn_number": "NB900200",
        "vrm": "XY12ZAB",
        "parking_location": "Northbridge Retail",
        "site_postcode": "LS1 1AA",
        "parking_event_date": "01/06/2026",
        "notice_issue_date": "20/06/2026",
        "alleged_breach": "Overstayed paid time",
        "operator_ata": "BPA",
        "entry_time": "10:00",
        "exit_time": "14:30",
        "jurisdiction": "ENGLAND_WALES",
    }.items():
        case.put(Fact(
            f"F-{name}", name, value, FactStatus.CONFIRMED,
            FactSource(SourceKind.DOCUMENT, "E1"),
        ))
    # Module resolver says ANPR is an unresolved candidate (missing multiple_visits).
    case.raw_answers["_module_resolve"] = json.dumps({
        "candidates": ["KB-POFA-02", "KB-ANPR-01", "KB-PAY-01"],
        "eligible_ids": ["KB-POFA-02"],
        "unresolved_ids": ["KB-ANPR-01", "KB-PAY-01"],
        "rows": {},
    })
    return case


class AccountGapQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = KnowledgeGraph()

    def test_unlocking_asks_multiple_visits_and_payment(self):
        eng = AnalysisEngine(self.kg, FakeLLM({}))
        case = _notice_case()
        result = CaseAnalysis()
        result.module_ids = ["KB-POFA-02"]
        result.candidate_ids = ["KB-POFA-02", "KB-ANPR-01", "KB-PAY-01"]
        qs = eng._unlocking_questions(case, result, case.fact_view())
        facts = {q["fact"] for q in qs}
        self.assertIn("multiple_visits", facts)
        self.assertIn("payment_made", facts)
        for q in qs:
            self.assertTrue(q.get("kb_gated"))
            self.assertTrue(q.get("unlocks"))

    def test_materiality_keeps_kb_gated_despite_pofa_supported(self):
        case = _notice_case()
        q = {
            "fact": "multiple_visits",
            "text": "Did the vehicle visit the site more than once that day?",
            "type": "bool",
            "kb_gated": True,
            "unlocks": ["KB-ANPR-01"],
            "related_module": "KB-ANPR-01",
        }
        rec = annotate(case, self.kg, q)
        self.assertTrue(rec["ask"], rec)


if __name__ == "__main__":
    unittest.main()
