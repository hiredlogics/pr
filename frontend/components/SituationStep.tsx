"use client";

import { useState } from "react";

/**
 * Free-text account of the parking event. External driver-disclosure status is
 * NOT collected here. An unchecked or absent control must not mark the driver
 * as formally identified to the operator — that killed Schedule 4 keeper analysis.
 */
export default function SituationStep({
  onSubmit,
  busy,
  initialAccount = "",
}: {
  onSubmit: (narrative: string, driverAlreadyNamed: boolean | null) => void;
  busy?: boolean;
  initialAccount?: string;
}) {
  const [free, setFree] = useState(initialAccount);
  const [localError, setLocalError] = useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = free.trim();
    if (!text) {
      setLocalError("Please say briefly what happened, even if it is only a few words.");
      return;
    }
    setLocalError(null);
    // null → API UNKNOWN. Never send true from this screen.
    onSubmit(text, null);
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <div>
        <h1>What happened?</h1>
        <p className="lede">
          In your own words — anything that helps explain the charge. You do not need legal
          language.
        </p>
      </div>

      <label className="field">
        <span className="field-label">Your account</span>
        <textarea
          className="input"
          value={free}
          onChange={(e) => {
            setLocalError(null);
            setFree(e.target.value);
          }}
          rows={5}
          placeholder="For example: the paid time ran out while waiting at the barrier…"
          disabled={busy}
        />
      </label>

      {localError && (
        <div className="notice" data-tone="attention" role="alert">
          <p>{localError}</p>
        </div>
      )}

      <button className="btn btn-primary" type="submit" disabled={busy}>
        Continue
      </button>
    </form>
  );
}
