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

/** Split the API's plain letter into paragraphs for an on-screen document preview. */
function letterParagraphs(letter: string): string[] {
  return letter
    .replace(/\r\n/g, "\n")
    .split(/\n\s*\n/)
    .map((p) => p.replace(/\n/g, " ").trim())
    .filter(Boolean);
}

function LetterPreview({ caseId, letter }: { caseId: string; letter: string }) {
  const paragraphs = letterParagraphs(letter);
  const today = new Date().toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <article className="letter-sheet" aria-label="Appeal letter preview">
      <header className="letter-sheet-head">
        <div>
          <p className="letter-brand">
            Parking Appeals <span className="accent">Group</span>
          </p>
          <p className="letter-brand-sub">Formal appeal — private parking charge</p>
        </div>
        <div className="letter-meta">
          <div>Case reference: {caseId}</div>
          <div>{today}</div>
        </div>
      </header>
      <div className="letter-sheet-body">
        {paragraphs.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
      <p className="letter-sheet-note">
        Preview of your letter. Download the PDF to send to the operator.
      </p>
    </article>
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
  RESOURCES: "/resources",
  RETRY_UPLOAD: "/",
};

/** A deployment-configured link wins (the backend only sends a site path or an
 *  https URL); otherwise the frontend's own page for the action key. */
function ctaHref(action?: string | null, href?: string): string {
  if (href && (href.startsWith("/") || href.startsWith("https://"))) return href;
  return (action && CTA_ROUTES[action]) || "/resources";
}

export default function ResultStep({
  data,
  onRestart,
  onContinue,
}: {
  data: AppealResponse;
  onRestart: () => void;
  /** Re-run generate on the same case — preserves answers. */
  onContinue?: () => void;
}) {
  const released = data.state === "RELEASED" && typeof data.letter === "string" && data.letter.trim().length > 0;
  const noAppeal = data.state === "NO_APPEAL_RIGHT";
  // Our document classifier returned nothing. Kept separate from both the
  // refusals above and the review outcome below: the customer did nothing wrong,
  // so telling them their notice cannot be appealed would be false, and telling
  // them a reviewer will look at it would be wrong — they can just try again.
  const technicalError = data.state === "CLASSIFICATION_FAILED";
  const awaitingQuestions = (data.questions?.length ?? 0) > 0;
  // Held after automatic recovery. The backend names *why* via `outcome` —
  // never collapse every MANUAL_REVIEW into a merits judgment.
  const held =
    !released &&
    !noAppeal &&
    !technicalError &&
    !awaitingQuestions &&
    (data.state === "MANUAL_REVIEW" ||
      data.state === "VALIDATION_FAILED" ||
      data.state === "DRAFTED" ||
      data.state === "CONFIRMED" ||
      data.state === "ANALYSED");

  const outcome = data.outcome;
  const isProcessing = outcome === "PROCESSING_ERROR" || (!outcome && held === false && technicalError);
  const isNoGrounds = outcome === "NO_SUPPORTED_GROUNDS";
  const isNeedsDocs = outcome === "NEEDS_DOCUMENTS";
  const isNeedsFacts = outcome === "NEEDS_FACTS";
  // Legacy payloads without `outcome` must not imply merits: treat as processing.
  const legacyHeldAsProcessing = held && !outcome;

  const title =
    data.outcome_title ||
    (noAppeal
      ? "We cannot generate an appeal for this"
      : technicalError
        ? "Something went wrong at our end"
        : isNeedsDocs
          ? "We need clearer documents"
          : isNeedsFacts
            ? "A few more details are needed"
            : isNoGrounds
              ? "We could not write an appeal we can stand behind"
              : held || isProcessing || legacyHeldAsProcessing
                ? "Something went wrong while preparing your appeal"
                : "No final appeal is ready yet");

  const lede =
    data.outcome_message ||
    (noAppeal
      ? data.stop_reason
      : technicalError
        ? data.stop_reason
        : isNoGrounds
          ? "From the notice and the answers you gave, we did not find a supported ground we are willing to put in a letter. That is our assessment of what we can argue — not a ruling that you have no case."
          : held || legacyHeldAsProcessing
            ? "A processing error stopped us finishing the letter. This is not a judgment on the strength of your case. Your answers are saved — you can continue this case without starting again."
            : "A generic introduction or incomplete draft is never shown as a finished appeal. Continue the steps, or start again if something went wrong.");

  const nextCopy =
    data.outcome_next ||
    (isNoGrounds
      ? "You can still appeal to the operator using the method on your notice. If they reject it, you can take it to their appeals service."
      : "Try continuing this case. If it keeps failing, contact support with your case reference.");

  const ctaLabel =
    data.cta?.label ||
    (isNoGrounds
      ? "Add more detail to this case"
      : data.can_continue !== false
        ? "Continue this case"
        : "Start another appeal");

  const showContinue =
    (held || isNeedsDocs || isNeedsFacts || isNoGrounds || legacyHeldAsProcessing) &&
    data.can_continue !== false &&
    typeof onContinue === "function";

  return (
    <div className="stack">
      {noAppeal ? (
        <div className="card stack">
          <div>
            <h1>{data.stop_title || "We cannot generate an appeal for this"}</h1>
            <p className="lede">{data.stop_reason}</p>
          </div>
          {data.recommendation && (
            <div className="notice" data-tone="attention">
              <h3>What to do instead</h3>
              <p>{data.recommendation}</p>
              {data.cta?.label && data.cta.action && data.cta.action !== "CONTINUE_CASE" && (
                <p style={{ marginTop: 12 }}>
                  <a className="btn btn-primary btn-inline" href={ctaHref(data.cta.action, data.cta.href)}>
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
            <h1>{title}</h1>
            <p className="lede">{lede}</p>
          </div>
          <div className="notice" data-tone="attention">
            <h3>What to do next</h3>
            <p>{nextCopy}</p>
            <p style={{ marginTop: 12 }}>
              {showContinue ? (
                <button className="btn btn-primary btn-inline" onClick={onContinue}>
                  {ctaLabel} &rarr;
                </button>
              ) : (
                <button className="btn btn-primary btn-inline" onClick={onRestart}>
                  {data.cta?.label || "Try again"} &rarr;
                </button>
              )}
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
              Check the letter below, then download the PDF and send it to the operator using the
              appeal method on your notice, before the deadline the notice gives.
            </p>
          </div>

          <div className="letterhead">
            <p className="qcount">Ready to send</p>
            <span className="letter-actions">
              <DownloadPdfButton caseId={data.case_id} />
              <CopyButton text={data.letter as string} />
            </span>
          </div>

          <LetterPreview caseId={data.case_id} letter={data.letter as string} />
        </div>
      ) : (
        <div className="card stack">
          <div>
            <h1>{title}</h1>
            <p className="lede">{lede}</p>
          </div>

          {(held || legacyHeldAsProcessing || isNoGrounds || isNeedsDocs || isNeedsFacts) && (
            <div className="notice" data-tone="attention">
              <h3>What to do next</h3>
              <p>{nextCopy}</p>
              <p style={{ marginTop: 12 }}>
                {showContinue ? (
                  <button className="btn btn-primary btn-inline" onClick={onContinue}>
                    {ctaLabel} &rarr;
                  </button>
                ) : (
                  <button className="btn btn-primary btn-inline" onClick={onRestart}>
                    Start another appeal &rarr;
                  </button>
                )}
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

      <button type="button" className="btn btn-secondary" onClick={onRestart}>
        Start another appeal
      </button>
    </div>
  );
}
