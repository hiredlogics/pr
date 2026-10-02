"""A case saved by one process and loaded by another carries on unchanged.

Found by a live run that reloaded the case between steps (as a restart or a
second worker would):

  * page images were not stored, so /confirm counted 0 pages and asked for a
    notice the customer had already uploaded both sides of;
  * a confirmed fact came back EXTRACTED - save() compared values only, and
    confirming changes the status, not the value;
  * the released letter was stored but never loaded, so letter.pdf was 404;
  * asked_questions was rebuilt from raw_answers and picked up the internal
    "_material_source_texts" key as a question;
  * the narrative was not loaded, so the next analysis round cleared every fact
    the customer's account had established;
  * the audit trail was not loaded, and analysis counts its rounds from it.

These run the real store SQL against tests/sqlite_store.py.
"""
from __future__ import annotations

import io
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from pcn_appeal import api
from pcn_appeal.models import (CaseFile, CaseState, Draft, DraftSentence, EvidenceItem, Fact,
                               FactSource, FactStatus, RetrievalPack, SourceKind,
                               ValidationIssue, ValidationResult)
from pcn_appeal.notice_completeness import assess_notice_sides
from pcn_appeal.orchestrator import AppealOutput
from pcn_appeal.store import cases as store

import sqlite_store
from support import analysis_llm, patch_client


def page(colour, size=(80, 120)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "JPEG")
    return out.getvalue()


def fact(name, value, status=FactStatus.EXTRACTED, kind=SourceKind.DOCUMENT, ref="E1#p1"):
    return Fact(f"F-{name}", name, value, status, FactSource(kind, ref), 0.97)


def pack() -> RetrievalPack:
    return RetrievalPack(
        primary_route="BAY", secondary_routes=["POFA"], module_ids=["KB-BAY-02"],
        verified_facts={"vrm": "AB12CDE"}, fact_refs={"vrm": "F-vrm"}, missing_facts=[],
        evidence_refs=[], prohibited_claims=[], code_version=None, pofa_route="POSTAL",
        pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[], lease_clauses=[], trace=["t"], case_context={"k": "v"})


