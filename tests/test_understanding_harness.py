"""The scorer for the live understanding run must itself be right before a
funded run is spent on it: it is checked here against hand-built packets, and
it must refuse a run the model never read."""
from __future__ import annotations

import unittest

from pcn_appeal.eval.understanding.__main__ import (
    _by_tag, _cause_kept, _order_kept, score_meaning)
from pcn_appeal.eval.understanding.cases import CASES, HOLDOUTS


def ev(i, text, polarity="AFFIRMED"):
    return {"event_id": i, "description": text, "polarity": polarity, "source_text": text}


class Scoring(unittest.TestCase):

    def test_order_is_kept_by_event_order_or_by_an_edge(self):
        packet = {"events": [ev("E1", "drove to the station"), ev("E2", "came back")]}
        self.assertTrue(_order_kept([["station"], ["came back"]], packet))
        self.assertFalse(_order_kept([["came back"], ["station"]], packet))
        swapped = {"events": [ev("E2", "came back"), ev("E1", "drove to the station")],
                   "relationships": [{"source_id": "E1", "relationship": "PRECEDES",
                                      "target_id": "E2"}]}
        self.assertTrue(_order_kept([["station"], ["came back"]], swapped))

    def test_a_missing_step_fails_order(self):
        self.assertFalse(_order_kept([["station"], ["came back"]],
                                     {"events": [ev("E1", "drove to the station")]}))

    def test_cause_needs_an_edge_or_one_event_stating_both(self):
        both = {"events": [ev("E1", "the app crashed so no payment was made")]}
        self.assertTrue(_cause_kept((["crash"], ["no payment"]), both))
        apart = {"events": [ev("E1", "the app crashed"), ev("E2", "no payment was made")]}
        self.assertFalse(_cause_kept((["crash"], ["no payment"]), apart))
        apart["relationships"] = [{"source_id": "E1", "relationship": "CAUSES", "target_id": "E2"}]
        self.assertTrue(_cause_kept((["crash"], ["no payment"]), apart))

    def test_negation_and_uncertainty_are_scored_on_the_item_that_carries_them(self):
        case = {"keep": [["shop"]], "held": [(["shop"], "NEGATED")]}
        self.assertTrue(score_meaning(case, {"events": [ev("E1", "did not enter the shop", "NEGATED")]})["polarity_kept"])
        self.assertFalse(score_meaning(case, {"events": [ev("E1", "entered the shop")]})["polarity_kept"])
        unsure = {"keep": [], "held": [(["meter"], "UNCERTAIN")]}
        self.assertTrue(score_meaning(unsure, {"uncertainties": [{"about": "fed the meter"}]})["polarity_kept"])

    def test_a_legal_conclusion_in_a_packet_is_flagged(self):
        self.assertTrue(score_meaning({}, {"summary": "KB-POFA-02 applies"})["leaked"])

    def test_tag_totals(self):
        rows = [{"tags": "negation specifics", "pass": True}, {"tags": "negation", "pass": False}]
        self.assertEqual(_by_tag(rows), {"negation": "1/2", "specifics": "1/1"})


class Fixtures(unittest.TestCase):

    def test_there_are_twenty_fixtures_and_ten_distinct_holdouts(self):
        self.assertEqual((len(CASES), len(HOLDOUTS)), (20, 10))
        texts = {c["text"].lower() for c in CASES}
        self.assertFalse(texts & {h["text"].lower() for h in HOLDOUTS})

    def test_every_case_declares_its_ambiguity_and_a_reply_when_it_must_be_asked(self):
        for c in CASES + HOLDOUTS:
            self.assertIn(c["ambiguity"], ("MATERIAL", "NON_MATERIAL", "NONE", "EITHER"), c["id"])
            if c["expect"] == "NEEDS_CLARIFICATION":
                self.assertEqual(c["ambiguity"], "MATERIAL", c["id"])
                self.assertTrue(c.get("answer"), c["id"])


if __name__ == "__main__":
    unittest.main()
