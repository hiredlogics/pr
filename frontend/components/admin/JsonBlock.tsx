"use client";

import { useState } from "react";

export default function JsonBlock({ value, label }: { value: unknown; label?: string }) {
  const [open, setOpen] = useState(false);
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const short = text.length > 120 ? text.slice(0, 120) + "…" : text;
  return (
    <div className="admin-json">
      {label && <div className="admin-muted">{label}</div>}
      <pre>{open ? text : short}</pre>
      {text.length > 120 && (
        <button type="button" className="admin-linkish" onClick={() => setOpen(!open)}>
          {open ? "Collapse" : "Expand JSON"}
        </button>
      )}
    </div>
  );
}
