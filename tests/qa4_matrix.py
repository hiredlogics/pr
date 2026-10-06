"""Phase 4 matrix: which questions Question Authority lets a customer see.

Each scenario drives the real pipeline (`AppealPipeline._reanalyse`, which runs
the analysis questions, the materiality filter and the Question Authority) on a
case whose facts are set through FactManager. A scenario returns a `Verdict`;
`run_matrix()` returns every verdict so the same table serves as the baseline
(before a change) and the final matrix (after it).

Nothing here names an operator, a site or a phrase: the facts and modules used
are the KB's own, and the customer wording is plain.

Defect classes (a failing scenario is filed under exactly one):
  QUESTION_MATERIALITY_DEFECT  asked what could not change a ground, or failed
                               to ask what could
  UNKNOWN_AS_FALSE_DEFECT      an unanswered/skipped/not-sure answer became a
                               negative fact or a rejection
  DUPLICATE_QUESTION_DEFECT    the same question was shown again
  ANSWER_PERSISTENCE_DEFECT    an answer did not reach FactManager / eligibility
  PRIORITY_DEFECT              the wrong question was shown first
  SEMANTIC_INPUT_DEFECT        the question stood on a reading it should not have
  ELIGIBILITY_INPUT_DEFECT     the question stood on an eligibility it should not have
  TEST_ERROR                   the scenario itself is wrong
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pcn_appeal.engines.module_eligibility import evaluate_module
from pcn_appeal.models import (CaseFile, Fact, FactSource, FactStatus, SourceKind)
from pcn_appeal.orchestrator import AppealPipeline
from support import ReferenceAnalysisLLM
from test_private_parking_v2 import BASE, fields, EvidenceItem

MATERIALITY, UNKNOWN_AS_FALSE, DUPLICATE = (
    "QUESTION_MATERIALITY_DEFECT", "UNKNOWN_AS_FALSE_DEFECT", "DUPLICATE_QUESTION_DEFECT")
PERSISTENCE, PRIORITY, SEMANTIC = (
    "ANSWER_PERSISTENCE_DEFECT", "PRIORITY_DEFECT", "SEMANTIC_INPUT_DEFECT")
ELIGIBILITY, TEST_ERROR = "ELIGIBILITY_INPUT_DEFECT", "TEST_ERROR"

PAY_Q = {"fact": "payment_made", "text": "Did you pay for parking during this visit?",
         "type": "bool", "material_because": "payment would answer the charge"}
PAY_Q_REWORDED = dict(PAY_Q, text="Was there a payment made for this stay?")


@dataclass
class Verdict:
    case: str
    title: str
    passed: bool
    defect: Optional[str] = None
    detail: str = ""
    shown: list[str] = field(default_factory=list)


class Harness:
    """One case on the real pipeline, with the model's questions scripted."""

    def __init__(self, ask=None, extra_fields=None, narrative="", responses=None,
                 kg_facts=None):
        f = dict(BASE, **(extra_fields or {}))
        resp = {"extraction": [{"fields": fields(**f), "doc_types": {"E1": "PCN"}}]}
        resp.update(responses or {})
        self.llm = ReferenceAnalysisLLM(resp, ask=ask or [])
        self.pipe = AppealPipeline(self.llm)
        self.case = CaseFile("C-QA4", evidence={
            "E1": EvidenceItem("E1", "PCN", "pcn.pdf",
                               text="Parking Charge Notice\nOperator: Acme Parking Ltd")})
        self.narrative = narrative
        self.pipe.ingest(self.case)
        # What the customer has already told us is in FactManager before the first
        # analysis round, as it is in production when a later round runs.
        for name, value in (kg_facts or {}).items():
            self.set(name, value)
        confirm = [n for n, fa in self.case.facts.items() if fa.status == FactStatus.EXTRACTED]
        self.last = self.pipe.confirm(self.case, {}, confirm, narrative)

    # --------------------------------------------------------------- facts
    def set(self, name: str, value: Any, *, kind=SourceKind.ANSWER,
            status=FactStatus.ANSWERED) -> None:
        self.case.put(Fact(f"F-{name}", name, value, status, FactSource(kind, f"qa4:{name}")))

    # ------------------------------------------------------------- rounds
    def round(self) -> list[str]:
        self.last = self.pipe._reanalyse(self.case, self.narrative)
        return self.shown

    def answer(self, fact: str, raw: Any) -> list[str]:
        self.last = self.pipe.answer(self.case, {fact: raw})
        return self.shown

    @property
    def shown(self) -> list[str]:
        return [q["fact"] for q in self.last]

    @property
    def modules(self) -> list[str]:
        return list(getattr(self.case, "analysis_module_ids", []) or [])

    def reviews(self) -> list[dict]:
        return [a for a in self.case.audit if a.get("event") == "question_review"]

    def status(self, module_id: str) -> str:
        view = self.case.fact_view()
        return evaluate_module(self.pipe.kg.modules[module_id], view).status


