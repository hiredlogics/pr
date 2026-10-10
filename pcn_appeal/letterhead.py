"""The parts of a posted letter around the validated body.

Validation checks the argument (`AppealOutput.letter`). A customer still needs
to post it: who it is from, who it is to, the date, the reference line, a
salutation and a sign-off. Live, the copied letter had none of these and the
PDF (not available on serverless deployments) was the only place they appeared.

Everything here comes from document facts read off the notice. A value that
was not read is a visible placeholder, never a guess, so a letter that cannot
be posted as it stands says so.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from .letter_frame import (PLACEHOLDER_ADDRESS, PLACEHOLDER_NAME, PLACEHOLDER_OPERATOR,
                           PLACEHOLDER_OPERATOR_ADDRESS, PLACEHOLDER_PCN, display_reg)
from .models import CaseFile

# Every block is always present; a value that was not read is a visible placeholder.
FROM_PLACEHOLDER = PLACEHOLDER_NAME
TO_PLACEHOLDER = PLACEHOLDER_OPERATOR_ADDRESS
SALUTATION = "Dear Sir or Madam,"
SIGN_OFF = "Yours faithfully,"


def _lines(value: Any) -> list[str]:
    text = str(value or "").replace("\r", "")
    parts = text.split("\n") if "\n" in text else text.split(",")
    return [p.strip().strip(",") for p in parts if p.strip().strip(",")]


def _vrm(case: CaseFile) -> str:
    """The registration exactly as the notice prints it, else the usual formatting."""
    return display_reg({"vrm_display": case.get("vrm_display"), "vrm": case.get("vrm")}) or ""


def _uk_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"   # no %-d: not portable to Windows


def letter_document(case: CaseFile, on: Optional[date] = None) -> dict:
    """Structured letterhead for the UI and the PDF."""
    keeper = str(case.get("keeper_name") or "").strip()
    keeper_address = _lines(case.get("keeper_address"))
    from_lines = [keeper or PLACEHOLDER_NAME] + (keeper_address or [PLACEHOLDER_ADDRESS])
    operator = str(case.get("operator_name") or "").strip()
    address = _lines(case.get("operator_address"))
    # Live: the printed address often starts with the operator's own name
    # ("APCOA", "Euro Car Parks"), which repeated the name in the To block.
    from .notice_completeness import same_operator
    if address and operator and (address[0].lower() == operator.lower()
                                 or same_operator(address[0], operator)):
        address = address[1:]
    # "Registered office: 1st Floor, ..., London, SE1 4PL" read as one line.
    if len(address) == 1 and address[0].count(",") >= 2:
        address = _lines(address[0].split(":", 1)[-1] if address[0].lower().startswith(
            ("registered office", "address")) else address[0])
    to_lines = [operator or PLACEHOLDER_OPERATOR] + (address or [PLACEHOLDER_OPERATOR_ADDRESS])
    pcn, vrm = str(case.get("pcn_number") or "").strip(), _vrm(case)
    subject = "Re: Parking Charge Notice " + (pcn or PLACEHOLDER_PCN)
    if vrm:
        subject += f", vehicle {vrm}"
    return {
        "from_lines": from_lines,
        "from_complete": bool(keeper and keeper_address),
        "to_lines": to_lines,
        "to_complete": bool(operator and address),
        "date": _uk_date(on or date.today()),
        "subject": subject,
        "salutation": SALUTATION,
        "sign_off": SIGN_OFF,
        "signature": keeper or PLACEHOLDER_NAME,
    }


def full_letter(body: str, doc: dict) -> str:
    """The postable letter as plain text: addresses, date, reference,
    salutation, the validated body unchanged, sign-off."""
    parts = ["\n".join(doc["from_lines"]), "\n".join(doc["to_lines"]), doc["date"],
             doc["subject"], doc["salutation"], body.strip(),
             doc["sign_off"] + ("\n\n" + doc["signature"] if doc["signature"] else "")]
    return "\n\n".join(p for p in parts if p)
