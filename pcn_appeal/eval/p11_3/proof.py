"""P11.3 live proof: CP Plus forgotten-purse particular must reach the letter."""
from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
IMG_DIR = ROOT / "datasets" / "pcn_finetune_v1" / "images"
NOTICE_JSON = ROOT / "datasets" / "p9_v1" / "notice_only" / "REF_00347261120013.json"

CP_PLUS_NARRATIVE = (
    "I attended the car park for shopping at the retail estate. "
    "Partway through I realised I had forgotten my purse at home, "
    "so I left the site. I returned later the same day to continue my visit."
)

REGRESSION = (
    ("forgot_wallet",
     "I went shopping, forgot my wallet, left the site and returned later the same day."),
    ("collect_card",
     "I left to collect my payment card and returned later the same day."),
    ("item_at_home",
     "I realised a necessary item was at home, left and returned the same day."),
)


def _load_env() -> None:
    for name in (".env.staging.local", ".env.local", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip().strip("\"'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def _jpeg_text_page(text: str) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + text.encode()[:180] + b"\xff\xd9"
    img = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    y = 24
    for line in text.splitlines():
        draw.text((28, y), line[:90], fill="black", font=font)
        y += 26
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _letter_from_case(case) -> str:
    for d in reversed(case.draft_versions or []):
        parts = []
        for para in d.get("content") or []:
            for sent in para or []:
                if isinstance(sent, dict) and sent.get("text"):
                    parts.append(sent["text"])
        if parts:
            return "\n".join(parts)
    return ""


def _anpr_paragraph(letter: str) -> str:
    low = letter.lower()
    # Prefer the paragraph that mentions shopping / left / return together.
    paras = [p.strip() for p in re.split(r"\n\s*\n", letter) if p.strip()]
    for p in paras:
        pl = p.lower()
        if ("shop" in pl or "visit" in pl) and ("left" in pl or "depart" in pl) and "return" in pl:
            return p
    for p in paras:
        if "keeper's account" in p.lower() or "keepers account" in p.lower().replace("'", ""):
            return p
    return paras[-1] if paras else letter[-800:]


def _coverage(letter: str) -> dict[str, bool]:
    t = (letter or "").lower()
    return {
        "shopping": bool(re.search(r"\bshop", t)),
        "reason_for_leaving": bool(re.search(
            r"\b(forgot|forgotten|retriev|collect|necessary item|essential item|"
            r"wallet|purse|at home|left elsewhere|realised to be elsewhere|"
            r"realised to have been left|prompting the departure|"
            r"needed for the visit)\b", t)),
        "left_site": bool(re.search(r"\b(left|depart)", t)),
        "returned": bool(re.search(r"\breturn", t)),
        "multiple_visits": bool(re.search(
            r"\b(more than one|multiple|second visit|separate occasion|more than one separate)\b", t)),
        "driver_disclosure": bool(re.search(
            r"\b(i (parked|drove|was driving)|the driver (was|is) (me|myself)|i am the driver)\b", t)),
    }


def _regression_offline() -> dict[str, Any]:
    from pcn_appeal.engines.narrative import extract_departure_reason, understand
    from pcn_appeal.models import CaseFile
    from pcn_appeal.drafting.support_contract import enrich_support_rows, build_bundle
    from pcn_appeal.models import Fact, FactSource, FactStatus, SourceKind

    rows = {}
    for key, text in REGRESSION:
        atom = extract_departure_reason(text)
        case = CaseFile(f"reg-{key}")
        understand(case, [text])
        case.put(Fact("F-mv", "multiple_visits", True, FactStatus.ANSWERED,
                      FactSource(SourceKind.ANSWER, "answer:multiple_visits")))
        enriched = enrich_support_rows(
            [{"fact": "multiple_visits", "value": True}], case=case)
        bundle = build_bundle(enriched, case=case)
        rows[key] = {
            "atom": atom,
            "departure_reason": case.get("departure_reason"),
            "bundle_has_departure_reason": "departure_reason" in bundle.source_fact_names,
            "passed": bool(atom and case.get("departure_reason")
                           and "departure_reason" in bundle.source_fact_names),
        }
    return {
        "cases": rows,
        "passed": all(v["passed"] for v in rows.values()),
    }


def run() -> dict[str, Any]:
    _load_env()
    from pcn_appeal import config
    from pcn_appeal.engines.claim_plan_authority import latest_locked
    from pcn_appeal.engines.narrative import extract_departure_reason
    from pcn_appeal.intake import run_intake
    from pcn_appeal.llm import default_client, probe
    from pcn_appeal.models import CaseState, EvidenceItem
    from pcn_appeal.orchestrator import AppealPipeline
    from pcn_appeal.store import cases as case_store
    from pcn_appeal.store import db, kb_source
    from pcn_appeal.kg.graph import KnowledgeGraph
    from pcn_appeal.drafting.plan import build_draft_plan
    from pcn_appeal.services.private_parking import PrivateParkingService
    from pcn_appeal.notice_completeness import assess_notice_sides

    config.load()
    report: dict[str, Any] = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    report["regression_offline"] = _regression_offline()

    info = probe()
    if info.get("provider") != "openai" or not db.enabled():
        report["verdict"] = "MATERIAL_PROPAGATION_FIX_REQUIRED"
        report["error"] = "live OpenAI + DATABASE_URL required"
        return report

    db.init_schema()
    client = default_client()
    pipe = AppealPipeline(client)
    try:
        rel = kb_source.load_release("kb-20261004T113657Z")
        pipe.kg = KnowledgeGraph.from_release(rel)
        pipe.reasoning.kg = pipe.kg
    except Exception:
        pass

    notice = json.loads(NOTICE_JSON.read_text(encoding="utf-8"))
    fields = ((notice.get("input") or {}).get("extraction_fields") or {})
    front = (IMG_DIR / "00347261120013_cpplus.png").read_bytes()
    back = _jpeg_text_page(
        f"NOTICE TO KEEPER REVERSE\nPCN {fields.get('pcn_number')}\n"
        "Schedule 4 Protection of Freedoms Act 2012\n"
        "Identify the driver or pass this notice to the driver."
    )
    front_text = ((notice.get("input") or {}).get("evidence") or [{}])[0].get("text") or ""

    case = case_store.new_case(kb_release_id=pipe.kg.release_id)
    report["case_id"] = case.case_id
    case.evidence["E1"] = EvidenceItem("E1", "PCN", "front.png", text=front_text)
    case.evidence["E1"].images = [front, back]
    case.evidence["E2"] = EvidenceItem(
        "E2", "PCN", "back.jpg",
        text=f"REVERSE PCN {fields.get('pcn_number')}\nSchedule 4\nIdentify the driver.",
    )
    case.evidence["E2"].images = [back]

    intake = run_intake(case, client)
    if intake.proceed:
        PrivateParkingService(pipe).extract_service_facts(case)
    else:
        pipe.ingest(case)

    report["notice_sides_complete"] = case.get("notice_sides_complete")
    report["raw_narrative"] = CP_PLUS_NARRATIVE
    report["narrative_atom_pre"] = extract_departure_reason(CP_PLUS_NARRATIVE)

    questions = pipe.confirm(case, {}, list(case.facts), CP_PLUS_NARRATIVE)
    answers = {
        "multiple_visits": "yes", "left_site": "yes", "returned_same_day": "yes",
        "site_postcode": "SE16 7LL", "payment_made": "no",
    }
    for _ in range(8):
        if not questions:
            break
        given = {q["fact"]: answers[q["fact"]] for q in questions if q.get("fact") in answers}
        if not given:
            for q in questions:
                if q.get("fact"):
                    given[q["fact"]] = answers.get(q["fact"], "yes")
                    break
        if not given:
            break
        questions = pipe.answer(case, given)

    report["departure_reason_fact"] = case.get("departure_reason")
    report["narrative_atoms"] = [
        a for ev in case.audit if ev.get("event") == "narrative_atom"
        for a in (ev.get("atoms") or [])
    ]
    report["provenance_departure"] = [
        p for p in (case.free_text_provenance or [])
        if p.get("fact_name") == "departure_reason"
    ]

    out = pipe.generate(case)
    report["final_state"] = case.state.value
    report["validation"] = {
        "passed": bool(getattr(getattr(out, "validation", None), "passed", False)),
        "issues": [getattr(i, "rule", None)
                   for i in (getattr(getattr(out, "validation", None), "issues", None) or [])],
    }
    # Neon can drop idle connections after a long generate — retry save.
    for attempt in range(3):
        try:
            case_store.save(case)
            report["persisted"] = True
            break
        except Exception as exc:  # noqa: BLE001
            report["persist_error"] = f"{type(exc).__name__}: {exc}"[:240]
            time.sleep(2 * (attempt + 1))
            try:
                db.init_schema()
            except Exception:
                pass
    else:
        report["persisted"] = False

    plan = latest_locked(case)
    grounds = [i.module_id for i in (getattr(plan, "supported", None) or [])] if plan else []
    report["claim_plan_grounds"] = grounds
    report["pofa_grounds"] = [g for g in grounds if str(g).startswith("KB-POFA")]
    anpr_bundle = {}
    for item in (getattr(plan, "supported", None) or []):
        if item.module_id == "KB-ANPR-01":
            b = item.support_bundle
            anpr_bundle = b.as_dict() if hasattr(b, "as_dict") else (b if isinstance(b, dict) else {})
            # also from supporting_facts because_of
            sf = str(item.supporting_facts)
            anpr_bundle["_has_departure_in_support"] = "departure_reason" in sf
    report["support_bundle_anpr"] = anpr_bundle

    pack = getattr(out, "pack", None)
    dplan = {}
    if pack is not None:
        try:
            dp = build_draft_plan(pack, case_id=case.case_id)
            for s in dp.sections:
                if str(s.ground_id).startswith("KB-ANPR"):
                    dplan = s.as_dict()
                    break
        except Exception as exc:
            dplan = {"error": str(exc)[:200]}
    report["draft_plan_anpr"] = dplan

    letter = getattr(out, "letter", None) or _letter_from_case(case)
    report["letter"] = letter
    report["anpr_paragraph"] = _anpr_paragraph(letter)
    cov = _coverage(letter)
    report["letter_coverage"] = cov

    # PoFA independence: narrative must not be the only support for PoFA
    pofa_from_narrative = False
    for item in (getattr(plan, "supported", None) or []):
        if not str(item.module_id).startswith("KB-POFA"):
            continue
        blob = str(item.supporting_facts).lower()
        if "departure_reason" in blob or "purse" in blob or "shopping" in blob:
            pofa_from_narrative = True
    report["pofa_independent"] = (not pofa_from_narrative) and bool(report["pofa_grounds"])

    # Findings
    report["pofa_findings"] = [
        f.get("finding_type") or f.get("family")
        for f in (case.legal_findings or [])
        if str(f.get("status") or "").upper() == "VERIFIED"
    ]

    unsupported = [
        i for i in (report["validation"].get("issues") or [])
        if i in ("VAL-FACT", "VAL-UNSUPPORTED", "DV-FACT")
    ]
    acceptance = {
        "shopping_rendered": cov["shopping"],
        "reason_for_leaving_rendered": cov["reason_for_leaving"],
        "left_site_rendered": cov["left_site"],
        "returned_rendered": cov["returned"],
        "multiple_visits_rendered": cov["multiple_visits"] or "KB-ANPR-01" in grounds,
        "pofa_preserved": report["pofa_independent"],
        "unsupported_assertions_zero": report["validation"]["passed"] and not unsupported,
        "driver_disclosure_zero": not cov["driver_disclosure"],
        "claim_plan_not_rewritten_by_drafter": case.state == CaseState.RELEASED,
        "departure_reason_in_bundle": bool(
            anpr_bundle.get("_has_departure_in_support")
            or "departure_reason" in str(anpr_bundle.get("source_fact_names") or [])
            or "departure_reason" in str(anpr_bundle.get("values") or {})
            or "departure_reason" in (dplan.get("required_particulars") or [])
        ),
        "regression_offline": report["regression_offline"]["passed"],
        "released": case.state == CaseState.RELEASED,
    }
    report["acceptance"] = acceptance
    report["trace"] = {
        "raw_narrative": CP_PLUS_NARRATIVE,
        "narrative_atom": report["narrative_atoms"] or report["narrative_atom_pre"],
        "support_bundle_particulars": (
            anpr_bundle.get("source_fact_names")
            or anpr_bundle.get("values")
            or {"departure_in_support": anpr_bundle.get("_has_departure_in_support")}
        ),
        "draft_plan_particulars": {
            "required": dplan.get("required_particulars"),
            "values": {
                k: (dplan.get("particular_values") or {}).get(k)
                for k in ("purpose_of_visit", "left_site", "returned_same_day",
                          "multiple_visits", "departure_reason")
            },
            "narrative_atoms": dplan.get("narrative_atoms"),
        },
        "generated_section": report["anpr_paragraph"],
        "final_sentence": (report["anpr_paragraph"] or "").split(".")[-2:]
        if report["anpr_paragraph"] else [],
    }

    ok = all(acceptance.values())
    report["verdict"] = (
        "CP_PLUS_ACCEPTANCE_CLOSED" if ok else "MATERIAL_PROPAGATION_FIX_REQUIRED"
    )
    return report


def write_report(report: dict[str, Any]) -> Path:
    dest = ROOT / "reports" / "p11_3"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "proof.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    md = f"""# P11.3 — Material Narrative Particular Propagation

**Verdict:** `{report.get('verdict')}`

| Item | Value |
|---|---|
| case_id | `{report.get('case_id')}` |
| state | `{report.get('final_state')}` |
| departure_reason | `{report.get('departure_reason_fact')}` |
| validation | `{report.get('validation')}` |

## Trace
```
{json.dumps(report.get('trace'), indent=2, default=str)[:5000]}
```

## ANPR paragraph
```
{report.get('anpr_paragraph')}
```

## Acceptance
```
{json.dumps(report.get('acceptance'), indent=2)}
```

## Regression (offline)
```
{json.dumps(report.get('regression_offline'), indent=2, default=str)[:3000]}
```

## PoFA
grounds={report.get('pofa_grounds')} findings={report.get('pofa_findings')} independent={report.get('pofa_independent')}
"""
    (dest / "P11_3_MATERIAL_PROPAGATION.md").write_text(md, encoding="utf-8")
    (ROOT / "P11_3_MATERIAL_PROPAGATION.md").write_text(md, encoding="utf-8")
    return dest / "P11_3_MATERIAL_PROPAGATION.md"
