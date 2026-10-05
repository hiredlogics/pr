"""P17.10 — prompt-contract hardening, proved by behaviour.

Six contracts, each with a generic defect behind it:

1. semantic_extraction promised five channels but a reply could omit one, and an
   omission read downstream as "nothing material was found".
2. event_type / category were open strings, so the taxonomy grew one label per
   paraphrase; and a relationship could name an id the reply never emitted.
3. Normalization was free to abstract: the concept was extracted and the
   specific detail that explained the account could then be dropped.
4. case_analysis was told to decide "which candidate grounds the evidence
   ACTUALLY supports" from the raw account - duplicating the semantic layer and
   the deterministic KB gate, and inviting a ground the claim plan then vetoes.
5. SupportBundle carried `supporting_events`; the DraftPlan did not, so a ground
   could keep its facts and lose the order they happened in.
6. "If two sources disagree, leave the point out" was unqualified, so a
   conflicted critical identity field could be dropped from a released letter.

Nothing here names an operator, a location, a retailer or a phrase.
"""
from __future__ import annotations

import datetime
import json
import unittest

from pcn_appeal.engines.analysis import AnalysisEngine
from pcn_appeal.kg.graph import KnowledgeGraph
from pcn_appeal.llm import FakeLLM
from pcn_appeal.models import (
    CaseFile, Fact, FactSource, FactStatus, SourceKind,
)
from pcn_appeal.semantics.extract import (
    ALLOWED_ATOM_CATEGORIES, ALLOWED_EVENT_TYPES, ATOM_CATEGORY_FALLBACK,
    EVENT_TYPE_FALLBACK, PRODUCT_CHANNELS, extract_semantic_product,
)


class _Injected:
    """A model that answers semantic_extraction with exactly what it is given."""

    def __init__(self, product):
        self.product = product
        self.seen = None

    def complete_json(self, *, task, system, user, images=None):
        self.seen = json.loads(user)
        return self.product


def _product(texts=("The visit is described.",), **product):
    llm = _Injected(product)
    out = extract_semantic_product(list(texts), llm=llm)
    return out, llm


# --------------------------------------------------------------- 1. schema
class SemanticProductIsAlwaysComplete(unittest.TestCase):

    def test_every_channel_is_present_when_the_reply_omits_most_of_them(self):
        out, _ = _product(concepts=[])
        for key in PRODUCT_CHANNELS:
            self.assertIn(key, out, key)
            self.assertIsInstance(out[key], list, key)

    def test_an_absent_channel_is_recorded_rather_than_assumed_empty(self):
        out, _ = _product(concepts=[])
        codes = {e["code"] for e in out["schema_errors"]}
        self.assertIn("channel_absent_normalized_to_empty", codes)
        self.assertTrue(
            any("narrative_atoms" in e["detail"] for e in out["schema_errors"]),
            out["schema_errors"])

    def test_a_reply_that_is_not_an_object_does_not_crash_the_pipeline(self):
        class Odd:
            def complete_json(self, **kwargs):
                return ["not", "an", "object"]

        out = extract_semantic_product(["Something happened."], llm=Odd())
        for key in PRODUCT_CHANNELS:
            self.assertEqual(out[key], [], key)
        self.assertTrue(out["schema_errors"])

    def test_a_malformed_row_is_recorded_not_silently_discarded(self):
        out, _ = _product(
            events=["a bare string", {"polarity": "AFFIRMED"}],
            narrative_atoms=[None],
            relationships=[42],
            material_relevance=[{"relevant_to": ["TIMELINE"]}],
        )
        codes = {e["code"] for e in out["schema_errors"]}
        self.assertEqual(out["events"], [])
        self.assertIn("event_not_an_object", codes)
        self.assertIn("event_has_no_meaning", codes)
        self.assertIn("atom_not_an_object", codes)
        self.assertIn("relationship_not_an_object", codes)
        self.assertIn("relevance_source_invalid", codes)

    def test_the_schema_breach_reaches_the_case_audit(self):
        from pcn_appeal.semantics.extract import extract_and_promote

        case = CaseFile("sem-audit")
        extract_and_promote(case, ["The vehicle was present."],
                            llm=_Injected({"concepts": []}))
        rows = [a for a in case.audit if a.get("event") == "semantic_concepts"]
        self.assertTrue(rows)
        self.assertTrue(rows[-1]["semantic_schema_errors"], rows[-1])


