"""Documents a customer can be asked to upload mid-case (P8 follow-up).

Keyed by the evidence kind a KB gate reads (`has_evidence`). `also` are kinds
that satisfy the same gate. The Question Authority reads REQUESTABLE so that a
ground waiting on one of these documents still counts as one the customer can
unlock: the receipt is asked for after the yes/no that makes it matter.
"""
from __future__ import annotations

EVIDENCE_REQUESTS = {
    "RECEIPT": {
        "also": ("BANK_STATEMENT",),
        "text": ("Do you have a receipt, or a bank or card statement showing a purchase "
                 "at {location} that day? Upload a photo or PDF of it and we will refer to "
                 "it in the letter. If you don't have one, you can skip this."),
    },
}

REQUESTABLE = frozenset(k for kind, spec in EVIDENCE_REQUESTS.items()
                        for k in (kind, *spec["also"]))
