"use client";

import { useState } from "react";
import type { AppealResponse } from "@/lib/types";
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
 * Where each routing stop sends the customer.
 *
 * The API returns a stable action key rather than a URL, so these routes stay
 * owned by the frontend. An unknown key falls back to the resources index: a
 * dead link is worse than a page that lists everything.
 */
const CTA_ROUTES: Record<string, string> = {
  DEBT_RECOVERY_TEMPLATE: "/resources/debt-recovery-template",
  COUNCIL_PCN_SERVICE: "/council-pcn",
  COURT_CLAIM_GUIDANCE: "/resources/court-claims",
  OUT_OF_STAGE_GUIDANCE: "/resources/missed-the-deadline",
  RETRY_UPLOAD: "/",
};

function ctaHref(action?: string | null): string {
  return (action && CTA_ROUTES[action]) || "/resources";
}

export default function ResultStep({
  data,
  onRestart,
}: {
  data: AppealResponse;
  onRestart: () => void;
}) {
  const grounds = data.grounds ?? [];

  const released = data.state === "RELEASED" && typeof data.letter === "string" && data.letter.trim().length > 0;
  const noAppeal = data.state === "NO_APPEAL_RIGHT";
  // Our document classifier returned nothing. Kept separate from both the
  // refusals above and the review outcome below: the customer did nothing wrong,
  // so telling them their notice cannot be appealed would be false, and telling
  // them a reviewer will look at it would be wrong — they can just try again.
  const technicalError = data.state === "CLASSIFICATION_FAILED";
  const awaitingQuestions = (data.questions?.length ?? 0) > 0;
  // The pipeline asks for what it is missing, retrieves more of the knowledge
  // base, re-analyses, drops a ground it cannot stand behind and re-drafts
  // before it ever lands here. So this is not "a reviewer will pick it up" —
  // automatic recovery has already run and could not get to a letter we are
  // willing to put someone's name on. The customer's next move is their own.
  const couldNotFinish =
    !released &&
    !noAppeal &&
    !technicalError &&
    !awaitingQuestions &&
    (data.state === "MANUAL_REVIEW" ||
      data.state === "VALIDATION_FAILED" ||
      data.state === "DRAFTED" ||
      data.state === "CONFIRMED" ||
      data.state === "ANALYSED");

  return (
    <div className="stack">
      {noAppeal ? (
        <div className="card stack">
          <div>
            <h1>We cannot generate an appeal for this</h1>
            {/* Wording comes from the routing table, which knows which kind of
                document this was. A fallback here would be a guess, and the old
                one told every stopped customer they were in debt recovery. */}
            <p className="lede">{data.stop_reason}</p>
          </div>
          {data.recommendation && (
            <div className="notice" data-tone="attention">
              <h3>What to do instead</h3>
              <p>{data.recommendation}</p>
              {data.cta?.label && (
                <p style={{ marginTop: 12 }}>
                  <a className="btn btn-primary btn-inline" href={ctaHref(data.cta.action)}>
                    {data.cta.label} &rarr;
                  </a>
                </p>
              )}
            </div>
          )}
        </div>
      ) : technicalError ? (
        <div className="card stack">
          <div>
            <h1>Something went wrong at our end</h1>
            <p className="lede">{data.stop_reason}</p>
          </div>
          {data.recommendation && (
            <div className="notice" data-tone="attention">
              <h3>What to do next</h3>
              <p>{data.recommendation}</p>
              <p style={{ marginTop: 12 }}>
                <button className="btn btn-primary btn-inline" onClick={onRestart}>
                  {data.cta?.label || "Try again"} &rarr;
                </button>
              </p>
            </div>
          )}
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
              {couldNotFinish
                ? "We could not write an appeal for this notice"
                : "No final appeal is ready yet"}
            </h1>
            <p className="lede">
              {couldNotFinish
                ? "Nothing in what you sent us gives an appeal that would stand up, so we will not send you a letter that is likely to be rejected. If there is more to the story — photos of the signs, a receipt, a permit, or anything else from that day — start again and add it."
                : "A generic introduction or incomplete draft is never shown as a finished appeal. Continue the steps, or start again if something went wrong."}
            </p>
          </div>

          {couldNotFinish && (
            <div className="notice" data-tone="attention">
              <h3>What to do next</h3>
              <p>
                You can still appeal to the operator in your own words using the method on your
                notice, and if they reject it you can take it to the operator&rsquo;s appeals
                service. Doing that keeps your options open and costs nothing.
              </p>
              <p style={{ marginTop: 12 }}>
                <button className="btn btn-primary btn-inline" onClick={onRestart}>
                  Start again with more detail &rarr;
                </button>
              </p>
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
          {data.skipped_questions.length > 0 && (
            <li>
              <span className="k">Questions skipped</span>
              <span className="v">
                {data.skipped_questions.length} &mdash; the grounds relying on those facts were not
                raised.
              </span>
            </li>
          )}
        </ul>
      </div>

      <button type="button" className="btn btn-secondary" onClick={onRestart}>
        Start another appeal
      </button>
    </div>
  );
}