class LocalIdsStayLocal(unittest.TestCase):
    """event_id / atom_id belong to one reply; a dangling edge points nowhere."""

    def test_a_relationship_between_two_emitted_events_survives(self):
        out, _ = _product(
            events=[
                {"event_id": "E1", "event_type": "DEPARTURE",
                 "description": "The vehicle departed the site during the visit.",
                 "polarity": "AFFIRMED", "attribution": "CUSTOMER"},
                {"event_id": "E2", "event_type": "RETURN",
                 "description": "The vehicle returned to the site later that day.",
                 "polarity": "AFFIRMED", "attribution": "CUSTOMER"},
            ],
            relationships=[{"source_id": "E1", "relationship": "PRECEDES",
                            "target_id": "E2"}],
        )
        self.assertEqual([e["event_type"] for e in out["events"]],
                         ["DEPARTURE", "RETURN"])
        self.assertEqual(out["relationships"],
                         [{"source_id": "E1", "relationship": "PRECEDES",
                           "target_id": "E2"}])

    def test_a_relationship_to_an_id_never_emitted_is_refused_and_recorded(self):
        out, _ = _product(
            events=[{"event_id": "E1", "event_type": "DEPARTURE",
                     "description": "The vehicle departed the site.",
                     "polarity": "AFFIRMED", "attribution": "CUSTOMER"}],
            relationships=[{"source_id": "E1", "relationship": "PRECEDES",
                            "target_id": "E9"}],
        )
        self.assertEqual(out["relationships"], [])
        self.assertIn("relationship_references_unemitted_id",
                      {e["code"] for e in out["schema_errors"]})


