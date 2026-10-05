"""Notice front/reverse completeness for in-scope private-parking PCNs.

Server-side gate. Two copies of the same front are not completeness.
Out-of-scope documents must not be forced through these requirements.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from .models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from .rules import scope

REVERSE_NAME = re.compile(r"(?:back|reverse|rear|verso|page\s*[2-9])\b", re.I)
# Wording that typically appears on the reverse / continuation of a postal NTK.
# Grouped: a front page commonly cites "Protection of Freedoms Act 2012,
# Schedule 4" on its own, so one group is never enough to stand in for a reverse.
REVERSE_TEXT_GROUPS = (
    re.compile(r"protection\s+of\s+freedoms|schedule\s+4", re.I),
    re.compile(r"how\s+to\s+appeal", re.I),
    re.compile(r"pass\s+(?:this|the)\s+notice", re.I),
    re.compile(r"independent\s+appeals|ias\s+or\s+popla", re.I),
)
REVERSE_TEXT = re.compile("|".join(g.pattern for g in REVERSE_TEXT_GROUPS), re.I)
# Page sides (from the classifier) that show something other than the face.
NON_FRONT_SIDES = {"REVERSE", "CONTINUATION", "BLANK"}

DIFFERENT_NOTICES_MESSAGE = (
    "These pages look like they come from different notices: the charge number or the "
    "vehicle registration does not match. Please upload the front and back of the same notice."
)
DUPLICATE_PAGES_MESSAGE = (
    "Two of your photos are the same picture. Please upload the front and the back of "
    "your notice. Both sides are mandatory, even if the back is blank."
)

BOTH_SIDES_MESSAGE = (
    "Please upload the front and back of your notice. Both sides are mandatory, "
    "even if the back is blank. You cannot continue until both sides have been uploaded."
)

FRONT_REQUIRED_MESSAGE = (
    "Please upload the front of your notice. The back is optional — add it if you "
    "have it, and we will check the wording printed there too."
)
OPTIONAL_REVERSE_REJECTED_MESSAGE = (
    "The extra page you added looks like it comes from a different notice: the charge "
    "number or the vehicle registration does not match. We have set it aside and kept "
    "going with the front of your notice. You can remove it or upload the right one."
)


def front_page_present(case: CaseFile) -> tuple[bool, str]:
    """The private-parking upload rule: a front page, and nothing more.

    The reverse is optional. It is not asked for, and its absence does not stop
    the case: it leaves `notice_sides_complete` False, which is what the PoFA
    content findings already gate on, so a finding that needs reverse wording
    stays unresolved instead of being invented. Completeness is still assessed
    and recorded by `assess_notice_sides` — it just no longer gates progress.

    Whether that page can be read at all is the classifier's judgement, not
    this gate's: an unreadable upload already stops at intake.
    """
    for e in _notice_evidence(case) or list(case.evidence.values()):
        if (getattr(e, "images", None) or []) or (getattr(e, "text", "") or "").strip():
            return True, "front_page_present"
    return False, "no_page_uploaded"


def rejectable_optional_page(case: CaseFile) -> Optional[str]:
    """The evidence id of an optional extra page that belongs to another notice.

    Returns None when the pages agree, when only one page was uploaded, or when
    the mismatch implicates the first page — the front is what the customer is
    appealing, so it is never the page set aside.
    """
    differed = different_notices(case)
    if not differed:
        return None
    order = [e.evidence_id for e in _notice_evidence(case)] or list(case.evidence)
    later = [d for d in (differed.get("documents") or []) if d in order[1:]]
    if not later or len(order) < 2:
        return None
    return max(later, key=order.index)


def upload_pages_sufficient(evidence_items: list) -> tuple[bool, str]:
    """Pre-classification gate: enough distinct pages / multipage PDF?

    Used at the initial upload endpoints so a single face never enters analysis.
    Two identical images (same bytes) are not sufficient.
    """
    page_hashes: list[str] = []
    multi_page_text = False
    for e in evidence_items:
        for img in getattr(e, "images", None) or []:
            page_hashes.append(_image_sha(img))
        text = getattr(e, "text", "") or ""
        if text.count("\f") >= 1 or text.count("--- page") >= 1:
            multi_page_text = True
        # PDF rendered to multiple page images by ingest
        if len(getattr(e, "images", None) or []) >= 2:
            pass

    unique = len(set(page_hashes))
    total = len(page_hashes)
    if multi_page_text or unique >= 2:
        return True, "distinct_pages_or_multipage"
    if total >= 2 and unique < 2:
        return False, "duplicate_front_images"
    return False, "front_only_or_single_page"


def _page_sides(case: CaseFile, notice_ev: list) -> list[str]:
    """The classifier's side label for every notice image page, or [] when the
    classifier gave no usable labels (an older answer, or a text-only upload)."""
    sides: list[str] = []
    for e in notice_ev:
        labels = {p.get("page"): p.get("side")
                  for p in ((case.classifications or {}).get(e.evidence_id) or {}).get("pages") or []
                  if isinstance(p, dict)}
        for n in range(1, len(e.images or []) + 1):
            sides.append(str(labels.get(n) or "UNKNOWN"))
    return sides if any(s != "UNKNOWN" for s in sides) else []


def rejection_message(reason: str) -> str:
    """Customer wording for an upload refused by the notice gate."""
    if reason == "different_notices":
        return DIFFERENT_NOTICES_MESSAGE
    if reason == "duplicate_front_images":
        return DUPLICATE_PAGES_MESSAGE
    if reason == "no_readable_page":
        return FRONT_REQUIRED_MESSAGE
    return BOTH_SIDES_MESSAGE


def _norm_ref(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _charge_number_like(value: str) -> bool:
    """A normalised reading that could be a charge number: mostly digits.
    Form and print codes on a reverse ("ABCDEF/P/0524") are mostly letters,
    and comparing them with the face's charge number refused genuine pairs."""
    digits = sum(ch.isdigit() for ch in value)
    return len(value) >= 6 and digits >= 5 and digits * 10 >= len(value) * 6


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def different_notices(case: CaseFile) -> Optional[dict]:
    """Pages that print a different vehicle registration or charge number are
    not two sides of one notice. Read from the classifier's per-document
    references, because a photo has no text layer for the extractor's own
    cross-check to scan. Values count as different only when more than 60% of
    their characters differ. Live, repeated reads of one blurred notice put its
    two sides up to four characters of seven apart, while two different
    notices differed in every character: refusing a genuine pair leaves the
    customer stuck, and a smaller misread is shown on the confirmation screen."""
    seen: dict[str, dict[str, str]] = {"vrm": {}, "pcn_number": {}}
    for ev_id, c in (case.classifications or {}).items():
        if (c or {}).get("document_type") not in (None, "PRIVATE_PARKING_NOTICE"):
            continue
        refs = (c or {}).get("references") or {}
        for key in ("vrm", "pcn_number"):
            value = _norm_ref(refs.get(key))
            if len(value) < 4 or (key == "pcn_number" and not _charge_number_like(value)):
                continue
            for other_id, other in seen[key].items():
                # Charge numbers from one scheme share a length. A long print or
                # barcode number on a reverse is not another notice's PCN.
                if key == "pcn_number" and abs(len(value) - len(other)) > 2:
                    continue
                if _edits(value, other) * 10 > max(len(value), len(other)) * 6:
                    return {"field": key, "documents": [other_id, ev_id]}
            seen[key][ev_id] = value
    return None


