"""Orchestrator - the only component that moves a case between states.

    CREATED -> EXTRACTED -> CONFIRMED -> QUESTIONING -> ANALYSED -> DRAFTED
            -> (VALIDATION_FAILED -> regenerate x N) -> RELEASED | MANUAL_REVIEW

Production: run each step as a durable workflow activity (Temporal, or Celery
+ Postgres state table). The customer-facing steps (confirm, answer) are
signals/API calls that resume the workflow; LLM steps are retried idempotently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .drafting.drafter import LLMDrafter, TemplateDrafter
from .engines.account import assess_material_account
from .engines.analysis import AnalysisEngine
from .engines.claim_plan_authority import ClaimPlanBuilder
from .integrity import ai_log
from .drafting import shadow_judge, versions as draft_versions
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


def _with_outcome(out: AppealOutput, case: CaseFile) -> AppealOutput:
    """Attach a customer outcome when the letter was not released, and close
    the run: this outcome is the run's, and the next step starts a new one."""
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
                 shadow_judge_enabled: Optional[bool] = None):
        self.kg = kg or KnowledgeGraph()
        # P5.5: every model call is logged against the case it serves
        # (integrity/ai_log.py) - task, model, prompt version, digests, timing.
        llm = ai_log.audited(llm)
        if judge is not None:
            judge = ai_log.audited(judge)
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
        # P5: the only authority over which claims a letter argues. Analysis,
        # reassessment and ground recovery propose; this decides and locks.
        self.claim_authority = ClaimPlanBuilder(self.kg, self.reasoning)

    # step 1-2
    def ingest(self, case: CaseFile) -> list[str]:
        case.ensure_run("ingest")
        return self.extraction.run(case)

    def confirm(self, case: CaseFile, corrections: dict, confirmed: list[str], narrative: str) -> list[dict]:
        case.ensure_run("confirm")
        self.extraction.confirm(case, corrections, confirmed)
        # Store narrative before any scope stop so free-text provenance survives
        # out-of-scope routing. Does not change disclosure status.
        case.raw_answers["narrative"] = narrative
        if self._apply_scope_stop(case):
            return []
        self.reasoning.enrich(case)
        return self._reanalyse(case, narrative)

    # step 3 (called per answer batch; returns follow-ups or [] when done)
    def answer(self, case: CaseFile, answers: dict) -> list[dict]:
        case.ensure_run("answer")
        for fact, raw in answers.items():
            self.questions.record_answer(case, fact, raw)
        return self._reanalyse(case, case.raw_answers.get("narrative", ""))

    # ------------------------------------------------------------ V2 analysis
    def _reanalyse(self, case: CaseFile, narrative: str) -> list[dict]:
        """Re-run case analysis against everything now known, and return only the
        questions it still genuinely needs.

        Called after confirmation and again after every answer, because an answer
        changes what is material: it can settle a ground, open one, or make a
        question that looked necessary pointless.
        """
        assess_material_account(case)
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
        conflict = [dict(q, source=CONFLICT) for q in self._pcn_conflict_question(case)]
        confirm = [dict(q, source=CONFIRMATION) for q in FactManager.confirmation_questions(case)]
        hypothesis = [dict(q, source=HYPOTHESIS)
                      for q in Hypotheses.questions(case, self._could_change_a_ground)]
        candidates = conflict + confirm + hypothesis + list(analysis.questions)
        candidates += self._site_postcode_question(case, analysis.module_ids, candidates)
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
        # next round. Mark as asked when presented; record_answer is idempotent.
        for q in review.shown:
            fact = q.get("fact")
            if fact and fact not in case.asked_questions:
                case.asked_questions.append(fact)
            if q.get("hypothesis_id"):
                Hypotheses.mark_asked(case, q["hypothesis_id"], q["text"])
        case.audit.append({"event": "analysis_round", "grounds": analysis.module_ids,
                           "asking": [q["fact"] for q in analysis.questions],
                           "approved_waiting": [q["fact"] for q in review.approved[1:]]})
        return analysis.questions

    def _could_change_a_ground(self, fact: str) -> bool:
        """Material: some in-force KB module is gated on or requires the fact."""
        return any(fact in self.kg.gating_facts(m.module_id) or fact in (m.required_facts or [])
                   for m in self.kg.active_modules())

    def _site_postcode_question(self, case: CaseFile, module_ids, already: list[dict]) -> list[dict]:
        """The site postcode, asked only when nothing selected can lead the
        letter and knowing the site is in England & Wales would unlock a
        leading Schedule 4 ground (`postcode_unlocks`). Otherwise an unreadable
        postcode silently withheld PoFA and the letter fell back to landowner
        authority alone. Asked once; never the keeper's address."""
        if "site_postcode" in case.asked_questions or any(q.get("fact") == "site_postcode" for q in already):
            return []
        if self.reasoning.leading_grounds(module_ids):
            return []
        unlocks = postcode_unlocks(case, self.kg)
        q = self.kg.question_for("site_postcode")
        if not unlocks or not q:
            return []
        case.audit.append({"event": "site_postcode_material", "unlocks": unlocks})
        # `source` and `unlocks` are for the Question Authority; the customer
        # gets only fact/text/type (customer_safe.customer_question).
        return [{"fact": "site_postcode", "type": q.get("type", "text"), "text": q["text"],
                 "source": POSTCODE, "unlocks": unlocks}]

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
        """Case analysis with the deterministic inputs it must respect.

        Order: recover from documents/calculators → applicability → LLM analysis.
        Questions are only proposed after recovery has exhausted automatic sources.
        """
        self.recovery.recover(case)
        version, pofa_res = self.reasoning.applicability(case)
        return self.analysis.analyse(case, narrative, pofa=pofa_res,
                                     code_version=getattr(version, "version_id", None))

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
        case.ensure_run("auto_appeal")
        if case.state == CaseState.CREATED:
            flags = self.ingest(case)
            stopped = self._stop_if_no_appeal_right(case, flags)
            if stopped:
                return stopped
            from .notice_completeness import requires_complete_notice
            if requires_complete_notice(case):
                if narrative:
                    case.raw_answers["narrative"] = narrative
                case.audit.append({"event": "blocked_notice_sides_incomplete",
                                   "stage": "auto_appeal"})
                case.state = CaseState.EXTRACTED
                return AutoAppealResult(
                    case.case_id, case.state,
                    [{
                        "fact": "notice_reverse_pages",
                        "text": (
                            "Please upload the reverse (and any continuation pages) of the "
                            "parking notice, or a multipage PDF of the whole notice. "
                            "Two copies of the front are not enough."
                        ),
                        "type": "text",
                    }],
                    None, flags + ["notice_sides_incomplete"], [],
                )
            questions = self.confirm(case, {}, self._auto_confirmable(case), narrative)
        else:
            stopped = self._stop_if_no_appeal_right(case, flags)
            if stopped:
                return stopped
            from .notice_completeness import requires_complete_notice
            if requires_complete_notice(case):
                case.audit.append({"event": "blocked_notice_sides_incomplete",
                                   "stage": "auto_appeal_continue"})
                return AutoAppealResult(
                    case.case_id, case.state,
                    [{
                        "fact": "notice_reverse_pages",
                        "text": (
                            "Please upload the reverse (and any continuation pages) of the "
                            "parking notice, or a multipage PDF of the whole notice."
                        ),
                        "type": "text",
                    }],
                    None, flags + ["notice_sides_incomplete"], [],
                )
            questions = self._reanalyse(case, case.raw_answers.get("narrative", ""))
        if case.state in (CaseState.NO_APPEAL_RIGHT, CaseState.CLASSIFICATION_FAILED):
            return self._stop_if_no_appeal_right(case, flags)  # type: ignore[return-value]
        if answers:
            questions = self.answer(case, answers)

        blocking = [] if skip_remaining else questions
        skipped = [q["fact"] for q in questions if q not in blocking]
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
        model was unsure of is exactly how a fabricated defect gets into a letter."""
        return [n for n, f in case.facts.items() if f.status == FactStatus.EXTRACTED]


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
        # P6: what the drafter is given, as ids and a digest (no text).
        case.audit.append({"event": "draft_context", **DraftContext.from_pack(pack).audit()})
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

            closing_blocks = self._with_closing(draft, pack)
            if closing_blocks:
                case.audit.append({"event": "closing_added", "attempt": attempt,
                                   "blocks": closing_blocks})
            case.state = CaseState.DRAFTED
            result, dv = self._validate(case, draft, pack)
            case.audit.append({"event": "validation", "attempt": attempt, "passed": result.passed,
                               "issues": [i.rule for i in result.issues]})
            version = self._record_version(case, plan, draft, result, dv, pack,
                                           released=result.passed)
            if result.passed:
                case.state = CaseState.RELEASED
                return _with_outcome(
                    AppealOutput(case.state, render(draft), pack, draft, result,
                                 self._evidence_list(case)), case)
            case.state = CaseState.VALIDATION_FAILED
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
            self._record_version(case, plan, trimmed, checked, dv, pack,
                                 released=checked.passed, parent=(version or {}).get("draft_id"))
            if checked.passed:
                case.state = CaseState.RELEASED
                return _with_outcome(
                    AppealOutput(case.state, render(trimmed), pack, trimmed, checked,
                                 self._evidence_list(case)), case)

        case.state = CaseState.MANUAL_REVIEW
        if result is None:
            result = ValidationResult(False, [ValidationIssue(
                "VAL-DRAFT", "BLOCK",
                "AI drafting failed or declined; substantive template fallback is disabled")])
        return _with_outcome(
            AppealOutput(case.state, None, pack, draft, result, self._evidence_list(case)),
            case)

    # ------------------------------------------------------------------ P6
    def _validate(self, case: CaseFile, draft: Draft, pack):
        """Sentence grounding and the draft checks (DV-*), then the VAL-* engine;
        one result. A sentence either maps to an approved claim, a fact and the
        evidence, or it is refused here and goes back to the drafter or is removed."""
        dv = self.draft_validation.check(draft, pack, {f.fact_id for f in case.facts.values()})
        case.audit.append({"event": "draft_validation", **dv.summary()})
        return merge_validation(self.validation.validate(draft, pack), dv), dv

    def _record_version(self, case: CaseFile, plan, draft: Draft, result, dv, pack,
                        released: bool, parent: Optional[str] = None) -> dict:
        """Store the draft as an immutable version tied to the claim plan; a draft
        that is going out is also read by the shadow judge, whose verdict is
        recorded and never acted on."""
        judge = None
        if released and self.shadow is not None:
            judge = self.shadow.review(draft, pack)
            case.audit.append({"event": "shadow_judge", "status": judge["status"],
                               "reasons": judge.get("reasons"), "blocking": False})
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
            asked = self._reanalyse(case, case.raw_answers.get("narrative", ""))
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

    def _hold_without_a_leading_ground(self, case: CaseFile, pack, reason: str) -> AppealOutput:
        """No ground that can lead the letter: a detail is missing that would
        unlock one (the site postcode, `postcode_unlocks`), or nothing we can
        stand behind was found. Neither drafts a letter."""
        case.state = CaseState.MANUAL_REVIEW
        unlocks = postcode_unlocks(case, self.kg)
        q = self.kg.question_for("site_postcode")
        if unlocks and q:
            case.pending_questions = [{"fact": "site_postcode", "type": q.get("type", "text"),
                                       "text": q["text"]}]
            case.audit.append({"event": "held_needs_site_postcode", "unlocks": unlocks,
                               "module_ids": list(pack.module_ids or [])})
        else:
            case.audit.append({"event": "analysis_complete_no_supported_grounds",
                               "module_ids": list(pack.module_ids or []), "reason": reason})
        return _with_outcome(
            AppealOutput(case.state, None, pack, Draft(case.case_id, []),
                         ValidationResult(False, []), self._evidence_list(case)),
            case)

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
        closing: list = []
        used: list[str] = []
        pofa_failed = bool(getattr(pack, "pofa_findings", None))
        has_pofa_close = _POFA_CONCLUSION.search(text)

        def _append(bid: str) -> bool:
            blk = self.kg.blocks.get(bid)
            if blk is None or blk.status != "ACTIVE":
                return False
            closing.extend(self.fallback._sentences(blk.letter_text, pack, "STRUCTURAL"))
            used.append(bid)
            return True

        if pofa_failed and not has_pofa_close:
            for bid in ("PP-POFA-006", "PP-POFA-007"):
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
                     model=draft.model, prompt_version=draft.prompt_version), dropped

    @staticmethod
    def _evidence_list(case: CaseFile) -> list[str]:
        return [f"{e.kind}: {e.filename}" for e in case.evidence.values()
                if e.uploaded and e.kind not in ("PCN", "NTK", "NTD")]


# A request to cancel, not any mention of cancelling ("not an automatic
# cancellation ground" asks for nothing).
_CANCEL_REQUEST = re.compile(r"\b(request\w*|ask\w*|should|please|invited?)\b[^.]{0,80}\bcancel", re.I)
_POFA_CONCLUSION = re.compile(r"keeper liability under Schedule 4", re.I)
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