# ------------------------------------------------------------- 2. taxonomy
class TaxonomyIsStableWithoutLosingMeaning(unittest.TestCase):

    MEANING = ("The visit was interrupted by a site condition the customer "
               "could not control.")

    def test_an_unseen_event_label_falls_back_and_keeps_the_meaning(self):
        out, _ = _product(events=[{
            "event_id": "E1", "event_type": "SITE_CONDITION_INTERRUPTION",
            "description": self.MEANING,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER"}])
        self.assertEqual(out["events"][0]["event_type"], EVENT_TYPE_FALLBACK)
        self.assertEqual(out["events"][0]["description"], self.MEANING)
        self.assertIn("event_type_outside_taxonomy",
                      {e["code"] for e in out["schema_errors"]})

    def test_an_unseen_atom_label_becomes_unmapped_material_and_keeps_the_meaning(self):
        out, _ = _product(narrative_atoms=[{
            "atom_id": "A1", "category": "SITE_CONDITION_REASON",
            "proposition": self.MEANING,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER"}])
        atom = out["narrative_atoms"][0]
        self.assertEqual(atom["category"], ATOM_CATEGORY_FALLBACK)
        self.assertEqual(atom["proposition"], self.MEANING)
        self.assertIn("atom_category_outside_taxonomy",
                      {e["code"] for e in out["schema_errors"]})

    def test_a_known_label_is_never_relabelled(self):
        out, _ = _product(
            concepts=[],
            events=[{"event_id": "E1", "event_type": "PAYMENT",
                     "description": "A payment was attempted at the machine.",
                     "polarity": "AFFIRMED"}],
            narrative_atoms=[{"atom_id": "A1", "category": "DEPARTURE_REASON",
                              "proposition": "The departure had a stated reason.",
                              "polarity": "AFFIRMED"}],
            relationships=[], material_relevance=[])
        self.assertEqual(out["events"][0]["event_type"], "PAYMENT")
        self.assertEqual(out["narrative_atoms"][0]["category"], "departure_reason")
        self.assertEqual(out["schema_errors"], [])

    def test_the_model_is_told_the_closed_vocabularies(self):
        _, llm = _product(concepts=[], events=[], narrative_atoms=[],
                          relationships=[], material_relevance=[])
        self.assertEqual(set(llm.seen["allowed_event_types"]), set(ALLOWED_EVENT_TYPES))
        self.assertEqual(set(llm.seen["allowed_atom_categories"]),
                         set(ALLOWED_ATOM_CATEGORIES))
        self.assertEqual(llm.seen["event_type_fallback"], EVENT_TYPE_FALLBACK)

    def test_the_fallback_label_is_itself_in_the_vocabulary(self):
        self.assertIn(EVENT_TYPE_FALLBACK, ALLOWED_EVENT_TYPES)
        self.assertIn(ATOM_CATEGORY_FALLBACK, ALLOWED_ATOM_CATEGORIES)


# ------------------------------------------------------- 3. specific detail
class SpecificDetailSurvivesTheConcept(unittest.TestCase):
    """A broad concept classifies; the atom explains. Both must survive."""

    def test_the_concept_and_the_specific_atom_both_reach_the_product(self):
        specific = ("The visit was for a retail purchase at the premises served "
                    "by the car park.")
        out, _ = _product(
            texts=("I was buying things in the shop.",),
            concepts=[{"concept": "SHOPPING", "polarity": "AFFIRMED",
                       "attribution": "CUSTOMER", "source_text": "buying things",
                       "confidence": 0.9}],
            narrative_atoms=[{"atom_id": "A1", "category": "VISIT_PURPOSE",
                              "proposition": specific, "polarity": "AFFIRMED",
                              "attribution": "CUSTOMER"}])
        self.assertIn("SHOPPING", {c.concept for c in out["concepts"]})
        self.assertEqual(out["narrative_atoms"][0]["proposition"], specific)

    def test_the_purpose_object_outranks_the_place_departed_from(self):
        """"left <place> to collect <thing>" names the thing, not the place.

        Found end to end: the deterministic reader took the first noun after a
        movement verb, so the car park became the reason for leaving and the
        specific particular the account turned on was lost. Fixed by verb class
        (grammar), not by a list of places or things.
        """
        from pcn_appeal.engines.narrative import extract_departure_reason

        for text, expect, reject in (
            ("I left the retail park to collect my spectacles from home.",
             "spectacles", "retail park"),
            ("I departed the shopping centre to retrieve my parking permit.",
             "parking permit", "shopping centre"),
            ("I left the multi-storey to get my walking stick.",
             "walking stick", "multi-storey"),
        ):
            with self.subTest(text=text):
                atom = extract_departure_reason(text)
                self.assertIsNotNone(atom, text)
                self.assertIn(expect, atom["proposition"])
                self.assertNotIn(reject, atom["proposition"])
                self.assertNotIn("necessary item", atom["proposition"])

    def test_a_place_alone_is_not_treated_as_a_reason(self):
        from pcn_appeal.engines.narrative import extract_departure_reason

        self.assertIsNone(
            extract_departure_reason("I left the car park and came back later."))

    def test_a_thing_left_somewhere_is_still_a_reason(self):
        from pcn_appeal.engines.narrative import extract_departure_reason

        atom = extract_departure_reason("I left the keys at home so I went back.")
        self.assertIsNotNone(atom)
        self.assertIn("keys", atom["proposition"])

    def test_the_prompt_states_that_normalization_is_not_abstraction(self):
        from pcn_appeal import prompts
        body = prompts.system("semantic_extraction").lower()
        self.assertIn("not abstraction", body)
        self.assertIn("may coexist", body)


class PolarityIsNotFlattened(unittest.TestCase):
    """Offline reference path: a denial stays a denial, a doubt stays a doubt."""

    def test_an_explicit_denial_is_negated(self):
        out = extract_semantic_product(["I did not pay for parking at any point."])
        by = {c.concept: c.polarity for c in out["concepts"]}
        self.assertEqual(by.get("PAYMENT_MADE"), "NEGATED", by)

    def test_an_uncertain_account_is_uncertain(self):
        out = extract_semantic_product(
            ["I cannot remember whether a permit was displayed."])
        by = {c.concept: c.polarity for c in out["concepts"]}
        self.assertTrue(
            any(p == "UNCERTAIN" for p in by.values()),
            f"nothing was marked uncertain: {by}")
        self.assertNotIn("AFFIRMED", [by.get("PERMIT_DISPLAYED")])


class EquivalentMeaningResolvesEquivalently(unittest.TestCase):
    """Paraphrase stability, including wording never used while fixing this."""

    PAIRS = (
        ("I left the car park and came back a little later.",
         "I drove away from the site and returned a short time afterwards."),
        ("I tried to pay at the machine but it would not accept the payment.",
         "I attempted to pay on the terminal and it declined the transaction."),
        ("The engine would not start so the car could not be moved.",
         "The vehicle suffered a mechanical failure and would not restart."),
    )

    HOLDOUT = (
        "I stepped out of the retail park briefly and then came back for the rest "
        "of my errands.",
        "My registration was keyed in incorrectly at the terminal.",
        "A passenger was set down at the entrance before I drove on.",
    )

    def _concepts(self, text):
        return {c.concept for c in extract_semantic_product([text])["concepts"]
                if c.polarity == "AFFIRMED"}

    def test_paraphrases_of_the_same_meaning_share_their_concepts(self):
        for first, second in self.PAIRS:
            with self.subTest(first=first):
                a, b = self._concepts(first), self._concepts(second)
                self.assertTrue(a & b, f"{a} vs {b}")

    def test_unseen_wording_still_resolves_to_controlled_concepts(self):
        for text in self.HOLDOUT:
            with self.subTest(text=text):
                self.assertTrue(self._concepts(text), text)


# ------------------------------------------------------- 4/5/6. authority
def _notice_case(case_id="p1710", **extra) -> CaseFile:
    case = CaseFile(case_id)
    values = {
        "operator_name": "Northgate Parking Ltd",
        "pcn_number": "NG5512",
        "vrm": "KL55 MNO",
        "parking_location": "Northgate Retail Park",
        "site_postcode": "LS1 1AA",
        "alleged_breach": "Overstayed the maximum free period",
        "parking_event_date": datetime.date(2026, 6, 2),
        "notice_issue_date": datetime.date(2026, 7, 20),
        "operator_ata": "BPA",
        "entry_time": "10:04",
        "exit_time": "13:31",
        "driver_status": "UNIDENTIFIED",
        "notice_route": "POSTAL",
        "jurisdiction": "ENGLAND_WALES",
    }
    values.update(extra)
    for name, value in values.items():
        case.put(Fact(f"F-{name}", name, value, FactStatus.CONFIRMED,
                      FactSource(SourceKind.DOCUMENT, "E1")))
    return case


class EligibilityIsDecidedBeforeTheModelIsAsked(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.kg = KnowledgeGraph()

    def _resolve(self, case):
        from pcn_appeal.engines.module_resolver import KnowledgeModuleResolver
        engine = AnalysisEngine(self.kg, FakeLLM({}))
        resolved = KnowledgeModuleResolver(self.kg, retriever=None).resolve(
            case, fact_view=case.fact_view(), analysis_engine=engine)
        by_id = {m.module_id: m for m in self.kg.active_modules()}
        candidates = [by_id[mid] for mid in resolved.candidates if mid in by_id]
        return engine, resolved, candidates

    def test_a_gate_satisfied_candidate_is_reported_supported(self):
        case = _notice_case(multiple_visits=True, left_site=True,
                            returned_same_day=True)
        engine, resolved, candidates = self._resolve(case)
        elig = engine._eligibility(case, resolved, candidates, "SCOP-1.1")
        supported = {mid for mid, row in elig.items() if row["status"] == "SUPPORTED"}
        self.assertTrue(supported, elig)
        for mid in supported:
            module = self.kg.modules[mid]
            from pcn_appeal.rules.dsl import evaluate
            self.assertTrue(evaluate(module.use_when, resolved.fact_view), mid)
            self.assertFalse(evaluate(module.do_not_use_when, resolved.fact_view), mid)

    def test_an_unresolved_candidate_arrives_with_the_fact_it_is_missing(self):
        case = _notice_case()
        engine, resolved, candidates = self._resolve(case)
        elig = engine._eligibility(case, resolved, candidates, "SCOP-1.1")
        unresolved = {mid: row for mid, row in elig.items()
                      if row["status"] == "UNRESOLVED"}
        self.assertTrue(unresolved, elig)
        self.assertTrue(any(row["missing"] for row in unresolved.values()), unresolved)

    def test_no_candidate_is_offered_a_status_outside_the_vocabulary(self):
        case = _notice_case(multiple_visits=True)
        engine, resolved, candidates = self._resolve(case)
        elig = engine._eligibility(case, resolved, candidates, None)
        self.assertTrue(
            {row["status"] for row in elig.values()}
            <= {"SUPPORTED", "UNRESOLVED", "REJECTED", "BLOCKED"}, elig)

    def test_a_legal_defect_ground_is_not_supported_without_a_verified_finding(self):
        """The claim plan refuses it; the model is told so instead of proposing
        a ground that would then be vetoed in silence."""
        from pcn_appeal.legal import findings as legal_findings

        case = _notice_case()
        engine, resolved, candidates = self._resolve(case)
        elig = engine._eligibility(case, resolved, candidates, "SCOP-1.1")
        self.assertFalse(legal_findings.verified_types(case.legal_findings, []))
        for module in candidates:
            if not engine._needs_pofa_finding(module):
                continue
            self.assertNotEqual(elig[module.module_id]["status"], "SUPPORTED",
                                module.module_id)

    def test_the_candidate_payload_carries_the_decided_status(self):
        case = _notice_case(multiple_visits=True)
        engine, resolved, candidates = self._resolve(case)
        elig = engine._eligibility(case, resolved, candidates, "SCOP-1.1")
        payload = json.loads(engine._payload(
            case, "", resolved.fact_view, candidates, None, "SCOP-1.1",
            match=resolved.match, eligibility=elig))
        self.assertTrue(payload["candidates"])
        for row in payload["candidates"]:
            self.assertIn("eligibility", row)
            self.assertIn(row["eligibility"],
                          ("SUPPORTED", "UNRESOLVED", "REJECTED", "BLOCKED"))
        self.assertIn("deterministically", payload["eligibility_authority"])
        self.assertEqual(
            payload["circumstances_note"],
            "provenance/reference only; do not treat as factual authority")


class AModelProposalCannotCreateEligibility(unittest.TestCase):
    """Whatever the model returns, the deterministic veto decides."""

    @classmethod
    def setUpClass(cls):
        cls.kg = KnowledgeGraph()

    def _plan(self, case, proposed):
        from pcn_appeal.engines.claim_plan import build_claim_plan
        return build_claim_plan(
            case, self.kg, list(proposed), list(proposed), case.fact_view(),
            findings=[], code_version="SCOP-1.1",
            needs_pofa_finding=AnalysisEngine._needs_pofa_finding,
            needs_code_version=AnalysisEngine._needs_code_version,
        )

    def test_an_unresolved_module_proposed_as_a_ground_is_excluded(self):
        case = _notice_case()                       # no account facts at all
        plan = self._plan(case, ["KB-ANPR-01"])
        self.assertNotIn("KB-ANPR-01", plan.module_ids)
        self.assertEqual(
            [c["reason"] for c in plan.claims if c["module_id"] == "KB-ANPR-01"],
            ["use_when not satisfied"])

    def test_a_legal_defect_module_proposed_without_its_finding_is_excluded(self):
        case = _notice_case(pofa_finding="POFA_POSTAL_LATE")
        plan = self._plan(case, ["KB-POFA-02"])
        self.assertNotIn("KB-POFA-02", plan.module_ids)

    def test_a_blocked_module_is_suppressed_before_the_claim_plan(self):
        from pcn_appeal.engines.analysis import CaseAnalysis
        from pcn_appeal.engines.knowledge_matcher import KnowledgeMatcher

        case = _notice_case()
        engine = AnalysisEngine(self.kg, FakeLLM({}))
        result = CaseAnalysis()
        result.knowledge = KnowledgeMatcher(self.kg).match(case, case.fact_view())
        blocked = [c.module_id for c in result.knowledge.blocked]
        if not blocked:
            self.skipTest("no module is blocked on these facts")
        kept = engine._finalize_claims(
            case, [{"module_id": blocked[0]}], case.fact_view(), None,
            "SCOP-1.1", result)
        self.assertNotIn(blocked[0], kept)
        self.assertTrue(any(s["module_id"] == blocked[0] for s in result.suppressed))


class DraftPlanCarriesTheMaterialMeaning(unittest.TestCase):
    """SupportBundle → DraftSection: facts, events, atoms and particulars."""

    def _plan(self):
        from pcn_appeal.drafting.plan import build_draft_plan
        from pcn_appeal.models import RetrievalPack

        bundle = {
            "source_fact_ids": ["F-left_site", "F-returned_same_day"],
            "source_fact_names": ["left_site", "returned_same_day"],
            "derived_fact_ids": [], "derived_fact_names": [],
            "evidence_ids": [], "legal_finding_ids": [], "relationship_ids": [],
            "values": {"left_site": True, "returned_same_day": True,
                       "departure_reason": "their spectacles had been forgotten, "
                                           "prompting the departure"},
            "material_narrative_atoms": [{
                "atom_id": "A1", "category": "departure_reason",
                "proposition": "their spectacles had been forgotten, prompting "
                               "the departure",
                "polarity": "AFFIRMED", "attribution": "CUSTOMER"}],
            "supporting_events": [{
                "event_id": "E1", "event_type": "DEPARTURE",
                "description": "The vehicle left the site during the recorded "
                               "period and came back afterwards.",
                "polarity": "AFFIRMED", "attribution": "CUSTOMER"}],
            "required_particulars": ["left_site", "returned_same_day"],
        }
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[], module_ids=["KB-ANPR-01"],
            verified_facts={"left_site": True, "returned_same_day": True,
                            "multiple_visits": True},
            fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[],
            code_version="SCOP-1.1", pofa_route="POSTAL", pofa_findings=[],
            driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
            context_chunks=[], lease_clauses=[],
            claim_plan={"approved": ["KB-ANPR-01"],
                        "support_bundles": {"KB-ANPR-01": bundle},
                        "draft_requirements": {"KB-ANPR-01": {
                            "required_particulars": ["left_site",
                                                     "returned_same_day"]}}},
        )
        return build_draft_plan(pack, case_id="dp")

    def test_the_section_carries_events_atoms_particulars_and_values(self):
        section = self._plan().sections[0]
        self.assertEqual([e["event_id"] for e in section.supporting_events], ["E1"])
        self.assertTrue(section.material_atoms)
        self.assertEqual(section.material_atoms, section.narrative_atoms)
        self.assertIn("left_site", section.required_particulars)
        self.assertIn("departure_reason", section.particular_values)

    def test_the_section_purpose_asks_for_the_sequence_not_a_summary(self):
        purpose = self._plan().sections[0].purpose.lower()
        self.assertIn("event sequence", purpose)
        self.assertIn("do not paste customer wording", purpose)

    def test_a_professional_paraphrase_counts_as_expressed(self):
        from pcn_appeal.drafting.plan import particular_expressed

        value = "their spectacles had been forgotten, prompting the departure"
        paraphrase = ("The keeper's account is that the spectacles needed for the "
                      "journey had been forgotten, which is why the site was left.")
        self.assertTrue(particular_expressed(paraphrase, "departure_reason", value))

    def test_abstraction_does_not_count_as_expressed(self):
        from pcn_appeal.drafting.plan import particular_expressed

        value = "their spectacles had been forgotten, prompting the departure"
        abstracted = ("The vehicle was recorded entering and leaving the site on "
                      "the date in question.")
        self.assertFalse(particular_expressed(abstracted, "departure_reason", value))

    def test_the_object_the_customer_named_is_matched_from_the_value(self):
        """No list of nameable objects: an unseen one must work the same."""
        from pcn_appeal.drafting.plan import particular_expressed

        for obj in ("spectacles", "walking stick", "prescription", "phone charger"):
            value = f"their {obj} had been forgotten, prompting the departure"
            text = (f"The keeper's account is that their {obj} had been left "
                    f"behind, prompting the departure from the site.")
            with self.subTest(obj=obj):
                self.assertTrue(particular_expressed(text, "departure_reason", value))


class MaterialMeaningMustReachTheLetter(unittest.TestCase):
    """VAL-MATERIAL-FACT-COVERAGE: paraphrase passes, omission blocks."""

    EVENT = {
        "event_id": "E1", "event_type": "DEPARTURE",
        "description": "The vehicle left the site during the recorded period "
                       "and returned afterwards.",
        "polarity": "AFFIRMED", "attribution": "CUSTOMER",
    }

    def _check(self, sentence_text):
        from pcn_appeal.engines.draft_validation_engine import DraftValidationEngine
        from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

        bundle = {
            "source_fact_ids": ["F1"], "source_fact_names": ["left_site"],
            "derived_fact_ids": [], "derived_fact_names": [], "evidence_ids": [],
            "legal_finding_ids": [], "relationship_ids": [],
            "values": {"left_site": True},
            "material_narrative_atoms": [],
            "supporting_events": [self.EVENT],
            "required_particulars": ["left_site"],
        }
        plan = {
            "status": "LOCKED", "claim_plan_id": "CP", "approved": ["KB-ANPR-01"],
            "module_ids": ["KB-ANPR-01"], "labels": {"KB-ANPR-01": "ANPR"},
            "support_bundles": {"KB-ANPR-01": bundle},
            "draft_requirements": {"KB-ANPR-01": {
                "required_particulars": ["left_site"],
                "prohibited_content": [], "explanation_goal": []}},
        }
        pack = RetrievalPack(
            primary_route="ANPR", secondary_routes=[], module_ids=["KB-ANPR-01"],
            verified_facts={"left_site": True, "returned_same_day": True,
                            "multiple_visits": True},
            fact_refs={"left_site": "F1"}, missing_facts=[], evidence_refs=[],
            prohibited_claims=[], code_version="BPA_v9", pofa_route="POSTAL",
            pofa_findings=[], driver_status="UNIDENTIFIED",
            jurisdiction="ENGLAND_WALES", context_chunks=[], lease_clauses=[],
            case_context={"claim_plan": plan},
        )
        draft = Draft("mm", [
            [DraftSentence("I appeal as the registered keeper.", [], ["STRUCTURAL"], [])],
            [DraftSentence(sentence_text, ["F1"], ["KB-ANPR-01"], [])],
            [DraftSentence("I invite the operator to cancel the charge.", [],
                           ["STRUCTURAL"], [])],
        ])
        issues = DraftValidationEngine().check(draft, pack, {"F1"}).issues
        return {i.rule for i in issues}, issues

    def test_a_paraphrase_of_the_event_is_accepted(self):
        rules, issues = self._check(
            "The keeper's account is that the vehicle left the site within the "
            "recorded period and returned later the same day.")
        self.assertNotIn("VAL-MATERIAL-FACT-COVERAGE", rules, issues)

    def test_an_omitted_event_blocks_release(self):
        rules, issues = self._check(
            "The operator's records are put to strict proof.")
        self.assertIn("VAL-MATERIAL-FACT-COVERAGE", rules, issues)
        self.assertTrue(
            any("event(s) not expressed" in i.message
                for i in issues if i.rule == "VAL-MATERIAL-FACT-COVERAGE"),
            [i.message for i in issues])


class CriticalIdentityConflictBlocksUpstream(unittest.TestCase):
    """A conflicted critical field is held before the Claim Plan, never written
    around in the letter."""

    def _conflicted(self):
        from pcn_appeal.document_identity import (
            FieldObservation, attach_identity_state, build_identity_state,
        )

        def seen(value, method):
            return FieldObservation(value, value, "E1", 1, "FRONT", "",
                                    0.95, method, "", 1, "VERIFIED")

        case = _notice_case("identity-conflict")
        observations = {
            # Two document-backed readings that do not agree: the whole point of
            # the independent re-read is that this is detectable.
            "pcn_number": [seen("NG5512", "initial_extraction"),
                           seen("NG5513", "independent_verify")],
            "vrm": [seen("KL55 MNO", "initial_extraction"),
                    seen("KL55 MNO", "independent_verify")],
            "operator_name": [seen("Northgate Parking Ltd", "initial_extraction"),
                              seen("Northgate Parking Ltd", "independent_verify")],
        }
        state = build_identity_state(case, observations, revision=1)
        attach_identity_state(case, state)
        return case, state

    def test_the_conflict_is_recorded_as_a_document_identity_conflict(self):
        from pcn_appeal.document_identity import STATUS_CONFLICT

        _case, state = self._conflicted()
        self.assertEqual(state.field_status["pcn_number"], STATUS_CONFLICT)
        self.assertFalse(state.complete)

    def test_the_claim_plan_is_blocked_before_anything_is_drafted(self):
        from pcn_appeal.document_identity import identity_blocks_claim_plan

        case, _state = self._conflicted()
        block = identity_blocks_claim_plan(case)
        self.assertTrue(block)
        self.assertIn("DOCUMENT_IDENTITY_CONFLICT", block)

    def test_the_conflicted_fact_is_not_left_usable(self):
        from pcn_appeal.document_identity import apply_identity_to_facts

        case, state = self._conflicted()
        flags = apply_identity_to_facts(case, state)
        self.assertIn("identity_conflict:pcn_number", flags)
        self.assertFalse(case.facts["pcn_number"].usable)

    def test_the_drafter_is_not_told_to_omit_a_critical_field(self):
        import re

        from pcn_appeal import prompts

        body = re.sub(r"\s+", " ", prompts.system("drafting"))
        self.assertIn("release-critical document identity", body)
        self.assertIn("blocked before drafting", body)
        self.assertIn("non-critical optional detail", body)


class LegalAndAccountGroundsAreCumulative(unittest.TestCase):
    """The independent legal path must not move when the account path changes.

    End to end, because that is the property the client reported: a verified
    Schedule 4 defect and a supported customer-account ground have to survive
    together, and the account ground must not displace the calculated one.
    """

    FIELDS = dict(
        operator_name="Northgate Parking Ltd", pcn_number="NG889001",
        vrm="KL55 MNO", parking_location="Northgate Retail Park",
        site_postcode="LS1 1AA", parking_event_date="02/06/2026",
        notice_issue_date="20/07/2026", charge_amount="£100",
        alleged_breach="Overstayed the maximum free period",
        operator_ata="BPA", entry_time="10:04", exit_time="13:31",
    )

    def _run(self, narrative, answers=None):
        from pcn_appeal.models import EvidenceItem
        from pcn_appeal.orchestrator import AppealPipeline
        from support import ReferenceAnalysisLLM

        fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
                  for k, v in self.FIELDS.items()}
        llm = ReferenceAnalysisLLM({"extraction": [
            {"fields": fields, "doc_types": {"E1": "NTK"}}]})
        case = CaseFile("cumulative", evidence={
            "E1": EvidenceItem("E1", "NTK", "ntk.pdf",
                               text="Notice to Keeper ... keeper liability ...")})
        pipe = AppealPipeline(llm)
        pipe.ingest(case)
        pipe.confirm(case, {}, list(case.facts), narrative)
        pipe.answer(case, answers or {})
        return case, pipe.generate(case)

    @staticmethod
    def _legal(module_ids):
        return {m for m in module_ids if m.startswith("KB-POFA-")}

    @staticmethod
    def _account(module_ids):
        return {m for m in module_ids
                if not m.startswith(("KB-POFA-", "KB-LAND-", "KB-GOV-"))}

    def test_a_document_only_legal_ground_stands_on_its_own(self):
        _case, out = self._run("")
        self.assertTrue(self._legal(out.pack.module_ids),
                        f"no legal ground: {out.pack.module_ids} {out.pack.trace[-6:]}")
        self.assertTrue(out.pack.pofa_findings, out.pack.pofa_findings)

    def test_an_account_ground_is_added_without_displacing_the_legal_one(self):
        _, baseline = self._run("")
        legal_alone = self._legal(baseline.pack.module_ids)

        _case, out = self._run(
            "I left the retail park to collect my spectacles from home and came "
            "back a little later to finish my errands.")
        both = set(out.pack.module_ids)
        self.assertTrue(
            legal_alone <= both,
            f"account facts cost a legal ground: {sorted(legal_alone)} -> {sorted(both)}")
        self.assertTrue(
            self._account(both),
            f"no account ground survived: {sorted(both)}")
        self.assertTrue(out.pack.pofa_findings,
                        "the verified finding must not be weakened by the account")


if __name__ == "__main__":
    unittest.main()
