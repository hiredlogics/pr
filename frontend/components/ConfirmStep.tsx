"use client";

import { useMemo, useState } from "react";
import type { Detail } from "@/lib/types";

/**
 * "Check your details" - the one place a human sees what was read off the
 * notice before any of it shapes a legal argument.
 *
 * It matters more than it looks: a misread issue date silently decides whether
 * the strongest ground (a late Notice to Keeper) exists at all. Anything the
 * extractor was unsure of is flagged and starts open for editing.
 */
export default function ConfirmStep({
  details,
  busy,
  onConfirm,
}: {
  details: Detail[];
  busy: boolean;
  /** Only the values the customer changed, keyed by fact name. */
  onConfirm: (corrections: Record<string, string>) => void;
}) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(details.filter((d) => d.needs_attention).map((d) => d.name)),
  );

  const value = (d: Detail) => edits[d.name] ?? d.value;

  const corrections = useMemo(() => {
    const out: Record<string, string> = {};
    for (const d of details) {
      const next = (edits[d.name] ?? d.value).trim();
      if (next !== d.value.trim()) out[d.name] = next;
    }
    return out;
  }, [details, edits]);

  const changed = Object.keys(corrections).length;
  const attention = details.filter((d) => d.needs_attention).length;

  function toggle(name: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <form
      className="card stack"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        onConfirm(corrections);
      }}
    >
      <div>
        <h1>Check your details</h1>
        <p className="lede">
          We have extracted the following information from your notice. Please check and amend if
          needed.
        </p>
      </div>

      {attention > 0 && (
        <div className="notice" data-tone="attention">
          <h3>
            {attention === 1 ? "One detail needs" : `${attention} details need`} your attention
          </h3>
          <p>
            We could not read {attention === 1 ? "it" : "them"} reliably. Nothing unreadable is used
            to argue your appeal, so filling {attention === 1 ? "it" : "them"} in can only help.
          </p>
        </div>
      )}

      <dl className="details">
        {details.map((d) => {
          const editing = open.has(d.name);
          return (
            <div className="detail" key={d.name} data-attention={d.needs_attention}>
              <dt>
                <label htmlFor={`f-${d.name}`}>{d.label}</label>
              </dt>
              <dd>
                {editing ? (
                  <input
                    id={`f-${d.name}`}
                    type="text"
                    value={value(d)}
                    placeholder="Not found on the notice"
                    onChange={(e) => setEdits((p) => ({ ...p, [d.name]: e.target.value }))}
                    autoFocus={!d.needs_attention}
                  />
                ) : (
                  <span className="dval">{value(d) || "Not found"}</span>
                )}
                <button
                  type="button"
                  className="editlink"
                  onClick={() => toggle(d.name)}
                  aria-expanded={editing}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                  </svg>
                  {editing ? "Done" : "Edit"}
                </button>
              </dd>
            </div>
          );
        })}
      </dl>

      <div className="stack" style={{ gap: 10 }}>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {changed > 0 ? "Save changes and continue" : "Looks correct, continue"}
        </button>
        <button
          className="btn btn-outline"
          type="button"
          disabled={busy}
          onClick={() => setOpen(new Set(details.map((d) => d.name)))}
        >
          The details are incorrect
        </button>
      </div>
    </form>
  );
}
