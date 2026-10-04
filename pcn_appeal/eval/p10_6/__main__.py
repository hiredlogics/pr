"""python -m pcn_appeal.eval.p10_6

P10.6 draft fidelity evaluation:
  build dataset → DEVELOPMENT drafting metrics → fresh drafting holdout →
  P8/P10 invariants → optional real-model probe.

Semantic metrics are recorded as frozen observations only (not remediated).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from pcn_appeal.eval.p10_4.freeze import write_freeze
from pcn_appeal.eval.p10_4.invariants import run_p8_p10_invariants
from pcn_appeal.eval.p10_4.metrics import score_case, score_drafting
from pcn_appeal.eval.p10_4.pipeline import load_json_cases, run_once
from pcn_appeal.drafting.plan import DRAFT_PLAN_VERSION, build_draft_plan, coverage_trace
from pcn_appeal.prompts import version as prompt_version

from .build_dataset import main as build_dataset

REPORT = ROOT / "reports" / "p10_6"
DS = ROOT / "datasets" / "p10_6_v1"

# Frozen semantic observations from P10.5 (do not mix remediation).
FROZEN_SEMANTIC = {
    "DEV_semantic_recall": 0.794,
    "VAL_semantic_recall": 0.733,
    "sealed_holdout_semantic_recall": 1.0,
    "note": "Existing P10.5 observations — not changed in P10.6",
}


def _mean_rate(rows: list[dict], key: str) -> float | None:
    vals = []
    for r in rows:
        v = (r.get("drafting") or {}).get(key)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _run_split(split: str) -> dict:
    directory = DS / split
    cases = load_json_cases(directory)
    rows = []
    for spec in cases:
        actual = run_once(spec)
        # strip private keys
        public = {k: v for k, v in actual.items() if not k.startswith("_")}
        scored = score_case(spec.get("expected") or {}, public)
        drafting = score_drafting(spec.get("expected") or {}, public)
        # First-defective-layer analysis
        pack = getattr(actual.get("_out"), "pack", None) if actual.get("_out") else None
        draft = getattr(actual.get("_out"), "draft", None) if actual.get("_out") else None
        layer = {
            "claim_plan_complete": bool(public.get("supported_grounds")),
            "support_bundle_complete": bool(public.get("support_bundle_complete")),
            "draft_requirements_complete": bool(
                (public.get("draft_context") or {}).get("draft_requirements")),
            "draft_context_complete": bool(public.get("draft_context_complete")),
            "state": public.get("state"),
            "letter_empty": public.get("letter_empty"),
        }
        trace_rows = []
        if pack is not None:
            try:
                plan = build_draft_plan(pack, case_id=spec["case_id"])
                trace_rows = coverage_trace(plan, draft, pack)
                layer["draft_plan_sections"] = len(plan.sections)
                layer["draft_plan_version"] = plan.version
            except Exception as exc:  # noqa: BLE001
                layer["draft_plan_error"] = str(exc)[:200]
        rendering_errors = [
            r for r in trace_rows if r.get("verdict") == "DRAFT_RENDERING_ERROR"
        ]
        if public.get("state") == "RELEASED" and not rendering_errors:
            layer["first_defective_layer"] = "NONE"
        elif public.get("state") == "RELEASED" and drafting.get("draft_ground_coverage") == 1.0:
            # Released with full ground coverage: residual particular soft-misses
            # are tracked in metrics, not as a blocking first defective layer.
            layer["first_defective_layer"] = (
                "DRAFT_PARTICULAR_SOFT_MISS" if rendering_errors else "NONE"
            )
        elif (layer["claim_plan_complete"] and layer["support_bundle_complete"]
                and layer["draft_context_complete"] and rendering_errors):
            layer["first_defective_layer"] = "DRAFT_RENDERING_ERROR"
        elif not layer["claim_plan_complete"]:
            layer["first_defective_layer"] = "CLAIM_PLAN"
        elif not layer["support_bundle_complete"]:
            layer["first_defective_layer"] = "SUPPORT_BUNDLE"
        elif not layer["draft_context_complete"]:
            layer["first_defective_layer"] = "DRAFT_CONTEXT"
        elif public.get("state") != "RELEASED" and (
                (spec.get("expected") or {}).get("must_not_release_empty_appeal")):
            layer["first_defective_layer"] = "NO_GROUND_PATH_OK"
        else:
            layer["first_defective_layer"] = "NONE"

        # No-ground acceptance
        exp = spec.get("expected") or {}
        if exp.get("must_not_release_empty_appeal"):
            ok_hold = public.get("state") != "RELEASED" or public.get("letter_empty")
            drafting = dict(drafting)
            drafting["no_ground_hold_ok"] = bool(ok_hold)
            drafting["passed"] = bool(ok_hold)

        row = {
            "case_id": spec["case_id"],
            "family": spec.get("family"),
            "actual": {
                "state": public.get("state"),
                "supported_grounds": public.get("supported_grounds"),
                "validation_passed": public.get("validation_passed"),
                "letter_preview": (public.get("letter") or "")[:400],
                "validation_issues": public.get("validation_issues"),
            },
            "drafting": drafting,
            "layer": layer,
            "coverage_trace": trace_rows,
            "score": scored,
        }
        rows.append(row)
        dest = REPORT / split / f"{spec['case_id']}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(row, indent=2, default=str) + "\n", encoding="utf-8")

    clean = [r for r in rows if (r.get("drafting") or {}).get("clean_upstream")]
    hold_cases = [r for r in rows if (r.get("drafting") or {}).get("no_ground_hold_ok") is not None]
    summary = {
        "split": split,
        "n_cases": len(rows),
        "n_clean_upstream": len(clean),
        "ground_coverage": _mean_rate(clean, "draft_ground_coverage"),
        "material_fact_coverage": _mean_rate(clean, "material_fact_coverage"),
        "required_particular_coverage": _mean_rate(clean, "required_particular_coverage"),
        "unsupported_assertion_rate": _mean_rate(clean, "unsupported_assertion_rate"),
        "validator_pass_rate": (
            sum(1 for r in clean if (r.get("drafting") or {}).get("validation_passed"))
            / len(clean) if clean else None
        ),
        "no_ground_hold_ok_rate": (
            sum(1 for r in hold_cases if r["drafting"].get("no_ground_hold_ok"))
            / len(hold_cases) if hold_cases else None
        ),
        "first_defective_layers": {
            k: sum(1 for r in rows if (r.get("layer") or {}).get("first_defective_layer") == k)
            for k in sorted({(r.get("layer") or {}).get("first_defective_layer") for r in rows})
        },
        "cases": rows,
    }
    (REPORT / split / "aggregate.json").write_text(
        json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2, default=str)
        + "\n", encoding="utf-8")
    return summary


def _real_model_probe() -> dict:
    """Non-production probe against intended provider when credentials exist."""
    from pcn_appeal.llm import DemoLLM, OpenAIClient, default_client, probe

    info = probe()
    result = {
        "provider": info.get("provider"),
        "models": info.get("models") or {},
        "reason": info.get("reason") or "",
        "prompt_version": prompt_version("drafting"),
        "temperature": None,
        "structured_output_valid": None,
        "ground_coverage": None,
        "latency_ms": None,
        "cost": None,
        "ran": False,
    }
    if info.get("provider") != "openai":
        result["note"] = "No live OpenAI credentials in this environment; DemoLLM/reference only."
        return result
    # Minimal structured DraftPlan render probe — not a production deploy.
    try:
        client = default_client()
        if isinstance(client, DemoLLM):
            result["note"] = "Fell back to DemoLLM"
            return result
        plan_payload = {
            "draft_plan": {
                "case_id": "PROBE_P10_6",
                "draft_plan_version": DRAFT_PLAN_VERSION,
                "sections": [{
                    "section_id": "S01",
                    "ground_id": "KB-PAY-01",
                    "ground_ids": ["KB-PAY-01"],
                    "purpose": "Express payment ground",
                    "required_particulars": ["payment_made"],
                    "particular_values": {"payment_made": True},
                    "supporting_fact_ids": ["F1"],
                    "prohibited_claims": [],
                }],
                "introduction_requirements": ["Identify as registered keeper"],
                "closing_requirements": ["Clear cancellation request"],
            },
            "verified_facts": {
                "vrm": "AB12CDE", "pcn_number": "PROBE1",
                "payment_made": True, "operator_name": "Probe Parking",
            },
            "fact_refs": {"payment_made": "F1", "vrm": "F2", "pcn_number": "F3"},
            "fact_basis": {"payment_made": "CUSTOMER_ACCOUNT"},
            "driver_status": "UNIDENTIFIED",
            "driver_rule": "Write about the keeper and the vehicle.",
            "module_ids": ["KB-PAY-01"],
            "case_context": {"operator_name": "Probe Parking", "vrm": "AB12CDE",
                            "pcn_number": "PROBE1"},
            "context_chunks": [],
            "verified_legal_findings": [],
            "pofa_findings": [],
        }
        from pcn_appeal import prompts
        t0 = time.time()
        out = client.complete_json(
            task="drafting",
            system=prompts.system("drafting"),
            user=json.dumps(plan_payload),
        )
        latency = int((time.time() - t0) * 1000)
        valid = bool(out.get("sections") or out.get("paragraphs"))
        text = ""
        if out.get("sections"):
            text = " ".join(s.get("text") or "" for s in out["sections"])
        elif out.get("paragraphs"):
            text = " ".join(
                s.get("text") or "" for p in out["paragraphs"] for s in p
            )
        covered = 1.0 if ("pay" in text.lower() or "payment" in text.lower()) else 0.0
        result.update({
            "ran": True,
            "model": (getattr(client, "models", None) or {}).get("drafting"),
            "temperature": getattr(client, "temperature", None),
            "structured_output_valid": valid,
            "ground_coverage": covered,
            "latency_ms": latency,
            "cost": "not_metered_in_probe",
        })
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result


def _recommendation(dev: dict, holdout: dict, invariants: dict) -> str:
    g = dev.get("ground_coverage")
    p = dev.get("required_particular_coverage")
    u = dev.get("unsupported_assertion_rate")
    inv_ok = bool(invariants.get("passed"))
    hold_g = holdout.get("ground_coverage")
    if (g == 1.0 and (p is None or p >= 0.99) and (u in (None, 0.0))
            and inv_ok and (hold_g in (None, 1.0) or (hold_g is not None and hold_g >= 0.9))):
        return "READY_FOR_STAGING"
    if g == 1.0 and inv_ok and (u in (None, 0.0)):
        # DraftPlan contract solved clean-upstream ground coverage; live provider
        # not probed or residual particular soft-misses remain.
        return "MODEL_EVALUATION_REQUIRED"
    if g is not None and g < 0.9:
        return "MORE_DRAFT_MODEL_WORK"
    if not inv_ok:
        return "MORE_DRAFT_MODEL_WORK"
    return "MODEL_EVALUATION_REQUIRED"


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    build_dataset()
    freeze = write_freeze(REPORT / "freeze")
    print("freeze:", freeze.get("git_commit") if isinstance(freeze, dict) else freeze)

    print("Running DEVELOPMENT drafting suite...")
    dev = _run_split("development")
    print("Running fresh drafting HOLDOUT...")
    holdout = _run_split("holdout")

    print("Running P8/P10 invariants...")
    invariants = run_p8_p10_invariants()
    if not isinstance(invariants, dict):
        invariants = {"passed": bool(invariants), "raw": str(invariants)[:200]}
    (REPORT / "invariants.json").write_text(
        json.dumps(invariants, indent=2, default=str) + "\n", encoding="utf-8")

    probe = _real_model_probe()
    reco = _recommendation(dev, holdout, invariants)

    aggregate = {
        "phase": "P10.6",
        "draft_plan_version": DRAFT_PLAN_VERSION,
        "prompt_version_drafting": prompt_version("drafting"),
        "frozen_semantic_observations": FROZEN_SEMANTIC,
        "development": {k: v for k, v in dev.items() if k != "cases"},
        "holdout": {k: v for k, v in holdout.items() if k != "cases"},
        "invariants": invariants,
        "real_model_probe": probe,
        "recommendation": reco,
    }
    (REPORT / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, default=str) + "\n", encoding="utf-8")

    # Markdown report
    md = _render_report(aggregate, dev, holdout)
    (ROOT / "P10_6_EVALUATION_REPORT.md").write_text(md, encoding="utf-8")
    (REPORT / "P10_6_EVALUATION_REPORT.md").write_text(md, encoding="utf-8")
    print("recommendation:", reco)
    print("report:", ROOT / "P10_6_EVALUATION_REPORT.md")
    return 0


def _pct(v) -> str:
    if v is None:
        return "N/A"
    return f"{100.0 * float(v):.1f}%"


def _render_report(agg: dict, dev: dict, holdout: dict) -> str:
    layers = dev.get("first_defective_layers") or {}
    probe = agg.get("real_model_probe") or {}
    inv = agg.get("invariants") or {}
    return f"""# P10.6 — Draft Fidelity + Claim Plan Rendering Contract

