"""The fixed letter frame: code writes it, the model writes only the grounds.

  frame (code)        addresses, date, Re: line, salutation, opening paragraph with the
                      driver sentence, the one request to cancel, sign-off, name
  grounds (model)     everything between the opening and the closing request
  validators          VAL-TEMPLATE holds the whole letter to the frame and the style rules
  display registration  stored twice: normalised for logic, as printed for the letter

Every check uses several unrelated operators, registrations and wordings: none of this is
about one case.
"""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import test_scenarios as ts
from pcn_appeal import letter_frame as lf
from pcn_appeal.engines import letter_template_check as ltc
from pcn_appeal.engines.validation import ValidationEngine, _contains_token, _ident
from pcn_appeal.letterhead import full_letter, letter_document
from pcn_appeal.models import (CaseFile, CaseState, Draft, DraftSentence, EvidenceItem,
                               RetrievalPack)

OPERATORS = [("Harbour Gate Parking Ltd", "HG20481177", "LK19 WXP", "LK19WXP"),
             ("Brightwater Car Parks", "BW-550322", "YN22 HGF", "YN22HGF"),
             ("Tolland Estates", "TE00412873", "GY19 HDS", "GY19HDS")]


def para(*texts, refs=("STRUCTURAL",)):
    return [DraftSentence(t, [], list(refs), []) for t in texts]


def facts(pcn="HG20481177", disp="LK19 WXP", vrm="LK19WXP", **more):
    return {"pcn_number": pcn, "vrm_display": disp, "vrm": vrm, **more}


def pack(f=None, **over):
    base = dict(primary_route="X", secondary_routes=[], module_ids=["KB-X-01"],
                verified_facts=f or facts(), fact_refs={}, missing_facts=[], evidence_refs=[],
                prohibited_claims=[], code_version=None, pofa_route="POSTAL", pofa_findings=[],
                driver_status="UNIDENTIFIED", jurisdiction="ENGLAND_WALES", context_chunks=[],
                lease_clauses=[], claim_plan={"status": "LOCKED", "approved": ["KB-X-01"]})
    base.update(over)
    return RetrievalPack(**base)


def ground(text, who="KB-X-01"):
    return [DraftSentence(text, [], [who], [])]


# --------------------------------------------------------------------------- 1
class TheFrameIsFixedText(unittest.TestCase):

    def test_the_opening_says_the_driver_sentence_word_for_word_whoever_the_operator(self):
        for _, pcn, disp, _ in OPERATORS:
            with self.subTest(pcn=pcn):
                first, second = lf.opening(pcn, disp)
                self.assertEqual(second, "I make no admission as to the identity of the driver, "
                                         "and I will not be naming the driver.")
                self.assertIn(disp, first)
                self.assertIn(pcn, first)

    def test_the_closing_is_one_request_and_one_next_step(self):
        a, b = lf.closing("TE00412873")
        self.assertEqual(a, "I ask you to cancel Parking Charge Notice TE00412873 and confirm "
                            "this in writing.")
        self.assertIn("independent appeals service", b)
        self.assertEqual(len([t for t in (a, b) if lf.CANCEL_REQUEST.search(t)]), 1)

    def test_a_missing_value_is_a_visible_placeholder_never_a_dropped_block(self):
        first, _ = lf.opening(None, None)
        self.assertIn("[PCN NUMBER]", first)
        self.assertIn("[VEHICLE REGISTRATION]", first)
        self.assertIn("[PCN NUMBER]", lf.closing(None)[0])

    def test_a_recipient_of_a_drivers_letter_answers_as_the_recipient_with_the_same_sentence(self):
        first, second = lf.opening("NC10293847", "BG74 EVB", recipient=True)
        self.assertIn("recipient", first)
        self.assertNotIn("registered keeper", first)
        self.assertEqual(second, lf.DRIVER_SENTENCE)


