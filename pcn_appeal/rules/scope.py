"""Document routing: whether this service may act on an upload at all.

This runs on the classifier's labels before case intelligence, fact recovery,
question generation or grounds analysis, so a document we cannot appeal never
reaches them and never produces grounds, questions or a letter.

The model labels; this table decides. One definition on purpose - the scope
check was previously three separate `case.get("debt_recovery_stage")` tests, so
adding a second out-of-scope type meant remembering all three sites.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..models import CaseFile

# The only labels that mean "a private parking charge still at the appeal stage".
NOTICE_KINDS = frozenset({"PCN", "NTK", "NTD"})

# EX-13. The backup debt check: the operator's own wording on the uploaded
# papers, never the customer's story. It lives here rather than in extraction
# because it is the second half of one decision - the classifier's label and
# this check are both inputs to `decide`, and splitting them let a debt letter
# the model labelled OTHER through the safety valve below.
DEBT_SIGNALS = re.compile(
    r"\b(debt\s*recovery|passed\s+to\s+(?:a\s+)?debt|"
    r"letter\s+of\s+claim|claim\s+form|"
    # No `civil enforcement`: it is the name of a private parking operator, and
    # a company name says nothing about what stage a charge is at.
    r"enforcement\s+agent|\bbailiffs?\b|"
    r"right\s+to\s+appeal\s+(?:has\s+)?(?:now\s+)?(?:expired|ended|lapsed|closed)|"
    r"appeal\s+(?:window|period|right)\s+(?:has\s+)?(?:now\s+)?(?:closed|expired|ended))\b",
    re.I)


def has_debt_signals(case: CaseFile) -> bool:
    """Whether any uploaded document says in the operator's words that this
    charge has left the appeal stage."""
    return any(DEBT_SIGNALS.search(ev.text or "") for ev in case.evidence.values())

# Any of these on any uploaded document closes the ordinary appeal route. Ordered
# most-specific-first: a case holding both a claim form and the original notice is
# past the appeal stage, so the more advanced document wins.
STOP_ORDER = ("COURT_CLAIM", "DEBT_RECOVERY", "OUT_OF_STAGE", "COUNCIL_PCN")

# A notice the classifier mislabelled must not strand the customer, so a document
# carrying a charge's identifying fields is treated as a notice regardless of its
# label. Deliberately asymmetric: this can only open the appeal route, never close it.
NOTICE_SHAPED_FACTS = ("pcn_number", "charge_amount", "alleged_breach")


@dataclass(frozen=True)
class ScopeStop:
    """A refusal to appeal, and what to offer the customer instead.

    `cta_action` is a stable key rather than a URL: the frontend owns its own
    routes, and a URL invented here would rot the moment they change.
    """
    code: str
    message: str
    recommendation: str
    cta_label: Optional[str] = None
    cta_action: Optional[str] = None
    # Heading for the stop screen. None lets the frontend use its generic one.
    title: Optional[str] = None


STOPS: dict[str, ScopeStop] = {
    "DEBT_RECOVERY": ScopeStop(
        code="DEBT_RECOVERY",
        title="Debt recovery document identified",
        message=("You have uploaded a debt recovery letter. Our appeal service does "
                 "not currently support cases at this stage."),
        recommendation=("You may wish to use our free debt recovery response "
                        "template."),
        cta_label="Get the free debt recovery template",
        cta_action="DEBT_RECOVERY_TEMPLATE"),
    "COUNCIL_PCN": ScopeStop(
        code="COUNCIL_PCN",
        message=("This is a Penalty Charge Notice issued by a council or local "
                 "authority, not a private parking charge. Council notices "
                 "follow a different statutory process, so we're unable to "
                 "generate a private parking appeal for this one."),
        recommendation=("Our Council PCN Appeal service handles these notices "
                        "and is the right route for this charge."),
        cta_label="Go to Council PCN Appeals",
        cta_action="COUNCIL_PCN_SERVICE"),
    "COURT_CLAIM": ScopeStop(
        code="COURT_CLAIM",
        message=("This document is part of a court claim rather than a parking "
                 "charge at the appeal stage, so we're unable to generate an "
                 "appeal for it. Court deadlines are short and missing one can "
                 "result in a judgment against you."),
        recommendation=("Please take independent legal advice promptly, and do "
                        "not ignore any date given on the document."),
        cta_label="Read about responding to a claim",
        cta_action="COURT_CLAIM_GUIDANCE"),
    "OUT_OF_STAGE": ScopeStop(
        code="OUT_OF_STAGE",
        message=("This notice says the time to appeal or challenge it has "
                 "already passed, so we're unable to generate an appeal for it."),
        recommendation=("If you believe the deadline is wrong, or you never "
                        "received the original notice, our Resources section "
                        "explains what you can still do."),
        cta_label="See what you can still do",
        cta_action="OUT_OF_STAGE_GUIDANCE"),
    "UNSUPPORTED": ScopeStop(
        code="UNSUPPORTED",
        message=("We couldn't read this as a private parking notice. It may be a "
                 "different kind of document, or the photo may be too unclear "
                 "for us to identify the charge."),
        recommendation=("Please upload a clear photo or PDF of the parking "
                        "charge notice itself, showing the whole page."),
        cta_label="Upload the notice again",
        cta_action="RETRY_UPLOAD"),
    "CLASSIFICATION_FAILED": ScopeStop(
        code="CLASSIFICATION_FAILED",
        message=("Something went wrong at our end while reading this document, "
                 "so we haven't been able to check what kind of notice it is. "
                 "Nothing is wrong with your upload."),
        recommendation=("Please try again in a few minutes. If it happens again, "
                        "contact us and we'll look at it for you."),
        cta_label="Try again",
        cta_action="RETRY_UPLOAD"),
}

# The stop that means "our classifier did not answer", as distinct from every
# other stop, which means "we read the document and cannot appeal it".
TECHNICAL_STOPS = frozenset({"CLASSIFICATION_FAILED"})


def decide(case: CaseFile) -> Optional[ScopeStop]:
    """The stop that applies to this case, or None to proceed to the appeal path.

    No labels at all is a technical failure of our own classifier, not a verdict
    on the document. It must never proceed: a case that skips this gate skips the
    only check that keeps a debt-recovery letter or a council PCN out of the
    appeal path, so "we could not read it" has to stop as loudly as a refusal.
    """
    # The intake router has already decided this case (pcn_appeal/intake). A
    # case it sent elsewhere, or a private-parking stage that service refuses,
    # stops here too, so no private-parking engine can run on it even when
    # called directly. A case the router never saw (route None) falls through
    # to the private-parking checks below unchanged.
    if case.route is not None:
        from ..services import intake_stop
        stop = intake_stop(case)
        if stop is not None:
            return stop

    if not case.document_classes:
        return STOPS["CLASSIFICATION_FAILED"]

    labels = set(case.document_classes.values())
    for code in STOP_ORDER:
        if code in labels:
            return STOPS[code]

    if labels & NOTICE_KINDS:
        return None

    # Safety valve: a notice the classifier mislabelled must not strand the
    # customer, so a document carrying a charge's identifying fields opens the
    # appeal route regardless of its label. It is conditioned on the backup debt
    # check because a debt-recovery letter carries those same fields - it quotes
    # the PCN number and the amount - so on its own the valve was a hole in the
    # gate exactly where the gate matters most.
    if any(case.has(name) for name in NOTICE_SHAPED_FACTS):
        if has_debt_signals(case):
            return STOPS["DEBT_RECOVERY"]
        return None
    return STOPS["UNSUPPORTED"]
