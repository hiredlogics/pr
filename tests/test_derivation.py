"""P8 derivation layer: notice facts -> KB gate facts.

Fixtures are the facts read off real notices from the 7 Oct 2026 test run
(names and addresses omitted). Each test states what the notice alone should
settle, so a gate the document can answer is never left for the customer.
"""
import unittest
from datetime import date

from pcn_appeal.engines.derivation import classify_breach, derive
from pcn_appeal.models import CaseFile, Fact, FactSource, FactStatus, SourceKind


def doc(case, name, value, status=FactStatus.CONFIRMED):
    case.put(Fact(f"F-{name}", name, value, status, FactSource(SourceKind.DOCUMENT, "test")))


def calc(case, name, value):
    case.put(Fact(f"F-{name}", name, value, FactStatus.DERIVED,
                  FactSource(SourceKind.CALCULATION, "duration_calc")))


def notice(location, breach, event=date(2026, 7, 9), ata="BPA", minutes=None):
    case = CaseFile("T")
    doc(case, "parking_location", location)
    doc(case, "alleged_breach", breach)
    doc(case, "parking_event_date", event)
    doc(case, "operator_ata", ata)
    if minutes is not None:
        calc(case, "total_recorded_duration_min", minutes)
    return case


class BreachClassification(unittest.TestCase):
    def test_wordings_from_real_notices(self):
        self.assertEqual(classify_breach(
            "Your vehicle has overstayed the maximum time period allowed"), "OVERSTAY")
        self.assertEqual(classify_breach(
            "the vehicle exceeded the maximum stay prominently displayed"), "OVERSTAY")
        self.assertEqual(classify_breach(
            "Use of Pick Up / Drop Off Zone without making a valid payment"), "DROPOFF_ZONE")
        self.assertEqual(classify_breach("No valid parking session"), "NO_PAYMENT")
        self.assertEqual(classify_breach(
            "Parked longer than the time paid for"), "OVERSTAY")

    def test_unknown_wording_is_not_guessed(self):
        self.assertIsNone(classify_breach("Breach of terms and conditions"))
        self.assertIsNone(classify_breach(None))


