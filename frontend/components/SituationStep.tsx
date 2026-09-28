"use client";

import { useState } from "react";

/**
 * "Your situation" - the customer's account of what happened.
 *
 * Picking an option here does NOT choose a legal ground. The text is sent as the
 * narrative, which only produces route *hints*: hints widen which questions get
 * asked, and every ground still has to clear its own use_when gate against
 * confirmed facts on the server. So a wrong choice costs one extra question, and
 * a right one is still no guarantee - the facts have to support it.
 *
 * Each option is phrased as the customer would say it, and carries the wording
 * the backend's classifier keys off.
 */
const OPTIONS: { id: string; label: string; narrative: string }[] = [
  {
    id: "signage",
    label: "The signage was unclear or inadequate",
    narrative: "the signs were unclear, hidden or inadequate",
  },
  {
    id: "permit",
    label: "I have a valid permit or was authorised to park",
    narrative: "a valid permit was held and there was permission to park",
  },
  {
    id: "resident",
    label: "I was a resident / have the right to park",
    narrative: "the vehicle was parked by a resident with a lease right to park in the space",
  },
  {
    id: "grace",
    label: "There wasn't enough time (grace period)",
    narrative: "the permitted period had ended and leaving was delayed by a queue at the barrier",
  },
  {
    id: "payment",
    label: "A payment was made or attempted",
    narrative: "a parking payment was made or attempted on the app or machine",
  },
  {
    id: "breakdown",
    label: "I experienced a breakdown or unforeseen circumstances",
    narrative: "the vehicle broke down and could not be moved, and recovery attended",
  },
  { id: "other", label: "Other (please specify)", narrative: "" },
];

export default function SituationStep({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (narrative: string, driverAlreadyNamed: boolean) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [free, setFree] = useState("");
  const [alreadyNamed, setAlreadyNamed] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const option = OPTIONS.find((o) => o.id === picked) ?? null;
  const needsText = option?.id === "other";

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!option) {
      setLocalError("Choose the line that comes closest to what happened.");
      return;
    }
    const extra = free.trim();
    if (needsText && !extra) {
      setLocalError("Tell us briefly what happened, in your own words.");
      return;
    }
    // Both parts go through: the preset gives the classifier something solid to
    // work with, the free text can add a detail no preset covers.
    onSubmit([option.narrative, extra].filter(Boolean).join(". "), alreadyNamed);
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <div>
        <h1>Your situation</h1>
        <p className="lede">
          Help us understand what happened so we can generate the strongest appeal for your case.
        </p>
      </div>

      <fieldset className="options">
        <legend className="sr-only">What happened?</legend>
        {OPTIONS.map((o) => (
          <label className="option" key={o.id} data-selected={picked === o.id}>
            <input
              type="radio"
              name="situation"
              value={o.id}
              checked={picked === o.id}
              onChange={() => {
                setPicked(o.id);
                setLocalError(null);
              }}
            />
            <span>{o.label}</span>
          </label>
        ))}
      </fieldset>

      {picked && (
        <div>
          <label htmlFor="free">
            {needsText ? "What happened?" : "Anything else we should know? (optional)"}
          </label>
          <textarea
            id="free"
            rows={4}
            value={free}
            onChange={(e) => setFree(e.target.value)}
            placeholder="For example: I paid on the app but the machine kept rejecting my card, and there was a queue at the exit barrier."
          />
          <p className="hint">
            Plain words are fine. What you write decides which questions we need to ask next, not
            which arguments we make - the facts decide that.
          </p>
        </div>
      )}

      <label className="filerow" style={{ alignItems: "flex-start", gap: 10, fontWeight: 400 }}>
        <input
          type="checkbox"
          checked={alreadyNamed}
          onChange={(e) => setAlreadyNamed(e.target.checked)}
          style={{ marginTop: 3, flex: "none" }}
        />
        <span>
          The operator has already been formally told who was driving.
          <span className="hint" style={{ marginTop: 2 }}>
            A status only. We never ask for, record or include that information.
          </span>
        </span>
      </label>

      {localError && (
        <div className="notice" data-tone="attention" role="alert">
          {localError}
        </div>
      )}

      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? "Working…" : "Continue"}
      </button>
    </form>
  );
}
