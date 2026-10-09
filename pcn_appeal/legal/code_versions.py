"""Resolve which sector Code version applies to a parking event.

Code values (grace minutes, keying-error treatment...) are DATA, loaded from
`data/code_versions.yaml` (production: Postgres table edited in admin UI).
Nothing in application code hard-codes "10 minutes".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

DATA = Path(__file__).resolve().parent.parent / "data" / "code_versions.yaml"


@dataclass
class CodeVersion:
    version_id: str
    label: str
    effective_from: date
    effective_to: Optional[date]
    applies_to_ata: list[str]
    provisions: dict
    verified: bool


def load(path: Path = DATA) -> list[CodeVersion]:
    raw = yaml.safe_load(path.read_text())
    out = []
    for r in raw["versions"]:
        out.append(CodeVersion(
            version_id=r["version_id"], label=r["label"],
            effective_from=r["effective_from"], effective_to=r.get("effective_to"),
            applies_to_ata=r.get("applies_to_ata", ["BPA", "IPC"]),
            provisions=r.get("provisions", {}), verified=bool(r.get("verified", False))))
    return out


def resolve(event_date: Optional[date], ata: Optional[str],
            operator_transitioned: Optional[bool] = None,
            versions: Optional[list[CodeVersion]] = None) -> tuple[Optional[CodeVersion], str]:
    """Return (version, status). status: RESOLVED / UNRESOLVED:<reason>."""
    versions = versions if versions is not None else load()
    if not event_date:
        return None, "UNRESOLVED:no_event_date"
    if operator_transitioned is False:
        return None, "UNRESOLVED:operator_not_transitioned"

    in_window = [v for v in versions
                 if v.effective_from <= event_date
                 and (v.effective_to is None or event_date <= v.effective_to)]
    if not in_window:
        return None, "UNRESOLVED:no_version_for_date"

    known_ata = bool(ata) and ata not in ("UNKNOWN", "NOT_SHOWN")
    hits = [v for v in in_window if ata in v.applies_to_ata] if known_ata else in_window
    if not hits:
        return None, "UNRESOLVED:no_version_for_date"

    # The sector Single Code is version-controlled by event date; the trade body
    # only narrows the choice while versions actually differ by it. Demanding an
    # ATA that changes nothing blocked every operator absent from the local
    # name table - including unseen ones - so it is required only when the date
    # alone leaves a genuine choice between trade-body-specific versions.
    if not known_ata and len({v.version_id for v in hits}) > 1:
        return None, "UNRESOLVED:ata_unknown"
    best = max(hits, key=lambda v: v.effective_from)
    if not best.verified:
        return best, "UNRESOLVED:version_not_legally_verified"
    if not known_ata:
        # Client decision: the Code version follows the event date, so the
        # provisions resolve and overstay/grace can be CALCULATED. The operator's
        # trade-body membership is still unestablished, which is a knowledge gap
        # the draft must not paper over - a ground that stands on a Code rule
        # needs that applicability check (VAL-CODE).
        return best, "RESOLVED_ATA_UNVERIFIED"
    return best, "RESOLVED"


# Statuses whose version may be used for calculation. RESOLVED_ATA_UNVERIFIED
# is usable because the sector Single Code is version-controlled by event date;
# the unestablished trade body is carried as a knowledge gap, not as a reason
# to abandon the calculation.
USABLE_STATUSES = ("RESOLVED", "RESOLVED_ATA_UNVERIFIED")


def is_usable(status: Optional[str]) -> bool:
    """Whether `resolve()`'s status means the version can be calculated with."""
    return str(status or "") in USABLE_STATUSES


def ata_unverified(status: Optional[str]) -> bool:
    """The Code applies by date but the operator's trade body is unestablished."""
    return str(status or "") == "RESOLVED_ATA_UNVERIFIED"
