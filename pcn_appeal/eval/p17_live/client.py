"""HTTP client for the live Railway backend (no secrets logged)."""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

DEFAULT_BASE = "https://api-p7-production.up.railway.app"


class LiveClient:
    def __init__(self, base: str = DEFAULT_BASE, admin_headers: Optional[dict] = None):
        self.base = base.rstrip("/")
        self.admin_headers = dict(admin_headers or {})
        self.latencies: list[int] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Optional[dict] = None,
        raw: Optional[bytes] = None,
        content_type: Optional[str] = None,
        admin: bool = False,
        timeout: int = 180,
    ) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.base}{path}"
        hdrs = {"Accept": "application/json"}
        if admin:
            hdrs.update(self.admin_headers)
        if headers:
            hdrs.update(headers)
        data = raw
        if body is not None and raw is None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = content_type or "application/json"
        elif content_type and raw is not None:
            hdrs["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_body = resp.read()
                ms = int((time.perf_counter() - t0) * 1000)
                self.latencies.append(ms)
                ct = resp.headers.get("Content-Type", "")
                parsed = None
                if "json" in ct or (raw_body[:1] in (b"{", b"[")):
                    try:
                        parsed = json.loads(raw_body.decode("utf-8"))
                    except Exception:
                        parsed = raw_body.decode("utf-8", errors="replace")[:2000]
                else:
                    parsed = raw_body.decode("utf-8", errors="replace")[:2000]
                return {
                    "ok": 200 <= resp.status < 300,
                    "status": resp.status,
                    "ms": ms,
                    "content_type": ct,
                    "headers": {k: resp.headers.get(k) for k in (
                        "access-control-allow-origin", "x-content-type-options",
                        "x-frame-options", "strict-transport-security",
                        "content-security-policy",
                    )},
                    "body": parsed,
                    "bytes": len(raw_body),
                }
        except urllib.error.HTTPError as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            self.latencies.append(ms)
            raw_body = exc.read() if exc.fp else b""
            parsed = None
            try:
                parsed = json.loads(raw_body.decode("utf-8"))
            except Exception:
                parsed = raw_body.decode("utf-8", errors="replace")[:2000]
            return {
                "ok": False,
                "status": exc.code,
                "ms": ms,
                "content_type": exc.headers.get("Content-Type") if exc.headers else "",
                "headers": {},
                "body": parsed,
                "bytes": len(raw_body),
                "error": str(exc.reason),
            }
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            self.latencies.append(ms)
            return {
                "ok": False, "status": 0, "ms": ms, "content_type": "",
                "headers": {}, "body": None, "bytes": 0, "error": f"{type(exc).__name__}: {exc}",
            }

    def get(self, path: str, **kw) -> dict[str, Any]:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw) -> dict[str, Any]:
        return self.request("POST", path, body=body, **kw)

    def post_multipart(
        self,
        path: str,
        files: list[tuple[str, str, bytes]],
        fields: Optional[dict[str, str]] = None,
        *,
        admin: bool = False,
        timeout: int = 300,
    ) -> dict[str, Any]:
        boundary = f"----LIVE_TEST_{int(time.time()*1000)}"
        chunks: list[bytes] = []
        for name, value in (fields or {}).items():
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n".encode("utf-8")
            )
        for field, filename, content in files:
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
                    f"Content-Type: {ctype}\r\n\r\n"
                ).encode("utf-8")
                + content
                + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        raw = b"".join(chunks)
        return self.request(
            "POST", path, raw=raw,
            content_type=f"multipart/form-data; boundary={boundary}",
            admin=admin, timeout=timeout,
        )

    def percentile(self, p: float) -> Optional[int]:
        if not self.latencies:
            return None
        xs = sorted(self.latencies)
        idx = min(len(xs) - 1, max(0, int(round((p / 100) * (len(xs) - 1)))))
        return xs[idx]