class StoreRoundTrip(unittest.TestCase):

    def setUp(self):
        self.db = sqlite_store.install(self)
        self.case = store.new_case()

    def reload(self) -> CaseFile:
        store.save(self.case)
        return store.load(self.case.case_id)

    def test_page_images_survive_and_the_sides_check_agrees(self):
        self.case.evidence["E1"] = EvidenceItem("E1", "NTK", "front.jpg", images=[page("red")])
        self.case.evidence["E2"] = EvidenceItem("E2", "NTK", "back.jpg", images=[page("blue")])
        self.case.document_classes = {"E1": "NTK", "E2": "NTK"}
        self.case.route = "PRIVATE_PARKING"
        before = assess_notice_sides(self.case)
        loaded = self.reload()
        for label in ("E1", "E2"):
            self.assertEqual(loaded.evidence[label].images, self.case.evidence[label].images)
        self.assertEqual(assess_notice_sides(loaded), before)
        self.assertTrue(before["complete"])

    def test_unchanged_pages_are_not_rewritten_and_removed_ones_go(self):
        self.case.evidence["E1"] = EvidenceItem("E1", "NTK", "a.jpg", images=[page("red")])
        self.case.evidence["E2"] = EvidenceItem("E2", "NTK", "b.jpg", images=[page("blue")])
        store.save(self.case)
        store.save(self.case)
        self.assertEqual(sqlite_store.count(self.db, "evidence_pages"), 2)
        # A rejected upload is cleared and the customer uploads again.
        self.case.evidence.clear()
        self.case.evidence["E1"] = EvidenceItem("E1", "NTK", "c.jpg", images=[page("green")])
        loaded = self.reload()
        self.assertEqual(sorted(loaded.evidence), ["E1"])
        self.assertEqual(loaded.evidence["E1"].images, [page("green")])
        self.assertEqual(sqlite_store.count(self.db, "evidence_pages"), 1)

    def test_a_confirmation_survives_without_a_value_change(self):
        self.case.put(fact("vrm", "AB12CDE"))
        store.save(self.case)
        self.case.put(fact("vrm", "AB12CDE", FactStatus.CONFIRMED, SourceKind.ANSWER, "confirm"))
        loaded = self.reload()
        self.assertEqual(loaded.facts["vrm"].status, FactStatus.CONFIRMED)
        self.assertEqual(loaded.facts["vrm"].source.kind, SourceKind.ANSWER)
        # One node, updated in place; both versions are in the append-only history.
        self.assertEqual(sqlite_store.count(self.db, "facts", "fact_name = 'vrm'"), 1)
        self.assertEqual(sqlite_store.count(self.db, "fact_history", "fact = 'vrm'"), 2)
        self.assertEqual(loaded.facts.node_id("vrm"), self.case.facts.node_id("vrm"))

    def test_dates_come_back_as_dates(self):
        from datetime import date
        self.case.put(fact("parking_event_date", date(2026, 6, 1)))
        self.case.put(fact("pcn_number", "PCN-2026-06-01X"))
        loaded = self.reload()
        self.assertEqual(loaded.facts["parking_event_date"].value, date(2026, 6, 1))
        self.assertEqual(loaded.facts["pcn_number"].value, "PCN-2026-06-01X")
        store.save(loaded)                    # a revived date is not a changed value
        self.assertEqual(sqlite_store.count(self.db, "facts", "fact_name = 'parking_event_date'"), 1)

    def test_an_unchanged_fact_is_not_versioned_again(self):
        self.case.put(fact("vrm", "AB12CDE"))
        store.save(self.case)
        store.save(self.case)
        self.assertEqual(sqlite_store.count(self.db, "facts", "fact_name = 'vrm'"), 1)
        self.assertEqual(sqlite_store.count(self.db, "fact_history", "fact = 'vrm'"), 1)

    def test_a_fact_removed_from_the_case_does_not_come_back(self):
        self.case.put(fact("child_occupant_present", True, FactStatus.ANSWERED,
                           SourceKind.CUSTOMER_FREE_TEXT, "free_text:child_occupant_present"))
        store.save(self.case)
        self.case.retract("child_occupant_present", "test: removed")
        self.assertNotIn("child_occupant_present", self.reload().facts)

    def test_the_narrative_and_latest_answers_survive(self):
        self.case.raw_answers["narrative"] = "first account"
        store.save(self.case)
        self.case.raw_answers["narrative"] = "edited account"
        self.case.raw_answers["payment_made"] = "yes"
        loaded = self.reload()
        self.assertEqual(loaded.raw_answers["narrative"], "edited account")
        self.assertEqual(loaded.raw_answers["payment_made"], "yes")

    def test_internal_working_keys_are_neither_stored_nor_asked(self):
        self.case.raw_answers["narrative"] = "account"
        self.case.raw_answers["_material_source_texts"] = "derived"
        self.case.asked_questions = ["payment_made"]
        loaded = self.reload()
        self.assertEqual(loaded.asked_questions, ["payment_made"])
        self.assertNotIn("_material_source_texts", loaded.raw_answers)
        self.assertEqual(sqlite_store.count(
            self.db, "raw_answers", "question LIKE '\\_%' ESCAPE '\\'"), 0)

    def test_a_row_from_before_the_question_columns_skips_internal_keys(self):
        cid = self.case.case_id
        for q in ("narrative", "_material_source_texts", "payment_made"):
            self.db.execute("INSERT INTO raw_answers (case_id, question, raw_text) VALUES (?, ?, ?)",
                            (cid, q, "x"))
        self.assertEqual(store.load(cid).asked_questions, ["payment_made"])

    def test_pending_questions_survive_with_their_type_and_options(self):
        q = {"fact": "payment_method", "text": "How did you pay?", "type": "choice",
             "options": ["APP", "MACHINE"]}
        self.case.pending_questions = [q]
        self.case.asked_questions = ["payment_method"]
        self.assertEqual(self.reload().pending_questions, [q])

    def test_the_audit_trail_survives_and_is_not_written_twice(self):
        self.case.audit.append({"event": "analysis_round", "grounds": []})
        loaded = self.reload()
        self.assertEqual([a["event"] for a in loaded.audit], ["analysis_round"])
        store.save(loaded)
        self.assertEqual(sqlite_store.count(self.db, "audit_log"), 1)


