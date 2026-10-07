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
  R5      the answer could change a module's eligibility. Eligibility is read
          from Phase 3 (`module_eligibility`) on the authoritative fact view,
          never re-derived here, and only a module that is UNRESOLVED can be
          asked about:
            SUPPORTED   nothing left to ask
            REJECTED    no answer can bring it back
            BLOCKED     no answer removes a blocker that holds
            UNRESOLVED  the missing fact is named (a use_when condition that is
                        unknown, or a hard do_not_use_when condition that is
                        unknown) and the question is material only if an answer
                        could move the module to SUPPORTED, REJECTED or BLOCKED
          A supporting or evidence-only module is asked about only while a
          substantive ground it shares a fact with already stands; otherwise
          the answer cannot change the appeal.

Unknown is not false. A question that was asked and not answered (skipped, "not
sure", left empty) leaves its fact unknown; the question is settled, is not
asked again, and no module is rejected for it.

Approved questions are ranked - integrity first, then a fact that can unlock a
substantive ground, then one that can settle a hard blocker, then a supporting
question - and, within a tier, by outcome impact, the strongest module and least
customer effort. ONE is shown. The rest wait for the next round, when the answer
just given may have made them pointless.

The full question object (question_id, target_fact, related_module,
material_reason, impact_if_yes, impact_if_no and the contract below) stays
internal. The customer sees the text and how to answer
(customer_safe.customer_question). Every decision, approved or rejected, is an
admin-only trace row (audit event `question_review`, `/trace`,
`/cases/{id}/facts`).

Internal contract of an approved question:
  question_id, fact_key, question, source_module_ids, current_status
  ("UNRESOLVED"), materiality_reason, possible_effect (the statuses an answer
  could produce), and the priority tier.
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
from ..module_roles import SUBSTANTIVE_GROUND, role_of
from ..rules.dsl import PredicateError, evaluate3
from .module_eligibility import (BLOCKED as ME_BLOCKED, REJECTED as ME_REJECTED,
                                 SUPPORTED as ME_SUPPORTED, UNRESOLVED as ME_UNRESOLVED,
                                 decide, evaluate_module_id)

APPROVED, REJECTED = "APPROVED", "REJECTED"

# Where a candidate came from. Only the sources marked as an issue below may
# stand on something other than a KB module (R1).
MODEL, KB_GATE, HYPOTHESIS = "case_analysis", "kb_gate", "hypothesis"
CONFLICT, CONFIRMATION = "document_conflict", "fact_confirmation"
POSTCODE, TRADE_BODY = "site_postcode", "operator_ata"

# Case-integrity issues: the case cannot be decided until the customer resolves
# them, so they outrank every ground and need no KB module.
# An account the customer wrote that cannot yet be represented without guessing
# (semantics/understanding.py). It stands on no knowledge-base module: it is
# asked because the case cannot be understood until it is answered.
UNDERSTANDING = "customer_understanding"
INTEGRITY_SOURCES = frozenset({CONFLICT, CONFIRMATION, UNDERSTANDING})
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
    r"\b(?:who was (?:driving|the driver)|name of the driver|were you (?:the )?driv\w*|"
    r"driver'?s (?:name|identity|address)|identify the driver)\b", re.I)

EFFORT = {"bool": 0, "choice": 1, "int": 2, "date": 2, "text": 3}

# Priority tiers (lowest asked first). One question is shown at a time.
TIER_INTEGRITY, TIER_UNLOCK, TIER_BLOCKER, TIER_SUPPORT = 1, 2, 3, 4
TIER_NAMES = {TIER_INTEGRITY: "CASE_INTEGRITY", TIER_UNLOCK: "UNLOCKS_GROUND",
              TIER_BLOCKER: "SETTLES_BLOCKER", TIER_SUPPORT: "SUPPORTS_GROUND"}

# Question lifecycle (derived from the case, never stored separately).
PENDING, ANSWERED, UNRESOLVED_STATE, SUPERSEDED = "PENDING", "ANSWERED", "UNRESOLVED", "SUPERSEDED"


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


