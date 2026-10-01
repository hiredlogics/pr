"""Claims: a letter before claim or an issued county court claim. No engine yet."""
from ...rules.scope import ScopeStop
from ..base import RedirectService

ROUTE = "CLAIMS"

# DRAFT customer wording - for client approval. Same guidance action as the
# private pipeline's COURT_CLAIM stop, so the frontend link is unchanged.
STOP = ScopeStop(
    code="CLAIMS",
    title="Court claim or letter before claim identified",
    message=("This is a letter before claim or a court claim rather than a notice at "
             "the appeal stage, so we're unable to generate an appeal for it. These "
             "documents have strict deadlines, and missing one can lead to a judgment "
             "against you."),
    recommendation=("Please take independent legal advice promptly, and do not "
                    "ignore any date given on the document."),
    cta_label="Read about responding to a claim",
    cta_action="COURT_CLAIM_GUIDANCE")

SERVICE = RedirectService(ROUTE, STOP)