class ReleasedLetterSurvives(unittest.TestCase):

    def setUp(self):
        self.db = sqlite_store.install(self)
        self.case = store.new_case()

    def output(self, state=CaseState.RELEASED, passed=True):
        draft = Draft(self.case.case_id, [[DraftSentence("First.", ["F-vrm"], ["KB-BAY-02"])],
                                          [DraftSentence("Second.")]],
                      attempt=2, model="m", prompt_version=3)
        issues = [] if passed else [ValidationIssue("VAL-X", "BLOCK", "no")]
        out = AppealOutput(state, "First.\n\nSecond." if passed else None, pack(), draft,
                           ValidationResult(passed, issues), ["RECEIPT: r.jpg"])
        if not passed:
            out.outcome, out.outcome_title, out.can_continue = "MANUAL_REVIEW", "Held", False
        return out

    def test_released_output_round_trips(self):
        out = self.output()
        self.case.state = CaseState.RELEASED
        store.save(self.case)
        store.save_output(self.case, out)
        loaded = store.load_output(store.load(self.case.case_id))
        self.assertEqual(loaded.state, CaseState.RELEASED)
        self.assertEqual(loaded.letter, out.letter)
        self.assertEqual(loaded.draft, out.draft)
        self.assertEqual(loaded.pack, out.pack)
        self.assertEqual(loaded.validation, out.validation)
        self.assertEqual(loaded.evidence_list, out.evidence_list)

    def test_a_held_output_stays_held(self):
        self.case.state = CaseState.MANUAL_REVIEW
        store.save(self.case)
        store.save_output(self.case, self.output(CaseState.MANUAL_REVIEW, passed=False))
        loaded = store.load_output(store.load(self.case.case_id))
        self.assertEqual(loaded.state, CaseState.MANUAL_REVIEW)
        self.assertIsNone(loaded.letter)
        self.assertEqual((loaded.outcome, loaded.outcome_title, loaded.can_continue),
                         ("MANUAL_REVIEW", "Held", False))

    def test_the_latest_draft_wins(self):
        self.case.state = CaseState.RELEASED
        store.save(self.case)
        store.save_output(self.case, self.output(CaseState.MANUAL_REVIEW, passed=False))
        store.save_output(self.case, self.output())
        self.assertEqual(store.load_output(self.case).state, CaseState.RELEASED)

    def test_a_draft_stored_before_state_was_recorded(self):
        self.case.state = CaseState.RELEASED
        store.save(self.case)
        store.save_output(self.case, self.output())
        self.db.execute("UPDATE drafts SET state = NULL, letter = NULL")
        loaded = store.load_output(self.case)
        self.assertEqual(loaded.state, CaseState.RELEASED)
        self.assertEqual(loaded.letter, "First.\n\nSecond.")
        self.case.state = CaseState.MANUAL_REVIEW            # case never released
        self.assertNotEqual(store.load_output(self.case).state, CaseState.RELEASED)

    def test_no_draft_means_no_output(self):
        self.assertIsNone(store.load_output(self.case))