# ---------------------------------------------------------------- scenarios
SCENARIOS: list[tuple[str, str, Callable[[], Verdict]]] = []


def scenario(letter: str, title: str):
    def deco(fn):
        def run() -> Verdict:
            try:
                return fn()
            except AssertionError as exc:       # pragma: no cover - never raised here
                return Verdict(letter, title, False, TEST_ERROR, str(exc))
        SCENARIOS.append((letter, title, run))
        return run
    return deco


def ok(letter, title, shown=()):
    return Verdict(letter, title, True, shown=list(shown))


def bad(letter, title, defect, detail, shown=()):
    return Verdict(letter, title, False, defect, detail, shown=list(shown))


@scenario("A", "payment_made missing -> the payment question is asked")
def _a():
    h = Harness(ask=[PAY_Q])
    if h.shown == ["payment_made"] and h.status("KB-PAY-01") == "UNRESOLVED":
        return ok("A", "payment_made missing -> the payment question is asked", h.shown)
    return bad("A", "payment_made missing -> the payment question is asked", MATERIALITY,
               f"shown={h.shown} PAY-01={h.status('KB-PAY-01')}", h.shown)


@scenario("B", "asked but unanswered -> UNKNOWN, never FALSE")
def _b():
    t = "asked but unanswered -> UNKNOWN, never FALSE"
    h = Harness(ask=[PAY_Q])
    h.round()                                    # a later round, nothing answered
    view = h.case.fact_view()
    if "payment_made" in view:
        return bad("B", t, UNKNOWN_AS_FALSE, f"payment_made became {view['payment_made']!r}")
    if h.status("KB-PAY-01") != "UNRESOLVED":
        return bad("B", t, UNKNOWN_AS_FALSE, f"PAY-01 {h.status('KB-PAY-01')} with payment unanswered")
    # a module that needs the NEGATIVE of the fact must not be supported by silence
    h.set("no_parking_took_place", True)
    st = h.status("KB-CON-02")
    if st == "SUPPORTED":
        return bad("B", t, UNKNOWN_AS_FALSE, "CON-02 SUPPORTED while payment_made unanswered")
    return ok("B", t)


@scenario("C", "an explicit No is recorded as a confirmed negative and eligibility recomputes")
def _c():
    t = "an explicit No is recorded as a confirmed negative and eligibility recomputes"
    h = Harness(ask=[PAY_Q])
    h.answer("payment_made", "No")
    node = h.case.facts.get("payment_made")
    if not (node and node.usable and node.value is False):
        return bad("C", t, PERSISTENCE, f"fact={getattr(node, 'value', None)!r}")
    if h.status("KB-PAY-01") != "REJECTED":
        return bad("C", t, ELIGIBILITY, f"PAY-01={h.status('KB-PAY-01')} after a No")
    return ok("C", t, h.shown)


@scenario("D", "an answered question never repeats")
def _d():
    t = "an answered question never repeats"
    h = Harness(ask=[PAY_Q])
    h.answer("payment_made", "Yes")
    again = []
    for _ in range(3):
        again += h.round()
    h.llm.ask = [PAY_Q_REWORDED]
    again += h.round()
    if "payment_made" in again:
        return bad("D", t, DUPLICATE, f"re-shown: {again}", again)
    return ok("D", t)


@scenario("E", "a skipped question does not loop and the fact stays UNKNOWN")
def _e():
    t = "a skipped question does not loop and the fact stays UNKNOWN"
    h = Harness(ask=[PAY_Q])
    first = list(h.shown)
    rounds = []
    for _ in range(3):
        rounds += h.round()
    if "payment_made" in rounds:
        return bad("E", t, DUPLICATE, f"re-asked after skip: {rounds}", rounds)
    if "payment_made" in h.case.fact_view():
        return bad("E", t, UNKNOWN_AS_FALSE, "skip wrote a payment_made value")
    if h.status("KB-PAY-01") != "UNRESOLVED":
        return bad("E", t, UNKNOWN_AS_FALSE, f"PAY-01={h.status('KB-PAY-01')} after a skip")
    return ok("E", t, first)


@scenario("F", "PoFA SUPPORTED + payment UNRESOLVED: payment asked, PoFA stays")
def _f():
    t = "PoFA SUPPORTED + payment UNRESOLVED: payment asked, PoFA stays"
    h = Harness(ask=[PAY_Q], extra_fields={"notice_issue_date": "30/06/2026"})
    pofa = [m for m in h.modules if m.startswith("KB-POFA")]
    if not pofa:
        return bad("F", t, TEST_ERROR, f"no PoFA ground in the case: {h.modules}")
    if h.shown != ["payment_made"]:
        return bad("F", t, MATERIALITY, f"shown={h.shown}", h.shown)
    h.round()
    if not [m for m in h.modules if m.startswith("KB-POFA")]:
        return bad("F", t, MATERIALITY, f"PoFA lost after the question: {h.modules}")
    return ok("F", t, h.shown)


