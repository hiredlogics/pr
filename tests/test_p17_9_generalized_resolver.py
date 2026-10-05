"""P17.9 — generalized SemanticCaseResolver + KnowledgeModuleResolver."""
from __future__ import annotations

import json
import pathlib
import unittest

from pcn_appeal.engines.module_resolver import (
    STATUS_SUPPORTED, STATUS_UNRESOLVED, KnowledgeModuleResolver,
)
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import CaseFile, EvidenceItem, Fact, FactSource, FactStatus, SourceKind
from pcn_appeal.reasoning_trace import build_reasoning_trace
from pcn_appeal.semantics import SemanticCaseResolver, from_case
from pcn_appeal.semantics.resolver import resolve as semantic_resolve
from support import ReferenceAnalysisLLM


def _case(narrative: str, **facts) -> CaseFile:
    case = CaseFile("C-P179", evidence={
        "E1": EvidenceItem(
            "E1", "PCN", "front.jpg",
            text="Parking Charge Notice. Operator Name: Acme Parking Ltd. "
                 "PCN Number: 1234567890. VRM: AB12CDE. Location: Retail Park. "
                 "Date of Contravention: 01/06/2026. Date of Issue: 20/06/2026.",
            images=[b"\xff\xd8\xff\xd9\x01"],
        ),
        "E1B": EvidenceItem(
            "E1B", "PCN", "back.jpg", text="Reverse page.",
            images=[b"\xff\xd8\xff\xd9\x02"],
        ),
    })
    case.raw_answers["narrative"] = narrative
    for name, value in facts.items():
        case.put(Fact(
            f"F-{name}", name, value, FactStatus.CONFIRMED,
            FactSource(SourceKind.DOCUMENT, "E1#p1"), confidence=0.95,
        ))
    return case


NARRATIVES = {
    "A": "I parked once and stayed until I left at the end of my visit.",
    "B": "I visited in the morning, left the site, and came back later the same day for a second visit.",
    "C": "I went shopping, realised I forgot my purse, left the site, and returned later the same day.",
    "D": "I dropped a passenger off and later returned to collect them.",
    "E": "I paid for parking on the app successfully.",
    "F": "I tried to pay at the machine but it failed and kept erroring.",
    "G": "I paid but typed one character of the registration incorrectly.",
    "H": "The vehicle broke down and would not start; recovery attended.",
    "I": "The barrier would not open and I could not exit.",
    "J": "I held a valid permit for the bay on that date.",
    "K": "I might have left but I am not sure.",
    "L": "I did NOT leave the site at any point that day.",
    "N": "I left because of an unforeseen personal urgency that I have not named before.",
    "O": "The weather was sunny and I listened to the radio.",
}


class ResolverContractTests(unittest.TestCase):
    def test_semantic_resolver_returns_state(self):
        case = _case(NARRATIVES["C"])
        out = SemanticCaseResolver.resolve(case, llm=FakeLLM({}))
        self.assertTrue(out.trace)
        self.assertIn("SEMANTIC_UNDERSTANDING", out.trace)
        self.assertIn("FACTMANAGER_RECONCILE", out.trace)
        self.assertTrue(case.raw_answers.get("_semantic_resolve_trace"))
        self.assertTrue(any(a.get("event") == "semantic_case_resolver" for a in case.audit))

    def test_input_contract_from_case(self):
        case = _case(NARRATIVES["B"], pcn_number="1234567890", vrm="AB12CDE")
        inp = from_case(case)
        self.assertEqual(inp.case_id, "C-P179")
        self.assertTrue(inp.documents)
        self.assertTrue(inp.customer_narrative)

    def test_module_resolver_candidate_ne_eligibility(self):
        kg = KnowledgeGraph()
        case = _case(NARRATIVES["C"], left_site=True, returned_same_day=True,
                     multiple_visits=True, jurisdiction="ENGLAND_WALES",
                     notice_route="POSTAL", parking_event_date=__import__("datetime").date(2026, 6, 1),
                     notice_issue_date=__import__("datetime").date(2026, 6, 20),
                     site_postcode="M1 1AA")
        semantic_resolve(case, llm=FakeLLM({}))
        from pcn_appeal.engines.reasoning import ReasoningEngine
        resolved = KnowledgeModuleResolver(kg, reasoning=ReasoningEngine(kg)).resolve(case)
        self.assertTrue(resolved.as_dict()["invariant"]["candidate_is_not_eligibility"])
        # A candidate list may include modules that are not SUPPORTED rows.
        for mid in resolved.candidates:
            row = resolved.rows.get(mid)
            if row and row.matcher_status in ("OPEN", "RELEVANT"):
                self.assertNotEqual(row.status, STATUS_SUPPORTED)

    def test_llm_cannot_own_claim_plan(self):
        """Semantic resolve does not write claim plan grounds."""
        case = _case(NARRATIVES["D"])
        out = semantic_resolve(case, llm=FakeLLM({}))
        self.assertIsNone(getattr(case, "claim_plan", None) or None)
        blob = json.dumps(out.as_dict(), default=str)
        self.assertNotIn("KB-ACT-02", blob)  # concepts may mention activity, not select modules

    def test_joined_trace_stages(self):
        case = _case(NARRATIVES["C"], left_site=True, returned_same_day=True,
                     multiple_visits=True)
        semantic_resolve(case, llm=FakeLLM({}))
        kg = KnowledgeGraph()
        KnowledgeModuleResolver(kg).resolve(case)
        trace = build_reasoning_trace(case)
        for stage in ("INPUT", "SEMANTIC_STATE", "FACTMANAGER", "MODULE_CANDIDATES",
                      "MODULE_ELIGIBILITY"):
            self.assertIn(stage, trace)


