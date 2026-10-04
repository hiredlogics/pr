"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import DataTable from "@/components/admin/DataTable";
import RefreshControls from "@/components/admin/RefreshControls";
import { dbTableRows, dbTables } from "@/lib/admin_db";

export default function AdminTablesPage() {
  const [tables, setTables] = useState<any>(null);
  const [schema, setSchema] = useState("public");
  const [table, setTable] = useState("cases");
  const [rows, setRows] = useState<any>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showSensitive, setShowSensitive] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const loadTables = useCallback(async () => {
    try {
      setTables(await dbTables(true));
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  }, []);

  const loadRows = useCallback(async () => {
    if (!schema || !table) return;
    setBusy(true);
    setErr("");
    try {
      setRows(
        await dbTableRows(schema, table, {
          page,
          page_size: 50,
          search: search || undefined,
          show_sensitive: showSensitive,
        }),
      );
    } catch (e: any) {
      setErr(String(e?.message || e));
      setRows(null);
    } finally {
      setBusy(false);
    }
  }, [schema, table, page, search, showSensitive]);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("schema")) setSchema(q.get("schema") || "public");
    if (q.get("table")) setTable(q.get("table") || "cases");
    loadTables();
  }, [loadTables]);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  const options = useMemo(
    () => (tables?.tables || []).map((t: any) => `${t.schema}.${t.table}`),
    [tables],
  );

  return (
    <div className="admin-stack">
      <div className="admin-row">
        <h2>PostgreSQL tables</h2>
        <RefreshControls onRefresh={loadRows} busy={busy} />
      </div>
      {err && <p className="admin-error">{err}</p>}

      <div className="admin-row">
        <select
          className="admin-input"
          value={`${schema}.${table}`}
          onChange={(e) => {
            const [s, t] = e.target.value.split(".");
            setSchema(s);
            setTable(t);
            setPage(1);
          }}
        >
          {options.map((o: string) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <input
          className="admin-input"
          placeholder="Search"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <label className="admin-check">
          <input
            type="checkbox"
            checked={showSensitive}
            onChange={(e) => setShowSensitive(e.target.checked)}
          />
          Show sensitive
        </label>
      </div>

      <p className="admin-muted">
        {schema}.{table} · total {rows?.total ?? "—"} · page {rows?.page ?? page} ·{" "}
        {rows?.query_ms ?? "—"} ms · default limit 50
      </p>

      <section className="admin-card">
        <DataTable columns={rows?.columns || []} rows={rows?.rows || []} />
        <div className="admin-row" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Prev
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={(rows?.rows || []).length < 50}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </section>
    </div>
  );
}