class Derivation(unittest.TestCase):
    def test_luton_dropoff_three_minutes(self):
        case = notice("Luton Airport Pick Up / Drop Off Zone",
                      "Use of Pick Up / Drop Off Zone without making a valid payment",
                      minutes=3)
        derive(case)
        self.assertEqual(case.get("alleged_breach_type"), "DROPOFF_ZONE")
        # Client 2026-10-08: not because it is a drop-off zone, but because no
        # overstay is alleged and the whole stay is inside the consideration period.
        self.assertIs(case.get("permitted_period_ended"), False)
        self.assertIn("no_overstay_alleged", case.facts["permitted_period_ended"].source.ref)
        self.assertIs(case.get("short_presence_before_acceptance"), True)
        self.assertIs(case.get("dropoff_site"), True)
        self.assertIs(case.get("relevant_land"), False)

    def test_supermarket_overstay(self):
        case = notice("Sainsburys - Harringay",
                      "Your vehicle has overstayed the maximum time period allowed",
                      minutes=207)
        derive(case)
        self.assertEqual(case.get("alleged_breach_type"), "OVERSTAY")
        self.assertIs(case.get("permitted_period_ended"), True)
        self.assertIs(case.get("customer_only_site"), True)
        # A long stay does not prove no time was spent considering terms (D-03).
        self.assertFalse(case.has("short_presence_before_acceptance"))
        self.assertFalse(case.has("relevant_land"))

    def test_bm_is_a_customer_site_a_shopping_centre_only_prompts(self):
        # Client 2026-10-08: named stores are customer sites; a retail park or
        # shopping centre only prompts the genuine-customer questions.
        case = notice("B&M Chatham - ME4 4HA", "No valid parking session")
        derive(case)
        self.assertIs(case.get("customer_only_site"), True)
        case = notice("Quayside Shopping Centre (M50 3AH)", "No valid parking session")
        derive(case)
        self.assertFalse(case.has("customer_only_site"))
        self.assertIs(case.get("retail_site"), True)
        # NO_PAYMENT does not settle whether a permitted period existed.
        self.assertFalse(case.has("permitted_period_ended"))

    def test_short_presence_needs_a_resolved_code_version(self):
        # No event date, so no Code version applies and nothing is derived from
        # one. The date is what version-controls the sector Single Code.
        case = notice("Luton Airport Pick Up / Drop Off Zone", "drop off zone",
                      event=None, minutes=3)
        derive(case)
        self.assertFalse(case.has("short_presence_before_acceptance"))

    def test_a_notice_without_a_trade_body_logo_still_resolves_the_code(self):
        """The Single Code is version-controlled by event date; the trade body
        only narrows the choice while versions actually differ by it. A notice
        that shows no ATA (or an operator absent from the local name table, as
        any unseen operator would be) must still get the Code provisions the
        event date resolves - otherwise consideration/grace analysis silently
        dies for every operator not already known."""
        case = notice("Luton Airport Pick Up / Drop Off Zone", "drop off zone",
                      ata="NOT_SHOWN", minutes=3)
        derive(case)
        self.assertIs(case.get("short_presence_before_acceptance"), True)

    def test_customer_answer_is_never_overwritten(self):
        case = notice("Luton Airport Pick Up / Drop Off Zone", "drop off zone", minutes=3)
        case.put(Fact("F-short_presence_before_acceptance", "short_presence_before_acceptance",
                      False, FactStatus.ANSWERED, FactSource(SourceKind.ANSWER, "answer")))
        derive(case)
        self.assertIs(case.get("short_presence_before_acceptance"), False)

    def test_railway_station_is_relevant_land_since_the_2025_order(self):
        # Railway Byelaws land was brought back into "relevant land" from
        # 26 Dec 2025, so a station car park must not be marked as byelaw land.
        case = notice("Stevenage Railway Station Car Park", "No valid parking session")
        derive(case)
        self.assertFalse(case.has("relevant_land"))

    def test_nothing_written_when_nothing_matches(self):
        case = notice("Canada Water Estate, SE16 7LL", "Breach of terms and conditions")
        self.assertEqual(derive(case), {})


class OverstayReconciliation(unittest.TestCase):
    """The allegation text and the operator's own figures must not be left
    asserting opposite things about the same parking period (VAL-CONFLICT)."""

    def _overstay_case(self, duration_min, permitted="3 hours"):
        case = notice("Retail Park", "Maximum stay exceeded", minutes=duration_min)
        doc(case, "permitted_period", permitted)
        derive(case)
        return case

    def test_operator_times_showing_no_overstay_retract_the_text_derived_flag(self):
        # Allegation says overstay; the operator's own times say the vehicle
        # left 117 minutes BEFORE the permitted period ended.
        case = self._overstay_case(63)
        self.assertEqual(case.get("overstay_min"), -117)
        self.assertIs(case.get("permitted_period_ended"), False)
        self.assertFalse(case.has("within_grace_period"))

    def test_leaving_exactly_at_the_limit_is_not_an_overstay(self):
        case = self._overstay_case(180)
        self.assertEqual(case.get("overstay_min"), 0)
        self.assertIs(case.get("permitted_period_ended"), False)

    def test_a_real_overstay_beyond_grace_is_kept_and_not_in_grace(self):
        case = self._overstay_case(215)
        self.assertEqual(case.get("overstay_min"), 35)
        self.assertIs(case.get("permitted_period_ended"), True)
        self.assertIs(case.get("within_grace_period"), False)

    def test_a_real_overstay_inside_grace_is_in_grace(self):
        case = self._overstay_case(185)
        self.assertEqual(case.get("overstay_min"), 5)
        self.assertIs(case.get("permitted_period_ended"), True)
        self.assertIs(case.get("within_grace_period"), True)

    def test_a_period_written_in_words_is_read_like_one_in_digits(self):
        """Notices print the period in words at least as often as in digits.

        A period the engine cannot read is not a harmless gap: the overstay
        becomes uncalculable, so the grace question goes to the customer, and a
        long overstay answered with a small number would be recorded as within
        grace. The operator's own figures must settle it instead.
        """
        for written, digits in (("four hours", "4 hours"),
                                ("two hours", "2 hours"),
                                ("ninety minutes", "90 minutes"),
                                ("one hour", "1 hour")):
            with self.subTest(permitted=written):
                spelled = self._overstay_case(390, permitted=written)
                numeric = self._overstay_case(390, permitted=digits)
                self.assertEqual(spelled.get("overstay_min"),
                                 numeric.get("overstay_min"),
                                 f"{written!r} must read the same as {digits!r}")
                self.assertIsNotNone(spelled.get("overstay_min"))

    def test_a_long_overstay_is_settled_by_the_notice_not_by_asking(self):
        """The grace question must not be reachable where it cannot help."""
        case = self._overstay_case(390, permitted="four hours")
        self.assertEqual(case.get("overstay_min"), 150)
        self.assertIs(case.get("within_grace_period"), False)
        # No customer answer was involved: the operator's figures decided it.
        self.assertFalse(case.has("exit_delay_min"))

    def test_an_unreadable_period_still_leaves_the_overstay_unknown(self):
        """Failing closed: an allegation with no period stated stays undecided
        rather than guessing one."""
        case = self._overstay_case(390, permitted="Exceeded maximum stay")
        self.assertIsNone(case.get("overstay_min"))


