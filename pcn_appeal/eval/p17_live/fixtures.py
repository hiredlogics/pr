"""Notice text fixtures for LIVE_TEST_ cases (no production customer data)."""
from __future__ import annotations

from typing import Any


def late_ntk(*, pcn: str = "LIVE_TEST_PCN_LATE01", days_late: bool = True) -> dict[str, str]:
    # Event 01/06/2026, issue 20/06/2026 → late postal for England/Wales.
    issue = "20/06/2026" if days_late else "05/06/2026"
    front = f"""PARKING CHARGE NOTICE
Operator Name: LIVE_TEST Parking Ltd
PCN Number: {pcn}
Vehicle Registration: LT12 EST
Location: LIVE_TEST Retail Park
Postcode: M1 1AA
Date of Contravention: 01/06/2026
Date of Issue: {issue}
Entry Time: 10:00
Exit Time: 12:47
Charge: £100
Alleged Breach: Overstayed paid time
Trade Association: BPA
"""
    back = f"""NOTICE TO KEEPER — REVERSE
PCN {pcn}
Schedule 4 Protection of Freedoms Act 2012
If you were not the driver, you may pass this notice to the driver or name them.
We are seeking recovery from the keeper under Schedule 4 if the driver is not identified.
Pay or appeal within 28 days of the date of issue.
"""
    return {"front": front, "back": back}


def content_defect_ntk(pcn: str = "LIVE_TEST_PCN_DEF01") -> dict[str, str]:
    front = late_ntk(pcn=pcn, days_late=False)["front"]
    # Back intentionally omits keeper-liability / invitation wording.
    back = f"""REVERSE PAGE
PCN {pcn}
How to pay: visit our website.
Contact: appeals@live-test.example
"""
    return {"front": front, "back": back}


def payment_ntk(pcn: str = "LIVE_TEST_PCN_PAY01") -> dict[str, str]:
    front = f"""PARKING CHARGE NOTICE
Operator Name: LIVE_TEST Parking Ltd
PCN Number: {pcn}
Vehicle Registration: AB12 CDE
Location: LIVE_TEST Retail Park
Postcode: M1 1AA
Date of Contravention: 01/06/2026
Date of Issue: 05/06/2026
Entry Time: 10:00
Exit Time: 12:47
Charge: £100
Alleged Breach: No valid payment for vehicle
Trade Association: BPA
"""
    back = late_ntk(pcn=pcn, days_late=False)["back"]
    return {"front": front, "back": back}


NARRATIVES = {
    "S1": (
        "LIVE_TEST_ I attended the car park for shopping at the retail estate. "
        "Partway through I realised I had forgotten my purse at home, "
        "so I left the site. I returned later the same day to continue my visit."
    ),
    "S2": "LIVE_TEST_ I went shopping, forgot my wallet, left the site and returned later the same day.",
    "S3": "LIVE_TEST_ I paid on the app but typed one character of the registration incorrectly.",
    "S4": "LIVE_TEST_ I tried to pay at the machine but it failed and kept erroring.",
    "S5": "LIVE_TEST_ The vehicle broke down and would not start; recovery attended.",
    "S6": "LIVE_TEST_ I was loading and delivering goods for a short period.",
    "S7": "LIVE_TEST_ I held a valid permit for the bay on that date.",
    "S8": "LIVE_TEST_ This was a passenger drop-off only.",
    "S9": "LIVE_TEST_ I did NOT leave the site at any point that day.",
    "S10": "LIVE_TEST_ I might have left but I am not sure.",
    "S11": "LIVE_TEST_ My passenger left the site while I stayed with the vehicle.",
    "CP_PLUS": (
        "LIVE_TEST_ I attended the car park for shopping at the retail estate. "
        "Partway through I realised I had forgotten my purse at home, "
        "so I left the site. I returned later the same day to continue my visit."
    ),
    "DRIVER_SAFE": "LIVE_TEST_ I parked there. I went shopping. I returned later.",
    "NO_GROUND": "LIVE_TEST_ I disagree with the charge but have no further details.",
}


def docs(front: str, back: str | None = None) -> list[dict[str, Any]]:
    out = [{
        "evidence_id": "E1",
        "kind": "PCN",
        "filename": "LIVE_TEST_front.txt",
        "text": front,
    }]
    if back is not None:
        out.append({
            "evidence_id": "E2",
            "kind": "PCN",
            "filename": "LIVE_TEST_back.txt",
            "text": back,
        })
    return out
