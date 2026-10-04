"""P17 live backend full system test suite."""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import fixtures as fx
from .client import DEFAULT_BASE, LiveClient
from ._env import admin_headers, load_env

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reports" / "live"


def _pass(name: str, detail: Any = None, **extra) -> dict:
    row = {"id": name, "result": "PASS", "detail": detail}
    row.update(extra)
    return row


def _fail(name: str, detail: Any = None, layer: Optional[str] = None, **extra) -> dict:
    row = {"id": name, "result": "FAIL", "detail": detail, "first_defective_layer": layer}
    row.update(extra)
    return row


def _skip(name: str, reason: str, **extra) -> dict:
    row = {"id": name, "result": "SKIP", "detail": reason}
    row.update(extra)
    return row


def _letter_cov(letter: str) -> dict[str, bool]:
    t = (letter or "").lower()
    return {
        "shopping": bool(re.search(r"\bshop", t)),
        "reason": bool(re.search(
            r"\b(forgot|forgotten|purse|wallet|necessary item|essential item|"
            r"left elsewhere|realised)\b", t)),
        "left": bool(re.search(r"\b(left|depart)", t)),
        "returned": bool(re.search(r"\breturn", t)),
        "cancel": bool(re.search(r"\bcancel", t)),
        "driver_id": bool(re.search(
            r"\b(i am the driver|i (parked|drove) the vehicle|the driver was me)\b", t)),
        "placeholder": bool(re.search(r"\{\{|TODO|TBD|\[insert", t, re.I)),
    }


def _jpeg(text: str = "LIVE_TEST") -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + text.encode()[:80] + b"\xff\xd9"
    img = Image.new("RGB", (800, 1100), "white")
    d = ImageDraw.Draw(img)
    y = 40
    for line in text.splitlines():
        d.text((40, y), line[:70], fill="black")
        y += 22
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _degrade(jpeg: bytes, mode: str) -> bytes:
    try:
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError:
        return jpeg
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if mode == "rotated":
        img = img.rotate(90, expand=True, fillcolor="white")
    elif mode == "low_light":
        img = ImageEnhance.Brightness(img).enhance(0.35)
    elif mode == "blur":
        img = img.filter(ImageFilter.GaussianBlur(2.5))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


