"""Debt recovery: a collector's demand for an unpaid private parking charge.

No engine yet. The stop is shared with the private-parking pipeline's own gate
(rules/scope.py) so the customer reads the same words whichever check fires.
"""
from ...rules import scope
from ..base import RedirectService

ROUTE = "DEBT_RECOVERY"
SERVICE = RedirectService(ROUTE, scope.STOPS["DEBT_RECOVERY"])
