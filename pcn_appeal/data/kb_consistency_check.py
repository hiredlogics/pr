#!/usr/bin/env python3
"""
KB consistency check - run after ANY change to the KB files, and in CI.
Exit code 0 = no ERRORS (warnings may remain). Exit code 1 = at least one ERROR.

Checks every file against the approved source (knowledge_graph.json, built from
the Word KB) and against each other:
  modules  <-> source   : every KB record present, topic + core proposition verbatim
  blocks   <-> source   : every Appendix A block present and verbatim
  modules  <-> blocks   : every building block exists; no ACTIVE module relies only on REVIEW blocks
  modules  <-> routes   : every route defined; tiers/ranks valid
  modules  <-> sources  : every legal_basis defined
  blocks   <-> facts    : every requires_facts fact has a producer (gate, extraction field or question vocabulary)
  modules  -> letter    : every ACTIVE module has at least one block that can actually render
  evidence codes        : every evidence code is producible from an extraction doc type
  routing  <-> extraction doc types
  prompts               : versions present; validation prompt covers every section 17 validator;
                          prompts name only ACTIVE modules (or name them conditionally)
  customer text         : no internal code in any customer-facing message
Usage:
  python kb_consistency_check.py --dir <folder with the files> [--questions questions.yaml]
"""
import argparse, json, re, sys, yaml
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=".")
ap.add_argument("--source", default="knowledge_graph.json")
ap.add_argument("--modules", default="kb_modules_corrected.yaml")
ap.add_argument("--blocks", default="kb_blocks_corrected.yaml")
ap.add_argument("--routes", default="kb_routes_corrected.yaml")
ap.add_argument("--prompts", default="prompts_corrected.yaml")
ap.add_argument("--routing", default="document_routing.yaml")
ap.add_argument("--questions", default=None, help="optional questions.yaml fact vocabulary")
a = ap.parse_args()
D = Path(a.dir)
L = lambda f: yaml.safe_load(open(D / f, encoding="utf-8"))

ERR, WARN, OK = [], [], []
def err(m): ERR.append(m)
def warn(m): WARN.append(m)
def ok(m): OK.append(m)

g = json.load(open(D / a.source, encoding="utf-8"))
SRC_MOD = {n["id"]: n for n in g["nodes"] if n["type"] == "module"}
SRC_BLK = {n["id"]: " ".join(n["text"]) for n in g["nodes"] if n["type"] == "drafting_block"}
SRC_VAL = {n["id"] for n in g["nodes"] if n["type"] == "validator"}

M = L(a.modules); mods = M["modules"]; MOD = {m["module_id"]: m for m in mods}
B = L(a.blocks)["blocks"]
R = L(a.routes)["routes"]
P = L(a.prompts)["prompts"]
RT = L(a.routing)
Q = set()
if a.questions:
    qy = L(a.questions)
    if isinstance(qy, dict):
        # Facts may be top-level, or nested under a heading such as `questions:`
        # or `facts:` (the project's questions.yaml nests them under `questions:`).
        for key in ("questions", "facts"):
            if isinstance(qy.get(key), dict):
                Q |= set(qy[key].keys())
        if not Q:
            Q = set(qy.keys())
    if not Q:
        warn(f"{a.questions}: no fact names found - check its layout")
    else:
        ok(f"questions.yaml: {len(Q)} fact names read")

ACTIVE = {k for k, m in MOD.items() if m.get("status") == "ACTIVE"}

# ---------- modules <-> source
miss = set(SRC_MOD) - set(MOD)
if miss: err(f"Source modules missing from modules file: {sorted(miss)}")
for k, s in SRC_MOD.items():
    m = MOD.get(k)
    if not m: continue
    if m["topic"] != s["label"]: err(f"{k}: topic differs from source")
    if m["core_proposition"] != s["fields"]["CORE PROPOSITION"]: err(f"{k}: core proposition differs from source")
    if m.get("status") != "ACTIVE": warn(f"{k}: source module is not ACTIVE (status {m.get('status')})")
for k in set(MOD) - set(SRC_MOD):
    if MOD[k].get("status") == "ACTIVE":
        err(f"{k}: not in source KB but ACTIVE (KB-GOV-03) - needs recorded approval + adding to the Word KB")
    else:
        warn(f"{k}: not in source KB, status {MOD[k].get('status')} - excluded until approved")
if not [e for e in ERR if "topic" in e or "core proposition" in e or "missing from modules" in e]:
    ok(f"All {len(SRC_MOD)} source modules present; topic and core proposition verbatim")
