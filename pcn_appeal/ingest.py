"""Turn an uploaded file into something Engine 1 can read.

A customer receives a PCN on paper and photographs it, or gets a PDF by email.
Neither arrives as text, so this module is what stands between "a file" and the
extraction engine:

    image/*            -> normalised to JPEG, sent to the vision model
    application/pdf    -> text layer if it has one, otherwise pages rasterised
                          to JPEG and sent to the vision model
    text/plain, .md    -> decoded as-is

A scanned PDF has no text layer, so reading it needs the pixels. The decision
is made per file by measuring the text actually recovered rather than trusting
the extension, because "PDF" says nothing about whether it is born-digital or a
photo of a letter someone printed.

Nothing here interprets the content. Extraction still reads every field with
its own confidence, and uploaded text is still untrusted data (rule EX-06).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

# Per file. Matches what the UI promises and what the Blob upload token allows;
# these three numbers disagreeing is how a customer gets told 10MB and refused at 11.
MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 6                   # a PCN plus a Notice to Keeper never needs more
RASTER_DPI = 150                    # legible for OCR-grade text without huge payloads
MIN_TEXT_CHARS = 120                # below this a PDF is treated as scanned
JPEG_QUALITY = 80

TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", ""}
IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic",
               "image/heif", "image/tiff", "image/gif"}
PDF_TYPES = {"application/pdf", "application/x-pdf"}


class UnsupportedUpload(ValueError):
    pass


@dataclass
class Ingested:
    evidence_id: str
    filename: str
    text: str = ""
    images: list[bytes] = field(default_factory=list)   # always JPEG
    note: str = ""                                      # how it was read, for the audit trail

    @property
    def readable(self) -> bool:
        return bool(self.text.strip() or self.images)


def fetch_upload(evidence_id: str, url: str, filename: Optional[str] = None,
                 *, allowed_hosts: Optional[set[str]] = None, timeout: float = 20.0) -> Ingested:
    """Read a document the browser uploaded straight to blob storage.

    Large photos never cross the web tier this way, which matters because a
    serverless function caps a request body at a few megabytes while a phone
    photo of a notice is routinely more.

    The URL comes from the client, so it is treated as hostile: only hosts on
    the allowlist are fetched, and the download is capped at MAX_BYTES. Without
    the allowlist this endpoint is an SSRF hole - "fetch this URL for me" aimed
    at cloud metadata or an internal address.
    """
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsupportedUpload(f"{url}: only https URLs are accepted")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsupportedUpload(f"{url}: no host")
    allowed = allowed_hosts if allowed_hosts is not None else blob_hosts()
    if allowed and not any(host == h or host.endswith(f".{h}") for h in allowed):
        raise UnsupportedUpload(f"{host}: not an allowed upload host")

    req = Request(url, headers={"User-Agent": "pcn-appeal-ai"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type")
            # Read one byte past the cap so an oversized body is refused rather
            # than silently truncated into a half-readable document.
            data = resp.read(MAX_BYTES + 1)
    except UnsupportedUpload:
        raise
    except Exception as exc:
        raise UnsupportedUpload(f"could not download {filename or host}: {exc}") from exc

    name = filename or (parsed.path.rsplit("/", 1)[-1] or evidence_id)
    return read_upload(evidence_id, name, content_type, data)


def blob_hosts() -> set[str]:
    """Hosts the API will fetch uploads from, via BLOB_ALLOWED_HOSTS.

    Defaults to Vercel Blob's public domain. An empty setting disables the
    allowlist, which is only reasonable in local development.
    """
    import os
    raw = os.getenv("BLOB_ALLOWED_HOSTS", "public.blob.vercel-storage.com")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def read_upload(evidence_id: str, filename: str, content_type: Optional[str], data: bytes) -> Ingested:
    if len(data) > MAX_BYTES:
        raise UnsupportedUpload(f"{filename}: larger than {MAX_BYTES // (1024 * 1024)}MB")
    if not data:
        raise UnsupportedUpload(f"{filename}: empty file")

    kind = _classify(filename, content_type, data)
    if kind == "text":
        return Ingested(evidence_id, filename, text=_decode(data), note="read as text")
    if kind == "image":
        return Ingested(evidence_id, filename, images=[_to_jpeg(data)], note="sent to vision model")
    return _read_pdf(evidence_id, filename, data)


def _classify(filename: str, content_type: Optional[str], data: bytes) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if data[:5] == b"%PDF-":                      # magic bytes beat a wrong content-type
        return "pdf"
    if ct in PDF_TYPES:
        return "pdf"
    if ct in IMAGE_TYPES or ct.startswith("image/"):
        return "image"
    if ct in TEXT_TYPES or ct.startswith("text/"):
        return "text"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        return "pdf"
    if ext in {"jpg", "jpeg", "png", "webp", "heic", "heif", "tif", "tiff", "gif"}:
        return "image"
    if ext in {"txt", "text", "md", "csv"}:
        return "text"
    raise UnsupportedUpload(f"{filename}: unsupported type {ct or ext or 'unknown'!r}. "
                            "Upload a photo, a PDF or a text file.")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _to_jpeg(data: bytes) -> bytes:
    """The LLM clients send images as image/jpeg, so anything else is converted
    rather than mislabelled. Alpha is flattened; JPEG has no alpha channel."""
    import pymupdf
    try:
        pix = pymupdf.Pixmap(data)
    except Exception as exc:
        raise UnsupportedUpload(f"could not read image: {exc}") from exc
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace and pix.colorspace.n == 4:          # CMYK
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)


def _read_pdf(evidence_id: str, filename: str, data: bytes) -> Ingested:
    text = _pdf_text(data)
    if len(text.strip()) >= MIN_TEXT_CHARS:
        return Ingested(evidence_id, filename, text=text, note="read PDF text layer")

    images, pages = _pdf_pages_as_jpeg(data)
    if not images:
        raise UnsupportedUpload(
            f"{filename}: no text layer and the pages could not be rendered. "
            "Photograph the notice and upload the photo instead.")
    note = f"scanned PDF: {len(images)} of {pages} page(s) rendered for the vision model"
    if pages > len(images):
        note += f" (capped at {MAX_PDF_PAGES})"
    return Ingested(evidence_id, filename, text=text.strip(), images=images, note=note)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
    except Exception:
        return ""                                  # encrypted or damaged; rasterising may still work


def _pdf_pages_as_jpeg(data: bytes) -> tuple[list[bytes], int]:
    import pymupdf
    out: list[bytes] = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return [], 0
    with doc:
        total = doc.page_count
        for page in list(doc)[:MAX_PDF_PAGES]:
            try:
                pix = page.get_pixmap(dpi=RASTER_DPI)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                out.append(pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY))
            except Exception:
                continue
    return out, total
