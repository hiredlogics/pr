"""Orchestrator - the only component that moves a case between states.

    CREATED -> EXTRACTED -> CONFIRMED -> QUESTIONING -> ANALYSED -> DRAFTED
            -> (VALIDATION_FAILED -> regenerate x N) -> RELEASED | MANUAL_REVIEW

Production: run each step as a durable workflow activity (Temporal, or Celery
+ Postgres state table). The customer-facing steps (confirm, answer) are
signals/API calls that resume the workflow; LLM steps are retried idempotently.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .drafting.drafter import LLMDrafter, TemplateDrafter
from .engines.account import assess_material_account  # thin path retained for tests
from .semantics.resolver import SemanticCaseResolver
from .engines.analysis import AnalysisEngine
from .engines.claim_plan_authority import ClaimPlanBuilder
from .integrity import ai_log
from .drafting import quality_judge, shadow_judge, versions as draft_versions
from .drafting.context import DraftContext
from .engines.draft_validation_engine import DraftValidationEngine, merge as merge_validation
from .engines.extraction import ExtractionEngine
from .engines.outcome import analysis_failed, classify_hold
from .engines.question_authority import (CONFIRMATION, CONFLICT, HYPOTHESIS, POSTCODE,
                                         QuestionAuthority)
from .engines.questioning import QuestionEngine
from .engines.reasoning import ReasoningEngine
from .engines.recovery import FactRecoveryEngine, postcode_unlocks
from .engines.validation import ValidationEngine
from . import customer_safe
from .fact_graph import FactManager
from .hypotheses import Hypotheses
from .kg.graph import KnowledgeGraph
from .models import CaseFile, CaseState, Draft, FactStatus, RetrievalPack, ValidationIssue, ValidationResult
from .rules import scope
from .rules.scope import ScopeStop

MAX_ATTEMPTS = 3
# Re-analysis rounds inside generate() before the last-resort safety stop. The
# customer flow always attempts automatic completion: a case whose first
# analysis produced nothing that can lead is re-analysed, not held.
MAX_ANALYSIS_ROUNDS = 3


DECLINED_KEY = "_declined"


def declined_questions(case: CaseFile) -> set[str]:
    """Facts the customer chose to skip. Recorded in raw_answers so it persists
    with the case; never asked again and never a reason to hold."""
    raw = (case.raw_answers or {}).get(DECLINED_KEY) or ""
    return {f for f in raw.split(",") if f}


def record_declined(case: CaseFile, facts) -> None:
    now = declined_questions(case) | {f for f in facts if f}
    if now:
        case.raw_answers[DECLINED_KEY] = ",".join(sorted(now))


def _with_outcome(out: AppealOutput, case: CaseFile) -> AppealOutput:
    """Attach a customer outcome when the letter was not released, and close
    the run: this outcome is the run's, and the next step starts a new one."""
    if case.get("appeal_period_expired"):
        out.customer_notices = [AppealPipeline.late_notice(case)]
        case.audit.append({"event": "customer_notice",
                           "codes": ["APPEAL_PERIOD_MAY_HAVE_EXPIRED"]})
    if out.state == CaseState.RELEASED:
        case.complete_run(CaseState.RELEASED.value)
        return out
    info = classify_hold(case, out.pack, out.validation, out.draft)
    out.outcome = info.get("outcome")
    out.outcome_title = info.get("outcome_title")
    out.outcome_message = info.get("outcome_message")
    out.outcome_next = info.get("outcome_next")
    out.cta_label = info.get("cta_label")
    out.can_continue = bool(info.get("can_continue", True))
    case.audit.append({"event": "customer_outcome", **{k: v for k, v in info.items()
                                                       if k != "detail"},
                       "detail": info.get("detail")})
    case.complete_run(info.get("outcome") or out.state.value)
    return out


@dataclass
class AppealOutput:
    state: CaseState
    letter: Optional[str]
    pack: RetrievalPack
    draft: Draft
    validation: ValidationResult
    evidence_list: list[str]
    # Customer hold outcome when state is MANUAL_REVIEW / VALIDATION_FAILED.
    # Never a legal ground — maps the pipeline stop to the right UI message.
    outcome: Optional[str] = None
    outcome_title: Optional[str] = None
    outcome_message: Optional[str] = None
    outcome_next: Optional[str] = None
    cta_label: Optional[str] = None
    can_continue: bool = True
    # P0.5: what produced this result (manifest.py). Admin only.
    manifest: Optional[dict] = None
    # P5.5: integrity checks and the execution trace for this run
    # (integrity/). Admin only.
    integrity: Optional[dict] = None
    # P8: customer-facing notices that accompany a letter (never block it).
    customer_notices: list = None



@dataclass
class AutoAppealResult:
    """One-click result. `questions` non-empty means the run paused because a
    missing fact gates a ground that would change the letter; answer them and
    call auto_appeal again to finish. Otherwise `output` holds the appeal.

    `NO_APPEAL_RIGHT` means Engine 0 routed the document out of this service, so
    `stop_reason` carries the explanation and no appeal letter is ever produced.
    """
    case_id: str
    state: CaseState
    questions: list[dict]
    output: Optional[AppealOutput]
    flags: list[str]
    skipped_questions: list[str]
    stop_reason: Optional[str] = None
    recommendation: Optional[str] = None
    stop_code: Optional[str] = None
    cta_label: Optional[str] = None
    cta_action: Optional[str] = None


