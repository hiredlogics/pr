"""P6 - Drafting and Validation Intelligence.

The model turns an approved Claim Plan into customer language; it does not
choose claims, invent facts, identify the driver or create legal defects. These
tests prove the context it is given, the grounding every sentence must have,
the checks the draft has to clear, the immutable draft versions, and the
regression harness that compares whole journeys.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from pcn_appeal import api, integrity
from pcn_appeal.drafting import shadow_judge, versions
from pcn_appeal.drafting.context import (CASE_CONTEXT_ALLOWED, FORBIDDEN_KEYS, DraftContext,
                                          find_forbidden)
from pcn_appeal.drafting.drafter import drafting_payload
from pcn_appeal.engines.claim_plan_authority import latest_locked
from pcn_appeal.engines.draft_validation_engine import (GROUNDED, STRUCTURAL_OK, UNGROUNDED,
                                                        DraftValidationEngine)
from pcn_appeal.integrity import checks
from pcn_appeal.integrity.journeys import (JourneyRunner, compare, load_journey, regressed,
                                           run_directory)
from pcn_appeal.models import CaseState, Draft, DraftSentence
from pcn_appeal.store import cases as store
import sqlite_store
from test_claim_plan_authority import NARRATIVE, anpr, answer, bay, photo_notice, scenario

JOURNEYS = Path(__file__).resolve().parent.parent / "journeys"
CANCEL = "The operator is requested to cancel the Parking Charge Notice."
# P6.2 VAL-COVERAGE: every approved ground needs a grounded sentence. These
# fixtures used to approve KB-POFA-01 alongside their main ground - it opened on
# keeper status and an unidentified driver alone - so a draft that was meant to
# clear validation had to argue Schedule 4 too. KB-POFA-01 v1.1 requires a
# specific authorised PoFA issue, so these cases no longer approve it and the
# cover sentence would now be an unauthorised substantive PoFA proposition
# (DV-CLAIM / VAL-POFA-AUTHORITY). It is kept as an empty tuple so the call
# sites still read the same.


def pofa_cover():
    return ()


def sentence(text, modules=("STRUCTURAL",), facts=(), evidence=()):
    return DraftSentence(text, list(facts), list(modules), list(evidence))


def draft_of(case, *sentences):
    """Build a draft, skipping any empty slot (see `pofa_cover`)."""
    flat = []
    for s in sentences:
        if s is None or (isinstance(s, tuple) and not s):
            continue
        flat.extend(s if isinstance(s, (list, tuple)) else [s])
    return Draft(case.case_id, [[s] for s in flat])


def pack_of(case, pipe):
    plan = latest_locked(case) or pipe.claim_authority.decide(case, trust={})
    return pipe.reasoning.pack_for(case, plan), plan


def opening(case):
    """The opening a letter must have: names the notice (the PCN number is checked)."""
    return sentence(f"I write as the registered keeper in respect of Parking Charge Notice "
                    f"{case.get('pcn_number')}.", facts=(fact_id(case, "pcn_number"),))


def fact_id(case, name):
    return case.facts[name].fact_id


def check(pipe, case, draft, pack):
    return pipe.draft_validation.check(draft, pack, {f.fact_id for f in case.facts.values()})


def rules(result):
    return {i.rule for i in result.issues}


def queue_drafts(pipe, *drafts):
    """The model drafts these, in order (raw JSON, as the model would return it)."""
    pipe.extraction.llm.responses["drafting"] = [
        {"paragraphs": [[s.__dict__ for s in p] for p in d.paragraphs], "no_ground_reason": None}
        for d in drafts]


def payloads(pipe):
    return [json.loads(c["user"]) for c in pipe.extraction.llm.calls if c["task"] == "drafting"]


# --------------------------------------------------------------------- context
class DraftContextAllowsAndWithholds(unittest.TestCase):
    """Spec 1: locked plan, verified facts, approved evidence, guidance, metadata -
    never the KB, rejected claims, candidates, the narrative, trace, confidence."""

    def setUp(self):
        self.case, self.pipe = bay()                # many claims are rejected or open here
        self.pipe.generate(self.case)
        self.payload = payloads(self.pipe)[0]

    def test_it_carries_the_locked_plan_facts_evidence_guidance_and_metadata(self):
        plan = latest_locked(self.case)
        self.assertEqual(self.payload["module_ids"], plan.supported_ids)
        self.assertEqual(self.payload["case_context"]["claim_plan"]["claim_plan_id"],
                         plan.claim_plan_id)
        self.assertEqual(self.payload["case_context"]["claim_plan"]["status"], "LOCKED")
        self.assertIn("pcn_number", self.payload["verified_facts"])
        self.assertTrue(self.payload["fact_refs"])
        self.assertEqual(self.payload["evidence_refs"], ["E1"])
        self.assertTrue(self.payload["context_chunks"])
        self.assertEqual(self.payload["case_context"]["operator_name"], "Acme Parking Ltd")

    def test_it_does_not_carry_rejected_claims_or_their_wording(self):
        plan = latest_locked(self.case)
        rejected = [i.module_id for i in plan.items if i.status != "SUPPORTED"]
        self.assertIn("KB-ANPR-01", rejected)
        self.assertIn("KB-PAY-01", rejected)
        text = json.dumps(self.payload, default=str)
        for module in rejected:
            self.assertNotIn(module, text, f"{module} reached the drafter")
        self.assertEqual({c.get("module_id") for c in self.payload["context_chunks"]}
                         - {None, "STRUCTURAL"} - set(plan.supported_ids), set())

    def test_it_does_not_carry_the_narrative_trace_or_confidence(self):
        text = json.dumps(self.payload, default=str)
        self.assertNotIn(NARRATIVE, text)
        self.assertEqual(find_forbidden(self.payload), [])
        self.assertEqual(set(self.payload["case_context"]) - set(CASE_CONTEXT_ALLOWED), set())
        for key in ("customer_source_texts", "free_text_provenance", "recovery"):
            self.assertNotIn(key, self.payload["case_context"])

    def test_forbidden_keys_are_stripped_even_when_upstream_leaks_them(self):
        pack, _ = pack_of(self.case, self.pipe)
        pack.case_context["customer_source_texts"] = ["secret narrative words"]
        pack.case_context["recovery"] = {"unknown_material": ["x"]}
        pack.context_chunks.append({"id": "c", "module_id": "KB-REJECTED-99",
                                    "text": "rejected wording", "confidence": 0.93})
        pack.case_context["trace"] = ["internal"]
        payload = drafting_payload(pack)
        text = json.dumps(payload, default=str)
        self.assertNotIn("secret narrative words", text)
        self.assertNotIn("rejected wording", text)
        self.assertNotIn("0.93", text)
        self.assertEqual(DraftContext.from_pack(pack).violations(), [])

    def test_context_audit_is_ids_and_a_digest_not_text(self):
        pack, plan = pack_of(self.case, self.pipe)
        audit = DraftContext.from_pack(pack).audit()
        self.assertEqual(audit["approved"], plan.supported_ids)
        self.assertRegex(audit["context_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn(NARRATIVE, json.dumps(audit))
        events = [a for a in self.case.audit if a.get("event") == "draft_context"]
        self.assertTrue(events)

    def test_every_fact_says_where_it_came_from(self):
        case, pipe = bay()
        pipe.generate(case)
        basis = payloads(pipe)[0]["fact_basis"]
        self.assertEqual(basis["child_occupant_present"], "CUSTOMER_ACCOUNT")
        self.assertEqual(basis["pcn_number"], "DOCUMENT")


# --------------------------------------------------------------------- grounding
class SentenceGrounding(unittest.TestCase):
    """Spec 3: every paragraph maps to claim plan item + fact + evidence."""

    def test_every_sentence_of_a_released_letter_is_grounded(self):
        case, pipe = bay()
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.RELEASED)
        row = [v for v in case.draft_versions if v["released"]][-1]
        self.assertTrue(row["grounding"])
        for g in row["grounding"]:
            self.assertIn(g["status"], (GROUNDED, STRUCTURAL_OK), g)
        grounded = [g for g in row["grounding"] if g["status"] == GROUNDED]
        self.assertTrue(grounded)
        self.assertTrue(all(g["claim_plan_items"] for g in grounded))
        self.assertIn("KB-BAY-02", {m for g in grounded for m in g["claim_plan_items"]})

    def test_a_sentence_mapped_to_no_claim_is_ungrounded(self):
        case, pipe = bay()
        pack, _ = pack_of(case, pipe)
        result = check(pipe, case, draft_of(case, sentence("Something unattached.", modules=())),
                       pack)
        self.assertIn("DV-GROUND", rules(result))
        self.assertEqual(result.grounding[0]["status"], UNGROUNDED)

    def test_ungrounded_sentences_are_regenerated_or_removed(self):
        case, pipe = bay()
        good = sentence("The keeper's account is that children were present in the vehicle.",
                        modules=("KB-BAY-02",), facts=(fact_id(case, "child_occupant_present"),))
        stray = sentence("An unattached assertion.", modules=())
        attach = draft_of(case, opening(case), good, stray, pofa_cover(), sentence(CANCEL))
        queue_drafts(pipe, attach, attach, attach)
        out = pipe.generate(case)
        self.assertNotIn("unattached assertion", (out.letter or "").lower())
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertIn("dropped_failing_sentences", [a.get("event") for a in case.audit])
        self.assertEqual(len(payloads(pipe)), 3)            # asked again, with the rule
        self.assertTrue(any("DV-GROUND" in f for f in payloads(pipe)[1].get("validator_feedback", [])))


# ---------------------------------------------------------------- language rules
class CustomerLanguage(unittest.TestCase):
    """Spec 4 and test 1: the keeper's account, never "the driver parked"."""

    def setUp(self):
        self.case, self.pipe = bay()
        self.pack, _ = pack_of(self.case, self.pipe)
        self.child = fact_id(self.case, "child_occupant_present")

    def verdict(self, text, modules=("KB-BAY-02",), facts=None):
        facts = [self.child] if facts is None else facts
        return check(self.pipe, self.case,
                     draft_of(self.case, sentence(text, modules, facts), pofa_cover(),
                              sentence(CANCEL)),
                     self.pack)

    def test_the_keepers_account_is_attributed(self):
        r = self.verdict("The keeper's account is that children were present in the vehicle.")
        self.assertEqual(rules(r), set(), r.issues)

    def test_an_unattributed_account_is_refused(self):
        r = self.verdict("Children were present in the vehicle.")
        self.assertIn("DV-ACCOUNT", rules(r))

    def test_driver_statements_are_refused_whatever_the_account_says(self):
        for text in ("The driver parked and the children were with them.",
                     "I parked and my children were with me.",
                     "The keeper was driving at the time.",
                     "When I arrived the children were in the back."):
            r = self.verdict(text)
            self.assertIn("DV-DRIVER", rules(r), text)

    def test_the_driver_may_be_mentioned_once_formally_identified(self):
        self.pack.driver_status = "FORMALLY_IDENTIFIED"
        r = self.verdict("The driver left the vehicle for a short period.", facts=[])
        self.assertNotIn("DV-DRIVER", rules(r))
        r = self.verdict("I parked the vehicle.", facts=[])
        self.assertIn("DV-DRIVER", rules(r))

    def test_the_prompt_teaches_the_attribution_rule(self):
        from pcn_appeal import prompts
        body = prompts.system("drafting")
        self.assertIn("The keeper's account is that", body)
        self.assertIn("CUSTOMER_ACCOUNT", body)
        self.assertGreaterEqual(prompts.version("drafting"), 11)