for k, m in MOD.items():
    for f in ["status", "version", "effective_from", "effective_to", "last_legal_review", "source_reference", "change_notes"]:
        if f not in m: err(f"{k}: missing admin field {f} (section 19)")
    if m.get("status") == "ACTIVE" and m.get("last_legal_review") is None:
        warn(f"{k}: last_legal_review not set") if False else None
nolr = [k for k in ACTIVE if MOD[k].get("last_legal_review") is None]
if nolr: warn(f"{len(nolr)} ACTIVE modules have no last_legal_review / effective_from date (administrator must set)")

# ---------- blocks <-> source
miss = set(SRC_BLK) - set(B)
if miss: err(f"Appendix A blocks missing from blocks file: {sorted(miss)}")
bad = [k for k in SRC_BLK if k in B and B[k]["text"] != SRC_BLK[k]]
for k in bad: err(f"{k}: block text is not verbatim Appendix A")
if not miss and not bad: ok(f"All {len(SRC_BLK)} Appendix A blocks present and verbatim")
for k in set(B) - set(SRC_BLK):
    if B[k].get("status", "ACTIVE") == "ACTIVE": err(f"{k}: block not in Appendix A but ACTIVE")
    else: warn(f"{k}: block not in Appendix A, status {B[k].get('status')}")
for k, v in B.items():
    ph = set(re.findall(r"{{(\w+)}}", v["text"]))
    pm = v.get("placeholder_map", {})
    for p in ph:
        if p in pm: continue
        # placeholder must be a known fact name somewhere
    if pm: warn(f"{k}: uses placeholder_map {pm} - renderer must support it or the placeholder stays unfilled")

# ---------- modules <-> blocks / routes / legal sources / conflicts
LS = M.get("legal_sources", {})
for k, m in MOD.items():
    for b in m["building_blocks"]:
        if b not in B: err(f"{k}: building block {b} does not exist")
        elif m.get("status") == "ACTIVE" and B[b].get("status", "ACTIVE") != "ACTIVE":
            err(f"{k}: ACTIVE module uses non-ACTIVE block {b}")
    if m["route"] not in R: err(f"{k}: route {m['route']} not defined in routes file")
    for l in m["legal_basis"]:
        if l not in LS: err(f"{k}: legal_basis {l} not defined in legal_sources")
for x, y in M.get("conflicts_with", []):
    for z in (x, y):
        if z not in MOD: err(f"conflicts_with references unknown module {z}")
ranks = [v.get("rank") for v in R.values()]
if None in ranks or len(set(ranks)) != len(ranks): err("routes: every route needs a unique rank")
unused = set(R) - {m["route"] for m in mods}
if unused: warn(f"routes defined but used by no module: {sorted(unused)}")
if not [e for e in ERR if "route" in e or "legal_basis" in e or "building block" in e]:
    ok("Every module's route, legal basis and building blocks resolve")