@scenario("G", "an irrelevant detail makes no question and no ground")
def _g():
    t = "an irrelevant detail makes no question and no ground"
    junk = {"fact": "tiredness_level", "text": "How tired were you that day?",
            "type": "text", "material_because": "context"}
    plain = Harness(ask=[junk])
    h = Harness(narrative="I was tired that day.", ask=[junk])
    if "tiredness_level" in h.shown or any(r["target_fact"] == "tiredness_level"
                                           and r["decision"] == "APPROVED" for r in h.reviews()):
        return bad("G", t, MATERIALITY, f"shown={h.shown}", h.shown)
    if h.shown != plain.shown:
        return bad("G", t, SEMANTIC, f"the irrelevant account changed the questions: "
                   f"{plain.shown} -> {h.shown}", h.shown)
    if h.modules != plain.modules:
        return bad("G", t, SEMANTIC, f"the irrelevant account changed the grounds: "
                   f"{plain.modules} -> {h.modules}")
    if "tired" not in str(h.case.raw_answers.get("narrative", "")).lower():
        return bad("G", t, TEST_ERROR, "the account was not preserved")
    return ok("G", t)


@scenario("K", "a derivable fact is not asked")
def _k():
    t = "a derivable fact is not asked"
    h = Harness(ask=[
        {"fact": "jurisdiction", "text": "Which country was the car park in?", "type": "text",
         "material_because": "needed for the Code"},
        {"fact": "total_recorded_duration_min", "text": "How many minutes was the car on site?",
         "type": "int", "material_because": "duration"}])
    asked = [r for r in h.reviews() if r["decision"] == "APPROVED"
             and r["target_fact"] in ("jurisdiction", "total_recorded_duration_min")]
    if asked:
        return bad("K", t, MATERIALITY, f"approved={[r['target_fact'] for r in asked]}", h.shown)
    return ok("K", t)


@scenario("L", "a REJECTED module's fact is not asked")
def _l():
    t = "a REJECTED module's fact is not asked"
    h = Harness(kg_facts={"payment_made": False},
                ask=[{"fact": "keying_error_type", "type": "choice",
                      "text": "Does the registration entered on the payment differ from the vehicle's registration?",
                      "options": ["NONE", "MINOR", "DIFFERENT_VEHICLE"],
                      "material_because": "keying"}])
    h.round()
    if h.status("KB-KEY-01") != "REJECTED":
        return bad("L", t, TEST_ERROR, f"KEY-01={h.status('KB-KEY-01')}")
    if "keying_error_type" in h.shown:
        return bad("L", t, MATERIALITY, f"shown={h.shown}", h.shown)
    return ok("L", t)


@scenario("M", "a BLOCKED module's fact is not asked")
def _m():
    t = "a BLOCKED module's fact is not asked"
    h = Harness(kg_facts={"payment_made": True},
                ask=[{"fact": "dropoff_activity", "type": "bool",
                      "text": "Was the vehicle stopped only to drop off or collect a passenger?",
                      "material_because": "activity"}])
    h.round()
    if h.status("KB-ACT-02") != "BLOCKED":
        return bad("M", t, TEST_ERROR, f"ACT-02={h.status('KB-ACT-02')}")
    if "dropoff_activity" in h.shown:
        return bad("M", t, MATERIALITY, f"shown={h.shown}", h.shown)
    return ok("M", t)


@scenario("N", "a support-only unresolved module with no ground behind it is not asked")
def _n():
    t = "a support-only unresolved module with no ground behind it is not asked"
    h = Harness(ask=[{"fact": "landholder_cancellation_route_available", "type": "bool",
                      "text": "Does the site's landholder operate its own cancellation or exemption process for this car park?",
                      "material_because": "supports the hospital route"}])
    h.round()
    if "landholder_cancellation_route_available" in h.shown:
        return bad("N", t, MATERIALITY, f"shown={h.shown}", h.shown)
    return ok("N", t)


HOSP_Q = {"fact": "hospital_attendance", "type": "bool",
          "text": "Was the visit connected with attendance at a hospital or other medical facility?",
          "material_because": "hospital"}
MULTI_Q = {"fact": "multiple_visits", "type": "bool",
           "text": "Did the vehicle visit the site more than once that day?",
           "material_because": "ANPR"}


