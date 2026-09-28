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
    # NOT_SHOWN is what the customer answers when the notice carries no trade-body
    # logo. Without it here the lookup below simply finds no version and reports
    # UNRESOLVED:no_version_for_date, blaming the date for a missing ATA.
    if not ata or ata in ("UNKNOWN", "NOT_SHOWN"):
        return None, "UNRESOLVED:ata_unknown"
    if operator_transitioned is False:
        return None, "UNRESOLVED:operator_not_transitioned"
    hits = [v for v in versions
            if ata in v.applies_to_ata and v.effective_from <= event_date
            and (v.effective_to is None or event_date <= v.effective_to)]
    if not hits:
        return None, "UNRESOLVED:no_version_for_date"
    best = max(hits, key=lambda v: v.effective_from)
    if not best.verified:
        return best, "UNRESOLVED:version_not_legally_verified"
    return best, "RESOLVED"