Make the drafting layer faithfully render every supported Claim Plan ground
and every required material particular. No P8 redesign, no ground-selection
change, no semantic ontology change, no fine-tune, no pgvector, no deploy.

## 1. First-defective-layer analysis

On clean-upstream DEVELOPMENT cases, when Claim Plan / SupportBundle /
DraftRequirement / DraftContext are complete but prose misses a ground, the
defect is classified **DRAFT_RENDERING_ERROR**.

DEVELOPMENT layer counts:
```json
{json.dumps(layers, indent=2)}
```

Trace shape: GROUND → SupportBundle → DraftRequirement → DraftContext →
DraftSection → Generated paragraph → Final sentence link.

## 2. DraftPlan schema

Version: `{DRAFT_PLAN_VERSION}`

```
DraftPlan {{
  case_id, claim_plan_id, version,
  sections: [DraftSection...],
  introduction_requirements[], closing_requirements[],
  leading_ground_ids[], support_only_ids[]
}}
DraftSection {{
  section_id, ground_id, ground_ids[], purpose, role,
  supporting_fact_ids[], supporting_fact_names[],
  derived_fact_ids[], evidence_ids[], legal_finding_ids[],
  required_particulars[], particular_values{{}},
  prohibited_claims[], required_outcome,
  support_module_ids[], context_chunk_ids[], merged
}}
```