@scenario("O", "several unresolved substantive modules: the highest-value one first")
def _o():
    t = "several unresolved substantive modules: the highest-value one first"
    a = Harness(ask=[HOSP_Q, PAY_Q])
    b = Harness(ask=[PAY_Q, HOSP_Q])
    if a.shown != ["payment_made"] or b.shown != ["payment_made"]:
        return bad("O", t, PRIORITY, f"order a={a.shown} b={b.shown}", a.shown)
    return ok("O", t, a.shown)


@scenario("P", "one answer resolving several modules is asked once")
def _p():
    t = "one answer resolving several modules is asked once"
    h = Harness(ask=[PAY_Q, PAY_Q_REWORDED])
    if h.shown != ["payment_made"]:
        return bad("P", t, DUPLICATE, f"shown={h.shown}", h.shown)
    h.answer("payment_made", "No")
    if "payment_made" in h.shown:
        return bad("P", t, DUPLICATE, "asked again after the answer", h.shown)
    return ok("P", t, h.shown)


@scenario("Q", "an answer that SUPPORTS a module stops questions about it")
def _q():
    t = "an answer that SUPPORTS a module stops questions about it"
    h = Harness(ask=[PAY_Q])
    h.answer("payment_made", "Yes")
    if h.status("KB-PAY-01") != "SUPPORTED":
        return bad("Q", t, ELIGIBILITY, f"PAY-01={h.status('KB-PAY-01')}")
    h.llm.ask = [{"fact": "payment_method", "type": "choice",
                  "options": ["APP", "MACHINE", "PHONE", "WEBSITE", "OTHER"],
                  "text": "How was the payment made?", "material_because": "supports payment"}]
    h.round()
    if "payment_made" in h.shown:
        return bad("Q", t, DUPLICATE, f"shown={h.shown}", h.shown)
    return ok("Q", t, h.shown)


@scenario("R", "an answer that REJECTS or BLOCKS modules stops their questions")
def _r():
    t = "an answer that REJECTS or BLOCKS modules stops their questions"
    h = Harness(ask=[PAY_Q])
    h.answer("payment_made", "Yes")
    h.llm.ask = [
        {"fact": "dropoff_activity", "type": "bool", "material_because": "activity",
         "text": "Was the vehicle stopped only to drop off or collect a passenger?"},
        {"fact": "no_parking_took_place", "type": "bool", "material_because": "consideration",
         "text": "Did the vehicle leave the site without parking?"}]
    h.round()
    if h.status("KB-ACT-02") != "BLOCKED" or h.status("KB-CON-02") != "REJECTED":
        return bad("R", t, ELIGIBILITY,
                   f"ACT-02={h.status('KB-ACT-02')} CON-02={h.status('KB-CON-02')}")
    if h.shown:
        return bad("R", t, MATERIALITY, f"shown={h.shown}", h.shown)
    return ok("R", t)


@scenario("S", "provider failure with PoFA supported: no customer-derived question, PoFA continues")
def _s():
    t = "provider failure with PoFA supported: no customer-derived question, PoFA continues"
    h = Harness(narrative="I paid at the machine but the car park was full of queues.",
                extra_fields={"notice_issue_date": "30/06/2026"})
    h.llm.responses["semantic_extraction"] = []
    orig = h.llm.complete_json

    def failing(*, task, system, user, images=None):
        if task == "semantic_extraction":
            raise RuntimeError("provider down")
        return orig(task=task, system=system, user=user, images=images)
    h.llm.complete_json = failing
    h.round()
    pofa = [m for m in h.modules if m.startswith("KB-POFA")]
    customer = [q for q in h.last if q["fact"].startswith(("account_clarification",))]
    if customer:
        return bad("S", t, SEMANTIC, f"customer-derived question shown: {h.shown}", h.shown)
    if not pofa:
        return bad("S", t, MATERIALITY, f"PoFA dropped: {h.modules}", h.shown)
    return ok("S", t, h.shown)


@scenario("T", "nothing can change the appeal -> zero questions")
def _t():
    t = "nothing can change the appeal -> zero questions"
    h = Harness()
    junk = [{"fact": "weather_that_day", "text": "What was the weather like?", "type": "text",
             "material_because": "context"},
            {"fact": "car_colour", "text": "What colour is the car?", "type": "text",
             "material_because": "context"}]
    review = h.pipe.authority.review(h.case, junk, selected=h.modules)
    if review.approved:
        return bad("T", t, MATERIALITY, f"approved={[q['fact'] for q in review.approved]}")
    if review.shown:
        return bad("T", t, MATERIALITY, f"shown={[q['fact'] for q in review.shown]}")
    return ok("T", t)



# ----------------------------------------------------------------- H, I, J
HDROP = ("I dropped my daughter off at the entrance, then left the car park, "
         "came back later and picked her up.")
ALREADY_SAID = {"dropoff_activity", "pickup_activity", "multiple_visits", "left_site",
                "returned_same_day", "purpose_of_visit", "possible_vehicle_departure"}


