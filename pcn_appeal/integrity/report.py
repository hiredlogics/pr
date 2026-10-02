"""CASE_REPORT.md - the AI audit report for one run (P5.5 §9). Admin only.

Reads the execution trace, the fact graph, the knowledge match, the question
reviews, the claim plan, the validation result and the integrity checks. Keeper
name and address values are never printed; the report is about the reasoning,
not the person.
"""
from __future__ import annotations

from typing import Any, Optional

from .checks import check_case, passed
from .trace import execution_trace, run_audit

REDACTED = ("keeper_name", "keeper_address", "customer_described_event",
            "material_account_summary", "material_account_points")


def _v(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def case_report(case, out=None, kg=None, checks: Optional[list[dict]] = None) -> str:
    trace = execution_trace(case)
    checks = checks if checks is not None else check_case(case, out, kg)
    audit = run_audit(case)
    v = trace["versions"]
    lines = [f"# Case {case.case_id} - run {trace['run_id']}", "",
             f"Result: **{'PASS' if passed(checks) else 'FAIL'}**  ",
             f"State: {trace['final_state']}  ",
             f"Execution: `{trace['execution_id']}`", "",
             "## Versions", "",
             f"- Code: `{v.get('code')}`",
             f"- KB: `{v.get('kb')}` (release {v.get('kb_release')}, relations "
             f"{v.get('relations_version')})",
             f"- Prompts: {', '.join(f'{k} v{n}' for k, n in sorted((v.get('prompts') or {}).items()))}",
             f"- Models: {', '.join(f'{k}={m}' for k, m in sorted((v.get('models') or {}).items()))}"
             f" (provider {v.get('provider')})",
             f"- Validator: {v.get('validator')}", "",
             "## Timeline", "", "| Stage | Status | Duration | Detail |", "|---|---|---|---|"]
    for st in trace["steps"]:
        detail = {k: val for k, val in st.items()
                  if k not in ("stage", "status", "events", "errors", "started_at", "ended_at",
                               "duration_ms", "held")}
        if st.get("errors"):
            detail["errors"] = st["errors"]
        if st.get("held"):
            detail["held"] = st["held"]
        dur = "" if st.get("duration_ms") is None else f"{st['duration_ms']} ms"
        lines.append(f"| {st['stage']} | {st['status']} | {dur} | "
                     f"{'; '.join(f'{k}={_v(val)}' for k, val in detail.items())} |")
    lines += ["", "## State history", ""]
    for t in trace["state_history"] or []:
        after = f" (after {t['preceded_by']})" if t.get("preceded_by") else ""
        lines.append(f"- {t['from']} -> {t['to']}: {t.get('reason') or ''}{after}")
    if not trace["state_history"]:
        lines.append("- (no transition in this run)")

    lines += ["", "## Facts", "", "| Fact | Value | Status | Source |", "|---|---|---|---|"]
    for name, f in sorted(case.facts.items()):
        if not f.usable:
            continue
        value = "(redacted)" if name in REDACTED else _v(f.value)
        lines.append(f"| {name} | {value} | {f.status.value} | {f.source.kind.value}: "
                     f"{_v(f.source.ref)} |")
    writes = [h for h in getattr(case, "fact_history", []) or [] if h.get("run_id") == case.run_id]
    if writes:
        lines += ["", f"Fact writes this run: {len(writes)} "
                  f"({sum(1 for h in writes if h.get('outcome') == 'CONFLICT')} conflicts)"]

    km = next((a for a in reversed(audit) if a.get("event") == "knowledge_match"), None)
    lines += ["", "## Knowledge", ""]
    if km:
        for row in km.get("selected") or []:
            lines.append(f"- {row['module']}: SUPPORTS via {', '.join(row.get('selected_because') or [])}")
        for row in km.get("rejected") or []:
            if row.get("status") == "BLOCKED":
                lines.append(f"- {row['module']}: BLOCKS - {row.get('reason')}")
        lines.append(f"- relevant: {len(km.get('relevant') or [])}, "
                     f"rejected: {len(km.get('rejected') or [])}")
    else:
        lines.append("- (no knowledge match in this run)")

    lines += ["", "## Questions", ""]
    any_q = False
    for a in audit:
        if a.get("event") != "question_review":
            continue
        any_q = True
        label = a.get("decision") or "?"
        if label == "APPROVED":
            label = "ASKED" if a.get("shown") else "APPROVED (held)"
        lines.append(f"- {label} {a.get('fact')}: {a.get('reason') or ''}")
    if not any_q:
        lines.append("- None")

    lines += ["", "## Claim Plan", ""]
    cp = trace.get("claim_plan")
    if cp:
        lines.append(f"Version {cp['version']} ({cp['status']}), approved: "
                     f"{', '.join(cp['approved']) or 'none'}")
        lines += [""] + [f"- {t}" for t in cp["trace"]]
    else:
        lines.append("- (no claim plan in this run)")

    lines += ["", "## AI calls", "", "| Task | Model | Prompt | Status | ms | in/out sha |",
              "|---|---|---|---|---|---|"]
    for c in trace["ai_calls"]:
        lines.append(f"| {c['task']} | {c.get('model')} | v{c.get('prompt_version')} | "
                     f"{c.get('status')} | {c.get('duration_ms')} | "
                     f"{(c.get('input_sha256') or '')[:8]}/{(c.get('output_sha256') or '')[:8]} |")

    lines += ["", "## Validation", ""]
    val = getattr(out, "validation", None)
    if val is not None:
        lines.append(f"{'PASS' if val.passed else 'FAIL'}")
        for i in val.issues:
            lines.append(f"- {i.rule} ({i.severity}): {i.message}")
    else:
        lines.append("- (no validation in this run)")
    outcome = getattr(out, "outcome", None)
    if outcome:
        lines += ["", f"Customer outcome: {outcome}"]

    # P6: the drafts written this run, each tied to its claim plan, with how
    # every sentence was grounded and what the shadow judge said.
    versions = [v for v in getattr(case, "draft_versions", []) or [] if v.get("run_id") == case.run_id]
    lines += ["", "## Legal findings", ""]
    findings = getattr(case, "legal_findings", []) or []
    if findings:
        lines += ["| Finding | Status | Facts | Calculation |", "|---|---|---|---|"]
        for f in findings:
            facts = ", ".join(e.get("fact", "") for e in f.get("supporting_facts") or []) or "-"
            calc = f.get("calculation_result") or {}
            shown = "; ".join(f"{k}={v}" for k, v in sorted(calc.items())
                              if k in ("deadline", "presumed_delivery", "days_between",
                                       "pofa_route", "note", "defects"))
            lines.append(f"| {f.get('finding_type')} | {f.get('status')} | {facts} | {shown or '-'} |")
    else:
        lines.append("- (no legal findings assessed)")

    lines += ["", "## Drafts", ""]
    if versions:
        lines += ["| Version | Draft | Claim plan | Attempt | Validation | Released | Grounding | Shadow judge |",
                  "|---|---|---|---|---|---|---|---|"]
        for v in versions:
            g = {}
            for row in v.get("grounding") or []:
                g[row["status"]] = g.get(row["status"], 0) + 1
            judge = (v.get("judge") or {}).get("status") or "-"
            lines.append(f"| {v['version']} | {v['draft_id'][:8]} ({v['content_hash'][:8]}) | "
                         f"{(v.get('claim_plan_id') or '')[:8]} | {v.get('attempt')} | "
                         f"{v['validation_status']} | {'yes' if v.get('released') else 'no'} | "
                         f"{', '.join(f'{k}={n}' for k, n in sorted(g.items())) or '-'} | {judge} |")
    else:
        lines.append("- (no draft written in this run)")

    lines += ["", "## Integrity checks", "", "| Check | Status | Detail |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['check']} | {c['status']} | {_v(c.get('detail')) if c['status'] == 'FAIL' else ''} |")
    return "\n".join(lines) + "\n"


__all__ = ["case_report"]
