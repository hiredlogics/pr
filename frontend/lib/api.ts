import {
  ApiError,
  type AppealResponse,
  type Confirmation,
  type Health,
  type Trace,
  type UploadResult,
} from "./types";

/**
 * Everything goes through the Next route handler at /api/... - the browser
 * never talks to FastAPI directly (no CORS there, and this keeps the backend
 * address server-side).
 */
async function unwrap<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;

  let detail: unknown;
  try {
    detail = (await res.json())?.detail;
  } catch {
    detail = undefined;
  }

  // FastAPI hands back `detail` either as a bare string (a rejected answer
  // value) or as an object carrying the per-file reasons (nothing readable).
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; rejected?: { filename: string; reason: string }[] };
    throw new ApiError(d.message ?? "That upload could not be processed.", res.status, d.rejected);
  }
  if (typeof detail === "string" && detail.trim()) {
    throw new ApiError(detail, res.status);
  }
  throw new ApiError(`The server returned an error (${res.status}).`, res.status);
}

/** A fetch that turns a dead server into a sentence rather than a stack trace. */
async function call(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(
      "We could not reach the appeal service. Check your connection and try again.",
      0,
    );
  }
}

export async function submitFiles(
  files: File[],
  narrative: string,
  driverAlreadyNamed: boolean,
): Promise<AppealResponse> {
  const body = new FormData();
  for (const f of files) body.append("files", f, f.name);
  body.append("narrative", narrative);
  // Status only. This says the operator has already been formally told who was
  // driving; it never asks us, or tells us, who that was.
  body.append("driver_already_named_to_operator", String(driverAlreadyNamed));
  return unwrap<AppealResponse>(await call("/appeal/files", { method: "POST", body }));
}

/* ------------------------------------------------------------------------ *
 * Step-by-step flow. The customer sees what we read and corrects it before
 * anything is argued on their behalf, which `submitFiles` above skips.
 * ------------------------------------------------------------------------ */

export async function createCase(): Promise<{ case_id: string }> {
  return unwrap<{ case_id: string }>(await call("/cases", { method: "POST" }));
}

export async function uploadToCase(caseId: string, files: File[]): Promise<UploadResult> {
  const body = new FormData();
  for (const f of files) body.append("files", f, f.name);
  return unwrap<UploadResult>(
    await call(`/cases/${encodeURIComponent(caseId)}/files`, { method: "POST", body }),
  );
}

/** Hand the API the URLs of files the browser uploaded straight to storage. */
export async function uploadBlobs(
  caseId: string,
  blobs: { url: string; filename: string }[],
): Promise<UploadResult> {
  return unwrap<UploadResult>(
    await call(`/cases/${encodeURIComponent(caseId)}/blobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blobs }),
    }),
  );
}

export async function getConfirmation(caseId: string): Promise<Confirmation> {
  return unwrap<Confirmation>(await call(`/cases/${encodeURIComponent(caseId)}/confirmation`));
}

/**
 * Accept the extracted details, with any edits, and hand over the customer's
 * account of what happened. The account only steers which questions get asked -
 * every legal ground still has to clear its own gate on the server.
 */
export async function confirmDetails(
  caseId: string,
  corrections: Record<string, string>,
  confirmed: string[],
  narrative: string,
  driverAlreadyNamed = false,
): Promise<AppealResponse> {
  return unwrap<AppealResponse>(
    await call(`/cases/${encodeURIComponent(caseId)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        corrections,
        confirmed,
        narrative,
        driver_already_named_to_operator: driverAlreadyNamed,
      }),
    }),
  );
}

export async function submitAnswers(
  caseId: string,
  answers: Record<string, unknown>,
  skip = false,
): Promise<AppealResponse> {
  return unwrap<AppealResponse>(
    await call(`/appeal/${encodeURIComponent(caseId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers, skip }),
    }),
  );
}

export async function getHealth(): Promise<Health> {
  return unwrap<Health>(await call("/health"));
}

export async function getTrace(caseId: string): Promise<Trace> {
  return unwrap<Trace>(await call(`/cases/${encodeURIComponent(caseId)}/trace`));
}
