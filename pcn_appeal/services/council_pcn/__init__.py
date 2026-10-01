"""Council PCN: a statutory Penalty Charge Notice. No engine yet."""
from ...rules import scope
from ..base import RedirectService

ROUTE = "COUNCIL_PCN"
SERVICE = RedirectService(ROUTE, scope.STOPS["COUNCIL_PCN"])
