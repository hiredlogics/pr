"""Phase 3C: the absence of uploaded evidence is never turned into a negative fact.

A proposition read from a document has three states:

  A. evidence not provided / not read      -> UNKNOWN (the fact is not written)
  B. evidence read, proposition absent     -> FALSE
  C. evidence read, proposition present    -> TRUE

The lease facts used to collapse A into B: no lease, and a lease nobody could read,
both wrote `lease_parking_clause_found = False`, which KB-AUTH-01/02 then read as
"the lease has no parking clause" and KB-RES-* as a reason to REJECT.

These tests drive the real pipeline (enrichment, recovery, the matcher) over the
three states, and scan the code so that a new derivation of this kind must be
classified rather than slip in.
"""
from __future__ import annotations

import glob
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pcn_appeal import evidence_review as ER
from pcn_appeal.engines import module_eligibility as ME
from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import (CaseFile, EvidenceItem, Fact, FactSource, FactStatus,
                               SourceKind)
from pcn_appeal.rules.dsl import referenced_facts

from test_private_parking_v2 import make_case, run_pipeline

KG = KnowledgeGraph()
ROOT = Path(__file__).resolve().parents[1] / "pcn_appeal"

CLAUSE = ("3.2 The Tenant shall have the right to park one private motor vehicle in the "
          "parking space numbered 14 shown on the plan.\n4. Rent is payable monthly.")
REGS = CLAUSE + ("\n5.1 The Tenant shall comply with any parking regulations or permit scheme "
                 "introduced by the Landlord for the car park.")
NO_CLAUSE = ("1. Definitions apply.\n2. The Tenant shall pay rent monthly on the first day.\n"
             "4. The Landlord shall keep the structure in repair.")

NONE_, UNREADABLE, ABSENT, PRESENT, PRESENT_REGS = (
    "no lease", "lease uploaded, nothing readable", "lease read, clause absent",
    "lease read, clause present", "lease read, clause + regulations power")
LEASE_TEXT = {UNREADABLE: "", ABSENT: NO_CLAUSE, PRESENT: CLAUSE, PRESENT_REGS: REGS}


def _lease(label):
    """A fresh evidence item each time: tests change it."""
    if label == NONE_:
        return None
    return EvidenceItem("E3", "LEASE", "lease.pdf", text=LEASE_TEXT[label])


def _answer(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "q")))


def _run(label):
    ev = _lease(label)
    case, pipe = make_case({"alleged_breach": "No valid permit displayed"},
                           evidence={"E3": ev} if ev else None,
                           doc_types={"E3": "LEASE"} if ev else None)
    run_pipeline(case, pipe, "resident, my bay", scenario="3c-lease")
    for name, value in (("authorisation_source", "PERMIT"), ("permit_held", True),
                        ("resident_status", "TENANT")):
        _answer(case, name, value)
    return case, pipe


def _statuses(case, pipe):
    got = KnowledgeMatcher(pipe.kg).match(case).candidates
    return {m: {"RELEVANT": "UNRESOLVED", "OPEN": "UNRESOLVED"}.get(got[m].status, got[m].status)
            for m in ("KB-AUTH-01", "KB-AUTH-02", "KB-RES-01", "KB-RES-02")}


# ------------------------------------------------------------------ the three states
class ReviewState(unittest.TestCase):

    def test_the_three_states(self):
        case = CaseFile("r")
        self.assertEqual(ER.review(case, ("LEASE",)).state, ER.NOT_PROVIDED)
        case.evidence["E1"] = EvidenceItem("E1", "LEASE", "l.pdf", text="  \n ")
        self.assertEqual(ER.review(case, ("LEASE",)).state, ER.NOT_REVIEWED)
        case.evidence["E1"].text = "3.2 right to park"
        self.assertEqual(ER.review(case, ("LEASE",)).state, ER.REVIEWED)

    def test_another_kind_of_evidence_is_not_the_lease(self):
        case = CaseFile("r")
        case.evidence["E1"] = EvidenceItem("E1", "PCN", "p.pdf", text="a long notice")
        self.assertEqual(ER.review(case, ("LEASE", "TENANCY")).state, ER.NOT_PROVIDED)

    def test_an_evidence_item_not_uploaded_is_not_provided(self):
        case = CaseFile("r")
        case.evidence["E1"] = EvidenceItem("E1", "LEASE", "l.pdf", uploaded=False, text="3.2 park")
        self.assertEqual(ER.review(case, ("LEASE",)).state, ER.NOT_PROVIDED)

    def test_withdraw_takes_back_only_what_was_derived(self):
        case = CaseFile("w")
        case.put(Fact("F-a", "lease_parking_clause_found", True, FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "lease_clause_finder")))
        case.put(Fact("F-b", "payment_made", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "q")))
        gone = ER.withdraw_derived(case, ("lease_parking_clause_found", "payment_made", "nope"), "t")
        self.assertEqual(gone, ["lease_parking_clause_found"])
        self.assertIsNone(case.get("lease_parking_clause_found"))
        self.assertIs(case.get("payment_made"), True)