## 3. Claim Plan → DraftPlan transformation

`pcn_appeal.drafting.plan.build_draft_plan(pack)`:
- One DraftSection per leading / substantive(+evidence) ground
- Explicit permitted merge: PAYMENT + KEYING → single section (`merged=true`)
- SUPPORTING_PROPOSITION / LEGAL_CONCLUSION attach as `support_module_ids`, not standalone paragraphs
- Particulars taken from DraftRequirement + SupportBundle values present in the locked plan

## 4. LLM structured-output contract

Prompt drafting **v{agg.get('prompt_version_drafting')}**. Preferred output:

```json
{{"opening": "...", "sections": [{{"section_id","ground_ids","text","fact_ids_used","finding_ids_used"}}], "closing": "..."}}
```

Legacy `paragraphs` still accepted. Assembler: `assemble_structured_draft`.

## 5. Section / ground ownership

Every substantive sentence carries `module_refs` = section `ground_ids`.
`Draft.section_ownership` records `merged_ground_ids` when applicable.
Merged grounds must both be semantically expressed.

## 6. Validators changed

- **VAL-GROUND-COVERAGE** (new / strengthened): DraftSection exists + rendered text + semantic expression; filler does not count; BLOCK
- **VAL-COVERAGE**: retained; also flags DraftPlan grounds without linked text
- **VAL-DRAFT-PARTICULARS**: reports `ground_id`, `section_id`, missing particular, supporting fact ids
- **VAL-INVENTED** / unsupported assertion path unchanged (target 0%)