def notice_pages_sufficient(case: CaseFile) -> tuple[bool, str]:
    """upload_pages_sufficient, with two cross-checks first: identical images
    and pages from different notices are refused whatever their labels. Then
    the classifier's page sides decide when it gave them: two distinct photos
    that are both the face are not front and back. Falls back to the
    page-count rule when no page is labelled."""
    evidence = list(case.evidence.values())
    hashes = [_image_sha(img) for e in evidence for img in (e.images or [])]
    unique = len(set(hashes))
    if len(hashes) >= 2 and unique < 2:
        return False, "duplicate_front_images"
    if different_notices(case):
        return False, "different_notices"
    sides = _page_sides(case, evidence)
    if sides:
        if any(s in NON_FRONT_SIDES for s in sides) and unique >= 2:
            return True, "classifier_labelled_reverse_page"
        if all(s == "FRONT" for s in sides):
            return False, "only_front_pages"
    return upload_pages_sufficient(evidence)


def _image_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _notice_evidence(case: CaseFile) -> list:
    return [
        e for e in case.evidence.values()
        if e.kind in ("PCN", "NTK", "NTD")
        or "pcn" in (e.filename or "").lower()
        or "ntk" in (e.filename or "").lower()
        or "notice" in (e.filename or "").lower()
    ]


def _in_scope_private_parking(case: CaseFile) -> bool:
    """True when this case is an ordinary private-parking notice appeal."""
    stop = scope.decide(case)
    if stop is not None:
        return False
    kinds = set(case.document_classes.values()) | {e.kind for e in case.evidence.values()}
    if kinds & {"DEBT_RECOVERY", "COUNCIL_PCN"}:
        return False
    return bool(kinds & {"PCN", "NTK", "NTD", "OTHER"} or case.evidence)