# ------------------------------------------------------------------ the lease facts
class LeaseFacts(unittest.TestCase):
    """What each fact means, and what is written in each state."""

    def test_contract(self):
        want = {
            NONE_: dict(provided=False, found=None, regs=None),
            UNREADABLE: dict(provided=True, found=None, regs=None),
            ABSENT: dict(provided=True, found=False, regs=False),
            PRESENT: dict(provided=True, found=True, regs=False),
            PRESENT_REGS: dict(provided=True, found=True, regs=True),
        }
        for label, w in want.items():
            case, _ = _run(label)
            with self.subTest(state=label):
                self.assertIs(case.get("lease_evidence_provided"), w["provided"])
                self.assertIs(case.get("lease_parking_clause_found"), w["found"])
                self.assertIs(case.get("lease_has_regulations_clause"), w["regs"])

    def test_no_lease_and_unreadable_lease_never_write_a_negative(self):
        for label in (NONE_, UNREADABLE):
            case, _ = _run(label)
            self.assertNotIn("lease_parking_clause_found", case.facts, label)
            self.assertNotIn("lease_has_regulations_clause", case.facts, label)
            self.assertNotIn("lease_clauses", case.facts, label)

    def test_a_negative_is_written_only_after_the_lease_was_read(self):
        case, _ = _run(ABSENT)
        self.assertIs(case.get("lease_parking_clause_found"), False)
        self.assertEqual(ER.review(case, ("LEASE", "TENANCY")).state, ER.REVIEWED)

    def test_a_value_derived_from_a_lease_is_withdrawn_when_the_lease_goes(self):
        case, pipe = _run(PRESENT)
        self.assertIs(case.get("lease_parking_clause_found"), True)
        del case.evidence["E3"]
        pipe.reasoning.enrich(case)
        self.assertIs(case.get("lease_evidence_provided"), False)
        self.assertNotIn("lease_parking_clause_found", case.facts)
        self.assertNotIn("lease_clauses", case.facts)

    def test_a_lease_that_stops_being_readable_is_withdrawn_too(self):
        case, pipe = _run(PRESENT)
        case.evidence["E3"].text = ""
        pipe.reasoning.enrich(case)
        self.assertIs(case.get("lease_evidence_provided"), True)
        self.assertNotIn("lease_parking_clause_found", case.facts)

    def test_the_facts_are_evidence_owned(self):
        from pcn_appeal.fact_ownership import EVIDENCE_OWNED
        self.assertTrue({"lease_evidence_provided", "lease_clauses", "lease_parking_clause_found",
                         "lease_has_regulations_clause"} <= EVIDENCE_OWNED)


