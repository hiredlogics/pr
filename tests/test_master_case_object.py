"""P8.1: the Master Case Object is the one authoritative case state.

Every section a stage works from - the notice, the customer's account, derived
values and where they came from, the legal findings, the knowledge matches, the
grounds and the claim plan - is read from one object bound to the case, and
serializes and reloads as the case the pipeline was holding.

A Sainsbury's-shaped case (a late postal Notice to Keeper, with a narrative that
adds a second ground) is the regression example. Nothing here is specific to an
operator or a notice.
"""
from __future__ import annotations

import unittest

import sqlite_store
from test_scenarios import make_case, run
from test_verified_legal_findings import LATE

from pcn_appeal import case_state
from pcn_appeal.case_state import (GroundsAuthorityError, MasterCase, MasterCaseStateError,
                                   SECTIONS)
from pcn_appeal.models import (CaseFile, EvidenceItem, Fact, FactSource, FactStatus,
                               SourceKind)
from pcn_appeal.store import cases as store

SAINSBURYS = dict(LATE, entry_time="10:00", exit_time="13:27",
                  parking_location="Sainsburys - Cromwell Road")


def released_case():
    """A case taken through the pipeline, with a locked claim plan."""
    case, pipe = make_case(SAINSBURYS)
    run(case, pipe, "", {})
    pipe.answer(case, {"multiple_visits": True})
    pipe.generate(case)
    return case, pipe


# ------------------------------------------------------------------ sections
class EverySectionIsPresent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.case, _ = released_case()
        cls.master = cls.case.master

    def test_the_case_exposes_one_master_object(self):
        self.assertIsInstance(self.case.master, MasterCase)
        self.assertIs(self.case.master, self.case.master)
        self.assertIs(self.case.master.case, self.case)

    def test_the_seven_sections_are_serialized(self):
        state = self.master.to_dict()
        for section in SECTIONS:
            self.assertIn(section, state, section)

    def test_notice_facts_carry_the_notice_and_its_evidence(self):
        notice = self.master.notice_facts
        self.assertEqual(notice["operator"]["operator_name"]["value"], "Acme Parking Ltd")
        self.assertEqual(notice["pcn"]["pcn_number"]["value"], "PCN123456")
        self.assertIn("vrm", notice["vrm"])
        self.assertIn("notice_issue_date", notice["dates"])
        self.assertIn("parking_location", notice["location"])
        self.assertIn("alleged_breach", notice["allegation"])
        self.assertEqual([e["evidence_id"] for e in notice["evidence_references"]], ["E1"])
        # Every value names the fact node it came from, so a letter can cite it.
        self.assertTrue(notice["operator"]["operator_name"]["fact_id"])

    def test_customer_facts_separate_stated_confirmed_and_inferred(self):
        customer = self.master.customer_facts
        for key in ("stated", "confirmed", "inferred", "hypotheses", "provenance"):
            self.assertIn(key, customer)
        self.assertIn("multiple_visits", customer["confirmed"])
        # Working values the engines keep under "_" are not customer statements.
        self.assertFalse([q for q in customer["stated"] if q.startswith("_")])

    def test_legal_findings_carry_calculation_rule_evidence_and_status(self):
        by_type = {r["finding_type"]: r for r in self.master.legal_findings}
        late = by_type["POFA_POSTAL_LATE"]
        self.assertEqual(late["status"], "VERIFIED")
        self.assertIn("deadline", late["calculation"])
        self.assertTrue(late["rule"])
        self.assertIn("notice_issue_date", late["evidence"])
        self.assertEqual(late["confidence"], 1.0)
        self.assertIn("POFA_POSTAL_LATE", self.master.verified_findings())

    def test_knowledge_matches_carry_the_relationship_and_its_reason(self):
        matches = {m.module_id: m for m in self.master.knowledge_matches}
        self.assertTrue(matches, "the knowledge matcher recorded nothing")
        for m in matches.values():
            self.assertTrue(m.relationship)
            self.assertTrue(m.reason or m.relationship == "OPEN")
        self.assertEqual(matches["KB-POFA-02"].relationship, "SUPPORTED")

    def test_one_module_is_recorded_once_per_conclusion(self):
        before = len(self.case.master_knowledge_matches)
        self.case.master.record_knowledge_matches(
            type("M", (), {"candidates": {}})())
        self.assertEqual(len(self.case.master_knowledge_matches), before)


