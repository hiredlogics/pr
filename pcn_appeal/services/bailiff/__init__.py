"""Bailiff / enforcement agent documents. No engine yet."""
from ...rules.scope import ScopeStop
from ..base import RedirectService

ROUTE = "BAILIFF"

# DRAFT customer wording - for client approval.
STOP = ScopeStop(
    code="BAILIFF",
    title="Enforcement agent document identified",
    message=("This is a letter from an enforcement agent (bailiff). Our appeal service "
             "does not handle cases at the enforcement stage."),
    recommendation=("Enforcement can move quickly. Please take independent advice "
                    "promptly and do not ignore any date given on the letter."),
    cta_label="See our free resources",
    cta_action="RESOURCES")

SERVICE = RedirectService(ROUTE, STOP)