# ------------------------------------------------------------------ AUTH-01 / AUTH-02 / RES
class LeaseEligibility(unittest.TestCase):
    """The truth table, through the pipeline."""

    TABLE = {
        #            AUTH-01       AUTH-02       RES-01        RES-02
        NONE_:       ("SUPPORTED",  "SUPPORTED",  "UNRESOLVED", "UNRESOLVED"),
        UNREADABLE:  ("UNRESOLVED", "UNRESOLVED", "UNRESOLVED", "UNRESOLVED"),
        ABSENT:      ("SUPPORTED",  "SUPPORTED",  "REJECTED",   "REJECTED"),
        PRESENT:     ("BLOCKED",    "BLOCKED",    "SUPPORTED",  "SUPPORTED"),
        PRESENT_REGS: ("BLOCKED",   "BLOCKED",    "SUPPORTED",  "BLOCKED"),
    }

    def test_truth_table(self):
        for label, want in self.TABLE.items():
            case, pipe = _run(label)
            got = _statuses(case, pipe)
            with self.subTest(state=label):
                self.assertEqual((got["KB-AUTH-01"], got["KB-AUTH-02"], got["KB-RES-01"],
                                  got["KB-RES-02"]), want, got)

    def test_an_unreadable_lease_is_not_a_lease_without_the_clause(self):
        unreadable, pu = _run(UNREADABLE)
        absent, pa = _run(ABSENT)
        self.assertNotEqual(_statuses(unreadable, pu), _statuses(absent, pa))
        self.assertEqual(_statuses(unreadable, pu)["KB-AUTH-02"], "UNRESOLVED")

    def test_an_unreadable_lease_names_what_it_cannot_rule_out(self):
        case, pipe = _run(UNREADABLE)
        c = KnowledgeMatcher(pipe.kg).match(case).candidates["KB-AUTH-02"]
        self.assertTrue(c.unverified_blockers, c.reason)
        self.assertTrue(any("lease_parking_clause_found" in b for b in c.unverified_blockers))

    def test_no_lease_leaves_the_resident_grounds_open_not_rejected(self):
        case, pipe = _run(NONE_)
        got = _statuses(case, pipe)
        self.assertEqual((got["KB-RES-01"], got["KB-RES-02"]), ("UNRESOLVED", "UNRESOLVED"))

    def test_no_lease_does_not_claim_anything_about_a_lease(self):
        """AUTH is supported because no lease is in the case, not because one was read."""
        case, pipe = _run(NONE_)
        c = KnowledgeMatcher(pipe.kg).match(case).candidates["KB-AUTH-02"]
        self.assertEqual(c.status, "SUPPORTED")
        self.assertNotIn("lease_parking_clause_found", case.fact_view())

    def test_the_lease_facts_belong_to_the_evidence_not_the_customer(self):
        from pcn_appeal.fact_ownership import owner_of
        for name in ("lease_evidence_provided", "lease_parking_clause_found",
                     "lease_has_regulations_clause"):
            self.assertEqual(owner_of(name), "EVIDENCE", name)

    def test_a_held_but_untrusted_clause_fact_does_not_settle_the_blocker(self):
        out = ME.evaluate_module_id(
            KG, "KB-AUTH-02", {"driver_status": "UNIDENTIFIED", "permit_held": True,
                               "lease_evidence_provided": True, "lease_parking_clause_found": True},
            unreliable={"lease_parking_clause_found"})
        self.assertEqual(out.status, "UNRESOLVED")

    def test_every_module_reading_a_lease_fact_treats_an_unread_lease_as_unknown(self):
        """Not SUPPORTED, and never REJECTED on the strength of a lease that was not read."""
        lease = {"lease_parking_clause_found", "lease_has_regulations_clause", "lease_clauses"}
        checked = 0
        for m in KG.active_modules():
            gate = referenced_facts(m.use_when) | referenced_facts(m.do_not_use_when)
            if not gate & lease:
                continue
            supported = {"lease_parking_clause_found": True, "lease_has_regulations_clause": False,
                         "lease_evidence_provided": True, "resident_status": "TENANT",
                         "permit_held": True, "authorisation_source": "PERMIT",
                         "allocated_bay": "14", "substantial_interference": True,
                         "enforcement_interference": True, "driver_status": "UNIDENTIFIED"}
            unread = {k: v for k, v in supported.items() if k not in lease}
            out = ME.evaluate_module(m, unread)
            checked += 1
            with self.subTest(module=m.module_id):
                self.assertNotEqual(out.status, ME.REJECTED, out.rejected_conditions)
                self.assertFalse(any(any(f in c for f in lease) for c in out.rejected_conditions),
                                 out.rejected_conditions)
        self.assertGreaterEqual(checked, 7)