# ------------------------------------------------------------------- lineage
class DerivedFactsKeepTheirLineage(unittest.TestCase):

    def test_a_derived_fact_names_the_facts_it_was_derived_from(self):
        case, _ = released_case()
        derived = case.master.derived_facts
        self.assertIn("pofa_findings", derived)
        row = derived["pofa_findings"]
        self.assertIn("notice_issue_date", row["source_facts"])
        self.assertIn("parking_event_date", row["source_facts"])
        self.assertEqual(row["rule"], "pofa.assess")
        # derived_from is fact ids: the nodes, which survive a rebuild.
        self.assertIn(case.facts.node_id("notice_issue_date"), row["derived_from"])

    def test_a_calculator_declares_its_inputs(self):
        case = CaseFile(case_id="C-LINEAGE")
        case.put(Fact("F-a", "parking_event_date", "2026-06-01", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case.put(Fact("F-b", "notice_issue_date", "2026-07-20", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
        with case_state.derives(case, "parking_event_date", "notice_issue_date",
                                rule="demo.timing"):
            case.put(Fact("F-c", "days_late", 35, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "demo.timing")))
        row = case.master.derived_facts["days_late"]
        self.assertEqual(row["source_facts"], ["parking_event_date", "notice_issue_date"])
        self.assertEqual(row["derived_from"],
                         [case.facts.node_id("parking_event_date"),
                          case.facts.node_id("notice_issue_date")])
        self.assertEqual(case.master.lineage_gaps(), [])

    def test_a_value_read_off_a_document_names_the_document(self):
        case = CaseFile(case_id="C-LINEAGE-DOC",
                        evidence={"E9": EvidenceItem("E9", "LEASE", "lease.pdf")})
        case.put(Fact("F-x", "lease_clauses", [{"clause_ref": "3.2"}], FactStatus.DERIVED,
                      FactSource(SourceKind.DOCUMENT, "E9#p1")))
        row = case.master.derived_facts["lease_clauses"]
        self.assertEqual(row["source_evidence"], ["E9"])
        self.assertEqual(case.master.lineage_gaps(), [])

    def test_an_undeclared_derivation_is_reported_not_invented(self):
        case = CaseFile(case_id="C-LINEAGE-GAP")
        case.put(Fact("F-y", "some_total", 3, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "nameless")))
        self.assertEqual(case.master.lineage_gaps(), ["some_total"])
        self.assertEqual(case.master.derived_facts["some_total"]["derived_from"], [])

    def test_lineage_is_additive(self):
        case = CaseFile(case_id="C-LINEAGE-ADD")
        case.put(Fact("F-a", "entry_time", "10:00", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
        case.put(Fact("F-b", "exit_time", "13:27", FactStatus.EXTRACTED,
                      FactSource(SourceKind.DOCUMENT, "E1#p1")))
        with case_state.derives(case, "entry_time", rule="duration.v1"):
            case.put(Fact("F-c", "stay_minutes", 100, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "duration")))
        with case_state.derives(case, "entry_time", "exit_time", rule="duration.v2"):
            case.put(Fact("F-c", "stay_minutes", 207, FactStatus.DERIVED,
                          FactSource(SourceKind.CALCULATION, "duration")))
        rules = [d.rule for d in case.master.derivations if d.fact == "stay_minutes"]
        self.assertEqual(rules, ["duration.v1", "duration.v2"])
        self.assertEqual(case.master.derived_facts["stay_minutes"]["rule"], "duration.v2")


# -------------------------------------------------------------------- grounds
class GroundsComeFromTheClaimPlan(unittest.TestCase):

    def test_a_ground_carries_origin_facts_evidence_findings_and_particulars(self):
        case, _ = released_case()
        grounds = {g.module_id: g for g in case.master.grounds}
        pofa = grounds["KB-POFA-02"]
        self.assertEqual(pofa.status, "SUPPORTED")
        self.assertIn(pofa.origin, ("SELECTED", "VERIFIED_FINDING", "CARRIED_FORWARD"))
        self.assertTrue(pofa.reason)
        self.assertTrue(pofa.supporting_facts)
        self.assertIn("POFA_POSTAL_LATE", pofa.findings)
        # A timed defect must be argued on its own calculation.
        particulars = {p["finding_type"]: p for p in pofa.required_particulars}
        self.assertIn("notice_issue_date", particulars["POFA_POSTAL_LATE"]["dates"])

    def test_the_regression_example_keeps_both_grounds(self):
        case, _ = released_case()
        argued = {g.module_id for g in case.master.argued_grounds}
        self.assertIn("KB-POFA-02", argued)
        self.assertIn("KB-ANPR-01", argued)
        self.assertEqual(argued, set(case.master.claim_plan.supported_ids))

    def test_nothing_but_a_claim_plan_may_decide_the_grounds(self):
        case, _ = released_case()
        with self.assertRaises(GroundsAuthorityError):
            case.master.record_grounds([{"module_id": "KB-ANPR-01", "status": "SUPPORTED"}])
        with self.assertRaises(GroundsAuthorityError):
            case.master.record_grounds(
                type("Plan", (), {"module_ids": ["KB-ANPR-01"]})())

    def test_a_draft_plan_decides_nothing(self):
        case, pipe = released_case()
        draft = pipe.claim_authority.build(case, version=99)
        self.assertEqual(draft.status, "DRAFT")
        with self.assertRaises(GroundsAuthorityError):
            case.master.record_grounds(draft)

    def test_a_plan_from_another_case_is_refused(self):
        case, _ = released_case()
        elsewhere = CaseFile(case_id="C-ELSEWHERE")
        with self.assertRaises(MasterCaseStateError):
            elsewhere.master.record_grounds(case.master.claim_plan)
        self.assertEqual(elsewhere.master.grounds, [])

    def test_recording_the_same_plan_twice_adds_nothing(self):
        case, _ = released_case()
        before = list(case.master_grounds)
        case.master.record_grounds(case.master.claim_plan)
        self.assertEqual(case.master_grounds, before)

    def test_grounds_of_an_earlier_plan_version_are_kept(self):
        case, pipe = released_case()
        first = case.master.claim_plan
        pipe.answer(case, {"vehicle_immobilised": "no"})
        pipe.generate(case)
        latest = case.master.claim_plan
        versions = {g.plan_version for g in case.master.ground_history}
        self.assertIn(first.version, versions)
        self.assertEqual(max(versions), latest.version)
        self.assertEqual({g.plan_version for g in case.master.grounds}, {latest.version})


# -------------------------------------------------------------- serialization
class SerializesAndReloads(unittest.TestCase):

    def setUp(self):
        self.db = sqlite_store.install(self)

    def saved_case(self) -> CaseFile:
        """A pipeline case persisted under a real store case id."""
        case, _ = released_case()
        stored = store.new_case()
        case.case_id = stored.case_id
        for plan in case.claim_plans:
            object.__setattr__(plan, "case_id", stored.case_id)
        store.save(case)
        return case

    def test_the_state_round_trips_through_the_store(self):
        case = self.saved_case()
        before = case.master.to_dict()
        loaded = store.load(case.case_id)
        after = loaded.master.to_dict()
        for section in ("derivations", "knowledge_matches", "grounds"):
            self.assertEqual(after[section], before[section], section)
        self.assertEqual(after["derived_facts"], before["derived_facts"])
        self.assertEqual(after["notice_facts"], before["notice_facts"])
        self.assertEqual(after["legal_findings"], before["legal_findings"])

    def test_the_digest_survives_a_reload(self):
        case = self.saved_case()
        self.assertEqual(store.load(case.case_id).master.digest(), case.master.digest())

    def test_the_grounds_come_back_as_the_plan_decided_them(self):
        case = self.saved_case()
        loaded = store.load(case.case_id)
        self.assertEqual([g.as_dict() for g in loaded.master.grounds],
                         [g.as_dict() for g in case.master.grounds])
        self.assertEqual(loaded.master.claim_plan.supported_ids,
                         case.master.claim_plan.supported_ids)

    def test_saving_twice_does_not_duplicate_the_state(self):
        case = self.saved_case()
        store.save(case)
        self.assertEqual(sqlite_store.count(self.db, "master_case_state"),
                         len(case.master_derivations) + len(case.master_knowledge_matches)
                         + len(case.master_grounds))

    def test_recorded_state_is_append_only_in_the_database(self):
        case = self.saved_case()
        with self.assertRaises(Exception):
            self.db.execute("UPDATE master_case_state SET section = 'grounds'")
        with self.assertRaises(Exception):
            self.db.execute("DELETE FROM master_case_state")

    def test_state_from_another_case_is_refused(self):
        case, _ = released_case()
        other = CaseFile(case_id="C-OTHER")
        with self.assertRaises(MasterCaseStateError):
            other.master.load_dict(case.master.to_dict())

    def test_a_newer_schema_is_refused_rather_than_half_read(self):
        case, _ = released_case()
        state = case.master.to_dict()
        state["schema_version"] = case_state.SCHEMA_VERSION + 1
        with self.assertRaises(MasterCaseStateError):
            case.master.load_dict(state)

    def test_from_dict_restores_the_sections_the_tables_do_not_hold(self):
        case, _ = released_case()
        state = case.master.to_dict()
        rebuilt = CaseFile(case_id=case.case_id)
        MasterCase.from_dict(rebuilt, state)
        self.assertEqual([g.as_dict() for g in rebuilt.master.ground_history],
                         [g.as_dict() for g in case.master.ground_history])
        self.assertEqual([d.as_dict() for d in rebuilt.master.derivations],
                         [d.as_dict() for d in case.master.derivations])
        self.assertEqual([m.as_dict() for m in rebuilt.master.knowledge_match_history],
                         [m.as_dict() for m in case.master.knowledge_match_history])


if __name__ == "__main__":
    unittest.main()