class AppealPipeline:
    def __init__(self, llm, drafter=None, judge=None, kg: Optional[KnowledgeGraph] = None,
                 shadow_judge_enabled: Optional[bool] = None,
                 quality_judge_enabled: Optional[bool] = None):
        self.kg = kg or KnowledgeGraph()
        # P5.5: every model call is logged against the case it serves
        # (integrity/ai_log.py) - task, model, prompt version, digests, timing.
        llm = ai_log.audited(llm)
        if judge is not None:
            judge = ai_log.audited(judge)
        self.llm = llm  # P10.5: semantic extraction uses the same client
        self.extraction = ExtractionEngine(llm)
        self.questions = QuestionEngine(self.kg)
        self.reasoning = ReasoningEngine(self.kg)
        # Exhaust documents + UK rule calculators before any customer question.
        self.recovery = FactRecoveryEngine(self.kg)
        # The only substantive authority over which grounds are argued and which
        # questions are asked. Shares the reasoning engine's retriever so the
        # candidate set comes from the same approved KB index.
        self.analysis = AnalysisEngine(self.kg, llm, retriever=self.reasoning.retriever)
        self.authority = QuestionAuthority(self.kg)
        # LLM drafting is primary so letters weave allegation, evidence and
        # unresolved facts. TemplateDrafter remains the outage / last-attempt
        # fallback and still builds case-specific REC paragraphs from the pack.
        self.drafter = drafter or LLMDrafter(llm)
        self.fallback = TemplateDrafter(self.kg)
        self.validation = ValidationEngine(judge, kg=self.kg)
        # P6: sentence grounding and the draft-level checks (DV-*), run before
        # the VAL-* engine; and the optional second-model judge, shadow only.
        self.draft_validation = DraftValidationEngine(kg=self.kg)
        self.shadow = (shadow_judge.ShadowJudge(judge or llm)
                       if shadow_judge.enabled(shadow_judge_enabled) else None)
        # Appeal quality judge (client brief 2026-10-09 §8): reads a letter that
        # already passed the validators for fidelity and case specificity, so a
        # template that would suit any charge, or a fact quietly strengthened,
        # is caught. It has no authority over the law - it cannot add or remove
        # a ground. It records its verdict on every letter. It enforces - a REWRITE
        # sends the same case back to the drafter, and a hard finding that survives
        # the rewrites holds the letter (drafting/quality_judge.py) - only when
        # QUALITY_JUDGE_ENFORCE=1. Live, enforcing held 4 of 5 sound letters (airport
        # drop-off, driver letter): a model judge is not yet reliable enough to be a
        # release gate, so by default the deterministic validators alone decide.
        self.quality = (quality_judge.QualityJudge(judge or llm)
                        if quality_judge.enabled(quality_judge_enabled, llm) else None)
        self.quality_enforcing = (os.getenv("QUALITY_JUDGE_ENFORCE") or "0").strip().lower()             in ("1", "true", "yes", "on")
        # P5: the only authority over which claims a letter argues. Analysis,
        # reassessment and ground recovery propose; this decides and locks.
        self.claim_authority = ClaimPlanBuilder(self.kg, self.reasoning)

    # step 1-2
    def ingest(self, case: CaseFile) -> list[str]:
        case.ensure_run("ingest")
        return self.extraction.run(case)

    def confirm(self, case: CaseFile, corrections: dict, confirmed: list[str], narrative: str) -> list[dict]:
        case.ensure_run("confirm")
        self.extraction.confirm(case, corrections, confirmed, llm=self.llm)
        # Store narrative before any scope stop so free-text provenance survives
        # out-of-scope routing. Does not change disclosure status.
        case.raw_answers["narrative"] = narrative
        if self._apply_scope_stop(case):
            return []
        self.reasoning.enrich(case)
        questions, shown = self._reanalyse(case, narrative)
        self._commit_shown(case, shown)
        return questions

    # step 3 (called per answer batch; returns follow-ups or [] when done)
    def answer(self, case: CaseFile, answers: dict) -> list[dict]:
        case.ensure_run("answer")
        for fact, raw in answers.items():
            self.questions.record_answer(case, fact, raw)
        questions, shown = self._reanalyse(case, case.raw_answers.get("narrative", ""))
        self._commit_shown(case, shown)
        return questions

    @staticmethod
    def _commit_shown(case: CaseFile, shown: list[dict]) -> None:
        """Record a question as asked only once it is the result actually
        returned to the customer for this request - never for a question that
        was only selected during an internal re-analysis pass whose return
        value was then discarded in favour of a later one in the same request
        (auto_appeal can call _reanalyse via confirm/answer more than once).
        Idempotent, so calling it again for the same facts is harmless."""
        for q in shown:
            fact = q.get("fact")
            if fact and fact not in case.asked_questions:
                case.asked_questions.append(fact)
            if q.get("hypothesis_id"):
                Hypotheses.mark_asked(case, q["hypothesis_id"], q["text"])

    # ------------------------------------------------------------ V2 analysis
    def _reanalyse(self, case: CaseFile, narrative: str) -> tuple[list[dict], list[dict]]:
        """Re-run case analysis against everything now known, and return only the
        questions it still genuinely needs, plus the raw `review.shown` entries
        behind them.

        Called after confirmation and again after every answer, because an answer
        changes what is material: it can settle a ground, open one, or make a
        question that looked necessary pointless.

        Does NOT mark anything as asked - a caller within the same request
        (auto_appeal) may call this more than once and only the last result is
        actually shown to the customer. The caller must commit `shown` via
        `_commit_shown` once it knows which result is final.
        """
        # P17.9: single semantic boundary → FactManager (no raw→fact bypass).
        SemanticCaseResolver.resolve(case, llm=self.llm, narrative=narrative)
        analysis = self.analysis_of(case, narrative)
        case.analysis_module_ids = analysis.module_ids
        if getattr(analysis, "claim_plan", None):
            case.audit.append({"event": "analysis_claim_plan",
                               "claim_plan": analysis.claim_plan})
        # Every source proposes candidates; the Question Authority decides.
        #   P1: a document value the customer contradicted (case integrity).
        #   P2: what the account might mean, its own wording replacing any
        #       analysis question for the same fact.
        #   Analysis: the model's questions and the KB gates of grounds it chose.
        from .document_identity import identity_customer_questions
        conflict = [dict(q, source=CONFLICT) for q in self._pcn_conflict_question(case)]
        conflict += [dict(q, source=CONFIRMATION) for q in self._original_notice_question(case)]
        confirm = [dict(q, source=CONFIRMATION) for q in FactManager.confirmation_questions(case)]
        # P17.8/P17.10: identity uncertainty/pair conflict must pause with a
        # customer question — never fall through to generate → empty hold.
        identity_qs = [
            dict(q, source=CONFIRMATION) for q in identity_customer_questions(case)
        ]
        hypothesis = [dict(q, source=HYPOTHESIS)
                      for q in Hypotheses.questions(case, self._could_change_a_ground)]
        # Phase 2: an account that cannot be understood without guessing is
        # clarified first, because everything after it reads what it means.
        from .semantics import understanding
        clarify = [dict(q, source=understanding.SOURCE)
                   for q in understanding.pending_question(case)]
        candidates = (clarify + conflict + confirm + identity_qs + hypothesis
                      + list(analysis.questions))
        candidates += self._site_postcode_question(case, analysis.module_ids, candidates)
        # Materiality gate: suppress background / already-resolved questions.
        # Integrity conflicts and FactManager confirmations always remain.
        from .engines.question_materiality import filter_material
        integrity = {CONFLICT, CONFIRMATION, understanding.SOURCE}
        must = [q for q in candidates if q.get("source") in integrity]
        rest = [q for q in candidates if q.get("source") not in integrity]
        keep, suppressed = filter_material(case, self.kg, rest)
        if suppressed:
            case.audit.append({
                "event": "question_materiality",
                "suppressed": [
                    {k: s.get("materiality", {}).get(k)
                     for k in ("question_id", "target_fact", "suppress_reason",
                               "requesting_module_ids", "why_answer_can_change_outcome",
                               "existing_sources_checked", "case_revision")}
                    for s in suppressed
                ],
                "kept": [k.get("fact") for k in keep],
            })
        candidates = must + keep
        review = self.authority.review(
            case, candidates, selected=analysis.module_ids,
            prior_rejections=getattr(analysis, "question_rejections", []))
        # One question at a time. The others wait: the answer to this one may
        # make them pointless, and the next round re-decides from scratch.
        # The full question object (question_id, related_module, material
        # reason, impacts) stays in the authority's trace; what is pending, and
        # returned, is only what the customer answers.
        analysis.questions = customer_safe.customer_questions(review.shown)
        case.pending_questions = analysis.questions
        # Q-07: once shown, a question must not reappear under a new name on the
        # next round - but "shown" here only means this pass selected it; the
        # caller commits it to asked_questions (_commit_shown) only once this
        # pass's result is the one actually returned to the customer.
        case.audit.append({"event": "analysis_round", "grounds": analysis.module_ids,
                           "asking": [q["fact"] for q in analysis.questions],
                           "approved_waiting": [q["fact"] for q in review.approved[1:]]})
        return analysis.questions, review.shown

    def _could_change_a_ground(self, fact: str) -> bool:
        """Material: some in-force KB module is gated on or requires the fact."""
        return any(fact in self.kg.gating_facts(m.module_id) or fact in (m.required_facts or [])
                   for m in self.kg.active_modules())

    def _postcode_materiality(self, case: CaseFile) -> tuple[list[str], dict]:
        """Is the site postcode material to THIS case? One predicate, two callers.

        The ask path and the hold path each used to decide this for themselves
        and had diverged: the hold path skipped the fact-specific-ground check
        entirely, so a case the ask path had deliberately spared was asked the
        postcode anyway at hold time. Returns ([], {}) when asking it cannot
        change the outcome.
        """
        if "site_postcode" in declined_questions(case):
            # Skip-loop fix: the customer skipped it once; holding for it again
            # would show the same question forever. The keeper appeal that does
            # not depend on the jurisdiction goes ahead instead.
            case.audit.append({"event": "site_postcode_skipped",
                               "reason": "customer_declined"})
            return [], {}
        if case.get("jurisdiction") not in (None, "", "UNKNOWN"):
            case.audit.append({
                "event": "site_postcode_skipped",
                "reason": "jurisdiction_already_established",
                "jurisdiction": case.get("jurisdiction"),
            })
            return [], {}
        # Fact-specific ANPR/ACTIVITY/etc. already open — postcode is background.
        try:
            if self.recovery._fact_specific_ground_open(case):
                case.audit.append({
                    "event": "site_postcode_skipped",
                    "reason": "fact_specific_ground_open_without_postcode",
                })
                return [], {}
        except Exception as exc:
            # Unknown either way. Keep the outcome-safe default (ask rather
            # than lose a ground) but never let the failure go unrecorded.
            case.audit.append({
                "event": "site_postcode_predicate_error",
                "error": str(exc)[:200],
            })
        unlocks = postcode_unlocks(case, self.kg)
        q = self.kg.question_for("site_postcode")
        if not unlocks or not q:
            return [], {}
        return list(unlocks), dict(q)

    def _site_postcode_question(self, case: CaseFile, module_ids, already: list[dict]) -> list[dict]:
        """The site postcode, asked only when nothing selected can lead the
        letter and knowing the site is in England & Wales would unlock a
        leading Schedule 4 ground (`postcode_unlocks`). Otherwise an unreadable
        postcode silently withheld PoFA and the letter fell back to landowner
        authority alone. Asked once; never the keeper's address.

        Not asked when a fact-specific non-PoFA ground already clears its gates
        (e.g. ANPR multiple visits, activity) — the answer would not change
        eligibility or outcome. Not asked when jurisdiction is already known
        from document/location evidence.
        """
        if "site_postcode" in case.asked_questions or any(q.get("fact") == "site_postcode" for q in already):
            return []
        if self.reasoning.leading_grounds(module_ids):
            return []
        unlocks, q = self._postcode_materiality(case)
        if not unlocks or not q:
            return []
        case.audit.append({
            "event": "site_postcode_material",
            "unlocks": unlocks,
            "reason": (
                "jurisdiction UNKNOWN and postcode would unlock leading Schedule 4 "
                "ground(s); no fact-specific non-PoFA path already open"
            ),
            "could_change_outcome": True,
            "already_resolved": False,
        })
        # `source` and `unlocks` are for the Question Authority; the customer
        # gets only fact/text/type (customer_safe.customer_question).
        return [{"fact": "site_postcode", "type": q.get("type", "text"), "text": q["text"],
                 "source": POSTCODE, "unlocks": unlocks}]

    # Documents a customer can be asked to upload (pcn_appeal/evidence_requests.py).
    from .evidence_requests import EVIDENCE_REQUESTS

    def evidence_requests(self, case: CaseFile) -> list[dict]:
        """Upload requests for grounds held back only by a missing document.

        A module qualifies when its use_when is not yet TRUE, would be TRUE with
        that document uploaded, and its do_not_use_when is not TRUE. Asked once:
        an upload or a skip settles it. Never asked when the keeper route is
        closed, since nothing here could then be argued."""
        from .disclosure import keeper_route_blocked
        from .rules.dsl import evaluate3
        if keeper_route_blocked(case):
            return []
        view = case.fact_view()
        have = set(view.get("evidence_kinds") or [])
        declined = declined_questions(case)
        out: list[dict] = []
        for kind, spec in self.EVIDENCE_REQUESTS.items():
            fact = f"evidence:{kind}"
            if fact in case.asked_questions or fact in declined:
                continue
            if have & {kind, *spec["also"]}:
                continue
            with_doc = dict(view, evidence_kinds=sorted(have | {kind}))
            unlocks = [m.module_id for m in self.kg.active_modules()
                       if evaluate3(m.use_when, view) is not True
                       and evaluate3(m.use_when, with_doc) is True
                       and evaluate3(m.do_not_use_when, with_doc) is not True]
            if not unlocks:
                continue
            location = str(case.get("parking_location") or "the site")
            out.append({"fact": fact, "type": "upload", "evidence_kind": kind,
                        "text": spec["text"].format(location=location)})
            case.asked_questions.append(fact)
            case.audit.append({"event": "evidence_requested", "kind": kind, "unlocks": unlocks})
        if out:
            case.pending_questions = out
        return out

    def add_customer_evidence(self, case: CaseFile, kind: str, items: list) -> list[str]:
        """Attach documents the customer uploaded in answer to an evidence
        request. The kind is the one requested, so the gate that asked for it
        reads it; the file is listed among the letter's enclosures."""
        from .models import EvidenceItem
        if kind not in self.EVIDENCE_REQUESTS:
            raise ValueError(f"no request for evidence of kind {kind}")
        added = []
        for doc in items:
            n = len(case.evidence) + 1
            while f"E{n}" in case.evidence:
                n += 1
            ev_id = f"E{n}"
            case.evidence[ev_id] = EvidenceItem(ev_id, kind, doc.filename, text=doc.text,
                                                images=doc.images,
                                                storage_url=getattr(doc, "storage_url", None))
            case.document_classes[ev_id] = kind
            added.append(ev_id)
        if added:
            case.audit.append({"event": "customer_evidence_added", "kind": kind,
                               "evidence": added})
            case.pending_questions = [q for q in case.pending_questions or []
                                      if q.get("fact") != f"evidence:{kind}"]
        return added

    def _original_notice_question(self, case: CaseFile) -> list[dict]:
        """P8: a reminder or driver letter is not the first Notice to Keeper.
        When its original could not be linked, ask once for the original date
        (the keeper-liability timing depends on it). Never asked when the
        keeper route is closed or the date is already known."""
        from .disclosure import keeper_route_blocked
        from .engines.derivation import LATER_STAGES
        fact = "original_notice_issue_date"
        if case.get("notice_stage") not in LATER_STAGES or case.has(fact):
            return []
        if fact in case.asked_questions or keeper_route_blocked(case):
            return []
        q = self.kg.question_for(fact) or {}
        if not q.get("text"):
            return []
        return [{"fact": fact, "text": q["text"], "type": q.get("type", "text")}]

    @staticmethod
    def _pcn_conflict_question(case: CaseFile) -> list[dict]:
        """The one question a missing value is never allowed to generate, and a
        conflict has to.

        Two documents carrying different charge numbers is not an absent field:
        the letter must cite one of them, and citing the wrong one puts the
        customer's appeal against a charge that is not theirs. The extractor
        marks the number UNCERTAIN, which correctly bars it from grounding
        anything (EX-02) and also stops auto-confirm settling it, so before this
        the case could only be held. Asking is the resolution; the options are
        the numbers actually read off the documents, so it cannot invent one.
        """
        if not case.get("pcn_conflict") or "pcn_number" in case.asked_questions:
            return []
        options = [str(c) for c in (case.get("pcn_candidates") or [])]
        if len(options) < 2:
            return []
        return [{
            "fact": "pcn_number",
            "text": "Your documents show more than one charge number. "
                    "Which one is on the notice you are appealing?",
            "type": "choice",
            "options": options,
        }]

    def analysis_of(self, case: CaseFile, narrative: str):
        """Case analysis against a completed document baseline.

        Order: document belt (enrich → recovery → legal findings) → persist
        DocumentBaseline → Case Intelligence (proposal only) → analysis state.
        A later round loads the baseline and adds a customer delta; it does not
        replace document truth.
        """
        from .document_baseline import establish_document_baseline, record_analysis_state
        baseline = establish_document_baseline(self, case)
        version, pofa_res = self.reasoning.applicability(case)
        analysis = self.analysis.analyse(case, narrative, pofa=pofa_res,
                                         code_version=getattr(version, "version_id", None))
        record_analysis_state(case, analysis, baseline)
        return analysis

    # ---------------------------------------------------------------- one click
    def auto_appeal(self, case: CaseFile, narrative: str = "", answers: Optional[dict] = None,
                    skip_remaining: bool = False) -> AutoAppealResult:
        """Run the whole journey unattended, pausing only where a missing fact
        actually gates a ground worth having.

        The customer's confirmation step is not skipped so much as defaulted:
        facts the extractor read confidently are auto-confirmed, while anything
        UNCERTAIN is left unconfirmed (EX-02 already bars those from grounding a
        defect) and reported in `flags` so the UI can still query them.

        `skip_remaining` is the customer declining to answer. It is safe rather
        than a shortcut: the facts stay absent, so every module they gate fails
        its own use_when and simply never fires. The letter gets narrower, never
        less supported - which is why a "skip" button cannot produce a claim the
        customer did not substantiate.
        """
        flags: list[str] = []
        # What the customer was looking at when they chose to skip: re-analysis
        # below replaces pending_questions, so read it first.
        shown_before = [q.get("fact") for q in (case.pending_questions or [])]
        case.ensure_run("auto_appeal")
        if case.state == CaseState.CREATED:
            flags = self.ingest(case)
            stopped = self._stop_if_no_appeal_right(case, flags)
            if stopped:
                return stopped
            # The reverse page is optional: it is never asked for and its
            # absence never pauses the case. What it would have told us is
            # carried by `notice_sides_complete`, so a content finding that
            # needs the reverse wording stays unresolved rather than invented.
            questions = self.confirm(case, {}, self._auto_confirmable(case), narrative)
        else:
            stopped = self._stop_if_no_appeal_right(case, flags)
            if stopped:
                return stopped
            questions, shown = self._reanalyse(case, case.raw_answers.get("narrative", ""))
            if not answers:
                self._commit_shown(case, shown)
        if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
            return self._stop_if_no_appeal_right(case, flags)  # type: ignore[return-value]
        if answers:
            questions = self.answer(case, answers)

        if not questions and not skip_remaining:
            # Receipt request: a ground that only lacks a document the customer
            # may hold (a receipt for KB-CUST-01) asks for it once.
            questions = self.evidence_requests(case)
        blocking = [] if skip_remaining else questions
        skipped = [q["fact"] for q in questions if q not in blocking]
        if skip_remaining:
            record_declined(case, skipped + shown_before
                            + [q.get("fact") for q in case.pending_questions or []])
            case.pending_questions = []
        if blocking:
            case.audit.append({"event": "auto_appeal_paused",
                               "asking": [q["fact"] for q in blocking], "skipped": skipped})
            return AutoAppealResult(case.case_id, case.state, blocking, None, flags, skipped)

        case.audit.append({"event": "auto_appeal_generating", "skipped": skipped,
                           "skipped_by_customer": bool(skip_remaining)})
        out = self.generate(case)
        return AutoAppealResult(case.case_id, out.state, [], out, flags, skipped)

    @staticmethod
    def _apply_scope_stop(case: CaseFile) -> Optional[ScopeStop]:
        """Engine 0's verdict, applied to the case. Returns the stop, if any.

        A product eligibility stop, not a legal ground selector: there is no
        question path and no letter beyond this point.
        """
        stop = scope.decide(case)
        if stop is None:
            return None
        # A classifier that returned nothing is our failure, not a verdict on the
        # document, so it gets its own state: the case is retryable and must not
        # be recorded as having no appeal right.
        technical = stop.code in scope.TECHNICAL_STOPS
        case.state = (CaseState.CLASSIFICATION_FAILED if technical
                      else CaseState.NO_APPEAL_RIGHT)
        case.scope_stop = stop.code
        case.audit.append({"event": "classification_failed" if technical else "no_appeal_right",
                           "document_class": stop.code,
                           "classified": dict(case.document_classes)})
        return stop

    def _stop_if_no_appeal_right(self, case: CaseFile, flags: list[str]) -> Optional[AutoAppealResult]:
        stop = self._apply_scope_stop(case)
        if stop is None:
            return None
        return AutoAppealResult(case.case_id, case.state, [], None, flags, [],
                                stop_reason=stop.message,
                                recommendation=stop.recommendation,
                                stop_code=stop.code, cta_label=stop.cta_label,
                                cta_action=stop.cta_action)

    @staticmethod
    def _auto_confirmable(case: CaseFile) -> list[str]:
        """Names of facts the extractor read above the confidence threshold.
        UNCERTAIN facts are deliberately excluded - auto-confirming a value the
        model was unsure of is exactly how a fabricated defect gets into a letter.

        P17.8: critical document identity fields are auto-confirmed only when
        DocumentIdentityState marked them VERIFIED (not CONFLICT / UNCERTAIN).
        """
        from .document_identity import CRITICAL_FIELDS, critical_fields_auto_confirmable
        allowed_critical = critical_fields_auto_confirmable(case)
        out = []
        for n, f in case.facts.items():
            if f.status != FactStatus.EXTRACTED:
                continue
            if n in CRITICAL_FIELDS and n not in allowed_critical:
                continue
            out.append(n)
        return out


    # step 4-6
    def generate(self, case: CaseFile) -> AppealOutput:
        """Steps 4-6 for the case's current run, ending in a released letter or a
        hold, with the run's execution manifest attached either way."""
        case.ensure_run("generate")
        out = self._generate(case)
        from . import manifest
        from .integrity import record
        manifest.attach(case, out, self)
        # P5.5: the run's integrity checks and execution trace, on the output
        # and in the audit. Never fails the run.
        record(case, out, self)
        return out

    def _generate(self, case: CaseFile) -> AppealOutput:
        stop = self._apply_scope_stop(case)
        if stop:
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts={}, fact_refs={}, missing_facts=[], evidence_refs=[],
                prohibited_claims=[], code_version=None, pofa_route="NOT_APPLICABLE",
                pofa_findings=[], driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=[f"stopped: {stop.code} - no ordinary appeal"])
            return _with_outcome(
                AppealOutput(case.state, None, empty,
                             Draft(case.case_id, []), ValidationResult(False, []), []),
                case)
        # P17.8: critical document identity must pass before Claim Plan LOCK.
        from .document_identity import identity_blocks_claim_plan
        identity_block = identity_blocks_claim_plan(case)
        if identity_block:
            case.state = CaseState.MANUAL_REVIEW
            case.audit.append({
                "event": "held_document_identity",
                "reason": identity_block,
                "identity": (case.raw_answers or {}).get("_document_identity_compact"),
            })
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts=case.fact_view(), fact_refs={},
                missing_facts=["document_identity"],
                evidence_refs=[], prohibited_claims=[], code_version=None,
                pofa_route="UNRESOLVED", pofa_findings=[],
                driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=[f"held: document identity ({identity_block})"])
            issues = [ValidationIssue(
                "VAL-CRITICAL-DOCUMENT-IDENTITY", "BLOCK",
                f"Critical document identity incomplete or conflicted ({identity_block}). "
                "Confirm or re-upload the notice before a letter can be released.")]
            return _with_outcome(
                AppealOutput(case.state, None, empty, Draft(case.case_id, []),
                             ValidationResult(False, issues), []),
                case)
        # Semantic → FactManager handoff must be current before Claim Plan lock.
        from .semantics.state import handoff_blocks_claim_plan, open_material_fact_conflicts
        handoff_block = handoff_blocks_claim_plan(case)
        if handoff_block:
            case.state = CaseState.MANUAL_REVIEW
            case.audit.append({
                "event": "held_semantic_handoff",
                "reasons": handoff_block.split(";"),
                "material_conflicts": open_material_fact_conflicts(case),
            })
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts=case.fact_view(), fact_refs={},
                missing_facts=[c.get("fact") for c in open_material_fact_conflicts(case)],
                evidence_refs=[], prohibited_claims=[], code_version=None,
                pofa_route="UNRESOLVED", pofa_findings=[],
                driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=[f"held: semantic handoff incomplete ({handoff_block})"])
            return _with_outcome(
                AppealOutput(case.state, None, empty, Draft(case.case_id, []),
                             ValidationResult(False, []), []),
                case)
        # P1: a document-owned fact the customer contradicted is not confirmed,
        # so nothing may be drafted from it, and the customer is asked rather
        # than the case being held without a route forward.
        pending = FactManager.needs_confirmation(case)
        if pending:
            case.state = CaseState.MANUAL_REVIEW
            # Through the Question Authority like every other question, one at a time.
            case.pending_questions = customer_safe.customer_questions(self.authority.review(
                case, [dict(q, source=CONFIRMATION)
                       for q in FactManager.confirmation_questions(case)]).shown)
            case.audit.append({"event": "held_needs_fact_confirmation",
                               "facts": [c["fact"] for c in pending]})
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts=case.fact_view(), fact_refs={},
                missing_facts=[c["fact"] for c in pending], evidence_refs=[],
                prohibited_claims=[], code_version=None, pofa_route="UNRESOLVED",
                pofa_findings=[], driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=["held: facts need the customer's confirmation"])
            return _with_outcome(
                AppealOutput(case.state, None, empty, Draft(case.case_id, []),
                             ValidationResult(False, []), []),
                case)
        # Unresolved PCN-number conflict must not ship a letter that may cite the
        # wrong reference: an appeal against a charge that is not the customer's
        # is worse than no appeal. Reaching here means the question above was put
        # and not answered, or the conflict was never confirmed away - so this is
        # the safety stop, not the first response to the conflict.
        if case.get("pcn_conflict"):
            case.state = CaseState.MANUAL_REVIEW
            case.audit.append({"event": "blocked_pcn_conflict",
                               "message": "conflicting PCN numbers remain unresolved"})
            empty = RetrievalPack(
                primary_route=None, secondary_routes=[], module_ids=[],
                verified_facts=case.fact_view(), fact_refs={}, missing_facts=["pcn_number"],
                evidence_refs=[], prohibited_claims=[], code_version=None,
                pofa_route="UNRESOLVED", pofa_findings=[],
                driver_status=case.driver_status.value,
                jurisdiction=str(case.get("jurisdiction") or "UNKNOWN"),
                context_chunks=[], lease_clauses=[],
                trace=["blocked: conflicting PCN numbers"])
            issues = [ValidationIssue("VAL-CONFLICT", "BLOCK",
                                      "Conflicting PCN numbers were read from the documents. "
                                      "Confirm the correct number before a letter can be released.")]
            return _with_outcome(
                AppealOutput(case.state, None, empty, Draft(case.case_id, []),
                             ValidationResult(False, issues), []),
                case)
        # P8.3: document belt before any further Case Intelligence proposal.
        from .document_baseline import establish_document_baseline
        establish_document_baseline(self, case)
        # Proposals: case analysis and ground recovery may still re-propose here.
        self._analyse_until_a_ground_can_lead(case)
        # P5: the decision. One LOCKED claim plan; from here on nothing adds,
        # removes or reorders a claim - the pack, the drafter and validation all
        # work from this plan (engines/claim_plan_authority.py).
        plan = self.claim_authority.decide(case, trust=self._plan_trust())
        pack = self.reasoning.pack_for(case, plan)
        # Structured diagnostic for audit / support — never invents retrieval hits.
        case.audit.append({
            "event": "retrieval_pack",
            "primary_route": pack.primary_route,
            "secondary_routes": list(pack.secondary_routes or []),
            "module_ids": list(pack.module_ids or []),
            "pofa_route": pack.pofa_route,
            "pofa_findings": list(pack.pofa_findings or []),
            "code_version": pack.code_version,
            "missing_facts": list(pack.missing_facts or []),
            "chunk_ids": [c.get("id") for c in (pack.context_chunks or [])],
            "case_context_keys": sorted((pack.case_context or {}).keys()),
            "validation_status": (pack.case_context or {}).get("validation_status"),
            "shopping_receipt_enclosed": (pack.case_context or {}).get("shopping_receipt_enclosed"),
            "trace": list(pack.trace or [])[-40:],
            "recovery_summary": {
                "recovered": list(((case.recovery_report or {}).get("recovered") or {}).keys()),
                "unknown_material": [
                    g.get("fact") for g in ((case.recovery_report or {}).get("unknown_material") or [])
                ],
                "conflicts": list((case.recovery_report or {}).get("conflicts") or []),
                "operator_requestable": list(
                    (case.recovery_report or {}).get("operator_requestable") or []),
            },
        })
        # Completed analysis with nothing to argue: stop here with a truthful
        # no-supported-grounds outcome. Do not draft an empty pack and then
        # dress VAL-SUBSTANCE / no_ground as a merits judgment.
        if not (pack.module_ids or []) and analysis_failed(case):
            # The model call failed: hold as a processing error, retryable on
            # this same case, and never recorded as "no supported grounds".
            case.state = CaseState.MANUAL_REVIEW
            case.audit.append({"event": "analysis_failed_nothing_selected", "module_ids": []})
            return _with_outcome(
                AppealOutput(case.state, None, pack, Draft(case.case_id, []),
                             ValidationResult(False, []), self._evidence_list(case)),
                case)
        # P8 (client instruction 2026-10-07): a keeper case with no stronger
        # ground gets the default keeper appeal (KB-KEEPER-01) instead of no
        # letter. Only where the hold below would otherwise be the terminal
        # "no supported grounds": a question that could unlock a ground, or an
        # account not yet understood, is still put to the customer first.
        if not self.reasoning.leading_grounds(pack.module_ids or []) \
                and self._default_keeper_appeal_applies(case):
            plan, pack = self._with_default_keeper_appeal(case)
        if not (pack.module_ids or []):
            return self._hold_without_a_leading_ground(
                case, pack, "case analysis finalized with no selectable grounds")
        # Support-only grounds (landowner authority, the general keeper-liability
        # framing) can never lead the letter, so a pack of nothing else is not
        # drafted: it used to go out as a landowner-only letter. The customer is
        # told the truth instead - a detail is missing that would unlock a
        # ground, or nothing we can stand behind was found.
        if not self.reasoning.leading_grounds(pack.module_ids):
            return self._hold_without_a_leading_ground(
                case, pack, "support-only grounds; none can lead the letter")

        feedback: list[str] = []
        draft = result = dv = version = None
        widened = False
        attempt = 0
        section_retries = 0
        MAX_SECTION_RETRIES = 2
        quality_rewrites = 0
        # P6: what the drafter is given, as ids and a digest (no text).
        ctx_audit = DraftContext.from_pack(pack).audit()
        case.audit.append({"event": "draft_context", **ctx_audit})
        # P10.6: first-defective-layer trace for clean-upstream drafting.
        try:
            from .drafting.plan import build_draft_plan, coverage_trace
            dplan = build_draft_plan(pack, case_id=case.case_id)
            case.audit.append({
                "event": "draft_plan",
                "draft_plan_version": dplan.version,
                "sections": [s.section_id for s in dplan.sections],
                "leading_ground_ids": list(dplan.leading_ground_ids),
                "support_only_ids": list(dplan.support_only_ids),
            })
        except Exception as exc:
            dplan = None
            case.audit.append({"event": "draft_plan_error", "error": str(exc)})
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            try:
                draft = self.drafter.draft(case.case_id, pack, feedback, attempt)
            except Exception as exc:                     # LLM outage / bad JSON
                case.audit.append({
                    "event": "draft_error", "attempt": attempt, "error": str(exc),
                    "fallback": "none_substantive_template_disabled",
                })
                # Do not substitute TemplateDrafter substantive prose after AI failure.
                continue

            # Missing knowledge: widen once over the same LOCKED claims, then hold.
            # Widening retrieves more approved wording; the claims are the plan's.
            if draft.no_ground_reason:
                case.audit.append({"event": "no_ground", "attempt": attempt,
                                   "reason": draft.no_ground_reason, "widened": widened})
                if not widened:
                    widened = True
                    pack = self.reasoning.pack_for(case, plan, widen=True)
                    case.audit.append({"event": "draft_context", "widened": True,
                                       **DraftContext.from_pack(pack).audit()})
                    attempt -= 1                     # the retry is not an attempt
                    continue
                case.audit.append({
                    "event": "no_ground_after_widen",
                    "reason": draft.no_ground_reason,
                    "fallback": "none_substantive_template_disabled",
                })
                break

            late_blocks = self._with_late_appeal(case, draft, pack)
            if late_blocks:
                case.audit.append({"event": "late_appeal_wording", "attempt": attempt,
                                   "blocks": late_blocks})
            added_ids = self._with_identifiers(draft, pack)
            if added_ids:
                case.audit.append({"event": "identifiers_added", "attempt": attempt,
                                   "fields": added_ids})
            if self._with_recorded_times(draft, pack):
                case.audit.append({"event": "recorded_times_added", "attempt": attempt})
            repeats = _without_repeats(draft)
            if repeats:
                case.audit.append({"event": "repeated_sentences_removed", "attempt": attempt,
                                   "count": repeats})
            closing_blocks = self._with_closing(draft, pack)
            if closing_blocks:
                case.audit.append({"event": "closing_added", "attempt": attempt,
                                   "blocks": closing_blocks})
            case.state = CaseState.DRAFTED
            result, dv = self._validate(case, draft, pack)
            if dplan is not None:
                case.audit.append({
                    "event": "draft_coverage_trace",
                    "attempt": attempt,
                    "rows": coverage_trace(dplan, draft, pack),
                })
            case.audit.append({"event": "validation", "attempt": attempt, "passed": result.passed,
                               "issues": [i.rule for i in result.issues]})
            quality = None
            if result.passed:
                # The deterministic validators have decided what may be said. The
                # quality judge now reads how faithfully and specifically it was
                # said, and a REWRITE goes back over the SAME locked case.
                quality = self._quality_check(case, draft, pack, attempt)
                if (quality is not None and self.quality_enforcing
                        and quality["status"] == quality_judge.REWRITE):
                    if quality_rewrites < quality_judge.MAX_REWRITES:
                        quality_rewrites += 1
                        feedback = [_CLAIM_ID.sub("an unapproved claim", f)
                                    for f in quality.get("feedback") or []]
                        case.audit.append({"event": "quality_rewrite", "attempt": attempt,
                                           "rewrite": quality_rewrites,
                                           "findings": len(feedback)})
                        attempt -= 1        # a rewrite of the same case is not a new attempt
                        continue
                    if quality.get("blocking"):
                        # Hard findings survived every rewrite: the letter is held,
                        # not released, and the reason is recorded against it.
                        result = ValidationResult(False, list(result.issues) + [
                            ValidationIssue(
                                "VAL-QUALITY", "BLOCK",
                                "Quality review found: " + "; ".join(
                                    quality_judge.hard_findings(quality))[:600])])
                        version = self._record_version(case, plan, draft, result, dv, pack,
                                                       released=False, quality=quality)
                        case.state = CaseState.VALIDATION_FAILED
                        break
                    # Scores short of a threshold with no hard finding: rewritten
                    # as far as the budget allows, and released with the verdict
                    # on the record rather than withheld for style.
            if result.passed:
                hold = self._release_gate(case, result, pack, draft)
                if hold is not None:
                    self._record_version(case, plan, draft, hold.validation, dv, pack,
                                         released=False)
                    return hold
                version = self._record_version(case, plan, draft, result, dv, pack,
                                               released=True, quality=quality)
                case.state = CaseState.RELEASED
                return _with_outcome(
                    AppealOutput(case.state, render(draft), pack, draft, result,
                                 self._evidence_list(case)), case)
            version = self._record_version(case, plan, draft, result, dv, pack,
                                           released=False)
            case.state = CaseState.VALIDATION_FAILED
            # P10.6: regenerate only failed DraftSections before whole-letter retry.
            failed_sections = _failed_section_ids(result.issues, dplan, draft)
            if (failed_sections and section_retries < MAX_SECTION_RETRIES
                    and hasattr(self.drafter, "regenerate_sections")):
                section_retries += 1
                section_feedback = [
                    _CLAIM_ID.sub("an unapproved claim", f"{i.rule}: {i.message}")
                    for i in result.issues
                    if i.rule in ("VAL-GROUND-COVERAGE", "VAL-DRAFT-PARTICULARS",
                                  "VAL-COVERAGE", "VAL-PARTICULARS")
                ]
                try:
                    draft = self.drafter.regenerate_sections(
                        case.case_id, pack, draft, failed_sections,
                        section_feedback, attempt)
                    case.audit.append({
                        "event": "section_regeneration",
                        "attempt": attempt,
                        "section_ids": failed_sections,
                        "section_retry": section_retries,
                    })
                    attempt -= 1  # section regen does not consume a full-letter attempt
                    feedback = section_feedback
                    continue
                except Exception as exc:
                    case.audit.append({
                        "event": "section_regeneration_error",
                        "error": str(exc),
                        "section_ids": failed_sections,
                    })
            # P6: back to the drafter without internal ids - it is told what failed,
            # never which modules the plan holds or rejected.
            feedback = [_CLAIM_ID.sub("an unapproved claim",
                                        f"{i.rule}: {i.message} :: {i.sentence}")
                        for i in result.issues]

        # Every attempt was refused for something a specific sentence said. Drop
        # those sentences and check what is left: an unsupported point is meant
        # to be omitted, not to take the rest of a sound letter down with it.
        trimmed, dropped = self._without_failing_sentences(draft, result)
        if dropped:
            checked, dv = self._validate(case, trimmed, pack)
            case.audit.append({"event": "dropped_failing_sentences",
                               "dropped": dropped, "passed": checked.passed,
                               "issues": [i.rule for i in checked.issues]})
            if checked.passed:
                hold = self._release_gate(case, checked, pack, trimmed)
                if hold is not None:
                    self._record_version(case, plan, trimmed, hold.validation, dv, pack,
                                         released=False,
                                         parent=(version or {}).get("draft_id"))
                    return hold
                self._record_version(case, plan, trimmed, checked, dv, pack,
                                     released=True,
                                     parent=(version or {}).get("draft_id"))
                case.state = CaseState.RELEASED
                return _with_outcome(
                    AppealOutput(case.state, render(trimmed), pack, trimmed, checked,
                                 self._evidence_list(case)), case)
            self._record_version(case, plan, trimmed, checked, dv, pack,
                                 released=False, parent=(version or {}).get("draft_id"))

        if result is None:
            # Drafter never produced a usable letter — processing failure, not merits.
            case.state = CaseState.MANUAL_REVIEW
            result = ValidationResult(False, [ValidationIssue(
                "VAL-DRAFT", "BLOCK",
                "AI drafting failed or declined; substantive template fallback is disabled")])
        else:
            # Exhausted validation retries: keep VALIDATION_FAILED. Collapsing into
            # MANUAL_REVIEW made technical holds look like a merits judgment.
            case.state = CaseState.VALIDATION_FAILED
        return _with_outcome(
            AppealOutput(case.state, None, pack, draft, result, self._evidence_list(case)),
            case)

    def _release_gate(self, case: CaseFile, result: ValidationResult, pack, draft):
        """P11.1: block RELEASED when immutable release identity is incomplete."""
        from .release_trace import (
            OUTCOME_RELEASE_METADATA_INCOMPLETE,
            gate_before_release,
        )
        blocked = gate_before_release(case, self)
        if blocked is None:
            return None
        case.state = CaseState.MANUAL_REVIEW
        issues = list(getattr(result, "issues", None) or [])
        issues.append(ValidationIssue(
            "RELEASE_METADATA",
            "BLOCK",
            "Release blocked: incomplete release metadata",
        ))
        held = ValidationResult(False, issues)
        out = AppealOutput(case.state, None, pack, draft, held, self._evidence_list(case))
        # Customer copy stays processing-safe; internal reason is audit-only.
        case.audit.append({
            "event": "release_blocked",
            "reason": OUTCOME_RELEASE_METADATA_INCOMPLETE,
            "missing": blocked.get("missing"),
        })
        return _with_outcome(out, case)

    # ------------------------------------------------------------------ P6
    def _validate(self, case: CaseFile, draft: Draft, pack):
        """Sentence grounding and the draft checks (DV-*), then the VAL-* engine;
        one result. A sentence either maps to an approved claim, a fact and the
        evidence, or it is refused here and goes back to the drafter or is removed."""
        dv = self.draft_validation.check(draft, pack, {f.fact_id for f in case.facts.values()})
        case.audit.append({"event": "draft_validation", **dv.summary()})
        return merge_validation(self.validation.validate(draft, pack), dv), dv

    def _quality_check(self, case: CaseFile, draft: Draft, pack, attempt: int):
        """The quality judge's read of a draft that passed validation, recorded.

        None when the judge is off. An unreachable judge returns its ERROR review,
        which the caller treats as no verdict: the validators already decided what
        may be said, and the audit shows the quality read did not happen.
        """
        if self.quality is None:
            return None
        q = self.quality.review(draft, pack, case)
        case.audit.append({
            "event": "appeal_quality", "attempt": attempt, "status": q["status"],
            "scores": q.get("scores"),
            "sendable_for_any_pcn": q.get("sendable_for_any_pcn"),
            "omitted_grounds": q.get("omitted_grounds"),
            "hard_findings": {k: q.get(k) for k in quality_judge.HARD_FLAGS if q.get(k)},
            "summary": q.get("summary"), "error": q.get("error"),
            "blocking": bool(q.get("blocking")) and self.quality_enforcing,
            "enforcing": self.quality_enforcing})
        return q

    def _record_version(self, case: CaseFile, plan, draft: Draft, result, dv, pack,
                        released: bool, parent: Optional[str] = None,
                        quality: Optional[dict] = None) -> dict:
        """Store the draft as an immutable version tied to the claim plan. A draft
        that is going out is also read by the observing judges - the shadow judge
        and the appeal quality judge - whose verdicts are recorded and never
        acted on."""
        judge = None
        if released and self.shadow is not None:
            judge = self.shadow.review(draft, pack)
            case.audit.append({"event": "shadow_judge", "status": judge["status"],
                               "reasons": judge.get("reasons"), "blocking": False})
        if released and self.quality is not None and quality is None:
            # Not read on the way here (a trimmed or late release): read it now.
            q = self.quality.review(draft, pack, case)
            case.audit.append({"event": "appeal_quality", "status": q["status"],
                               "scores": q.get("scores"),
                               "sendable_for_any_pcn": q.get("sendable_for_any_pcn"),
                               "omitted_grounds": q.get("omitted_grounds"),
                               "weakened_customer_facts": q.get("weakened_customer_facts"),
                               "summary": q.get("summary"), "blocking": False})
        row = draft_versions.record(case, plan, draft, result, dv.grounding if dv else None,
                                    judge=judge, parent=parent, released=released)
        case.audit.append({"event": "draft_version", "draft_id": row["draft_id"],
                           "version": row["version"], "content_hash": row["content_hash"],
                           "claim_plan_id": row["claim_plan_id"],
                           "validation_status": row["validation_status"], "released": released})
        return row

    def _plan_trust(self) -> dict:
        """What a claim plan records about what produced it (client trust)."""
        from . import prompts, version
        from .manifest import provider_of
        llm = self.extraction.llm
        return {"code_version": version.commit(), "prompt_versions": prompts.versions(),
                "model_versions": dict(getattr(llm, "models", None) or {}),
                "provider": provider_of(llm)}

    # --------------------------------------------------------- recovery rungs
    def _analyse_until_a_ground_can_lead(self, case: CaseFile) -> RetrievalPack:
        """Re-analyse while nothing proposed can carry the letter.

        P5: this is the PROPOSAL phase. It may change case.analysis_module_ids
        (Case Intelligence's selection); the Claim Plan built after it decides.
        The pack it returns is only used to test for a leading ground.

        Case analysis reads the notice and the account afresh each round, and a
        round that came back with only the keeper-liability framing point is the
        commonest reason a perfectly ordinary case used to stop: the same facts
        re-analysed return the grounds that answer the allegation. So an unclear
        case is re-analysed rather than held.

        Questions a later round raises are recorded but not put to the customer -
        by this point they have finished answering or declined to, and going back
        to ask would loop. The facts stay absent, so any module they gate simply
        does not fire.
        """
        pack = self.reasoning.analyse(case, selected_ids=case.analysis_module_ids)
        for round_no in range(2, MAX_ANALYSIS_ROUNDS + 1):
            if self.reasoning.leading_grounds(pack.module_ids):
                return pack
            before = list(case.analysis_module_ids or [])
            asked, _shown = self._reanalyse(case, case.raw_answers.get("narrative", ""))
            now = list(case.analysis_module_ids or [])
            # Never silently wipe a finalized selection to empty — that maps to
            # a processing failure, not a merits judgment that "nothing stands up".
            if before and not now:
                case.analysis_module_ids = before
                case.audit.append({
                    "event": "ground_recovery_preserved",
                    "round": round_no, "was": before, "now": now,
                    "questions_not_put": [q.get("fact") for q in asked],
                })
                pack = self.reasoning.analyse(case, selected_ids=case.analysis_module_ids)
                break
            case.audit.append({"event": "ground_recovery", "round": round_no,
                               "was": before, "now": now,
                               "questions_not_put": [q.get("fact") for q in asked]})
            pack = self.reasoning.analyse(case, selected_ids=case.analysis_module_ids)
            if now == before:
                break              # the same answer twice; another round is waste
        return pack

    DEFAULT_KEEPER_MODULE = "KB-KEEPER-01"

    def _default_keeper_appeal_applies(self, case: CaseFile) -> bool:
        """True when the case would otherwise end with no letter and the keeper
        route is open. Mirrors _hold_without_a_leading_ground: every branch
        that asks the customer something or holds for processing keeps
        priority over the default letter."""
        from .disclosure import keeper_route_blocked
        from .semantics import understanding
        module = self.kg.modules.get(self.DEFAULT_KEEPER_MODULE)
        if module is None or module.status != "ACTIVE":
            return False
        if case.driver_status.value != "UNIDENTIFIED" or keeper_route_blocked(case):
            return False
        if case.get("notice_stage") == "DRIVER_LETTER":
            # Addressed to a person said to be the driver: a keeper appeal is
            # the wrong answer to it (client 2026-10-08; PP-DRIVER-001 is off).
            return False
        if analysis_failed(case):
            return False
        unlocks, q = self._postcode_materiality(case)
        if unlocks and q:
            return False
        if understanding.customer_stream_blocked(case):
            return False
        return True

    def _with_default_keeper_appeal(self, case: CaseFile):
        """Switch on KB-KEEPER-01 and re-decide the plan. The fact is a system
        decision (CALCULATION source), never a customer or document value."""
        from .models import Fact, FactSource, SourceKind
        case.put(Fact("F-default_keeper_appeal", "default_keeper_appeal", True,
                      FactStatus.DERIVED,
                      FactSource(SourceKind.CALCULATION, "default_keeper_appeal")))
        selected = list(case.analysis_module_ids or [])
        if self.DEFAULT_KEEPER_MODULE not in selected:
            selected.append(self.DEFAULT_KEEPER_MODULE)
        case.analysis_module_ids = selected
        case.audit.append({"event": "default_keeper_appeal",
                           "reason": "no ground that can lead the letter; keeper route open"})
        plan = self.claim_authority.decide(case, trust=self._plan_trust())
        return plan, self.reasoning.pack_for(case, plan)

    def _hold_without_a_leading_ground(self, case: CaseFile, pack, reason: str) -> AppealOutput:
        """No ground that can lead the letter: a detail is missing that would
        unlock one (the site postcode, `postcode_unlocks`), or nothing we can
        stand behind was found. Neither drafts a letter."""
        # Same predicate as the ask path, so the two cannot disagree.
        unlocks, q = self._postcode_materiality(case)
        if unlocks and q:
            # Missing unlock fact — not a merits no-grounds finding.
            case.state = CaseState.MANUAL_REVIEW
            case.pending_questions = [{"fact": "site_postcode", "type": q.get("type", "text"),
                                       "text": q["text"]}]
            case.audit.append({"event": "held_needs_site_postcode", "unlocks": unlocks,
                               "module_ids": list(pack.module_ids or [])})
        else:
            from .semantics import understanding
            blocked = understanding.customer_stream_blocked(case)
            pending = understanding.pending_question(case)
            if blocked == "CLARIFICATION_REQUIRED" and pending:
                # The account is not usable yet and the customer can still be
                # asked: nothing has been judged, so this is a request for detail.
                case.state = CaseState.MANUAL_REVIEW
                case.pending_questions = customer_safe.customer_questions(pending)
                case.audit.append({"event": "held_customer_clarification",
                                   "customer_semantics_not_ready": blocked,
                                   "module_ids": list(pack.module_ids or []),
                                   "questions": [q["fact"] for q in pending]})
            elif blocked and understanding.customer_stream_failure(case) == understanding.TECHNICAL:
                # No model reading of the account was made. We know nothing about
                # it - not that it is ambiguous, not that it fails to support a
                # ground - so this is a retryable processing hold. The account is
                # kept; continuing the case reads it again.
                case.state = CaseState.MANUAL_REVIEW
                case.audit.append({
                    "event": "held_semantic_processing",
                    "customer_semantics_not_ready": blocked,
                    "cause": understanding.technical_cause(understanding.load_packet(case)),
                    "module_ids": list(pack.module_ids or []), "reason": reason})
            elif blocked:
                # Nothing independent of the account supported a ground, and the
                # account could not be understood well enough to weigh. That is
                # not "no supported grounds": the account is kept, unjudged.
                case.state = CaseState.MANUAL_REVIEW
                case.audit.append({"event": "held_account_unresolved",
                                   "customer_semantics_not_ready": blocked,
                                   "module_ids": list(pack.module_ids or []),
                                   "reason": reason})
            else:
                # Authoritative no-grounds terminal: state + outcome agree. The
                # account (if any) was understood, and everything was weighed.
                case.state = CaseState.NO_SUPPORTED_GROUNDS
                case.audit.append({"event": "analysis_complete_no_supported_grounds",
                                   "module_ids": list(pack.module_ids or []), "reason": reason})
        return _with_outcome(
            AppealOutput(case.state, None, pack, Draft(case.case_id, []),
                         ValidationResult(False, []), self._evidence_list(case)),
            case)

    LATE_APPEAL_NOTICE = {
        "code": "APPEAL_PERIOD_MAY_HAVE_EXPIRED",
        "title": "The normal appeal period may have passed",
        "message": ("The notice gives a set period to appeal, and it looks as if that "
                    "period has passed. Your appeal has still been prepared and the letter "
                    "asks the operator to consider it, but the operator may say it was made "
                    "too late. Send it as soon as you can."),
    }

    def _with_late_appeal(self, case: CaseFile, draft, pack) -> list[str]:
        """P8: when the normal appeal period has expired, say so in the letter
        (PP-LATE-001, after the opening paragraph). Never stops the appeal."""
        if not case.get("appeal_period_expired") or not draft.paragraphs:
            return []
        # Client 2026-10-08: when only a reminder is held and the original
        # deadline cannot be established, the appeal MAY be late (PP-LATE-002).
        bid = "PP-LATE-002" if case.get("appeal_period_status") == "MAY_HAVE_EXPIRED" \
            else "PP-LATE-001"
        blk = self.kg.blocks.get(bid)
        if blk is None or blk.status != "ACTIVE":
            return []
        body = _said(blk.letter_text)
        if body and body in _said(draft.plain_text()):
            return []
        sentences = self.fallback._sentences(blk.letter_text, pack, "STRUCTURAL")
        if not sentences:
            return []
        draft.paragraphs.insert(1 if len(draft.paragraphs) > 1 else len(draft.paragraphs),
                                sentences)
        return [bid]

    @staticmethod
    def _with_identifiers(draft, pack) -> list[str]:
        """Live: a letter that never named the PCN or the registration failed
        the identity check and ended as a processing error. The opening
        paragraph states them from the verified facts when the drafter did not."""
        from .engines.validation import _contains_token, _ident
        from .models import DraftSentence
        facts = pack.verified_facts or {}
        if not draft.paragraphs:
            return []
        text = draft.plain_text()
        added, parts = [], []
        pcn, vrm = facts.get("pcn_number"), facts.get("vrm")
        if pcn and not _contains_token(text, _ident(pcn)):
            parts.append((f"This appeal concerns Parking Charge Notice {pcn}.", "pcn_number"))
        if vrm and not _contains_token(text, _ident(vrm)):
            parts.append((f"The vehicle concerned is registration {vrm}.", "vrm"))
        for sentence, name in parts:
            refs = [pack.fact_refs[name]] if name in pack.fact_refs else []
            draft.paragraphs[0].append(DraftSentence(sentence, refs, ["STRUCTURAL"], []))
            added.append(name)
        return added

    @staticmethod
    def _with_recorded_times(draft, pack) -> bool:
        """Letter quality: state the operator's own recorded entry and exit
        times when the letter does not already. They are document facts, read
        off the notice; the sentence attributes them to the operator's records
        and says nothing about who was driving."""
        facts = pack.verified_facts or {}
        entry, exit_ = facts.get("entry_time"), facts.get("exit_time")
        if not entry or not exit_ or not draft.paragraphs:
            return False
        text = draft.plain_text()
        if str(entry) in text and str(exit_) in text:
            return False
        from .models import DraftSentence
        refs = [pack.fact_refs[k] for k in ("entry_time", "exit_time") if k in pack.fact_refs]
        sentence = DraftSentence(
            f"The operator's records show the vehicle entering at {entry} and leaving at {exit_}.",
            refs, ["STRUCTURAL"], [])
        for i, para in enumerate(draft.paragraphs):
            if any("alleges" in s.text for s in para):
                para.append(sentence)
                return True
        draft.paragraphs.insert(1 if len(draft.paragraphs) > 1 else len(draft.paragraphs),
                                [sentence])
        return True

    def customer_notices(self, case: CaseFile) -> list[dict]:
        out = []
        if case.get("appeal_period_expired"):
            out.append(self.late_notice(case))
        return out

    @classmethod
    def late_notice(cls, case: CaseFile) -> dict:
        notice = dict(cls.LATE_APPEAL_NOTICE)
        if case.get("appeal_period_status") == "MAY_HAVE_EXPIRED":
            notice["message"] = (
                "This letter refers to an earlier notice, and we could not find the date of "
                "that first notice, so the normal appeal period may already have passed. "
                "Your appeal has still been prepared, but the operator may say it was made "
                "too late. Send it as soon as you can.")
        return notice

    def _with_closing(self, draft, pack) -> list[str]:
        """Ensure the letter ends with an approved conclusion, not a bare ground.

        - When Schedule 4 transfer has failed (verified PoFA findings), append
          PP-POFA-006 / PP-POFA-007 if the letter never states the Schedule 4
          conclusion — the live shape clients expect (keeper liability fails;
          driver unidentified; cancel).
        - Otherwise, when there is no cancel request at all, append PP-END-001
          / PP-END-002 as before.
        - Always add PP-END-002 when a PoFA conclusion was added and a clear
          response request is still missing.

        Returns the block ids appended (empty if nothing changed).
        """
        if not draft.paragraphs:
            return []
        text = draft.plain_text()
        said = _said(text)
        closing: list = []
        used: list[str] = []
        pofa_failed = bool(getattr(pack, "pofa_findings", None))
        has_pofa_close = _POFA_CONCLUSION.search(text)

        def _append(bid: str) -> bool:
            blk = self.kg.blocks.get(bid)
            if blk is None or blk.status != "ACTIVE":
                return False
            sentences = self.fallback._sentences(blk.letter_text, pack, "STRUCTURAL")
            # A block the letter already contains is not added a second time.
            # `has_pofa_close` asks whether the letter reaches the Schedule 4
            # conclusion in ONE particular wording; a letter that reaches it in
            # another ("liability cannot be transferred to the registered keeper
            # under Schedule 4") used to have the block appended on top of
            # itself, word for word. The test is the block against the letter,
            # so it holds for every block and needs no phrase added to any list.
            body = _said(" ".join(s.text for s in sentences))
            if body and body in said:
                return True
            closing.extend(sentences)
            used.append(bid)
            return True

        # PP-POFA-006 says the Schedule 4 requirements "have also not been
        # satisfied" - true of a timing or content failure, wrong for land that
        # is outside Schedule 4 altogether (KB-POFA-07 states its own conclusion).
        outside_sch4 = set(getattr(pack, "pofa_findings", None) or []) == {"POFA_NOT_RELEVANT_LAND"}
        closing_ids = ("PP-POFA-007",) if outside_sch4 else ("PP-POFA-006", "PP-POFA-007")
        if pofa_failed and not has_pofa_close:
            for bid in closing_ids:
                if not _append(bid):
                    return []
            if "clear response addressing" not in text.lower():
                _append("PP-END-002")
        elif not _CANCEL_REQUEST.search(text):
            for bid in ("PP-END-001", "PP-END-002"):
                if not _append(bid):
                    return []
        else:
            return []

        if not closing:
            return []
        # The letter ends with its closing. When the drafter has already written
        # one, a statutory conclusion appended after it left the letter asking
        # for cancellation and then carrying on, so the conclusion goes before
        # that last paragraph and the request to cancel stays last.
        last = " ".join(s.text for s in draft.paragraphs[-1])
        if len(draft.paragraphs) > 1 and _CANCEL_REQUEST.search(last):
            draft.paragraphs.insert(len(draft.paragraphs) - 1, closing)
            # Letter quality: the statutory conclusion (PP-POFA-007, "For the
            # reasons set out above, ...") now sits right before a closing that
            # opens with the same words. Say it once: the closing keeps its
            # request to cancel without repeating the opener.
            head = _GENERIC_CLOSE.match(last)
            if head and _GENERIC_CLOSE.match(closing[0].text):
                first = draft.paragraphs[-1][0]
                rest = first.text[head.end():].lstrip(" ,")
                if rest:
                    first.text = rest[0].upper() + rest[1:]
                    used.append("closing_opener_deduplicated")
        else:
            draft.paragraphs.append(closing)
        return used

    @staticmethod
    def _without_failing_sentences(draft: Optional[Draft],
                                   result: Optional[ValidationResult]):
        """The draft minus the sentences validation blocked.

        Only usable when every blocking issue names a sentence. A document-level
        refusal - a missing PCN number, no substantive ground at all - is about
        what the letter does not say, and dropping sentences cannot answer it.
        """
        if draft is None or result is None:
            return draft, []
        blocking = [i for i in result.issues if i.severity == "BLOCK"]
        if not blocking or any(not i.sentence for i in blocking):
            return draft, []
        bad = {i.sentence for i in blocking}
        paragraphs = [[s for s in p if s.text not in bad] for p in draft.paragraphs]
        kept = [p for p in paragraphs if p]
        dropped = sorted(bad)
        # Same letter, fewer sentences: it keeps the provenance of the draft it
        # came from, or a trimmed release records no model and no prompt version.
        return Draft(draft.case_id, kept, draft.attempt,
                     model=draft.model, prompt_version=draft.prompt_version,
                     section_ownership=draft.section_ownership), dropped

    @staticmethod
    def _evidence_list(case: CaseFile) -> list[str]:
        return [f"{e.kind}: {e.filename}" for e in case.evidence.values()
                if e.uploaded and e.kind not in ("PCN", "NTK", "NTD")]


