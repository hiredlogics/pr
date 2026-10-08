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

from .models import CaseFile

FROM_PLACEHOLDER = "[Your name and address]"
TO_PLACEHOLDER = "[The operator's appeals address, as printed on the notice]"
SALUTATION = "Dear Sir or Madam,"
SIGN_OFF = "Yours faithfully,"


def _lines(value: Any) -> list[str]:
    text = str(value or "").replace("\r", "")
    parts = text.split("\n") if "\n" in text else text.split(",")
    return [p.strip().strip(",") for p in parts if p.strip().strip(",")]


def _vrm(value: Any) -> str:
    v = str(value or "").strip()
    return f"{v[:4]} {v[4:]}" if len(v) == 7 and " " not in v else v


def _uk_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"   # no %-d: not portable to Windows


def letter_document(case: CaseFile, on: Optional[date] = None) -> dict:
    """Structured letterhead for the UI and the PDF."""
    keeper = str(case.get("keeper_name") or "").strip()
    from_lines = ([keeper] if keeper else []) + _lines(case.get("keeper_address"))
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
    to_lines = ([operator] if operator else []) + address
    pcn, vrm = str(case.get("pcn_number") or "").strip(), _vrm(case.get("vrm"))
    subject = "Re: Parking Charge Notice " + (pcn or "[PCN number]")
    if vrm:
        subject += f", vehicle {vrm}"
    return {
        "from_lines": from_lines or [FROM_PLACEHOLDER],
        "from_complete": bool(keeper and len(from_lines) > 1),
        "to_lines": to_lines if len(to_lines) > 1 else (to_lines + [TO_PLACEHOLDER]),
        "to_complete": len(to_lines) > 1,
        "date": _uk_date(on or date.today()),
        "subject": subject,
        "salutation": SALUTATION,
        "sign_off": SIGN_OFF,
        "signature": keeper or "",
    }


def full_letter(body: str, doc: dict) -> str:
    """The postable letter as plain text: addresses, date, reference,
    salutation, the validated body unchanged, sign-off."""
    parts = ["\n".join(doc["from_lines"]), "\n".join(doc["to_lines"]), doc["date"],
             doc["subject"], doc["salutation"], body.strip(),
             doc["sign_off"] + ("\n\n" + doc["signature"] if doc["signature"] else "")]
    return "\n\n".join(p for p in parts if p)
