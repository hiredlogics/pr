"""County Court Judgment (CCJ removal). No engine yet."""
from ...rules.scope import ScopeStop
from ..base import RedirectService

ROUTE = "CCJ_REMOVAL"

# DRAFT customer wording - for client approval.
STOP = ScopeStop(
    code="CCJ_REMOVAL",
    title="County Court Judgment identified",
    message=("This document shows a County Court Judgment. Our appeal service does not "
             "handle cases at the judgment stage."),
    recommendation=("Our CCJ service will handle these. Until then, please take "
                    "independent advice promptly - some options depend on acting quickly."),
    cta_label="See our free resources",
    cta_action="RESOURCES")

SERVICE = RedirectService(ROUTE, STOP)
