"use client";

import { useState, type ReactNode } from "react";
import type { TraceStatus } from "@/lib/trace";

export function StatusChip({ status }: { status: TraceStatus }) {
  const s = String(status || "NOT_RUN").toUpperCase();
  const tone =
    s === "PASS" || s === "VERIFIED" || s === "SUCCESS"
      ? "pass"
      : s === "FAIL" || s === "FAILED"
        ? "fail"
        : s === "WARNING" || s === "WARN"
          ? "warn"
          : s === "BLOCKED" || s === "HELD"
            ? "blocked"
            : s === "UNRESOLVED"
              ? "unresolved"
              : "idle";
  return (
    <span className={`trace-chip trace-chip-${tone}`} data-status={s}>
      {s}
    </span>
  );
}

export function CopyId({ id, label }: { id: string; label?: string }) {
  const [done, setDone] = useState(false);
  const short = id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      /* ignore */
    }
  }
  return (
    <button type="button" className="trace-copy-id" onClick={copy} title={id}>
      {label || short}
      <span className="trace-copy-hint">{done ? "Copied" : "Copy"}</span>
    </button>
  );
}

export function Accordion({
  id,
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  id: string;
  title: string;
  badge?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="trace-acc" data-open={open ? "1" : "0"}>
      <button
        type="button"
        className="trace-acc-head"
        aria-expanded={open}
        aria-controls={`trace-panel-${id}`}
        id={`trace-btn-${id}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="trace-acc-title">{title}</span>
        {badge}
        <span className="trace-acc-chevron" aria-hidden>
          {open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div
          className="trace-acc-body"
          id={`trace-panel-${id}`}
          role="region"
          aria-labelledby={`trace-btn-${id}`}
        >
          {children}
        </div>
      )}
    </section>
  );
}

export function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="trace-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="trace-json">
      <button type="button" className="btn btn-secondary trace-btn-full" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide JSON" : "Show JSON"}
      </button>
      {open && <pre className="trace-code">{JSON.stringify(value, null, 2)}</pre>}
    </div>
  );
}

export function Empty({ text = "None" }: { text?: string }) {
  return <p className="trace-empty">{text}</p>;
}
