"""Order for Recovery (TE9 / PE3). Phase 3 builds the engine; until then this
route only identifies the document and says where the case will be handled.

The completeness policy is declared, not enforced (see RedirectService): the
client's OFR specification asks for the Order itself, not front-and-back pages.
"""
from ...rules.scope import ScopeStop
from ..base import CompletenessPolicy, RedirectService

ROUTE = "ORDER_FOR_RECOVERY"

# DRAFT customer wording - for client approval.
STOP = ScopeStop(
    code="ORDER_FOR_RECOVERY",
    title="Order for Recovery identified",
    message=("You have uploaded an Order for Recovery. This is a later stage of a "
             "council penalty charge, which our parking appeal service does not handle."),
    recommendation=("This service will be available through our Order for Recovery "
                    "flow. Please keep an eye on the date shown on the order."),
    cta_label=None,
    cta_action=None)

COMPLETENESS = CompletenessPolicy(
    "ORDER_FOR_RECOVERY_DOCUMENT",
    "The Order for Recovery itself, readable enough to show its date and references.")

SERVICE = RedirectService(ROUTE, STOP, COMPLETENESS)
