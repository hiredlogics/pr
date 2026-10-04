"""PII masking for admin explorer (default on)."""
from __future__ import annotations

import re
from typing import Any

# Column names that are masked unless show_sensitive=true.
SENSITIVE_COLUMNS = frozenset({
    "customer_name", "name", "full_name", "address", "email", "phone",
    "telephone", "mobile", "vrm", "vehicle_registration", "keeper_name",
    "keeper_address", "driver_name", "driver_address", "raw_answer",
    "answer_text", "ocr_text", "s3_key", "customer_id",
})

# Never returned even with show_sensitive.
SECRET_COLUMNS = frozenset({
    "password", "api_key", "secret", "token", "authorization",
    "database_url", "openai_api_key", "oauth", "private_key",
})

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\b(?:\+?\d[\d\s()-]{7,}\d)\b")
_VRM_HINT = re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b", re.I)


def mask_value(column: str, value: Any, *, show_sensitive: bool) -> Any:
    col = (column or "").lower()
    if col in SECRET_COLUMNS or any(s in col for s in ("password", "api_key", "secret")):
        return "[REDACTED]"
    if value is None:
        return None
    if not show_sensitive and (col in SENSITIVE_COLUMNS or col.endswith("_email")
                               or col.endswith("_phone") or col.endswith("_vrm")):
        return _mask_scalar(value)
    if isinstance(value, str) and not show_sensitive:
        text = value
        text = _EMAIL.sub("[email]", text)
        if len(text) > 40 and ("address" in col or "ocr" in col):
            return text[:24] + "…"
        return text
    return value


def _mask_scalar(value: Any) -> str:
    s = str(value)
    if not s:
        return ""
    if "@" in s:
        return "[email]"
    if len(s) <= 2:
        return "**"
    return s[:1] + ("*" * min(len(s) - 2, 6)) + s[-1:]


def mask_row(columns: list[str], row: tuple, *, show_sensitive: bool) -> dict[str, Any]:
    out = {}
    for col, val in zip(columns, row):
        out[col] = _jsonable(mask_value(col, val, show_sensitive=show_sensitive))
    return out


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "tolist"):  # numpy / vector
        try:
            arr = value.tolist()
            if isinstance(arr, list) and arr and isinstance(arr[0], (int, float)):
                return {
                    "_vector_preview": [round(float(x), 4) for x in arr[:3]],
                    "dimensions": len(arr),
                    "_raw_omitted": True,
                }
            return arr
        except Exception:
            return str(value)
    if isinstance(value, (list, dict)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, memoryview):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"
    return str(value)