## 7. Targeted regeneration

On coverage/particulars failure, orchestrator calls `LLMDrafter.regenerate_sections`
for failed section_ids only (max 2 section retries). Claim Plan is not modified.

## 8–12. DEVELOPMENT before/after draft metrics

| Metric | DEVELOPMENT (after) |
| --- | --- |
| Clean-upstream ground coverage | {_pct(dev.get('ground_coverage'))} |
| Material-fact coverage | {_pct(dev.get('material_fact_coverage'))} |
| Required-particular coverage | {_pct(dev.get('required_particular_coverage'))} |
| Unsupported assertion rate | {_pct(dev.get('unsupported_assertion_rate'))} |
| Validator pass rate (clean) | {_pct(dev.get('validator_pass_rate'))} |
| No-ground hold OK | {_pct(dev.get('no_ground_hold_ok_rate'))} |

P10.5 clean-upstream draft ground coverage baseline (evidence): **20%**.

## 13. Real-model probe

```json
{json.dumps(probe, indent=2, default=str)}
```

## 14. P8/P10 invariant results

```json
{json.dumps({k: inv.get(k) for k in ('passed', 'failures', 'summary') if k in inv} or inv, indent=2, default=str)}
```

## 15. Fresh sealed drafting-holdout result

| Metric | HOLDOUT |
| --- | --- |
| Ground coverage (clean) | {_pct(holdout.get('ground_coverage'))} |
| Material-fact coverage | {_pct(holdout.get('material_fact_coverage'))} |
| Required-particular coverage | {_pct(holdout.get('required_particular_coverage'))} |
| Unsupported assertion rate | {_pct(holdout.get('unsupported_assertion_rate'))} |
| Cases | {holdout.get('n_cases')} |

## Frozen semantic observations (unchanged)

```json
{json.dumps(FROZEN_SEMANTIC, indent=2)}
```

## Recommendation

**{agg.get('recommendation')}**

Do not fine-tune. Do not add pgvector. Do not production deploy. STOP after evaluation.
"""


if __name__ == "__main__":
    raise SystemExit(main())
