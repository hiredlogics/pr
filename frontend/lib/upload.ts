import { ApiError, type UploadResult } from "./types";
import { uploadBlobs, uploadToCase } from "./api";

/**
 * Two ways in, chosen by deployment shape.
 *
 *   "proxy" - multipart through the Next route to FastAPI. Fine behind a normal
 *             server, and what local development uses.
 *   "blob"  - the browser PUTs each file to Blob storage and sends only URLs.
 *             Required on Vercel, where a function request body caps out well
 *             below the size of a photo of a parking notice.
 *
 * Default is "proxy" so a checkout with no Blob store keeps working.
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

export async function sendFiles(caseId: string, files: File[]): Promise<UploadResult> {
  const oversized = tooLarge(files);
  if (oversized.length > 0) {
    const mb = Math.round(MAX_UPLOAD_BYTES / (1024 * 1024));
    throw new ApiError(
      `${oversized.map((f) => f.name).join(", ")} is larger than ${mb}MB. ` +
        "Try a photo taken at a lower resolution.",
      413,
    );
  }

  if (UPLOAD_MODE === "proxy") return uploadToCase(caseId, files);

  // Dynamic import: the client SDK is only pulled into the bundle for
  // deployments that actually use it.
  const { upload } = await import("@vercel/blob/client");
  const blobs = await Promise.all(
    files.map(async (file) => {
      const result = await upload(file.name, file, {
        access: "public",
        handleUploadUrl: "/api/blob/upload",
        contentType: file.type || undefined,
      });
      return { url: result.url, filename: file.name };
    }),
  );
  return uploadBlobs(caseId, blobs);
}
