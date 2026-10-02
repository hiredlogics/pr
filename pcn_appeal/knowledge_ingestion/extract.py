"""Parsed document -> structured knowledge records.

Two sources, joined by module id and never by hand:

  the controlled DOCX   what the module is: name, category, the prose USE WHEN,
                        core proposition, what the AI must check, evidence,
                        legal basis and what it must not claim.
  kb_modules.yaml       the machine-evaluable form live reasoning runs today:
                        use_when / do_not_use_when predicates, required facts,
                        prohibited claims, building-block ids. Attached as
                        COMPILED rules so the store holds both and the drift
                        report can say where they differ.

Prose is mapped onto system vocabulary generically:

  required facts   an AI MUST CHECK item names a fact when every word of the
                   fact's name (or of an alias) appears in it ("Payment method"
                   -> payment_method, "event date" -> parking_event_date); a
                   one-word name only when it is the whole item ("VRM").
                   Inferred, so confidence < 1 and source DOCX_CHECK.
  evidence         an EVIDENCE item maps to a system evidence kind when the
                   kind's leading word appears in it ("bank transaction" ->
                   BANK_STATEMENT). Unmapped items are kept, kind null.

No module id appears in this file.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from ..rules.dsl import referenced_facts
from .parser import ParsedDocument, ParsedModule

# Field names as the document writes them (normalised upper case).
F_USE_WHEN, F_CORE, F_CHECK = "USE WHEN", "CORE PROPOSITION", "AI MUST CHECK"
F_EVIDENCE, F_DO_NOT, F_BASIS = "EVIDENCE", "DO NOT", "LEGAL / CODE BASIS"
KNOWN_FIELDS = {F_USE_WHEN, F_CORE, F_CHECK, F_EVIDENCE, F_DO_NOT, F_BASIS}

RULE_TYPES = ("USE_WHEN", "DO_NOT_USE_WHEN", "CORE_PROPOSITION", "AI_MUST_CHECK",
              "LEGAL_BASIS", "DRAFTING_GUIDANCE", "DOCUMENT_FIELD")
REQUIREMENT_TYPES = ("GATE", "REQUIRED", "CHECK")
RESTRICTION_TYPES = ("PROHIBITED_CLAIM", "DRAFTING_RULE")

CHECK_CONFIDENCE = 0.6          # an inferred prose -> fact mapping
EVIDENCE_CONFIDENCE = 0.7       # an inferred prose -> evidence kind mapping

_SPLIT = re.compile(r"\s*[;,]\s*|\s+and\s+(?=[a-z])")
_WORD = re.compile(r"[a-z0-9]+")


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _sing(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def words(text: str) -> set[str]:
    return {_sing(w) for w in _WORD.findall(str(text).lower())}


def items(text: str) -> list[str]:
    """A prose list split into its items ("a; b, c and d." -> [a, b, c, d])."""
    out = []
    for part in _SPLIT.split((text or "").strip().rstrip(".")):
        part = part.strip(" .")
        if part:
            out.append(part)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _snake(text: str) -> str:
    return "_".join(_sing(w) for w in _WORD.findall(text.lower()))


# ------------------------------------------------------------------ vocabulary
@dataclass
class Vocabulary:
    """The system's own names, read from the live KB and the fact graph."""
    facts: dict[str, str]                 # spelling (fact name or alias) -> canonical
    evidence_kinds: set[str]

    @classmethod
    def from_kg(cls, kg, curated: Optional[dict] = None) -> "Vocabulary":
        from ..fact_graph import ALIASES, EVIDENCE_KINDS, LABELS
        names: set[str] = set(LABELS)
        kinds: set[str] = set(EVIDENCE_KINDS)
        for m in kg.modules.values():
            names |= referenced_facts(m.use_when) | referenced_facts(m.do_not_use_when)
            names |= set(m.required_facts or [])
            kinds |= {str(k) for k in (m.evidence_helpful or [])}
            kinds |= _evidence_in(m.use_when) | _evidence_in(m.do_not_use_when)
        for k, v in ((curated or {}).get("depends_on") or {}).items():
            names |= {k, *v}
        facts = {n: n for n in names if n}
        facts.update({a: c for a, c in ALIASES.items()})
        return cls(facts, kinds)

    def facts_in(self, text: str) -> list[str]:
        """Canonical facts an item names. Longest spelling wins, so "notice
        issue date" yields notice_issue_date and not also a shorter name."""
        have = words(text)
        # A one-word name ("vrm", "location") names the fact only when it is
        # the whole item: "operator photos" is not operator_name.
        hits = [(len(s.split("_")), s, c) for s, c in self.facts.items()
                if (w := {_sing(x) for x in s.split("_")})
                and (w == have if len(w) == 1 else w <= have)]
        hits.sort(key=lambda h: (-h[0], h[1]))
        out, covered = [], set()
        for _, spelling, canonical in hits:
            w = {_sing(x) for x in spelling.split("_")}
            if canonical in out or w <= covered:
                continue
            out.append(canonical)
            covered |= w
        return out

    def evidence_kind(self, label: str) -> Optional[str]:
        have = words(label)
        for kind in sorted(self.evidence_kinds):
            lead = _sing(kind.split("_")[0].lower())
            if lead in have:
                return kind
        return None