# --------------------------------------------------- eligibility, read not re-derived
# The statuses come from module_eligibility (Phase 3). What this module adds is
# a question about them: which unknown condition is the one an answer would
# settle, and could the answer move the module anywhere.
_LEAF_FREE = ("all", "any", "not", "always")
MAX_OPEN = 10           # open conditions enumerated exhaustively; beyond, answered as-is


def _leaf_list(pred: Any) -> list[dict]:
    """Every condition (not a connective) of a predicate, depth first."""
    return [leaf for leaf, _ in _leaves_with_polarity(pred)]


def _leaves_with_polarity(pred: Any) -> list[tuple[dict, bool]]:
    """Each condition with whether it is a requirement (True) or an exclusion
    (False: under a `not`, or a `missing` test). Same order as `_replace`."""
    out: list[tuple[dict, bool]] = []

    def walk(p: Any, positive: bool) -> None:
        if not isinstance(p, dict) or len(p) != 1:
            return
        op, arg = next(iter(p.items()))
        if op in ("all", "any"):
            for q in arg:
                walk(q, positive)
        elif op == "not":
            walk(arg, not positive)
        elif op != "always":
            out.append((p, positive and op != "missing"))
    walk(pred, True)
    return out


def _replace(pred: Any, fixed: dict[int, bool]) -> Any:
    """`pred` with the conditions at the given positions replaced by a known truth."""
    counter = [0]

    def walk(p: Any) -> Any:
        if not isinstance(p, dict) or len(p) != 1:
            return p
        op, arg = next(iter(p.items()))
        if op in ("all", "any"):
            return {op: [walk(q) for q in arg]}
        if op == "not":
            return {op: walk(arg)}
        if op == "always":
            return p
        i = counter[0]
        counter[0] += 1
        return {"always": fixed[i]} if i in fixed else p
    return walk(pred)


def _fact_of(leaf: dict) -> Optional[str]:
    op, arg = next(iter(leaf.items()))
    if op == "has_evidence":
        return None                      # evidence is uploaded, not answered
    return arg if isinstance(arg, str) else arg[0]


def _is_open(leaf: dict, view: dict, unreliable) -> bool:
    return evaluate3(leaf, view, unreliable=unreliable) is None