class ParentChildCase(unittest.TestCase):
    """Test 1: includes the children fact, identifies no driver."""

    def test_the_letter_includes_the_children_fact_and_identifies_no_driver(self):
        case, pipe = bay()
        children = fact_id(case, "child_occupant_present")
        letter = draft_of(
            case,
            opening(case),
            sentence("The keeper's account is that children were present in the vehicle.",
                     ("KB-BAY-02",), (children,)),
            pofa_cover(),
            sentence(CANCEL))
        queue_drafts(pipe, letter)
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
        self.assertIn("children were present in the vehicle", out.letter)
        self.assertNotRegex(out.letter, r"\b(I|we) (parked|drove)\b|\bthe driver (parked|drove)\b")
        self.assertTrue(payloads(pipe)[0]["verified_facts"]["child_occupant_present"])
        grounding = [v for v in case.draft_versions if v["released"]][0]["grounding"]
        self.assertIn("child_occupant_present", {f for g in grounding for f in g["facts"]})


# ----------------------------------------------------------------- the checks
class DraftChecks(unittest.TestCase):
    """Spec 5: fact, claim, evidence, driver, legal, structure, leak."""

    def setUp(self):
        self.case, self.pipe = anpr()
        self.pack, self.plan = pack_of(self.case, self.pipe)
        self.module = self.plan.supported_ids[0]

    def verdict(self, *sentences):
        return check(self.pipe, self.case, draft_of(self.case, *sentences, sentence(CANCEL)),
                     self.pack)

    def test_fact_check_unknown_fact_reference(self):
        r = self.verdict(sentence("The vehicle was recorded.", (self.module,), ("no-such-fact",)))
        self.assertIn("DV-FACT", rules(r))

    def test_claim_check_names_the_family_not_the_rejected_module(self):
        case, pipe = bay()
        pack, plan = pack_of(case, pipe)
        r = check(pipe, case, draft_of(case, sentence("The camera recorded two visits.",
                                                      ("KB-ANPR-01",)), sentence(CANCEL)), pack)
        issue = next(i for i in r.issues if i.rule == "DV-CLAIM")
        self.assertNotIn("KB-ANPR-01", issue.message)
        self.assertIn("not approved in Claim Plan", issue.message)

    def test_evidence_check(self):
        r = self.verdict(sentence("The signage is shown in the photograph.", (self.module,),
                                  evidence=("E99",)))
        self.assertIn("DV-EVIDENCE", rules(r))
        r = self.verdict(sentence("The photograph is enclosed.", (self.module,)))
        self.assertIn("DV-EVIDENCE", rules(r))
        r = self.verdict(sentence("The notice is enclosed.", (self.module,), evidence=("E1",)))
        self.assertNotIn("DV-EVIDENCE", rules(r))

    def test_legal_check_refuses_unsupported_conclusions(self):
        for text in ("The notice is unlawful and unenforceable.",
                     "The operator is in breach of the Protection of Freedoms Act 2012.",
                     "The operator has no right to issue this charge.",
                     "See Smith v Jones for the position.",
                     "The charge is void."):
            self.assertIn("DV-LEGAL", rules(self.verdict(sentence(text, (self.module,)))), text)

    def test_legal_check_allows_reported_allegations_and_plain_requests(self):
        r = self.verdict(sentence("The operator alleges the vehicle overstayed.", (self.module,)),
                         sentence("The operator is put to proof of each element.", (self.module,)))
        self.assertNotIn("DV-LEGAL", rules(r))

    def test_structure_check_requires_a_request_to_cancel(self):
        r = check(self.pipe, self.case,
                  draft_of(self.case, sentence("I write as the keeper."),
                           sentence("The vehicle was recorded.", (self.module,))), self.pack)
        self.assertIn("DV-STRUCTURE", rules(r))
        r = self.verdict(sentence("The vehicle was recorded.", (self.module,)))
        self.assertNotIn("DV-STRUCTURE", rules(r))

    def test_leak_check(self):
        for text in ("Relying on KB-ANPR-01 and VAL-PLAN.", "Dear {{operator}},",
                     "The claim plan approved this.", "As an AI language model I cannot."):
            self.assertIn("DV-LEAK", rules(self.verdict(sentence(text, (self.module,)))), text)

    def test_a_clean_draft_clears_every_check(self):
        r = self.verdict(
            sentence("The Parking Charge Notice alleges an overstay.", ("STRUCTURAL",)),
            sentence("The keeper's account is that the vehicle left and returned, so there "
                     "were separate visits.", (self.module,),
                     (fact_id(self.case, "multiple_visits"),)),
            pofa_cover())
        self.assertEqual(rules(r), set(), r.issues)


