"""P7 B6 - evidence-sensitive grounds stop being an unrestricted drafting route.

SIGNAGE_DEFECT and OPERATOR_AUTHORITY_ABSENT are registered as assertion-only
finding types: no calculator exists, so they can never be VERIFIED. The
drafter's own assertion of such a defect is refused (VAL-LEGAL-FINDING); the
only permitted routes are approved knowledge-module wording (plan- and
fact-gated) and putting the operator to proof. When an evidential calculator
is added, the flag comes off and the verified-finding route takes over - the
same shape as PoFA.

Payment is deliberately NOT an assertion-only finding: "payment was made" is a
fact-based claim already policed by DV-FACT against the established
`payment_made` fact (hypothesis-gated in P7 B2), and a finding would wrongly
outlaw the legitimate established-fact route.
"""
from __future__ import annotations

import unittest

from test_scenarios import make_case, run
from test_verified_legal_findings import CANCEL, rules

from pcn_appeal.legal import findings as lf
from pcn_appeal.models import Draft, DraftSentence

SIGNAGE_CLAIMS = (
    "The signage at the site was inadequate and could not form a contract.",
    "The signs were illegible and not sufficiently prominent.",
    "The parking terms were not adequately communicated by the signage.",
)
AUTHORITY_CLAIMS = (
    "The operator has no authority from the landowner to issue this charge.",
    "The operator lacked the authorisation required to enforce at this site.",
)
PROOF_OK = (
    "The operator is requested to produce evidence that the signage at the "
    "location was adequate on the material date.",
    "The operator is required to demonstrate its authority from the landowner "
    "to operate and enforce at the location.",
)


def sentence(text, modules, facts=()):
    return DraftSentence(text, list(facts), list(modules), [])


def draft(case, *sentences):
    return Draft(case.case_id, [[s] for s in sentences]
                 + [[DraftSentence(CANCEL, module_refs=["STRUCTURAL"])]])


class RegistryShape(unittest.TestCase):
    def test_assertion_only_types_are_registered_and_never_evaluated(self):
        self.assertEqual(lf.ASSERTION_ONLY,
                         {"SIGNAGE_DEFECT", "OPERATOR_AUTHORITY_ABSENT"})
        case, pipe = make_case()
        run(case, pipe, "", {})
        self.assertTrue(case.legal_findings)
        self.assertFalse({r["finding_type"] for r in case.legal_findings}
                         & lf.ASSERTION_ONLY)

    def test_the_patterns_classify_the_claims(self):
        for s in SIGNAGE_CLAIMS:
            self.assertIn("SIGNAGE_DEFECT", lf.asserted_types(s), s)
        for s in AUTHORITY_CLAIMS:
            self.assertIn("OPERATOR_AUTHORITY_ABSENT", lf.asserted_types(s), s)
        for s in PROOF_OK:
            self.assertTrue(lf.PUT_TO_PROOF.search(s), s)

    def test_no_timing_particulars_for_assertion_only_types(self):
        self.assertEqual(lf.particulars({"finding_type": "SIGNAGE_DEFECT",
                                         "calculation": {"deadline": "2026-06-15"}}), {})


class DrafterAssertionsAreRefused(unittest.TestCase):
    def setUp(self):
        self.case, self.pipe = make_case()
        # A substantive non-PoFA ground. This file is about what the DRAFTER
        # may assert, so it needs an approved module to cite; what that module
        # argues is immaterial. Previously the bare fixture's only ground was
        # KB-POFA-01, which opened on keeper status alone - the spurious PoFA
        # ground closed in v1.1.
        self.out = run(self.case, self.pipe, "I paid for my parking at the machine.",
                       {"payment_made": "yes"})
        self.known = {f.fact_id for f in self.case.facts.values()}
        self.module = self.out.pack.module_ids[0]

    def check(self, d):
        return self.pipe.draft_validation.check(d, self.out.pack, self.known)

    def test_a_model_written_signage_defect_is_blocked(self):
        for text in SIGNAGE_CLAIMS:
            r = self.check(draft(self.case, sentence(text, (self.module,))))
            self.assertIn("VAL-LEGAL-FINDING", rules(r), text)
            msg = next(i.message for i in r.issues if i.rule == "VAL-LEGAL-FINDING")
            self.assertIn("signage", msg)

    def test_a_model_written_authority_defect_is_blocked(self):
        for text in AUTHORITY_CLAIMS:
            r = self.check(draft(self.case, sentence(text, (self.module,))))
            self.assertIn("VAL-LEGAL-FINDING", rules(r), text)

    def test_putting_the_operator_to_proof_stays_allowed(self):
        for text in PROOF_OK:
            r = self.check(draft(self.case, sentence(text, (self.module,))))
            self.assertNotIn("VAL-LEGAL-FINDING", rules(r), (text, r.issues))

    def test_approved_module_wording_stays_allowed(self):
        """The controlled wording of an approved module may state its own
        proposition: it is gated by the plan and the module's facts, which is
        the licence until a calculator exists."""
        chunk = next((c for c in self.out.pack.context_chunks
                      if c.get("kind") == "block" and c.get("module_id") == self.module
                      and c.get("text")), None)
        self.assertIsNotNone(chunk)
        text = str(chunk["text"])
        r = self.check(draft(self.case, sentence(text, (self.module,))))
        self.assertNotIn("VAL-LEGAL-FINDING", rules(r), r.issues)

    def test_calculable_defects_keep_the_strict_rule(self):
        """P6.1 is unchanged: a timing-defect sentence is refused without its
        verified finding even if it were approved wording."""
        r = self.check(draft(self.case, sentence(
            "The Notice to Keeper was not delivered within the applicable "
            "statutory period.", (self.module,))))
        self.assertIn("VAL-LEGAL-FINDING", rules(r))


class EndToEnd(unittest.TestCase):
    def test_an_injected_signage_assertion_never_ships(self):
        case, pipe = make_case()
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), "")
        pipe.answer(case, {})
        bad = {"paragraphs": [
            [{"text": "I write as the registered keeper in respect of Parking "
                      "Charge Notice PCN123456.", "fact_refs": [],
              "module_refs": ["STRUCTURAL"], "evidence_refs": [], "quote_of": None}],
            [{"text": SIGNAGE_CLAIMS[0], "fact_refs": [],
              "module_refs": ["KB-POFA-01"], "evidence_refs": [], "quote_of": None}],
            [{"text": CANCEL, "fact_refs": [], "module_refs": ["STRUCTURAL"],
              "evidence_refs": [], "quote_of": None}]], "no_ground_reason": None}
        pipe.extraction.llm.responses["drafting"] = [bad]
        out = pipe.generate(case)
        self.assertNotIn("inadequate", (out.letter or ""))


if __name__ == "__main__":
    unittest.main()
