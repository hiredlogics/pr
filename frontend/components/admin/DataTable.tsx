"use client";

import JsonBlock from "./JsonBlock";

export default function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
}) {
  if (!columns?.length) return <p className="admin-muted">No columns</p>;
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => {
                const v = row[c];
                if (v !== null && typeof v === "object") {
                  return (
                    <td key={c}>
                      <JsonBlock value={v} />
                    </td>
                  );
                }
                return <td key={c}>{v === null || v === undefined ? "—" : String(v)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
