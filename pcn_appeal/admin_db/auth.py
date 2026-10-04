"""Admin gate for explorer routes — avoids importing api.py (circular)."""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException

from pcn_appeal import runtime


def require_admin(authorization: Optional[str], x_admin_token: Optional[str]) -> None:
    token = (os.getenv("ADMIN_TRACE_TOKEN") or os.getenv("ADMIN_TOKEN") or "").strip()
    if not token:
        if runtime.is_production():
            raise HTTPException(403, "admin endpoints are disabled: no admin token configured")
        return
    presented = [f"Bearer {token}", token]
    offered = [authorization or "", x_admin_token or ""]
    if not any(hmac.compare_digest(o.encode(), p.encode())
               for o, p in zip(offered, presented) if o):
        raise HTTPException(401, "admin authorization required")
