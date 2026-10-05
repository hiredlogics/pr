"""Question Authority (P3): every question is approved or rejected here before
a customer can see it.

Questions come from several places - the case-analysis model, the KB gates of
a ground analysis proposed (`kb_gated`), narrative hypotheses (P2), document
conflicts and confirmations (P1), and the site-postcode / trade-association
checks. Each of those is a *candidate*. None of them is shown until this
module has decided that the answer is genuinely needed to decide an issue the
case can actually raise. Nothing is asked because a field is empty, a template
or module exists, a pack looks thin, or more information would be nice.
Zero questions is a valid outcome, and silence is preferred to an irrelevant
question.

A candidate is APPROVED only if every rule holds:

  dedupe  not already known (under any alias), already answered, already
          asked, asked twice in one round, or already covered by something
          the customer said (a narrative fact or a hypothesis about it)
  R1      a supported issue: an in-force KB module gated on or requiring the
          fact and compatible with the allegation - or
          a case-integrity issue (two documents disagree, the customer
          contradicted a document) or an administrative unlock the source
          proved deterministically (postcode / trade association)
  R2      the fact is genuinely missing
  R3      the documents cannot answer it: not a notice field, not something a
          supporting document shows, not a value the engines calculate, not
          already recovered automatically
  R4      the customer can reasonably answer it: no legal interpretation,
          nothing only the operator holds, nothing about driver identity
  R5      the answer could change the outcome: with YES and with NO (or each
          option) the related module's gate must not come out the same

Approved questions are ranked - highest impact on the outcome, then the
strongest supported module, then least customer effort - and ONE is shown.
The rest wait for the next round, when the answer just given may have made
them pointless.

The full question object (question_id, target_fact, related_module,
material_reason, impact_if_yes, impact_if_no) stays internal. The customer
sees the text and how to answer (customer_safe.customer_question). Every
decision, approved or rejected, is an admin-only trace row (audit event
`question_review`, `/trace`, `/cases/{id}/facts`).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..fact_graph import canonical
from ..fact_ownership import DOCUMENT_OWNED, EVIDENCE_OWNED
from ..kg.graph import KnowledgeGraph
from ..models import CaseFile
from ..rules.dsl import PredicateError, evaluate

APPROVED, REJECTED = "APPROVED", "REJECTED"

# Where a candidate came from. Only the sources marked as an issue below may
# stand on something other than a KB module (R1).
MODEL, KB_GATE, HYPOTHESIS = "case_analysis", "kb_gate", "hypothesis"
CONFLICT, CONFIRMATION = "document_conflict", "fact_confirmation"
POSTCODE, TRADE_BODY = "site_postcode", "operator_ata"

# Case-integrity issues: the case cannot be decided until the customer resolves
# them, so they outrank every ground and need no KB module.
INTEGRITY_SOURCES = frozenset({CONFLICT, CONFIRMATION})
# Administrative unlocks: the source has already computed, deterministically,
# which in-force modules the answer would open (recovery.postcode_unlocks,
# AnalysisEngine._ata_would_unlock). The authority checks those modules exist
# and are in force; it does not re-derive the calculation.
UNLOCK_SOURCES = frozenset({POSTCODE, TRADE_BODY})
# Notice fields the customer can read off the notice or the signs when the
# extractor could not. Every other notice field is the document's to answer.
READABLE_BY_CUSTOMER = frozenset({"site_postcode", "operator_ata"})

# ------------------------------------------------------------- dedupe (spec 5)
# Something the customer already said answers the question, even though the
# graph holds it under a different name. Fact vocabulary only: no operator,
# site or module names.
COVERED_BY: dict[str, tuple[tuple[str, Any], ...]] = {
    # "I went to the supermarket to shop" already says the visit was to use
    # the premises; asking "was the visit connected with using the premises?"
    # repeats it back.
    "genuine_customer": (("visited_premises", True),),
}

# ----------------------------------------------------------- R1: allegation fit
# A module can be offered for the case and still be unable to answer the
# allegation. These are the allegation classes where a whole route is
# logically incapable of changing the outcome: no payment, tariff keying or
# end-of-period grace can permit parking where parking is prohibited. Generic
# shapes of the allegation wording, not operator or site rules. An unclassified
# allegation excludes nothing.
ALLEGATION_CLASSES: tuple[tuple[str, re.Pattern], ...] = (
    ("PROHIBITION", re.compile(
        r"\b(?:no[ -]parking|no[ -]stopping|no[ -]waiting|prohibited|not (?:a|an) "
        r"(?:designated |authorised )?(?:parking|bay)|outside (?:of )?(?:a )?(?:marked |designated )?"
        r"(?:bay|parking area)|restricted (?:area|zone)|yellow (?:line|hatch)|hatch(?:ed|ing)|"
        r"keep clear|fire (?:lane|route|exit)|emergency access|footway|pavement|grass(?:ed)? verge|"
        r"obstruct)", re.I)),
)
ROUTES_IRRELEVANT_TO: dict[str, frozenset[str]] = {
    "PROHIBITION": frozenset({"PAYMENT", "KEYING", "GRACE"}),
}

# ------------------------------------------------------- R4: answerable by them
# Wording that asks the customer for a legal conclusion or the law's name.
LEGAL_INTERPRETATION = re.compile(
    r"\b(?:pofa|schedule 4|protection of freedoms|relevant land|keeper liability|legally|"
    r"lawful(?:ly)?|unlawful|enforceable|contractual(?:ly)?|contract (?:was|is) formed|"
    r"code of practice|consideration period|grace period|landowner|land ?holder|"
    r"authority to (?:operate|enforce|issue)|entitled to (?:issue|enforce|charge)|"
    r"valid (?:notice|claim)|compliant)\b", re.I)
# Things only the operator holds.
OPERATOR_HELD = re.compile(
    r"(?:validation_log|landowner|signage_plan|anpr_raw|authority_|camera_|operator_records|"
    r"contract_with)", re.I)
DRIVER_IDENTITY = re.compile(
    r"\b(?:who was (?:driving|the driver)|name of the driver|were you (?:the )?driv|"
    r"driver'?s (?:name|identity|address)|identify the driver)\b", re.I)

EFFORT = {"bool": 0, "choice": 1, "int": 2, "date": 2, "text": 3}
UNKNOWN = None          # three-valued gate result: could go either way
_ORDER = {False: 0, None: 1, True: 2}


@dataclass
class Review:
    """One round's decisions. `shown` holds at most one question."""
    approved: list[dict] = field(default_factory=list)
    shown: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)