class PaymentAttempt(unittest.TestCase):
    """Test 2: an attempt is not a payment."""

    def setUp(self):
        # payment_method is what opens KB-PAY-03 (a failed app payment). The
        # fixture previously relied on KB-POFA-01, which opened on keeper
        # status alone and is closed in v1.1; this file is about what the
        # drafter may say about payment, so it needs the payment ground.
        self.case, self.pipe = scenario("Overstayed paid time",
                                        answers={"payment_attempted": True,
                                                 "payment_method": "APP"},
                                        narrative="I tried to pay on the app but it failed")
        self.pack, plan = pack_of(self.case, self.pipe)
        self.module = plan.supported_ids[0]
        self.assertFalse(self.case.has("payment_made"))

    def verdict(self, text):
        return check(self.pipe, self.case, draft_of(self.case, sentence(text, (self.module,)),
                                                    sentence(CANCEL)), self.pack)

    def test_it_does_not_say_payment_completed(self):
        for text in ("Payment was made for the full period.", "The tariff was paid in full.",
                     "The keeper paid for the parking.", "Payment has been completed."):
            self.assertIn("DV-FACT", rules(self.verdict(text)), text)

    def test_it_may_say_an_attempt_was_made(self):
        r = self.verdict("An attempt was made to pay through the app, but the payment could "
                         "not be completed.")
        self.assertNotIn("DV-FACT", rules(r), r.issues)

    def test_a_completed_payment_may_be_stated_once_it_is_established(self):
        case, pipe = scenario("Overstayed paid time", answers={"payment_made": True},
                              narrative="I paid")
        pack, plan = pack_of(case, pipe)
        r = check(pipe, case, draft_of(case, sentence("Payment was made for the full period.",
                                                      (plan.supported_ids[0],)),
                                       sentence(CANCEL)), pack)
        self.assertNotIn("DV-FACT", rules(r))