@scenario("H", "drop-off, left, returned, pick-up, semantics clear -> no redundant question")
def _h():
    t = "drop-off, left, returned, pick-up, semantics clear -> no redundant question"
    h = Harness(narrative=HDROP)
    h.round()
    again = [r["target_fact"] for r in h.reviews()
             if r["decision"] == "APPROVED" and r["target_fact"] in ALREADY_SAID]
    if again or set(h.shown) & ALREADY_SAID:
        return bad("H", t, MATERIALITY, f"asked what the account says: {again or h.shown}", h.shown)
    return ok("H", t, h.shown)


def _reply(status="UNDERSTOOD", clarification=None, **kw):
    out = {"status": status, "summary": "The customer described the visit.",
           "concepts": [], "events": [], "narrative_atoms": [], "relationships": [],
           "material_relevance": [], "uncertainties": [], "clarification": clarification}
    out.update(kw)
    return out


@scenario("I", "an ambiguous material account -> exactly one precise clarification")
def _i():
    t = "an ambiguous material account -> exactly one precise clarification"
    q = "Which of these did you mean: the ticket or the card payment?"
    h = Harness(narrative="My mum said she would sort it.",
                responses={"semantic_extraction": [
                    _reply("NEEDS_CLARIFICATION", {"question": q, "ambiguity": "'it' is unclear"}),
                    _reply()]})
    if len(h.shown) != 1 or not h.shown[0].startswith("account_clarification"):
        return bad("I", t, SEMANTIC, f"shown={h.shown}", h.shown)
    first = h.shown[0]
    h.answer(first, "The card payment.")
    if first in h.shown:
        return bad("I", t, DUPLICATE, "the clarification was asked again", h.shown)
    return ok("I", t, [first])


@scenario("J", "a non-material ambiguity -> no question")
def _j():
    t = "a non-material ambiguity -> no question"
    h = Harness(narrative="We got there at about lunchtime.",
                responses={"semantic_extraction": [
                    _reply("UNDERSTOOD", uncertainties=["the exact time of arrival is not stated"])]})
    if any(f.startswith("account_clarification") for f in h.shown):
        return bad("J", t, SEMANTIC, f"shown={h.shown}", h.shown)
    return ok("J", t, h.shown)


# ------------------------------------------- the same rules on the KB-gate path
# The model's own questions and the KB gates both reach the authority; a KB gate
# names the modules the answer is said to unlock (`unlocks`), and that claim has
# to be checked against what those modules now are, not trusted.
def gate_q(fact, text, module, qtype="bool", **kw):
    return dict({"fact": fact, "text": text, "type": qtype, "kb_gated": True,
                 "unlocks": [module], "related_module": module, "source": "kb_gate",
                 "material_because": f"gates {module}"}, **kw)


DROP_Q = "Was the vehicle stopped only to drop off or collect a passenger?"
KEY_Q = "Does the registration entered on the payment differ from the vehicle's registration?"


@scenario("U1", "a KB gate for a BLOCKED module is not asked")
def _u1():
    t = "a KB gate for a BLOCKED module is not asked"
    h = Harness(kg_facts={"payment_made": True},
                ask=[gate_q("dropoff_activity", DROP_Q, "KB-ACT-02")])
    if h.status("KB-ACT-02") != "BLOCKED":
        return bad("U1", t, TEST_ERROR, f"ACT-02={h.status('KB-ACT-02')}")
    if "dropoff_activity" in h.shown:
        return bad("U1", t, ELIGIBILITY, f"asked to unlock a BLOCKED module: {h.shown}", h.shown)
    return ok("U1", t)


@scenario("U2", "a KB gate for a REJECTED module is not asked")
def _u2():
    t = "a KB gate for a REJECTED module is not asked"
    h = Harness(kg_facts={"payment_made": False},
                ask=[gate_q("keying_error_type", KEY_Q, "KB-KEY-01", "choice",
                            options=["NONE", "MINOR", "DIFFERENT_VEHICLE"])])
    if h.status("KB-KEY-01") != "REJECTED":
        return bad("U2", t, TEST_ERROR, f"KEY-01={h.status('KB-KEY-01')}")
    if "keying_error_type" in h.shown:
        return bad("U2", t, ELIGIBILITY, f"asked to unlock a REJECTED module: {h.shown}", h.shown)
    return ok("U2", t)


@scenario("U3", "a KB gate for a SUPPORTED module is not asked")
def _u3():
    t = "a KB gate for a SUPPORTED module is not asked"
    h = Harness(kg_facts={"multiple_visits": True},
                ask=[gate_q("anpr_sequence_incomplete",
                            "Did the camera record show only one of your entry or exit?",
                            "KB-ANPR-01")])
    if h.status("KB-ANPR-01") != "SUPPORTED":
        return bad("U3", t, TEST_ERROR, f"ANPR-01={h.status('KB-ANPR-01')}")
    if "anpr_sequence_incomplete" in h.shown:
        return bad("U3", t, MATERIALITY, f"asked about a ground already SUPPORTED: {h.shown}", h.shown)
    return ok("U3", t)