# --------------------------------------------------------------------------- 2
class TheFrameReplacesWhatTheModelWroteAroundTheGrounds(unittest.TestCase):

    def draft(self):
        return Draft("C", [
            para("I write as the keeper. I dispute this charge.", "I will not name the driver."),
            ground("The notice was issued 25 days after the event."),
            para("The notice alleges overstay at the site."),            # a structural content paragraph
            ground("The operator has not shown that signs were visible."),
            para("I invite the operator to cancel this charge.", "I request cancellation."),
        ])

    def test_opening_and_closing_are_the_codes_and_the_grounds_are_untouched(self):
        out = lf.apply_fixed_frame(self.draft(), facts())
        texts = [" ".join(s.text for s in p) for p in out.paragraphs]
        self.assertTrue(texts[0].startswith("I am the registered keeper of vehicle LK19 WXP"))
        self.assertIn(lf.DRIVER_SENTENCE, texts[0])
        self.assertEqual(texts[-1], " ".join(lf.closing("HG20481177")))
        self.assertIn("The notice was issued 25 days after the event.", texts)
        self.assertIn("The operator has not shown that signs were visible.", texts)
        self.assertIn("The notice alleges overstay at the site.", texts)      # a middle one stays
        self.assertNotIn("I write as the keeper", " ".join(texts))

    def test_there_is_exactly_one_request_to_cancel_and_one_driver_sentence(self):
        out = lf.apply_fixed_frame(self.draft(), facts())
        sentences = [s.text for s in out.sentences()]
        self.assertEqual(len([t for t in sentences if lf.CANCEL_REQUEST.search(t)]), 1)
        self.assertEqual(sum(t == lf.DRIVER_SENTENCE for t in sentences), 1)

    def test_a_model_that_repeats_the_driver_disclaimer_inside_a_ground_is_not_repeated(self):
        d = Draft("C", [ground("The notice is late. I make no admission as to the identity of the driver.")])
        out = lf.apply_fixed_frame(d, facts())
        self.assertEqual(sum("no admission" in s.text for s in out.sentences()), 1)

    def test_applying_it_twice_changes_nothing(self):
        once = lf.apply_fixed_frame(self.draft(), facts())
        twice = lf.apply_fixed_frame(once, facts())
        self.assertEqual([[s.text for s in p] for p in once.paragraphs],
                         [[s.text for s in p] for p in twice.paragraphs])

    def test_identifiers_a_model_re_typed_are_restored_to_exactly_as_displayed(self):
        for _, pcn, disp, norm in OPERATORS:
            d = Draft("C", [ground(f"Notice {pcn.lower()} for {norm} was issued late.")])
            out = lf.apply_fixed_frame(d, facts(pcn=pcn, disp=disp, vrm=norm))
            body = " ".join(s.text for s in out.paragraphs[1])
            with self.subTest(pcn=pcn):
                self.assertIn(pcn, body)
                self.assertIn(disp, body)
                self.assertNotIn(norm, body.replace(disp, ""))

    def test_nothing_is_framed_when_the_model_found_no_ground(self):
        from pcn_appeal.drafting.drafter import with_fixed_frame
        d = Draft("C", [], no_ground_reason="nothing supported")
        self.assertIs(with_fixed_frame(d, pack()), d)


# --------------------------------------------------------------------------- 3
class TheLetterAroundTheBodyIsAlwaysComplete(unittest.TestCase):

    def case(self, **f):
        c = CaseFile("C-L")
        from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind
        for k, v in f.items():
            c.put(Fact(f"F-{k}", k, v, FactStatus.CONFIRMED, FactSource(SourceKind.DOCUMENT, "E1#p1")))
        return c

    def test_every_block_is_present_with_a_placeholder_for_what_was_not_read(self):
        doc = letter_document(self.case(), on=date(2026, 10, 10))
        self.assertEqual(doc["from_lines"], ["[YOUR FULL NAME]", "[YOUR ADDRESS]"])
        self.assertEqual(doc["to_lines"], ["[PARKING COMPANY NAME]", "[OPERATOR APPEALS ADDRESS]"])
        self.assertEqual(doc["subject"], "Re: Parking Charge Notice [PCN NUMBER]")
        self.assertEqual(doc["signature"], "[YOUR FULL NAME]")
        self.assertFalse(doc["from_complete"])
        self.assertFalse(doc["to_complete"])
        self.assertEqual(doc["date"], "10 October 2026")

    def test_the_reference_line_shows_the_registration_as_printed(self):
        doc = letter_document(self.case(pcn_number="BW-550322", vrm="YN22HGF",
                                        vrm_display="YN22 HGF"), on=date(2026, 10, 10))
        self.assertEqual(doc["subject"], "Re: Parking Charge Notice BW-550322, vehicle YN22 HGF")

    def test_a_complete_case_has_no_placeholder_and_the_full_letter_runs_in_order(self):
        c = self.case(keeper_name="Mr J Sample", keeper_address="1 Example Street\nLeeds LS2 9XX",
                      operator_name="TEST PARKING SERVICES LTD",
                      operator_address="PO Box 000\nTesttown TE1 1ST", pcn_number="TPS1", vrm="AB12CDE")
        doc = letter_document(c, on=date(2026, 10, 10))
        self.assertTrue(doc["from_complete"] and doc["to_complete"])
        text = full_letter("BODY", doc)
        self.assertNotIn("[", text)
        order = ["Mr J Sample", "TEST PARKING SERVICES LTD", "10 October 2026", "Re: Parking Charge Notice",
                 "Dear Sir or Madam,", "BODY", "Yours faithfully,"]
        self.assertEqual([text.index(x) for x in order], sorted(text.index(x) for x in order))
        self.assertTrue(text.rstrip().endswith("Mr J Sample"))