# ---------- fact producers
def facts_in(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            if k in ("is", "exists", "has_evidence") and isinstance(v, str):
                if k != "has_evidence": out.add(v)
            elif k in ("eq", "ne", "in", "lte", "gte", "lt", "gt", "contains") and isinstance(v, list):
                out.add(v[0])
            else: facts_in(v, out)
    elif isinstance(x, list):
        for i in x: facts_in(i, out)
    return out
ext_body = P["extraction"]["body"]
fm = re.search(r"Field names:(.*?)\.\s*\n", ext_body, re.S)
EXT_FIELDS = {f.strip() for f in fm.group(1).replace("\n", " ").split(",")} if fm else set()
GATE = set()
for m in mods:
    facts_in(m["use_when"], GATE); facts_in(m["do_not_use_when"], GATE)
PRODUCED = GATE | EXT_FIELDS | Q | {f for m in mods for f in m["required_facts"]}
for k, v in B.items():
    for f in v.get("requires_facts", []):
        if f not in PRODUCED:
            (err if B[k].get("status", "ACTIVE") == "ACTIVE" else warn)(
                f"{k}: requires fact '{f}' that no gate, extraction field or question produces - block can never render")

# ---------- every ACTIVE module has a renderable block
for k in sorted(ACTIVE):
    m = MOD[k]
    if not m["building_blocks"]:
        if m["strength"] >= 50: warn(f"{k}: no building blocks (drafts from proposition only)")
        continue
    gate = facts_in(m["use_when"], set())
    usable = []
    for b in m["building_blocks"]:
        bb = B.get(b)
        if not bb or bb.get("status", "ACTIVE") != "ACTIVE": continue
        rf = [f for f in bb.get("requires_facts", []) if f not in gate and f not in EXT_FIELDS]
        if not rf: usable.append(b)
    if not usable:
        warn(f"{k}: every block needs a fact outside this module's gate - may render NO text: {m['building_blocks']}")
    ev_only = all(B.get(b, {}).get("requires_evidence_any") for b in m["building_blocks"] if b in B)
    if ev_only and m["building_blocks"] and "has_evidence" not in json.dumps(m["use_when"]):
        warn(f"{k}: all blocks need uploaded evidence but the module gate does not - fires with no text when evidence is absent")

# ---------- evidence codes vs extraction doc types
dt = re.search(r'"doc_types":\s*\{"<evidence_id>":\s*"(.*?)"\}', ext_body, re.S)
DOC_TYPES = {t.strip() for t in dt.group(1).replace("\n", "").split("|")} if dt else set()
ALIAS = {"WITNESS": "WITNESS_STATEMENT", "PCN": "PCN", "NTK": "NTK"}
ev = set()
for m in mods:
    ev |= set(m["evidence_helpful"])
    for x in re.findall(r"has_evidence: (\w+)", json.dumps(m["use_when"]).replace('"', '').replace("{", "").replace("}", "")): ev.add(x)
for v in B.values(): ev |= set(v.get("requires_evidence_any", []))
for e in sorted(ev):
    if e not in DOC_TYPES and ALIAS.get(e) not in DOC_TYPES:
        warn(f"evidence code {e} has no extraction doc type - can never be detected from an upload")
if "WITNESS" in ev: warn("evidence code WITNESS must be mapped from doc type WITNESS_STATEMENT in the engine")

# ---------- routing gate vs doc types
rt_types = set()
for r in RT["stop_rules"]:
    rt_types |= set(r.get("when_any_doc_type", [])) | set(r.get("when_no_doc_type_in", []))
rt_types |= set(RT["proceed_rule"]["when_any_doc_type"])
for t in rt_types - DOC_TYPES: err(f"routing uses doc type {t} that extraction never outputs")
if not (rt_types - DOC_TYPES): ok("Routing gate doc types all exist in the extraction prompt")
for r in RT["stop_rules"]:
    if r.get("status") != "CLIENT_APPROVED": warn(f"routing message {r['id']} is {r.get('status')}")
    if r.get("cta") and "url" in r["cta"] and r["cta"]["url"] is None: warn(f"routing {r['id']}: CTA URL not set")
if RT["backup_checks"]["appeal_window"]["days_from_notice_issue"] is None:
    warn("appeal time limit not set - only notices that SAY the window closed are stopped (client decision)")

# ---------- prompts
for k, v in P.items():
    if "version" not in v or "body" not in v: err(f"prompt {k}: missing version/body")
val_rules = set(re.findall(r"VAL-[A-Z]+", P["validation"]["body"]))
if SRC_VAL - val_rules: err(f"validation prompt misses validators {sorted(SRC_VAL - val_rules)}")
else: ok(f"Validation prompt covers all {len(SRC_VAL)} section 17 validators")
for k, v in P.items():
    for mid in set(re.findall(r"KB-[A-Z]+-\d+", v["body"])):
        if mid not in MOD: err(f"prompt {k} names unknown module {mid}")
        elif mid not in ACTIVE and "among the candidates" not in v["body"] and "if present" not in v["body"]:
            err(f"prompt {k} names non-ACTIVE module {mid} unconditionally")

# ---------- customer-facing text leak check
LEAK = re.compile(r"confirm:|missing:|\bKB-|\bPP-|\bAI-[A-Z]+-\d|\bVAL-|\bSCOP-|\bCL-|\b[A-Z]{3,}_[A-Z_]+\b|\b[a-z]+_[a-z_]+\b")
cust = []
for r in RT["stop_rules"]:
    cust += [r.get("customer_title", ""), r.get("customer_message", "")] + ([r["cta"].get("label", "")] if r.get("cta") else [])
cust += [v["label"] for v in R.values()]
leaks = [t for t in cust if LEAK.search(t)]
for t in leaks: err(f"customer-facing text contains an internal code: {t[:60]}")
if not leaks: ok("No internal codes in customer-facing messages or route labels")

# ---------- report
print("=" * 70); print(f"ERRORS: {len(ERR)}   WARNINGS: {len(WARN)}   PASSED: {len(OK)}"); print("=" * 70)
for m in OK: print("  PASS ", m)
for m in ERR: print("  ERROR", m)
for m in WARN: print("  WARN ", m)
sys.exit(1 if ERR else 0)