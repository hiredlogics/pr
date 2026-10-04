"use client";

import { useCallback, useEffect, useState } from "react";
import DataTable from "@/components/admin/DataTable";
import JsonBlock from "@/components/admin/JsonBlock";
import RefreshControls from "@/components/admin/RefreshControls";
import { dbStatus, diagnostics, diagnosticsList, vectorHealth } from "@/lib/admin_db";

export default function AdminHealthPage() {
  const [status, setStatus] = useState<any>(null);
  const [vhealth, setVhealth] = useState<any>(null);
  const [queries, setQueries] = useState<any[]>([]);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const [s, v, d] = await Promise.all([dbStatus(), vectorHealth(), diagnosticsList()]);
      setStatus(s);
      setVhealth(v);
      setQueries(d.queries || []);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function run(id: string) {
    setBusy(true);
    setErr("");
    try {
      setResult(await diagnostics(id));
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-stack">
      <div className="admin-row">
        <h2>Database health</h2>
        <RefreshControls onRefresh={load} busy={busy} />
      </div>
      {err && <p className="admin-error">{err}</p>}

      <section className="admin-card">
        <h3>Connection</h3>
        <JsonBlock value={status} />
      </section>

      <section className="admin-card">
        <h3>Vector health</h3>
        <JsonBlock value={vhealth} />
      </section>

      <section className="admin-card admin-stack">
        <h3>Read-only diagnostics</h3>
        <p className="admin-muted">
          Predefined SELECT queries only. No UPDATE/DELETE/DDL. Arbitrary SQL is
          SELECT-only with timeout and row limit when used via API.
        </p>
        <div className="admin-row" style={{ flexWrap: "wrap" }}>
          {queries.map((q) => (
            <button
              key={q.id}
              type="button"
              className="btn btn-secondary"
              onClick={() => run(q.id)}
            >
              {q.description || q.id}
            </button>
          ))}
        </div>
        {result && (
          <>
            <p className="admin-muted">
              {result.id} · {result.ok ? "ok" : result.error} · {result.query_ms} ms
            </p>
            <DataTable columns={result.columns || []} rows={result.rows || []} />
          </>
        )}
      </section>
    </div>
  );
}