@scenario("U4", "a payment gate is not asked when the allegation is a prohibition")
def _u4():
    t = "a payment gate is not asked when the allegation is a prohibition"
    h = Harness(extra_fields={"alleged_breach": "Parked in a no parking area"},
                ask=[gate_q("payment_made", PAY_Q["text"], "KB-PAY-01")])
    if h.status("KB-PAY-01") not in ("UNRESOLVED", "REJECTED"):
        return bad("U4", t, TEST_ERROR, f"PAY-01={h.status('KB-PAY-01')}")
    if "payment_made" in h.shown:
        return bad("U4", t, ELIGIBILITY, f"asked a payment question of a prohibition: {h.shown}", h.shown)
    return ok("U4", t)


@scenario("U5", "an evidence-only module nothing substantive depends on is not asked")
def _u5():
    t = "an evidence-only module nothing substantive depends on is not asked"
    h = Harness(ask=[])
    asked = [r["target_fact"] for r in h.reviews() if r["decision"] == "APPROVED"]
    if "anpr_duration_disputed" in asked:
        return bad("U5", t, MATERIALITY, f"approved={asked}", h.shown)
    return ok("U5", t)


@scenario("U6", "a support-only module with no substantive ground behind it is not asked")
def _u6():
    t = "a support-only module with no substantive ground behind it is not asked"
    q = {"fact": "signage_issue_type", "type": "choice",
         "options": ["TERM_PROMINENCE", "CONFLICTING", "CHANGED_OR_DAMAGED", "OTHER"],
         "text": "What was the problem with the signs?", "material_because": "signage"}
    h = Harness(kg_facts={"signage_issue_raised": True}, ask=[q])
    modules = {m: h.status(m) for m in ("KB-SIGN-02", "KB-SIGN-03", "KB-SIGN-04")}
    approved = any(r["target_fact"] == "signage_issue_type" and r["decision"] == "APPROVED"
                   for r in h.reviews())
    if approved:
        return bad("U6", t, MATERIALITY, f"asked for supporting modules {modules}", h.shown)
    return ok("U6", t)


@scenario("U7", "a hard blocker the customer can settle is asked, against the right module")
def _u7():
    t = "a hard blocker the customer can settle is asked, against the right module"
    h = Harness(kg_facts={"payment_made": False, "short_presence_before_acceptance": True},
                ask=[gate_q("permitted_period_ended",
                            "Was there a paid or permitted parking period that ended before the vehicle left?",
                            "KB-CON-01")])
    q = next((x for x in h.last if x["fact"] == "permitted_period_ended"), None)
    rows = [r for r in h.reviews() if r["target_fact"] == "permitted_period_ended"
            and r["decision"] == "APPROVED"]
    if not rows:
        return bad("U7", t, MATERIALITY, "the blocker question was never approved")
    if "KB-CON-01" not in str(rows[-1].get("source_module_ids") or rows[-1].get("related_module")):
        return bad("U7", t, ELIGIBILITY, f"not attributed to CON-01: {rows[-1].get('related_module')}")
    return ok("U7", t)


@scenario("U8", "the internal question contract is complete and not shown to the customer")
def _u8():
    t = "the internal question contract is complete and not shown to the customer"
    h = Harness(ask=[PAY_Q])
    row = next((r for r in h.reviews() if r["target_fact"] == "payment_made"
                and r["decision"] == "APPROVED" and r["shown"]), None)
    need = {"question_id", "fact_key", "question", "source_module_ids", "current_status",
            "materiality_reason", "possible_effect"}
    missing = sorted(need - set(row or {}))
    if missing:
        return bad("U8", t, ELIGIBILITY, f"contract fields missing: {missing}")
    if row["current_status"] != "UNRESOLVED" or "KB-PAY-01" not in row["source_module_ids"]:
        return bad("U8", t, ELIGIBILITY, f"contract wrong: {row['current_status']} {row['source_module_ids']}")
    shown = h.last[0]
    if set(shown) - {"fact", "text", "type", "options"}:
        return bad("U8", t, TEST_ERROR, f"customer view carries {sorted(set(shown))}")
    return ok("U8", t)