@dataclass
class Effect:
    """What answering `fact` could do to one UNRESOLVED module."""
    module_id: str
    statuses: set = field(default_factory=set)       # every status an answer could lead to
    sensitive: bool = False                          # some completion where answers differ
    can_support: bool = False                        # SUPPORTED is reachable at all
    decisive: bool = False                           # one answer, nothing else missing, SUPPORTED
    in_use_when: bool = False
    in_blocker: bool = False
    yes: Optional[str] = None
    no: Optional[str] = None

    @property
    def material(self) -> bool:
        return self.sensitive and self.can_support


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
        self._ctx = None                      # eligibility is read afresh every round
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
        if source == UNDERSTANDING and fact in case.asked_questions \
                and fact in (case.raw_answers or {}):
            # Asked, and settled without a usable answer (empty): not asked again.
            return REJECTED, "question already asked and settled without an answer", None
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
                "source_module_ids": [], "possible_effect": [], "tier": TIER_INTEGRITY,
                "_rank": (TIER_INTEGRITY, 0, 0, EFFORT.get(cand.get("type"), 3))}
        if source in UNLOCK_SOURCES:
            # The source has computed, deterministically, which modules the answer
            # would open (it evaluates them as if the answer were given). Phase 3
            # is asked only whether any of them is already decided regardless of
            # that answer: a SUPPORTED module needs nothing, a BLOCKED one cannot
            # be unlocked. A REJECTED or UNRESOLVED one may be exactly what the
            # missing fact (the site, the trade body) is holding shut, so it stays.
            listed = [m for m in (cand.get("unlocks") or [])
                      if m in self.kg.modules and self.kg.modules[m].status == "ACTIVE"]
            if not listed:
                return REJECTED, "R1: no in-force module would be unlocked", None
            ctx = self._round(case)
            mods = [m for m in listed if ctx.outcome(m).status not in (ME_SUPPORTED, ME_BLOCKED)]
            if not mods:
                states = ", ".join(f"{m} {ctx.outcome(m).status}" for m in listed)
                return REJECTED, f"R5: every module this would unlock is already decided ({states})", None
            best = max(mods, key=lambda m: self.kg.modules[m].strength)
            return APPROVED, f"R5: answering would unlock {', '.join(mods)}", {
                "related_module": best, "issue": "MODULE",
                "material_reason": cand.get("material_reason")
                or f"needed to decide whether {best} applies",
                "impact_if_yes": f"{best} can be considered",
                "impact_if_no": f"{best} stays unavailable",
                "source_module_ids": sorted(mods), "possible_effect": [ME_SUPPORTED],
                "tier": TIER_UNLOCK,
                "_rank": (TIER_UNLOCK, -2, -self.kg.modules[best].strength,
                          EFFORT.get(cand.get("type"), 3))}

        return self._module_materiality(case, cand, facts)

    def _module_materiality(self, case, cand, facts):
        """R1 + R5 against Phase 3 eligibility.

        For every in-force module that depends on the fact: what is it now? Only
        an UNRESOLVED module can be asked about, and only if the fact is one of
        the conditions still unknown and an answer could move it somewhere."""
        fact = cand["fact"]
        named = cand.get("related_module")
        related = [m for m in self.kg.active_modules()
                   if fact in self.kg.gating_facts(m.module_id)
                   or fact in (m.required_facts or [])]
        if named and named not in {m.module_id for m in related}:
            return REJECTED, f"R1: {named} does not depend on this fact", None
        if not related:
            return REJECTED, "R1: no in-force module depends on this fact", None
        cls = allegation_class(facts.get("alleged_breach"))
        excluded = ROUTES_IRRELEVANT_TO.get(cls or "", frozenset())
        fitting = [m for m in related if str(getattr(m.route, "value", m.route)) not in excluded]
        if not fitting:
            return REJECTED, (f"R1: the allegation ({cls.lower()}) cannot be answered by "
                              f"{', '.join(sorted({str(getattr(m.route, 'value', m.route)) for m in related}))}"
                              ), None

        ctx = self._round(case)
        decided: dict[str, str] = {}          # module -> why it is not asked about
        found: list[tuple[Any, Effect, int]] = []
        for m in fitting:
            out = ctx.outcome(m.module_id)
            if out.status != ME_UNRESOLVED:
                decided[m.module_id] = out.status
                continue
            effect = self._effect(m, fact, cand, ctx)
            if effect is None or not effect.material:
                continue
            tier = self._tier(m, effect, ctx)
            if tier is None:
                decided[m.module_id] = "SUPPORT_ONLY"
                continue
            found.append((m, effect, tier))

        if not found:
            if decided and len(decided) == len(fitting):
                parts = ", ".join(f"{k} {v}" for k, v in sorted(decided.items()))
                if all(v in (ME_SUPPORTED, ME_REJECTED, ME_BLOCKED) for v in decided.values()):
                    return REJECTED, f"R5: every module that depends on this fact is already decided ({parts})", None
                return REJECTED, f"R5: no module that depends on this fact can use the answer ({parts})", None
            ruled = sorted({str(getattr(m.route, "value", m.route)) for m in related
                            if m not in fitting})
            if ruled:
                return REJECTED, (f"R1: the allegation ({cls.lower()}) cannot be answered by "
                                  f"{', '.join(ruled)}; R5: no other module's result changes"), None
            return REJECTED, "R5: every possible answer leads to the same result", None

        def rank(item):
            m, effect, tier = item
            return (tier, -(2 if effect.decisive else 1), -m.strength,
                    EFFORT.get(cand.get("type"), 3))
        found.sort(key=rank)
        best, effect, tier = found[0]
        effects = sorted({x for _, e, _ in found for x in e.statuses
                          if x in (ME_SUPPORTED, ME_REJECTED, ME_BLOCKED)})
        return APPROVED, f"R5: the answer changes whether {best.module_id} applies", {
            "related_module": best.module_id, "issue": "MODULE",
            "material_reason": cand.get("material_reason")
            or f"needed to decide whether {best.module_id} ({best.topic}) applies",
            "impact_if_yes": self._say(best.module_id, effect.yes),
            "impact_if_no": self._say(best.module_id, effect.no),
            "source_module_ids": [m.module_id for m, _, _ in found],
            "possible_effect": effects, "tier": tier,
            "_rank": rank(found[0])}

    @staticmethod
    def _say(mid: str, status: Optional[str]) -> str:
        return {ME_SUPPORTED: f"{mid} applies", ME_REJECTED: f"{mid} does not apply",
                ME_BLOCKED: f"{mid} does not apply",
                ME_UNRESOLVED: f"{mid} may apply, subject to other facts"}.get(
                    status, f"{mid} may apply, subject to other facts")

    # ------------------------------------------------------- the effect of an answer
    def _effect(self, module, fact, cand, ctx) -> Optional[Effect]:
        """What each possible answer to `fact` does to this UNRESOLVED module.

        The conditions still unknown are the case's open questions; this finds
        the ones on `fact`. The question is material when

          * the answers lead to different statuses on what is known now (an
            answer to a condition that another unknown fact already decides
            changes nothing yet), and
          * the module can still be SUPPORTED through facts a customer can
            answer (a condition on uploaded evidence is not answerable here, so a
            module that waits on one cannot be unlocked by an answer), and
          * an exclusion (a hard blocker, a `not ...` condition) is asked about
            only once the module's requirements stand, or the module was not
            otherwise open: asking whether something rules a ground out, before
            there is a ground, spends the customer's patience on nothing.
        """
        view, unreliable = ctx.view, ctx.unreliable
        use = _leaves_with_polarity(module.use_when)
        dnu = _leaves_with_polarity(module.do_not_use_when)
        try:
            open_use = [i for i, (lf, _) in enumerate(use) if _is_open(lf, view, unreliable)]
            open_dnu = [i for i, (lf, _) in enumerate(dnu) if _is_open(lf, view, unreliable)]
        except PredicateError:
            return None
        mine_use = [i for i in open_use if _fact_of(use[i][0]) == fact]
        mine_dnu = [i for i in open_dnu if _fact_of(dnu[i][0]) == fact]
        if not mine_use and not mine_dnu:
            return None                                  # not the missing fact
        eff = Effect(module.module_id, in_use_when=bool(mine_use), in_blocker=bool(mine_dnu))

        positive = [i for i in mine_use if use[i][1]]
        if not positive:
            # Only ever an exclusion here. Ask once the requirements are met.
            waiting = [i for i in open_use if use[i][1] and _fact_of(use[i][0]) not in (None, fact)]
            if waiting:
                return None
        # One condition a truth value per possible answer.
        answers = self._leaf_answers(fact, cand, view, unreliable,
                                     [lf for lf, _ in use], [lf for lf, _ in dnu], mine_use, mine_dnu)
        if not answers:
            return None

        def status_of(answer, completion) -> str:
            fu, fd = dict(answer[0]), dict(answer[1])
            for (kind, i), v in completion.items():
                (fu if kind == "u" else fd)[i] = v
            gate = evaluate3(_replace(module.use_when, fu), view, unreliable=unreliable)
            dnuw = evaluate3(_replace(module.do_not_use_when, fd), view, unreliable=unreliable)
            return decide(gate, dnuw)

        now = [status_of(a, {}) for a in answers]            # the case as it stands
        eff.statuses = set(now)
        # P8: a number or free-text answer to an `exists` / `is` condition has
        # one possible truth value, so "the answers differ" can never hold.
        # Material there means the single answer moves the module off
        # UNRESOLVED (KB-GRACE-01 waits on exit_delay_min: any answer settles it).
        eff.sensitive = len(set(now)) > 1 or (
            len(answers) == 1 and now[0] != ME_UNRESOLVED)
        eff.decisive = ME_SUPPORTED in now
        eff.yes, eff.no = now[0], now[-1]
        # Could the module still be SUPPORTED through answerable facts?
        others = ([("u", i) for i in open_use if i not in mine_use and _fact_of(use[i][0])]
                  + [("d", i) for i in open_dnu if i not in mine_dnu and _fact_of(dnu[i][0])])
        if len(others) > MAX_OPEN:
            eff.can_support = all(v != ME_REJECTED and v != ME_BLOCKED for v in now) or \
                ME_SUPPORTED in now
        else:
            eff.can_support = any(
                status_of(a, dict(zip(others, bits))) == ME_SUPPORTED
                for bits in _bits(len(others)) for a in answers)
        return eff

    def _leaf_answers(self, fact, cand, view, unreliable, use_leaves, dnu_leaves,
                      mine_use, mine_dnu) -> list[tuple[dict, dict]]:
        """One (use_when, do_not_use_when) truth assignment for the conditions on
        `fact` per possible answer. A yes/no or a choice is evaluated on the value
        itself; free text or a number only says "some answer", so each condition
        is taken both ways (an `is`/`exists` condition holds, `missing` does not)."""
        qtype = cand.get("type") or (self.kg.question_for(fact) or {}).get("type") or "text"
        held = frozenset(u for u in unreliable if u != fact)
        values: list[Any] = []
        if qtype == "bool":
            values = [True, False]
        elif qtype == "choice":
            options = cand.get("options") or (self.kg.question_for(fact) or {}).get("options") or []
            values = [str(o) for o in options] if len(options) >= 2 else []
        if values:
            out = []
            for v in values:
                answered = {**view, fact: v}
                fu = {i: evaluate3(use_leaves[i], answered, unreliable=held) for i in mine_use}
                fd = {i: evaluate3(dnu_leaves[i], answered, unreliable=held) for i in mine_dnu}
                if any(t is None for t in [*fu.values(), *fd.values()]):
                    continue
                out.append((fu, fd))
            return out
        slots = [("u", i, use_leaves[i]) for i in mine_use] + [("d", i, dnu_leaves[i]) for i in mine_dnu]
        choices = []
        for _, _, leaf in slots:
            op = next(iter(leaf))
            choices.append((True,) if op in ("is", "exists") else (False,) if op == "missing"
                           else (True, False))
        out = []
        for combo in _product(choices):
            fu = {i: v for (k, i, _), v in zip(slots, combo) if k == "u"}
            fd = {i: v for (k, i, _), v in zip(slots, combo) if k == "d"}
            out.append((fu, fd))
        return out

    def _tier(self, module, effect: Effect, ctx) -> Optional[int]:
        """Where this module's question sits, or None when it may not be asked.

        A substantive ground is asked about directly. A supporting or evidence
        module is asked about only once a substantive ground it shares a fact
        with already stands: otherwise the answer cannot change the appeal."""
        if role_of(module) == SUBSTANTIVE_GROUND:
            return TIER_UNLOCK if effect.in_use_when else TIER_BLOCKER
        mine = self._shared_facts(module)
        for other in self.kg.active_modules():
            if other.module_id == module.module_id or role_of(other) != SUBSTANTIVE_GROUND:
                continue
            if ctx.outcome(other.module_id).status != ME_SUPPORTED:
                continue
            if mine & (self._shared_facts(other) | set(other.required_facts or [])):
                return TIER_SUPPORT
        return None

    def _shared_facts(self, module) -> set[str]:
        from ..rules.dsl import referenced_facts
        gate = referenced_facts(module.use_when) | referenced_facts(module.do_not_use_when)
        return {f for f in gate if f not in DOCUMENT_OWNED and f not in EVIDENCE_OWNED}

    # ------------------------------------------------------------ the round
    def _round(self, case: CaseFile) -> "_Round":
        """Eligibility of every module on the case as it stands this round."""
        if getattr(self, "_ctx", None) is None:
            self._ctx = _Round(self.kg, case)
        return self._ctx

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
            # The internal contract (never shown: customer_safe cuts a question
            # to fact/text/type/options).
            "fact_key": fact,
            "question": text,
            "source_module_ids": list(extra.get("source_module_ids") or []),
            "current_status": ME_UNRESOLVED,
            "materiality_reason": extra["material_reason"],
            "possible_effect": list(extra.get("possible_effect") or []),
            "tier": extra.get("tier"),
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
                **({k: q.get(k) for k in ("fact_key", "question", "source_module_ids",
                                          "current_status", "materiality_reason",
                                          "possible_effect", "tier")}
                   if decision == APPROVED else {}),
                "shown": False}


