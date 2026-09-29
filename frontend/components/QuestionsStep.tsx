"use client";

import { useMemo, useState } from "react";
import type { Question } from "@/lib/types";

/**
 * Answers are held per fact, never per position: question order within a round
 * is not stable between runs, so nothing here may key off the array index.
 *
 * A question the customer has not touched is absent from the payload entirely.
 * That matters for `bool`: the backend coerces an unrecognised value to false,
 * so sending a placeholder would silently record "no" on their behalf.
 */
type Draft = Record<string, boolean | string | number>;

export default function QuestionsStep({
  questions,
  round,
  busy,
  onSubmit,
  onSkip,
}: {
  questions: Question[];
  round: number;
  busy: boolean;
  onSubmit: (answers: Record<string, unknown>) => void;
  onSkip: () => void;
}) {
  const [draft, setDraft] = useState<Draft>({});
  const [error, setError] = useState<string | null>(null);

  // A fresh round brings fresh facts; identity of the set is what resets us.
  const key = useMemo(() => questions.map((q) => q.fact).sort().join("|"), [questions]);
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {
    setSeenKey(key);
    setDraft({});
    setError(null);
  }

  function set(fact: string, value: boolean | string | number) {
    setError(null);
    setDraft((d) => ({ ...d, [fact]: value }));
  }

  function clear(fact: string) {
    setDraft((d) => {
      const next = { ...d };
      delete next[fact];
      return next;
    });
  }

  const answeredCount = questions.filter((q) => draft[q.fact] !== undefined).length;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const payload: Record<string, unknown> = {};

    for (const q of questions) {
      const v = draft[q.fact];
      if (v === undefined) continue; // untouched: leave the fact absent

      if (q.type === "bool") {
        if (typeof v !== "boolean") continue;
        payload[q.fact] = v; // a real JSON boolean, never a label
      } else if (q.type === "choice") {
        // Guard before sending: a value outside `options` is a 422.
        if (typeof v !== "string" || !(q.options ?? []).includes(v)) continue;
        payload[q.fact] = v;
      } else if (q.type === "int") {
        const n = typeof v === "number" ? v : Number(String(v).trim());
        if (!Number.isFinite(n)) {
          setError(`Enter a number for: ${q.text}`);
          return;
        }
        payload[q.fact] = Math.round(n);
      } else {
        const s = String(v).trim();
        if (s) payload[q.fact] = s;
      }
    }

    if (Object.keys(payload).length === 0) {
      setError("Answer at least one question, or choose to skip the rest.");
      return;
    }
    onSubmit(payload);
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <div>
        <h1 style={{ marginTop: 0 }}>
          {round > 1 ? "A few more questions" : "A few questions"}
        </h1>
      </div>

      <div>
        {questions.map((q) => (
          <div className="question" key={q.fact}>
            <p className="qtext">{q.text}</p>

            {q.type === "bool" && (
              <div className="choices">
                {[
                  { label: "Yes", value: true },
                  { label: "No", value: false },
                ].map((opt) => (
                  <button
                    key={opt.label}
                    type="button"
                    className="chip"
                    aria-pressed={draft[q.fact] === opt.value}
                    onClick={() =>
                      draft[q.fact] === opt.value ? clear(q.fact) : set(q.fact, opt.value)
                    }
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}

            {q.type === "choice" && (
              <div className="choices">
                {(q.options ?? []).map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    className="chip"
                    aria-pressed={draft[q.fact] === opt}
                    onClick={() => (draft[q.fact] === opt ? clear(q.fact) : set(q.fact, opt))}
                  >
                    {opt.charAt(0) + opt.slice(1).toLowerCase().replace(/_/g, " ")}
                  </button>
                ))}
              </div>
            )}

            {q.type === "int" && (
              <input
                type="number"
                inputMode="numeric"
                min={0}
                step={1}
                value={draft[q.fact] === undefined ? "" : String(draft[q.fact])}
                onChange={(e) =>
                  e.target.value === "" ? clear(q.fact) : set(q.fact, e.target.value)
                }
                placeholder="Number of minutes"
                style={{ maxWidth: "12rem" }}
              />
            )}

            {q.type === "text" && (
              <input
                type="text"
                value={draft[q.fact] === undefined ? "" : String(draft[q.fact])}
                onChange={(e) =>
                  e.target.value === "" ? clear(q.fact) : set(q.fact, e.target.value)
                }
                placeholder="Your answer"
              />
            )}
          </div>
        ))}
      </div>

      {error && (
        <div className="notice" data-tone="attention" role="alert">
          {error}
        </div>
      )}

      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy
          ? "Working…"
          : `Continue with ${answeredCount} of ${questions.length} answered`}
      </button>

      <div>
        <button type="button" className="btn btn-quiet" onClick={onSkip} disabled={busy}>
          Skip the rest of the questions
        </button>
      </div>
    </form>
  );
}
