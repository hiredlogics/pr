"use client";

import { useEffect, useRef, useState } from "react";
import { confirmDetails, createCase, getConfirmation, getHealth, submitAnswers } from "@/lib/api";
import { sendFiles } from "@/lib/upload";
import {
  ApiError,
  type AppealResponse,
  type Confirmation,
  type Health,
  type Rejected,
} from "@/lib/types";
import ProgressRail, { type RailStage } from "@/components/ProgressRail";
import UploadStep from "@/components/UploadStep";
import ConfirmStep from "@/components/ConfirmStep";
import SituationStep from "@/components/SituationStep";
import QuestionsStep from "@/components/QuestionsStep";
import ResultStep from "@/components/ResultStep";
import { RejectedNotes } from "@/components/Notices";

/**
 * Screens, in order. `confirm` and `situation` are client-side stages: the
 * server has already extracted by then and is waiting to be told what is right.
 */
type Screen = "upload" | "reading" | "confirm" | "situation" | "questions" | "result";

/** Which rail step a screen belongs to. */
const RAIL: Record<Screen, RailStage> = {
  upload: "upload",
  reading: "reading",
  confirm: "reading",
  situation: "questions",
  questions: "questions",
  result: "result",
};

const WORKING: Record<string, { label: string; detail: string; steps: string[] }> = {
  reading: {
    label: "We're reading your notice…",
    detail: "Our system is extracting the key details from your notice. This only takes a few seconds.",
    steps: ["Identifying parking company", "Reading notice details", "Extracting key information"],
  },
  drafting: {
    label: "Building your appeal…",
    detail: "Checking which grounds your answers open up, then drafting and validating the letter.",
    steps: ["Applying the legal rules", "Drafting your letter", "Checking every sentence"],
  },
};

