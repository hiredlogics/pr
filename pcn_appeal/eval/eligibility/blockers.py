"""Phase 3B.2: the hard-blocker inventory and where each blocker fact comes from.

A hard `do_not_use_when` condition is only a safety rule if its truth can become
known in the real application. This module lists every hard blocker of the KB,
the facts it stands on, and for each fact the production path that writes it
(or asks for it). It reads the KB; it names no module in any decision. It is
evidence for a test and a report, not a second eligibility engine.

Source classes
  DOCUMENT                    read from an uploaded document
  FACTMANAGER                 a value FactManager holds from any provenance
  DETERMINISTIC_DERIVATION    calculated from facts/evidence the case holds
  VERIFIED_FINDING            a legal-calculation finding
  EVIDENCE                    read from the evidence set
  CUSTOMER_QUESTION           asked of the customer (a questions.yaml fact the
                              Question Authority can approve)
  NONE                        nothing writes it or asks it  -> UNREACHABLE_BLOCKER
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...rules.dsl import referenced_facts

ROOT = Path(__file__).resolve().parents[2]            # .../pcn_appeal

SOURCE_CLASSES = ("DOCUMENT", "FACTMANAGER", "DETERMINISTIC_DERIVATION", "VERIFIED_FINDING",
                  "EVIDENCE", "CUSTOMER_QUESTION", "NONE")

# fact -> (source classes, [(file under pcn_appeal/, text proving the path exists)],
#          which truth values the real flow can give a condition on it)
SOURCES: dict[str, dict[str, Any]] = {
    "payment_made": dict(
        classes=("CUSTOMER_QUESTION",), settles=("TRUE", "FALSE"),
        paths=[("data/questions.yaml", "payment_made:")]),
    "payment_method": dict(
        classes=("CUSTOMER_QUESTION",), settles=("TRUE", "FALSE"),
        paths=[("data/questions.yaml", "payment_method:")]),
    "permitted_period_ended": dict(
        classes=("CUSTOMER_QUESTION",), settles=("TRUE", "FALSE"),
        paths=[("data/questions.yaml", "permitted_period_ended:")]),
    "lease_evidence_provided": dict(
        classes=("DETERMINISTIC_DERIVATION", "EVIDENCE"), settles=("TRUE", "FALSE"),
        paths=[("engines/reasoning.py", "F-lease_evidence_provided")]),
    "lease_parking_clause_found": dict(
        classes=("DETERMINISTIC_DERIVATION", "EVIDENCE"), settles=("TRUE", "FALSE"),
        paths=[("engines/reasoning.py", "F-lease_parking_clause_found")]),  # only once read
    "lease_has_regulations_clause": dict(
        classes=("DETERMINISTIC_DERIVATION", "EVIDENCE"), settles=("TRUE", "FALSE"),
        paths=[("engines/reasoning.py", "F-lease_has_regulations_clause")]),
    "driver_status": dict(
        classes=("DETERMINISTIC_DERIVATION", "DOCUMENT"), settles=("TRUE", "FALSE"),
        paths=[("models.py", 'view["driver_status"]'), ("disclosure.py", "case.driver_status =")]),
    "pofa_route": dict(
        classes=("DETERMINISTIC_DERIVATION", "VERIFIED_FINDING"), settles=("TRUE", "FALSE"),
        paths=[("engines/recovery.py", "F-pofa_route")]),
    "notice_sides_complete": dict(
        classes=("DETERMINISTIC_DERIVATION", "EVIDENCE"), settles=("TRUE", "FALSE"),
        paths=[("notice_completeness.py", "F-notice_sides_complete")]),
    "ntk_invites_pass_to_driver": dict(
        classes=("DOCUMENT", "DETERMINISTIC_DERIVATION"), settles=("TRUE", "FALSE"),
        paths=[("engines/recovery.py", "F-ntk_invites_pass_to_driver"),
               ("data/prompts.yaml", "ntk_invites_pass_to_driver")]),
    "shopping_purchase_confirmed": dict(
        classes=("DETERMINISTIC_DERIVATION", "EVIDENCE"), settles=("TRUE", "FALSE"),
        paths=[("engines/recovery.py", "F-shopping_purchase_confirmed")]),
    # Only the sentinel UNKNOWN is ever written (a receipt does not show validation).
    # The validation outcome itself has no source yet; the EV-01 exclusion does not
    # depend on it being anything else, because shopping_purchase_confirmed settles it.
    "parking_validation_status": dict(
        classes=("DETERMINISTIC_DERIVATION",), settles=("TRUE",),
        paths=[("engines/recovery.py", "F-parking_validation_status")]),
}

# What the audit decided for each hard blocker (module -> verdict). A module not
# listed here has no hard blocker (`always: false`).
CLASSIFICATION = {
    "KB-ACT-02": "HARD_RESOLVABLE", "KB-AUTH-01": "HARD_RESOLVABLE",
    "KB-AUTH-02": "HARD_RESOLVABLE", "KB-CON-01": "HARD_RESOLVABLE",
    "KB-CON-02": "HARD_RESOLVABLE", "KB-EV-01": "HARD_RESOLVABLE",
    "KB-PAY-02": "HARD_RESOLVABLE", "KB-PAY-03": "HARD_RESOLVABLE",
    "KB-POFA-01": "HARD_RESOLVABLE", "KB-POFA-02": "HARD_RESOLVABLE",
    "KB-POFA-03": "HARD_RESOLVABLE", "KB-POFA-04": "HARD_RESOLVABLE",
    "KB-POFA-05": "HARD_RESOLVABLE", "KB-RES-02": "HARD_RESOLVABLE",
    # P8 (client instruction 2026-10-07): blocker is driver_status, settled by disclosure.
    "KB-POFA-07": "HARD_RESOLVABLE", "KB-KEEPER-01": "HARD_RESOLVABLE",
}

# Conditions the audit removed from the hard set, and why (the verdict before it).
REMOVED = {
    "KB-PAY-01": dict(was="is terms_rejected_left", verdict="UNREACHABLE_BLOCKER -> ADVISORY",
                      why="no extractor, derivation or question produces it; a customer who "
                          "paid has accepted the terms, so it contradicts use_when"),
    "KB-BREAK-01": dict(was="is fault_pre_existing_not_preventing",
                        verdict="UNREACHABLE_BLOCKER -> REDUNDANT + ADVISORY",
                        why="synthetic flag nothing produces; its 'not preventing' half is the "
                            "negation of use_when; the pre-existing-fault half is advisory"),
    "KB-POFA-01": dict(was="eq relevant_land false", verdict="UNREACHABLE_BLOCKER -> RE-EXPRESSED",
                       why="relevant_land has no producer; pofa.assess already turns it into "
                           "pofa_route NOT_APPLICABLE, which is derived"),
    "KB-POFA-04": dict(was="eq relevant_land false (arm)", verdict="REDUNDANT",
                       why="the same exclusion is the pofa_route NOT_APPLICABLE arm"),
    "KB-EV-01": dict(was="eq parking_validation_status UNKNOWN",
                     verdict="ONE-SIDED -> RE-EXPRESSED",
                     why="only the UNKNOWN sentinel is ever written, so the blocker could be "
                         "true or unknown, never false; it now excludes only a relied-on receipt"),
}


def _leaves(pred):
    from ...kg.relations import _leaves as leaves
    return [leaf for _, leaf in leaves(pred)]


def is_hard(module) -> bool:
    return bool(module.do_not_use_when) and module.do_not_use_when != {"always": False}


def inventory(kg) -> list[dict]:
    """One row per (module, blocker fact) for every ACTIVE module with a hard blocker."""
    rows = []
    for m in sorted(kg.active_modules(), key=lambda x: x.module_id):
        if not is_hard(m):
            continue
        for fact in sorted(referenced_facts(m.do_not_use_when)):
            src = SOURCES.get(fact)
            rows.append({
                "module_id": m.module_id, "blocker": m.do_not_use_when, "fact": fact,
                "also_in_use_when": fact in referenced_facts(m.use_when),
                "source": list(src["classes"]) if src else ["NONE"],
                "settles": list(src["settles"]) if src else [],
                "paths": [f"{f}: {n}" for f, n in src["paths"]] if src else [],
                "classification": CLASSIFICATION.get(m.module_id, "UNCLASSIFIED"),
            })
    return rows


def path_exists(file: str, needle: str) -> bool:
    p = ROOT / file
    return p.exists() and needle in p.read_text(encoding="utf-8")


def can_be(pred, want: bool) -> bool:
    """Whether the real flow can make this predicate `want` (True/False), reading
    only which truth values each fact's sources can give. Kleene connectives:
    `all` is FALSE when any arm can be, TRUE only when every arm can be; `any` the
    reverse; `not` swaps."""
    if pred in (None, {}):
        return want is False
    op, arg = next(iter(pred.items()))
    if op == "always":
        return bool(arg) is want
    if op == "all":
        return any(can_be(p, False) for p in arg) if not want else all(can_be(p, True) for p in arg)
    if op == "any":
        return any(can_be(p, True) for p in arg) if want else all(can_be(p, False) for p in arg)
    if op == "not":
        return can_be(arg, not want)
    facts = referenced_facts(pred)
    if op == "has_evidence" or not facts:
        return True
    src = SOURCES.get(next(iter(facts)))
    return bool(src) and ("TRUE" if want else "FALSE") in src["settles"]


def unreachable(kg) -> list[dict]:
    """Hard blockers a real flow cannot settle: a fact with no source at all, a
    blocker that can never become FALSE (the module could never be SUPPORTED), or
    one that can never become TRUE (the exclusion could never fire)."""
    bad = []
    for r in inventory(kg):
        if r["source"] == ["NONE"]:
            bad.append({**r, "defect": "UNREACHABLE_BLOCKER"})
    for m in kg.active_modules():
        if not is_hard(m):
            continue
        if not can_be(m.do_not_use_when, False):
            bad.append({"module_id": m.module_id, "defect": "NEVER_FALSE"})
        if not can_be(m.do_not_use_when, True):
            bad.append({"module_id": m.module_id, "defect": "NEVER_TRUE"})
    return bad