class AnprMultipleVisits(unittest.TestCase):
    """Test 3: only after the confirmed fact."""

    def test_without_the_fact_the_claim_cannot_reach_the_letter(self):
        case, pipe = scenario("Overstayed paid time", extra={"entry_time": "10:00",
                                                              "exit_time": "13:00"})
        pipe.generate(case)
        plan = latest_locked(case)
        self.assertNotIn("KB-ANPR-01", plan.supported_ids)
        self.assertNotIn("KB-ANPR-01", json.dumps(payloads(pipe)[0], default=str) if payloads(pipe) else "")
        pack, _ = pack_of(case, pipe)
        r = check(pipe, case, draft_of(case, sentence("The camera recorded separate visits.",
                                                      ("KB-ANPR-01",)), sentence(CANCEL)), pack)
        self.assertIn("DV-CLAIM", rules(r))

    def test_with_the_fact_it_is_argued_and_attributed_to_the_keeper(self):
        case, pipe = anpr()
        pipe.generate(case)
        self.assertIn("KB-ANPR-01", latest_locked(case).supported_ids)
        pack, _ = pack_of(case, pipe)
        visits = fact_id(case, "multiple_visits")
        cancel = sentence(CANCEL)
        bare = check(pipe, case, draft_of(case, sentence(
            "The vehicle left and returned, so there were separate visits.", ("KB-ANPR-01",),
            (visits,)), cancel), pack)
        self.assertIn("DV-ACCOUNT", rules(bare))
        attributed = check(pipe, case, draft_of(case, sentence(
            "The keeper's account is that the vehicle left and returned, so there were separate "
            "visits.", ("KB-ANPR-01",), (visits,)), pofa_cover(), cancel), pack)
        self.assertEqual(rules(attributed), set(), attributed.issues)