# ------------------------------------------------------------------ the pattern, everywhere
class AbsenceDerivations(unittest.TestCase):
    """Every derived fact a KB gate reads is classified. A new one must be too."""

    SAFE_BOOLEAN = {
        # a literal statement about the upload ("a lease is in the supplied set")
        "lease_evidence_provided",
        # "a receipt is in the supplied evidence": read only as the EV-01 exclusion
        # (a receipt relied on that leaves validation unknown), never as "no purchase"
        "shopping_purchase_confirmed",
        # "an eligibility affirmation was found in the customer's own account"
        "account_contradicts_allegation",
    }
    CORRECT_THREE_STATE = {
        # written only when the thing was read / is computable; otherwise not written
        "lease_clauses", "lease_parking_clause_found", "lease_has_regulations_clause",
        "notice_sides_complete", "ntk_invites_pass_to_driver", "ntk_defect_document_confirmed",
        "ntk_defect_statutory_invitation", "pofa_route", "pofa_finding",
        "keying_error_type", "material_account_proposition", "observation_window_min",
        "total_recorded_duration_min", "restricted_bay_alleged", "authority_challenge_proportionate",
        "jurisdiction", "notice_route", "parking_validation_status",
    }
    EVIDENCE_NOT_REVIEWED_BUG: set = set()          # lease_* were here until Phase 3C

    def _derived_gate_facts(self):
        gate = set()
        for m in KG.active_modules():
            gate |= (referenced_facts(m.use_when) | referenced_facts(m.do_not_use_when)
                     | set(m.required_facts or []))
        files = [f for f in glob.glob(str(ROOT / "**" / "*.py"), recursive=True)
                 if "/eval/" not in f]
        found = set()
        for fact in gate:
            pat = re.compile(r'"F-%s"|Fact\([^)]*"%s"' % (re.escape(fact), re.escape(fact)))
            for f in files:
                lines = Path(f).read_text(encoding="utf-8").split("\n")
                for i, line in enumerate(lines):
                    if pat.search(line) and re.search(r"DERIVED|CALCULATION", " ".join(lines[i:i + 4])):
                        found.add(fact)
        return found

    def test_every_derived_gate_fact_is_classified(self):
        classified = self.SAFE_BOOLEAN | self.CORRECT_THREE_STATE | self.EVIDENCE_NOT_REVIEWED_BUG
        self.assertEqual(self._derived_gate_facts() - classified, set(),
                         "a new derivation: classify it SAFE_BOOLEAN / CORRECT_THREE_STATE")

    def test_nothing_is_left_in_the_bug_class(self):
        self.assertEqual(self.EVIDENCE_NOT_REVIEWED_BUG, set())

    def test_the_classes_do_not_overlap(self):
        self.assertEqual(self.SAFE_BOOLEAN & self.CORRECT_THREE_STATE, set())

    def test_a_three_state_fact_is_not_written_false_by_a_missing_input(self):
        """The enrichment code writes the lease facts only on the reviewed branch."""
        text = (ROOT / "engines" / "reasoning.py").read_text(encoding="utf-8")
        body = text[text.index("def enrich("):text.index("# ------------------------------------------------------------------ 2")]
        self.assertIn("if not seen.reviewed:", body)
        before_branch = body[:body.index("if not seen.reviewed:")]
        self.assertNotIn("lease_parking_clause_found", before_branch.split('"""', 2)[2])
        self.assertNotIn("bool(clauses)", before_branch)

    def test_the_receipt_flag_is_read_only_as_a_reliance_exclusion(self):
        readers = [m.module_id for m in KG.active_modules()
                   if "shopping_purchase_confirmed" in (referenced_facts(m.use_when)
                                                        | referenced_facts(m.do_not_use_when))]
        self.assertEqual(readers, ["KB-EV-01"])
        self.assertEqual(KG.modules["KB-EV-01"].use_when.get("all") is not None, True)
        self.assertNotIn("shopping_purchase_confirmed", referenced_facts(KG.modules["KB-EV-01"].use_when))

    def test_the_hire_documents_fact_is_a_statement_about_the_upload(self):
        """hire_docs_supplied: 'those documents have not been shown to have been supplied'."""
        m = KG.modules.get("KB-POFA-06")
        self.assertIn("have not been shown to have been supplied", m.core_proposition)


# ------------------------------------------------------------------ the evaluator is untouched
class Untouched(unittest.TestCase):

    def test_the_frozen_files_name_no_lease_fact(self):
        for rel in ("rules/dsl.py", "engines/module_eligibility.py", "engines/knowledge_matcher.py",
                    "engines/question_authority.py", "engines/claim_plan.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            for token in (r"(?<![a-z])lease_", "evidence_review"):
                self.assertIsNone(re.search(token, text), f"{rel} mentions {token}")


if __name__ == "__main__":
    unittest.main()
