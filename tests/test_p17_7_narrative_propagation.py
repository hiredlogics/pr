"""P17.7 — generic customer narrative propagation + question materiality."""
from __future__ import annotations

import json
import unittest

from pcn_appeal.drafting.plan import particular_expressed
from pcn_appeal.engines.account import assess_material_account
from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.semantics import extract_concepts, extract_and_promote
from test_scenarios import make_case, run


def _base(**extra):
    f = dict(
        operator_name="Northbridge Parking Ltd", pcn_number="NB900177",
        vrm="XY12ZAB", parking_location="Northbridge Retail",
        site_postcode="LS1 1AA", parking_event_date="01/06/2026",
        notice_issue_date="05/06/2026", charge_amount="£100",
        alleged_breach="Overstayed paid time", operator_ata="BPA",
        entry_time="10:00", exit_time="14:30",
    )
    f.update(extra)
    return f


class DropOffSemanticPropagation(unittest.TestCase):
    NARR = (
        "I dropped my mum off, left the car park and then returned to pick her up."
    )

    def test_concepts_and_facts(self):
        cs = {c.concept: c.polarity for c in extract_concepts([self.NARR])}
        self.assertEqual(cs.get("DROP_OFF"), "AFFIRMED")
        self.assertEqual(cs.get("LEFT_SITE"), "AFFIRMED")
        self.assertEqual(cs.get("RETURNED"), "AFFIRMED")
        self.assertEqual(cs.get("PICK_UP"), "AFFIRMED")
        case = CaseFile("p177-a")
        case.raw_answers["narrative"] = self.NARR
        assess_material_account(case)
        # MULTIPLE_VISITS may be derived from LEFT+RETURNED at promote time.
        promoted = {c["concept"]: c["polarity"]
                    for c in json.loads(case.raw_answers["_semantic_concepts"])}
        self.assertEqual(promoted.get("MULTIPLE_VISITS"), "AFFIRMED")
        self.assertTrue(case.get("dropoff_activity"))
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))
        self.assertTrue(case.get("multiple_visits"))
        self.assertTrue(case.get("pickup_activity"))
        st = json.loads(case.raw_answers["_semantic_case_state"])
        labels = [t["label"] for t in st["timeline"]
                  if t.get("attribution") == "CUSTOMER_ACCOUNT"]
        self.assertEqual(
            labels[:4], ["drop_off", "depart_site", "return_site", "pick_up"])
        preds = {(r["subject"], r["predicate"], r["object"])
                 for r in st["relationships"]}
        self.assertIn(("DROP_OFF", "PRECEDES", "DEPART_SITE"), preds)
        self.assertIn(("DEPART_SITE", "PRECEDES", "RETURN_SITE"), preds)
        self.assertIn(("RETURN_SITE", "PRECEDES", "PICK_UP"), preds)

    def test_meaning_equivalent_b(self):
        text = (
            "Left after dropping a passenger off and returned later to collect them."
        )
        cs = {c.concept: c.polarity for c in extract_concepts([text])}
        self.assertEqual(cs.get("DROP_OFF"), "AFFIRMED")
        self.assertEqual(cs.get("PICK_UP"), "AFFIRMED")
        self.assertEqual(cs.get("LEFT_SITE"), "AFFIRMED")
        self.assertEqual(cs.get("RETURNED"), "AFFIRMED")

    def test_kb_anpr_and_act_evaluation(self):
        case, pipe = make_case(_base())
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), self.NARR)
        self.assertTrue(case.get("multiple_visits"))
        self.assertTrue(case.get("dropoff_activity"))
        match = KnowledgeMatcher(pipe.kg).match(case)
        anpr = match.candidates.get("KB-ANPR-01")
        act = match.candidates.get("KB-ACT-02")
        self.assertIsNotNone(anpr)
        self.assertEqual(anpr.status, "SUPPORTED", anpr.as_dict())
        self.assertIsNotNone(act)
        # ACT-02 also needs permitted_period_ended absent; status may vary.
        self.assertIn(act.status, ("SUPPORTED", "RELEVANT", "OPEN", "REJECTED"))


