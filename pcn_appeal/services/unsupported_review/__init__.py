"""Documents the classifier could not place with confidence.

Not a guess at a service: the customer is told we could not identify the
document and asked for a clearer copy. No human review queue exists yet.
"""
from ...rules.scope import ScopeStop
from ..base import RedirectService

ROUTE = "UNSUPPORTED_REVIEW"

# DRAFT customer wording - for client approval.
STOP = ScopeStop(
    code="UNSUPPORTED_REVIEW",
    title="We couldn't identify this document",
    message=("We couldn't tell what kind of document this is. It may be a type we "
             "don't handle yet, or the photo may be too unclear to read."),
    recommendation=("Please upload a clear photo or PDF of the whole notice or letter "
                    "you received, including any reference numbers."),
    cta_label="Upload again",
    cta_action="RETRY_UPLOAD")

SERVICE = RedirectService(ROUTE, STOP)
