"""Every image type the upload path accepts must be one it can read.

IMAGE_TYPES listed WebP, HEIC and HEIF while _to_jpeg tried only PyMuPDF, which
reads none of them: a phone screenshot saved as WebP was accepted and then
rejected as "unknown image file format". WebP now decodes via Pillow; HEIC is
no longer advertised and says plainly what to upload instead.
"""
from __future__ import annotations

import io
import unittest

from PIL import Image

from pcn_appeal import ingest
from pcn_appeal.ingest import UnsupportedUpload, read_upload

PILLOW_FORMAT = {"image/jpeg": "JPEG", "image/jpg": "JPEG", "image/png": "PNG",
                 "image/webp": "WEBP", "image/tiff": "TIFF", "image/gif": "GIF",
                 "image/bmp": "BMP"}


def sample(fmt: str, mode: str = "RGB") -> bytes:
    im = Image.new(mode, (64, 40), (180, 40, 40) if mode == "RGB" else (180, 40, 40, 120))
    out = io.BytesIO()
    im.save(out, fmt)
    return out.getvalue()


def is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


class AcceptedMeansReadable(unittest.TestCase):

    def test_every_accepted_image_type_decodes_to_jpeg(self):
        self.assertEqual(set(PILLOW_FORMAT), ingest.IMAGE_TYPES,
                         "a type added to IMAGE_TYPES needs a decode test here")
        for content_type, fmt in PILLOW_FORMAT.items():
            with self.subTest(content_type=content_type):
                doc = read_upload("E1", "page", content_type, sample(fmt))
                self.assertEqual(len(doc.images), 1)
                self.assertTrue(is_jpeg(doc.images[0]))

    def test_webp_with_transparency_is_flattened(self):
        doc = read_upload("E1", "shot.webp", "image/webp", sample("WEBP", "RGBA"))
        self.assertTrue(is_jpeg(doc.images[0]))
        with Image.open(io.BytesIO(doc.images[0])) as im:
            self.assertEqual(im.mode, "RGB")

    def test_webp_is_recognised_by_extension_alone(self):
        doc = read_upload("E1", "screenshot.webp", None, sample("WEBP"))
        self.assertTrue(is_jpeg(doc.images[0]))

    def test_heic_is_not_advertised_and_says_what_to_do(self):
        self.assertNotIn("image/heic", ingest.IMAGE_TYPES)
        self.assertNotIn("image/heif", ingest.IMAGE_TYPES)
        heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
        with self.assertRaises(UnsupportedUpload) as ctx:
            read_upload("E1", "IMG_0001.heic", "image/heic", heic)
        self.assertIn("JPEG or PNG", str(ctx.exception))

    def test_garbage_is_still_refused(self):
        with self.assertRaises(UnsupportedUpload):
            read_upload("E1", "photo.jpg", "image/jpeg", b"not an image at all")


class FrontendAgrees(unittest.TestCase):
    """The browser's lists must not offer what the API cannot read."""

    def _read(self, path):
        with open(path) as fh:
            return fh.read()

    def test_picker_offers_webp_and_not_heic(self):
        src = self._read("frontend/components/UploadStep.tsx")
        accept = next(l for l in src.splitlines() if l.startswith("const ACCEPT"))
        self.assertIn("image/webp", accept)
        self.assertNotIn("heic", accept.lower())

    def test_blob_upload_does_not_store_heic(self):
        src = self._read("frontend/app/api/blob/upload/route.ts")
        block = src[src.index("ALLOWED_CONTENT_TYPES"):src.index("];")]
        self.assertIn('"image/webp"', block)
        self.assertNotIn('"image/heic"', block)
        self.assertNotIn('"image/heif"', block)


if __name__ == "__main__":
    unittest.main()