def question_id(case: CaseFile, fact: str, text: str) -> str:
    """Stable per case, fact and wording: the same question re-proposed on the
    next round is recognisably the same question."""
    digest = hashlib.sha1(f"{case.case_id}|{fact}|{_norm(text)}".encode()).hexdigest()[:12]
    return f"Q-{digest}"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def allegation_class(breach: Any) -> Optional[str]:
    text = str(breach or "")
    for name, rx in ALLEGATION_CLASSES:
        if rx.search(text):
            return name
    return None


# ----------------------------------------------------------- three-valued gate
def tri(pred: Any, facts: dict[str, Any], unknown: set[str]) -> Optional[bool]:
    """Evaluate a KB predicate where the facts in `unknown` are not yet known.

    True/False when the answer is already determined whatever the unknown facts
    turn out to be, None when it still depends on them. Same operators as
    rules.dsl.evaluate, which this mirrors for the known facts.
    """
    if pred in (None, {}):
        return False
    if not isinstance(pred, dict) or len(pred) != 1:
        raise PredicateError(f"Predicate must be a single-key dict: {pred!r}")
    op, arg = next(iter(pred.items()))
    if op == "always":
        return bool(arg)
    if op == "all":
        vals = [tri(p, facts, unknown) for p in arg]
        if any(v is False for v in vals):
            return False
        return True if all(v is True for v in vals) else UNKNOWN
    if op == "any":
        vals = [tri(p, facts, unknown) for p in arg]
        if any(v is True for v in vals):
            return True
        return False if all(v is False for v in vals) else UNKNOWN
    if op == "not":
        v = tri(arg, facts, unknown)
        return None if v is None else not v
    if op == "has_evidence":
        return arg in (facts.get("evidence_kinds") or [])
    name = arg if isinstance(arg, str) else arg[0]
    if name in unknown:
        return UNKNOWN
    if facts.get(name) is _ANY:
        # Some answer, value not known: present, but any comparison is open.
        if op in ("is", "exists"):
            return True
        if op == "missing":
            return False
        return UNKNOWN
    return evaluate(pred, facts)