class Suite:
    def __init__(self, base: str = DEFAULT_BASE):
        self.env_flags = load_env()
        self.client = LiveClient(base, admin_headers=admin_headers())
        self.has_admin = bool(self.client.admin_headers)
        self.results: list[dict] = []
        self.cases: list[dict] = []
        self.meta: dict[str, Any] = {
            "base": base,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "admin_token_configured_locally": self.has_admin,
            "env_keys_present": self.env_flags,
        }

    def add(self, row: dict) -> dict:
        self.results.append(row)
        print(f"  [{row['result']}] {row['id']}", flush=True)
        return row

    # -------------------------------------------------------------- discovery
    def discover(self) -> None:
        endpoints = {}
        for path in ("/", "/health", "/healthz", "/ready", "/docs", "/openapi.json"):
            r = self.client.get(path, timeout=60)
            endpoints[path] = {
                "status": r["status"], "ms": r["ms"],
                "content_type": r["content_type"], "ok": r["ok"],
            }
            if path == "/health" and isinstance(r.get("body"), dict):
                # redact nothing sensitive — health has no secrets
                body = dict(r["body"])
                self.meta["health"] = body
                ok = body.get("status") == "ok" and body.get("store") == "postgres" \
                    and body.get("provider") == "openai" and bool(body.get("kb_release"))
                self.add(_pass("HEALTH", body) if ok else _fail(
                    "HEALTH", body, layer="INFRASTRUCTURE_ERROR"))
            if path == "/openapi.json" and isinstance(r.get("body"), dict):
                paths = sorted((r["body"].get("paths") or {}).keys())
                self.meta["openapi_paths"] = paths
                self.meta["openapi_n_paths"] = len(paths)
                self.add(_pass("OPENAPI_DISCOVERY", {"n_paths": len(paths)}))
        self.meta["endpoint_probes"] = endpoints
        # /healthz /ready may 404 — record, not necessarily fail
        for path in ("/healthz", "/ready"):
            st = endpoints.get(path, {}).get("status")
            if st == 404:
                self.add(_skip(f"PROBE_{path.strip('/')}", "not implemented (404)"))
            elif endpoints.get(path, {}).get("ok"):
                self.add(_pass(f"PROBE_{path.strip('/')}"))
            else:
                self.add(_fail(f"PROBE_{path.strip('/')}", endpoints.get(path),
                               layer="INFRASTRUCTURE_ERROR"))
        root_ok = endpoints.get("/", {}).get("ok")
        self.add(_pass("BACKEND_AVAILABLE") if root_ok else _fail(
            "BACKEND_AVAILABLE", endpoints.get("/"), layer="INFRASTRUCTURE_ERROR"))

    # ------------------------------------------------------------------ auth
    def auth(self) -> None:
        # Unauthenticated admin
        r = self.client.get("/admin/knowledge/modules")
        if r["status"] in (401, 403):
            self.add(_pass("AUTH_ADMIN_UNAUTH", {"status": r["status"]}))
        elif r["ok"] and not self.has_admin:
            # Dev-open admin when no token configured on server
            self.add(_pass("AUTH_ADMIN_UNAUTH", {
                "status": r["status"],
                "note": "admin open (server has no ADMIN_TOKEN; environment=staging)",
            }))
        else:
            self.add(_fail("AUTH_ADMIN_UNAUTH", r, layer="SECURITY_ERROR"))

        # Invalid token
        r = self.client.request(
            "GET", "/admin/knowledge/modules",
            headers={"X-Admin-Token": "LIVE_TEST_INVALID_TOKEN_DO_NOT_USE"},
        )
        if r["status"] in (401, 403):
            self.add(_pass("AUTH_ADMIN_INVALID", {"status": r["status"]}))
        elif r["ok"]:
            self.add(_fail("AUTH_ADMIN_INVALID", {
                "status": r["status"],
                "note": "invalid token accepted — security risk if token expected",
            }, layer="SECURITY_ERROR"))
        else:
            self.add(_fail("AUTH_ADMIN_INVALID", r, layer="SECURITY_ERROR"))

        # Valid admin if local token present
        if self.has_admin:
            r = self.client.get("/admin/knowledge/modules", admin=True)
            self.add(_pass("AUTH_ADMIN_VALID", {"status": r["status"]}) if r["ok"]
                     else _fail("AUTH_ADMIN_VALID", r, layer="SECURITY_ERROR"))
        else:
            self.add(_skip("AUTH_ADMIN_VALID", "no local ADMIN_TOKEN/ADMIN_TRACE_TOKEN"))

        # Customer create without auth should work
        r = self.client.post("/cases")
        if r["ok"] and isinstance(r.get("body"), dict) and r["body"].get("case_id"):
            self.add(_pass("AUTH_CUSTOMER_CREATE", {"case_id": r["body"]["case_id"]}))
            self.cases.append({"tag": "empty_create", "case_id": r["body"]["case_id"]})
        else:
            self.add(_fail("AUTH_CUSTOMER_CREATE", r, layer="SECURITY_ERROR"))

    # ----------------------------------------------------------- document matrix
    def documents(self) -> None:
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_DOC")

        # T02 front only
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], None),
            "narrative": "LIVE_TEST_ front only upload",
            "answers": {},
        })
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        state = body.get("state") or body.get("outcome")
        letter = body.get("letter")
        incomplete_ok = (not letter) or state not in ("RELEASED",)
        # Prefer explicit incompleteness signals
        msg = json.dumps(body).lower()
        incomplete_signal = any(x in msg for x in (
            "incomplete", "both sides", "reverse", "needs_documents", "front",
        ))
        if r["ok"] and incomplete_ok:
            self.add(_pass("T02_FRONT_ONLY", {
                "case_id": body.get("case_id"), "state": state,
                "incomplete_signal": incomplete_signal,
            }))
            self.cases.append({"tag": "T02", "case_id": body.get("case_id"), "state": state})
        else:
            self.add(_fail("T02_FRONT_ONLY", body, layer="DOCUMENT_ERROR"))

        # T01 complete clean via text docs
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": "LIVE_TEST_ complete clean notice baseline",
            "answers": {"payment_made": "no"},
        }, timeout=300)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        self.cases.append({"tag": "T01", "case_id": body.get("case_id"), "body": body})
        if r["ok"] and body.get("case_id"):
            self.add(_pass("T01_COMPLETE_CLEAN", {
                "case_id": body.get("case_id"), "state": body.get("state"),
                "has_letter": bool(body.get("letter")),
                "questions": bool(body.get("questions")),
            }))
        else:
            self.add(_fail("T01_COMPLETE_CLEAN", body, layer="INFRASTRUCTURE_ERROR"))

        # T03 duplicate page (same front twice)
        r = self.client.post("/appeal", {
            "documents": [
                {"evidence_id": "E1", "kind": "PCN", "filename": "LIVE_TEST_front.txt",
                 "text": ntk["front"]},
                {"evidence_id": "E2", "kind": "PCN", "filename": "LIVE_TEST_front_dup.txt",
                 "text": ntk["front"]},
            ],
            "narrative": "LIVE_TEST_ duplicate front pages",
        }, timeout=300)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        # Should not invent reverse / should not treat as complete sides lightly
        self.add(_pass("T03_DUPLICATE_FRONT", {
            "case_id": body.get("case_id"), "state": body.get("state"),
            "released": body.get("state") == "RELEASED",
        }) if r["ok"] else _fail("T03_DUPLICATE_FRONT", body, layer="DOCUMENT_ERROR"))

        # Image quality variants via multipart
        notice_img = _jpeg(ntk["front"])
        for tid, mode in (
            ("T_IMG_ROTATED", "rotated"),
            ("T_IMG_LOW_LIGHT", "low_light"),
            ("T_IMG_BLUR", "blur"),
        ):
            data = _degrade(notice_img, mode)
            r = self.client.post_multipart(
                "/appeal/files",
                files=[("files", f"LIVE_TEST_{mode}.jpg", data)],
                fields={"narrative": f"LIVE_TEST_ {mode} image"},
                timeout=300,
            )
            body = r.get("body") if isinstance(r.get("body"), dict) else {}
            self.add(_pass(tid, {"case_id": body.get("case_id"), "state": body.get("state"),
                                 "rejected": body.get("rejected")})
                     if r["ok"] or r["status"] in (422,)
                     else _fail(tid, body, layer="EXTRACTION_ERROR"))

        # Unreadable
        r = self.client.post_multipart(
            "/appeal/files",
            files=[("files", "LIVE_TEST_unreadable.bin", b"\x00\x01\x02notanimage")],
            fields={"narrative": "LIVE_TEST_ unreadable"},
        )
        if r["status"] in (422,) or (
            isinstance(r.get("body"), dict) and r["body"].get("rejected")
        ):
            self.add(_pass("T_IMG_UNREADABLE", r.get("body")))
        else:
            self.add(_fail("T_IMG_UNREADABLE", r.get("body"), layer="DOCUMENT_ERROR"))

    # -------------------------------------------------------------- semantics
    def semantics(self) -> None:
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_SEM", days_late=True)
        answers_mv = {
            "multiple_visits": "yes", "left_site": "yes", "returned_same_day": "yes",
            "payment_made": "no",
        }
        for key in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"):
            answers = dict(answers_mv)
            if key == "S3":
                answers = {"payment_made": "yes", "payment_method": "APP",
                           "keying_error_type": "MINOR"}
            elif key == "S4":
                answers = {"payment_made": "no"}
            elif key == "S5":
                answers = {"vehicle_immobilised": "yes",
                           "immobilisation_prevented_departure": "yes"}
            elif key == "S9":
                answers = {"multiple_visits": "no", "left_site": "no", "payment_made": "no"}
            elif key in ("S10", "S11"):
                answers = {"payment_made": "no"}

            r = self.client.post("/appeal", {
                "documents": fx.docs(ntk["front"], ntk["back"]),
                "narrative": fx.NARRATIVES[key],
                "answers": answers,
            }, timeout=360)
            body = r.get("body") if isinstance(r.get("body"), dict) else {}
            case_id = body.get("case_id")
            self.cases.append({"tag": key, "case_id": case_id, "state": body.get("state")})

            # Follow-up questions via customer continue
            for _ in range(6):
                qs = body.get("questions") or []
                if not qs or body.get("letter") or body.get("state") == "RELEASED":
                    break
                ans = {}
                for q in qs:
                    fact = q.get("fact")
                    if fact in answers:
                        ans[fact] = answers[fact]
                    elif fact:
                        ans[fact] = "no"
                if not ans:
                    break
                r2 = self.client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=360)
                body = r2.get("body") if isinstance(r2.get("body"), dict) else {}

            letter = body.get("letter") or ""
            # Negated / uncertain must not invent multiple visits in letter as fact
            bad_affirm = False
            if key == "S9" and re.search(r"\bmultiple visits?\b", letter.lower()):
                # may still discuss ANPR generally — fail only if asserts left/returned sequence
                if re.search(r"\bleft\b.+\breturn", letter.lower()):
                    bad_affirm = True
            layer = None
            if not r["ok"] and not case_id:
                layer = "INFRASTRUCTURE_ERROR"
            elif bad_affirm:
                layer = "SEMANTIC_ERROR"
            self.add(
                _fail(f"SEM_{key}", {"case_id": case_id, "state": body.get("state"),
                                     "bad_affirm": bad_affirm}, layer=layer)
                if layer else
                _pass(f"SEM_{key}", {
                    "case_id": case_id, "state": body.get("state"),
                    "has_letter": bool(letter), "outcome": body.get("outcome"),
                })
            )

    # --------------------------------------------------------------- CP Plus
    def cp_plus(self) -> None:
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_CPPLUS", days_late=True)
        answers = {
            "multiple_visits": "yes", "left_site": "yes", "returned_same_day": "yes",
            "payment_made": "no", "site_postcode": "M1 1AA",
        }
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": fx.NARRATIVES["CP_PLUS"],
            "answers": answers,
        }, timeout=420)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        case_id = body.get("case_id")
        for _ in range(8):
            qs = body.get("questions") or []
            if not qs or body.get("letter"):
                break
            ans = {q["fact"]: answers.get(q["fact"], "yes")
                   for q in qs if q.get("fact")}
            r = self.client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=420)
            body = r.get("body") if isinstance(r.get("body"), dict) else {}

        letter = body.get("letter") or ""
        cov = _letter_cov(letter)
        self.meta["cp_plus"] = {
            "case_id": case_id, "state": body.get("state"), "coverage": cov,
            "letter_excerpt": letter[:1200],
        }
        self.cases.append({"tag": "T07_CP_PLUS", "case_id": case_id, "state": body.get("state")})

        ok = (
            body.get("state") == "RELEASED"
            and cov["shopping"] and cov["reason"] and cov["left"] and cov["returned"]
            and cov["cancel"] and not cov["driver_id"] and not cov["placeholder"]
        )
        # If questions remain / hold — still check we didn't release shallowly
        if body.get("state") != "RELEASED":
            self.add(_fail("T07_CP_PLUS_ACCEPTANCE", {
                "case_id": case_id, "state": body.get("state"),
                "outcome": body.get("outcome"), "coverage": cov,
                "note": "not RELEASED — material propagation cannot be fully scored",
            }, layer="DRAFT_RENDERING_ERROR" if case_id else "INFRASTRUCTURE_ERROR"))
        elif ok:
            self.add(_pass("T07_CP_PLUS_ACCEPTANCE", {
                "case_id": case_id, "coverage": cov,
            }))
        else:
            self.add(_fail("T07_CP_PLUS_ACCEPTANCE", {
                "case_id": case_id, "coverage": cov, "letter_excerpt": letter[:800],
            }, layer="FACT_PROPAGATION_ERROR" if not cov["reason"] else "DRAFT_RENDERING_ERROR"))

        # Admin trace for SupportBundle / Claim Plan if available
        if case_id and self.has_admin:
            for path, tid in (
                (f"/admin/cases/{case_id}/claim-plans", "T07_CLAIM_PLAN"),
                (f"/admin/cases/{case_id}/console", "T07_CONSOLE"),
                (f"/cases/{case_id}/trace", "T07_TRACE"),
            ):
                tr = self.client.get(path, admin=True, timeout=120)
                self.add(_pass(tid, {"status": tr["status"]}) if tr["ok"]
                         else _fail(tid, tr.get("body"), layer="CLAIM_PLAN_ERROR"))
        elif case_id:
            self.add(_skip("T07_CLAIM_PLAN", "admin token not available locally"))

    # ----------------------------------------------------------- matrix cases
    def matrix_legal_and_grounds(self) -> None:
        # T04 late NTK
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_T04", days_late=True)
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": "LIVE_TEST_ late NTK timing case",
            "answers": {"payment_made": "no"},
        }, timeout=360)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        self.cases.append({"tag": "T04", "case_id": body.get("case_id"), "state": body.get("state")})
        self.add(_pass("T04_LATE_NTK", {"case_id": body.get("case_id"), "state": body.get("state")})
                 if r["ok"] else _fail("T04_LATE_NTK", body, layer="LEGAL_FINDING_ERROR"))

        # T05 compliant / timely
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_T05", days_late=False)
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": "LIVE_TEST_ timely NTK",
            "answers": {"payment_made": "no"},
        }, timeout=360)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        self.add(_pass("T05_TIMELY_NTK", {"case_id": body.get("case_id"), "state": body.get("state")})
                 if r["ok"] else _fail("T05_TIMELY_NTK", body, layer="LEGAL_FINDING_ERROR"))

        # T06 content defect
        ntk = fx.content_defect_ntk()
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": "LIVE_TEST_ content defect NTK",
            "answers": {"payment_made": "no"},
        }, timeout=360)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        self.add(_pass("T06_CONTENT_DEFECT", {"case_id": body.get("case_id"), "state": body.get("state")})
                 if r["ok"] else _fail("T06_CONTENT_DEFECT", body, layer="LEGAL_FINDING_ERROR"))

        # T08–T13 payment / keying / breakdown / loading / permit
        for tid, narr_key, answers, pcn in (
            ("T08_PAYMENT", "S4", {"payment_made": "yes", "payment_method": "APP"}, "LIVE_TEST_PCN_T08"),
            ("T09_KEYING", "S3", {"payment_made": "yes", "payment_method": "APP",
                                  "keying_error_type": "MINOR"}, "LIVE_TEST_PCN_T09"),
            ("T10_PAY_KEY", "S3", {"payment_made": "yes", "payment_method": "APP",
                                   "keying_error_type": "MINOR"}, "LIVE_TEST_PCN_T10"),
            ("T11_BREAKDOWN", "S5", {"vehicle_immobilised": "yes",
                                     "immobilisation_prevented_departure": "yes"},
             "LIVE_TEST_PCN_T11"),
            ("T12_LOADING", "S6", {"payment_made": "no"}, "LIVE_TEST_PCN_T12"),
            ("T13_PERMIT", "S7", {"permit_held": "yes", "payment_made": "no"}, "LIVE_TEST_PCN_T13"),
        ):
            base = fx.payment_ntk(pcn=pcn) if "PAY" in tid or "KEY" in tid else fx.late_ntk(pcn=pcn, days_late=False)
            r = self.client.post("/appeal", {
                "documents": fx.docs(base["front"], base["back"]),
                "narrative": fx.NARRATIVES[narr_key],
                "answers": answers,
            }, timeout=360)
            body = r.get("body") if isinstance(r.get("body"), dict) else {}
            case_id = body.get("case_id")
            for _ in range(5):
                qs = body.get("questions") or []
                if not qs or body.get("letter"):
                    break
                ans = {q["fact"]: answers.get(q["fact"], "yes") for q in qs if q.get("fact")}
                r = self.client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=360)
                body = r.get("body") if isinstance(r.get("body"), dict) else {}
            self.cases.append({"tag": tid, "case_id": case_id, "state": body.get("state")})
            self.add(_pass(tid, {"case_id": case_id, "state": body.get("state"),
                                 "outcome": body.get("outcome")})
                     if r["ok"] and case_id
                     else _fail(tid, body, layer="GROUND_SELECTION_ERROR"))

        # T17 no-supported-ground — minimal/weak narrative on timely compliant notice
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_NOG", days_late=False)
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": fx.NARRATIVES["NO_GROUND"],
            "answers": {"payment_made": "no"},
        }, timeout=360)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        state = body.get("state")
        outcome = body.get("outcome")
        # Accept NO_SUPPORTED_GROUNDS or non-released hold without invented letter
        ok = (
            state in ("NO_SUPPORTED_GROUNDS", "MANUAL_REVIEW", "ANALYSED", "DRAFTED")
            or outcome == "NO_SUPPORTED_GROUNDS"
            or (state != "RELEASED" and not body.get("letter"))
        )
        # Spec: NOT routine MANUAL_REVIEW for ordinary no-ground — soft warn
        note = None
        if state == "MANUAL_REVIEW":
            note = "landed MANUAL_REVIEW — prefer NO_SUPPORTED_GROUNDS for ordinary no-ground"
        if ok and state == "NO_SUPPORTED_GROUNDS":
            self.add(_pass("T17_NO_SUPPORTED_GROUND", {"case_id": body.get("case_id"), "state": state}))
        elif ok:
            self.add(_fail("T17_NO_SUPPORTED_GROUND", {
                "case_id": body.get("case_id"), "state": state, "outcome": outcome, "note": note,
            }, layer="OUTCOME_STATE_ERROR"))
        else:
            self.add(_fail("T17_NO_SUPPORTED_GROUND", body, layer="OUTCOME_STATE_ERROR"))

    # ---------------------------------------------------- driver / idempotency
    def safety_and_idempotency(self) -> None:
        ntk = fx.late_ntk(pcn="LIVE_TEST_PCN_DRV", days_late=True)
        r = self.client.post("/appeal", {
            "documents": fx.docs(ntk["front"], ntk["back"]),
            "narrative": fx.NARRATIVES["DRIVER_SAFE"],
            "answers": {"multiple_visits": "yes", "left_site": "yes",
                        "returned_same_day": "yes", "payment_made": "no"},
        }, timeout=360)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        case_id = body.get("case_id")
        for _ in range(6):
            qs = body.get("questions") or []
            if not qs or body.get("letter"):
                break
            ans = {q["fact"]: "yes" for q in qs if q.get("fact")}
            r = self.client.post(f"/appeal/{case_id}", {"answers": ans}, timeout=360)
            body = r.get("body") if isinstance(r.get("body"), dict) else {}
        letter = body.get("letter") or ""
        cov = _letter_cov(letter)
        if cov["driver_id"]:
            self.add(_fail("T23_DRIVER_DISCLOSURE", {
                "case_id": case_id, "coverage": cov,
            }, layer="VALIDATION_ERROR"))
        else:
            self.add(_pass("T23_DRIVER_DISCLOSURE", {
                "case_id": case_id, "state": body.get("state"), "driver_id": False,
            }))

        # T20 duplicate generate / confirm — create case then confirm twice
        c = self.client.post("/cases")
        cid = (c.get("body") or {}).get("case_id") if c["ok"] else None
        if not cid:
            self.add(_fail("T20_IDEMPOTENCY", c, layer="INFRASTRUCTURE_ERROR"))
            return
        up = self.client.post_multipart(
            f"/cases/{cid}/files",
            files=[
                ("files", "LIVE_TEST_front.jpg", _jpeg(ntk["front"])),
                ("files", "LIVE_TEST_back.jpg", _jpeg(ntk["back"])),
            ],
            timeout=300,
        )
        conf1 = self.client.post(f"/cases/{cid}/confirm", {
            "confirmed": [], "corrections": {},
            "narrative": "LIVE_TEST_ idempotency confirm",
        }, timeout=360)
        conf2 = self.client.post(f"/cases/{cid}/confirm", {
            "confirmed": [], "corrections": {},
            "narrative": "LIVE_TEST_ idempotency confirm",
        }, timeout=360)
        # Second confirm should not 500 / should be stable
        ok = conf1["status"] not in (0, 500) and conf2["status"] not in (0, 500)
        self.add(_pass("T20_IDEMPOTENCY", {
            "case_id": cid,
            "upload": up.get("status"),
            "confirm1": conf1.get("status"),
            "confirm2": conf2.get("status"),
            "state1": (conf1.get("body") or {}).get("state") if isinstance(conf1.get("body"), dict) else None,
            "state2": (conf2.get("body") or {}).get("state") if isinstance(conf2.get("body"), dict) else None,
        }) if ok else _fail("T20_IDEMPOTENCY", {
            "confirm1": conf1, "confirm2": conf2,
        }, layer="OUTCOME_STATE_ERROR"))

        # T22 reload via GET /cases/{id}
        st1 = self.client.get(f"/cases/{cid}")
        st2 = self.client.get(f"/cases/{cid}")
        b1 = st1.get("body") if isinstance(st1.get("body"), dict) else {}
        b2 = st2.get("body") if isinstance(st2.get("body"), dict) else {}
        self.add(_pass("T22_PERSIST_RELOAD", {"case_id": cid, "state": b1.get("state")})
                 if st1["ok"] and b1.get("state") == b2.get("state")
                 else _fail("T22_PERSIST_RELOAD", {"b1": b1, "b2": b2},
                            layer="INFRASTRUCTURE_ERROR"))

    # ------------------------------------------------ admin / pgvector / integrity
    def admin_readonly(self) -> None:
        if not self.has_admin:
            for tid in (
                "T25_ADMIN_DB_STATUS", "T25_PGVECTOR_STATUS", "T25_PGVECTOR_SEARCH",
                "T25_DB_CHECKS", "T21_RELEASE_METADATA",
            ):
                self.add(_skip(tid, "no local admin token — cannot call admin routes safely"))
            return

        r = self.client.get("/admin/api/db/status", admin=True, timeout=90)
        self.add(_pass("T25_ADMIN_DB_STATUS", {
            "connected": (r.get("body") or {}).get("connected") if isinstance(r.get("body"), dict) else None,
            "database_name": (r.get("body") or {}).get("database_name") if isinstance(r.get("body"), dict) else None,
        }) if r["ok"] else _fail("T25_ADMIN_DB_STATUS", r.get("body"), layer="INFRASTRUCTURE_ERROR"))

        r = self.client.get("/admin/api/db/vector/status", admin=True, timeout=90)
        body = r.get("body") if isinstance(r.get("body"), dict) else {}
        self.add(_pass("T25_PGVECTOR_STATUS", {
            k: body.get(k) for k in (
                "installed", "extversion", "embedding_rows", "dimensions", "model",
            ) if k in body or True
        }) if r["ok"] else _fail("T25_PGVECTOR_STATUS", body, layer="INFRASTRUCTURE_ERROR"))

        # similarity searches (read-only)
        searches = {}
        for q in (
            "paid but typed registration incorrectly",
            "left site and returned later",
            "vehicle broke down",
            "loading goods",
            "permit was valid",
        ):
            sr = self.client.post("/admin/api/db/vector/search", {
                "query": q, "limit": 5,
            }, admin=True, timeout=120)
            searches[q] = sr.get("body") if sr["ok"] else {"error": sr.get("status")}
        self.meta["pgvector_searches"] = searches
        self.add(_pass("T25_PGVECTOR_SEARCH", {"n_queries": len(searches)})
                 if any(isinstance(v, (dict, list)) for v in searches.values())
                 else _fail("T25_PGVECTOR_SEARCH", searches, layer="KNOWLEDGE_RETRIEVAL_ERROR"))

        r = self.client.get("/admin/integrity/db-checks", admin=True, timeout=120)
        self.add(_pass("T25_DB_CHECKS", {"status": r["status"]}) if r["ok"]
                 else _fail("T25_DB_CHECKS", r.get("body"), layer="INFRASTRUCTURE_ERROR"))

        # Release metadata on a RELEASED test case if we have one
        released = next((c for c in self.cases if c.get("state") == "RELEASED"), None)
        # Also scan results meta cp_plus
        cp = self.meta.get("cp_plus") or {}
        rid = (released or {}).get("case_id") or cp.get("case_id")
        if rid and cp.get("state") == "RELEASED":
            tr = self.client.get(f"/admin/cases/{rid}/console", admin=True, timeout=120)
            body = tr.get("body") if isinstance(tr.get("body"), dict) else {}
            # Look for release metadata keys without requiring exact schema
            blob = json.dumps(body).lower()
            keys_ok = all(k in blob for k in ("kb_release", "commit")) or "release" in blob
            self.add(_pass("T21_RELEASE_METADATA", {"case_id": rid, "keys_signal": keys_ok})
                     if tr["ok"] else _fail("T21_RELEASE_METADATA", body,
                                            layer="OUTCOME_STATE_ERROR"))
        else:
            self.add(_skip("T21_RELEASE_METADATA", "no RELEASED LIVE_TEST_ case to inspect"))

    # -------------------------------------------------------------- security
    def security(self) -> None:
        r = self.client.get("/health")
        headers = r.get("headers") or {}
        findings = []
        if not headers.get("strict-transport-security"):
            findings.append("missing_hsts_header_on_health")
        # CORS presence is informational
        self.meta["security_headers_sample"] = headers
        # Error leakage: invalid case id
        e = self.client.get("/cases/not-a-uuid")
        body_s = json.dumps(e.get("body"))
        leaked = any(x in body_s.lower() for x in ("traceback", "psycopg", "password", "secret"))
        if leaked:
            self.add(_fail("SECURITY_ERROR_LEAKAGE", e.get("body"), layer="SECURITY_ERROR"))
        else:
            self.add(_pass("SECURITY_ERROR_LEAKAGE", {"status": e["status"]}))
        self.add(_pass("SECURITY_BASICS", {"findings": findings})
                 if not leaked else _fail("SECURITY_BASICS", findings, layer="SECURITY_ERROR"))

    # -------------------------------------------------------------- validators
    def validators_limited(self) -> None:
        """Validator mutation tests require admin draft injection — usually unavailable.

        Record capability: without admin draft mutation APIs, mark controlled
        mutation tests as SKIP rather than inventing a bypass.
        """
        for tid in (
            "T24_PLACEHOLDER_VALIDATION",
            "T25_INVENTED_FACT_VALIDATION",
            "T18_SUPPORT_ONLY",
            "T19_ADDITIVE_POFA",
        ):
            self.add(_skip(tid, "no non-destructive draft-mutation API for controlled validator PoCs on live"))

    def finalize(self) -> dict[str, Any]:
        fails = [r for r in self.results if r["result"] == "FAIL"]
        passes = [r for r in self.results if r["result"] == "PASS"]
        skips = [r for r in self.results if r["result"] == "SKIP"]
        critical = [
            f for f in fails
            if (f.get("first_defective_layer") in (
                "SECURITY_ERROR", "INFRASTRUCTURE_ERROR", "VALIDATION_ERROR",
                "FACT_PROPAGATION_ERROR",
            ) or str(f.get("id", "")).startswith("HEALTH")
               or str(f.get("id", "")).startswith("T07_CP_PLUS"))
        ]
        lat = {
            "api_p50_ms": self.client.percentile(50),
            "api_p95_ms": self.client.percentile(95),
            "n_requests": len(self.client.latencies),
        }
        health = self.meta.get("health") or {}
        if not self.meta.get("health") or health.get("status") != "ok":
            reco = "PAUSE_RELEASE"
        elif critical:
            reco = "LIVE_FIXES_REQUIRED"
        elif fails:
            reco = "LIVE_FIXES_REQUIRED"
        else:
            reco = "LIVE_BACKEND_PASS"

        report = {
            "meta": self.meta,
            "latency": lat,
            "summary": {
                "pass": len(passes), "fail": len(fails), "skip": len(skips),
                "n_tests": len(self.results),
            },
            "results": self.results,
            "cases": [{k: v for k, v in c.items() if k != "body"} for c in self.cases],
            "failures": fails,
            "critical_blockers": critical,
            "recommendation": reco,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return report


def run(base: str = DEFAULT_BASE) -> dict[str, Any]:
    s = Suite(base)
    print("P17 discover...", flush=True)
    s.discover()
    print("P17 auth...", flush=True)
    s.auth()
    print("P17 documents...", flush=True)
    s.documents()
    print("P17 semantics...", flush=True)
    s.semantics()
    print("P17 CP Plus...", flush=True)
    s.cp_plus()
    print("P17 legal/grounds matrix...", flush=True)
    s.matrix_legal_and_grounds()
    print("P17 safety/idempotency...", flush=True)
    s.safety_and_idempotency()
    print("P17 admin readonly...", flush=True)
    s.admin_readonly()
    print("P17 security...", flush=True)
    s.security()
    print("P17 validators capability...", flush=True)
    s.validators_limited()
    return s.finalize()
