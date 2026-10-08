import { ApiError, type AppealResponse, type UploadResult } from "./types";
import { uploadBlobs, uploadEvidence, uploadEvidenceBlobs, uploadToCase } from "./api";
import { prepareFilesForUpload } from "./compress";

/**
 * Two ways in, chosen by deployment shape.
 *
 *   "proxy" - multipart through the Next route to FastAPI. Fine behind a normal
 *             server, and what local development uses.
 *   "blob"  - the browser PUTs each file to Blob storage and sends only URLs.
 *             Preferred on Vercel, where a function request body caps near 4.5MB
 *             — below a typical phone photo of a parking notice.
 *
 * Default is "proxy" so a checkout with no Blob store keeps working. Phone
 * photos are compressed client-side first so they fit under Vercel's body
 * limit when blob mode is off.
 */
export type UploadMode = "proxy" | "blob";

export const UPLOAD_MODE: UploadMode =
  process.env.NEXT_PUBLIC_UPLOAD_MODE === "blob" ? "blob" : "proxy";

export const MAX_UPLOAD_BYTES = Number(
  process.env.NEXT_PUBLIC_MAX_UPLOAD_BYTES ?? 10 * 1024 * 1024,
);

export function tooLarge(files: File[]): File[] {
  return files.filter((f) => f.size > MAX_UPLOAD_BYTES);
}

async function prepare(files: File[]): Promise<File[]> {
  // Shrink large phone photos before any network hop — otherwise Vercel drops
  // the request (413 FUNCTION_PAYLOAD_TOO_LARGE) and the browser often reports
  // a generic "could not reach the appeal service" error.
  const prepared = UPLOAD_MODE === "proxy" ? await prepareFilesForUpload(files) : files;

  const oversized = tooLarge(prepared);
  if (oversized.length > 0) {
    const mb = Math.round(MAX_UPLOAD_BYTES / (1024 * 1024));
    throw new ApiError(
      `${oversized.map((f) => f.name).join(", ")} is larger than ${mb}MB. ` +
        "Try a photo taken at a lower resolution, or upload a PDF.",
      413,
    );
  }
  return prepared;
}

async function toBlobs(files: File[]): Promise<{ url: string; filename: string }[]> {
  // Dynamic import: the client SDK is only pulled into the bundle for
  // deployments that actually use it.
  const { upload } = await import("@vercel/blob/client");
  return Promise.all(
    files.map(async (file) => {
      const result = await upload(file.name, file, {
        access: "public",
        handleUploadUrl: "/api/blob/upload",
        contentType: file.type || undefined,
      });
      return { url: result.url, filename: file.name };
    }),
  );
}

export async function sendFiles(caseId: string, files: File[]): Promise<UploadResult> {
  const prepared = await prepare(files);
  if (UPLOAD_MODE === "proxy") return uploadToCase(caseId, prepared);
  return uploadBlobs(caseId, await toBlobs(prepared));
}

/** A document the case asked for mid-way (e.g. a receipt), then continue. */
export async function sendEvidence(
  caseId: string,
  kind: string,
  files: File[],
): Promise<AppealResponse> {
  const prepared = await prepare(files);
  if (UPLOAD_MODE === "proxy") return uploadEvidence(caseId, kind, prepared);
  return uploadEvidenceBlobs(caseId, kind, await toBlobs(prepared));
}