class GraceGateResolvability(unittest.TestCase):
    """KB-GRACE-01's gate must be able to reach FALSE. It previously carried an
    {exists: exit_delay_min} leg inside an `any`, and because that fact is
    QUESTION-only, {exists:} could never be FALSE - so the gate stayed UNKNOWN
    even once within_grace_period was known FALSE, and the engine kept asking
    for a delay figure that could not change the outcome."""

    def _gate(self):
        import yaml
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        kb = yaml.safe_load((root / "pcn_appeal/data/kb_modules.yaml").read_text())
        mods = kb.get("modules") or kb
        return [m for m in mods if m.get("module_id") == "KB-GRACE-01"][0]["use_when"]

    def test_overstay_beyond_grace_rejects_the_module(self):
        from pcn_appeal.rules.dsl import evaluate3
        self.assertIs(evaluate3(self._gate(), {
            "permitted_period_ended": True, "within_grace_period": False}), False)

    def test_overstay_inside_grace_selects_the_module(self):
        from pcn_appeal.rules.dsl import evaluate3
        self.assertIs(evaluate3(self._gate(), {
            "permitted_period_ended": True, "within_grace_period": True}), True)

    def test_no_kb_gate_hides_a_question_only_fact_inside_an_any(self):
        """Generic guard: an {exists: <QUESTION-only fact>} leg inside an `any`
        can never evaluate FALSE, so it pins the whole gate at UNKNOWN. No
        module may reintroduce that shape."""
        import yaml
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        kb = yaml.safe_load((root / "pcn_appeal/data/kb_modules.yaml").read_text())
        prod = yaml.safe_load((root / "pcn_appeal/data/fact_producers.yaml").read_text())
        question_only = {k for k, v in prod["facts"].items() if list(v) == ["QUESTION"]}

        def traps(node, found):
            if isinstance(node, dict):
                for key, val in node.items():
                    if key == "any":
                        for leg in (val if isinstance(val, list) else [val]):
                            if isinstance(leg, dict) and leg.get("exists") in question_only:
                                found.append(leg["exists"])
                    traps(val, found)
            elif isinstance(node, list):
                for item in node:
                    traps(item, found)
            return found

        offenders = {m.get("module_id"): traps(m.get("use_when"), [])
                     for m in (kb.get("modules") or kb)
                     if traps(m.get("use_when"), [])}
        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
