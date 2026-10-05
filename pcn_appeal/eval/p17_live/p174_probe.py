"""P17.4 probes: frontend identity, proxied health, security headers (no secrets)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

FRONT = "https://pcn-appeal-p75.vercel.app"
BACK = "https://api-p7-production.up.railway.app"
OUT = Path(__file__).resolve().parents[3] / "reports" / "live"
EXPECTED_COMMIT = "3fe19f1c8fb639be1e75265034bc56a536489ddc"
EXPECTED_KB = "kb-20261004T174817Z"


def _get(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            hdrs = {k: v for k, v in resp.headers.items()}
            body = None
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = raw.decode("utf-8", "replace")[:4000]
            return {"ok": True, "status": resp.status, "headers": hdrs, "body": body, "bytes": len(raw)}
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = raw.decode("utf-8", "replace")[:2000]
        return {"ok": False, "status": exc.code, "headers": dict(exc.headers.items()) if exc.headers else {},
                "body": body, "bytes": len(raw)}
    except Exception as exc:
        return {"ok": False, "status": 0, "headers": {}, "body": None, "error": f"{type(exc).__name__}: {exc}"}


def security_sample(headers: dict) -> dict:
    keys = [
        "strict-transport-security", "content-security-policy", "x-content-type-options",
        "x-frame-options", "referrer-policy", "permissions-policy", "content-type",
        "x-vercel-id", "x-vercel-cache", "cache-control",
    ]
    low = {k.lower(): v for k, v in headers.items()}
    return {k: low.get(k) for k in keys}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    front_home = _get(FRONT + "/")
    html = front_home.get("body") if isinstance(front_home.get("body"), str) else ""
    build_ids = sorted(set(re.findall(r"/_next/static/([^/\"']+)/", html or "")))
    # Proxied health — proves PCN_API_URL on Vercel points at the Railway service.
    proxied = _get(FRONT + "/api/health")
    direct = _get(BACK + "/health")
    pb = proxied.get("body") if isinstance(proxied.get("body"), dict) else {}
    db = direct.get("body") if isinstance(direct.get("body"), dict) else {}

    mismatch = False
    reasons = []
    if not proxied.get("ok"):
        mismatch = True
        reasons.append(f"proxied health failed: {proxied.get('status')} {proxied.get('error')}")
    else:
        if pb.get("commit") != EXPECTED_COMMIT:
            mismatch = True
            reasons.append(f"commit {pb.get('commit')!r} != {EXPECTED_COMMIT}")
        if pb.get("kb_release") != EXPECTED_KB:
            mismatch = True
            reasons.append(f"kb {pb.get('kb_release')!r} != {EXPECTED_KB}")
        pv = pb.get("prompt_versions") or {}
        if pv.get("drafting") != 17:
            mismatch = True
            reasons.append(f"drafting={pv.get('drafting')}")
        if pv.get("semantic_extraction") != 1:
            mismatch = True
            reasons.append(f"semantic_extraction={pv.get('semantic_extraction')}")
        if pb.get("kb_drift") not in ([], None):
            mismatch = True
            reasons.append(f"kb_drift={pb.get('kb_drift')}")

    report = {
        "frontend": {
            "url": FRONT,
            "home_status": front_home.get("status"),
            "security_headers": security_sample(front_home.get("headers") or {}),
            "next_static_ids": build_ids[:12],
            "x_vercel_id": (front_home.get("headers") or {}).get("x-vercel-id")
            or (front_home.get("headers") or {}).get("X-Vercel-Id"),
            "api_base_url_server_side": "PCN_API_URL (server-only; not exposed to browser)",
            "browser_api_surface": FRONT + "/api/* → proxy",
        },
        "backend_direct": {
            "commit": db.get("commit"),
            "kb_release": db.get("kb_release"),
            "prompt_versions": db.get("prompt_versions"),
            "kb_drift": db.get("kb_drift"),
            "build_id": db.get("build_id"),
            "environment": db.get("environment"),
        },
        "backend_via_frontend_proxy": {
            "status": proxied.get("status"),
            "ok": proxied.get("ok"),
            "commit": pb.get("commit"),
            "kb_release": pb.get("kb_release"),
            "prompt_versions": pb.get("prompt_versions"),
            "kb_drift": pb.get("kb_drift"),
            "build_id": pb.get("build_id"),
            "environment": pb.get("environment"),
            "proxy_security_headers": security_sample(proxied.get("headers") or {}),
        },
        "version_match": not mismatch,
        "mismatch_reasons": reasons,
        "verdict_if_stop": "FRONTEND_BACKEND_VERSION_MISMATCH" if mismatch else None,
    }
    path = OUT / "P17_4_IDENTITY.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "version_match": report["version_match"],
        "proxy_commit": pb.get("commit"),
        "proxy_kb": pb.get("kb_release"),
        "proxy_drafting": (pb.get("prompt_versions") or {}).get("drafting"),
        "proxy_sem": (pb.get("prompt_versions") or {}).get("semantic_extraction"),
        "proxy_drift": pb.get("kb_drift"),
        "front_headers": report["frontend"]["security_headers"],
        "static_ids": build_ids[:5],
        "path": str(path),
    }, indent=2))
    return 1 if mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())
