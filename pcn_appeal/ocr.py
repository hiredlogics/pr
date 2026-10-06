"""Independent, bounded OCR; transcriptions are never confirmed facts."""
from __future__ import annotations

import io
import os
import shutil
import subprocess

OCR_MARKER = "<ocr_transcription"
MAX_OCR_CHARS = 16000


def available() -> bool:
    return bool(shutil.which("tesseract")) and os.getenv("PCN_OCR_ENABLED", "1") != "0"


def _prepare(data: bytes) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("L")
        scale = min(2.0, 3200 / max(image.size))
        if scale != 1:
            image = image.resize(tuple(max(1, round(n * scale)) for n in image.size),
                                 Image.Resampling.LANCZOS)
        image = ImageOps.autocontrast(image, cutoff=1)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


def transcribe_pages(images: list[bytes]) -> tuple[str, str]:
    if not available():
        return "", "OCR unavailable; vision reading retained"
    pages = []
    failures = 0
    for page, image in enumerate(images, 1):
        try:
            raster = _prepare(image)
            result = subprocess.run(
                [shutil.which("tesseract"), "stdin", "stdout", "-l", "eng", "--psm", "11"],
                input=raster, capture_output=True, timeout=12, check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
            text = result.stdout.decode("utf-8", errors="replace").strip()[:MAX_OCR_CHARS]
            if text:
                pages.append(f'<ocr_transcription page="{page}" engine="tesseract">\n'
                             "Machine OCR may misread characters. Check against the attached page; "
                             "this is not customer confirmation.\n" + text + "\n</ocr_transcription>")
        except (OSError, ValueError, subprocess.SubprocessError):
            failures += 1
    return "\n\n".join(pages), f"OCR: {len(pages)} page(s) transcribed, {failures} failed; vision retained"
