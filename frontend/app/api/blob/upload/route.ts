import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";

/**
 * Issues a short-lived token so the browser can upload straight to Blob storage.
 *
 * The file never passes through this function, which is the whole point: a
 * serverless request body caps out well below the size of a phone photo. Only
 * the resulting URL comes back to us, and the API re-validates it before
 * fetching (https, host allowlist, size cap - see ingest.fetch_upload).
 *
 * This path is more specific than the catch-all proxy at app/api/[...path], so
 * Next routes it here rather than forwarding it to FastAPI.
 */
export const dynamic = "force-dynamic";

const ALLOWED_CONTENT_TYPES = [
  "image/jpeg",
  "image/png",
  "image/webp",
  // No HEIC/HEIF: the API cannot decode them, so storing one only defers the
  // failure to after the upload.
  "application/pdf",
  "text/plain",
];

const MAX_BYTES = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_BYTES ?? 10 * 1024 * 1024);

export async function POST(request: Request): Promise<Response> {
  if (!process.env.BLOB_READ_WRITE_TOKEN) {
    return Response.json(
      { detail: { message: "Blob uploads are not configured on this deployment." } },
      { status: 501 },
    );
  }

  try {
    const body = (await request.json()) as HandleUploadBody;
    const result = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async () => ({
        allowedContentTypes: ALLOWED_CONTENT_TYPES,
        maximumSizeInBytes: MAX_BYTES,
        // Random suffix: two customers photographing "notice.jpg" must not
        // collide, and a guessable path would expose someone else's notice.
        addRandomSuffix: true,
      }),
      // Nothing to do on completion. The case is not advanced here - the client
      // sends the URLs to the API, which decides whether they are readable.
      onUploadCompleted: async () => {},
    });
    return Response.json(result);
  } catch (e) {
    const message = e instanceof Error ? e.message : "Upload could not be prepared.";
    return Response.json({ detail: { message } }, { status: 400 });
  }
}
