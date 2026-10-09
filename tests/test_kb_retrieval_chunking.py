"""KB chunking and semantic ground finding (client brief 2026-10-09 §2-§5).

Two separate jobs that must not be confused:

  RETRIEVAL    which approved modules are even LOOKED AT for this case. Should
               be generous: a customer who writes "I decided not to stay" must
               reach KB-CON-02 without using its wording.
  ELIGIBILITY  which of those may be ARGUED. Decided by `use_when` /
               `do_not_use_when` against verified facts, and nothing in the
               retrieval metadata can touch it.

The brief's §2 names the failure mode directly: "semantic similarity is not
legal support" and the nearest ground must never be used merely because
nothing better was found. So the load-bearing test here is not that retrieval
finds more - it is that finding more changes nothing about what is argued.

§4 is the other half: a module's retrieval text must carry its CONDITIONS, not
just its proposition, "because the AI could retrieve the proposition without
retrieving its restrictions".
"""
from __future__ import annotations

import unittest

from pcn_appeal import kb_retrieval as kr
from pcn_appeal.kg.graph import KnowledgeGraph


class TheMetadataIsRetrievalOnly(unittest.TestCase):

    def test_it_only_describes_modules_that_exist(self):
        """A phrase attached to no module is dead weight that will rot."""
        known = set(KnowledgeGraph().modules)
        self.assertTrue(known, "no modules loaded; the rest of this is vacuous")
        self.assertEqual(sorted(set(kr.load()) - known), [])

    def test_it_carries_no_facts_and_no_gates(self):
        """The one property that makes this file safe: it cannot express a
        condition, so it cannot create one."""
        legal_keys = {"use_when_facts", "do_not_use_when", "required_facts",
                      "facts", "gate", "supported", "eligibility", "verified_facts"}
        for module_id, meta in kr.load().items():
            with self.subTest(module=module_id):
                self.assertEqual(set(meta) - {"use_when", "must_check", "concepts"},
                                 set(), "unexpected key in retrieval-only metadata")
                self.assertFalse(set(meta) & legal_keys)

    def test_every_phrase_is_plain_customer_language(self):
        for module_id, meta in kr.load().items():
            for phrase in meta.get("concepts") or ():
                with self.subTest(module=module_id, phrase=phrase):
                    self.assertIsInstance(phrase, str)
                    self.assertTrue(phrase.strip())
                    self.assertNotIn("KB-", phrase, "a phrase must not cite a module")


class TheChunkKeepsAPropositionWithItsConditions(unittest.TestCase):
    """§4: USE WHEN and MUST CHECK travel with the proposition."""

    def setUp(self):
        self.kg = KnowledgeGraph()

    def test_the_conditions_are_in_the_text_the_module_is_found_on(self):
        checked = 0
        for module_id, meta in kr.load().items():
            module = self.kg.modules.get(module_id)
            if module is None:
                continue
            text = kr.retrieval_text(module)
            for part in ("use_when", "must_check"):
                if (meta.get(part) or "").strip():
                    with self.subTest(module=module_id, part=part):
                        self.assertIn(meta[part].strip(), text)
                    checked += 1
        self.assertGreater(checked, 40, "too few modules checked to mean anything")

    def test_the_proposition_is_still_there(self):
        for module_id in list(kr.load())[:15]:
            module = self.kg.modules.get(module_id)
            if module is None or not (module.core_proposition or "").strip():
                continue
            with self.subTest(module=module_id):
                self.assertIn(module.core_proposition.strip(),
                              kr.retrieval_text(module))

    def test_a_module_with_no_metadata_still_retrieves_on_its_own_text(self):
        """Modules absent from the file must not fall out of the corpus."""
        absent = [m for mid, m in self.kg.modules.items() if mid not in kr.load()]
        for module in absent[:10]:
            with self.subTest(module=module.module_id):
                self.assertTrue(kr.retrieval_text(module).strip())


class TheCustomersOwnWordsReachTheRightProposition(unittest.TestCase):
    """§1 and §10: the same meaning in unfamiliar wording must land in the
    same place. These are the brief's own example sentences."""

    SAME_MEANING = [
        "I read the sign and didn't agree, so I left.",
        "The terms weren't acceptable and I drove away.",
        "I looked at the conditions and decided not to stay.",
    ]

    def _retrieve(self, text, k=5):
        from pcn_appeal.rag.retriever import Doc, HybridRetriever
        kg = KnowledgeGraph()
        corpus = [Doc(m.module_id, kr.retrieval_text(m), {"module_id": m.module_id})
                  for m in kg.modules.values()]
        hits = HybridRetriever(corpus).search(text, k=k)
        return [h.doc.doc_id if hasattr(h, "doc") else h.doc_id for h in hits]

    def test_each_phrasing_finds_the_no_contract_proposition(self):
        for sentence in self.SAME_MEANING:
            with self.subTest(sentence=sentence):
                self.assertIn("KB-CON-02", self._retrieve(sentence))

    def test_the_three_phrasings_agree_with_each_other(self):
        """Not just "each finds it" - they must converge, which is what makes
        behaviour on unseen wording predictable."""
        found = [set(self._retrieve(s)) for s in self.SAME_MEANING]
        common = set.intersection(*found)
        self.assertIn("KB-CON-02", common)

    def test_a_short_badly_written_account_still_reaches_it(self):
        for scrap in ("never parked", "didnt agree with sign so left",
                      "no contract i left straight away"):
            with self.subTest(scrap=scrap):
                self.assertIn("KB-CON-02", self._retrieve(scrap, k=8))


class ReachIsNotSupport(unittest.TestCase):
    """§2, the safety property. Retrieval finding a module must never be what
    decides it may be argued."""

    def test_a_retrieved_module_whose_conditions_fail_is_not_argued(self):
        """A payment/keying case retrieves the no-contract proposition, because
        the words overlap. Eligibility must still reject it."""
        from pcn_appeal.rules.dsl import evaluate3
        kg = KnowledgeGraph()
        module = kg.modules.get("KB-CON-02")
        self.assertIsNotNone(module, "KB-CON-02 missing; test is vacuous")
        paid = {"payment_made": True, "vrm_keyed_incorrectly": True,
                "no_parking_took_place": False}
        self.assertIsNot(evaluate3(module.use_when, paid), True,
                         "a paid-and-parked case must not satisfy KB-CON-02")

    def test_a_concept_phrase_cannot_satisfy_a_gate(self):
        """The direct form of the brief's prohibition: the retrieval vocabulary
        is not readable by the rule evaluator at all."""
        from pcn_appeal.rules.dsl import evaluate3
        kg = KnowledgeGraph()
        for module_id in ("KB-CON-02", "KB-PAY-01"):
            module = kg.modules.get(module_id)
            if module is None or not module.use_when:
                continue
            facts = {p: True for p in kr.concepts_for(module_id)}
            with self.subTest(module=module_id):
                self.assertIsNot(evaluate3(module.use_when, facts), True,
                                 "concept phrases satisfied a legal gate")


if __name__ == "__main__":
    unittest.main()
