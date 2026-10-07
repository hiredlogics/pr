"""P8: every KB gate fact has a producer.

A module whose gate reads a fact nothing can produce can never fire, and
nothing fails until a customer's case goes silent. This test makes that a
build failure instead: add a module, and its gate facts must be producible.
"""
import unittest
from pathlib import Path

import yaml

from pcn_appeal.engines.derivation import DERIVED_FACTS

DATA = Path(__file__).resolve().parent.parent / "pcn_appeal" / "data"
SOURCES = {"DOCUMENT", "EVIDENCE", "DERIVED", "SYSTEM", "QUESTION", "OPERATOR"}
_FACT_OPS = {"is", "exists", "missing"}
_CMP_OPS = {"eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"}


def gate_facts(pred, out):
    if isinstance(pred, dict):
        for op, arg in pred.items():
            if op in _FACT_OPS:
                out.add(arg)
            elif op in _CMP_OPS:
                out.add(arg[0])
            elif op in ("all", "any"):
                for p in arg:
                    gate_facts(p, out)
            elif op == "not":
                gate_facts(arg, out)
    elif isinstance(pred, list):
        for p in pred:
            gate_facts(p, out)
    return out


def load(name):
    with open(DATA / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class FactProducers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb = load("kb_modules.yaml")
        mods = kb["modules"] if isinstance(kb, dict) else kb
        cls.gates = {}
        for m in mods:
            for f in gate_facts(m.get("use_when"), set()) | gate_facts(m.get("do_not_use_when"), set()):
                cls.gates.setdefault(f, []).append(m["module_id"])
        reg = load("fact_producers.yaml")
        cls.registry = reg.get("facts") or {}
        cls.derived_only = reg.get("derived_only") or []
        cls.questions = load("questions.yaml").get("questions") or {}

    def test_every_gate_fact_has_a_producer(self):
        orphans = {f: mods for f, mods in self.gates.items() if not self.registry.get(f)}
        self.assertEqual(orphans, {}, "gate facts with no producer (module can never fire)")

    def test_sources_are_known(self):
        for f, srcs in self.registry.items():
            self.assertTrue(set(srcs) <= SOURCES, f"{f}: {srcs}")

    def test_question_sources_exist_in_the_bank(self):
        missing = [f for f, s in self.registry.items() if "QUESTION" in s and f not in self.questions]
        self.assertEqual(missing, [])

    def test_derived_sources_match_the_derivation_engine(self):
        declared = {f for f, s in self.registry.items() if "DERIVED" in s} | set(self.derived_only)
        self.assertEqual(declared, set(DERIVED_FACTS))

    def test_registry_has_no_stale_entries(self):
        stale = [f for f in self.registry if f not in self.gates]
        self.assertEqual(stale, [], "registry names facts no gate reads; move to derived_only or delete")


if __name__ == "__main__":
    unittest.main()
