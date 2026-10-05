"""P18 closure — the four behaviour gaps the 10-case acceptance run exposed.

Each class is one reproduced failure, not a refactor:

1. An account that part-satisfied a module's gate produced neither a ground nor
   a question: the breakdown and broken-terminal accounts both died silently.
   A question is material when the customer raised that route themselves; it is
   not material when the route is already argued, nor when the fact that raised
   it gates several routes (someone who says they paid is not asked what kind
   of keying error they made).
2. The customer's atoms and events were attached to EVERY ground, so a
   statutory-timing paragraph was required to express a shopping trip and the
   validator blocked release when it did not.
3. A locked claim plan is frozen, so its bundles arrive as read-only Mappings.
   `isinstance(row, dict)` then dropped every atom and event on the way into
   the DraftPlan: a bundle carrying eight particulars produced a section
   carrying none. Merging two grounds into one paragraph dropped them too.
4. A fallback semantic run was indistinguishable from a successful live one.

Nothing here names an operator, a site, a retailer or a phrase.
"""
from __future__ import annotations

import json
import unittest

from pcn_appeal.drafting.plan import _merge_group, build_draft_plan
from pcn_appeal.drafting.support_contract import SupportBundle, build_bundle
from pcn_appeal.models import CaseFile, EvidenceItem, FactStatus
from pcn_appeal.orchestrator import AppealPipeline
from pcn_appeal.semantics.extract import (
    SEMANTIC_FALLBACK, SEMANTIC_LIVE, extract_and_promote,
    extract_semantic_product,
)

NOTICE = dict(
    operator_name="Northgate Parking Ltd", pcn_number="NG778899", vrm="KL55MNO",
    parking_location="Northgate Retail Park", site_postcode="LS2 7AA",
    parking_event_date="02/06/2026", notice_issue_date="08/06/2026",
    charge_amount="£100", alleged_breach="Overstayed maximum free period",
    operator_ata="BPA", entry_time="09:10", exit_time="12:37",
    jurisdiction="ENGLAND_WALES",
)


def _semantic(concepts=(), events=(), atoms=(), relationships=(), relevance=()):
    return {"concepts": list(concepts), "events": list(events),
            "narrative_atoms": list(atoms), "relationships": list(relationships),
            "material_relevance": list(relevance)}


def _c(concept, polarity="AFFIRMED", src=""):
    return {"concept": concept, "polarity": polarity, "attribution": "CUSTOMER",
            "source_text": src, "confidence": 0.9}