class UnsupportedArgumentIsRefused(unittest.TestCase):
    """Test 4: inject an unsupported argument -> validation failure."""

    def test_the_injected_argument_fails_validation_and_never_ships(self):
        case, pipe = bay()                           # ANPR is rejected
        injected = draft_of(
            case,
            opening(case),
            sentence("The ANPR system recorded two separate visits.", ("KB-ANPR-01",)),
            sentence("The notice is unlawful.", ("KB-BAY-02",)),
            sentence(CANCEL))
        queue_drafts(pipe, injected, injected, injected)
        out = pipe.generate(case)
        first = [a for a in case.audit if a.get("event") == "validation"][0]
        self.assertFalse(first["passed"])
        self.assertTrue({"DV-CLAIM", "VAL-PLAN"} <= set(first["issues"]) | {"DV-CLAIM"},
                        first["issues"])
        self.assertIn("VAL-PLAN", first["issues"])
        self.assertIn("DV-LEGAL", first["issues"])
        self.assertNotIn("separate visits", out.letter or "")
        self.assertNotIn("unlawful", out.letter or "")
        # what the drafter was told names no rejected module
        feedback = json.dumps(payloads(pipe)[1].get("validator_feedback"))
        self.assertNotIn("KB-ANPR-01", feedback)
        failed = [v for v in case.draft_versions if v["validation_status"] == "FAILED"]
        self.assertTrue(failed)
        self.assertFalse(any(v["released"] for v in failed))


