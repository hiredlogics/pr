"""VAL-CUSTOMER-COPY names the sentence and the copied words.

Live, a letter failed three attempts in a row on "Draft pastes customer free-text wording;
rewrite professionally": the message named nothing, so the drafter changed nothing and the
same six-word runs came back ("the car park was very busy"). The check now says which
sentence reuses which words, so a retry can rewrite that sentence and, as a last resort, that
one sentence can be dropped on its own instead of failing the whole letter.
"""
import unittest

from pcn_appeal.engines.validation import ValidationEngine, _customer_prose_pasted, _pasted_phrases
from pcn_appeal.models import Draft, DraftSentence, RetrievalPack

ACCOUNT = ("The car park was very busy and I spent more than twenty minutes driving round. "
           "The payment machine near the entrance was not working so I queued at another one.")


def pack(texts=(ACCOUNT,), quotations=()):
    return RetrievalPack(
        primary_route="X", secondary_routes=[], module_ids=["KB-X-01"], verified_facts={"pcn_number": "T1"},
        fact_refs={}, missing_facts=[], evidence_refs=[], prohibited_claims=[], code_version=None,
        pofa_route="POSTAL", pofa_findings=[], driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES",
        context_chunks=[], lease_clauses=[], claim_plan={"status": "LOCKED", "approved": ["KB-X-01"]},
        case_context={"customer_source_texts": list(texts), "customer_quotations": list(quotations)})


def draft(*texts):
    return Draft("C", [[DraftSentence(t, [], ["KB-X-01"], []) for t in texts]])


def copy_issues(d, p=None):
    return [i for i in ValidationEngine(None).validate(d, p or pack()).issues if i.rule == "VAL-CUSTOMER-COPY"]


class TheCopiedWordsAreNamed(unittest.TestCase):

    def test_the_phrases_found_are_the_ones_actually_shared(self):
        got = _pasted_phrases(ACCOUNT, "I am informed that the car park was very busy at the time.", {})
        self.assertIn("the car park was very busy", got)
        self.assertTrue(all(p in "the car park was very busy at the time" for p in got))

    def test_a_paraphrase_shares_no_six_word_run(self):
        letter = "When the vehicle first arrived, the site was extremely crowded and a space took over twenty minutes to find."
        self.assertEqual(_pasted_phrases(ACCOUNT, letter, {}), [])
        self.assertFalse(_customer_prose_pasted(ACCOUNT, letter, {}))

    def test_the_issue_names_the_sentence_and_the_words_and_only_that_sentence(self):
        bad = "I am informed that the car park was very busy and that parking took a long time."
        ok = "The notice is dated 21 September 2026."
        issues = copy_issues(draft(ok, bad))
        self.assertEqual([i.sentence for i in issues], [bad])
        self.assertIn("the car park was very busy", issues[0].message)
        self.assertIn("different words", issues[0].message)

    def test_each_offending_sentence_is_named_once(self):
        a = "I am informed that the car park was very busy that day."
        b = "I am further informed that the payment machine near the entrance was out of order."
        self.assertEqual(sorted(i.sentence for i in copy_issues(draft(a, b))), sorted([a, b]))

    def test_a_clean_paraphrase_passes(self):
        d = draft("I understand that the site was extremely crowded on arrival, and that the machine by the "
                  "gate was out of order, so payment had to be arranged at a second machine.")
        self.assertEqual(copy_issues(d), [])

    def test_a_justified_quotation_is_still_allowed(self):
        quote = "the car park was very busy"
        p = pack(quotations=[{"text": quote, "reason": "the customer's own phrase, recorded"}])
        self.assertEqual(copy_issues(draft("I note that the car park was very busy."), p), [])

    def test_every_blocking_issue_names_a_sentence_so_the_last_resort_can_drop_it(self):
        from pcn_appeal.orchestrator import AppealPipeline
        bad = "I am informed that the car park was very busy and that parking took a long time."
        d = draft("The notice is dated 21 September 2026.", bad)
        result = ValidationEngine(None).validate(d, pack())
        trimmed, dropped = AppealPipeline._without_failing_sentences(d, result)
        blockers = [i for i in result.issues if i.severity == "BLOCK"]
        if all(i.sentence for i in blockers):
            self.assertEqual(dropped, [bad])
            self.assertNotIn(bad, trimmed.plain_text())


if __name__ == "__main__":
    unittest.main()