def _failed_section_ids(issues, draft_plan, draft) -> list[str]:
    """Map coverage / particulars failures to DraftPlan section_ids (P10.6)."""
    if draft_plan is None:
        return []
    rules = {"VAL-GROUND-COVERAGE", "VAL-DRAFT-PARTICULARS", "VAL-COVERAGE",
             "VAL-PARTICULARS"}
    hit = [i for i in (issues or []) if getattr(i, "rule", None) in rules]
    if not hit:
        return []
    ids: list[str] = []
    for issue in hit:
        msg = getattr(issue, "message", "") or ""
        m = re.search(r"section_id=([A-Za-z0-9_-]+)", msg)
        if m and m.group(1) not in ids and m.group(1) != "?":
            ids.append(m.group(1))
            continue
        m = re.search(r"DraftSection\s+([A-Za-z0-9_-]+)", msg)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
            continue
        for mid in re.findall(r"\bKB-[A-Z]+(?:-[A-Z0-9]+)+\b", msg):
            for sec in draft_plan.sections:
                if mid in sec.ground_ids and sec.section_id not in ids:
                    ids.append(sec.section_id)
    if not ids:
        # Ownership map fallback: any section whose grounds lack letter text
        ownership = (draft.section_ownership if draft else None) or {}
        letter = (draft.plain_text() if draft else "") or ""
        for sec in draft_plan.sections:
            owned = ownership.get(sec.section_id) or {}
            grounds = owned.get("ground_ids") or sec.ground_ids
            if not any(g.lower() in letter.lower() for g in grounds):
                if sec.section_id not in ids:
                    ids.append(sec.section_id)
    return ids


