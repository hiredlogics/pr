"""Store layer + one-click appeal.

These run with no database. What needs a live Postgres (init/sync/round-trip
through real tables) is covered by tests/test_pg_integration.py, which skips
itself unless DATABASE_URL points at a pgvector-enabled server.
"""
import unittest
from pathlib import Path

import numpy as np
import yaml

from pcn_appeal.kg.graph import DATA, KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import CaseFile, CaseState, EvidenceItem, FactStatus
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.rag.embedder import HashingEmbedder

BASE = dict(operator_name="Acme Parking Ltd", pcn_number="PCN123456", vrm="AB12 CDE",
            parking_location="Retail Park", site_postcode="M1 1AA", parking_event_date="01/06/2026",
            notice_issue_date="05/06/2026", charge_amount="£100", operator_ata="BPA")


def fields(**kw):
    return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1} for k, v in kw.items()}


def make_pipe(alleged_breach, extra=None, evidence=None, doc_types=None):
    f = dict(BASE, alleged_breach=alleged_breach, **(extra or {}))
    ev = {"E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice")}
    ev.update(evidence or {})
    llm = FakeLLM({"extraction": [{"fields": fields(**f),
                                   "doc_types": {"E1": "PCN", **(doc_types or {})}}]})
    return CaseFile("C-1", evidence=ev), AppealPipeline(llm)


def release_from_yaml(data_dir: Path = DATA) -> dict:
    """Build the dict shape store.kb_source.load_release returns, straight from
    the YAML. Lets us prove the Postgres-served graph is behaviour-identical
    without standing up a database."""
    kb = yaml.safe_load((data_dir / "kb_modules.yaml").read_text())
    bb = yaml.safe_load((data_dir / "building_blocks.yaml").read_text())
    rt = yaml.safe_load((data_dir / "routes.yaml").read_text())
    qs = yaml.safe_load((data_dir / "questions.yaml").read_text())
    return {"kb_modules": kb, "building_blocks": bb, "routes": rt, "questions": qs,
            "release_id": "kb-test"}


class ReleaseRoundTrip(unittest.TestCase):
    """'YAML authors, Postgres serves' is only safe if both produce the same graph."""

    def test_graph_from_release_matches_graph_from_yaml(self):
        yaml_kg = KnowledgeGraph()
        pg_kg = KnowledgeGraph.from_release(release_from_yaml())

        self.assertEqual(sorted(yaml_kg.modules), sorted(pg_kg.modules))
        self.assertEqual(sorted(yaml_kg.blocks), sorted(pg_kg.blocks))
        self.assertEqual(yaml_kg.routes, pg_kg.routes)
        self.assertEqual(yaml_kg.questions, pg_kg.questions)
        self.assertEqual(pg_kg.release_id, "kb-test")
        self.assertIsNone(yaml_kg.release_id)

        for mid, m in yaml_kg.modules.items():
            other = pg_kg.modules[mid]
            # the predicates are the decision surface - they must survive verbatim
            self.assertEqual(m.use_when, other.use_when, mid)
            self.assertEqual(m.do_not_use_when, other.do_not_use_when, mid)
            self.assertEqual(m.strength, other.strength, mid)
            self.assertEqual(m.route, other.route, mid)
            self.assertEqual(yaml_kg.gating_facts(mid), pg_kg.gating_facts(mid), mid)
            self.assertEqual(yaml_kg.conflicts(mid), pg_kg.conflicts(mid), mid)

    def test_same_letter_either_way(self):
        case_a, pipe_a = make_pipe("Overstayed paid time")
        out_a = pipe_a.auto_appeal(case_a, "nothing relevant")

        case_b, pipe_b = make_pipe("Overstayed paid time")
        pipe_b.kg = KnowledgeGraph.from_release(release_from_yaml())
        pipe_b.questions.kg = pipe_b.kg
        pipe_b.reasoning.kg = pipe_b.kg
        out_b = pipe_b.auto_appeal(case_b, "nothing relevant")

        self.assertEqual(out_a.state, out_b.state)
        self.assertEqual([q["fact"] for q in out_a.questions], [q["fact"] for q in out_b.questions])


