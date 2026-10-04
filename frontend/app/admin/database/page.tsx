"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import RefreshControls from "@/components/admin/RefreshControls";
import { dbStatus, dbTables, vectorStatus } from "@/lib/admin_db";

export default function DatabaseOverviewPage() {
  const [status, setStatus] = useState<any>(null);
  const [tables, setTables] = useState<any>(null);
  const [vector, setVector] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    // Load independently so a slow tables scan cannot blank the connection card.
    const settled = await Promise.allSettled([
      dbStatus(),
      dbTables(true), // estimated counts (fast)
      vectorStatus(),
    ]);
    const errors: string[] = [];
    if (settled[0].status === "fulfilled") setStatus(settled[0].value);
    else errors.push(`status: ${settled[0].reason}`);
    if (settled[1].status === "fulfilled") setTables(settled[1].value);
    else errors.push(`tables: ${settled[1].reason}`);
    if (settled[2].status === "fulfilled") setVector(settled[2].value);
    else errors.push(`vector: ${settled[2].reason}`);
    if (errors.length) setErr(errors.join(" · "));
    setBusy(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const important = (tables?.tables || []).filter((t: any) => t.important);
  const connected = Boolean(status?.connected);

  return (
    <div className="admin-stack">
      <div className="admin-row">
        <h2>PostgreSQL overview</h2>
        <RefreshControls onRefresh={load} busy={busy} />
      </div>
      {err && <p className="admin-error">{err}</p>}

      <section className="admin-card admin-grid-3">
        <div>
          <div className="admin-muted">Environment</div>
          <div className="admin-stat">{status?.environment ?? "—"}</div>
        </div>
        <div>
          <div className="admin-muted">Database</div>
          <div className="admin-stat">{status?.database_name ?? "—"}</div>
        </div>
        <div>
          <div className="admin-muted">Connection</div>
          <div className="admin-stat">
            {connected ? "Connected" : status?.error || (status ? "Unavailable" : busy ? "Loading…" : "Unavailable")}
          </div>
        </div>
        <div>
          <div className="admin-muted">PostgreSQL</div>
          <div className="admin-stat">{status?.postgres_version ?? "—"}</div>
        </div>
        <div>
          <div className="admin-muted">Host kind</div>
          <div className="admin-stat">{status?.host_kind ?? "—"}</div>
        </div>
        <div>
          <div className="admin-muted">Credentials</div>
          <div className="admin-stat">redacted</div>
        </div>
      </section>

      <section className="admin-card">
        <h3>pgvector</h3>
        {vector?.installed ? (
          <p>
            Installed · {vector.extname} {vector.extversion} · embedding rows:{" "}
            <strong>{vector.embedding_rows ?? 0}</strong>
          </p>
        ) : vector ? (
          <p className="admin-warn">PGVECTOR NOT CONFIGURED</p>
        ) : (
          <p className="admin-muted">{busy ? "Loading…" : "No vector status yet"}</p>
        )}
        <p className="admin-muted">
          Vector tables/columns: {(vector?.columns || []).length || "NONE"} ·{" "}
          <Link href="/admin/database/vectors">Open vectors</Link>
        </p>
      </section>

      <section className="admin-card">
        <h3>Important application tables</h3>
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Schema</th>
                <th>Table</th>
                <th>Rows (est.)</th>
                <th>PK</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(important.length ? important : tables?.tables || []).slice(0, 40).map((t: any) => (
                <tr key={`${t.schema}.${t.table}`}>
                  <td>{t.schema}</td>
                  <td>{t.table}</td>
                  <td>{t.row_count ?? "—"}</td>
                  <td>{(t.primary_key || []).join(", ") || "—"}</td>
                  <td>
                    <Link href={`/admin/database/tables?schema=${t.schema}&table=${t.table}`}>
                      Browse
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="admin-muted">
          {tables?.n ?? 0} tables discovered · query {tables?.query_ms ?? "—"} ms
          {tables?.counts ? ` · counts ${tables.counts}` : ""}
        </p>
      </section>
    </div>
  );
}