class QuestionAuthority:
    """Approves or rejects every candidate question. Stateless: decisions are
    written to the case's audit log as `question_review` rows."""

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    # --------------------------------------------------------------- review
    def review(self, case: CaseFile, candidates: Iterable[dict], *,
               selected: Iterable[str] = (),
               prior_rejections: Iterable[dict] = (), show: int = 1) -> Review:
        """Decide every candidate; return the approved ones ranked and the one
        to show. `selected` is the claim plan's modules (an answer that could
        rule one out is material); `prior_rejections` are the analysis
        pre-checks' drops, recorded so the trace covers every question that
        was generated.

        Not restricted to the semantic candidate window analysis was shown:
        a module whose gate an answer satisfies is pinned into that window
        on the next round (AnalysisEngine._ensure_gate_satisfied_visible),
        so the window cannot decide whether an answer matters."""
        out = Review()
        facts = case.fact_view()
        self._selected = frozenset(selected or ())
        seen_facts: set[str] = set()
        seen_texts: set[str] = set(self._texts_already_asked(case))
        round_no = 1 + sum(1 for a in case.audit if a.get("event") == "question_round")

        for r in prior_rejections:
            out.rows.append(self._row(case, r, REJECTED, r.get("reason", "rejected"), round_no,
                                      stage="analysis_precheck"))

        for cand in candidates:
            cand = dict(cand or {})
            fact = canonical(str(cand.get("fact") or "").strip())
            text = str(cand.get("text") or "").strip()
            cand["fact"] = fact
            verdict, reason, extra = self._decide(case, cand, facts,
                                                  seen_facts, seen_texts)
            if verdict == APPROVED:
                q = self._question(case, cand, extra)
                out.approved.append(q)
                seen_facts.add(fact)
                seen_texts.add(_norm(text))
                out.rows.append(self._row(case, q, APPROVED, reason, round_no))
            else:
                out.rows.append(self._row(case, cand, REJECTED, reason, round_no))

        out.approved.sort(key=lambda q: q["_rank"])
        for i, q in enumerate(out.approved):
            q["priority"] = i + 1
        out.shown = out.approved[:max(0, show)]
        shown_ids = {q["question_id"] for q in out.shown}
        for row in out.rows:
            if row["decision"] == APPROVED:
                row["shown"] = row["question_id"] in shown_ids
                row["priority"] = next(q["priority"] for q in out.approved
                                       if q["question_id"] == row["question_id"])
                if not row["shown"]:
                    row["reason"] += "; held for a later round (one question at a time)"
        for q in out.approved:
            q.pop("_rank", None)

        case.audit.append({"event": "question_round", "round": round_no,
                           "approved": len(out.approved), "shown": [q["fact"] for q in out.shown],
                           "rejected": sum(1 for r in out.rows if r["decision"] == REJECTED)})
        for row in out.rows:
            case.audit.append({"event": "question_review", **row})
        return out

    # ------------------------------------------------------------- decide
    def _decide(self, case, cand, facts, seen_facts, seen_texts):
        fact, text = cand["fact"], cand.get("text") or ""
        source = cand.get("source") or MODEL
        integrity = source in INTEGRITY_SOURCES

        if not fact or not text:
            return REJECTED, "no target fact or no wording", None

        # -------------------------------------------------------- dedupe / R2
        if fact in seen_facts:
            return REJECTED, "duplicate: another question for this fact was approved this round", None
        if not integrity:
            if case.has(fact):
                return REJECTED, "fact already confirmed", None
            if fact in (case.raw_answers or {}):
                return REJECTED, "customer already answered this", None
            if fact in case.asked_questions:
                return REJECTED, "question already asked", None
            if _norm(text) in seen_texts:
                return REJECTED, "duplicate: the same question was already asked", None
            covered = self._covered_by(case, fact)
            if covered:
                return REJECTED, f"already established by what the customer said ({covered})", None
            if source != HYPOTHESIS and self._hypothesis_covers(case, fact):
                return REJECTED, "a narrative hypothesis already covers this fact", None

        # ------------------------------------------------------------------ R4
        if DRIVER_IDENTITY.search(text):
            return REJECTED, "R4: asks about driver identity", None
        if LEGAL_INTERPRETATION.search(text):
            return REJECTED, "R4: asks the customer for a legal interpretation", None
        if OPERATOR_HELD.search(fact):
            return REJECTED, "R4: only the operator holds this (request it in the letter)", None

        # ------------------------------------------------------------------ R3
        if not integrity:
            recovery = case.recovery_report or {}
            if fact in set(recovery.get("do_not_ask") or []) | set((recovery.get("recovered") or {})):
                return REJECTED, "R3: recovered automatically from the documents", None
            if fact in set(recovery.get("operator_requestable") or []):
                return REJECTED, "R4: operator-requestable; request it in the letter", None
            if fact in DOCUMENT_OWNED and fact not in READABLE_BY_CUSTOMER:
                return REJECTED, "R3: a notice field; the documents answer it", None
            if fact in EVIDENCE_OWNED:
                return REJECTED, "R3: shown by a supporting document, not asked", None
            from .analysis import is_internal_fact
            if is_internal_fact(fact):
                return REJECTED, "R3: derived by the engines, not asked", None

        # ---------------------------------------------------------- R1 + R5
        if integrity:
            return APPROVED, "case integrity: the case cannot be decided until this is resolved", {
                "related_module": None, "issue": "CASE_INTEGRITY",
                "material_reason": cand.get("material_reason")
                or "the documents and the customer's answers disagree",
                "impact_if_yes": "the case continues on the confirmed value",
                "impact_if_no": "the case continues on the confirmed value",
                "_rank": (0, 0, 0, EFFORT.get(cand.get("type"), 3))}
        if source in UNLOCK_SOURCES or (
                cand.get("kb_gated") and cand.get("unlocks")):
            mods = [m for m in (cand.get("unlocks") or [])
                    if m in self.kg.modules and self.kg.modules[m].status == "ACTIVE"]
            if not mods and source in UNLOCK_SOURCES:
                return REJECTED, "R1: no in-force module would be unlocked", None
            if mods:
                best = max(mods, key=lambda m: self.kg.modules[m].strength)
                return APPROVED, f"R5: answering would unlock {', '.join(mods)}", {
                    "related_module": best, "issue": "MODULE",
                    "material_reason": cand.get("material_reason")
                    or f"needed to decide whether {best} applies",
                    "impact_if_yes": f"{best} can be considered",
                    "impact_if_no": f"{best} stays unavailable",
                    "_rank": (1, -1, -self.kg.modules[best].strength,
                              EFFORT.get(cand.get("type"), 3))}
            # kb_gated without resolvable unlocks → fall through to module materiality.

        return self._module_materiality(case, cand, facts)

    def _module_materiality(self, case, cand, facts):
        fact = cand["fact"]
        named = cand.get("related_module")
        related = [m for m in self.kg.active_modules()
                   if fact in self.kg.gating_facts(m.module_id)
                   or fact in (m.required_facts or [])]
        if named:
            related = [m for m in related if m.module_id == named]
        if not related:
            return REJECTED, ("R1: no in-force module depends on this fact"
                              if not named else f"R1: {named} does not depend on this fact"), None
        cls = allegation_class(facts.get("alleged_breach"))
        excluded = ROUTES_IRRELEVANT_TO.get(cls or "", frozenset())
        fitting = [m for m in related if str(getattr(m.route, "value", m.route)) not in excluded]
        if not fitting:
            return REJECTED, (f"R1: the allegation ({cls.lower()}) cannot be answered by "
                              f"{', '.join(sorted({str(getattr(m.route, 'value', m.route)) for m in related}))}"
                              ), None

        best, best_rank, best_impact = None, None, None
        for m in fitting:
            impact = self._flip(m, fact, cand, facts, self._selected)
            if impact is None:
                continue
            decisive = impact["decisive"]
            rank = (1, -(2 if decisive else 1), -m.strength, EFFORT.get(cand.get("type"), 3))
            if best_rank is None or rank < best_rank:
                best, best_rank, best_impact = m, rank, impact
        if best is None:
            ruled = sorted({str(getattr(m.route, "value", m.route)) for m in related
                            if m not in fitting})
            if ruled:
                return REJECTED, (f"R1: the allegation ({cls.lower()}) cannot be answered by "
                                  f"{', '.join(ruled)}; R5: no other module's result changes"), None
            return REJECTED, "R5: every possible answer leads to the same result", None
        return APPROVED, f"R5: the answer changes whether {best.module_id} applies", {
            "related_module": best.module_id, "issue": "MODULE",
            "material_reason": cand.get("material_reason")
            or f"needed to decide whether {best.module_id} ({best.topic}) applies",
            "impact_if_yes": best_impact["yes"], "impact_if_no": best_impact["no"],
            "_rank": best_rank}

    def _flip(self, module, fact, cand, facts, selected=frozenset()) -> Optional[dict]:
        """R5. The module's gate now, and under each possible answer.

        An answer matters only if it could OPEN the module (move it from
        ruled out to possible, or from possible to applies) - or, for a module
        already selected for the letter, rule it out. A fact the module only
        uses to exclude itself ("not payment_made") is never asked on its own
        account: answering it can only take a ground away. `do_not_use_when`
        is evaluated as the reasoning gate (R-03) does, on what is known; it
        is an exclusion, not something to ask about.

        None when no answer changes the result (yes and no lead to the same
        place), otherwise the impacts and whether the answer is decisive.
        """
        unknown = {f for f in self.kg.gating_facts(module.module_id) | set(module.required_facts or [])
                   if f != fact and f not in facts}
        base = {k: v for k, v in facts.items() if k != fact}

        def gate(view):
            if evaluate(module.do_not_use_when, view):
                return False
            return tri(module.use_when, view, unknown)

        try:
            current = gate(base)
            in_gate = fact in _gate_facts(module)
            if in_gate:
                results = {label: gate({**base, fact: value})
                           for label, value in self._answers(fact, cand)}
            else:
                # A required fact the gate does not test: needed to argue the
                # ground, so it matters only while the ground can still apply.
                if current is False:
                    return None
                results = {"answered": current, "unanswered": False}
        except PredicateError:
            return None
        if len(set(results.values())) < 2:
            return None
        if in_gate:
            opens = any(_ORDER[v] > _ORDER[current] for v in results.values())
            rules_out = module.module_id in selected and \
                any(_ORDER[v] < _ORDER[current] for v in results.values())
            if not (opens or rules_out):
                return None
        mid = module.module_id

        def say(v):
            return {True: f"{mid} applies", False: f"{mid} does not apply",
                    None: f"{mid} may apply, subject to other facts"}[v]
        vals = list(results.values())
        return {"yes": say(vals[0]), "no": say(vals[-1]),
                "decisive": True in vals and False in vals}

    def _answers(self, fact: str, cand: dict) -> list[tuple[str, Any]]:
        qtype = cand.get("type") or (self.kg.question_for(fact) or {}).get("type") or "text"
        if qtype == "bool":
            return [("yes", True), ("no", False)]
        options = cand.get("options") or (self.kg.question_for(fact) or {}).get("options") or []
        if qtype == "choice" and len(options) >= 2:
            return [(str(o), o) for o in options]
        # Free text / number: some answer versus none. The gate's comparison on
        # the value itself is left open (unknown), which is what "some answer" is.
        return [("answered", _ANY), ("unanswered", None)]

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _covered_by(case: CaseFile, fact: str) -> Optional[str]:
        for other, value in COVERED_BY.get(fact, ()):
            f = case.facts.get(other)
            if f is not None and f.usable and f.value == value:
                return f"{other}={str(value).lower()}"
        return None

    @staticmethod
    def _hypothesis_covers(case: CaseFile, fact: str) -> bool:
        return any(h["fact_name"] == fact for h in case.fact_hypotheses)

    @staticmethod
    def _texts_already_asked(case: CaseFile) -> list[str]:
        return [_norm(a.get("candidate_question") or "") for a in case.audit
                if a.get("event") == "question_review" and a.get("shown")]

    def _question(self, case, cand, extra) -> dict:
        """The full question object. Everything beyond fact/text/type/options is
        internal (customer_safe.INTERNAL_KEYS)."""
        fact, text = cand["fact"], cand["text"]
        q = {"fact": fact, "text": text, "type": cand.get("type") or "text"}
        if cand.get("options"):
            q["options"] = list(cand["options"])
        q.update({
            "question_id": question_id(case, fact, text),
            "target_fact": fact,
            "related_module": extra["related_module"],
            "issue": extra["issue"],
            "material_reason": extra["material_reason"],
            "impact_if_yes": extra["impact_if_yes"],
            "impact_if_no": extra["impact_if_no"],
            "source": cand.get("source") or MODEL,
            "_rank": extra["_rank"],
        })
        for k in ("hypothesis_id", "reason", "possible_impact", "kb_gated"):
            if k in cand:
                q[k] = cand[k]
        return q

    @staticmethod
    def _row(case, q, decision, reason, round_no, stage="authority") -> dict:
        fact = q.get("fact") or q.get("target_fact") or ""
        text = q.get("text") or ""
        return {"round": round_no, "stage": stage,
                "question_id": q.get("question_id") or question_id(case, fact, text),
                "candidate_question": text, "target_fact": fact,
                "related_module": q.get("related_module"), "source": q.get("source") or MODEL,
                "decision": decision, "reason": reason,
                "material_reason": q.get("material_reason") if decision == APPROVED else None,
                "impact_if_yes": q.get("impact_if_yes"), "impact_if_no": q.get("impact_if_no"),
                "shown": False}


class _Any:
    """An answer whose exact value is not known: satisfies `is`/`exists`."""
    def __bool__(self):
        return True

    def __repr__(self):
        return "<answered>"


_ANY = _Any()


def _gate_facts(module) -> set[str]:
    from ..rules.dsl import referenced_facts
    return referenced_facts(module.use_when) | referenced_facts(module.do_not_use_when)


def trace(case: CaseFile) -> list[dict]:
    """Every question decision on the case, oldest first. Admin only."""
    return [{k: v for k, v in a.items() if k not in ("event", "_persisted")}
            for a in case.audit if a.get("event") == "question_review"]
