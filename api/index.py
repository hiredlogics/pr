"""Vercel Python entrypoint for the appeal API.

Vercel routes every request under /api to this file and expects an ASGI app in
`app`, so this is a one-line adapter over pcn_appeal.api.

A container is still the better home for this service - see DEPLOY.md - and two
things genuinely do not work here:

  * PDF rendering. WeasyPrint draws through Pango and cairo, which are system
    libraries a function cannot install. The endpoint returns 503 with a reason;
    the letter text is unaffected.
  * Multipart upload of anything large. A function request body caps out well
    below the size of a phone photo, so set NEXT_PUBLIC_UPLOAD_MODE=blob on the
    frontend and let the browser upload to Blob storage instead.

DATABASE_URL is not optional in this deployment. Function instances are
ephemeral and not shared, so without it a case created by one request does not
exist for the next.
"""
import sys
from pathlib import Path

# The package lives one level up from this file.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pcn_appeal.api import app  # noqa: E402

__all__ = ["app"]
