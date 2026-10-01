"""Render a released letter as a branded PDF.

`orchestrator.render()` returns plain text - correct for the API's `letter`
field, useless to actually send. This module is the other half of the Draft ->
customer document path: Jinja2 builds a letterhead around the same text,
WeasyPrint turns that into a PDF.

Nothing here writes appeal wording. It only lays out text the drafter already
produced (`Draft.plain_text()`); the letter's substance is untouched.

Branding is a placeholder deliberately: this repo has no company name, logo or
colour on file. Swap TEMPLATE's constants below for the real ones - nothing
else about the pipeline changes.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment

from .models import Draft, RetrievalPack

# --------------------------------------------------------------------------- branding
# Matches the customer-facing design: the middle word carries the brand colour,
# as it does in the web wordmark. Overridable so a white-label deployment does
# not need a code change.
#
# Still no logo: a mark would be an <img> pointing at a data: URI or a file
# WeasyPrint can resolve, and guessing at an asset is worse than the wordmark.
# Supply the SVG or PNG and it goes in the letterhead.
BRAND_NAME = os.getenv("BRAND_NAME", "Parking Appeals Group")
BRAND_ACCENT_WORD = os.getenv("BRAND_ACCENT_WORD", "Appeals")
BRAND_COLOUR = os.getenv("BRAND_COLOUR", "#d81b52")
BRAND_FOOTER = os.getenv(
    "BRAND_FOOTER",
    "This appeal was prepared for the registered keeper. It is not legal advice.",
)


def _logo_data_uri() -> str | None:
    """BRAND_LOGO=/path/to/logo.png|svg, embedded so the PDF is self-contained.

    No default and no placeholder mark: an invented logo on a legal document is
    worse than none. Drop a file in, set the variable, and it appears beside the
    wordmark; leave it unset and the wordmark stands alone.
    """
    import base64
    path = os.getenv("BRAND_LOGO", "").strip()
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    media = {"svg": "image/svg+xml", "png": "image/png",
             "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(file.suffix.lower().lstrip("."))
    if not media:
        return None
    return f"data:{media};base64,{base64.b64encode(file.read_bytes()).decode()}"


def _wordmark(name: str, accent: str):
    """PARKING <accent>APPEALS</accent> GROUP - the accent word in brand colour.

    Returns Markup because the template escapes by default. Every interpolated
    part is escaped here first, so a brand name from the environment cannot
    inject markup into the letterhead.
    """
    from markupsafe import Markup, escape
    if not accent or accent.lower() not in name.lower():
        return escape(name)
    lowered, needle = name.lower(), accent.lower()
    at = lowered.index(needle)
    before, word, after = name[:at], name[at:at + len(accent)], name[at + len(accent):]
    return Markup("{}<span class='accent'>{}</span>{}").format(before, word, after)

_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 2.4cm 2.2cm 2.2cm 2.2cm;
    @bottom-center {
      content: "Page " counter(page) " of " counter(pages);
      font-size: 8pt;
      color: #888;
    }
  }
  body {
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.5;
    color: #1a1a1a;
  }
  .letterhead {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 2pt solid {{ brand_colour }};
    padding-bottom: 10pt;
    margin-bottom: 22pt;
  }
  .brand {
    font-size: 14pt;
    font-weight: 800;
    color: #14161c;
    letter-spacing: 0.01em;
    text-transform: uppercase;
  }
  .brand .accent { color: {{ brand_colour }}; }
  .logo { height: 26pt; margin-bottom: 5pt; }
  .brand-sub {
    font-size: 8pt;
    color: #777;
    margin-top: 2pt;
  }
  .meta {
    text-align: right;
    font-size: 9pt;
    color: #555;
  }
  .addresses {
    display: flex;
    justify-content: space-between;
    margin-bottom: 20pt;
  }
  .addresses .block { width: 47%; font-size: 9.5pt; }
  .addresses .label {
    font-size: 7.5pt;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #999;
    margin-bottom: 3pt;
  }
  .placeholder { color: #999; font-style: italic; }
  .subject {
    font-weight: 700;
    margin: 18pt 0 14pt 0;
    padding: 8pt 10pt;
    background: #f3f1ec;
    border-left: 3pt solid {{ brand_colour }};
  }
  .body p { margin: 0 0 11pt 0; text-align: justify; }
  .enclosures {
    margin-top: 20pt;
    padding-top: 10pt;
    border-top: 0.75pt solid #ddd;
    font-size: 9pt;
    color: #444;
  }
  .enclosures .label { font-weight: 700; margin-bottom: 4pt; }
  .footer-note {
    margin-top: 26pt;
    font-size: 7.5pt;
    color: #999;
    border-top: 0.75pt solid #eee;
    padding-top: 6pt;
  }
</style>
</head>
<body>
  <div class="letterhead">
    <div>
      {% if brand_logo %}<img class="logo" src="{{ brand_logo }}" alt="">{% endif %}
      <div class="brand">{{ brand_wordmark }}</div>
      <div class="brand-sub">Formal appeal - private parking charge</div>
    </div>
    <div class="meta">
      Case reference: {{ case_id }}<br>
      {{ today }}
    </div>
  </div>

  <div class="addresses">
    <div class="block">
      <div class="label">From</div>
      {% if keeper_name or keeper_address %}
        {% if keeper_name %}<div>{{ keeper_name }}</div>{% endif %}
        {% for line in keeper_address_lines %}<div>{{ line }}</div>{% endfor %}
      {% else %}
        <span class="placeholder">[Add your name and address before sending]</span>
      {% endif %}
    </div>
    <div class="block">
      <div class="label">To</div>
      {{ operator_name or '[Parking operator name and address]' | e }}
    </div>
  </div>

  <div class="subject">
    Re: {{ pcn_label }}{{ pcn_number or '[PCN number]' }}{% if vrm %} &mdash; {{ vrm }}{% endif %}
  </div>

  <div class="body">
    {% for para in paragraphs %}
    <p>{{ para }}</p>
    {% endfor %}
  </div>

  {% if evidence_list %}
  <div class="enclosures">
    <div class="label">Enclosed</div>
    {% for item in evidence_list %}
    &bull; {{ item }}<br>
    {% endfor %}
  </div>
  {% endif %}

  <div class="footer-note">
    {{ brand_footer }}
  </div>
</body>
</html>"""

