"use client";

import { useEffect, useState } from "react";

export default function RefreshControls({
  onRefresh,
  busy,
}: {
  onRefresh: () => void;
  busy?: boolean;
}) {
  const [intervalSec, setIntervalSec] = useState(0);

  useEffect(() => {
    if (!intervalSec) return;
    const id = window.setInterval(() => onRefresh(), intervalSec * 1000);
    return () => window.clearInterval(id);
  }, [intervalSec, onRefresh]);

  return (
    <div className="admin-refresh">
      <button type="button" className="btn btn-secondary" onClick={onRefresh} disabled={busy}>
        {busy ? "Refreshing…" : "Refresh"}
      </button>
      <label>
        Auto
        <select
          value={intervalSec}
          onChange={(e) => setIntervalSec(Number(e.target.value))}
          className="admin-input"
        >
          <option value={0}>OFF</option>
          <option value={5}>5s</option>
          <option value={10}>10s</option>
          <option value={30}>30s</option>
        </select>
      </label>
    </div>
  );
}