@scenario("U9", "'not sure' to a choice question is UNKNOWN and settled, not an error")
def _u9():
    t = "'not sure' to a choice question is UNKNOWN and settled, not an error"
    q = {"fact": "payment_method", "type": "choice",
         "options": ["APP", "MACHINE", "PHONE", "WEBSITE", "OTHER"],
         "text": "How was the payment made?", "material_because": "payment route"}
    h = Harness(kg_facts={"payment_made": True}, ask=[q])
    try:
        h.answer("payment_method", "not sure")
    except ValueError as exc:
        return bad("U9", t, UNKNOWN_AS_FALSE, f"raised {exc}")
    if "payment_method" in h.case.fact_view():
        return bad("U9", t, UNKNOWN_AS_FALSE, "an uncertain answer was stored as a value")
    if "payment_method" in h.shown or "payment_method" in h.round():
        return bad("U9", t, DUPLICATE, "asked again after 'not sure'")
    return ok("U9", t)


@scenario("U10", "an empty answer is UNKNOWN and settled")
def _u10():
    t = "an empty answer is UNKNOWN and settled"
    for qtype, fact, text in (("bool", "payment_made", PAY_Q["text"]),
                              ("int", "exit_delay_min", "How many minutes did leaving take?")):
        h = Harness(ask=[{"fact": fact, "type": qtype, "text": text, "material_because": "x"}])
        try:
            h.answer(fact, "")
        except (ValueError, TypeError) as exc:
            return bad("U10", t, UNKNOWN_AS_FALSE, f"{qtype}: raised {exc!r}")
        if fact in h.case.fact_view():
            return bad("U10", t, UNKNOWN_AS_FALSE, f"{qtype}: empty answer stored as a value")
        if fact in h.round():
            return bad("U10", t, DUPLICATE, f"{qtype}: asked again after an empty answer")
    return ok("U10", t)


@scenario("U11", "the lifecycle of a question is readable: pending, answered, unresolved, superseded")
def _u11():
    t = "the lifecycle of a question is readable: pending, answered, unresolved, superseded"
    from pcn_appeal.engines import question_authority as qa
    life = getattr(qa, "lifecycle", None)
    if life is None:
        return bad("U11", t, PERSISTENCE, "no lifecycle is recorded")
    h = Harness(ask=[PAY_Q])
    state = {r["target_fact"]: r["state"] for r in life(h.case)}
    if state.get("payment_made") != "PENDING":
        return bad("U11", t, PERSISTENCE, f"pending expected: {state}")
    h.answer("payment_made", "No")
    state = {r["target_fact"]: r["state"] for r in life(h.case)}
    if state.get("payment_made") != "ANSWERED":
        return bad("U11", t, PERSISTENCE, f"answered expected: {state}")
    g = Harness(ask=[PAY_Q])
    g.answer("payment_made", "not sure")
    state = {r["target_fact"]: r["state"] for r in life(g.case)}
    if state.get("payment_made") != "UNRESOLVED":
        return bad("U11", t, PERSISTENCE, f"unresolved expected: {state}")
    return ok("U11", t)


@scenario("U12", "a skipped run leaves every unknown UNKNOWN: nothing is invented, nothing rejected")
def _u12():
    t = "a skipped run leaves every unknown UNKNOWN: nothing is invented, nothing rejected"
    h = Harness(ask=[PAY_Q])
    before = dict(h.case.fact_view())
    result = h.pipe.auto_appeal(h.case, "", None, skip_remaining=True)
    after = h.case.fact_view()
    invented = {k: after[k] for k in ("payment_made", "multiple_visits", "permitted_period_ended")
                if k in after and k not in before}
    if invented:
        return bad("U12", t, UNKNOWN_AS_FALSE, f"skip wrote {invented}")
    if h.status("KB-PAY-01") != "UNRESOLVED":
        return bad("U12", t, UNKNOWN_AS_FALSE, f"PAY-01={h.status('KB-PAY-01')} after a skip")
    return ok("U12", t)


@scenario("U13", "driver identity is never asked, however the question is worded or sourced")
def _u13():
    t = "driver identity is never asked, however the question is worded or sourced"
    h = Harness(ask=[
        {"fact": "driver_name", "type": "text", "text": "What is the name of the driver?",
         "material_because": "x"},
        gate_q("driver_status", "Were you driving at the time?", "KB-POFA-01")])
    if {"driver_name", "driver_status"} & set(h.shown):
        return bad("U13", t, MATERIALITY, f"shown={h.shown}", h.shown)
    return ok("U13", t)


@scenario("U14", "independent grounds are additive: the customer's answer never removes PoFA")
def _u14():
    t = "independent grounds are additive: the customer's answer never removes PoFA"
    h = Harness(ask=[PAY_Q], extra_fields={"notice_issue_date": "30/06/2026"})
    before = {m for m in h.modules if m.startswith("KB-POFA")}
    for raw in ("No", "Yes"):
        g = Harness(ask=[PAY_Q], extra_fields={"notice_issue_date": "30/06/2026"})
        g.answer("payment_made", raw)
        after = {m for m in g.modules if m.startswith("KB-POFA")}
        if after != before or not after:
            return bad("U14", t, MATERIALITY, f"{raw}: PoFA {sorted(before)} -> {sorted(after)}")
    return ok("U14", t)


