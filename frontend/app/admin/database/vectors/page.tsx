"use client";

import { useCallback, useEffect, useState } from "react";
import DataTable from "@/components/admin/DataTable";
import JsonBlock from "@/components/admin/JsonBlock";
import RefreshControls from "@/components/admin/RefreshControls";
import {
  vectorEmbeddings,
  vectorHealth,
  vectorSearch,
  vectorStatus,
} from "@/lib/admin_db";

export default function AdminVectorsPage() {
  const [status, setStatus] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [embeds, setEmbeds] = useState<any>(null);
  const [searchQ, setSearchQ] = useState("");
  const [searchOut, setSearchOut] = useState<any>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const [s, h, e] = await Promise.all([
        vectorStatus(),
        vectorHealth(),
        vectorEmbeddings({ show_raw_vector: showRaw }),
      ]);
      setStatus(s);
      setHealth(h);
      setEmbeds(e);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }, [showRaw]);

  useEffect(() => {
    load();
  }, [load]);

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      setSearchOut(await vectorSearch(searchQ, 10));
    } catch (ex: any) {
      setErr(String(ex?.message || ex));
    } finally {
      setBusy(false);
    }
  }

  if (status && !status.installed) {
    return (
      <div className="admin-stack">
        <h2>Vectors</h2>
        <div className="admin-card">
          <p className="admin-warn">PGVECTOR NOT CONFIGURED</p>
          <p>Vector tables: NONE</p>
          <p>Embedding rows: 0</p>
          <p className="admin-muted">This is not an error. No fake vectors are created.</p>
        </div>
      </div>
    );
  }

  const rows = embeds?.rows || [];
  const cols = rows[0] ? Object.keys(rows[0]).filter((c) => c !== "embedding_raw") : [];

  return (
    <div className="admin-stack">
      <div className="admin-row">
        <h2>Vectors / embeddings</h2>
        <RefreshControls onRefresh={load} busy={busy} />
      </div>
      {err && <p className="admin-error">{err}</p>}

      <section className="admin-card admin-grid-3">
        <div>
          <div className="admin-muted">pgvector</div>
          <div className="admin-stat">
            {status?.extname} {status?.extversion}
          </div>
        </div>
        <div>
          <div className="admin-muted">Embedding rows</div>
          <div className="admin-stat">{status?.embedding_rows ?? 0}</div>
        </div>
        <div>
          <div className="admin-muted">Vector columns</div>
          <div className="admin-stat">{(status?.columns || []).length}</div>
        </div>
      </section>

      <section className="admin-card">
        <h3>Vector columns</h3>
        <DataTable
          columns={["schema", "table", "column", "dimensions", "non_null_row_count"]}
          rows={status?.columns || []}
        />
        <h3>Indexes</h3>
        <DataTable
          columns={["schema", "table", "index_name", "index_type", "definition"]}
          rows={status?.indexes || []}
        />
      </section>

      <section className="admin-card">
        <h3>Vector health</h3>
        <JsonBlock value={health} />
        {(health?.flags || []).length > 0 && (
          <p className="admin-warn">Flags: {(health.flags || []).join(", ")}</p>
        )}
      </section>

      <section className="admin-card">
        <div className="admin-row">
          <h3>Embeddings</h3>
          <label className="admin-check">
            <input
              type="checkbox"
              checked={showRaw}
              onChange={(e) => setShowRaw(e.target.checked)}
            />
            Show raw vector
          </label>
        </div>
        <DataTable columns={cols} rows={rows} />
      </section>

      <section className="admin-card admin-stack">
        <h3>Similarity search (debug)</h3>
        <p className="admin-muted">
          Retrieval candidates only — does not alter Claim Plan or case state.
        </p>
        <form onSubmit={runSearch} className="admin-row">
          <input
            className="admin-input"
            style={{ flex: 1 }}
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            placeholder="Plain text query"
          />
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Search
          </button>
        </form>
        {(searchOut?.results || []).map((r: any) => (
          <div key={r.rank} className="admin-sim-card">
            <strong>
              #{r.rank} {r.module_id || r.entity_id}
            </strong>
            <div className="admin-pipeline">
              VECTOR MATCH ({r.similarity != null ? r.similarity.toFixed(3) : "—"})
              <span>↓</span>
              TYPED ELIGIBILITY ({r.eligibility})
              <span>↓</span>
              MODULE ROLE ({r.module_role || "—"})
              <span>↓</span>
              FINAL STATUS ({r.claim_plan_status})
            </div>
            <p className="admin-muted">{r.source_text}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
