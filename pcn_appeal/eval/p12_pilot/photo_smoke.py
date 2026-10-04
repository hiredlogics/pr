"""Physical-photo extraction smoke for P12 pre-pilot blockers.

Uses real notice photos under datasets/pcn_finetune_v1/images plus degraded
variants (angle / low-light / glare / blur). Labels score only — never injected.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
IMG_DIR = ROOT / "datasets" / "pcn_finetune_v1" / "images"
CASE_DIR = ROOT / "datasets" / "pcn_finetune_v1" / "cases"
NOTICE_DIR = ROOT / "datasets" / "p9_v1" / "notice_only"

CRITICAL = (
    "operator_name", "pcn_number", "vrm", "parking_event_date",
    "notice_issue_date", "parking_location", "alleged_breach", "notice_route",
)


def _load_expected(stem: str) -> dict[str, Any]:
    # Prefer p9 notice_only labels (same REF ids as image stems often share).
    for path in NOTICE_DIR.glob("REF_*.json"):
        if stem.split("_")[0] in path.stem or stem in path.stem:
            spec = json.loads(path.read_text(encoding="utf-8"))
            return ((spec.get("expected") or {}).get("extraction") or {}).get("values") or {}
    for path in CASE_DIR.glob("*.json"):
        if stem.split("_")[0] in path.stem:
            spec = json.loads(path.read_text(encoding="utf-8"))
            return (spec.get("gold_facts") or spec.get("expected") or {})
    return {}


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper().replace(" ", "")


def _date_ymd(value: Any) -> str:
    import re
    s = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{y}{mo:02d}{d:02d}"
    return "".join(ch for ch in s if ch.isdigit())


def _match(expected: Any, got: Any, field: str) -> bool:
    if expected in (None, ""):
        return got not in (None, "")
    if field in ("parking_event_date", "notice_issue_date"):
        ed, gd = _date_ymd(expected), _date_ymd(got)
        return bool(ed) and bool(gd) and ed == gd
    e, g = _norm(expected), _norm(got)
    if field == "vrm":
        return e == g
    if field == "operator_name":
        return e[:10] in g or g[:10] in e
    if field == "alleged_breach":
        return any(tok in g for tok in e.split()[:3] if len(tok) > 3) or e[:16] in g
    return e == g or e in g or g in e


def _degrade(jpeg: bytes, mode: str) -> bytes:
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageDraw
    except ImportError:
        return jpeg
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if mode == "angled":
        img = img.rotate(12, expand=True, fillcolor="white")
    elif mode == "low_light":
        img = ImageEnhance.Brightness(img).enhance(0.35)
    elif mode == "glare":
        draw = ImageDraw.Draw(img)
        w, h = img.size
        draw.ellipse([w * 0.3, h * 0.1, w * 0.85, h * 0.55], fill=(255, 255, 240))
        img = Image.blend(img, ImageEnhance.Brightness(img).enhance(1.4), 0.35)
    elif mode == "blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=1.8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def run(max_base: int = 4) -> dict[str, Any]:
    from pcn_appeal import config
    config.load()
    from pcn_appeal.llm import DemoLLM, default_client, probe
    from pcn_appeal.models import CaseFile, EvidenceItem
    from pcn_appeal.orchestrator import AppealPipeline

    info = probe()
    client = default_client()
    images = sorted(IMG_DIR.glob("*.png")) + sorted(IMG_DIR.glob("*.jpg"))
    if not images:
        return {
            "ran": False,
            "passed": False,
            "reason": f"No physical photos in {IMG_DIR}",
            "physical_photos_present": False,
        }
    if isinstance(client, DemoLLM) or info.get("provider") != "openai":
        return {
            "ran": False,
            "passed": False,
            "reason": "live OpenAI required for photo extraction smoke",
            "physical_photos_present": True,
            "n_photos": len(images),
        }

    rows = []
    modes_per = ("normal", "angled", "low_light", "glare", "blur")
    for path in images[:max_base]:
        raw = path.read_bytes()
        # Normalize to JPEG bytes for degradation helpers.
        try:
            from PIL import Image
            buf = io.BytesIO()
            Image.open(io.BytesIO(raw)).convert("RGB").save(buf, format="JPEG", quality=90)
            base_jpeg = buf.getvalue()
        except Exception:
            base_jpeg = raw
        expected = _load_expected(path.stem)
        for mode in modes_per:
            jpeg = base_jpeg if mode == "normal" else _degrade(base_jpeg, mode)
            # Separate front/back: only front available → completeness gate
            # exercised via notice_sides when second page absent.
            case = CaseFile(f"P12_PHOTO_{path.stem}_{mode}", evidence={
                "E1": EvidenceItem("E1", "PCN", path.name, text="", images=[jpeg]),
            })
            if mode == "normal":
                # Two-page smoke: duplicate as distinct-ish back surrogate only
                # when bytes differ after light degrade — still not inventing text.
                back = _degrade(base_jpeg, "angled")
                case.evidence["E1"].images = [jpeg, back]
            pipe = AppealPipeline(client)
            t0 = time.time()
            try:
                pipe.ingest(case)
                latency = int((time.time() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001
                rows.append({
                    "image": path.name, "mode": mode,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                    "passed": False,
                })
                continue
            facts = {n: getattr(node, "value", None)
                     for n, node in (case.facts or {}).items()}
            crit = [n for n in CRITICAL if n in expected] or list(CRITICAL)
            # When labels missing, require presence of core identity fields.
            if expected:
                hits = {n: _match(expected[n], facts.get(n), n)
                        for n in crit if n in expected}
                score = sum(hits.values()) / max(len(hits), 1)
            else:
                present = [n for n in ("operator_name", "pcn_number", "vrm")
                           if facts.get(n) not in (None, "")]
                hits = {n: n in present for n in ("operator_name", "pcn_number", "vrm")}
                score = len(present) / 3.0
            # Safety: do not invent reverse-page legal content when only photos
            # of the front exist — missing allegation is acceptable; fabricated
            # high-confidence allegation without evidence is not scored here.
            rows.append({
                "image": path.name,
                "mode": mode,
                "latency_ms": latency,
                "legal_critical_accuracy": score,
                "field_hits": hits,
                "extracted_sample": {k: facts.get(k) for k in CRITICAL if k in facts},
                "passed": score >= 0.5,  # degraded photos: safety floor
            })

    scores = [r["legal_critical_accuracy"] for r in rows
              if isinstance(r.get("legal_critical_accuracy"), float)]
    passed_n = sum(1 for r in rows if r.get("passed"))
    # Normal photos must mostly pass; degraded may be weaker but mean >= 0.5.
    normal = [r for r in rows if r.get("mode") == "normal" and "error" not in r]
    normal_ok = all(r.get("passed") for r in normal) if normal else False
    acceptable = bool(scores) and normal_ok and (sum(scores) / len(scores) >= 0.5)
    return {
        "ran": True,
        "passed": acceptable,
        "physical_photos_present": True,
        "provider": info.get("provider"),
        "model": (info.get("models") or {}).get("extraction"),
        "n_rows": len(rows),
        "n_passed": passed_n,
        "n_base_photos": min(len(images), max_base),
        "legal_critical_accuracy_mean": (sum(scores) / len(scores)) if scores else None,
        "normal_all_passed": normal_ok,
        "modes": list(modes_per) + ["front_back_pair_on_normal"],
        "cases": rows,
        "note": (
            "Real phone/scan PNGs from pcn_finetune_v1; degraded variants "
            "simulate angle/low-light/glare/blur. Labels used only for scoring."
        ),
    }
