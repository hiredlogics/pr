"""Phase 3A: knowledge retrieval finds candidate modules; it decides nothing.

The fixtures start after Phases 1-2 (authoritative facts, notice context, a
semantic packet) and carry no customer prose. The expected modules are written
in pcn_appeal/eval/retrieval/fixtures.py before any retrieval ran.

Run:  PYTHONPATH=.:tests python -m unittest discover -s tests -p test_knowledge_retrieval.py -v
"""
from __future__ import annotations

import copy
import inspect
import json
import re
import unittest

from pcn_appeal.engines import knowledge_retrieval as KR
from pcn_appeal.engines.knowledge_retrieval import KnowledgeRetrieval, build_input
from pcn_appeal.eval.retrieval import fixtures as FX
from pcn_appeal.eval.retrieval import harness as H
from pcn_appeal.semantics import understanding as U


ENGINE = KnowledgeRetrieval(H.kg())


def _run(fx, customer=True):
    case = H.build_case(fx, customer=customer)
    return case, ENGINE.retrieve(build_input(H.kg(), case))


def ids(fid, customer=True):
    return set(_run(FX.BY_ID[fid], customer)[1].module_ids)


class TheFixtureMatrix(unittest.TestCase):
    def test_every_fixture_meets_its_pre_registered_expectation(self):
        rep = H.run_and_report("new")
        bad = [r for r in rep["rows"] if not r["pass"]]
        self.assertEqual([], [(r["id"], r["missed"], r["must_not_hit"], r["zero_violation"],
                               r["leak"]) for r in bad])

    def test_there_are_at_least_25_fixtures_across_the_required_situations(self):
        self.assertGreaterEqual(len(FX.FIXTURES), 25)
        tags = {t for f in FX.FIXTURES for t in f["tags"]}
        for need in ("movement", "payment", "keying", "mechanical", "authorisation", "access",
                     "notice", "anpr", "evidence", "signage", "negation", "uncertainty", "noise",
                     "unknown_material", "relationship", "blocked", "independent", "zero",
                     "breadth", "vector_false_positive", "role", "legal_conclusion", "context",
                     "cap"):
            self.assertIn(need, tags, need)

    def test_equivalent_meaning_gives_the_same_candidates(self):
        a, b, c = ids("F01"), ids("F02"), ids("F27")
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertIn("KB-ANPR-01", a)


