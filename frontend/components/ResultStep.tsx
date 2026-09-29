"use client";

import { useState } from "react";
import { getTrace } from "@/lib/api";
import type { AppealResponse, Trace } from "@/lib/types";
import { FlagNotes, ReadAsNotes, RejectedNotes } from "./Notices";

/**
 * Downloads the branded PDF the API renders.
 *
 * Fetched rather than linked so a failure is a message instead of a browser tab
 * showing raw JSON, and so the filename is ours rather than the URL's last
 * segment. The PDF is the sendable artefact - the text on screen is for checking.
 */
function DownloadPdfButton({ caseId }: { caseId: string }) {
  const [state, setState] = useState<"idle" | "working" | "failed">("idle");

  async function download() {
    setState("working");
    try {
      const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/letter.pdf`);
      if (!res.ok) throw new Error(String(res.status));
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `parking-appeal-${caseId}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      setState("idle");
    } catch {
      setState("failed");
      setTimeout(() => setState("idle"), 4000);
    }
  }

  return (
    <button type="button" className="btn btn-primary btn-inline" onClick={download}
            disabled={state === "working"}>
      {state === "working" ? "Preparing…" : state === "failed" ? "Could not download" : "Download PDF"}
    </button>
  );
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("done");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2500);
  }

  return (
    <button type="button" className="btn btn-secondary" onClick={copy}>
      {state === "done" ? "Copied" : state === "failed" ? "Press Ctrl+C to copy" : "Copy letter"}
    </button>
  );
}

/**
 * Reviewer-grade provenance, kept behind a disclosure. A customer who wants to
 * know why a paragraph is there can look; nobody is made to.
 */
function WhyThisLetter({ caseId }: { caseId: string }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (trace || error) return;
    try {
      setTrace(await getTrace(caseId));
    } catch {
      setError("The detail behind this letter could not be loaded.");
    }
  }

  return (
    <details className="why" onToggle={load}>
      <summary>Why this letter says what it says</summary>
      <div className="why-body">
        {error && <p>{error}</p>}
        {!trace && !error && <p>Loading&hellip;</p>}
        {trace && (
          <>
            <p>
              Each line below is a decision the system recorded while assembling your appeal.
            </p>
            {trace.trace.length > 0 && <pre className="tracelog">{trace.trace.join("\n")}</pre>}
            {trace.missing_facts.length > 0 && (
              <p style={{ marginTop: 10 }}>
                Facts that were never established: {trace.missing_facts.join(", ")}.
              </p>
            )}
          </>
        )}
      </div>
    </details>
  );
}

