import { describeFlag } from "@/lib/flags";
import type { ReadAs, Rejected } from "@/lib/types";

/**
 * What the backend could not read, in the customer's terms.
 *
 * The API decides which flags reach this component; anything internal never
 * arrives. A flag with no plain-English description is therefore a gap in
 * `describeFlag`, and is dropped rather than printed as a machine code - the
 * raw string used to be appended to every line, so customers read things like
 * `confirm:jurisdiction` next to the prose.
 */

export function FlagNotes({ flags }: { flags: string[] }) {
  const notes = flags.map(describeFlag).filter((n) => n.text !== n.raw);
  if (notes.length === 0) return null;
  const tone = notes.some((n) => n.tone === "attention") ? "attention" : "plain";

  return (
    <div className="notice" data-tone={tone}>
      <h3>What we could not read from your notice</h3>
      <ul>
        {notes.map((n) => (
          <li key={n.raw}>{n.text}</li>
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