# A request to cancel, not any mention of cancelling ("not an automatic
# cancellation ground" asks for nothing).
_CANCEL_REQUEST = re.compile(r"\b(request\w*|ask\w*|should|please|invited?)\b[^.]{0,80}\bcancel", re.I)
_POFA_CONCLUSION = re.compile(r"keeper liability under Schedule 4", re.I)
_GENERIC_CLOSE = re.compile(r"\s*(For (all )?the reasons (set out|given) above|In (the )?light of the above|"
                            r"Accordingly)\b", re.I)


def _without_repeats(draft) -> int:
    """Drop a sentence the letter has already said word for word. Returns the
    number removed. Empty paragraphs are removed with them."""
    seen: set[str] = set()
    removed = 0
    paragraphs = []
    for para in draft.paragraphs:
        kept = []
        for sent in para:
            key = _said(sent.text)
            if key and key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(sent)
        if kept:
            paragraphs.append(kept)
    if removed:
        draft.paragraphs = paragraphs
    return removed


def _said(text: str) -> str:
    """Letter text reduced to words, for asking whether a block is already in it."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

# Module ids, which feedback to the drafter never carries (rule names may stay).
_CLAIM_ID = re.compile(r"\bKB-[A-Z]+(?:-[A-Z0-9]+)+\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def uk_dates(text: str) -> str:
    """ISO dates as a UK letter writes them: 2026-09-19 -> 19 September 2026.
    Facts carry dates as date objects, which reach the drafter as ISO strings."""
    import calendar

    def fmt(m: re.Match) -> str:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return m.group(0)
        return f"{d} {calendar.month_name[mo]} {y}"
    return _ISO_DATE.sub(fmt, text or "")


def render(draft: Draft) -> str:
    """Strip provenance and return the customer-facing letter body.
    Production: Jinja2 -> HTML -> WeasyPrint PDF, plus evidence list page."""
    return uk_dates(draft.plain_text())
