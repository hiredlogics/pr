"""Read the controlled knowledge document into plain records.

Structure is read, not assumed per module:

  Heading 1                      a section ("5. Payment, Keying and Systems")
  Heading 2 "KB-XXX-NN - Name"   a knowledge module, followed by one two-column
                                 table of FIELD | value rows (USE WHEN, CORE
                                 PROPOSITION, AI MUST CHECK, ...). Every field
                                 the table carries is kept, known or not.
  Heading 2 "PP-... - Name"      a drafting building block (Appendix A). Only
                                 its id and name are recorded: the paragraph is
                                 drafting text, not knowledge, and is never stored.
  two-column table under a       a document-wide rule set (governance, validators,
  section with no module         drafting priority, selection matrix, schema ...)
  List Bullet                    a checklist item of its section

Nothing here knows any module id. A new module added to the document with the
same layout is read like every other.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PARSER_VERSION = "1"

MODULE_HEADING = re.compile(r"^(KB-[A-Z]+-\d+[A-Z]?)\s*[-–—]\s*(.+)$")
BLOCK_HEADING = re.compile(r"^([A-Z]{2,}-[A-Z0-9]+-\d+[A-Z]?)\s*[-–—]\s*(.+)$")
DOC_VERSION = re.compile(r"\bVERSION\s+(\d+(?:\.\d+)*)\b", re.I)


@dataclass
class ParsedModule:
    module_id: str
    name: str
    section: str
    fields: dict[str, str] = field(default_factory=dict)
    order: int = 0

    @property
    def family(self) -> str:
        return self.module_id.split("-")[1]


@dataclass
class ParsedRuleSet:
    """A document-wide table: one rule per row, keyed by its first column."""
    section: str
    header: list[str]
    rows: list[list[str]]


@dataclass
class ParsedDocument:
    source_document: str
    source_hash: str
    title: str
    version: str
    preamble: list[str]
    modules: list[ParsedModule]
    rule_sets: list[ParsedRuleSet]
    checklists: dict[str, list[str]]
    block_ids: dict[str, str]           # drafting block id -> name (no text)
    sections: list[str]

    def module(self, module_id: str) -> Optional[ParsedModule]:
        return next((m for m in self.modules if m.module_id == module_id), None)


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _norm_field(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().upper())


def parse(path: Path) -> ParsedDocument:
    import docx                                     # python-docx; imported lazily
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    path = Path(path)
    d = docx.Document(str(path))
    preamble: list[str] = []
    modules: list[ParsedModule] = []
    rule_sets: list[ParsedRuleSet] = []
    checklists: dict[str, list[str]] = {}
    blocks: dict[str, str] = {}
    sections: list[str] = []
    section: Optional[str] = None
    current: Optional[ParsedModule] = None

    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text, style = p.text.strip(), (p.style.name if p.style is not None else "")
            if not text:
                continue
            if style == "Heading 1":
                section, current = text, None
                sections.append(text)
                continue
            if section is None:
                preamble.append(text)
                continue
            if style == "Heading 2":
                current = None
                m = MODULE_HEADING.match(text)
                if m:
                    current = ParsedModule(m.group(1), m.group(2).strip(), section,
                                           order=len(modules) + 1)
                    modules.append(current)
                    continue
                b = BLOCK_HEADING.match(text)
                if b:
                    blocks[b.group(1)] = b.group(2).strip()
                continue
            if style.startswith("List"):
                checklists.setdefault(section, []).append(text)
            continue
        if tag == "tbl" and section is not None:
            rows = [[c.text.strip() for c in r.cells] for r in Table(el, d).rows]
            rows = [r for r in rows if any(r)]
            if not rows:
                continue
            if current is not None and not current.fields:
                for r in rows:
                    key = _norm_field(r[0])
                    if key in ("FIELD",):            # the table's own header row
                        continue
                    current.fields[key] = r[1] if len(r) > 1 else ""
                continue
            rule_sets.append(ParsedRuleSet(section, rows[0], rows[1:]))

    title = preamble[0] if preamble else path.stem
    vm = next((DOC_VERSION.search(t) for t in preamble if DOC_VERSION.search(t)), None)
    version = f"V{vm.group(1)}" if vm else "V0"
    return ParsedDocument(path.name, file_hash(path), title, version, preamble, modules,
                          rule_sets, checklists, blocks, sections)


__all__ = ["parse", "ParsedDocument", "ParsedModule", "ParsedRuleSet", "PARSER_VERSION",
           "file_hash"]