class StructuredSignalsDominate(unittest.TestCase):
    def test_a_vector_hit_never_outranks_or_displaces_a_structured_one(self):
        _, res = _run(FX.BY_ID["F28"])
        rows = res.candidates
        scores = [c.structured_score for c in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        vec_only = [c for c in rows if c.retrieval_sources == [KR.VECTOR]]
        self.assertTrue(vec_only, "the fixture is meant to exercise the vector route")
        self.assertTrue(all(c.structured_score == 0.0 for c in vec_only))
        last_structured = max(i for i, c in enumerate(rows) if c.structured_score > 0)
        first_vector = min(i for i, c in enumerate(rows) if c.structured_score == 0)
        self.assertGreater(first_vector, last_structured)
        self.assertTrue({"KB-BREAK-01", "KB-BREAK-02"} <= set(res.module_ids))

    def test_the_cap_removes_vector_candidates_before_structured_ones(self):
        eng = KnowledgeRetrieval(H.kg())
        old = (KR.CANDIDATE_LIMIT, KR.VECTOR_ONLY_MAX)
        try:
            KR.CANDIDATE_LIMIT, KR.VECTOR_ONLY_MAX = 5, 4
            case = H.build_case(FX.BY_ID["F28"])
            res = eng.retrieve(build_input(H.kg(), case))
        finally:
            KR.CANDIDATE_LIMIT, KR.VECTOR_ONLY_MAX = old
        self.assertLessEqual(len(res.candidates), 5)
        structured = [c for c in res.candidates if c.structured_score > 0]
        self.assertGreaterEqual(len(structured), 4)       # BREAK-01/02/03 and POFA-01 survive
        self.assertTrue({"KB-BREAK-01", "KB-BREAK-02", "KB-BREAK-03"} <= set(res.module_ids))

    def test_a_vector_hit_is_never_marked_as_anything_but_a_candidate(self):
        _, res = _run(FX.BY_ID["F17"])
        c = next(c for c in res.candidates if c.module_id == "KB-HOSP-02")
        self.assertEqual([KR.VECTOR], c.retrieval_sources)
        self.assertEqual(0.0, c.structured_score)
        self.assertIn("not a ground", c.candidate_reason)
        for forbidden in ("status", "supported", "eligible", "blocked", "rejected"):
            self.assertFalse(hasattr(c, forbidden), forbidden)

    def test_zero_candidates_is_valid_and_nothing_is_forced_in(self):
        for fid in ("F16", "F17b", "F21"):
            self.assertLessEqual(ids(fid), {"KB-POFA-01"}, fid)


class PolarityAndUncertainty(unittest.TestCase):
    def test_a_negated_concept_does_not_retrieve_the_module_that_needs_it_present(self):
        got = ids("F14")
        self.assertFalse(got & {"KB-PAY-01", "KB-KEY-01", "KB-KEY-02"})
        self.assertTrue({"KB-CON-01", "KB-CON-02"} <= got)    # whose gates speak of its absence

    def test_affirmed_and_negated_payment_retrieve_different_modules(self):
        self.assertIn("KB-PAY-01", ids("F04"))
        self.assertNotIn("KB-PAY-01", ids("F14"))

    def test_an_uncertain_concept_is_retrieved_marked_uncertain_and_never_a_fact(self):
        case, res = _run(FX.BY_ID["F15"])
        row = {c.module_id: c for c in res.candidates}
        self.assertTrue(row["KB-PAY-01"].uncertain)
        self.assertLess(row["KB-PAY-01"].structured_score, 0.85)    # half weight, not affirmed
        self.assertNotIn("payment_made", case.fact_view())
        self.assertEqual(["PAYMENT_MADE"], res.diagnostics["uncertain_concepts"])
        _, affirmed = _run(FX.BY_ID["F04"])
        self.assertFalse({c.module_id: c for c in affirmed.candidates}["KB-PAY-01"].uncertain)

    def test_retrieval_writes_no_fact(self):
        case = H.build_case(FX.BY_ID["F06"])
        before = case.fact_view()
        KnowledgeRetrieval(H.kg()).retrieve(build_input(H.kg(), case))
        self.assertEqual(before, case.fact_view())


class Relationships(unittest.TestCase):
    def test_the_order_of_the_same_events_changes_the_candidates(self):
        self.assertIn("KB-ANPR-01", ids("F18a"))
        self.assertNotIn("KB-ANPR-01", ids("F18b"))

    def test_a_contradiction_between_the_events_removes_the_match(self):
        self.assertNotIn("KB-ANPR-01", ids("F18e"))

    def test_the_relationship_route_is_reported_as_such(self):
        _, res = _run(FX.BY_ID["F18a"])
        c = {c.module_id: c for c in res.candidates}["KB-ANPR-01"]
        self.assertIn(KR.RELATIONSHIP, c.retrieval_sources)
        self.assertTrue(c.matched_relationship_ids)

    def test_a_cause_relationship_reaches_the_breakdown_modules(self):
        self.assertTrue({"KB-BREAK-01", "KB-BREAK-02"} <= ids("F18d"))


class TheCustomerStreamGate(unittest.TestCase):
    def test_a_blocked_stream_contributes_no_customer_derived_candidate(self):
        for state in FX.STREAM_STATES[1:]:
            fx = FX.BY_ID[f"F19-{state}"]
            with_customer, without = ids(fx["id"]), ids(fx["id"], customer=False)
            self.assertEqual(without, with_customer, state)

    def test_the_independent_notice_stream_survives_a_blocked_customer_stream(self):
        for state in FX.STREAM_STATES[1:]:
            got = ids(f"F19-{state}")
            self.assertTrue({"KB-POFA-02", "KB-ANPR-01", "KB-TIME-01"} <= got, state)
            self.assertFalse(got & {"KB-ACT-02", "KB-BREAK-01"}, state)

    def test_the_same_account_when_ready_adds_its_candidates(self):
        got = ids("F19-READY")
        self.assertTrue({"KB-ACT-02", "KB-BREAK-01", "KB-POFA-02"} <= got)

    def test_availability_is_recomputed_never_read_from_a_stored_flag(self):
        fx = FX.BY_ID["F19-NEEDS_CLARIFICATION"]
        case = H.build_case(fx)
        packet = U.load_packet(case)
        packet["customer_semantics_ready"] = True          # a stale / forged flag
        packet["ready_for_knowledge"] = True
        U._save(case, {**U._state(case), "packet": packet})
        inp = build_input(H.kg(), case)
        self.assertFalse(inp.customer_semantics["available"])
        self.assertEqual([], inp.customer_semantics["concepts"])

    def test_nothing_independent_and_a_blocked_stream_is_nothing_customer_derived(self):
        for state in FX.STREAM_STATES[1:]:
            self.assertLessEqual(ids(f"F20-{state}"), {"KB-POFA-01"}, state)


class NoRawCustomerText(unittest.TestCase):
    MARK = "zq-raw-customer-sentence-marker"

    def test_the_input_never_carries_source_text_or_free_text(self):
        fx = copy.deepcopy(FX.BY_ID["F19-READY"])
        for ch in ("concepts", "events", "narrative_atoms"):
            for row in fx["packet"][ch]:
                row["source_text"] = self.MARK
        case = H.build_case(fx)
        case.raw_answers["customer_narrative"] = self.MARK
        case.raw_answers["_clar_1"] = self.MARK
        inp = build_input(H.kg(), case)
        blob = json.dumps({"s": inp.customer_semantics, "n": inp.notice_context,
                           "f": inp.fact_view}, default=str)
        self.assertNotIn(self.MARK, blob)
        for ch in ("concepts", "events", "narrative_atoms"):
            for row in inp.customer_semantics[ch]:
                self.assertNotIn("source_text", row)

    def test_changing_the_raw_text_changes_nothing(self):
        fx = FX.BY_ID["F19-READY"]
        a = H.build_case(fx)
        b = H.build_case(fx)
        b.raw_answers["customer_narrative"] = "something entirely different " * 20
        eng = KnowledgeRetrieval(H.kg())
        ra, rb = (eng.retrieve(build_input(H.kg(), c)) for c in (a, b))
        self.assertEqual([c.as_dict() for c in ra.candidates], [c.as_dict() for c in rb.candidates])

    def test_the_analysis_window_does_not_depend_on_the_circumstances_text(self):
        from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
        case = H.build_case(FX.BY_ID["F19-READY"])
        facts = case.fact_view()
        match = KnowledgeMatcher(H.kg()).match(case, facts)
        a = [m.module_id for m in H.engine()._candidates(case, "", facts, match)]
        b = [m.module_id for m in H.engine()._candidates(
            case, "a long unrelated passage about hospitals and permits and barriers", facts, match)]
        self.assertEqual(a, b)


class RolesAndCandidateContract(unittest.TestCase):
    def test_module_roles_are_carried_unchanged(self):
        for fid in ("F24", "F25", "F12", "F13", "F19-READY"):
            _, res = _run(FX.BY_ID[fid])
            for c in res.candidates:
                self.assertEqual(H.kg().modules[c.module_id].module_role, c.module_role)

    def test_support_and_legal_conclusion_modules_are_retrieved_without_promotion(self):
        _, res = _run(FX.BY_ID["F25"])
        roles = {c.module_id: c.module_role for c in res.candidates}
        self.assertEqual("LEGAL_CONCLUSION", roles["KB-POFA-05"])
        _, res = _run(FX.BY_ID["F24"])
        self.assertEqual("SUPPORTING_PROPOSITION",
                         {c.module_id: c.module_role for c in res.candidates}["KB-LAND-02"])

    def test_the_result_has_the_documented_shape(self):
        _, res = _run(FX.BY_ID["F19-READY"])
        d = res.as_dict()
        self.assertEqual(H.kg().release_id, d["kb_release_id"])
        want = {"module_id", "module_role", "retrieval_sources", "matched_fact_keys",
                "matched_concept_ids", "matched_event_ids", "matched_atom_ids",
                "matched_relationship_ids", "structured_score", "vector_score",
                "candidate_reason"}
        for c in d["candidates"]:
            self.assertTrue(want <= set(c))

    def test_the_candidate_set_is_observable_on_the_case(self):
        from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
        case = H.build_case(FX.BY_ID["F19-READY"])
        facts = case.fact_view()
        window = H.engine()._candidates(case, "", facts, KnowledgeMatcher(H.kg()).match(case, facts))
        stored = json.loads(case.raw_answers["_knowledge_retrieval"])
        self.assertTrue(stored["candidates"])
        self.assertEqual([m.module_id for m in window],
                         json.loads(case.raw_answers["_knowledge_retrieval_window"]))
        self.assertTrue(any(e.get("event") == "knowledge_retrieval" for e in case.audit))


class NoRulesAboutPeopleOrPlaces(unittest.TestCase):
    def test_the_retrieval_source_names_no_module_operator_site_or_phrase(self):
        src = inspect.getsource(KR)
        self.assertNotIn("KB-", re.sub(r'"""[\s\S]*?"""', "", src))
        for word in ("Northgate", "Euro Car", "ParkingEye", "Horizon", "Tesco", "hospital",
                     "ambulance", "barrier", "permit"):
            self.assertNotIn(word.lower(), re.sub(r'"""[\s\S]*?"""', "", src).lower(), word)

    def test_every_route_is_a_named_type(self):
        for route in ("FACT_MATCH", "LEGAL_FINDING", "SEMANTIC_CONCEPT", "RELATIONSHIP",
                      "SEMANTIC_EVENT", "NARRATIVE_ATOM", "ALLEGATION", "EVIDENCE_METHOD",
                      "VECTOR"):
            self.assertIn(route, KR.WEIGHT | {"VECTOR": 0})


if __name__ == "__main__":
    unittest.main()
