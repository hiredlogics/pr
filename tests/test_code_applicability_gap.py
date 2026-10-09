"""The Code applies by event date; the operator's membership is a knowledge gap.

Client decision 2026-10-09. The sector Single Code of Practice is
version-controlled by the DATE of the parking event, not by which trade body
the operator belongs to - and the notices customers upload almost never print
the trade body at all. Resolving the Code only for a known ATA therefore threw
away the Code on nearly every real case, including every operator the system
had not seen before.

So the version now resolves from the date. What stays unestablished is whether
THIS operator is bound by that Code, and that is a knowledge gap: the letter
has to carry it (KB-GOV-03 - no legal proposition the knowledge base does not
support; VAL-CODE - the applicability check), not quietly assert it and not
pretend no Code existed.

These tests fix both halves: the gap is flagged rather than hidden, and a
sentence that states a Code value as settled on it is blocked.
"""
from __future__ import annotations

import unittest
from datetime import date

from pcn_appeal.engines.validation import ValidationEngine
from pcn_appeal.legal import code_versions
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

EVENT = date(2026, 6, 1)


def pack(code_status, text_version="SCOP-1.1"):
    return RetrievalPack(
        primary_route="CODE", secondary_routes=[], module_ids=[],
        verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
        prohibited_claims=[], code_version=text_version,
        code_status=code_status,
        pofa_route="POSTAL", pofa_findings=[],
        driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[], lease_clauses=[],
    )


def rules_for(sentence, code_status):
    draft = Draft("C-CODE", [[DraftSentence(sentence, [], [])]])
    return {i.rule for i in ValidationEngine().validate(draft, pack(code_status)).issues}


class ResolutionFollowsTheEventDate(unittest.TestCase):

    def test_a_notice_without_a_trade_body_still_resolves_the_code(self):
        version, status = code_versions.resolve(EVENT, None, None)
        self.assertIsNotNone(version, "the Code applies by date; it must resolve")
        self.assertTrue(code_versions.is_usable(status))
        self.assertTrue(code_versions.ata_unverified(status))

    def test_a_known_trade_body_resolves_without_the_gap(self):
        _, status = code_versions.resolve(EVENT, "BPA", None)
        self.assertTrue(code_versions.is_usable(status))
        self.assertFalse(code_versions.ata_unverified(status))

    def test_no_event_date_resolves_nothing(self):
        """The date is what the version hangs on, so without it there is no Code."""
        version, status = code_versions.resolve(None, "BPA", None)
        self.assertIsNone(version)
        self.assertFalse(code_versions.is_usable(status))

    def test_an_unknown_trade_body_is_not_treated_as_a_named_one(self):
        for shown in ("UNKNOWN", "NOT_SHOWN", ""):
            with self.subTest(operator_ata=shown):
                _, status = code_versions.resolve(EVENT, shown, None)
                self.assertTrue(code_versions.ata_unverified(status))


class TheLetterMustCarryTheGap(unittest.TestCase):
    """VAL-CODE: a Code value may be used, but not asserted as settled."""

    FLAT = ("A 10-minute grace period applies and the vehicle left within it.",
            "The Code requires a grace period of 10 minutes after the permitted "
            "period ends.")
    CAVEATED = (
        "Insofar as the operator is bound by the Code, a 10-minute grace period "
        "applies after the permitted period.",
        "The notice does not state the operator's trade body; to the extent that "
        "the operator is a member, the 10 minute grace period applies.",
        "The operator is invited to confirm its accredited body membership, under "
        "which a grace period of 10 minutes applies.",
    )

    def test_a_flat_code_value_is_blocked_while_membership_is_unestablished(self):
        for s in self.FLAT:
            with self.subTest(sentence=s):
                self.assertIn("VAL-CODE", rules_for(s, "RESOLVED_ATA_UNVERIFIED"))

    def test_the_same_sentence_is_fine_once_the_trade_body_is_known(self):
        """The gap is the reason for the block - not the Code value itself."""
        for s in self.FLAT:
            with self.subTest(sentence=s):
                self.assertNotIn("VAL-CODE", rules_for(s, "RESOLVED"))

    def test_a_conditional_code_value_passes_on_the_unverified_ata(self):
        for s in self.CAVEATED:
            with self.subTest(sentence=s):
                self.assertNotIn("VAL-CODE", rules_for(s, "RESOLVED_ATA_UNVERIFIED"))

    def test_an_unresolved_code_still_blocks_the_value_outright(self):
        """Unverified membership and no Code at all are different failures."""
        draft = Draft("C-CODE", [[DraftSentence(self.FLAT[0], [], [])]])
        p = pack("UNRESOLVED:no_event_date", text_version=None)
        rules = {i.rule for i in ValidationEngine().validate(draft, p).issues}
        self.assertIn("VAL-CODE", rules)


class TheDrafterIsToldAboutTheGap(unittest.TestCase):
    """A blocker the drafter cannot see would just fail the letter repeatedly."""

    def test_the_payload_states_the_applicability_not_just_the_version(self):
        from pcn_appeal.drafting.context import DraftContext
        for status, expected in (
                ("RESOLVED_ATA_UNVERIFIED",
                 "APPLIES_BY_DATE_OPERATOR_MEMBERSHIP_UNESTABLISHED"),
                ("RESOLVED", "SETTLED")):
            with self.subTest(code_status=status):
                g = DraftContext.from_pack(pack(status)).guidance
                self.assertEqual(g.get("code_applicability"), expected)

    def test_no_code_version_reads_as_unresolved(self):
        from pcn_appeal.drafting.context import DraftContext
        g = DraftContext.from_pack(
            pack("UNRESOLVED:no_event_date", text_version=None)).guidance
        self.assertEqual(g.get("code_applicability"), "UNRESOLVED")

    def test_the_prompt_tells_the_drafter_what_to_do_with_it(self):
        from pcn_appeal import prompts
        body = prompts.system("drafting")
        self.assertIn("APPLIES_BY_DATE_OPERATOR_MEMBERSHIP_UNESTABLISHED", body)


if __name__ == "__main__":
    unittest.main()
