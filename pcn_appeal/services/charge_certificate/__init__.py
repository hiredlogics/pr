"""Charge Certificate. No engine yet.

Declared completeness follows the client's Charge Certificate specification:
front and back are required. Not enforced until the service is live.
"""
from ...notice_completeness import upload_pages_sufficient
from ...rules.scope import ScopeStop
from ..base import CompletenessPolicy, RedirectService

ROUTE = "CHARGE_CERTIFICATE"

# DRAFT customer wording - for client approval.
STOP = ScopeStop(
    code="CHARGE_CERTIFICATE",
    title="Charge Certificate identified",
    message=("You have uploaded a Charge Certificate. This is a later stage of a "
             "council penalty charge, which our parking appeal service does not handle."),
    recommendation="This service will be available through our Charge Certificate flow.",
    cta_label=None,
    cta_action=None)

COMPLETENESS = CompletenessPolicy(
    "FRONT_AND_BACK_OR_MULTIPAGE",
    "Front and back of the Charge Certificate, or a multipage PDF.",
    check=lambda case: upload_pages_sufficient(list(case.evidence.values())))

SERVICE = RedirectService(ROUTE, STOP, COMPLETENESS)