_env = Environment(autoescape=True)
_compiled = _env.from_string(_TEMPLATE)


def render_letter_pdf(draft: Draft, pack: RetrievalPack, case_id: str,
                      evidence_list: Optional[list[str]] = None,
                      grounds: Optional[list[str]] = None,
                      keeper_name: Optional[str] = None,
                      keeper_address: Optional[str] = None) -> bytes:
    """RELEASED draft + its pack -> a formatted PDF. Raises if WeasyPrint or
    its native libraries (Pango/cairo) are not installed - see requirements.txt.

    The keeper's name and address are arguments rather than pack fields: they are
    deliberately withheld from the drafter (see reasoning.py) so no generated
    sentence can contain them, which means the letterhead must be given them.
    Without them the sender block says so rather than rendering a letter that
    looks finished and cannot be posted.
    """
    from weasyprint import HTML  # imported lazily: not every deployment needs it
    from .orchestrator import uk_dates

    facts: dict[str, Any] = pack.verified_facts or {}
    address_lines = [ln.strip() for ln in (keeper_address or "").replace(",", "\n").splitlines()
                     if ln.strip()]
    html = _compiled.render(
        keeper_name=(keeper_name or "").strip() or None,
        keeper_address=(keeper_address or "").strip() or None,
        keeper_address_lines=address_lines,
        brand_logo=_logo_data_uri(),
        brand_name=BRAND_NAME, brand_colour=BRAND_COLOUR, case_id=case_id,
        brand_wordmark=_wordmark(BRAND_NAME, BRAND_ACCENT_WORD),
        brand_footer=BRAND_FOOTER,
        today=date.today().strftime("%d %B %Y"),
        operator_name=facts.get("operator_name"),
        pcn_number=facts.get("pcn_number"), vrm=facts.get("vrm"),
        pcn_label="PCN " if facts.get("pcn_number") else "",
        # Same text as the API letter; dates in UK form. Grounds are not printed:
        # route labels are internal (client issue 5).
        paragraphs=[uk_dates(" ".join(s.text for s in p)) for p in draft.paragraphs],
        evidence_list=evidence_list or [], grounds=grounds or [])
    return HTML(string=html).write_pdf()
