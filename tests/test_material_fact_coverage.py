"""VAL-MATERIAL-FACT-COVERAGE.

Where the locked claim plan carries a factual rebuttal with required
particulars, the finished draft must actually say the material fact. A draft
that keeps only the evidential put-to-proof wording has dropped the ground the
plan locked, and must not be released.

The check is semantic, not exact-string: the drafter may rewrite
professionally, so what is verified is that the fact's substance is present,
not that a particular sentence was copied.
"""
import unittest

from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

ONLY_EVIDENCE = ("The operator has not demonstrated that the conditions of use for "
                 "the bay were breached, and is requested to produce the records "
                 "relied upon.")
STATES_THE_FACT = ("The keeper's account is that children were present in the vehicle "
                   "throughout the visit, which disputes the factual basis of the "
                   "allegation. The operator is requested to produce the records "
                   "relied upon.")
REWORDED_FACT = ("A child was travelling in the vehicle for the duration of the stay, "
                 "contrary to the premise of the allegation.")


class CoverageOfARequiredParticular(unittest.TestCase):

    def _validate(self, text, *, grounds, fact_name="child_occupant_present",
                  fact_value=True):
        fid = f"fid-{fact_name}"
        for g in grounds:
            g["supporting_fact_ids"] = [fid]
            for p in g.get("required_particulars") or []:
                p.setdefault("fact_id", fid)
        plan = {"status": "LOCKED", "approved": ["KB-BAY-01"], "grounds": grounds}
        pack = RetrievalPack(
            primary_route="BAY", secondary_routes=[], module_ids=["KB-BAY-01"],
            verified_facts={fact_name: fact_value, "vrm": "AB12CDE"},
            fact_refs={fact_name: fid, "vrm": "fid-vrm"},
            missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version=None, pofa_route="POSTAL", pofa_findings=[],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[], claim_plan=plan)
        draft = Draft("C-1", [[DraftSentence(text, ["fid-vrm"], ["KB-BAY-01"])]])
        return ValidationEngine().validate(draft, pack)

    def _ground(self, fact_name="child_occupant_present"):
        return {
            "ground_id": "G-1", "ground_type": "FACTUAL_REBUTTAL",
            "allegation_ref": "A-1", "relationship": "CONTRADICTS",
            "supporting_module_ids": ["KB-BAY-01"],
            "required_particulars": [
                {"type": "STATE_FACT", "fact_name": fact_name},
                {"type": "CONNECT_FACT_TO_ALLEGATION", "allegation_ref": "A-1"},
            ],
        }

    def test_a_draft_that_drops_the_fact_is_blocked(self):
        res = self._validate(ONLY_EVIDENCE, grounds=[self._ground()])
        codes = [i.rule for i in res.issues]
        self.assertIn("VAL-MATERIAL-FACT-COVERAGE", codes, codes)

    def test_a_draft_that_states_the_fact_passes(self):
        res = self._validate(STATES_THE_FACT, grounds=[self._ground()])
        codes = [i.rule for i in res.issues]
        self.assertNotIn("VAL-MATERIAL-FACT-COVERAGE", codes, codes)

    def test_coverage_is_semantic_not_exact_string(self):
        """"A child was travelling in the vehicle" is the same fact as
        "children were present" - the drafter may reword."""
        res = self._validate(REWORDED_FACT, grounds=[self._ground()])
        codes = [i.rule for i in res.issues]
        self.assertNotIn("VAL-MATERIAL-FACT-COVERAGE", codes, codes)

    def test_a_plan_with_no_factual_rebuttal_is_unaffected(self):
        res = self._validate(ONLY_EVIDENCE, grounds=[
            {"ground_id": "G-2", "ground_type": "KB_MODULE",
             "supporting_module_ids": ["KB-BAY-01"]}])
        codes = [i.rule for i in res.issues]
        self.assertNotIn("VAL-MATERIAL-FACT-COVERAGE", codes, codes)

    def test_the_check_generalises_to_another_fact(self):
        g = self._ground("permit_held")
        res = self._validate(
            "The operator is requested to produce its records.",
            grounds=[g], fact_name="permit_held")
        codes = [i.rule for i in res.issues]
        self.assertIn("VAL-MATERIAL-FACT-COVERAGE", codes, codes)


if __name__ == "__main__":
    unittest.main()
