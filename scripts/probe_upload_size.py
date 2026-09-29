"""Reproduce Vercel multipart size limit against production proxy."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

CREATE = urllib.request.Request(
    "https://pcn-appeal.vercel.app/api/cases",
    method="POST",
    data=b"{}",
    headers={"Content-Type": "application/json"},
)
case = json.load(urllib.request.urlopen(CREATE, timeout=30))
cid = case["case_id"]
print("case", cid)

boundary = "----bound" + uuid.uuid4().hex
payload = b"x" * (5 * 1024 * 1024)
head = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="files"; filename="big.jpg"\r\n'
    f"Content-Type: image/jpeg\r\n\r\n"
).encode()
tail = f"\r\n--{boundary}--\r\n".encode()
body = head + payload + tail

req = urllib.request.Request(
    f"https://pcn-appeal.vercel.app/api/cases/{cid}/files",
    data=body,
    method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        print("upload_ok", resp.status, resp.read()[:200])
except urllib.error.HTTPError as exc:
    print("upload_http", exc.code, exc.read()[:500])
except Exception as exc:
    print("upload_fail", type(exc).__name__, exc)