# --------------------------------------------------------------------------- 4
class ValTemplateHoldsTheWholeLetterToTheFrame(unittest.TestCase):

    def framed(self, *grounds, f=None):
        body = Draft("C", [ground(g) for g in grounds] or [ground("The notice was issued late.")])
        return lf.apply_fixed_frame(body, f or facts())

    def rules(self, draft, p=None):
        return [m for m, _ in ltc.check(draft, p or pack())]

    def test_a_clean_framed_letter_passes_for_any_operator(self):
        for _, pcn, disp, norm in OPERATORS:
            f = facts(pcn=pcn, disp=disp, vrm=norm)
            with self.subTest(pcn=pcn):
                self.assertEqual(self.rules(self.framed(f=f), pack(f)), [])

    def test_a_letter_without_the_driver_sentence_is_refused(self):
        d = Draft("C", [para("I am appealing."), ground("Late notice."),
                        para(*lf.closing("HG20481177"))])
        self.assertTrue(any("driver" in m for m in self.rules(d)))

    def test_two_requests_to_cancel_are_refused_and_one_is_not(self):
        d = self.framed()
        self.assertFalse(any("requests to cancel" in m for m in self.rules(d)))
        d.paragraphs.insert(1, para("I invite the operator to cancel this charge.", refs=("KB-X-01",)))
        self.assertTrue(any("requests to cancel" in m for m in self.rules(d)))

    def test_a_retyped_registration_or_pcn_is_refused_naming_the_sentence(self):
        """The frame step repairs these before validation; the validator is the net for
        anything that reaches it re-typed anyway (a later step, a hand-built draft)."""
        for text in ("Vehicle LK19WXP was recorded entering the site.",
                     "Notice hg20481177 was issued by post."):
            d = Draft("C", [para(*lf.opening("HG20481177", "LK19 WXP")), ground(text),
                            para(*lf.closing("HG20481177"))])
            with self.subTest(text=text):
                got = ltc.check(d, pack())
                self.assertTrue([m for m, s in got if "re-typed" in m and s == text], got)

    def test_a_postcode_the_case_does_not_hold_or_re_typed_is_refused(self):
        held = pack(facts(site_postcode="BS1 6QF", operator_address="PO Box 4410, Leeds LS1 1AA"))
        ok = self.framed("The site at BS1 6QF is a public car park.", f=held.verified_facts)
        self.assertEqual([m for m, _ in ltc.check(ok, held) if "Postcode" in m], [])
        for text, kind in (("The site is at BS1 9ZZ.", "not one this case holds"),
                           ("The site is at BS16QF.", "re-typed")):
            with self.subTest(text=text):
                got = [m for m, _ in ltc.check(self.framed(text, f=held.verified_facts), held)]
                self.assertTrue(any(kind in m for m in got), got)

    def test_banned_phrases_and_the_keeper_in_the_third_person_are_refused(self):
        for text in ("The verified facts for this case show a late notice.",
                     "Our records show the vehicle left on time.",
                     "In light of the above the charge should be cancelled.",
                     "The VRM was matched by the camera.",
                     "The keeper states that the machine was broken.",
                     "According to the keeper, signs were small."):
            with self.subTest(text=text):
                self.assertTrue([m for m, s in ltc.check(self.framed(text), pack()) if s == text])

    def test_first_person_attribution_is_fine(self):
        text = "I understand that the payment machine near the entrance was not working."
        self.assertEqual([m for m, s in ltc.check(self.framed(text), pack()) if s == text], [])

    def test_wording_that_does_not_match_the_type_of_notice_is_refused(self):
        windscreen = pack(pofa_route="WINDSCREEN")
        text = "The ANPR camera records were not provided."
        self.assertTrue([m for m, s in ltc.check(self.framed(text), windscreen) if s == text])
        self.assertEqual([m for m, s in ltc.check(self.framed(text), pack(pofa_route="POSTAL")) if s == text], [])
        overstay = pack(facts(alleged_breach="Exceeded the maximum stay"))
        permit = "The operator has not shown the vehicle needed a permit."
        self.assertTrue([m for m, s in ltc.check(self.framed(permit, f=overstay.verified_facts), overstay)
                         if s == permit])
        about_permit = pack(facts(alleged_breach="No valid permit displayed"))
        self.assertEqual([m for m, s in ltc.check(self.framed(permit, f=about_permit.verified_facts),
                                                  about_permit) if s == permit], [])

    def test_the_engine_refuses_it_as_VAL_TEMPLATE_only_when_the_frame_is_enforced(self):
        p = pack()
        d = Draft("C", [ground("The verified facts show a late notice.")])
        self.assertNotIn("VAL-TEMPLATE", [i.rule for i in ValidationEngine(None).validate(d, p).issues])
        enforced = ValidationEngine(None, enforce_frame=True).validate(d, p).issues
        self.assertIn("VAL-TEMPLATE", [i.rule for i in enforced])

    def test_the_closing_may_name_the_independent_appeals_service_and_nothing_else_may(self):
        engine = ValidationEngine(None, allowed_next_step=lf.ALLOWED_NEXT_STEPS, enforce_frame=True)
        ok = engine.validate(self.framed(), pack())
        self.assertNotIn("VAL-STAGE", [i.rule for i in ok.issues])
        bad = engine.validate(self.framed("Please contact POPLA about this."), pack())
        self.assertIn("VAL-STAGE", [i.rule for i in bad.issues])


