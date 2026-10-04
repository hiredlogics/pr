"""P8.5: Claim Plan → DraftContext particularisation contract.

A supported ground is not a conclusion. It is a SupportBundle (source facts,
derived facts, evidence, findings, relationships) plus a DraftRequirement
(what the letter must and must not say). DraftContext is built only from a
LOCKED plan and those two objects. Derived facts never replace their sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


class DraftContextError(ValueError):
    """DraftContext cannot be built: the locked plan's contract is incomplete."""


def _thaw(row: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except TypeError:
        return {}


def _plain_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _name(value: Any) -> str:
    if value is None or value is False:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("fact", "name", "id", "condition", "finding_id", "finding_type"):
            nested = _name(value.get(key))
            if nested:
                return nested
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            nested = _name(item)
            if nested:
                return nested
        return ""
    return str(value)


def _id_of(row: dict, name: str) -> str:
    return str(row.get("fact_id") or row.get("finding_id") or name or "")


@dataclass(frozen=True)
class SupportBundle:
    source_fact_ids: tuple = ()
    derived_fact_ids: tuple = ()
    evidence_ids: tuple = ()
    legal_finding_ids: tuple = ()
    relationship_ids: tuple = ()
    source_fact_names: tuple = ()
    derived_fact_names: tuple = ()
    values: dict = field(default_factory=dict)

    def complete(self) -> bool:
        """Source facts (or a legal finding / evidence) must be present.
        A derived conclusion alone is not a bundle."""
        if self.derived_fact_ids and not (
                self.source_fact_ids or self.legal_finding_ids):
            return False
        return bool(self.source_fact_ids or self.legal_finding_ids or self.evidence_ids)

    def as_dict(self) -> dict:
        return {
            "source_fact_ids": list(self.source_fact_ids),
            "derived_fact_ids": list(self.derived_fact_ids),
            "evidence_ids": list(self.evidence_ids),
            "legal_finding_ids": list(self.legal_finding_ids),
            "relationship_ids": list(self.relationship_ids),
            "source_fact_names": list(self.source_fact_names),
            "derived_fact_names": list(self.derived_fact_names),
            "values": {k: _plain_value(v) for k, v in self.values.items()},
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SupportBundle":
        d = d or {}
        return cls(
            tuple(d.get("source_fact_ids") or ()),
            tuple(d.get("derived_fact_ids") or ()),
            tuple(d.get("evidence_ids") or ()),
            tuple(d.get("legal_finding_ids") or ()),
            tuple(d.get("relationship_ids") or ()),
            tuple(d.get("source_fact_names") or ()),
            tuple(d.get("derived_fact_names") or ()),
            dict(d.get("values") or {}),
        )


@dataclass(frozen=True)
class DraftRequirement:
    required_particulars: tuple = ()
    prohibited_content: tuple = ()
    explanation_goal: tuple = ()

    def as_dict(self) -> dict:
        return {
            "required_particulars": list(self.required_particulars),
            "prohibited_content": list(self.prohibited_content),
            "explanation_goal": list(self.explanation_goal),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "DraftRequirement":
        d = d or {}
        return cls(
            tuple(d.get("required_particulars") or d.get("must_express") or ()),
            tuple(d.get("prohibited_content") or d.get("must_not_express") or ()),
            tuple(d.get("explanation_goal") or ()),
        )


def enrich_support_rows(rows: list[dict], case=None, module=None,
                        facts: Optional[dict] = None) -> list[dict]:
    """Attach source lineage to derived support rows. Derived facts cannot
    stand in for the facts they were computed from."""
    out = [dict(r) for r in rows]
    lineage = _lineage_map(case)
    seen = {_name(r.get("fact") or r.get("condition")) for r in out}
    if module is not None:
        for name in getattr(module, "required_facts", None) or []:
            if name in seen:
                continue
            value = None
            if case is not None and case.has(name):
                value = case.get(name)
            elif facts is not None:
                value = facts.get(name)
            if value in (None, "", [], False):
                continue
            row = {"condition": name, "fact": name, "value": value}
            if case is not None and case.facts.get(name) is not None:
                row["fact_id"] = case.facts.node_id(name)
            out.append(row)
            seen.add(name)
    for row in out:
        name = _name(row.get("fact") or row.get("condition"))
        sources = list(lineage.get(name) or [])
        existing = []
        for dep in row.get("because_of") or []:
            dname = _name(dep)
            if dname:
                existing.append(dname)
        extra = []
        for src in sources:
            if src in existing:
                continue
            extra.append(_source_row(case, src, facts))
            existing.append(src)
        if extra:
            row["because_of"] = list(row.get("because_of") or []) + extra
    return out


def _lineage_map(case) -> dict[str, list[str]]:
    if case is None:
        return {}
    try:
        from ..case_state import master
        derived = master(case).derived_facts
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for name, row in (derived or {}).items():
        srcs = [s for s in (row.get("source_facts") or []) if s]
        if srcs:
            out[name] = srcs
    # Multiple-visits is often set by answer / semantic promotion without a
    # derives() frame. Narrative atoms that hold on the case are still the
    # material source particulars the letter must express (P11.2).
    if case.get("multiple_visits") is True:
        atoms = []
        for src in ("left_site", "returned_same_day", "visited_premises",
                    "purpose_of_visit", "departure_reason"):
            if case.has(src) and case.get(src) not in (None, "", [], False):
                atoms.append(src)
        if atoms:
            existing = list(out.get("multiple_visits") or [])
            for src in atoms:
                if src not in existing:
                    existing.append(src)
            out["multiple_visits"] = existing
    return out


def _source_row(case, name: str, facts: Optional[dict]) -> dict:
    row = {"fact": name}
    if case is not None and case.facts.get(name) is not None:
        f = case.facts[name]
        row["value"] = f.value
        row["fact_id"] = case.facts.node_id(name)
        row["source"] = f.source.ref
    elif facts and name in facts:
        row["value"] = facts[name]
    return row


def build_bundle(support_rows: Iterable, evidence_refs: Iterable = (),
                 relationships: Iterable = (), finding_rows: Iterable = (),
                 case=None) -> SupportBundle:
    source_ids, source_names = [], []
    derived_ids, derived_names = [], []
    values: dict[str, Any] = {}
    finding_ids = []

    rows = [_thaw(r) for r in (support_rows or ())]
    derived_declared = set()
    sources_declared = set()
    for row in rows:
        if row.get("finding_id") or row.get("finding_type"):
            fid = row.get("finding_id") or row.get("finding_type")
            if fid:
                finding_ids.append(str(fid))
            continue
        name = _name(row.get("fact") or row.get("condition"))
        if not name:
            continue
        if "value" in row:
            values[name] = row.get("value")
        deps = [_name(d) for d in (row.get("because_of") or []) if _name(d)]
        if deps:
            derived_declared.add(name)
            for dep in row.get("because_of") or []:
                d = _thaw(dep)
                dname = _name(d)
                if not dname:
                    continue
                sources_declared.add(dname)
                if "value" in d:
                    values[dname] = d.get("value")
                sid = _id_of(d, dname)
                if sid and sid not in source_ids:
                    source_ids.append(sid)
                if dname not in source_names:
                    source_names.append(dname)
        else:
            sources_declared.add(name)

    for row in rows:
        if row.get("finding_id") or row.get("finding_type"):
            continue
        name = _name(row.get("fact") or row.get("condition"))
        if not name:
            continue
        nid = _id_of(row, name)
        if name in derived_declared:
            if nid and nid not in derived_ids:
                derived_ids.append(nid)
            if name not in derived_names:
                derived_names.append(name)
        else:
            if nid and nid not in source_ids:
                source_ids.append(nid)
            if name not in source_names:
                source_names.append(name)

    if case is not None:
        lineage = _lineage_map(case)
        for name in list(derived_names) + list(source_names):
            for src in lineage.get(name) or []:
                if src in source_names:
                    continue
                source_names.append(src)
                extra = _source_row(case, src, None)
                sid = extra.get("fact_id") or src
                if sid not in source_ids:
                    source_ids.append(sid)
                if "value" in extra:
                    values[src] = extra["value"]
                if name not in derived_names:
                    derived_names.append(name)
                    derived_ids.append(case.facts.node_id(name) if case.facts.get(name) else name)

    evidence_ids = []
    for e in evidence_refs or ():
        ev = _thaw(e)
        eid = ev.get("evidence_id") or ev.get("kind")
        if eid:
            evidence_ids.append(str(eid))

    rel_ids = []
    for rel in relationships or ():
        r = _thaw(rel)
        rid = r.get("edge_id") or r.get("id") or r.get("relationship")
        if rid:
            rel_ids.append(str(rid))

    for row in finding_rows or ():
        r = _thaw(row)
        fid = r.get("finding_id") or r.get("finding_type")
        if fid and str(fid) not in finding_ids:
            finding_ids.append(str(fid))

    return SupportBundle(
        tuple(source_ids), tuple(derived_ids), tuple(evidence_ids),
        tuple(finding_ids), tuple(rel_ids),
        tuple(source_names), tuple(derived_names), values,
    )


def build_requirement(bundle: SupportBundle, *, prohibited: Iterable[str] = (),
                      findings: Iterable[dict] = (), claim_type: str = "") -> DraftRequirement:
    particulars: list[str] = []
    for name in bundle.source_fact_names:
        if name not in particulars:
            particulars.append(name)
    for name in bundle.derived_fact_names:
        if name not in particulars:
            particulars.append(name)
    goals: list[str] = []
    if bundle.source_fact_names and bundle.derived_fact_names:
        goals.append("state the source sequence that produces the derived conclusion")
    elif bundle.source_fact_names:
        goals.append("state the supporting facts")
    if bundle.legal_finding_ids or findings:
        goals.append("state each calculated date and the day count")
        from ..legal import findings as lf
        for rec in findings or ():
            p = lf.particulars(rec)
            for key in (p.get("dates") or {}):
                if key not in particulars:
                    particulars.append(key)
            if p.get("days") is not None and "days_late" not in particulars:
                particulars.append("days_late")
    if not goals:
        goals.append("particularise the ground from the support bundle")
    prohibited_list = [p for p in prohibited if p]
    if "driver_identity_unless_formally_identified" not in prohibited_list:
        prohibited_list.append("driver_identity_unless_formally_identified")
    return DraftRequirement(tuple(particulars), tuple(prohibited_list), tuple(goals))


def bundle_for_item(item, case=None) -> SupportBundle:
    stored = getattr(item, "support_bundle", None)
    if stored:
        return SupportBundle.from_dict(_thaw(stored))
    return build_bundle(
        getattr(item, "supporting_facts", None) or (),
        getattr(item, "evidence_refs", None) or (),
        getattr(item, "relationships", None) or (),
        case=case,
    )


def requirement_for_item(item, *, findings: Iterable[dict] = (),
                         prohibited: Iterable[str] = ()) -> DraftRequirement:
    stored = getattr(item, "draft_requirement", None)
    if stored:
        return DraftRequirement.from_dict(_thaw(stored))
    return build_requirement(
        bundle_for_item(item),
        prohibited=prohibited,
        findings=findings,
        claim_type=getattr(item, "claim_type", "") or "",
    )


def contract_views(plan, case=None) -> tuple[dict, dict]:
    """module_id -> SupportBundle / DraftRequirement dicts for a plan."""
    bundles, reqs = {}, {}
    findings = list(getattr(getattr(plan, "_case", None), "legal_findings", None) or [])
    if case is not None:
        findings = list(case.legal_findings or [])
    by_module: dict[str, list] = {}
    for rec in findings:
        mid = rec.get("legal_module_id")
        if mid:
            by_module.setdefault(mid, []).append(rec)
    for item in getattr(plan, "supported", None) or ():
        bundle = bundle_for_item(item, case=case)
        recs = by_module.get(item.module_id) or []
        if recs and not bundle.legal_finding_ids:
            bundle = build_bundle(
                item.supporting_facts, item.evidence_refs, item.relationships,
                finding_rows=recs, case=case)
        req = requirement_for_item(item, findings=recs)
        if recs and "days_late" not in req.required_particulars:
            req = build_requirement(bundle, findings=recs,
                                    prohibited=req.prohibited_content,
                                    claim_type=item.claim_type)
        bundles[item.module_id] = bundle.as_dict()
        reqs[item.module_id] = req.as_dict()
    return bundles, reqs


__all__ = [
    "SupportBundle", "DraftRequirement", "DraftContextError",
    "enrich_support_rows", "build_bundle", "build_requirement",
    "bundle_for_item", "requirement_for_item", "contract_views",
]
