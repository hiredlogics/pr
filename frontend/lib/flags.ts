// The backend emits flags as machine codes (extraction.py). We show every one
// of them, but in words a customer can act on. We never drop a flag we do not
// recognise - it gets shown as-is rather than hidden.

/** Field names as they appear in `missing:<field>` / `uncertain:<field>`. */
const FIELD_LABELS: Record<string, string> = {
  operator_name: "the parking operator's name",
  pcn_number: "the charge notice number",
  vrm: "the vehicle registration",
  parking_event_date: "the date of the parking event",
  notice_issue_date: "the date the notice was issued",
  notice_received_date: "the date the notice was received",
  ntd_date: "the date of the notice left on the windscreen",
  charge_amount: "the charge amount",
  alleged_breach: "what the operator says went wrong",
  site_postcode: "the site postcode",
  site_address: "the site address",
  entry_time: "the recorded entry time",
  exit_time: "the recorded exit time",
  trade_association: "the operator's trade association",
  jurisdiction: "which country's law applies",
};

function field(name: string): string {
  return FIELD_LABELS[name] ?? name.replace(/_/g, " ");
}

export type FlagNote = {
  raw: string;
  tone: "missing" | "uncertain" | "attention";
  text: string;
};

export function describeFlag(raw: string): FlagNote {
  const [kind, ...rest] = raw.split(":");
  const arg = rest.join(":");

  switch (kind) {
    case "missing":
      return {
        raw,
        tone: "missing",
        text: `We could not find ${field(arg)} in what you uploaded.`,
      };
    case "uncertain":
      return {
        raw,
        tone: "uncertain",
        text: `We read ${field(arg)}, but not reliably enough to rely on it.`,
      };
    case "confirm":
      return arg === "jurisdiction"
        ? {
            raw,
            tone: "uncertain",
            text: "We could not tell from the postcode which country's rules apply to this site.",
          }
        : {
            raw,
            tone: "uncertain",
            text: `${field(arg)} still needs confirming.`,
          };
    case "chronology":
      return {
        raw,
        tone: "attention",
        text: "The issue date on the notice appears to fall before the parking event itself.",
      };
    case "conflict":
      return arg === "pcn_number"
        ? {
            raw,
            tone: "attention",
            text:
              "Different charge-notice numbers appear across the documents. Confirm the correct PCN number before continuing — we will not guess or silently correct it.",
          }
        : {
            raw,
            tone: "attention",
            text: `Conflicting values were found for ${field(arg)}. Please confirm the correct one.`,
          };
    case "injection_suspected":
      return {
        raw,
        tone: "attention",
        text: `One uploaded file (${arg}) contained text that looks like an instruction to the system. Its wording was not followed.`,
      };
    default:
      return { raw, tone: "attention", text: raw };
  }
}