def _ev(eid, etype, desc):
    return {"event_id": eid, "event_type": etype, "description": desc,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": desc, "confidence": 0.9}


def _at(aid, category, proposition):
    return {"atom_id": aid, "category": category, "proposition": proposition,
            "polarity": "AFFIRMED", "attribution": "CUSTOMER",
            "source_text": proposition, "confidence": 0.9}


class _Injected:
    """A stand-in model whose semantic reading is supplied by the test."""

    def __init__(self, product, inner=None):
        self.product, self.inner = product, inner
        self.draft_payload: dict = {}

    def complete_json(self, *, task, system, user, images=None):
        if task == "drafting":
            try:
                self.draft_payload = json.loads(user)
            except Exception:
                self.draft_payload = {}
        if task == "semantic_extraction":
            return self.product
        return self.inner.complete_json(task=task, system=system, user=user,
                                        images=images)


def _run(narrative, semantic, doc=None, answers=None, rounds=4):
    """One case end to end through the real pipeline. JSON fixtures, no OCR."""
    from support import ReferenceAnalysisLLM

    fields = {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
              for k, v in dict(NOTICE, **(doc or {})).items()}
    inner = ReferenceAnalysisLLM({"extraction": [
        {"fields": fields, "doc_types": {"E1": "PCN"}}]})
    llm = _Injected(semantic, inner)
    case = CaseFile("p18", evidence={
        "E1": EvidenceItem("E1", "PCN", "pcn.pdf", text="Parking Charge Notice")})
    pipe = AppealPipeline(llm)
    pipe.ingest(case)
    confirmed = [n for n, f in case.facts.items()
                 if f.status == FactStatus.EXTRACTED]
    questions = pipe.confirm(case, {}, confirmed, narrative)
    asked = [[q.get("fact") for q in questions]]
    for _ in range(rounds):
        if not questions:
            break
        batch = {q["fact"]: (answers or {})[q["fact"]] for q in questions
                 if q.get("fact") in (answers or {})}
        if not batch:
            break
        questions = pipe.answer(case, batch)
        asked.append([q.get("fact") for q in questions])
    return case, pipe, llm, asked


# ------------------------------------------------- 1. the account raises a topic
BREAKDOWN = _semantic(
    concepts=[_c("BROKEN_DOWN", src="would not restart"),
              _c("IMMOBILISED", src="waited for recovery")],
    events=[_ev("E1", "MECHANICAL", "The vehicle would not restart"),
            _ev("E2", "DELAY", "Waited for recovery assistance")],
    atoms=[_at("A1", "mechanical",
               "The vehicle could not be restarted when the customer tried to leave"),
           _at("A2", "timing",
               "The additional time was spent waiting for recovery")],
)
BREAKDOWN_TEXT = ("The car would not restart when I tried to leave. I waited "
                  "for recovery, which is why the vehicle stayed longer.")


class AnAccountThatRaisesATopicIsAsked(unittest.TestCase):
    """One answerable fact short of a ground the customer described."""

    def test_the_remaining_gate_of_a_part_satisfied_module_is_asked(self):
        _case, _pipe, _llm, asked = _run(BREAKDOWN_TEXT, BREAKDOWN)
        self.assertIn("immobilisation_prevented_departure", asked[0],
                      f"rounds={asked}")

    def test_answering_it_reaches_the_ground_the_account_described(self):
        case, pipe, _llm, asked = _run(
            BREAKDOWN_TEXT, BREAKDOWN,
            answers={"immobilisation_prevented_departure": True,
                     "immobilisation_cause": "FLAT_BATTERY",
                     "recovery_attended": True})
        out = pipe.generate(case)
        self.assertTrue([m for m in out.pack.module_ids if m.startswith("KB-BREAK-")],
                        f"{out.pack.module_ids} after {asked}")

    def test_a_route_already_argued_is_not_asked_about_again(self):
        """The answer cannot change the outcome, so the question is not material."""
        case, pipe, _llm, asked = _run(
            BREAKDOWN_TEXT, BREAKDOWN,
            answers={"immobilisation_prevented_departure": True})
        later = [r for r in asked[1:] if r]
        self.assertEqual(
            [f for r in later for f in r if str(f).startswith("immobilisation")],
            [], f"rounds={asked}")

    def test_a_gate_shared_across_routes_raises_nothing(self):
        """Saying a payment was made is not raising a keying error."""
        paid = _semantic(concepts=[_c("PAYMENT_MADE", src="I paid at the machine")])
        _case, _pipe, _llm, asked = _run("I paid at the machine before I walked off",
                                         paid)
        self.assertNotIn("keying_error_type", [f for r in asked for f in r],
                         f"rounds={asked}")


# --------------------------------------------- 2. particulars belong to a ground
LEGAL_PLUS_ACCOUNT = _semantic(
    concepts=[_c("COLLECTION", src="collecting a prescription"),
              _c("LEFT_SITE", src="left the site"),
              _c("RETURNED", src="returned that afternoon"),
              _c("MULTIPLE_VISITS", src="returned that afternoon")],
    events=[_ev("E1", "ACTIVITY", "Collected a prescription for a relative"),
            _ev("E2", "DEPARTURE", "Left the site straight afterwards"),
            _ev("E3", "RETURN", "Returned later that afternoon")],
    atoms=[_at("A1", "visit_purpose",
               "The first attendance was to collect a prescription for a relative"),
           _at("A2", "return_event",
               "The later attendance was to take the relative home")],
)
ACCOUNT_TEXT = ("I was collecting a prescription for a relative, left the site "
                "straight afterwards and returned later that afternoon.")


class ParticularsBelongToTheGroundTheyExplain(unittest.TestCase):
    """A statutory-timing ground is not particularised by a shopping trip."""

    @classmethod
    def setUpClass(cls):
        cls.case, pipe, cls.llm, _asked = _run(
            ACCOUNT_TEXT, LEGAL_PLUS_ACCOUNT,
            doc={"notice_issue_date": "26/06/2026"})      # late NTK
        cls.out = pipe.generate(cls.case)
        cls.sections = ((cls.llm.draft_payload.get("draft_plan") or {})
                        .get("sections") or [])

    def _section(self, prefix):
        for s in self.sections:
            if any(str(g).startswith(prefix) for g in s.get("ground_ids") or []):
                return s
        return None

    def _bundle(self, module_id):
        plan = dict(self.out.pack.claim_plan or {})
        return dict((plan.get("support_bundles") or {}).get(module_id) or {})

    def test_both_streams_survive_together(self):
        ids = list(self.out.pack.module_ids)
        self.assertTrue([m for m in ids if m.startswith("KB-POFA-")], ids)
        self.assertTrue([m for m in ids if not m.startswith("KB-POFA-")], ids)
        self.assertTrue(self.out.pack.pofa_findings)

    def test_the_legal_grounds_bundle_carries_no_customer_particulars(self):
        legal = next((m for m in self.out.pack.module_ids
                      if m.startswith("KB-POFA-")), None)
        self.assertIsNotNone(legal)
        bundle = self._bundle(legal)
        self.assertEqual(list(bundle.get("material_narrative_atoms") or []), [])
        self.assertEqual(list(bundle.get("supporting_events") or []), [])

    def test_the_account_grounds_bundle_does_carry_them(self):
        account = next((m for m in self.out.pack.module_ids
                        if not m.startswith("KB-POFA-")), None)
        self.assertIsNotNone(account)
        bundle = self._bundle(account)
        self.assertTrue(bundle.get("material_narrative_atoms"),
                        "the ground resting on the account lost its particulars")

    def test_the_legal_section_is_not_asked_to_express_the_account(self):
        section = self._section("KB-POFA-")
        self.assertIsNotNone(section, [s.get("ground_ids") for s in self.sections])
        self.assertEqual(
            [a for a in (section.get("narrative_atoms") or [])
             if (a or {}).get("proposition")], [])

    def test_the_account_section_is(self):
        section = next((s for s in self.sections
                        if not any(str(g).startswith("KB-POFA-")
                                   for g in s.get("ground_ids") or [])), None)
        self.assertIsNotNone(section)
        props = [(a or {}).get("proposition")
                 for a in (section.get("narrative_atoms")
                           or section.get("material_atoms") or [])]
        self.assertTrue([p for p in props if p], section.get("ground_ids"))

    def test_no_validation_issue_blames_the_legal_ground_for_the_account(self):
        legal = next((m for m in self.out.pack.module_ids
                      if m.startswith("KB-POFA-")), None)
        offending = [i.message for i in self.out.validation.issues
                     if i.rule == "VAL-MATERIAL-FACT-COVERAGE"
                     and f"ground_id={legal}" in i.message
                     and ("atom(s)" in i.message or "event(s)" in i.message)]
        self.assertEqual(offending, [])


# ------------------------------------------------- 3. frozen rows are still rows
class FrozenBundleRowsStillReachTheDraftPlan(unittest.TestCase):
    """A locked plan is read-only; its particulars must survive the trip."""

    ATOM = {"atom_id": "A1", "category": "visit_purpose",
            "proposition": "The attendance was to collect a prescription",
            "polarity": "AFFIRMED"}
    EVENT = {"event_id": "E1", "event_type": "RETURN",
             "description": "Returned later that afternoon"}

    class Pack:
        module_ids = ["KB-ANPR-01"]
        context_chunks: list = []
        legal_findings: list = []
        prohibited_claims: list = []
        facts: dict = {"multiple_visits": True}
        case_context: dict = {}

        def __init__(self, bundle):
            self.claim_plan = {"approved": ["KB-ANPR-01"],
                               "support_bundles": {"KB-ANPR-01": bundle}}

    def _frozen(self, mapping):
        from types import MappingProxyType
        return MappingProxyType(dict(mapping))

    def test_a_mapping_bundle_is_read_like_a_dict(self):
        bundle = self._frozen({
            "source_fact_ids": ["F1"], "source_fact_names": ["multiple_visits"],
            "required_particulars": ["multiple_visits"],
            "material_narrative_atoms": [self._frozen(self.ATOM)],
            "supporting_events": [self._frozen(self.EVENT)],
        })
        plan = build_draft_plan(self.Pack(bundle), case_id="p18")
        section = plan.sections[0]
        self.assertEqual([a.get("proposition") for a in section.material_atoms],
                         [self.ATOM["proposition"]])
        self.assertEqual([e.get("description") for e in section.supporting_events],
                         [self.EVENT["description"]])

    def test_merging_two_grounds_keeps_both_sets_of_particulars(self):
        one = SupportBundle(("F1",), (), (), (), (), ("payment_made",), (), {},
                            ({"atom_id": "A1", "proposition": "A payment was made"},),
                            ({"event_id": "E1", "description": "Paid at the machine"},),
                            ("payment_made",))
        two = SupportBundle(("F2",), (), (), (), (), ("keying_error_type",), (), {},
                            ({"atom_id": "A2", "proposition": "One character was mistyped"},),
                            ({"event_id": "E2", "description": "Mistyped the registration"},),
                            ("payment_made",))
        bundles = {"KB-PAY-01": one.as_dict(), "KB-KEY-01": two.as_dict()}
        groups = _merge_group(["KB-PAY-01", "KB-KEY-01"], bundles)
        self.assertEqual(groups, [["KB-PAY-01", "KB-KEY-01"]])

        class Pack(FrozenBundleRowsStillReachTheDraftPlan.Pack):
            module_ids = ["KB-PAY-01", "KB-KEY-01"]
            facts = {"payment_made": True, "keying_error_type": "MINOR"}

            def __init__(self):
                self.claim_plan = {"approved": list(self.module_ids),
                                   "support_bundles": bundles}

        plan = build_draft_plan(Pack(), case_id="p18")
        props = {a.get("proposition") for s in plan.sections
                 for a in s.material_atoms}
        self.assertEqual(props, {"A payment was made", "One character was mistyped"})

    def test_a_caller_with_a_row_is_not_overridden_by_the_case(self):
        """An empty list from the resolver means "not this ground"."""
        case = CaseFile("p18-bundle")
        case.raw_answers["_semantic_case_state"] = json.dumps(
            {"narrative_atoms": [self.ATOM], "events": [self.EVENT]})
        decided = build_bundle(
            [{"fact": "notice_issue_date", "value": "2026-06-26",
              "fact_id": "F1"}], case=case, semantic_from_case=False)
        self.assertEqual(list(decided.material_narrative_atoms), [])
        undecided = build_bundle(
            [{"fact": "notice_issue_date", "value": "2026-06-26",
              "fact_id": "F1"}], case=case)
        self.assertTrue(undecided.material_narrative_atoms)


# ------------------------------------------------------- 4. LIVE vs FALLBACK
class _Raises:
    def __init__(self, exc):
        self.exc = exc

    def complete_json(self, *, task, system, user, images=None):
        raise self.exc


class TheReadingThatProducedTheProductIsNamed(unittest.TestCase):
    """A fallback run is not live-AI acceptance, and must never look like it."""

    TEXT = "I left the site and came back later that day."

    def test_a_model_that_answers_is_recorded_live(self):
        out = extract_semantic_product(
            [self.TEXT], llm=_Injected(_semantic(concepts=[_c("LEFT_SITE")])))
        self.assertEqual(out["semantic_mode"], SEMANTIC_LIVE)
        self.assertEqual(out["fallback_reason"], "")
        self.assertEqual(out["exception_class"], "")

    def test_a_provider_fault_is_recorded_with_its_class(self):
        out = extract_semantic_product(
            [self.TEXT], llm=_Raises(TimeoutError("semantic provider timeout")))
        self.assertEqual(out["semantic_mode"], SEMANTIC_FALLBACK)
        self.assertEqual(out["fallback_reason"], "semantic_provider_call_failed")
        self.assertEqual(out["exception_class"], "TimeoutError")

    def test_no_provider_at_all_is_recorded_as_such(self):
        out = extract_semantic_product([self.TEXT])
        self.assertEqual(out["semantic_mode"], SEMANTIC_FALLBACK)
        self.assertEqual(out["fallback_reason"],
                         "no_semantic_provider_configured")

    def test_the_mode_reaches_the_case_audit_with_the_run_it_belongs_to(self):
        case = CaseFile("p18-mode")
        extract_and_promote(case, [self.TEXT],
                            llm=_Raises(ValueError("bad json")))
        rows = [a for a in case.audit if a.get("event") == "semantic_concepts"]
        self.assertTrue(rows)
        row = rows[-1]
        self.assertEqual(row["semantic_mode"], SEMANTIC_FALLBACK)
        self.assertEqual(row["exception_class"], "ValueError")
        self.assertEqual(row["fallback_reason"], "semantic_provider_call_failed")
        self.assertIn("run_id", row)
        self.assertIn("case_revision", row)

    def test_a_fallback_is_never_reported_as_a_passed_live_run(self):
        case = CaseFile("p18-mode-2")
        extract_and_promote(case, [self.TEXT])
        row = [a for a in case.audit if a.get("event") == "semantic_concepts"][-1]
        self.assertFalse(row["llm_passed"])
        self.assertEqual(row["semantic_mode"], SEMANTIC_FALLBACK)


if __name__ == "__main__":
    unittest.main()
