"""One-shot live discovery for the admin DB explorer deliverable."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _load_env() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in (".env.staging.local", ".env.local", ".env"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("\"'")
            if k and k not in os.environ:
                os.environ[k] = v


def main() -> None:
    _load_env()
    from pcn_appeal.admin_db import catalog, cases_view, knowledge_view, vectors

    st = catalog.status()
    print("=== STATUS ===")
    print(json.dumps(st, indent=2, default=str))

    vt = catalog.vector_columns()
    print("=== VECTOR COLUMNS ===")
    print(json.dumps(vt, indent=2, default=str))

    vh = vectors.health()
    print("=== VECTOR HEALTH ===")
    print(json.dumps(vh, indent=2, default=str)[:4000])

    vi = catalog.vector_indexes()
    print("=== VECTOR INDEXES ===")
    print(json.dumps(vi, indent=2, default=str)[:3000])

    tabs = catalog.list_tables(include_counts=True)
    print("=== TABLE COUNTS (important + all n) ===")
    print("total_tables", tabs["n"])
    for t in sorted(tabs["tables"], key=lambda x: x["table"]):
        if t.get("important") or t["table"] in {
            "cases", "facts", "fact_sources", "fact_history", "claim_plans",
            "kb_embeddings", "kb_modules", "legal_findings", "draft_versions",
            "validations", "master_case_state", "document_baselines",
        }:
            print(f"  {t['schema']}.{t['table']}: {t.get('row_count')}")

    kb = knowledge_view.list_knowledge(page_size=2)
    print("=== KB ===", kb.get("table"), "total", kb.get("total"), kb.get("kb_release"))

    cases = cases_view.case_overview(limit=3)
    print("=== CASES SAMPLE ===", len(cases.get("cases") or []))

    if vh.get("installed") and (vh.get("total_embedding_rows") or 0) > 0:
        sim = vectors.similarity_search("late notice keeper liability", limit=3)
        print("=== SIM ===")
        print(json.dumps(sim, indent=2, default=str)[:2500])
    else:
        print("=== SIM === skipped")


if __name__ == "__main__":
    main()