# ------------------------------------------------------------------- versions
class DraftVersions(unittest.TestCase):
    """Spec 7 and test 5."""

    def setUp(self):
        self.db = sqlite_store.install(self)
        row = store.new_case()
        self.case, self.pipe = bay(case_id=row.case_id)

    def persist(self, out):
        store.save(self.case)
        store.save_output(self.case, out)

    def test_each_draft_is_recorded_against_the_claim_plan_that_produced_it(self):
        out = self.pipe.generate(self.case)
        self.persist(out)
        plan = latest_locked(self.case)
        rows = self.case.draft_versions
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["claim_plan_id"], plan.claim_plan_id)
            self.assertRegex(r["content_hash"], r"^[0-9a-f]{64}$")
            self.assertTrue(r["model"])
            self.assertEqual(r["prompt_version"], 13)
            self.assertIn(r["validation_status"], ("PASSED", "FAILED"))
            self.assertTrue(r["created_at"])
        released = [r for r in rows if r["released"]]
        self.assertEqual(len(released), 1)
        self.assertEqual(released[0]["content_hash"], versions.content_hash(out.draft))
        self.assertEqual(sqlite_store.count(self.db, "draft_versions"), len(rows))

    def test_reload_gives_the_same_draft_version(self):
        out = self.pipe.generate(self.case)
        self.persist(out)
        loaded = store.load(self.case.case_id)
        self.assertEqual([(r["draft_id"], r["version"], r["content_hash"], r["claim_plan_id"],
                           r["validation_status"], r["released"]) for r in loaded.draft_versions],
                         [(r["draft_id"], r["version"], r["content_hash"], r["claim_plan_id"],
                           r["validation_status"], r["released"]) for r in self.case.draft_versions])
        released = next(r for r in loaded.draft_versions if r["released"])
        self.assertEqual(versions.content_hash(versions.draft_of(released)),
                         versions.content_hash(out.draft))
        self.assertEqual(versions.draft_of(released).plain_text(), out.draft.plain_text())

    def test_regenerating_identical_content_does_not_make_a_new_version(self):
        out = self.pipe.generate(self.case)
        self.persist(out)
        before = [(r["draft_id"], r["version"]) for r in self.case.draft_versions]
        loaded = store.load(self.case.case_id)
        out2 = self.pipe.generate(loaded)
        store.save(loaded)
        after = [(r["draft_id"], r["version"]) for r in loaded.draft_versions]
        self.assertEqual(out2.letter, out.letter)
        self.assertEqual(before, after)
        self.assertEqual(sqlite_store.count(self.db, "draft_versions"), len(before))

    def test_a_new_claim_plan_makes_a_new_draft_version(self):
        out = self.pipe.generate(self.case)
        self.persist(out)
        answer(self.case, {"payment_made": True, "payment_method": "APP"})
        self.pipe._reanalyse(self.case, NARRATIVE)
        out2 = self.pipe.generate(self.case)
        self.persist(out2)
        plans = {r["claim_plan_id"] for r in self.case.draft_versions}
        self.assertEqual(len(plans), 2)
        self.assertEqual(sorted(r["version"] for r in self.case.draft_versions),
                         list(range(1, len(self.case.draft_versions) + 1)))

    def test_the_database_refuses_to_edit_a_draft(self):
        self.persist(self.pipe.generate(self.case))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE draft_versions SET content_hash = 'x'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE draft_versions SET released = 0 WHERE released = 1")

    def test_the_integrity_layer_checks_the_version_is_recorded(self):
        out = self.pipe.generate(self.case)
        result = {c["check"]: c["status"] for c in out.integrity["checks"]}
        self.assertEqual(result["DRAFT_VERSION_RECORDED"], "PASS")
        self.case.draft_versions.clear()
        result = {c["check"]: c["status"]
                  for c in checks.check_case(self.case, out, self.pipe.kg)}
        self.assertEqual(result["DRAFT_VERSION_RECORDED"], "FAIL")
        self.assertIn("## Drafts", out.integrity["report"])


