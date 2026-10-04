"use client";

import { useCallback, useEffect, useState } from "react";
import DataTable from "@/components/admin/DataTable";
import RefreshControls from "@/components/admin/RefreshControls";
import { dbKnowledge } from "@/lib/admin_db";

export default function AdminKnowledgePage() {
  const [data, setData] = useState<any>(null);
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      setData(await dbKnowledge({ q: q || undefined, role: role || undefined }));
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }, [q, role]);

  useEffect(() => {
    load();
  }, [load]);

  const modules = data?.modules || [];
  const cols = modules[0]
    ? Object.keys(modules[0]).filter((c) => !["use_when", "do_not_use_when"].includes(c)).slice(0, 12)
    : ["module_id", "topic", "module_role"];

  return (
    <div className="admin-stack">
      <div className="admin-row">
        <h2>Knowledge</h2>
        <RefreshControls onRefresh={load} busy={busy} />
      </div>
      {err && <p className="admin-error">{err}</p>}
      <div className="admin-row">
        <input
          className="admin-input"
          placeholder="Search module id / topic / proposition"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <input
          className="admin-input"
          placeholder="Role filter"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        />
        <button type="button" className="btn btn-secondary" onClick={load}>
          Search
        </button>
      </div>
      <p className="admin-muted">
        Table: {data?.table ?? "—"} · total {data?.total ?? 0} · KB release{" "}
        {data?.kb_release?.kb_release_id ?? "—"}
      </p>
      <section className="admin-card">
        <DataTable columns={cols} rows={modules} />
      </section>
    </div>
  );
}