@scenario("U15", "an empty answer to a clarification settles it: no loop")
def _u15():
    t = "an empty answer to a clarification settles it: no loop"
    q = "Which of these did you mean: the ticket or the card payment?"
    h = Harness(narrative="My mum said she would sort it.",
                responses={"semantic_extraction": [
                    _reply("NEEDS_CLARIFICATION", {"question": q, "ambiguity": "'it' is unclear"}),
                    _reply("NEEDS_CLARIFICATION", {"question": q, "ambiguity": "'it' is unclear"})]})
    first = list(h.shown)
    if not first or not first[0].startswith("account_clarification"):
        return bad("U15", t, TEST_ERROR, f"no clarification to answer: {first}")
    seen = []
    h.answer(first[0], "")
    for _ in range(3):
        seen += h.round()
    if first[0] in seen:
        return bad("U15", t, DUPLICATE, f"asked again after an empty answer: {seen}", seen)
    return ok("U15", t)


@scenario("U16", "a fact that only settles a hard blocker is a blocker-tier question for that module")
def _u16():
    t = "a fact that only settles a hard blocker is a blocker-tier question for that module"
    h = Harness(extra_fields={"alleged_breach": "Parked in a no parking area"},
                kg_facts={"dropoff_activity": True, "permitted_period_ended": False,
                          "short_presence_before_acceptance": False,
                          "no_parking_took_place": False},
                ask=[gate_q("payment_made", PAY_Q["text"], "KB-ACT-02")])
    if h.status("KB-ACT-02") != "UNRESOLVED":
        return bad("U16", t, TEST_ERROR, f"ACT-02={h.status('KB-ACT-02')}")
    row = next((r for r in h.reviews() if r["target_fact"] == "payment_made"
                and r["decision"] == "APPROVED"), None)
    if not row:
        return bad("U16", t, MATERIALITY, "the blocker question was not approved")
    if row.get("tier") != 3 or row.get("source_module_ids") != ["KB-ACT-02"]:
        return bad("U16", t, PRIORITY, f"tier={row.get('tier')} modules={row.get('source_module_ids')}")
    return ok("U16", t, h.shown)


@scenario("U17", "the question cap never turns UNKNOWN into FALSE or UNRESOLVED into REJECTED")
def _u17():
    t = "the question cap never turns UNKNOWN into FALSE or UNRESOLVED into REJECTED"
    h = Harness(ask=[PAY_Q])
    before = {m: h.status(m) for m in ("KB-PAY-01", "KB-ANPR-01")}
    asked = list(h.shown)
    for _ in range(6):                          # every question skipped, one after another
        asked += h.round()
    after = {m: h.status(m) for m in ("KB-PAY-01", "KB-ANPR-01")}
    if len(asked) != len(set(asked)):
        return bad("U17", t, DUPLICATE, f"asked more than once: {asked}", asked)
    if before != after or any(v != "UNRESOLVED" for v in after.values()):
        return bad("U17", t, UNKNOWN_AS_FALSE, f"{before} -> {after}")
    invented = [k for k in ("payment_made", "multiple_visits") if k in h.case.fact_view()]
    if invented:
        return bad("U17", t, UNKNOWN_AS_FALSE, f"facts written without an answer: {invented}")
    return ok("U17", t, asked)


@scenario("U18", "'not sure' to a yes/no question is settled UNKNOWN: the module stays UNRESOLVED")
def _u18():
    t = "'not sure' to a yes/no question is settled UNKNOWN: the module stays UNRESOLVED"
    h = Harness(ask=[PAY_Q])
    h.answer("payment_made", "not sure")
    again = h.round() + h.round()
    if "payment_made" in again or "payment_made" in h.shown:
        return bad("U18", t, DUPLICATE, f"asked again: {again}", again)
    if "payment_made" in h.case.fact_view():
        return bad("U18", t, UNKNOWN_AS_FALSE, "an uncertain answer was stored as a value")
    if h.status("KB-PAY-01") != "UNRESOLVED":
        return bad("U18", t, UNKNOWN_AS_FALSE, f"PAY-01={h.status('KB-PAY-01')}")
    return ok("U18", t)


def run_matrix(only: Optional[set[str]] = None) -> list[Verdict]:
    return [fn() for letter, _, fn in SCENARIOS if not only or letter in only]


def table(verdicts: list[Verdict]) -> str:
    rows = []
    for v in verdicts:
        mark = "PASS" if v.passed else "FAIL"
        rows.append(f"{v.case:2} {mark}  {v.title}"
                    + ("" if v.passed else f"   [{v.defect}] {v.detail}"))
    return "\n".join(rows)


if __name__ == "__main__":
    print(table(run_matrix()))