def assess_notice_sides(case: CaseFile) -> dict[str, Any]:
    """Derive notice_sides_complete with provenance. Never invents a reverse."""
    notice_ev = _notice_evidence(case)
    result: dict[str, Any] = {
        "applicable": False,
        "complete": None,
        "reason": "no notice evidence",
        "unique_page_images": 0,
        "total_page_images": 0,
        "duplicate_pages": False,
        "same_notice": None,
    }
    if not notice_ev:
        return result

    if not _in_scope_private_parking(case):
        result["applicable"] = False
        result["complete"] = None
        result["reason"] = "out_of_scope_document"
        return result

    result["applicable"] = True

    page_hashes: list[str] = []
    for e in notice_ev:
        for img in e.images or []:
            page_hashes.append(_image_sha(img))

    unique = len(set(page_hashes))
    total = len(page_hashes)
    result["unique_page_images"] = unique
    result["total_page_images"] = total
    result["duplicate_pages"] = total >= 2 and unique < 2

    multi_page_text = any(
        (e.text or "").count("\f") >= 1 or (e.text or "").count("--- page") >= 1
        for e in notice_ev
    )
    # One multipage PDF / multi-image evidence item with distinct pages.
    single_ev_multi = any(len(e.images or []) >= 2 for e in notice_ev)

    named_reverse = any(REVERSE_NAME.search(e.filename or "") for e in notice_ev)
    text_blob = "\n".join((e.text or "") for e in notice_ev)
    text_has_reverse = sum(1 for g in REVERSE_TEXT_GROUPS if g.search(text_blob or "")) >= 2
    # Vision may have transcribed invitation / reverse content into bools even
    # when OCR char count is 0 — that is page-backed evidence, not unreadability.
    vision_reverse_signal = (
        case.get("ntk_invites_pass_to_driver") is not None
        or case.get("ntk_invites_name_driver") is not None
    )

    # Same-notice check: a different registration or charge number printed on
    # another page, or conflicting PCN numbers across uploads → not complete.
    if different_notices(case):
        result["complete"] = False
        result["same_notice"] = False
        result["reason"] = "different_notices"
        return result
    if case.get("pcn_conflict"):
        result["complete"] = False
        result["same_notice"] = False
        result["reason"] = "conflicting_pcn_across_pages"
        return result
    result["same_notice"] = True

    if result["duplicate_pages"]:
        result["complete"] = False
        result["reason"] = "duplicate_front_images"
        return result

    # Page-side labels decide when they exist: distinct images are not two
    # sides if every labelled page is the face. UNKNOWN pages are not counted
    # either way; when nothing is labelled the rules below apply unchanged.
    sides = _page_sides(case, notice_ev)
    result["page_sides"] = sides
    if sides:
        if any(s in NON_FRONT_SIDES for s in sides) and unique >= 2:
            result["complete"] = True
            result["reason"] = "classifier_labelled_reverse_page"
            return result
        if all(s in ("FRONT", "UNKNOWN") for s in sides) and "UNKNOWN" not in sides:
            result["complete"] = False
            result["reason"] = "only_front_pages"
            return result

    if unique >= 2 or (single_ev_multi and unique >= 2) or multi_page_text:
        result["complete"] = True
        result["reason"] = "distinct_pages_or_multipage"
        return result

    if named_reverse and total >= 1 and unique >= 1 and len(notice_ev) >= 2:
        # Explicit reverse filename plus another upload — accept only when hashes differ.
        if unique >= 2:
            result["complete"] = True
            result["reason"] = "named_reverse_with_distinct_pages"
            return result

    if text_has_reverse and len(text_blob) >= 200:
        # Substantial text covering reverse-typical wording in one upload (e.g. full PDF text).
        result["complete"] = True
        result["reason"] = "text_includes_reverse_particulars"
        return result

    if vision_reverse_signal and unique >= 2:
        result["complete"] = True
        result["reason"] = "vision_reverse_signals_on_distinct_pages"
        return result

    # Counting two evidence rows without distinct page images is not completeness
    # (two copies of the front used to pass the old len(notice_ev) >= 2 rule).
    if len(notice_ev) >= 2 and unique < 2 and total < 2 and not multi_page_text:
        result["complete"] = False
        result["reason"] = "multiple_uploads_without_distinct_pages"
        return result

    result["complete"] = False
    result["reason"] = "front_only_or_single_page"
    return result


def apply_notice_sides_fact(case: CaseFile) -> dict[str, Any]:
    """Write notice_sides_complete when applicable; leave untouched for out-of-scope."""
    assessed = assess_notice_sides(case)
    if not assessed["applicable"]:
        case.audit.append({"event": "notice_sides_assessment", **assessed})
        return assessed
    complete = bool(assessed["complete"])
    case.put(Fact(
        "F-notice_sides_complete", "notice_sides_complete", complete,
        FactStatus.DERIVED,
        FactSource(SourceKind.CALCULATION, f"notice_sides:{assessed['reason']}"),
    ))
    case.audit.append({"event": "notice_sides_assessment", **assessed})
    return assessed


# `requires_complete_notice` and `incompleteness_payload` are deliberately gone.
# They held a case whose reverse page was missing and returned a NEEDS_DOCUMENTS
# screen asking for it. The reverse is optional: its absence is recorded in
# `notice_sides_complete` and read by the findings that need reverse wording,
# and it stops nothing. Removing the hold rather than making it always-false
# keeps it from being reintroduced by accident.
