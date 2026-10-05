"use client";

import { useRef, useState } from "react";
import { MAX_UPLOAD_BYTES } from "@/lib/upload";

/** PDF + JPEG + PNG only — matches the extraction API capabilities customers use. */
// WebP is accepted because lib/compress.ts re-encodes it to JPEG and the API
// decodes it. HEIC is deliberately absent: listing only these types makes iOS
// hand over a JPEG, and the API cannot decode HEIC.
const ACCEPT = "image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf";
const MAX_LABEL = 1024 * 1024;

const FRONT_REQUIRED_MESSAGE =
  "Please upload the front of your notice before continuing. The back is optional.";

/** The four documents an operator sends, so it is obvious what to photograph. */
const SAMPLES: { label: string; tone?: "warn" }[] = [
  { label: "Parking Charge Notice" },
  { label: "Notice to Keeper", tone: "warn" },
  { label: "Reminder Notice" },
  { label: "Final Notice" },
];

function size(bytes: number): string {
  if (bytes < MAX_LABEL) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The front is required; the back is optional, so one file is enough. */
function frontReady(files: File[]): boolean {
  return files.length >= 1;
}

export default function UploadStep({
  files,
  onFilesChange,
  busy,
  vision = true,
  onSubmit,
}: {
  /** Owned by the page: this component unmounts while the reading screen is up. */
  files: File[];
  onFilesChange: (files: File[]) => void;
  busy: boolean;
  /** When false, only the text of a PDF can be read - warn before a photo is chosen. */
  vision?: boolean;
  onSubmit: () => void;
}) {
  const [over, setOver] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const pickRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  const ready = frontReady(files);

  function add(incoming: FileList | null) {
    if (!incoming || incoming.length === 0) return;
    setLocalError(null);
    const seen = new Set(files.map((f) => `${f.name}:${f.size}`));
    const next = [...files];
    for (const f of Array.from(incoming)) {
      if (!seen.has(`${f.name}:${f.size}`)) next.push(f);
    }
    onFilesChange(next);
  }

  function remove(target: File) {
    onFilesChange(files.filter((f) => f !== target));
    setLocalError(null);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) {
      setLocalError(FRONT_REQUIRED_MESSAGE);
      return;
    }
    onSubmit();
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <div>
        <h1>Upload your Private Parking Notice</h1>
        <p className="lede">
          Upload a clear photo or PDF of your notice and we will extract the key details for you.
        </p>
      </div>

      <div className="notice" data-tone="attention" role="status">
        <p>
          <b>Front of the notice — required.</b> This is the page with the charge number and
          your vehicle registration on it.
        </p>
        <p style={{ marginTop: 8 }}>
          <b>Back of the notice — optional.</b> Add it if you have it and we will check the
          wording printed there as well. You can continue without it.
        </p>
      </div>

      <div>
        {/*
          iOS Safari often ignores programmatic input.click() on a clipped/sr-only
          file input. The pick control is a real <input type="file"> stretched over
          the dropzone so a tap hits the native control directly. Camera stays a
          separate higher-z control so it still opens the rear camera.
        */}
        <div
          className="dropzone"
          data-over={over}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            add(e.dataTransfer.files);
          }}
        >
          <input
            ref={pickRef}
            id="pick-files"
            className="dz-file-input"
            type="file"
            accept={ACCEPT}
            multiple
            disabled={busy}
            aria-label="Upload parking notice PDF or photo"
            onChange={(e) => {
              add(e.target.files);
              e.target.value = "";
            }}
          />

          <div className="dz-content" aria-hidden="true">
            <div className="dz-icon">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 16V8m0 0-3.5 3.5M12 8l3.5 3.5" />
                <path d="M20 16.6A4.5 4.5 0 0 0 17.5 8h-1A6.5 6.5 0 1 0 5 14.2" />
              </svg>
            </div>
            <p className="dz-title">
              Drag and drop the front here, or click to upload
            </p>
            <p className="dz-sub">
              {vision
                ? `Accepted formats: JPG, PNG, PDF (Max ${Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))}MB)`
                : "Accepted here: PDF with selectable text, or a text file. Photos and scans need a vision model, which is not configured."}
            </p>
          </div>

          <div className="dz-actions">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                cameraRef.current?.click();
              }}
            >
              Photograph the notice
            </button>
          </div>

          <input
            ref={cameraRef}
            id="camera-files"
            className="sr-only"
            type="file"
            accept="image/jpeg,image/png,.jpg,.jpeg,.png"
            capture="environment"
            multiple
            disabled={busy}
            tabIndex={-1}
            onChange={(e) => {
              add(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        <ul className="samples" aria-label="Documents we can read">
          {SAMPLES.map((sample) => (
            <li key={sample.label} data-tone={sample.tone}>
              <span className="thumb" aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
                <i />
              </span>
              {sample.label}
            </li>
          ))}
        </ul>

        {files.length > 0 && (
          <ul className="filelist">
            {files.map((f) => (
              <li className="filerow" key={`${f.name}:${f.size}:${f.lastModified}`}>
                <span className="fname">{f.name}</span>
                <span className="fsize">{size(f.size)}</span>
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => remove(f)}
                  aria-label={`Remove ${f.name}`}
                >
                  &times;
                </button>
              </li>
            ))}
          </ul>
        )}

        {files.length === 1 && (
          <p className="lede" style={{ marginTop: 8 }}>
            You can add the back of the notice too, or continue with the front alone.
          </p>
        )}

        <p className="trust">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 3l7 3v5.5c0 4.3-2.9 8.2-7 9.5-4.1-1.3-7-5.2-7-9.5V6l7-3z" />
            <path d="m9 12 2 2 4-4" />
          </svg>
          <span>
            <b>Your information is secure</b> and used only to generate your appeal.
          </span>
        </p>
      </div>

      {localError && (
        <div className="notice" data-tone="attention" role="alert">
          {localError}
        </div>
      )}

      <button className="btn btn-primary" type="submit" disabled={busy || !ready}>
        {busy ? "Reading your notice…" : "Upload notice"}
      </button>
    </form>
  );
}
