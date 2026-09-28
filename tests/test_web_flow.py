"""End-to-end customer journey over HTTP, via TestClient (no server, no network).

Covers what the browser does: upload -> answer or skip -> letter, plus the two
things the customer UI depends on that the JSON API did not previously provide -
plain-English ground labels, and an honest provider report.
"""
import unittest

from fastapi.testclient import TestClient

from pcn_appeal.api import app

NOTICE = """PARKING CHARGE NOTICE
Operator Name: Acme Parking Ltd
PCN Number: PCN778899
Vehicle Registration: KX19 PLT
Location: Riverside Retail Park
Postcode: M1 4BT
Date of Contravention: 12/06/2026
Date of Issue: 02/07/2026
Entry Time: 14:05
Exit Time: 16:58
Charge: 100
Alleged Breach: Overstayed the maximum permitted period
Trade Association: BPA"""

NARRATIVE = "the letter only arrived weeks later and there was a queue at the barrier"


def upload(client, narrative=NARRATIVE, text=NOTICE):
    return client.post("/appeal", json={
        "documents": [{"evidence_id": "E1", "filename": "notice.txt", "text": text}],
        "narrative": narrative,
    })


class Pages(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_customer_app_is_served_at_root(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Appeal your parking charge", r.text)

    def test_customer_app_exposes_no_internals(self):
        """The whole point of the split: no rule IDs or engine vocabulary here."""
        body = self.c.get("/").text
        for leak in ("KB-POFA", "PP-INTRO", "use_when", "RetrievalPack", "UNCERTAIN", "Engine "):
            self.assertNotIn(leak, body, f"customer page leaks {leak!r}")

    def test_reviewer_console_is_separate(self):
        r = self.c.get("/console")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Provenance", r.text)

    def test_health_reports_which_provider_is_really_in_use(self):
        h = self.c.get("/health").json()
        self.assertIn(h["provider"], ("openai", "demo"))
        self.assertGreater(h["modules"], 0)
        if h["provider"] == "demo":
            self.assertTrue(h["provider_note"], "demo mode must say why")


class CustomerJourney(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_upload_then_answer_produces_a_letter(self):
        """Answer whatever is asked, however many rounds, and end with a letter.

        Whether a pause happens at all is the analysis model's judgement, so the
        assertion is that the journey terminates - not that it detours.
        """
        result = upload(self.c).json()
        case_id = result["case_id"]

        for _ in range(4):                              # guard against a loop
            questions = result.get("questions") or []
            if not questions:
                break
            answers = {q["fact"]: 18 if q["type"] == "int" else "yes" for q in questions}
            result = self.c.post(f"/appeal/{case_id}", json={"answers": answers}).json()
        else:
            self.fail("still asking after four rounds")
        second = result

        self.assertEqual(second["state"], "RELEASED", second.get("blocking_issues"))
        self.assertIn("PCN778899", second["letter"])
        self.assertNotRegex(second["letter"], r"\bI (drove|parked|was driving)\b")

    def test_skipping_every_question_still_produces_a_letter(self):
        """'Skip these' in the UI sends no answers: the grounds those facts would
        have unlocked are dropped, and the letter is written from what is proven."""
        first = upload(self.c).json()
        case_id = first["case_id"]
        res = self.c.post(f"/appeal/{case_id}", json={"skip": True}).json()

        self.assertEqual(res["state"], "RELEASED", res.get("blocking_issues"))
        self.assertTrue(res["letter"].strip())

    def test_grounds_are_plain_english_not_internal_codes(self):
        first = upload(self.c).json()
        res = self.c.post(f"/appeal/{first['case_id']}", json={"skip": True}).json()

        self.assertTrue(res["grounds"])
        for label in res["grounds"]:
            self.assertNotRegex(label, r"^[A-Z_]+$", f"{label!r} is an internal route code")
        self.assertTrue(any("Keeper liability" in g for g in res["grounds"]), res["grounds"])

    def test_letter_never_contains_internal_identifiers(self):
        first = upload(self.c).json()
        res = self.c.post(f"/appeal/{first['case_id']}", json={"skip": True}).json()
        for leak in ("KB-", "PP-", "AI-", "{{", "}}"):
            self.assertNotIn(leak, res["letter"], f"letter leaks {leak!r}")

    def test_unknown_case_is_a_404(self):
        self.assertEqual(self.c.post("/appeal/C-9999", json={"skip": True}).status_code, 404)

    def test_a_bad_answer_is_rejected_not_swallowed(self):
        first = upload(self.c).json()
        choice = next((q for q in first["questions"] if q["type"] == "int"), None)
        if choice is None:
            self.skipTest("no int question in this round")
        r = self.c.post(f"/appeal/{first['case_id']}",
                        json={"answers": {choice["fact"]: "not-a-number"}})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