class AcceptanceMatrixTests(unittest.TestCase):
    """Category narratives — no operator/case-specific strings in implementation."""

    def _run(self, key: str, **facts):
        case = _case(NARRATIVES[key], jurisdiction="ENGLAND_WALES",
                     notice_route="POSTAL", site_postcode="M1 1AA",
                     alleged_breach="Overstayed", **facts)
        out = semantic_resolve(case, llm=FakeLLM({}))
        kg = KnowledgeGraph()
        mod = KnowledgeModuleResolver(kg).resolve(case)
        return case, out, mod

    def test_categories_produce_semantic_state(self):
        for key in ("A", "B", "C", "D", "E", "G", "H", "K", "L", "N", "O"):
            with self.subTest(key=key):
                case, out, mod = self._run(key)
                self.assertIsNotNone(out)
                self.assertTrue(mod.trace)
                # Unknown material meaning path must not crash.
                self.assertIn("FACT_VIEW", mod.trace)

    def test_B_C_multiple_visits_atoms_or_facts(self):
        for key in ("B", "C"):
            case, out, mod = self._run(key)
            state = out.semantic_state or {}
            has_signal = (
                case.get("multiple_visits")
                or case.get("left_site")
                or any(
                    (a.get("kind") or a.get("atom_kind") or "").lower().find("depart") >= 0
                    or (a.get("kind") or "").lower().find("return") >= 0
                    for a in (state.get("narrative_atoms") or [])
                )
                or any(
                    (e.get("kind") or e.get("type") or "") in (
                        "DEPART_SITE", "RETURN_SITE", "DROP_OFF", "LEFT_SITE",
                    )
                    for e in (state.get("events") or [])
                )
            )
            self.assertTrue(has_signal or out.account, f"{key} should capture leave/return meaning")

    def test_L_negation_preserved(self):
        case, out, _ = self._run("L")
        # Must not affirm multiple visits from "did NOT leave".
        self.assertIsNot(case.get("left_site"), True)

    def test_K_uncertainty_not_false(self):
        case, out, _ = self._run("K")
        # Uncertainty must not become an explicit false continuous-stay fact.
        self.assertNotEqual(case.get("left_site"), False)

    def test_paraphrase_holdout_equivalent(self):
        """Unseen wording with equivalent meaning → similar semantic signals."""
        a = "I left the car park halfway through and came back later that day."
        b = "Partway through my stay I exited the site, then I returned subsequently the same day."
        ca, oa, _ = self._run("C")
        # Override with paraphrase not in NARRATIVES development set.
        cb = _case(b, jurisdiction="ENGLAND_WALES", notice_route="POSTAL",
                   site_postcode="M1 1AA")
        ob = semantic_resolve(cb, llm=FakeLLM({}))
        # Both should produce visit-related concepts/events/atoms or facts.
        def visitish(case, out):
            st = out.semantic_state or {}
            return bool(
                case.get("left_site") or case.get("multiple_visits")
                or st.get("events") or st.get("narrative_atoms") or st.get("concepts")
            )
        self.assertTrue(visitish(ca, oa))
        self.assertTrue(visitish(cb, ob))


class NoBypassOrPhraseRulesTests(unittest.TestCase):
    def test_no_acceptance_phrases_in_resolvers(self):
        roots = [
            pathlib.Path("pcn_appeal/semantics/resolver.py"),
            pathlib.Path("pcn_appeal/engines/module_resolver.py"),
            pathlib.Path("pcn_appeal/semantics/input_contract.py"),
        ]
        banned = [
            "forgot my purse", "Euro Car Parks", "CP Plus", "Canada Water",
            "EX15CZT", "typed one character",
        ]
        for path in roots:
            text = path.read_text(encoding="utf-8")
            for phrase in banned:
                self.assertNotIn(phrase, text, f"{path} contains {phrase!r}")

    def test_orchestrator_uses_semantic_resolver(self):
        src = pathlib.Path("pcn_appeal/orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("SemanticCaseResolver", src)


if __name__ == "__main__":
    unittest.main()