class _Round:
    """The case's module eligibility for one review: the authoritative fact view
    (with the verified legal findings the gates read) and each module's Phase 3
    outcome, computed once and only when asked for."""

    def __init__(self, kg: KnowledgeGraph, case: CaseFile):
        from ..legal import findings as legal_findings
        from .module_eligibility import effective_view
        facts = legal_findings.gate_facts(case.fact_view(), list(case.get("pofa_findings") or []))
        states = dict(facts.get("pofa_finding_states") or {})
        for r in getattr(case, "legal_findings", None) or []:
            if r.get("finding_type") and r.get("status"):
                states.setdefault(str(r["finding_type"]), str(r["status"]))
        if states:
            facts["pofa_finding_states"] = states
        self.kg = kg
        # Held but not trusted (uncertain, conflicted): not in the view, and never
        # a value a condition can be settled on.
        self.unreliable = frozenset(k for k, f in (getattr(case, "facts", None) or {}).items()
                                    if not f.usable)
        self.view = effective_view(facts)
        self._out: dict = {}

    def outcome(self, module_id: str):
        if module_id not in self._out:
            self._out[module_id] = evaluate_module_id(
                self.kg, module_id, self.view, unreliable=self.unreliable)
        return self._out[module_id]


def _bits(n: int):
    import itertools
    return itertools.product((False, True), repeat=n)


