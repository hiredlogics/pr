"""Where the controlled document and the live YAML disagree.

The YAML is what reasoning runs on; the document is the controlled source. A
difference is reported, never resolved here: a reviewer decides which side is
right. Severity:

  HIGH     a live module the document does not contain, or a document module
           with no compiled (machine-evaluable) form
  MEDIUM   the core proposition differs
  LOW      the name differs; the document lists evidence the YAML does not
  INFO     the document asks the AI to check a fact the compiled gate never reads
"""
from __future__ import annotations

import re
from typing import Optional

from .extract import ModuleRecord
from .parser import ParsedDocument


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def report(doc: ParsedDocument, records: list[ModuleRecord], kg) -> list[dict]:
    out: list[dict] = []
    in_doc = {m.module_id for m in doc.modules}
    for r in sorted(records, key=lambda r: r.module_id):
        mid, ym = r.module_id, kg.modules.get(r.module_id)
        if mid not in in_doc:
            out.append({"module_id": mid, "kind": "NOT_IN_CONTROLLED_DOCUMENT",
                        "severity": "HIGH", "yaml": ym.topic if ym else None,
                        "note": "live in kb_modules.yaml, absent from the controlled document"})
            continue
        if ym is None:
            out.append({"module_id": mid, "kind": "NOT_COMPILED", "severity": "HIGH",
                        "docx": r.name, "note": "in the document, no machine-evaluable gate"})
            continue
        if _norm(r.name) != _norm(ym.topic):
            out.append({"module_id": mid, "kind": "NAME", "severity": "LOW",
                        "docx": r.name, "yaml": ym.topic})
        docx_core = doc.module(mid).fields.get("CORE PROPOSITION")
        if docx_core and _norm(docx_core) != _norm(ym.core_proposition):
            out.append({"module_id": mid, "kind": "CORE_PROPOSITION", "severity": "MEDIUM",
                        "docx": docx_core, "yaml": ym.core_proposition})
        docx_kinds = {e["requirement"]["evidence_kind"] for e in r.evidence
                      if e["requirement"]["source"] == "DOCX" and e["requirement"]["evidence_kind"]}
        missing = sorted(docx_kinds - set(ym.evidence_helpful or []))
        if missing:
            out.append({"module_id": mid, "kind": "EVIDENCE", "severity": "LOW",
                        "docx": missing, "yaml": list(ym.evidence_helpful or []),
                        "note": "document lists evidence the YAML does not"})
        compiled = {f["fact_name"] for f in r.required_facts
                    if f["requirement_type"] in ("GATE", "REQUIRED")}
        checks = sorted({f["fact_name"] for f in r.required_facts
                         if f["requirement_type"] == "CHECK"} - compiled)
        if checks:
            out.append({"module_id": mid, "kind": "CHECK_NOT_COMPILED", "severity": "INFO",
                        "docx": checks, "note": "AI MUST CHECK names facts the gate never reads"})
    return out


def summary(drift: list[dict]) -> dict:
    by: dict[str, int] = {}
    for d in drift:
        by[d["severity"]] = by.get(d["severity"], 0) + 1
    return by


__all__ = ["report", "summary"]