def _evidence_in(pred: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(pred, dict):
        for k, v in pred.items():
            if k == "has_evidence":
                out.add(str(v))
            else:
                out |= _evidence_in(v)
    elif isinstance(pred, list):
        for x in pred:
            out |= _evidence_in(x)
    return out


# ------------------------------------------------------------------ records
@dataclass
class ModuleRecord:
    module_id: str
    name: str
    category: str
    version: str
    status: str
    effective_from: Optional[str]
    effective_to: Optional[str]
    source_document: str
    source_reference: str
    metadata: dict = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)            # {rule_type, rule_definition}
    required_facts: list[dict] = field(default_factory=list)   # {fact_name, requirement_type, metadata}
    evidence: list[dict] = field(default_factory=list)         # {evidence_type, requirement}
    restrictions: list[dict] = field(default_factory=list)     # {restriction_type, content, metadata}

    @property
    def content_hash(self) -> str:
        d = asdict(self)
        d.pop("status")                 # governance state, not content
        return _sha(d)

    def as_dict(self) -> dict:
        return {**asdict(self), "source_hash": self.content_hash}


def _iso(d) -> Optional[str]:
    return d.isoformat() if d else None


def _record(pm: Optional[ParsedModule], ym, doc: Optional[ParsedDocument],
            vocab: Vocabulary) -> ModuleRecord:
    mid = pm.module_id if pm else ym.module_id
    in_doc = pm is not None
    f = pm.fields if pm else {}
    rec = ModuleRecord(
        module_id=mid,
        name=pm.name if pm else ym.topic,
        category=mid.split("-")[1],
        version=(doc.version if in_doc else f"YAML-{ym.version}"),
        status=(ym.status if ym is not None else "ACTIVE"),
        effective_from=_iso(getattr(ym, "effective_from", None)),
        effective_to=_iso(getattr(ym, "effective_to", None)),
        source_document=(doc.source_document if in_doc else "kb_modules.yaml"),
        source_reference=(f"{doc.source_document} / {pm.section} / {mid}" if in_doc
                          else (getattr(ym, "source_reference", "") or "kb_modules.yaml")),
    )
    rec.metadata = {
        "section": pm.section if pm else None,
        "in_controlled_document": in_doc,
        "compiled": ym is not None,
        "core_proposition": f.get(F_CORE) or (ym.core_proposition if ym else ""),
        "route": getattr(ym, "route", None),
        "strength": getattr(ym, "strength", None),
        "yaml_version": getattr(ym, "version", None),
        "building_block_ids": list(getattr(ym, "building_blocks", None) or []),
    }

    # ---- rules
    rec.rules.append({"rule_type": "USE_WHEN", "rule_definition": {
        "text": f.get(F_USE_WHEN),
        "predicate": ym.use_when if ym else None,
        "compiled_from": "kb_modules.yaml" if ym else None}})
    if ym is not None:
        rec.rules.append({"rule_type": "DO_NOT_USE_WHEN", "rule_definition": {
            "predicate": ym.do_not_use_when, "compiled_from": "kb_modules.yaml"}})
    rec.rules.append({"rule_type": "CORE_PROPOSITION", "rule_definition": {
        "text": rec.metadata["core_proposition"],
        "source": "DOCX" if f.get(F_CORE) else "YAML"}})
    if f.get(F_CHECK):
        rec.rules.append({"rule_type": "AI_MUST_CHECK", "rule_definition": {
            "text": f[F_CHECK], "items": items(f[F_CHECK])}})
    if f.get(F_BASIS) or (ym is not None and ym.legal_basis):
        rec.rules.append({"rule_type": "LEGAL_BASIS", "rule_definition": {
            "text": f.get(F_BASIS), "sources": list(getattr(ym, "legal_basis", None) or [])}})
    if ym is not None and ym.drafting_notes:
        rec.rules.append({"rule_type": "DRAFTING_GUIDANCE", "rule_definition": {
            "text": ym.drafting_notes, "source": "YAML"}})
    for name, value in sorted(f.items()):
        if name not in KNOWN_FIELDS:
            rec.rules.append({"rule_type": "DOCUMENT_FIELD",
                              "rule_definition": {"field": name, "text": value}})

    # ---- required facts: compiled gate + compiled required + inferred checks
    seen: set[tuple[str, str]] = set()

    def need(name: str, kind: str, **meta):
        if (name, kind) in seen:
            return
        seen.add((name, kind))
        rec.required_facts.append({"fact_name": name, "requirement_type": kind, "metadata": meta})

    if ym is not None:
        for n in sorted(referenced_facts(ym.use_when)):
            need(n, "GATE", source="YAML_COMPILED", confidence=1.0)
        for n in ym.required_facts or []:
            need(n, "REQUIRED", source="YAML_COMPILED", confidence=1.0)
    for item in items(f.get(F_CHECK, "")):
        for n in vocab.facts_in(item):
            need(n, "CHECK", source="DOCX_CHECK", confidence=CHECK_CONFIDENCE, source_text=item)

    # ---- evidence
    ev_seen: set[str] = set()
    kinds_seen: set[str] = set()
    for label in items(re.sub(r"/", ", ", f.get(F_EVIDENCE, ""))):
        et = _snake(label)
        if et and et not in ev_seen:
            ev_seen.add(et)
            kind = vocab.evidence_kind(label)
            kinds_seen.add(kind)
            rec.evidence.append({"evidence_type": et, "requirement": {
                "label": label, "evidence_kind": kind, "source": "DOCX",
                "confidence": EVIDENCE_CONFIDENCE if kind else None}})
    for kind in (getattr(ym, "evidence_helpful", None) or []):
        et = str(kind).lower()
        if et not in ev_seen and kind not in kinds_seen:
            ev_seen.add(et)
            rec.evidence.append({"evidence_type": et, "requirement": {
                "label": kind, "evidence_kind": kind, "source": "YAML_COMPILED",
                "confidence": 1.0}})

    # ---- restrictions
    for s in sentences(f.get(F_DO_NOT, "")):
        rtype = "PROHIBITED_CLAIM" if re.match(r"^(do not|never|don't)\b", s, re.I) else "DRAFTING_RULE"
        rec.restrictions.append({"restriction_type": rtype, "content": s,
                                 "metadata": {"source": "DOCX"}})
    for c in (getattr(ym, "prohibited_claims", None) or []):
        rec.restrictions.append({"restriction_type": "PROHIBITED_CLAIM", "content": c,
                                 "metadata": {"source": "YAML_COMPILED"}})
    return rec


def extract(doc: ParsedDocument, kg, vocab: Optional[Vocabulary] = None) -> list[ModuleRecord]:
    """One record per module in the document, plus one per live YAML module
    the document does not contain (flagged: not in the controlled document)."""
    from ..kg.relations import load_curated
    vocab = vocab or Vocabulary.from_kg(kg, load_curated())
    out = [_record(pm, kg.modules.get(pm.module_id), doc, vocab) for pm in doc.modules]
    in_doc = {pm.module_id for pm in doc.modules}
    out += [_record(None, kg.modules[mid], doc, vocab) for mid in sorted(kg.modules)
            if mid not in in_doc]
    return out


__all__ = ["extract", "ModuleRecord", "Vocabulary", "items", "words", "RULE_TYPES",
           "REQUIREMENT_TYPES", "RESTRICTION_TYPES"]