class JourneyAcrossRestarts(unittest.TestCase):
    """The customer journey over HTTP, dropping the process cache before every
    request so each one is served from storage."""

    BREACH = "Parked in a Parent and Child bay without being accompanied by a child"

    def fields(self):
        values = dict(operator_name="Example Parking Ltd", pcn_number="PCN000222",
                      vrm="AB12CDE", parking_location="Example Store car park",
                      site_postcode="M1 1AA", parking_event_date="01/06/2026",
                      notice_issue_date="03/06/2026", charge_amount="£100",
                      alleged_breach=self.BREACH, observation_time="12:23",
                      event_time="12:23", operator_ata="BPA",
                      keeper_name="A Keeper", keeper_address="1 Example Road, M1 1AA")
        return {k: {"value": v, "confidence": 0.97, "evidence_id": "E1", "page": 1}
                for k, v in values.items()}

    def setUp(self):
        sqlite_store.install(self)
        patch_client(self, analysis_llm({"fields": self.fields(),
                                         "doc_types": {"E1": "NTK", "E2": "NTK"}}))
        self.c = TestClient(api.app)

    def restart(self, case_id):
        api.CASES.pop(case_id, None)

    def test_photographed_notice_reaches_a_letter_and_pdf_across_restarts(self):
        case_id = self.c.post("/cases").json()["case_id"]
        r = self.c.post(f"/cases/{case_id}/files", files=[
            ("files", ("front.jpg", page("red"), "image/jpeg")),
            ("files", ("back.jpg", page("blue"), "image/jpeg"))])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["route"], "PRIVATE_PARKING")

        self.restart(case_id)
        details = self.c.get(f"/cases/{case_id}/confirmation").json()["details"]
        confirmed = [d["name"] for d in details if d["value"]]

        self.restart(case_id)
        r = self.c.post(f"/cases/{case_id}/confirm", json={
            "corrections": {}, "confirmed": confirmed,
            "narrative": "I would like to appeal the charge."})
        body = r.json()
        self.assertNotEqual(body.get("outcome"), "NEEDS_DOCUMENTS",
                            "both sides were uploaded; a reload must not lose them")
        self.assertEqual(body["state"], "RELEASED", body)
        self.assertNotIn("charging session", body["letter"].lower())

        self.restart(case_id)
        case = api._case(case_id)["case"]
        self.assertEqual(case.facts["vrm"].status, FactStatus.CONFIRMED)
        self.assertEqual(case.raw_answers["narrative"], "I would like to appeal the charge.")
        self.assertFalse(any(q.startswith("_") for q in case.asked_questions))

        self.restart(case_id)
        with mock.patch("pcn_appeal.pdf.render_letter_pdf", return_value=b"%PDF-1.7 test") as render:
            pdf = self.c.get(f"/cases/{case_id}/letter.pdf")
        self.assertEqual(pdf.status_code, 200, pdf.text)
        self.assertEqual(render.call_args.kwargs["keeper_name"], "A Keeper")
        self.assertEqual(self.c.get(f"/cases/{case_id}").json()["state"], "RELEASED")


class SchemaDeclaresWhatTheStoreWrites(unittest.TestCase):
    """The SQLite stand-in mirrors infra/postgres_schema.sql for these columns;
    a column the store writes must exist in the real schema too."""

    def test_new_columns_are_in_the_postgres_schema(self):
        with open("infra/postgres_schema.sql") as fh:
            schema = fh.read()
        for needle in ("asked_questions jsonb", "pending_questions jsonb",
                       "CREATE TABLE IF NOT EXISTS evidence_pages", "image     bytea",
                       "drafts ADD COLUMN IF NOT EXISTS state", "drafts ADD COLUMN IF NOT EXISTS letter",
                       "drafts ADD COLUMN IF NOT EXISTS evidence_list",
                       "drafts ADD COLUMN IF NOT EXISTS outcome",
                       "drafts ADD COLUMN IF NOT EXISTS no_ground_reason"):
            self.assertIn(needle, schema)


if __name__ == "__main__":
    unittest.main()