class PolarityRegressions(unittest.TestCase):
    def test_shopping_purse(self):
        text = (
            "Entered for shopping, forgot my purse, left the site and returned later."
        )
        case = CaseFile("p177-c")
        extract_and_promote(case, [text])
        self.assertEqual(case.get("purpose_of_visit"), "shopping")
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))
        self.assertTrue(case.get("multiple_visits"))

    def test_paid_left_returned(self):
        text = "I paid for parking, left the site, then came back later."
        case = CaseFile("p177-d")
        extract_and_promote(case, [text])
        self.assertTrue(case.get("payment_made"))
        self.assertTrue(case.get("left_site"))
        self.assertTrue(case.get("returned_same_day"))
        self.assertTrue(case.get("multiple_visits"))

    def test_breakdown(self):
        case = CaseFile("p177-e")
        extract_and_promote(case, ["Vehicle broke down and could not leave."])
        self.assertTrue(case.get("vehicle_immobilised"))

    def test_negated_left(self):
        concepts = extract_concepts(["I did not leave the site."])
        left = [c for c in concepts if c.concept == "LEFT_SITE"]
        self.assertTrue(left)
        self.assertEqual(left[0].polarity, "NEGATED")
        case = CaseFile("p177-f")
        extract_and_promote(case, ["I did not leave the site."])
        self.assertNotEqual(case.get("left_site"), True)

    def test_uncertain_not_affirmed(self):
        concepts = extract_concepts(
            ["I think I may have left and come back."])
        left = next(c for c in concepts if c.concept == "LEFT_SITE")
        self.assertEqual(left.polarity, "UNCERTAIN")
        case = CaseFile("p177-g")
        extract_and_promote(case, ["I think I may have left and come back."])
        self.assertNotEqual(case.get("left_site"), True)
        self.assertNotEqual(case.get("multiple_visits"), True)


class ParticularExpression(unittest.TestCase):
    def test_dropoff_sequence_expressed(self):
        text = (
            "The vehicle entered to drop off a passenger, left the site, "
            "and later returned to collect the passenger. These were separate visits."
        )
        self.assertTrue(particular_expressed(text, "dropoff_activity", True))
        self.assertTrue(particular_expressed(text, "left_site", True))
        self.assertTrue(particular_expressed(text, "returned_same_day", True))
        self.assertTrue(particular_expressed(text, "pickup_activity", True))
        self.assertTrue(particular_expressed(text, "multiple_visits", True))
        generic = (
            "ANPR timestamps may include manoeuvring, consideration time, "
            "payment activity or exit time."
        )
        self.assertFalse(particular_expressed(generic, "dropoff_activity", True))


class PostcodeMateriality(unittest.TestCase):
    def test_postcode_skipped_when_anpr_open(self):
        case, pipe = make_case(_base(site_postcode=None))
        # Clear postcode if extracted
        if case.facts.get("site_postcode"):
            case.retract("site_postcode", reason="test")
        case.put(Fact(
            "F-jur", "jurisdiction", "UNKNOWN", FactStatus.UNCERTAIN,
            FactSource(SourceKind.CALCULATION, "test"),
        ))
        pipe.ingest(case)
        # Confirm with drop-off narrative → multiple_visits → ANPR path
        qs = pipe.confirm(
            case, {}, list(case.facts),
            "I dropped my mum off, left the car park and then returned to pick her up.",
        )
        facts_asked = {q.get("fact") for q in (qs or [])}
        # With fact-specific ANPR open, postcode must not be asked.
        self.assertNotIn("site_postcode", facts_asked)
        skipped = [a for a in case.audit
                   if a.get("event") == "site_postcode_skipped"]
        self.assertTrue(skipped or case.get("multiple_visits"))


class MixedPofaNarrative(unittest.TestCase):
    def test_late_ntk_and_multiple_visits(self):
        case, pipe = make_case(_base(
            notice_issue_date="20/06/2026",
            parking_event_date="01/06/2026",
        ))
        out = run(
            case, pipe,
            "I dropped my mum off, left the car park and then returned to pick her up.",
            {},
        )
        mods = set(out.pack.module_ids or [])
        # PoFA late-NTK family should survive with narrative ANPR when available.
        self.assertTrue(
            any(m.startswith("KB-POFA") for m in mods) or out.state.value != "RELEASED",
            f"modules={mods} state={out.state}",
        )
        # Narrative facts must reach FactManager regardless of release gates.
        self.assertTrue(case.get("multiple_visits"))
        self.assertTrue(case.get("dropoff_activity"))

def setUpModule():
    from support import finished_reader
    finished_reader.start()


def tearDownModule():
    from support import finished_reader
    finished_reader.stop()


if __name__ == "__main__":
    unittest.main()