function Working({ label, detail, steps }: { label: string; detail: string; steps: string[] }) {
  return (
    <div className="card" aria-live="polite">
      <div className="working">
        <span className="work-doc" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
               strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z" />
            <path d="M14 3v4h4M9 12h6M9 15h6M9 18h4" />
          </svg>
        </span>
        <span className="ring" aria-hidden="true" />
        <h2>{label}</h2>
        <p className="lede">{detail}</p>
        <ul className="checklist">
          {steps.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function Page() {
  const [screen, setScreen] = useState<Screen>("upload");
  const [caseId, setCaseId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [data, setData] = useState<AppealResponse | null>(null);
  const [corrections, setCorrections] = useState<Record<string, string>>({});
  // Held here, not inside UploadStep: the step unmounts while the reading
  // screen shows, so local state would drop the customer's file on any error.
  const [files, setFiles] = useState<File[]>([]);

  const [busy, setBusy] = useState<"reading" | "drafting" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorRejected, setErrorRejected] = useState<Rejected[] | null>(null);
  const [round, setRound] = useState(0);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealthFailed(true));
  }, []);

  // Scroll after the banner exists, not from inside the async handler: the
  // error renders on the next commit, and the button that triggered it is at
  // the bottom of the page, so otherwise a failed submit looks like a no-op.
  const errorRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (error || errorRejected) {
      errorRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [error, errorRejected]);

  // A new screen should start at the top, whatever the previous one scrolled to.
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [screen]);

  function fail(e: unknown) {
    if (e instanceof ApiError) {
      setError(e.message);
      setErrorRejected(e.rejected && e.rejected.length > 0 ? e.rejected : null);
    } else {
      setError("Something went wrong. Please try again.");
      setErrorRejected(null);
    }
  }

  async function run(kind: "reading" | "drafting", work: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    setErrorRejected(null);
    try {
      await work();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  /** Step 1 -> 2: create the case, upload, then show what was read. */
  const upload = () =>
    run("reading", async () => {
      const created = await createCase();
      setCaseId(created.case_id);
      const uploaded = await sendFiles(created.case_id, files);
      // Intake routed the case away from the appeal journey (a debt letter, an
      // Order for Recovery ...) or could not classify it: there is nothing to
      // confirm, so say so now rather than after the customer has written
      // their account.
      if (uploaded.state === "NO_APPEAL_RIGHT" || uploaded.state === "CLASSIFICATION_FAILED") {
        setData({ ...uploaded, questions: uploaded.questions ?? [],
                  skipped_questions: uploaded.skipped_questions ?? [] });
        setScreen("result");
        return;
      }
      setConfirmation(await getConfirmation(created.case_id));
      setScreen("confirm");
    });

  /** Step 2 -> 3: hold the edits; they go up with the narrative in one call. */
  function acceptDetails(edited: Record<string, string>) {
    setCorrections(edited);
    setError(null);
    setScreen("situation");
  }

  /** Step 3: confirm + narrative in a single call, then ask what is missing. */
  const submitSituation = (narrative: string, alreadyNamed: boolean | null) =>
    run("drafting", async () => {
      if (!caseId || !confirmation) return;
      const confirmed = confirmation.details.filter((d) => d.value).map((d) => d.name);
      const next = await confirmDetails(caseId, corrections, confirmed, narrative, alreadyNamed);
      setData(next);
      setRound(1);
      setScreen(next.questions.length > 0 && next.state !== "NO_APPEAL_RIGHT" ? "questions" : "result");
    });

  const answer = (answers: Record<string, unknown>, skip = false) =>
    run("drafting", async () => {
      if (!caseId) return;
      const next = await submitAnswers(caseId, answers, skip);
      setData(next);
      setRound((r) => (next.questions.length > 0 ? r + 1 : r));
      setScreen(next.questions.length > 0 ? "questions" : "result");
    });

  function restart() {
    setScreen("upload");
    setCaseId(null);
    setConfirmation(null);
    setData(null);
    setCorrections({});
    setFiles([]);
    setRound(0);
    setError(null);
    setErrorRejected(null);
  }

  /** Same case + saved answers — re-run the pipeline without starting again. */
  const continueCase = () =>
    run("drafting", async () => {
      if (!caseId) return;
      const next = await submitAnswers(caseId, {}, false);
      setData(next);
      setRound((r) => (next.questions.length > 0 ? r + 1 : r));
      setScreen(next.questions.length > 0 ? "questions" : "result");
    });

  return (
    <main className="shell">
      <header className="masthead">
        <div className="wordmark">
          <span>
            Parking <span className="accent">Appeals</span> Group
          </span>
        </div>
        <p className="statusline">
          <span className="statusdot" data-down={healthFailed} aria-hidden="true" />
          {healthFailed
            ? "Appeal service unreachable"
            : health
              ? `${health.app_version ? `${health.app_version} · ` : ""}${health.provider === "demo" ? "Demo reader" : `${health.provider} reader`} · ${health.modules} legal modules`
              : "Checking service…"}
        </p>
        <button className="menubtn" type="button" aria-label="Menu">
          <span aria-hidden="true" />
          <span aria-hidden="true" />
          <span aria-hidden="true" />
        </button>
      </header>

      <ProgressRail stage={busy === "reading" ? "reading" : RAIL[screen]} />

      <div className="layout">
        <div className="layout-main">
          <div ref={errorRef} />
          {error && (
            <div className="notice" data-tone="attention" role="alert" style={{ marginBottom: 16 }}>
              <h3>We could not carry on</h3>
              <p>{error}</p>
            </div>
          )}
          {errorRejected && (
            <div style={{ marginBottom: 16 }}>
              <RejectedNotes rejected={errorRejected} />
            </div>
          )}

          {busy ? (
            <Working {...WORKING[busy]} />
          ) : screen === "upload" ? (
            <UploadStep
              files={files}
              onFilesChange={setFiles}
              busy={false}
              vision={health?.vision ?? true}
              onSubmit={upload}
            />
          ) : screen === "confirm" && confirmation ? (
            <ConfirmStep details={confirmation.details} busy={false} onConfirm={acceptDetails} />
          ) : screen === "situation" ? (
            <SituationStep busy={false} onSubmit={submitSituation} />
          ) : screen === "questions" && data ? (
            <QuestionsStep
              questions={data.questions}
              round={round}
              busy={false}
              onSubmit={(answers) => answer(answers)}
              onSkip={() => answer({}, true)}
            />
          ) : screen === "result" && data ? (
            <ResultStep data={data} onRestart={restart} onContinue={continueCase} />
          ) : null}
        </div>

        {/* Desktop-only standing panel; CSS hides it below 60rem so small
            screens do not get duplicate reassurance under the form. */}
        <aside className="layout-side" aria-label="What happens next">
          <div className="sidecard">
            <h3>How this works</h3>
            <ol>
              <li>We read the operator, charge and dates off your notice.</li>
              <li>You check what we read and correct anything wrong.</li>
              <li>We ask only what is still missing.</li>
              <li>You get a letter to check and send.</li>
            </ol>
          </div>
          <div className="sidecard">
            <h3>Written as the keeper</h3>
            <p>
              We never ask who was driving. Nothing we produce identifies a driver, because that
              protection is often the strongest part of an appeal.
            </p>
          </div>
          <div className="sidecard">
            <h3>Your deadline</h3>
            <p>
              Most operators allow 28 days from the date on the notice. Check yours and send before
              it passes.
            </p>
          </div>
        </aside>
      </div>

      <p className="footnote">
        We write your appeal as the registered keeper. We never ask who was driving, and nothing we
        produce identifies a driver.
      </p>
    </main>
  );
}
