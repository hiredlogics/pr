"""AI case analysis - the V2 replacement for the decision tree.

V1 routed a customer through a fixed chain:

    regex(their words) -> ground -> that ground's gating facts -> preset question

Every step was a lookup, so "kids were in the car" could reach an authorisation
question with no permission anywhere in evidence, and a photographic notice could
reach an ANPR double-visit question with no entry/exit pair in the document.

V2 has one substantive authority: a model reading the notice's own facts and the
customer's words, choosing from the approved knowledge base, and asking only for
a fact that would change the outcome. There is no route list to classify into.

What is deliberately NOT delegated
----------------------------------
The model PROPOSES. Deterministic code still vetoes, because the guarantees the
product rests on cannot be a matter of judgement:

  * a ground the KB forbids for these facts      -> `do_not_use_when`
  * a statutory defect without the calculation   -> legal/pofa.py
  * a Code value without a verified Code version -> legal/code_versions.py
  * a module id that does not exist              -> the KB is closed
  * a question that touches driver identity      -> banned terms
  * a question that re-asks a settled topic      -> topic clusters + ask history
  * anything in the letter itself                -> Engine 4

So a wrong proposal costs a suppressed ground, never a wrong letter.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .narrative import NARRATIVE_FACTS
from .. import prompts
from ..kg.graph import KnowledgeGraph
from ..models import CaseFile, KBModule
from ..rules.dsl import evaluate
from ..routes import GENERAL_GROUND_ROUTES, Route
from .claim_plan import build_claim_plan
from .knowledge_matcher import OFFERABLE, RELEVANT, SUPPORTED, KnowledgeMatcher

# A question is a cost to the customer, so the ceiling is low and silence is the
# default. `questions.yaml` still supplies these caps and the banned terms; it no
# longer supplies the questions.
DEFAULT_MAX_QUESTIONS = 4
DEFAULT_MAX_ROUNDS = 3
CANDIDATE_LIMIT = 24

QUESTION_TYPES = {"bool", "int", "choice", "text"}

# Facts that only make sense when the notice itself shows an ANPR entry/exit pair.
# Facts the engines derive themselves. Asking the customer for one puts an
# internal judgment ("does your account contradict the allegation?") on screen
# and lets an answer overwrite a calculation.
INTERNAL_FACTS = {
    "account_contradicts_allegation", "material_account_proposition",
    "material_account_propositions", "restricted_bay_alleged", "observation_window_min",
    "total_recorded_duration_min", "duration_min", "jurisdiction", "code_version",
    "notice_route", "notice_sides_complete", "pcn_conflict", "pcn_candidates",
    "authority_challenge_proportionate", "independent_evidence_contradicts",
    "driver_status", "customer_described_event",
} | NARRATIVE_FACTS
INTERNAL_PREFIXES = ("pofa_", "ntk_", "_")


def is_internal_fact(fact: str) -> bool:
    return fact in INTERNAL_FACTS or fact.startswith(INTERNAL_PREFIXES)


ANPR_SHAPED_FACTS = {
    "anpr_sequence_incomplete", "anpr_discrepancy", "anpr_duration_disputed",
    "multiple_visits",
}

# Strength at or above this may lead a letter (mirrors reasoning.SUPPORTING_THRESHOLD).
LEADING_STRENGTH = 50


# Semantic topic clusters. Exact fact-name dedupe alone lets the model re-ask
# "did you use the kiosk?" as "can you remember validating?" under a new name.
# Any prior ask or answered fact whose name/text hits a cluster blocks further
# questions in that cluster.
TOPIC_CLUSTERS: dict[str, tuple[str, ...]] = {
    "kiosk_validation": (
        "kiosk", "validat", "voucher", "ticket_machine", "pay_station",
        "validation_machine", "pay_and_display", "display_ticket",
    ),
    "payment_attempt": (
        "payment_made", "payment_attempt", "payment_method", "payment_fail",
        "machine_fault", "app_payment",
    ),
    "further_evidence": (
        "further_evidence", "additional_evidence", "more_evidence",
        "store_confirmation", "shop_confirmation", "receipt_available",
        "other_evidence", "store_contact", "contact_store", "contacted_store",
        "speak_to_store", "spoke_to_manager", "ask_the_store", "store_manager",
        "customer_service", "ask_sainsbury",
    ),
    "signage": ("signage", "sign_visible", "signs_"),
    "permit": ("permit_held", "permit_display", "visitor_authoris"),
    # Administrative: model often invents alternate snake_case names for these.
    "operator_ata": (
        "operator_ata", "trade_association", "trade_body", "accredited_trade",
        "bpa_or_ipc", "british_parking", "international_parking",
    ),
    "site_postcode": (
        "site_postcode", "car_park_postcode", "location_postcode",
        "parking_postcode", "site_post_code",
    ),
}

# Question-text markers when the model invents a fact name outside the bank.
ATA_TEXT_MARKERS = (
    "trade association", "trade body", "bpa or ipc", "bpa / ipc",
    "accredited trade", "which association",
)
POSTCODE_TEXT_MARKERS = (
    "postcode", "post code", "postal code",
)

# Customer answers that settle a topic as unresolved rather than inviting another
# wording of the same question.
UNRESOLVED_ANSWER_MARKERS = (
    "don't remember", "do not remember", "cant remember", "can't remember",
    "cannot remember", "not sure", "unsure", "unknown", "no idea",
    "don't know", "do not know", "n/a", "na", "cannot say", "can't say",
)

# Rationale the customer must never see. The client's example was
# "Permission to park defeats the alleged breach outright, so whether it existed
# is the primary fact." A model told not to explain itself will occasionally
# explain itself anyway, so the text is checked rather than trusted.
RATIONALE_MARKERS = (
    "defeats", "alleged breach", "primary fact", "this ground", "the ground",
    "we are looking at", "we're looking at", "because this", "in order to establish",
    "legally", "statutory", "schedule 4", "pofa", "code of practice",
)


@dataclass
class CaseAnalysis:
    """Grounds and questions the pipeline may act on, after vetoes."""
    module_ids: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    # Finalized claim plan + material-fact accounting (engines.claim_plan).
    claim_plan: dict = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    # The case_analysis call failed (after its retry): nothing above is a
    # judgment on the case.
    analysis_failed: bool = False
    # Questions the pre-checks below dropped, for the Question Authority's
    # admin trace (engines/question_authority.py): every generated question
    # is accounted for, not only the ones that reached the authority.
    question_rejections: list[dict] = field(default_factory=list)
    # P4: the knowledge match this analysis was offered from (engines/
    # knowledge_matcher.py) - why each module was offered, blocked or not.
    knowledge: Any = None

    @property
    def needs_answers(self) -> bool:
        return bool(self.questions)


class AnalysisEngine:
    def __init__(self, kg: KnowledgeGraph, llm, retriever=None,
                 max_questions: int = DEFAULT_MAX_QUESTIONS):
        self.kg = kg
        self.llm = llm
        self.retriever = retriever
        cfg = getattr(kg, "question_cfg", {}) or {}
        self.banned = [t.lower() for t in cfg.get("banned_question_terms", [])]
        self.max_questions = int(cfg.get("max_questions_v2", max_questions))
        self.max_rounds = int(cfg.get("max_question_rounds", DEFAULT_MAX_ROUNDS))

    def _call(self, case, circumstances, facts, candidates, pofa, code_version,
              result: CaseAnalysis) -> Optional[dict]:
        """The case_analysis call, retried once. Records `case_analysis_completed`
        or `case_analysis_error` so the outcome can tell "analysis found nothing"
        from "analysis never ran"."""
        payload = self._payload(case, circumstances, facts, candidates, pofa, code_version,
                                match=result.knowledge)
        last: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                raw = self.llm.complete_json(
                    task="case_analysis", system=prompts.system("case_analysis"), user=payload)
                if not isinstance(raw, dict):
                    raise ValueError(f"case analysis returned {type(raw).__name__}, not an object")
                case.audit.append({"event": "case_analysis_completed", "attempt": attempt})
                return raw
            except Exception as exc:
                last = exc
        case.audit.append({"event": "case_analysis_error", "error": str(last)})
        result.analysis_failed = True
        result.trace.append(f"case analysis unavailable ({type(last).__name__}); nothing proposed")
        return None

    # ------------------------------------------------------------------ main
    def analyse(self, case: CaseFile, circumstances: str = "",
                pofa: Any = None, code_version: Optional[str] = None) -> CaseAnalysis:
        facts = case.fact_view()
        result = CaseAnalysis()
        # P17.9: KnowledgeModuleResolver — candidate discovery ≠ eligibility.
        # Vector/LLM may rank candidates; matcher + eligibility remain authoritative.
        from .module_resolver import KnowledgeModuleResolver
        from ..manifest import kb_digest
        resolved = KnowledgeModuleResolver(
            self.kg, reasoning=None, retriever=self.retriever,
        ).resolve(
            case, fact_view=facts, circumstances=circumstances,
            analysis_engine=self,
        )
        result.knowledge = resolved.match or KnowledgeMatcher(self.kg).match(case, facts)
        case.audit.append({"event": "knowledge_match", "kb_digest": kb_digest(self.kg),
                           **result.knowledge.trace()})
        by_id = {m.module_id: m for m in self.kg.active_modules()}
        candidates = [by_id[mid] for mid in resolved.candidates if mid in by_id]
        result.candidate_ids = [m.module_id for m in candidates]
        result.trace.append(
            f"candidates={len(candidates)} (module_resolver; candidate≠eligibility)")

        rounds = sum(1 for a in case.audit if a.get("event") == "analysis_round")
        if rounds >= self.max_rounds:
            result.trace.append(
                f"question round limit reached ({rounds}>={self.max_rounds}); asking nothing")
            # Still propose grounds so drafting can proceed with what is known.
            raw = self._call(case, circumstances, facts, candidates, pofa, code_version, result)
            if raw is not None:
                result.module_ids = self._finalize_claims(
                    case, raw.get("grounds") or [], facts, pofa, code_version, result)
            return result

        raw = self._call(case, circumstances, facts, candidates, pofa, code_version, result)
        if raw is None:
            # Nothing is proposed, and the case records that analysis did not
            # run: an empty selection here is a processing failure, never a
            # finding that the case has no supported ground (engines/outcome.py).
            return result

        proposed = raw.get("grounds") or []
        result.module_ids = self._finalize_claims(
            case, proposed, facts, pofa, code_version, result)

        # One bounded reassessment when gate-satisfied candidates were omitted.
        omitted = list((result.claim_plan or {}).get("omitted_gate_satisfied") or [])
        if omitted:
            try:
                hint = json.loads(self._payload(
                    case, circumstances, facts, candidates, pofa, code_version,
                    match=result.knowledge))
                hint["reassessment"] = {
                    "omitted_gate_satisfied": omitted,
                    "instruction": (
                        "Reassess only these candidates whose use_when is already "
                        "satisfied on the case facts. Include a candidate only if "
                        "the complete facts and account actually support it. "
                        "Do not select always-on landowner authority as filler."
                    ),
                }
                raw2 = self.llm.complete_json(
                    task="case_analysis", system=prompts.system("case_analysis"),
                    user=json.dumps(hint, default=str))
                proposed = raw2.get("grounds") or proposed
                result.suppressed = []
                result.module_ids = self._finalize_claims(
                    case, proposed, facts, pofa, code_version, result)
                result.trace.append(
                    f"bounded reassessment for omitted candidates {omitted} -> "
                    f"{result.module_ids}")
                raw = raw2
            except Exception as exc:
                case.audit.append({
                    "event": "case_analysis_reassessment_error", "error": str(exc),
                    "omitted": omitted,
                })
                result.trace.append(
                    f"reassessment unavailable ({type(exc).__name__}); "
                    f"keeping first finalized plan")

        asking = list(raw.get("questions") or []) + self._unlocking_questions(case, result, facts)
        # Candidates only: the Question Authority (orchestrator._reanalyse)
        # decides which, if any, the customer sees. Zero is a valid outcome.
        result.questions = self._safe_questions(case, asking, result)

        from .claim_plan_authority import latest_locked
        previous = latest_locked(case)
        prev_ids = set(previous.supported_ids) if previous is not None else set()
        kept = list(result.module_ids or [])
        # P8.2: Case Intelligence is a proposer. These three lists are the
        # contract; there is no final_ground_list.
        case.audit.append({
            "event": "case_analysis",
            "proposed": [g.get("module_id") for g in proposed],
            "kept": kept,
            "suppressed": result.suppressed,
            "claim_plan": result.claim_plan,
            # P5: what was offered, so the final Claim Plan accounts for every
            # candidate after a reload (claim_plan_authority).
            "candidates": list(result.candidate_ids or []),
            "asked": [q["fact"] for q in result.questions],
            "why_asked": {q.get("fact"): q.get("material_because")
                          for q in (raw.get("questions") or []) if q.get("fact")},
            "not_supported": raw.get("not_supported") or [],
            "add_ground_candidates": [m for m in kept if m not in prev_ids],
            "support_existing_ground": [m for m in kept if m in prev_ids],
            "proposed_invalidations": [
                {"ground_id": s.get("module_id"),
                 "reason": s.get("why") or s.get("reason") or "suppressed"}
                for s in (result.suppressed or []) if s.get("module_id")
            ],
        })
        return result

    # ------------------------------------------------------- candidate set
    def _candidates(self, case: CaseFile, circumstances: str,
                    facts: dict[str, Any], match=None) -> list[KBModule]:
        """Approved grounds worth showing the model, by semantic relevance.

        Not filtered by route: that filtering was the V1 branch selector. The
        only exclusions are metadata ones - a module the KB has disabled, or one
        whose jurisdiction cannot apply to this case.
        """
        active = [m for m in self.kg.active_modules()]
        jurisdiction = facts.get("jurisdiction")
        filtered = [m for m in active if self._jurisdiction_ok(m, jurisdiction)]
        if match is not None:
            # P4: only modules the relation engine says could still apply. A
            # BLOCKED module (wrong evidence type, an allegation it cannot
            # answer, a blocked condition) or a REJECTED one (its gate cannot
            # hold on what is known) is never shown to the model.
            filtered = [m for m in filtered
                        if match.candidates.get(m.module_id) is not None
                        and match.candidates[m.module_id].status in OFFERABLE]

        if self.retriever is None:
            return self._by_relation(filtered, match)[:CANDIDATE_LIMIT]

        query = " ".join(str(x) for x in (
            case.get("alleged_breach", ""), circumstances,
            case.get("parking_location", ""),
        ) if x)
        allowed = {m.module_id for m in filtered}
        hits = self.retriever.search(query or "parking charge", allowed_ids=allowed, k=CANDIDATE_LIMIT * 2)

        order: list[str] = []
        for d in hits:
            mid = d.meta.get("module_id")
            if mid and mid not in order and mid in allowed:
                order.append(mid)
        by_id = {m.module_id: m for m in filtered}
        ranked = [by_id[mid] for mid in order if mid in by_id]
        ranked += [m for m in filtered if m.module_id not in order]
        # Visibility only: gate-satisfied fact-specific modules must be offered
        # to Case Intelligence even if semantic rank pushed them out of top-N.
        # This does not finalize them as grounds.
        ranked = self._ensure_gate_satisfied_visible(ranked, filtered, facts)
        return self._by_relation(ranked, match)[:CANDIDATE_LIMIT]

    @staticmethod
    def _by_relation(ranked: list[KBModule], match) -> list[KBModule]:
        """Relationship first, similarity second: SUPPORTED, then RELEVANT (the
        case's facts, evidence or allegation point at it), then the existing
        order for the rest - so the cap never cuts a supported module."""
        if match is None:
            return ranked
        tier = {SUPPORTED: 0, RELEVANT: 1}
        pos = {m.module_id: i for i, m in enumerate(ranked)}
        return sorted(ranked, key=lambda m: (
            tier.get(match.candidates[m.module_id].status, 2), pos[m.module_id]))

    @staticmethod
    def _jurisdiction_ok(module: KBModule, jurisdiction: Optional[str]) -> bool:
        """Metadata filter. PoFA grounds cannot apply outside England & Wales, so
        offering them in Scotland invites a proposal that must then be vetoed."""
        if not jurisdiction or jurisdiction == "UNKNOWN":
            return True
        pofa_only = any("PoFA" in str(s) for s in (module.legal_basis or []))
        return not (pofa_only and jurisdiction != "ENGLAND_WALES")

    @staticmethod
    def _is_always_on(module: KBModule) -> bool:
        uw = module.use_when
        return isinstance(uw, dict) and uw.get("always") is True

    def _ensure_gate_satisfied_visible(self, ranked: list[KBModule],
                                       filtered: list[KBModule],
                                       facts: dict[str, Any]) -> list[KBModule]:
        """Pin gate-satisfied non-filler modules into the candidate window.

        Semantic top-N alone can drop a module whose use_when is already true
        (e.g. multiple_visits → KB-ANPR-01). This only affects visibility for
        Case Intelligence — it does not finalize the ground.
        """
        leaders: list[KBModule] = []
        seen: set[str] = set()
        for m in list(filtered) + list(ranked):
            if m.module_id in seen:
                continue
            if self._is_always_on(m) or m.route == Route.LANDOWNER:
                continue
            if evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts):
                leaders.append(m)
                seen.add(m.module_id)
        rest = [m for m in ranked if m.module_id not in seen]
        return leaders + rest
    def _rerank(self, modules: list[KBModule], facts: dict[str, Any]) -> list[KBModule]:
        """Metadata rerank: a ground whose required facts are already present is
        more useful to consider than one that would need everything asked."""
        def score(m: KBModule) -> tuple[float, float, str]:
            required = list(m.required_facts or [])
            have = sum(1 for f in required if facts.get(f) not in (None, "", []))
            coverage = (have / len(required)) if required else 0.5
            gated = 0.0
            try:
                if evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts):
                    gated = -1.0 if not self._is_always_on(m) else 0.0
            except Exception:
                gated = 0.0
            return (gated, -coverage, m.module_id)
        return sorted(modules, key=score)

    # --------------------------------------------------------------- payload
    def _payload(self, case: CaseFile, circumstances: str, facts: dict[str, Any],
                 candidates: list[KBModule], pofa: Any, code_version: Optional[str],
                 match=None) -> str:
        closed_facts = sorted({
            *(self.kg.questions or {}).keys(),
            *(f for m in candidates for f in (m.required_facts or [])),
        })
        prior_texts = [
            str(q.get("text") or "")
            for q in (case.pending_questions or [])
            if q.get("text")
        ]
        return json.dumps({
            "facts": facts,
            "evidence": [{"evidence_id": e.evidence_id, "kind": e.kind, "filename": e.filename,
                          "has_text": bool((e.text or "").strip()),
                          "page_images": len(getattr(e, "images", []) or [])}
                         for e in case.evidence.values()],
            "circumstances": circumstances,
            "already_asked": list(case.asked_questions),
            "already_asked_texts": prior_texts,
            "unresolved_topics": self._unresolved_topics(case),
            "preferred_fact_names": closed_facts,
            "candidates": [{
                "module_id": m.module_id,
                "topic": m.topic,
                "proposition": m.core_proposition,
                "depends_on": sorted(set(list(m.required_facts or []))),
                "prohibited_claims": m.prohibited_claims or [],
                # P4: why the relation engine offered it (status, the conditions
                # that hold, the facts still missing). Never the whole KB.
                **({"relation": match.candidates[m.module_id].for_analysis()}
                   if match is not None and m.module_id in match.candidates else {}),
            } for m in candidates],
            "case_signals": {k: v["value"] for k, v in (match.signals if match else {}).items()},
            "pofa": {"route": getattr(pofa, "route", None),
                     "findings": list(getattr(pofa, "findings", []) or [])},
            "code_version": code_version,
            "driver_status": case.driver_status.value,
            # Automatic recovery already ran: do not ask for recovered / do_not_ask
            # facts, and prefer operator_requestable gaps over customer chase.
            "recovery": case.recovery_report or {},
        }, default=str)

    def _unanswerable_topics(self, case: CaseFile) -> list[str]:
        """Topics the customer has already said they cannot answer.

        Narrower than `_unresolved_topics`, which also counts a topic as closed
        once anything in it has been asked. That wider reading is right for the
        prompt (it stops the model re-asking under a new name) but wrong as a
        hard block, because a KB gate can name two facts in one topic.
        """
        hit: list[str] = []
        for topic, tokens in TOPIC_CLUSTERS.items():
            for name, fact in case.facts.items():
                if not fact.usable:
                    continue
                blob = f"{name} {fact.value}"
                if self._touches(blob, tokens) and self._is_unresolved_value(fact.value):
                    hit.append(topic)
                    break
        return hit

    def _unresolved_topics(self, case: CaseFile) -> list[str]:
        """Topics already asked or answered as 'cannot remember' / unknown."""
        hit: list[str] = []
        for topic, tokens in TOPIC_CLUSTERS.items():
            if any(self._touches(token_blob, tokens)
                   for token_blob in case.asked_questions):
                hit.append(topic)
                continue
            for name, fact in case.facts.items():
                if not fact.usable:
                    continue
                blob = f"{name} {fact.value}"
                if self._touches(blob, tokens) and self._is_unresolved_value(fact.value):
                    hit.append(topic)
                    break
        return hit

    @staticmethod
    def _touches(blob: str, tokens: tuple[str, ...]) -> bool:
        low = re.sub(r"[^a-z0-9_]+", "_", str(blob).lower())
        return any(tok in low for tok in tokens)

    @staticmethod
    def _is_unresolved_value(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return False
        low = str(value).strip().lower()
        if not low:
            return True
        return any(m in low for m in UNRESOLVED_ANSWER_MARKERS)

    # ----------------------------------------------------------- claim plan
    def _finalize_claims(self, case: CaseFile, proposed: list[dict], facts: dict[str, Any],
                         pofa: Any, code_version: Optional[str],
                         result: CaseAnalysis) -> list[str]:
        """Veto proposed grounds into one finalized claim plan.

        Does not auto-seed strength>=50 grounds and does not strip LAND merely
        because another ground exists. Omissions are listed for reassessment.
        """
        # P6.1: what counts is the VERIFIED legal findings the calculation
        # engine recorded, not the raw code list.
        from ..legal import findings as legal_findings
        findings = sorted(legal_findings.verified_types(
            case.legal_findings, getattr(pofa, "findings", []) or []))
        proposed_ids = [(e or {}).get("module_id") for e in (proposed or [])]
        # P4: a module the relation engine BLOCKS for this case is not proposed
        # to the claim plan at all (the plan's own vetoes are unchanged).
        match = result.knowledge
        if match is not None:
            for mid in [m for m in proposed_ids if m and match.is_blocked(m)]:
                proposed_ids.remove(mid)
                self._suppress(result, mid,
                               f"blocked by relation: {match.candidates[mid].reason}")
        plan = build_claim_plan(
            case, self.kg, proposed_ids, list(result.candidate_ids or []), facts,
            findings=findings, code_version=code_version,
            needs_pofa_finding=self._needs_pofa_finding,
            needs_code_version=self._needs_code_version,
        )
        result.claim_plan = plan.as_dict()
        result.trace.extend(plan.trace)
        for claim in plan.claims:
            if claim.get("status") == "excluded":
                self._suppress(result, claim.get("module_id"), claim.get("reason") or "excluded")
        if not plan.module_ids:
            result.trace.append("no proposed ground survived the deterministic checks")
        return list(plan.module_ids)

    @staticmethod
    def _needs_pofa_finding(module: KBModule) -> bool:
        """A ground that alleges a Schedule 4 timing failure. The framing ground
        (keeper liability is not automatic) asserts no defect, so it is exempt."""
        bases = [str(s) for s in (module.legal_basis or [])]
        return any("para" in b and "PoFA" in b for b in bases)

    @staticmethod
    def _needs_code_version(module: KBModule) -> bool:
        return any(str(s).startswith("SCOP-") for s in (module.legal_basis or []))

    def _suppress(self, result: CaseAnalysis, module_id: Optional[str], why: str) -> None:
        result.suppressed.append({"module_id": module_id, "why": why})
        result.trace.append(f"suppressed {module_id}: {why}")

    # ------------------------------------------------------------- questions
    UNLOCKABLE = "required facts for this ground are not yet established"

    def _unlocking_questions(self, case: CaseFile, result: CaseAnalysis,
                             facts: dict[str, Any]) -> list[dict]:
        """Ask for the facts that are stopping a ground the model chose for THIS case.

        A ground suppressed at `use_when` is the one case where the three tests
        for asking are met by construction, not by guesswork: the analysis engine
        read this notice and this account and proposed the ground, so it is
        material; the KB itself names the facts its gate needs, so the question
        is answerable only by the customer; and supplying one flips the ground
        from suppressed to arguable, so the answer changes the letter.

        Not the V1 chain. Nothing here maps a circumstance to a ground or a
        ground to a fixed question: the ground comes from the model, the facts
        come from the KB's own gate, and the wording and answer shape come from
        the fact vocabulary. Remove a module and its questions go with it.

        Every safeguard still applies - these are handed to `_safe_questions`
        exactly like the model's own, so already-asked, already-recovered,
        operator-requestable, ANPR-shaped and Q-01 checks all run.
        """
        blocked = [s.get("module_id") for s in result.suppressed
                   if s.get("why") == self.UNLOCKABLE]
        out: list[dict] = []
        for mid in blocked:
            module = self.kg.modules.get(mid)
            if module is None:
                continue
            for fact in sorted(self.kg.gating_facts(mid) | set(module.required_facts or [])):
                if facts.get(fact) not in (None, "", []):
                    continue
                shape = self.kg.question_for(fact)
                if not shape or not shape.get("text"):
                    # No approved wording and no answer shape for this fact, so
                    # there is no question to ask - the ground stays suppressed
                    # rather than being unlocked by something invented here.
                    result.trace.append(f"no question shape for {fact} (gates {mid})")
                    continue
                out.append({
                    "fact": fact,
                    "text": shape["text"],
                    "type": shape.get("type", "text"),
                    "options": shape.get("options") or [],
                    "material_because": f"gates {mid}, which analysis proposed for this case",
                    "related_module": mid,
                    # Named by the KB's own gate, not invented by the model, so the
                    # "one question per topic" cluster must not swallow it: a gate
                    # like {signage_issue_raised AND signage_issue_type} needs both
                    # halves, and both live in the same topic.
                    "kb_gated": True,
                })
        return out

    def _has_leading_ground(self, module_ids: list[str]) -> bool:
        return any(
            (m := self.kg.modules.get(mid)) and m.strength >= LEADING_STRENGTH
            for mid in (module_ids or [])
        )

    def _safe_questions(self, case: CaseFile, proposed: list[dict],
                        result: CaseAnalysis) -> list[dict]:
        """Keep the model's wording, enforce the rules it was told to follow.

        Only `fact`, `text`, `type` and `options` survive. `material_because` is
        the system's reasoning: it goes to the audit log, never to the customer.
        """
        out: list[dict] = []
        seen = set(case.asked_questions)
        # Two different reasons a topic can be closed, and they are not
        # interchangeable:
        #   dead    - the customer already said they cannot remember / do not
        #             know. Nothing in that topic is worth asking again, however
        #             the question arose.
        #   covered - something in that topic has been asked once. This stops the
        #             model inventing a second name for the same question; it must
        #             NOT stop the KB asking for the *other* fact its gate needs.
        dead_topics = set(self._unanswerable_topics(case))
        covered_topics: set[str] = set()
        for name in case.asked_questions:
            topic = self._topic_for(name, "")
            if topic:
                covered_topics.add(topic)

        evidence_kinds = set(case.fact_view().get("evidence_kinds") or [])
        # A shopping receipt is already uploaded — do not ask the customer to
        # chase the store for the same purchase confirmation.
        if "RECEIPT" in evidence_kinds or "BANK_STATEMENT" in evidence_kinds:
            dead_topics.add("further_evidence")

        # Facts already on the notice / recovered automatically must not be re-asked.
        recovery = case.recovery_report or {}
        do_not_ask = set(recovery.get("do_not_ask") or [])
        operator_gaps = set(recovery.get("operator_requestable") or [])
        known_on_notice = {n for n in (
            "operator_name", "pcn_number", "vrm", "parking_location",
            "parking_event_date", "notice_issue_date", "charge_amount",
            "alleged_breach", "entry_time", "exit_time",
        ) if case.has(n)}
        known_on_notice |= set((recovery.get("recovered") or {}).keys())
        known_on_notice |= do_not_ask

        for entry in proposed:
            fact = str((entry or {}).get("fact") or "").strip()
            text = str((entry or {}).get("text") or "").strip()
            qtype = str((entry or {}).get("type") or "text").strip().lower()

            if not fact or not text:
                continue
            # Canonicalise invented admin fact names before dedupe / materiality.
            admin_kind = self._admin_question_kind(fact, text)
            if admin_kind == "operator_ata":
                fact = "operator_ata"
            elif admin_kind == "site_postcode":
                fact = "site_postcode"
            if is_internal_fact(fact):
                self._drop(result, fact, text, "derived by the engines, not asked")
                continue
            if fact in seen or case.has(fact) or fact in known_on_notice:
                self._drop(result, fact, text, "already known or already asked")
                continue
            if not self._question_material_for_case(case, fact, result, text):
                self._drop(result, fact, text, "not needed for any available ground")
                continue
            if fact in operator_gaps or any(g in fact for g in (
                    "validation_log", "landowner", "signage_plan", "anpr_raw")):
                self._drop(result, fact, text, "operator-requestable; use records request in draft")
                continue
            kb_gated = bool((entry or {}).get("kb_gated"))
            topic = self._topic_for(fact, text)
            if topic and topic in dead_topics:
                self._drop(result, fact, text, f"topic {topic} unresolved for this customer")
                continue
            if topic and topic in covered_topics and not kb_gated:
                self._drop(result, fact, text, f"topic {topic} already asked")
                continue
            # Don't ask the customer to contact the store when operator records
            # can be requested in the draft instead.
            low_text = text.lower()
            if any(p in low_text for p in (
                "contact the store", "ask the store", "speak to the store",
                "ask sainsbury", "contact sainsbury", "ask the supermarket",
            )):
                self._drop(result, fact, text, "store-contact; use operator records request")
                continue
            if fact in ANPR_SHAPED_FACTS and not (
                    case.has("entry_time") and case.has("exit_time")):
                self._drop(result, fact, text, "notice has no ANPR entry/exit pair")
                continue
            if qtype not in QUESTION_TYPES:
                qtype = "text"

            low = text.lower()
            if any(b in low for b in self.banned):
                self._drop(result, fact, text, "touches driver identity")
                continue
            if any(marker in low for marker in RATIONALE_MARKERS):
                self._drop(result, fact, text, "explains its own legal purpose")
                continue

            question: dict[str, Any] = {"fact": fact, "text": text, "type": qtype}
            if qtype == "choice":
                options = [str(o).strip() for o in ((entry or {}).get("options") or []) if str(o).strip()]
                # Prefer the approved bank options for known choice facts so the
                # UI and validator share the same closed set (e.g. BPA/IPC/NOT_SHOWN).
                bank = self.kg.question_for(fact) or {}
                if bank.get("type") == "choice" and bank.get("options"):
                    options = [str(o) for o in bank["options"]]
                if len(options) < 2:
                    self._drop(result, fact, text, "choice with no options")
                    continue
                question["options"] = options

            # Internal, for the Question Authority: where the candidate came
            # from and what it is said to decide. Never shown to the customer.
            from .question_authority import KB_GATE, MODEL, POSTCODE, TRADE_BODY
            if admin_kind == "site_postcode":
                from .recovery import postcode_unlocks
                question.update(source=POSTCODE, unlocks=postcode_unlocks(case, self.kg))
            elif admin_kind == "operator_ata":
                question.update(source=TRADE_BODY, unlocks=self._ata_unlocks(case))
            else:
                question["source"] = KB_GATE if kb_gated else MODEL
            if (entry or {}).get("related_module"):
                question["related_module"] = entry["related_module"]
            if (entry or {}).get("material_because"):
                question["material_reason"] = str(entry["material_because"])
            out.append(question)
            seen.add(fact)
            if topic:
                covered_topics.add(topic)
            if admin_kind:
                covered_topics.add(admin_kind)
            if len(out) >= self.max_questions:
                break

        return out

    @staticmethod
    def _drop(result: CaseAnalysis, fact: str, text: str, why: str) -> None:
        result.trace.append(f"dropped question {fact}: {why}")
        result.question_rejections.append({"fact": fact, "text": text, "reason": why})

    def _topic_for(self, fact: str, text: str) -> Optional[str]:
        blob = f"{fact} {text}"
        for topic, tokens in TOPIC_CLUSTERS.items():
            if self._touches(blob, tokens):
                return topic
        return None

    @classmethod
    def _admin_question_kind(cls, fact: str, text: str) -> Optional[str]:
        """Classify ATA / postcode questions even when the fact name is invented."""
        low_fact = fact.lower().strip()
        low_text = (text or "").lower()
        if low_fact in ("operator_ata", "site_postcode"):
            return low_fact
        if cls._touches(low_fact, TOPIC_CLUSTERS["operator_ata"]) or \
                any(m in low_text for m in ATA_TEXT_MARKERS):
            return "operator_ata"
        if cls._touches(low_fact, TOPIC_CLUSTERS["site_postcode"]) or \
                any(m in low_text for m in POSTCODE_TEXT_MARKERS):
            return "site_postcode"
        return None

    def _question_material_for_case(self, case: CaseFile, fact: str,
                                   result: CaseAnalysis,
                                   text: str = "") -> bool:
        """Drop administrative questions that cannot change the appeal.

        operator_ata / site_postcode are only worth asking when a Code- or
        PoFA-dependent path is live. Fact-specific BAY/REC letters must not
        interrupt the customer for trade-association or postcode trivia.
        """
        kind = self._admin_question_kind(fact, text) or fact
        # If a fact-specific ground is already selected or open, neither admin
        # question changes the letter — drop both regardless of jurisdiction.
        if kind in ("operator_ata", "site_postcode") and self._fact_specific_path_open(case, result):
            return False
        if kind == "operator_ata":
            return self._ata_would_unlock(case)
        if kind == "site_postcode":
            # Material only when it would unlock a leading Schedule 4 ground.
            from .recovery import postcode_unlocks
            return bool(postcode_unlocks(case, self.kg))
        return True

    def _fact_specific_path_open(self, case: CaseFile, result: CaseAnalysis) -> bool:
        """True when BAY/REC/other non-PoFA non-LANDOWNER ground is selected or gated-in.

        Also true when the allegation itself is already bay- or validation-shaped:
        those letters do not need ATA/postcode even before every gating fact lands.
        """
        if case.get("restricted_bay_alleged"):
            return True
        breach = str(case.get("alleged_breach") or "").lower()
        if any(tok in breach for tok in ("validat", "kiosk", "voucher", "ticket")):
            return True
        for mid in result.module_ids:
            mod = self.kg.modules.get(mid)
            if mod and mod.route not in GENERAL_GROUND_ROUTES:
                return True
        facts = case.fact_view()
        for m in self.kg.active_modules():
            if m.route in GENERAL_GROUND_ROUTES:
                continue
            if evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts):
                return True
        return False

    def _ata_would_unlock(self, case: CaseFile) -> bool:
        return bool(self._ata_unlocks(case))

    def _ata_unlocks(self, case: CaseFile) -> list[str]:
        """The in-force Code-dependent modules a known trade body would open."""
        facts = case.fact_view()
        out = []
        for m in self.kg.active_modules():
            if m.route == Route.LANDOWNER:
                continue
            if not self._needs_code_version(m):
                continue
            if evaluate(m.do_not_use_when, facts):
                continue
            if evaluate(m.use_when, facts):
                out.append(m.module_id)
        return out