# --------------------------------------------------------------------------- 5
class TheRegistrationIsStoredTwice(unittest.TestCase):

    def test_display_is_what_the_notice_printed_and_a_corrected_registration_wins(self):
        self.assertEqual(lf.display_reg({"vrm_display": "AB12 CDE", "vrm": "AB12CDE"}), "AB12 CDE")
        self.assertEqual(lf.display_reg({"vrm_display": "ab12cde", "vrm": "AB12CDE"}), "ab12cde")
        # corrected by the customer: the old printed form is not the registration any more
        self.assertEqual(lf.display_reg({"vrm_display": "AB12 CDE", "vrm": "XY70ZZZ"}), "XY70 ZZZ")
        self.assertEqual(lf.display_reg({"vrm": "XY70ZZZ"}), "XY70 ZZZ")
        self.assertIsNone(lf.display_reg({}))

    def test_a_spaced_registration_is_found_wherever_it_falls_in_the_sentence(self):
        for lead in ("", "x ", "one two ", "a b c "):
            with self.subTest(lead=lead):
                self.assertTrue(_contains_token(f"{lead}vehicle AB12 CDE here", _ident("AB12CDE")))
        self.assertFalse(_contains_token("vehicle AB12 CDF here", _ident("AB12CDE")))

    def test_extraction_keeps_the_printed_form_beside_the_normalised_one(self):
        with mock.patch("pcn_appeal.llm.probe", return_value={"provider": "t", "models": {"drafting": "r"}}):
            case, pipe = ts.make_case({"vrm": "AB12 CDE"})
            pipe.ingest(case)
        self.assertEqual(case.get("vrm"), "AB12CDE")              # for logic
        self.assertEqual(case.get("vrm_display"), "AB12 CDE")     # for the letter


# --------------------------------------------------------------------------- 6
class ARealLetterThroughThePipeline(unittest.TestCase):

    def run_case(self, extra, story, answers, evidence=None, doc_types=None):
        with mock.patch("pcn_appeal.llm.probe", return_value={"provider": "t", "models": {"drafting": "r"}}):
            case, pipe = ts.make_case(extra, evidence, doc_types)
            return case, ts.run(case, pipe, story, answers)

    def test_each_letter_has_the_frame_once_and_obeys_the_style_rules(self):
        for operator, pcn, disp, norm in OPERATORS:
            extra = {"operator_name": operator, "pcn_number": pcn, "vrm": disp}
            with self.subTest(operator=operator):
                case, out = self.run_case(
                    extra, "The engine would not start and I waited for the recovery truck.",
                    {"vehicle_immobilised": "yes", "immobilisation_prevented_departure": "yes",
                     "recovery_attended": "yes", "permitted_period_ended": "yes", "exit_delay_min": 40},
                    {"E2": EvidenceItem("E2", "RECOVERY_REPORT", "r.pdf", text="job")},
                    {"E2": "RECOVERY_REPORT"})
                self.assertEqual(out.state, CaseState.RELEASED, out.validation.issues)
                sentences = [s.text for s in out.draft.sentences()]
                self.assertEqual(sum(t == lf.DRIVER_SENTENCE for t in sentences), 1)
                self.assertEqual(len([t for t in sentences if lf.CANCEL_REQUEST.search(t)]), 1)
                self.assertFalse(lf.BANNED_PHRASES.search(out.letter))
                self.assertIn(disp, out.letter)
                self.assertNotIn(norm, out.letter.replace(disp, ""))
                self.assertTrue(out.letter.index(lf.DRIVER_SENTENCE) < out.letter.index("I ask you to cancel"))


if __name__ == "__main__":
    unittest.main()
