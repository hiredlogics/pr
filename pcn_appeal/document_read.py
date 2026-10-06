"""Reading a notice: the revision it was read at, what was read, and not reading it twice.

The document-reading path is classification -> one primary extraction ->
a targeted check of any critical field still in doubt. Two things were wrong
with how that path treated an unchanged document:

* nothing remembered a successful read, so any second pass over the same pages
  (a retry after a later failure, a resume, a re-run of intake) sent the same
  images to the model again;
* the result existed only as scattered facts, with no single record of what was
  read, how sure the reader was, and from which page.

`document_revision` names exactly the pages that would be read. `memo_call`
runs a model call once per revision and replays the stored answer afterwards.
`build_document_read_result` is the one structured output of the whole path.
Nothing here reasons about the customer, the law or the knowledge base.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Callable

from .models import CaseFile

MEMO_KEY = "_doc_read_memo"
RESULT_KEY = "_document_read_result"
# A stored model answer larger than this is not kept: it would crowd the case
# record, and the next pass simply reads that revision again.
MAX_MEMO_CHARS = 30000

# The fields a notice read returns, in the order they are reported.
READ_FIELDS = (
    "operator_name", "pcn_number", "vrm", "parking_event_date", "notice_issue_date",
    "parking_location", "entry_time", "exit_time", "alleged_breach",
)


def input_digest(text: str, images: list[bytes] | None) -> str:
    """The exact text and page bytes a model would be shown."""
    h = hashlib.sha256((text or "").encode())
    for img in images or ():
        h.update(hashlib.sha256(img).digest())
    return h.hexdigest()


def document_revision(case: CaseFile) -> str:
    """Names the uploaded pages as they are now. Any change to what a model
    would read - a new page, a replaced file, different text - changes it."""
    h = hashlib.sha256()
    for ev_id in sorted(case.evidence):
        e = case.evidence[ev_id]
        h.update(f"{ev_id}\0{e.filename}\0{e.text or ''}\0".encode())
        for img in e.images or ():
            h.update(hashlib.sha256(img).digest())
    return h.hexdigest()[:16]


def _memo(case: CaseFile) -> dict:
    try:
        held = json.loads((case.raw_answers or {}).get(MEMO_KEY) or "{}")
    except Exception:
        return {}
    return held if isinstance(held, dict) else {}


def memo_call(case: CaseFile, kind: str, digest: str,
              call: Callable[[], Any]) -> Any:
    """Run `call` once for this input; afterwards replay what it returned.

    Only a successful, JSON-safe answer is kept. A failure is never stored, so
    a read that timed out is attempted again, and a read that worked never is.
    """
    held = _memo(case).get(kind)
    if isinstance(held, dict) and held.get("digest") == digest and "output" in held:
        case.audit.append({"event": "document_read_reused", "kind": kind,
                           "input_sha256": digest})
        return held["output"]
    out = call()
    try:
        blob = json.dumps({"digest": digest, "output": out}, default=str)
    except Exception:
        return out
    if len(blob) <= MAX_MEMO_CHARS:
        memo = _memo(case)
        memo[kind] = json.loads(blob)
        case.raw_answers[MEMO_KEY] = json.dumps(memo)
    return out


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _page_of(ref: str) -> int | None:
    m = re.search(r"#p(\d+)", ref or "")
    return int(m.group(1)) if m else None


def build_document_read_result(case: CaseFile) -> dict:
    """What was read from the notice, field by field.

    Every field carries its value, the reader's confidence, where it came from
    and which page. A field that was not read is present with a null value and
    zero confidence, so a caller never has to guess between "absent" and
    "forgotten". `uncertain` lists the fields a customer could not rely on.
    """
    fields: dict[str, dict] = {}
    uncertain: list[str] = []
    for name in READ_FIELDS:
        node = case.facts.get(name) if getattr(case, "facts", None) else None
        if node is None or node.value in (None, ""):
            fields[name] = {"value": None, "confidence": 0.0, "source": None, "page": None}
            continue
        ref = getattr(node.source, "ref", "") or ""
        fields[name] = {
            "value": _plain(node.value),
            "confidence": float(node.confidence or 0.0),
            "source": ref.split("#", 1)[0] or getattr(node.source.kind, "value", None),
            "page": _page_of(ref),
        }
        if not node.usable:
            uncertain.append(name)
    return {
        "document_revision": document_revision(case),
        "fields": fields,
        "uncertain": uncertain,
        "pages": {e.evidence_id: {"filename": e.filename,
                                  "images": len(e.images or []),
                                  "text_chars": len(e.text or "")}
                  for e in case.evidence.values()},
        "notice_sides_complete": case.get("notice_sides_complete"),
    }


def store_document_read_result(case: CaseFile) -> dict:
    result = build_document_read_result(case)
    case.raw_answers[RESULT_KEY] = json.dumps(result, default=str)
    return result


def load_document_read_result(case: CaseFile) -> dict | None:
    try:
        return json.loads((case.raw_answers or {}).get(RESULT_KEY) or "null")
    except Exception:
        return None
