"use client";

import { useState } from "react";

/**
 * Customer's account of what happened.
 *
 * Does not itself select a legal ground. The backend assesses the text for
 * material relevance to the allegation and, where it supports or contradicts
 * an available ground, incorporates keeper-safe points into the appeal.
 */
export default function SituationStep({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (narrative: string, driverAlreadyNamed: boolean) => void;
}) {
  const [free, setFree] = useState("");
  const [alreadyNamed, setAlreadyNamed] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = free.trim();
    if (!text) {
      setLocalError("Tell us briefly what happened, in your own words.");
      return;
    }
    onSubmit(text, alreadyNamed);
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <div>
        <h1>What happened?</h1>
        <p className="lede">Describe the situation in your own words.</p>
      </div>

      <label className="stack" style={{ gap: 6 }}>
        <span className="qtext">Your account</span>
        <textarea
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

      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: "0.95rem" }}>
        <input
          type="checkbox"
          checked={alreadyNamed}
          onChange={(e) => setAlreadyNamed(e.target.checked)}
          disabled={busy}
          style={{ marginTop: 3 }}
        />
        <span>
          The operator has already been formally told who was driving (status only — we never ask
          who that was).
        </span>
      </label>

      {localError && (
        <div className="notice" data-tone="attention" role="alert">
          {localError}
        </div>
      )}

      <button className="btn btn-primary" type="submit" disabled={busy}>
        Continue
      </button>
    </form>
  );
}
