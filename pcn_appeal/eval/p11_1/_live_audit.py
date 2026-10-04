"""P11.1 live audit of RELEASED cases + KB + vectors (read-only)."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _env() -> None:
    for name in (".env.staging.local", ".env.local"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("DATABASE_URL="):
                os.environ.setdefault("DATABASE_URL", line.split("=", 1)[1].strip())


def main() -> None:
    _env()
    from pcn_appeal.admin_db.connection import read_only_connect
    from pcn_appeal.admin_db import cases_view, vectors
    from pcn_appeal.store import kb_source
    from pcn_appeal.kg.graph import KnowledgeGraph
    from pcn_appeal.manifest import kb_digest

    out: dict = {}
    with read_only_connect() as conn:
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='cases' "
            "ORDER BY ordinal_position"
        ).fetchall()]
        out["case_columns"] = cols

        rows = conn.execute(
            "SELECT case_id, state, kb_release_id, commit_sha, route, "
            "document_type, stage, frontend_version, llm_provider, "
            "appeal_deadline, created_at "
            "FROM cases WHERE state = 'RELEASED' "
            "ORDER BY created_at DESC NULLS LAST LIMIT 5"
        ).fetchall()
        keys = ["case_id", "state", "kb_release_id", "commit_sha", "route",
                "document_type", "stage", "frontend_version", "llm_provider",
                "appeal_deadline", "created_at"]
        out["released_sample"] = [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v)
             for k, v in zip(keys, r)}
            for r in rows
        ]

        nulls = conn.execute("""
            SELECT
              count(*) FILTER (WHERE state='RELEASED') AS released_n,
              count(*) FILTER (WHERE state='RELEASED' AND kb_release_id IS NULL) AS kb_null,
              count(*) FILTER (WHERE state='RELEASED' AND commit_sha IS NULL) AS commit_null,
              count(*) FILTER (WHERE state='RELEASED' AND route IS NULL) AS route_null,
              count(*) FILTER (WHERE state='RELEASED' AND document_type IS NULL) AS doc_null,
              count(*) FILTER (WHERE state='RELEASED' AND stage IS NULL) AS stage_null,
              count(*) FILTER (WHERE state='RELEASED' AND frontend_version IS NULL) AS fe_null,
              count(*) FILTER (WHERE state='RELEASED' AND llm_provider IS NULL) AS llm_null,
              count(*) FILTER (WHERE state='RELEASED' AND appeal_deadline IS NULL) AS deadline_null
            FROM cases
        """).fetchone()
        out["released_nulls"] = dict(zip(
            ["released_n", "kb_null", "commit_null", "route_null", "doc_null",
             "stage_null", "fe_null", "llm_null", "deadline_null"],
            nulls,
        ))

        # Pick best case for E2E trace: prefer non-null kb_release_id
        pick = conn.execute(
            "SELECT case_id FROM cases WHERE state='RELEASED' "
            "AND kb_release_id IS NOT NULL "
            "ORDER BY created_at DESC NULLS LAST LIMIT 1"
        ).fetchone()
        if not pick:
            pick = conn.execute(
                "SELECT case_id FROM cases WHERE state='RELEASED' "
                "ORDER BY created_at DESC NULLS LAST LIMIT 1"
            ).fetchone()
        out["trace_case_id"] = str(pick[0]) if pick else None

        # Index audit
        idxs = conn.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename='kb_embeddings' AND indexdef ILIKE '%%hnsw%%'
            ORDER BY indexname
        """).fetchall()
        out["hnsw_indexes"] = [{"name": r[0], "definition": r[1]} for r in idxs]
        # Compare body after index name — names differ (idx, idx1…) even when
        # the opclass / column / method are identical accidental duplicates.
        import re
        bodies = {
            re.sub(r"CREATE INDEX \S+ ON", "CREATE INDEX <name> ON", r[1] or "", count=1)
            for r in idxs
        }
        out["hnsw_index_analysis"] = {
            "count": len(idxs),
            "distinct_index_bodies": len(bodies),
            "bodies": sorted(bodies),
            "verdict": (
                "ACCIDENTAL_DUPLICATES"
                if len(idxs) > 1 and len(bodies) == 1
                else ("MIXED_OR_DISTINCT" if len(idxs) > 1 else "SINGLE")
            ),
            "note": "Do not drop indexes in this audit; cleanup is a later change.",
        }

    if out.get("trace_case_id"):
        detail = cases_view.case_detail(out["trace_case_id"], show_sensitive=False)
        out["trace_summary"] = {
            "case_id": detail["case_id"],
            "state": (detail.get("case") or {}).get("state"),
            "kb_release_id": (detail.get("case") or {}).get("kb_release_id"),
            "commit_sha": (detail.get("case") or {}).get("commit_sha"),
            "n_facts": len(detail.get("all_facts") or []),
            "n_sources": len(detail.get("fact_sources") or []),
            "n_derived": len(detail.get("derived_facts") or []),
            "n_findings": len(detail.get("legal_findings") or []),
            "n_plans": len(detail.get("claim_plans") or []),
            "n_plan_items": len(detail.get("claim_plan_items") or []),
            "n_drafts": len(detail.get("drafts") or []),
            "n_validations": len(detail.get("validations") or []),
            "n_baselines": len(detail.get("document_baselines") or []),
            "broken_joins": [],
        }
        # Join checks
        case = detail.get("case") or {}
        cid = out["trace_case_id"]
        if not detail.get("claim_plans"):
            out["trace_summary"]["broken_joins"].append("missing_claim_plan")
        if not detail.get("drafts"):
            out["trace_summary"]["broken_joins"].append("missing_draft_version_or_draft")
        if not detail.get("all_facts"):
            out["trace_summary"]["broken_joins"].append("missing_facts")
        if case.get("state") == "RELEASED" and not case.get("kb_release_id"):
            out["trace_summary"]["broken_joins"].append("kb_release_id_null")
            out["trace_summary"]["legacy_class"] = "LEGACY_UNVERSIONED"
        if case.get("state") == "RELEASED" and not case.get("commit_sha"):
            out["trace_summary"]["broken_joins"].append("commit_sha_null")

    # KB release
    try:
        release = kb_source.load_release("kb-20261004T113657Z")
        kg = KnowledgeGraph.from_release(release)
        out["kb_release"] = {
            "release_id": kg.release_id,
            "release_digest": kg.release_digest or kb_digest(kg),
            "module_count": len(kg.modules),
            "module_versions": {mid: m.version for mid, m in list(kg.modules.items())[:8]},
            "module_versions_n": len(kg.modules),
            "created_at": release.get("published_at"),
            "published_by": release.get("published_by"),
            "embedder": (release.get("manifest") or {}).get("embedder")
            if isinstance(release.get("manifest"), dict)
            else None,
        }
    except Exception as exc:
        # fall back to latest
        try:
            release = kb_source.load_release()
            kg = KnowledgeGraph.from_release(release)
            out["kb_release"] = {
                "release_id": kg.release_id,
                "release_digest": kg.release_digest or kb_digest(kg),
                "module_count": len(kg.modules),
                "error_loading_pinned": str(exc)[:200],
                "created_at": release.get("published_at"),
            }
        except Exception as exc2:
            out["kb_release"] = {"error": str(exc2)[:300]}

    out["vector_health"] = vectors.health()

    dest = ROOT / "reports" / "p11_1"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "live_audit.json").write_text(
        json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "trace_case_id": out.get("trace_case_id"),
        "released_nulls": out.get("released_nulls"),
        "trace_summary": out.get("trace_summary"),
        "kb_release_id": (out.get("kb_release") or {}).get("release_id"),
        "hnsw": out.get("hnsw_index_analysis"),
        "vector_flags": (out.get("vector_health") or {}).get("flags"),
        "embedding_rows": (out.get("vector_health") or {}).get("total_embedding_rows"),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