class Embeddings(unittest.TestCase):
    def test_deterministic_unit_vectors_of_declared_dim(self):
        emb = HashingEmbedder(dim=1024)
        a = emb(["keeper liability is not automatic"])
        b = emb(["keeper liability is not automatic"])
        self.assertEqual(a.shape, (1, 1024))
        np.testing.assert_array_equal(a, b)                       # sync must be reproducible
        self.assertAlmostEqual(float(np.linalg.norm(a[0])), 1.0, places=5)

    def test_related_text_scores_above_unrelated(self):
        emb = HashingEmbedder(dim=1024)
        v = emb(["the notice to keeper was delivered late by post",
                 "notice to keeper delivered late",
                 "the vehicle had a flat battery and could not move"])
        self.assertGreater(float(v[0] @ v[1]), float(v[0] @ v[2]))

    def test_empty_text_does_not_divide_by_zero(self):
        v = HashingEmbedder(dim=64)([""])
        self.assertEqual(v.shape, (1, 64))
        self.assertEqual(float(np.linalg.norm(v[0])), 0.0)


class OneClickAppeal(unittest.TestCase):
    def test_pauses_only_for_a_ground_that_would_change_the_letter(self):
        case, pipe = make_pipe("Overstayed paid time",
                               evidence={"E2": EvidenceItem("E2", "RECOVERY_REPORT", "rac.pdf",
                                                            text="RAC job")},
                               doc_types={"E2": "RECOVERY_REPORT"})
        result = pipe.auto_appeal(case, "the car broke down, battery died, RAC attended")

        self.assertIsNone(result.output)                      # paused, not finished
        self.assertTrue(result.questions)
        asked = {q["fact"] for q in result.questions}
        self.assertIn("vehicle_immobilised", asked)           # gates BREAKDOWN (strength >= 50)
        for q in result.questions:
            self.assertNotRegex(q["text"].lower(), r"driv(er|ing)|who (drove|parked)")

    def test_finishes_in_one_call_when_nothing_gates_a_ground(self):
        case, pipe = make_pipe("Vehicle in restricted zone")
        result = pipe.auto_appeal(case, "nothing relevant here")

        self.assertEqual(result.questions, [])
        self.assertEqual(result.state, CaseState.RELEASED)
        self.assertTrue(result.output.letter)

    def test_answering_the_paused_questions_completes_the_appeal(self):
        case, pipe = make_pipe("Overstayed paid time",
                               evidence={"E2": EvidenceItem("E2", "RECOVERY_REPORT", "rac.pdf",
                                                            text="RAC job")},
                               doc_types={"E2": "RECOVERY_REPORT"})
        first = pipe.auto_appeal(case, "the car broke down, battery died, RAC attended")
        answers = {"vehicle_immobilised": "yes", "immobilisation_prevented_departure": "yes",
                   "recovery_attended": "yes", "immobilisation_cause": "flat battery",
                   "payment_made": "no", "payment_method": "OTHER",
                   "permitted_period_ended": "yes", "exit_delay_min": 20}
        asked = {q["fact"] for q in first.questions}
        second = pipe.auto_appeal(case, answers={k: v for k, v in answers.items() if k in asked})

        self.assertEqual(second.state, CaseState.RELEASED)
        self.assertEqual(second.output.pack.primary_route, "BREAKDOWN")
        self.assertIn("RECOVERY_REPORT", " ".join(second.output.evidence_list))

    def test_uncertain_facts_are_never_auto_confirmed(self):
        f = fields(**dict(BASE, alleged_breach="Overstayed paid time"))
        f["notice_issue_date"]["confidence"] = 0.4              # below EX-02 threshold
        llm = FakeLLM({"extraction": [{"fields": f, "doc_types": {"E1": "PCN"}}]})
        case = CaseFile("C-2", evidence={"E1": EvidenceItem("E1", "PCN", "pcn.pdf")})
        AppealPipeline(llm).auto_appeal(case, "nothing relevant")

        self.assertEqual(case.facts["notice_issue_date"].status, FactStatus.UNCERTAIN)
        confirmed = {n for n, x in case.facts.items() if x.status == FactStatus.CONFIRMED}
        self.assertNotIn("notice_issue_date", confirmed)
        self.assertIn("operator_name", confirmed)               # the confident ones still promote


if __name__ == "__main__":
    unittest.main()