# --------------------------------------------------------------- shadow judge
class ShadowJudge(unittest.TestCase):
    """Spec 6: a second model reads the draft against the plan; it never blocks."""

    def test_off_by_default_and_no_extra_model_call(self):
        case, pipe = bay()
        pipe.generate(case)
        self.assertIsNone(pipe.shadow)
        self.assertFalse([c for c in pipe.extraction.llm.calls if c["task"] == "validation"])
        self.assertIsNone([v for v in case.draft_versions if v["released"]][0]["judge"])

    def test_a_warning_is_recorded_and_changes_nothing(self):
        case, pipe = bay()
        pipe.shadow = shadow_judge.ShadowJudge(pipe.extraction.llm)
        pipe.extraction.llm.responses["validation"] = [{"issues": [
            {"rule": "VAL-PLAN", "sentence": "x", "message": "Unsupported statement found."}]}]
        baseline_case, baseline_pipe = bay(case_id="C-BASE")
        baseline = baseline_pipe.generate(baseline_case)
        out = pipe.generate(case)
        self.assertEqual(out.state, CaseState.RELEASED)
        self.assertEqual(out.letter, baseline.letter)
        judge = [v for v in case.draft_versions if v["released"]][0]["judge"]
        self.assertEqual(judge["status"], "WARNING")
        self.assertFalse(judge["blocking"])
        self.assertIn("Unsupported statement found", json.dumps(judge))
        event = next(a for a in case.audit if a.get("event") == "shadow_judge")
        self.assertEqual(event["status"], "WARNING")
        call = next(c for c in pipe.extraction.llm.calls if c["task"] == "validation")
        sent = json.loads(call["user"])
        self.assertEqual(sent["review_mode"], "shadow")
        self.assertEqual(sent["claim_plan"]["approved"], latest_locked(case).supported_ids)

    def test_pass_and_a_failing_judge(self):
        case, pipe = bay()
        pipe.shadow = shadow_judge.ShadowJudge(pipe.extraction.llm)
        out = pipe.generate(case)
        self.assertEqual([v for v in case.draft_versions if v["released"]][0]["judge"]["status"],
                         "PASS")

        class Down:
            def complete_json(self, **kw):
                raise RuntimeError("judge unavailable")
        case2, pipe2 = bay(case_id="C-DOWN")
        pipe2.shadow = shadow_judge.ShadowJudge(Down())
        out2 = pipe2.generate(case2)
        self.assertEqual(out2.state, CaseState.RELEASED)
        self.assertEqual([v for v in case2.draft_versions if v["released"]][0]["judge"]["status"],
                         "ERROR")

    def test_switch(self):
        with mock.patch.dict(os.environ, {"SHADOW_JUDGE": "1"}):
            self.assertTrue(shadow_judge.enabled())
        self.assertFalse(shadow_judge.enabled())
        self.assertTrue(shadow_judge.enabled(True))