export default function ResultStep({
  data,
  onRestart,
}: {
  data: AppealResponse;
  onRestart: () => void;
}) {
  const grounds = data.grounds?.length
    ? data.grounds
    : [data.primary_route, ...(data.secondary_routes ?? [])].filter(
        (g): g is string => typeof g === "string" && g.length > 0,
      );

  const released = data.state === "RELEASED" && typeof data.letter === "string" && data.letter.trim().length > 0;
  const noAppeal = data.state === "NO_APPEAL_RIGHT";
  const awaitingQuestions = (data.questions?.length ?? 0) > 0;
  const needsReview =
    !released &&
    !noAppeal &&
    !awaitingQuestions &&
    (data.state === "MANUAL_REVIEW" ||
      data.state === "VALIDATION_FAILED" ||
      data.state === "DRAFTED" ||
      data.state === "CONFIRMED" ||
      data.state === "ANALYSED" ||
      Boolean(data.blocking_issues?.length));

  return (
    <div className="stack">
      {noAppeal ? (
        <div className="card stack">
          <div>
            <h1>We cannot proceed with an appeal</h1>
            <p className="lede">
              {data.stop_reason ??
                "The documents show this case has reached debt recovery and the right to appeal is no longer available."}
            </p>
          </div>
          <div className="notice" data-tone="attention">
            <h3>What to do instead</h3>
            <p>
              {data.recommendation ??
                "Use the Debt Recovery Letter service instead of an ordinary parking appeal."}
            </p>
          </div>
        </div>
      ) : awaitingQuestions ? (
        <div className="card stack">
          <div>
            <h1>A few more details are needed</h1>
            <p className="lede">
              The appeal is not ready yet. Answer the questions on the previous step so a
              case-specific letter can be prepared.
            </p>
          </div>
        </div>
      ) : released ? (
        <div className="card stack">
          <div>
            <h1>Your appeal letter</h1>
            <p className="lede">
              Copy this and send it to the operator using the appeal method on your notice, before
              the deadline the notice gives.
            </p>
          </div>

          <div>
            <div className="letterhead">
              <p className="qcount">Case {data.case_id}</p>
              <span className="letter-actions">
                <DownloadPdfButton caseId={data.case_id} />
                <CopyButton text={data.letter as string} />
              </span>
            </div>
            <pre className="letter">{data.letter}</pre>
          </div>
        </div>
      ) : (
        <div className="card stack">
          <div>
            <h1>
              {needsReview
                ? "This one needs a person to look at it"
                : "No final appeal is ready yet"}
            </h1>
            <p className="lede">
              {needsReview
                ? "We could not release a letter for this case automatically. The checks below stopped it, and a reviewer needs to resolve them first."
                : "A generic introduction or incomplete draft is never shown as a finished appeal. Continue the steps, or start again if something went wrong."}
            </p>
          </div>

          {data.blocking_issues && data.blocking_issues.length > 0 ? (
            <div className="notice" data-tone="attention">
              <h3>What stopped it</h3>
              <ul>
                {data.blocking_issues.map((issue, i) => (
                  <li key={`${issue.rule}:${i}`}>
                    {issue.message}
                    {issue.sentence && (
                      <>
                        {" "}
                        <code>{issue.sentence}</code>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="notice" data-tone="attention">
              The case finished in state <code>{data.state}</code> without a released letter.
            </div>
          )}
        </div>
      )}

      <div className="stack">
        {data.rejected && <RejectedNotes rejected={data.rejected} />}
        <FlagNotes flags={data.flags} />
        {data.read_as && <ReadAsNotes readAs={data.read_as} />}
      </div>

      <div className="card stack">
        <h2>What went into this</h2>
        <ul className="meta">
          {grounds.length > 0 && (
            <li>
              <span className="k">Grounds raised</span>
              <span className="v">
                <ul className="taglist">
                  {grounds.map((g) => (
                    <li className="tag" key={g}>
                      {g}
                    </li>
                  ))}
                </ul>
              </span>
            </li>
          )}
          {data.evidence_list && data.evidence_list.length > 0 && (
            <li>
              <span className="k">Evidence listed</span>
              <span className="v">{data.evidence_list.join(", ")}</span>
            </li>
          )}
          {data.pofa_route && (
            <li>
              <span className="k">Notice route</span>
              <span className="v">
                {data.pofa_route === "POSTAL"
                  ? "Posted to the registered keeper"
                  : data.pofa_route === "WINDSCREEN"
                    ? "Left on the windscreen"
                    : data.pofa_route}
              </span>
            </li>
          )}
          {data.pofa_findings && data.pofa_findings.length > 0 && (
            <li>
              <span className="k">Keeper liability</span>
              <span className="v">{data.pofa_findings.join(" ")}</span>
            </li>
          )}
          {data.skipped_questions.length > 0 && (
            <li>
              <span className="k">Questions skipped</span>
              <span className="v">
                {data.skipped_questions.length} &mdash; the grounds relying on those facts were not
                raised.
              </span>
            </li>
          )}
          {data.code_version && (
            <li>
              <span className="k">Code of practice</span>
              <span className="v">{data.code_version}</span>
            </li>
          )}
        </ul>

        {released && <WhyThisLetter caseId={data.case_id} />}
      </div>

      <button type="button" className="btn btn-secondary" onClick={onRestart}>
        Start another appeal
      </button>
    </div>
  );
}
