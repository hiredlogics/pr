import { describeFlag } from "@/lib/flags";
import type { ReadAs, Rejected } from "@/lib/types";

/**
 * Everything the backend told us about what it could and could not read.
 * Nothing is suppressed - a customer who does not know a field was unreadable
 * cannot fix it.
 */

export function FlagNotes({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  const notes = flags.map(describeFlag);
  const tone = notes.some((n) => n.tone === "attention") ? "attention" : "plain";

  return (
    <div className="notice" data-tone={tone}>
      <h3>What we could not read from your notice</h3>
      <ul>
        {notes.map((n) => (
          <li key={n.raw}>
            {n.text}
            {n.text !== n.raw && <code> {n.raw}</code>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RejectedNotes({ rejected }: { rejected: Rejected[] }) {
  if (rejected.length === 0) return null;
  return (
    <div className="notice" data-tone="attention">
      <h3>
        {rejected.length === 1 ? "One file could not be used" : `${rejected.length} files could not be used`}
      </h3>
      <ul>
        {rejected.map((r) => (
          <li key={`${r.filename}:${r.reason}`}>
            <strong>{r.filename || "unnamed file"}</strong> &mdash; {r.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ReadAsNotes({ readAs }: { readAs: ReadAs[] }) {
  if (readAs.length === 0) return null;
  return (
    <div className="notice" data-tone="info">
      <h3>Files we read</h3>
      <ul>
        {readAs.map((r) => (
          <li key={r.evidence_id}>
            <strong>{r.filename}</strong> &mdash;{" "}
            {r.chars > 0 ? `${r.chars.toLocaleString("en-GB")} characters of text` : "no text layer"}
            {r.images > 0 ? `, ${r.images} page image${r.images === 1 ? "" : "s"} for the reader` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
