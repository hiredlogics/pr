"""CLI - see the package docstring."""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path


def _dry_run(path: Path) -> int:
    from ..kg.graph import KnowledgeGraph
    from . import drift
    from .extract import extract
    from .graph import build_graph
    from .parser import parse
    kg = KnowledgeGraph()
    doc = parse(path)
    records = extract(doc, kg)
    nodes, edges = build_graph(records, kg.relations, doc)
    d = drift.report(doc, records, kg)
    print(f"document      {doc.source_document} ({doc.version}, sha256 {doc.source_hash[:12]})")
    print(f"modules       {len(doc.modules)} in document, {len(records)} stored "
          f"({len(records) - len(doc.modules)} live-only)")
    print(f"rules         {sum(len(r.rules) for r in records)}")
    print(f"req. facts    {sum(len(r.required_facts) for r in records)}")
    print(f"evidence      {sum(len(r.evidence) for r in records)}")
    print(f"restrictions  {sum(len(r.restrictions) for r in records)}")
    print(f"nodes         {len(nodes)} {dict(collections.Counter(n.node_type for n in nodes))}")
    print(f"edges         {len(edges)} {dict(collections.Counter(e.relationship_type for e in edges))}")
    print(f"              {dict(collections.Counter(e.origin for e in edges))}")
    print(f"drift         {drift.summary(d)}")
    for x in d:
        if x["severity"] in ("HIGH", "MEDIUM"):
            print(f"  {x['severity']:<6} {x['module_id']:<12} {x['kind']}")
    return 0


def main(argv: list[str]) -> int:
    from .store import DEFAULT_DOCUMENT
    p = argparse.ArgumentParser(prog="python -m pcn_appeal.knowledge_ingestion")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("parse"); a.add_argument("docx", nargs="?", default=str(DEFAULT_DOCUMENT))
    b = sub.add_parser("ingest"); b.add_argument("docx", nargs="?", default=str(DEFAULT_DOCUMENT))
    b.add_argument("--by", required=True); b.add_argument("--reason", required=True)
    sub.add_parser("releases")
    c = sub.add_parser("compare"); c.add_argument("a"); c.add_argument("b")
    args = p.parse_args(argv)
    if args.cmd == "parse":
        return _dry_run(Path(args.docx))
    from ..store import db
    if not db.enabled():
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    from . import store
    if args.cmd == "ingest":
        print(json.dumps(store.ingest(Path(args.docx), created_by=args.by, reason=args.reason),
                         indent=2))
    elif args.cmd == "releases":
        print(json.dumps(store.list_releases(), indent=2, default=str))
    else:
        print(json.dumps(store.compare_releases(args.a, args.b), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
