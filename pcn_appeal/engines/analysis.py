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

from .. import prompts
from ..kg.graph import KnowledgeGraph
from ..models import CaseFile, KBModule
from ..rules.dsl import evaluate

# A question is a cost to the customer, so the ceiling is low and silence is the
# default. `questions.yaml` still supplies these caps and the banned terms; it no
# longer supplies the questions.
DEFAULT_MAX_QUESTIONS = 4
DEFAULT_MAX_ROUNDS = 3
CANDIDATE_LIMIT = 24

QUESTION_TYPES = {"bool", "int", "choice", "text"}

# Facts that only make sense when the notice itself shows an ANPR entry/exit pair.
ANPR_SHAPED_FACTS = {
    "anpr_sequence_incomplete", "anpr_discrepancy", "anpr_duration_disputed",
    "multiple_visits",
}

# Strength at or above this may lead a letter (mirrors reasoning.SUPPORTING_THRESHOLD).
LEADING_STRENGTH = 50

# When analysis has no leading ground and the model asked nothing, ask plain
# situation facts that can unlock payment / permit / bay / signage / auth paths.
SITUATION_FALLBACK: list[dict[str, Any]] = [
    {"fact": "payment_made", "text": "Was a parking payment made or attempted for this visit?",
     "type": "bool", "material_because": "unlocks payment / keying grounds"},
    {"fact": "genuine_customer",
     "text": "Was the visit connected with genuine use of the premises at this location?",
     "type": "bool", "material_because": "unlocks customer / authorisation grounds"},
    {"fact": "permit_held",
     "text": "Was a permit or permission to park held for this location?",
     "type": "bool", "material_because": "unlocks permit grounds"},
    {"fact": "signage_issue_raised",
     "text": "Is there a specific problem with the signs (missing, hidden, damaged, unlit or contradictory)?",
     "type": "bool", "material_because": "unlocks signage grounds"},
    {"fact": "short_presence_before_acceptance",
     "text": "Did the vehicle leave without parking, or was time spent looking for a space or reading the terms?",
     "type": "bool", "material_because": "unlocks consideration / presence grounds"},
    {"fact": "vehicle_immobilised",
     "text": "Did the vehicle become mechanically unable to move (for example breakdown, flat battery or puncture)?",
     "type": "bool", "material_because": "unlocks breakdown grounds"},
]

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

    # ------------------------------------------------------------------ main
    def analyse(self, case: CaseFile, circumstances: str = "",
                pofa: Any = None, code_version: Optional[str] = None) -> CaseAnalysis:
        facts = case.fact_view()
        candidates = self._candidates(case, circumstances, facts)
        result = CaseAnalysis()
        result.trace.append(f"candidates={len(candidates)} (semantic + metadata filter + rerank)")

        rounds = sum(1 for a in case.audit if a.get("event") == "analysis_round")
        if rounds >= self.max_rounds:
            result.trace.append(
                f"question round limit reached ({rounds}>={self.max_rounds}); asking nothing")
            # Still propose grounds so drafting can proceed with what is known.
            try:
                raw = self.llm.complete_json(
                    task="case_analysis", system=prompts.system("case_analysis"),
                    user=self._payload(case, circumstances, facts, candidates, pofa, code_version))
                result.module_ids = self._veto(
                    case, raw.get("grounds") or [], facts, pofa, code_version, result)
            except Exception as exc:
                case.audit.append({"event": "case_analysis_error", "error": str(exc)})
                result.trace.append(f"case analysis unavailable ({type(exc).__name__}); nothing proposed")
            return result

        try:
            raw = self.llm.complete_json(
                task="case_analysis", system=prompts.system("case_analysis"),
                user=self._payload(case, circumstances, facts, candidates, pofa, code_version))
        except Exception as exc:
            # No proposal is a safe outcome: the pipeline drafts from what is
            # already proven rather than guessing, and asks nothing.
            case.audit.append({"event": "case_analysis_error", "error": str(exc)})
            result.trace.append(f"case analysis unavailable ({type(exc).__name__}); nothing proposed")
            return result

        proposed = raw.get("grounds") or []
        result.module_ids = self._veto(case, proposed, facts, pofa, code_version, result)
        # The model's own questions, then the facts that are holding back a ground
        # it chose. Both go through the same safeguards below.
        asking = list(raw.get("questions") or []) + self._unlocking_questions(case, result, facts)
        result.questions = self._safe_questions(case, asking, result)
        # Thin packs (support-only / landowner-only) must ask before drafting —
        # never dump the customer into manual review with nothing to answer.
        result.questions = self._ensure_situation_questions(case, result)

        case.audit.append({
            "event": "case_analysis",
            "proposed": [g.get("module_id") for g in proposed],
            "kept": result.module_ids,
            "suppressed": result.suppressed,
            "asked": [q["fact"] for q in result.questions],
            # Kept for audit only. `material_because` explains the system's
            # reasoning and is stripped before the question reaches a customer.
            "why_asked": {q.get("fact"): q.get("material_because")
                          for q in (raw.get("questions") or []) if q.get("fact")},
            "not_supported": raw.get("not_supported") or [],
        })
        return result

    # ------------------------------------------------------- candidate set
    def _candidates(self, case: CaseFile, circumstances: str,
                    facts: dict[str, Any]) -> list[KBModule]:
        """Approved grounds worth showing the model, by semantic relevance.

        Not filtered by route: that filtering was the V1 branch selector. The
        only exclusions are metadata ones - a module the KB has disabled, or one
        whose jurisdiction cannot apply to this case.
        """
        active = [m for m in self.kg.active_modules()]
        jurisdiction = facts.get("jurisdiction")
        filtered = [m for m in active if self._jurisdiction_ok(m, jurisdiction)]

        if self.retriever is None:
            return filtered[:CANDIDATE_LIMIT]

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
        return self._rerank(ranked, facts)[:CANDIDATE_LIMIT]

    @staticmethod
    def _jurisdiction_ok(module: KBModule, jurisdiction: Optional[str]) -> bool:
        """Metadata filter. PoFA grounds cannot apply outside England & Wales, so
        offering them in Scotland invites a proposal that must then be vetoed."""
        if not jurisdiction or jurisdiction == "UNKNOWN":
            return True
        pofa_only = any("PoFA" in str(s) for s in (module.legal_basis or []))
        return not (pofa_only and jurisdiction != "ENGLAND_WALES")

    def _rerank(self, modules: list[KBModule], facts: dict[str, Any]) -> list[KBModule]:
        """Metadata rerank: a ground whose required facts are already present is
        more useful to consider than one that would need everything asked."""
        def score(m: KBModule) -> tuple[float, str]:
            required = list(m.required_facts or [])
            have = sum(1 for f in required if facts.get(f) not in (None, "", []))
            coverage = (have / len(required)) if required else 0.5
            return (-coverage, m.module_id)       # id breaks ties deterministically
        return sorted(modules, key=score)

    # --------------------------------------------------------------- payload
    def _payload(self, case: CaseFile, circumstances: str, facts: dict[str, Any],
                 candidates: list[KBModule], pofa: Any, code_version: Optional[str]) -> str:
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
            } for m in candidates],
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

    # ------------------------------------------------------------------ veto
    def _veto(self, case: CaseFile, proposed: list[dict], facts: dict[str, Any],
              pofa: Any, code_version: Optional[str], result: CaseAnalysis) -> list[str]:
        findings = list(getattr(pofa, "findings", []) or [])
        kept: list[str] = []

        for entry in proposed:
            mid = (entry or {}).get("module_id")
            module = self.kg.modules.get(mid)

            if module is None or module.status != "ACTIVE":
                self._suppress(result, mid, "not an active ground in the approved knowledge base")
                continue
            if mid in kept:
                continue
            if evaluate(module.do_not_use_when, facts):
                self._suppress(result, mid, "the knowledge base forbids this ground on these facts")
                continue
            # Speculative grounds (use_when not yet satisfied) may still drive
            # questions, but must not enter analysis_module_ids for drafting —
            # otherwise the pack drops them and the letter collapses to intro+end.
            if not evaluate(module.use_when, facts):
                self._suppress(result, mid, "required facts for this ground are not yet established")
                continue
            if self._needs_pofa_finding(module) and not findings:
                self._suppress(result, mid, "statutory defect not confirmed by the PoFA calculation")
                continue
            if mid == "KB-POFA-04" and facts.get("notice_sides_complete") is False:
                self._suppress(result, mid, "both sides of the notice are not confirmed")
                continue
            if self._needs_code_version(module) and not code_version:
                self._suppress(result, mid, "no verified Code of Practice version applies")
                continue
            kept.append(mid)

        if not kept:
            # Allegation-shaped records request only — do not pad with always-on
            # landowner authority, which turns every thin case into the same letter.
            rec = self.kg.modules.get("KB-REC-01")
            if rec and rec.status == "ACTIVE":
                if evaluate(rec.use_when, facts) and not evaluate(rec.do_not_use_when, facts):
                    kept.append("KB-REC-01")
                    result.trace.append("seeded KB-REC-01 from allegation text")

        # When a fact-specific records ground is available, drop always-on LAND
        # filler so the letter stays about this allegation.
        if "KB-REC-01" in kept and "KB-LAND-01" in kept:
            kept = [m for m in kept if m != "KB-LAND-01"]
            result.trace.append("dropped KB-LAND-01: fact-specific REC ground present")

        if not kept:
            result.trace.append("no proposed ground survived the deterministic checks")
        return kept

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

    def _ensure_situation_questions(self, case: CaseFile,
                                    result: CaseAnalysis) -> list[dict]:
        """If nothing selected can lead a letter and the model asked nothing,
        ask a short set of situation facts instead of ending in manual review."""
        if result.questions:
            return result.questions
        if self._has_leading_ground(result.module_ids):
            return result.questions
        injected = self._safe_questions(case, list(SITUATION_FALLBACK), result)
        if injected:
            result.trace.append(
                f"injected situation questions (no leading ground): "
                f"{[q['fact'] for q in injected]}")
            return injected
        result.trace.append("no leading ground and no further situation questions available")
        return result.questions

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
            if fact in seen or case.has(fact) or fact in known_on_notice:
                result.trace.append(f"dropped question {fact}: already known or already asked")
                continue
            if not self._question_material_for_case(case, fact, result, text):
                result.trace.append(
                    f"dropped question {fact}: not needed for any available ground")
                continue
            if fact in operator_gaps or any(g in fact for g in (
                    "validation_log", "landowner", "signage_plan", "anpr_raw")):
                result.trace.append(
                    f"dropped question {fact}: operator-requestable; use records request in draft")
                continue
            kb_gated = bool((entry or {}).get("kb_gated"))
            topic = self._topic_for(fact, text)
            if topic and topic in dead_topics:
                result.trace.append(
                    f"dropped question {fact}: topic {topic} unresolved for this customer")
                continue
            if topic and topic in covered_topics and not kb_gated:
                result.trace.append(
                    f"dropped question {fact}: topic {topic} already asked")
                continue
            # Don't ask the customer to contact the store when operator records
            # can be requested in the draft instead.
            low_text = text.lower()
            if any(p in low_text for p in (
                "contact the store", "ask the store", "speak to the store",
                "ask sainsbury", "contact sainsbury", "ask the supermarket",
            )):
                result.trace.append(f"dropped question {fact}: store-contact; use operator records request")
                continue
            if fact in ANPR_SHAPED_FACTS and not (
                    case.has("entry_time") and case.has("exit_time")):
                result.trace.append(f"dropped question {fact}: notice has no ANPR entry/exit pair")
                continue
            if qtype not in QUESTION_TYPES:
                qtype = "text"

            low = text.lower()
            if any(b in low for b in self.banned):
                result.trace.append(f"dropped question {fact}: touches driver identity")
                continue
            if any(marker in low for marker in RATIONALE_MARKERS):
                result.trace.append(f"dropped question {fact}: explains its own legal purpose")
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
                    result.trace.append(f"dropped question {fact}: choice with no options")
                    continue
                question["options"] = options

            out.append(question)
            seen.add(fact)
            if topic:
                covered_topics.add(topic)
            if admin_kind:
                covered_topics.add(admin_kind)
            if len(out) >= self.max_questions:
                break

        return out

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
            if case.get("jurisdiction") not in (None, "", "UNKNOWN"):
                return False
            return True
        return True

    def _fact_specific_path_open(self, case: CaseFile, result: CaseAnalysis) -> bool:
        """True when BAY/REC/other non-PoFA non-LAND ground is selected or gated-in.

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
            if mod and mod.route not in ("POFA", "LAND"):
                return True
        facts = case.fact_view()
        for m in self.kg.active_modules():
            if m.route in ("POFA", "LAND"):
                continue
            if evaluate(m.use_when, facts) and not evaluate(m.do_not_use_when, facts):
                return True
        return False

    def _ata_would_unlock(self, case: CaseFile) -> bool:
        facts = case.fact_view()
        for m in self.kg.active_modules():
            if m.route == "LAND" or str(m.module_id).startswith("KB-LAND"):
                continue
            if not self._needs_code_version(m):
                continue
            if evaluate(m.do_not_use_when, facts):
                continue
            if evaluate(m.use_when, facts):
                return True
        return False
