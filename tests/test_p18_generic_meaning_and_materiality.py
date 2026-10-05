"""P18 — generic meaning propagation and question materiality.

Three generic defects, each proved by behaviour rather than by wording:

1. The semantic model payload was serialized without normalizing fact values.
   A notice date is a `datetime.date` by the time it reaches here, so
   `json.dumps` raised for every real case and the caller's except branch fell
   back to the offline reference bridge - the model's reading never ran in
   production, and specific customer particulars were replaced by the
   deterministic bridge's generic propositions.

2. `departure_reason_proposition` discarded the object the customer actually
   named and substituted "a necessary item".

3. `postcode_unlocks` computed only the counterfactual supported set, never a
   baseline, so it could not tell whether knowing the postcode would change
   anything - and the hold path asked for it without the ask path's gate.

None of these fixes names an operator, a location, a retailer or a phrase.
"""
from __future__ import annotations

import datetime
import json
import unittest

from pcn_appeal.engines.narrative import (
    departure_reason_object, departure_reason_proposition, extract_departure_reason,
)
from pcn_appeal.semantics.extract import extract_semantic_product


class SemanticPayloadSurvivesRealFactValues(unittest.TestCase):
    """A date-valued fact must not silently disable the model's reading."""

    class _Recorder:
        def __init__(self):
            self.seen = None

        def complete_json(self, *, task, system, user, images=None):
            self.seen = json.loads(user)       # must be serializable at all
            return {
                "concepts": [{"concept": "SHOPPING", "polarity": "AFFIRMED",
                              "attribution": "CUSTOMER", "source_text": "shopping",
                              "confidence": 0.9}],
                "events": [], "narrative_atoms": [], "relationships": [],
                "material_relevance": [],
            }

    def test_a_date_valued_fact_does_not_disable_the_model(self):
        llm = self._Recorder()
        product = extract_semantic_product(
            ["I was shopping at the time."], llm=llm,
            confirmed_facts={
                "parking_event_date": datetime.date(2026, 6, 2),
                "notice_issue_date": datetime.date(2026, 6, 8),
                "pcn_number": "NG1",
            })
        self.assertTrue(product["llm_passed"],
                        f"fell back to the bridge: {product.get('degraded_reason')}")
        self.assertEqual(product.get("degraded_reason"), "")
        self.assertIn("SHOPPING", {c.concept for c in product["concepts"]})

    def test_the_date_reaches_the_model_as_a_readable_value(self):
        llm = self._Recorder()
        extract_semantic_product(
            ["I was shopping."], llm=llm,
            confirmed_facts={"parking_event_date": datetime.date(2026, 6, 2)})
        self.assertEqual(llm.seen["confirmed_facts"]["parking_event_date"],
                         "2026-06-02")

    def test_a_real_provider_failure_is_reported_not_silent(self):
        class Broken:
            def complete_json(self, **kwargs):
                raise RuntimeError("provider down")

        product = extract_semantic_product(["I was shopping."], llm=Broken())
        self.assertFalse(product["llm_passed"])
        self.assertIn("provider down", product["degraded_reason"])


class DepartureReasonKeepsWhatTheCustomerNamed(unittest.TestCase):
    """Any noun, not a list of known items."""

    def test_the_named_object_survives(self):
        for text, expect in (
            ("I realised I had left my purse at home, so I left.", "purse"),
            ("I forgot my wallet and drove home to collect it.", "wallet"),
            ("I left the site to fetch my medication.", "medication"),
            ("I forgot the keys and went back for them.", "keys"),
            ("I left to collect my laptop.", "laptop"),
            ("I forgot my daughter's inhaler.", "inhaler"),
        ):
            atom = extract_departure_reason(text)
            self.assertIsNotNone(atom, text)
            self.assertIn(expect, atom["proposition"], text)
            self.assertNotIn("necessary item", atom["proposition"], text)

    def test_an_unnamed_object_still_reads_professionally(self):
        atom = extract_departure_reason("I forgot something and went back.")
        self.assertIsNotNone(atom)
        self.assertIn("necessary item", atom["proposition"])

    def test_grammar_words_are_not_mistaken_for_the_object(self):
        obj, _ = departure_reason_object("I left home to collect it")
        self.assertNotIn(obj, ("home", "it"))

    def test_the_proposition_never_echoes_a_module_id(self):
        for text in ("I forgot my purse.", "I left to collect my bag."):
            self.assertNotIn("KB-", departure_reason_proposition(text))


class PostcodeIsAskedOnlyWhenItChangesTheOutcome(unittest.TestCase):
    """The generic materiality rule does the suppressing, not a named rule."""

    def _case_without_jurisdiction(self):
        from pcn_appeal.models import (
            CaseFile, Fact, FactSource, FactStatus, SourceKind,
        )
        case = CaseFile("pc")
        for name, value in (
            ("operator_name", "Northgate Parking Ltd"), ("pcn_number", "NG1"),
            ("vrm", "KL55MNO"), ("parking_location", "Northgate Retail Park"),
            ("alleged_breach", "Overstayed maximum free period"),
            # Dates arrive here parsed, as they do from extraction.
            ("parking_event_date", datetime.date(2026, 6, 2)),
            ("notice_issue_date", datetime.date(2026, 6, 26)),
            ("operator_ata", "BPA"),
            ("driver_status", "UNIDENTIFIED"), ("notice_route", "POSTAL"),
        ):
            case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                          FactSource(SourceKind.DOCUMENT, "E1")))
        return case

    def test_no_unlock_is_reported_when_nothing_new_would_be_supported(self):
        """A module already supported without the postcode is not 'unlocked'."""
        from pcn_appeal.engines.recovery import postcode_unlocks
        from pcn_appeal.kg.graph import KnowledgeGraph

        kg = KnowledgeGraph()
        case = self._case_without_jurisdiction()

        class AlwaysSupported:
            """Stands for any module whose gates ignore jurisdiction."""
            def active_modules(self):
                from pcn_appeal.routes import Route
                import dataclasses
                return [dataclasses.replace(m, use_when={}, do_not_use_when={})
                        for m in kg.active_modules() if m.route == Route.POFA]

        unlocks = postcode_unlocks(case, AlwaysSupported())
        self.assertEqual(
            unlocks, [],
            "a ground that already applies cannot be unlocked by the postcode")

    def test_a_genuine_unlock_is_still_reported(self):
        from pcn_appeal.engines.recovery import postcode_unlocks
        from pcn_appeal.kg.graph import KnowledgeGraph

        case = self._case_without_jurisdiction()
        self.assertTrue(postcode_unlocks(case, KnowledgeGraph()),
                        "a late postal NTK in an unknown jurisdiction is a real delta")

    def test_the_ask_path_and_the_hold_path_use_one_predicate(self):
        """They had diverged; the hold path must not ask what the ask path spared."""
        import inspect

        from pcn_appeal.orchestrator import AppealPipeline
        src = inspect.getsource(AppealPipeline._hold_without_a_leading_ground)
        self.assertIn("_postcode_materiality", src)
        ask = inspect.getsource(AppealPipeline._site_postcode_question)
        self.assertIn("_postcode_materiality", ask)

    def test_a_known_jurisdiction_is_never_asked(self):
        from pcn_appeal.engines.recovery import postcode_unlocks
        from pcn_appeal.kg.graph import KnowledgeGraph
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind

        case = self._case_without_jurisdiction()
        case.put(Fact("F-j", "jurisdiction", "ENGLAND_WALES", FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "calc")))
        self.assertEqual(postcode_unlocks(case, KnowledgeGraph()), [])


if __name__ == "__main__":
    unittest.main()