def _product(choices):
    import itertools
    return itertools.product(*choices)


def trace(case: CaseFile) -> list[dict]:
    """Every question decision on the case, oldest first. Admin only."""
    return [{k: v for k, v in a.items() if k not in ("event", "_persisted")}
            for a in case.audit if a.get("event") == "question_review"]


# ------------------------------------------------------------------ lifecycle
def lifecycle(case: CaseFile) -> list[dict]:
    """Where each question the customer has been shown now stands. Derived from
    the case (what was shown, what the customer answered, what FactManager holds),
    never stored, so it cannot disagree with it.

      PENDING     shown, and still waiting for an answer
      ANSWERED    the fact is held (FactManager) from the customer's answer
      UNRESOLVED  shown and settled without a value: skipped, "not sure" or empty.
                  The fact stays unknown, the question is not asked again, and
                  no module is rejected for it
      SUPERSEDED  shown, then no longer needed: the fact became known another way,
                  or the modules it served were decided without it
    """
    from ..models import SourceKind
    shown: dict[str, dict] = {}
    last: dict[str, dict] = {}
    for a in case.audit:
        if a.get("event") != "question_review":
            continue
        fact = a.get("target_fact") or ""
        last[fact] = a
        if a.get("shown") and fact not in shown:
            shown[fact] = a
    for fact in case.asked_questions or []:
        shown.setdefault(fact, {"question_id": None, "candidate_question": "", "target_fact": fact})
    pending = {q.get("fact") for q in (case.pending_questions or [])}
    out = []
    for fact, row in shown.items():
        node = case.facts.get(fact) if getattr(case, "facts", None) else None
        held = bool(node is not None and node.usable and node.value not in (None, "", []))
        if held and node.source.kind in (SourceKind.ANSWER, SourceKind.CUSTOMER_FREE_TEXT):
            state = ANSWERED
        elif held:
            state = SUPERSEDED
        elif fact in (case.raw_answers or {}):
            state = UNRESOLVED_STATE
        elif fact in pending:
            state = PENDING
        elif last.get(fact, {}).get("decision") == REJECTED and str(
                last[fact].get("reason", "")).startswith("R5"):
            state = SUPERSEDED
        else:
            state = UNRESOLVED_STATE
        out.append({"question_id": row.get("question_id"), "target_fact": fact,
                    "question": row.get("candidate_question"), "state": state})
    return out