# --------------------------------------------------------- regression harness
class RegressionHarness(unittest.TestCase):
    """Spec 8: compare facts, questions, claim plan, draft and validation."""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"ADMIN_TOKEN": "t0k", "DATABASE_URL": ""})
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(api.app)
        self.tmp = Path(self.id().replace(".", "_"))
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)

    def runner(self, **kw):
        return JourneyRunner(self.client, "t0k", golden_dir=self.dir, **kw)

    def test_snapshots_match_on_a_rerun_and_hold_no_customer_values(self):
        self.runner(update_golden=True).run(load_journey(JOURNEYS / "overstay_skip_all.yaml"))
        files = list(self.dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        for pii in ("KX19", "PCN778899", "Riverside", "Acme"):
            self.assertNotIn(pii, text)
        golden = json.loads(text)
        self.assertEqual(set(golden), {"facts", "questions", "claim_plan", "draft", "validation"})
        result = self.runner().run(load_journey(JOURNEYS / "overstay_skip_all.yaml"))
        self.assertTrue(result.passed, result.failures)
        self.assertEqual(result.diffs, {})

    def test_every_difference_is_reported_by_section(self):
        self.runner(update_golden=True).run(load_journey(JOURNEYS / "overstay_skip_all.yaml"))
        golden = json.loads(next(self.dir.glob("*.json")).read_text())
        now = json.loads(json.dumps(golden))
        now["facts"]["pcn_number"] = "0" * 12
        now["facts"].pop("vrm", None)
        now["questions"]["asked"] = ["something_new"]
        now["claim_plan"]["approved"] = ["KB-X"]
        now["draft"]["structure"] = [[["KB-X"], "UNGROUNDED"]]
        now["validation"]["state"] = "MANUAL_REVIEW"
        diffs = compare(golden, now)
        self.assertEqual(set(diffs), {"facts", "questions", "claim_plan", "draft", "validation"})
        self.assertTrue(regressed(diffs))

    def test_a_wording_change_alone_is_not_a_regression(self):
        self.runner(update_golden=True).run(load_journey(JOURNEYS / "overstay_skip_all.yaml"))
        golden = json.loads(next(self.dir.glob("*.json")).read_text())
        now = json.loads(json.dumps(golden))
        now["draft"]["content_hash"] = "f" * 64
        diffs = compare(golden, now)
        self.assertEqual(list(diffs), ["draft"])
        self.assertTrue(diffs["draft"][0].startswith("TEXT_ONLY"))
        self.assertFalse(regressed(diffs))

    def test_a_missing_snapshot_fails_the_journey(self):
        result = self.runner().run(load_journey(JOURNEYS / "overstay_skip_all.yaml"))
        self.assertFalse(result.passed)
        self.assertIn("no golden snapshot", " ".join(result.failures))

    def test_the_shipped_golden_snapshots_still_match(self):
        golden = JOURNEYS / "golden"
        self.assertTrue(list(golden.glob("*.json")))
        results = run_directory(JourneyRunner(self.client, "t0k", golden_dir=golden), JOURNEYS)
        for r in results:
            self.assertTrue(r.passed, (r.name, r.failures, r.diffs))


if __name__ == "__main__":
    unittest.main()
