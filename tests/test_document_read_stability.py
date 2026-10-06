"""Phase 1: upload a notice -> read it -> one structured DocumentReadResult.

What is pinned here is the reading path only. Nothing downstream of it runs.

  - one classification and one extraction per unchanged document;
  - a critical field in doubt costs one targeted read, never a second full read;
  - a successful read is kept by document revision, so refresh, resume and a
    second pass over the same pages send nothing to the model;
  - front required, back optional; a wrong optional back does not touch the front;
  - the result carries the nine fields with value / confidence / source / page;
  - every retry the provider client makes is counted and visible in the audit log.

The model here is scripted, so these tests prove call counts and state. They say
nothing about how accurately a real model reads a photograph; that is measured by
`python -m pcn_appeal.eval.doc_read --provider live`.

Run:  PYTHONPATH=.:tests python -m unittest discover -s tests -p test_document_read_stability.py -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from pcn_appeal.document_read import READ_FIELDS, document_revision, load_document_read_result
from pcn_appeal.eval.doc_read.__main__ import (
    CPPLUS, Scripted, _back_text, _front_text, _render, read_once)

OTHER_PCN = "99887766"
FRONT = _render(_front_text(CPPLUS), "FRONT")
BACK = _render(_back_text(CPPLUS["pcn_number"]), "REVERSE")
OTHER_BACK = _render(_back_text(OTHER_PCN), "REVERSE")


def tasks(r):
    return [c["task"] for c in r["calls"]]


class _VerifyTimesOut(Scripted):
    def complete_json(self, *, task, **kw):
        if task == "identity_verification":
            raise TimeoutError("provider timed out")
        return super().complete_json(task=task, **kw)


class OneReadPerDocument(unittest.TestCase):

    def test_a_confident_notice_costs_one_classification_and_one_extraction(self):
        r = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)
        self.assertEqual(tasks(r), ["classification", "extraction"])
        self.assertEqual(r["retries"], 0)

    def test_every_critical_field_is_read_correctly_from_the_script(self):
        r = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)
        self.assertEqual({k: v["pass"] for k, v in r["fields"].items()},
                         {k: True for k in CPPLUS})

    def test_three_runs_of_the_same_notice_return_the_same_result(self):
        seen = []
        for _ in range(3):
            r = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)
            seen.append(r["document_read_result"]["fields"])
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[1], seen[2])


class DocumentReadResultShape(unittest.TestCase):

    def test_all_nine_fields_each_with_value_confidence_source_page(self):
        res = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)["document_read_result"]
        self.assertEqual(tuple(res["fields"]), READ_FIELDS)
        for name, f in res["fields"].items():
            self.assertEqual(set(f), {"value", "confidence", "source", "page"}, name)
        pcn = res["fields"]["pcn_number"]
        self.assertEqual((pcn["value"], pcn["source"], pcn["page"]), (CPPLUS["pcn_number"], "E1", 1))
        self.assertGreaterEqual(pcn["confidence"], 0.85)

    def test_an_unread_field_is_present_and_empty_not_missing(self):
        partial = {k: v for k, v in CPPLUS.items() if k != "alleged_breach"}
        res = read_once(Scripted(partial), [FRONT], partial)["document_read_result"]
        self.assertEqual(res["fields"]["alleged_breach"],
                         {"value": None, "confidence": 0.0, "source": None, "page": None})

    def test_the_result_names_the_pages_it_was_read_from(self):
        res = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)["document_read_result"]
        self.assertEqual(len(res["document_revision"]), 16)
        self.assertIn("E1", res["pages"])


class ReadSurvivesRefreshAndRepeat(unittest.TestCase):

    def setUp(self):
        self.first = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)
        self.case, self.pipe = self.first["case"], self.first["pipe"]

    def test_D_the_same_image_again_sends_nothing_to_the_model(self):
        again = read_once(self.pipe.llm, [FRONT], CPPLUS, case=self.case, pipe=self.pipe)
        self.assertEqual(again["model_calls"], 0)
        self.assertEqual(again["document_read_result"]["fields"],
                         self.first["document_read_result"]["fields"])

    def test_E_resume_on_a_fresh_pipeline_sends_nothing_to_the_model(self):
        from pcn_appeal.orchestrator import AppealPipeline
        again = read_once(self.pipe.llm, [FRONT], CPPLUS, case=self.case,
                          pipe=AppealPipeline(self.pipe.llm))
        self.assertEqual(again["model_calls"], 0)

    def test_the_stored_result_is_what_a_refresh_loads(self):
        stored = load_document_read_result(self.case)
        self.assertEqual(stored["document_revision"], document_revision(self.case))
        self.assertEqual(stored["fields"], self.first["document_read_result"]["fields"])

    def test_a_changed_page_is_read_again(self):
        changed = _render(_front_text({**CPPLUS, "vrm": "AB12CDE"}), "FRONT")
        again = read_once(Scripted({**CPPLUS, "vrm": "AB12CDE"}), [changed],
                          {**CPPLUS, "vrm": "AB12CDE"})
        self.assertEqual(tasks(again), ["classification", "extraction"])
        self.assertNotEqual(again["document_read_result"]["document_revision"],
                            self.first["document_read_result"]["document_revision"])


class OneUncertainFieldCostsOneTargetedRead(unittest.TestCase):

    def test_F_a_doubtful_field_is_read_once_more_and_not_re_extracted(self):
        r = read_once(Scripted(CPPLUS, pcn_conf=0.6, blind_classifier=True), [FRONT], CPPLUS)
        self.assertEqual(tasks(r), ["classification", "extraction", "identity_verification"])
        self.assertEqual(r["uncertain"], [])
        self.assertTrue(r["fields"]["pcn_number"]["pass"])

    def test_F_the_targeted_read_asks_about_the_doubtful_field_only(self):
        llm = Scripted(CPPLUS, pcn_conf=0.6, blind_classifier=True)
        seen = []
        real = llm.complete_json

        def spy(*, task, system, user, images=None):
            if task == "identity_verification":
                seen.append(user)
            return real(task=task, system=system, user=user, images=images)
        llm.complete_json = spy
        read_once(llm, [FRONT], CPPLUS)
        self.assertEqual(len(seen), 1)
        self.assertIn("pcn_number", seen[0])
        self.assertNotIn("parking_location", seen[0])

    def test_F_a_failed_targeted_read_leaves_the_field_uncertain_and_does_not_loop(self):
        llm = _VerifyTimesOut(CPPLUS, pcn_conf=0.6, blind_classifier=True)
        r = read_once(llm, [FRONT], CPPLUS)
        self.assertEqual(tasks(r).count("extraction"), 1)
        self.assertEqual(tasks(r).count("identity_verification"), 1)
        self.assertIn("pcn_number", r["uncertain"])
        again = read_once(llm, [FRONT], CPPLUS, case=r["case"], pipe=r["pipe"])
        self.assertEqual(tasks(again).count("extraction"), 0)

    def test_F_a_second_independent_reading_makes_a_targeted_read_unnecessary(self):
        r = read_once(Scripted(CPPLUS, pcn_conf=0.6), [FRONT], CPPLUS)
        self.assertEqual(tasks(r), ["classification", "extraction"])
        self.assertEqual(r["uncertain"], [])


class FrontRequiredBackOptional(unittest.TestCase):

    def test_A_front_only_reads(self):
        r = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)
        self.assertEqual((r["status"], r["uncertain"]), ("OK", []))

    def test_B_front_and_correct_back_reads_both_pages(self):
        r = read_once(Scripted(CPPLUS), [FRONT, BACK], CPPLUS)
        self.assertEqual((r["status"], r["uncertain"]), ("OK", []))
        self.assertEqual(sorted(r["document_read_result"]["pages"]), ["E1", "E2"])
        self.assertEqual(tasks(r).count("extraction"), 1)

    def test_C_a_wrong_back_is_set_aside_and_the_front_stays_valid(self):
        r = read_once(Scripted(CPPLUS, back_pcn=OTHER_PCN), [FRONT, OTHER_BACK], CPPLUS)
        self.assertEqual(r["status"], "OK")
        self.assertEqual(list(r["case"].evidence), ["E1"])
        self.assertEqual(r["uncertain"], [])
        self.assertTrue(all(v["pass"] for v in r["fields"].values()))
        self.assertEqual(tasks(r).count("extraction"), 1)


class NoDownstreamWorkInTheRead(unittest.TestCase):

    def test_reading_asks_no_question_and_looks_up_nothing(self):
        r = read_once(Scripted(CPPLUS), [FRONT], CPPLUS)
        case = r["case"]
        self.assertFalse(getattr(case, "questions", None))
        self.assertFalse(set(tasks(r)) - {"classification", "page_references",
                                          "extraction", "identity_verification"})


class RetriesAreCountedAndVisible(unittest.TestCase):

    def _client(self, failures):
        import httpx
        import openai
        from pcn_appeal.llm import OpenAIClient
        client = object.__new__(OpenAIClient)
        client.last_call, client.models = {}, {"extraction": "m"}
        req = httpx.Request("POST", "http://x")
        errs = [openai.APIConnectionError(request=req) for _ in range(failures)]
        ok = mock.Mock()
        ok.choices = [mock.Mock(message=mock.Mock(content="{}"))]
        create = mock.Mock(side_effect=errs + [ok])
        client._c = mock.Mock()
        client._c.chat.completions.create = create
        return client, create

    def test_a_clean_call_records_no_retries(self):
        client, create = self._client(0)
        client._create_with_retries(model="m")
        self.assertEqual((client.last_call["attempts"], client.last_call["retries"]), (1, 0))

    def test_a_transient_failure_is_retried_and_counted(self):
        client, create = self._client(1)
        with mock.patch("time.sleep"):
            client._create_with_retries(model="m")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(client.last_call["retries"], 1)
        self.assertEqual(len(client.last_call["attempt_seconds"]), 2)

    def test_retries_stop_at_the_ceiling(self):
        client, create = self._client(5)
        with mock.patch("time.sleep"), self.assertRaises(Exception):
            client._create_with_retries(model="m")
        self.assertEqual(create.call_count, client.MAX_RETRIES + 1)
        self.assertEqual(client.last_call["retries"], client.MAX_RETRIES)

    def test_exhausted_credit_is_not_retried(self):
        import httpx
        import openai
        client, create = self._client(0)
        resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
        create.side_effect = openai.RateLimitError("429 no credits remaining", response=resp, body=None)
        with mock.patch("time.sleep"), self.assertRaises(openai.RateLimitError):
            client._create_with_retries(model="m")
        self.assertEqual(create.call_count, 1)

    def test_the_audit_row_carries_the_retry_count(self):
        client, _ = self._client(1)
        with mock.patch("time.sleep"):
            r = read_once(_Retrying(client), [FRONT], CPPLUS)
        self.assertTrue(any(c["retries"] == 1 for c in r["calls"]), r["calls"])


class _Retrying(Scripted):
    """A scripted model that reports one retry on its first call, as the real
    client does through `last_call`."""

    def __init__(self, client):
        super().__init__(CPPLUS)
        self.last_call = {}
        self._fresh = True

    def complete_json(self, **kw):
        self.last_call = {"attempts": 2 if self._fresh else 1, "retries": 1 if self._fresh else 0,
                          "attempt_seconds": [0.1, 0.1] if self._fresh else [0.1]}
        self._fresh = False
        return super().complete_json(**kw)


if __name__ == "__main__":
    unittest.main()
