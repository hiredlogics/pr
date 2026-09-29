/**
 * Shrink phone photos before they hit the Next → Railway proxy.
 *
 * Vercel's serverless request body caps near 4.5MB. An iPhone JPEG is often
 * 5–12MB, so the browser fetch aborts and the UI shows "could not reach the
 * appeal service" even though /health is fine. Compressing keeps the existing
 * proxy path working without requiring Blob storage.
 */
const PROXY_SAFE_BYTES = 3.5 * 1024 * 1024;
const MAX_EDGE = 2000;

function isRasterImage(file: File): boolean {
  if (file.type === "image/jpeg" || file.type === "image/png" || file.type === "image/webp") {
    return true;
  }
  // Some mobile browsers leave type empty; fall back to extension.
  return /\.(jpe?g|png|webp)$/i.test(file.name);
}

async function canvasToBlob(
  canvas: HTMLCanvasElement,
  type: string,
  quality: number,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Could not compress the photo."))),
      type,
      quality,
    );
  });
}

/** Return a JPEG small enough for the Vercel proxy, or the original file. */
export async function prepareForUpload(file: File): Promise<File> {
  if (!isRasterImage(file) || file.size <= PROXY_SAFE_BYTES) return file;
  if (typeof createImageBitmap !== "function") return file;

  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return file;
  }

  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close();
    return file;
  }
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();

  let quality = 0.82;
  let blob = await canvasToBlob(canvas, "image/jpeg", quality);
  while (blob.size > PROXY_SAFE_BYTES && quality > 0.45) {
    quality -= 0.12;
    blob = await canvasToBlob(canvas, "image/jpeg", quality);
  }

  // If still too large, keep the compressed attempt — caller still enforces MAX.
  const base = file.name.replace(/\.[^.]+$/, "") || "notice";
  return new File([blob], `${base}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
}

export async function prepareFilesForUpload(files: File[]): Promise<File[]> {
  return Promise.all(files.map((f) => prepareForUpload(f)));
}
