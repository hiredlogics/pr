"""Phase 3A fixtures: what knowledge retrieval is given, and what it should find.

A fixture starts AFTER Phases 1 and 2. It carries no image, no customer prose
and no letter:

    facts      the authoritative case facts (what FactManager holds)
    packet     the Phase-2 semantic packet, as a model reading would have made it
               (built through understanding.build_packet, so readiness is real)
    findings   verified legal findings (PoFA codes)
    expect     must          modules a careful reader of the pinned KB would call
                             genuinely related (recall is scored against these)
               acceptable    related too, so retrieving them is not a false positive
               must_not      modules whose retrieval would mean a signal was misread
               zero          nothing at all should be retrieved

"Related" is a judgement about the KB's own wording (topic, proposition, gate
facts). It is written down here before any retrieval is run, never adjusted to
match what a run returned. A retrieved module is only a CANDIDATE: nothing here
says it is supported.

The wording of propositions below is normalised meaning, as the semantic model
returns it. No fixture is a customer's sentence.
"""
from __future__ import annotations

from typing import Any, Optional

NEUTRAL_BREACH = "Vehicle not parked within the markings of a bay"
OVERSTAY_BREACH = "Overstayed the maximum free period"
PAYMENT_BREACH = "Parked without paying the tariff"
PERMIT_BREACH = "Parked without displaying a valid permit"

BASE_FACTS = {
    "operator_name": "Northgate Parking Ltd", "pcn_number": "NG778899", "vrm": "KL55MNO",
    "parking_location": "Northgate Retail Park", "site_postcode": "LS2 7AA",
    "parking_event_date": "2026-06-02", "notice_issue_date": "2026-06-08",
    "charge_amount": "£100", "jurisdiction": "ENGLAND_WALES", "notice_route": "POSTAL",
}
ANPR_TIMES = {"entry_time": "09:10", "exit_time": "12:37"}

# Modules the notice itself relates to through the KB's curated allegation /
# evidence-method relationships (data/kb_relations.yaml). Independent of what the
# customer said.
OVERSTAY_RELATED = {"KB-GRACE-01", "KB-GRACE-02", "KB-CON-01", "KB-ANPR-01", "KB-TIME-01",
                    "KB-PAY-01", "KB-KEY-01"}
ANPR_RELATED = {"KB-ANPR-01", "KB-ANPR-02", "KB-ANPR-03", "KB-TIME-01"}
PAYMENT_RELATED = {"KB-PAY-01", "KB-PAY-02", "KB-PAY-03", "KB-KEY-01", "KB-KEY-02",
                   "KB-CON-01", "KB-CON-02", "KB-REC-01"}
PERMIT_RELATED = {"KB-AUTH-01", "KB-AUTH-02", "KB-AUTH-03", "KB-RES-06", "KB-REC-01"}


# ------------------------------------------------------------- packet pieces
def concept(name: str, polarity: str = "AFFIRMED", conf: float = 0.9) -> dict:
    return {"concept": name, "polarity": polarity, "attribution": "CUSTOMER",
            "source_text": "", "confidence": conf}


def event(eid: str, etype: str, description: str, polarity: str = "AFFIRMED") -> dict:
    return {"event_id": eid, "event_type": etype, "description": description,
            "polarity": polarity, "attribution": "CUSTOMER", "source_text": "",
            "confidence": 0.9}


def atom(aid: str, category: str, proposition: str, polarity: str = "AFFIRMED") -> dict:
    return {"atom_id": aid, "category": category, "proposition": proposition,
            "polarity": polarity, "attribution": "CUSTOMER", "source_text": "",
            "confidence": 0.9}


def rel(src: str, kind: str, tgt: str) -> dict:
    return {"source_id": src, "relationship": kind, "target_id": tgt}


def semantics(concepts=(), events=(), atoms=(), rels=()) -> dict:
    return {"concepts": list(concepts), "events": list(events),
            "narrative_atoms": list(atoms), "relationships": list(rels)}


# Every state the customer stream can be in. Built through the real packet
# builder, so a packet is "ready" exactly when understanding.is_ready says so.
STREAM_STATES = ("READY", "NEEDS_CLARIFICATION", "UNRESOLVED", "NOT_ASSESSED",
                 "PROVIDER_TIMEOUT", "INVALID_RESPONSE")


def make_packet(sem: dict, state: str = "READY") -> dict:
    from pcn_appeal.semantics import understanding as U
    product: dict[str, Any] = {
        "status": "UNDERSTOOD", "summary": "", "uncertainties": [], "clarification": None,
        "concepts": sem.get("concepts", []), "events": sem.get("events", []),
        "narrative_atoms": sem.get("narrative_atoms", []),
        "relationships": sem.get("relationships", []), "material_relevance": [],
        "semantic_mode": "LIVE",
    }
    if state == "READY":
        pass
    elif state == "NEEDS_CLARIFICATION":
        product.update(status="NEEDS_CLARIFICATION", clarification={
            "question": "Which of the two visits did you mean?",
            "ambiguity": "it is not clear which visit is meant"})
    elif state == "UNRESOLVED":
        product.update(status="UNRESOLVED", summary="which visit is meant stays unclear")
    elif state == "NOT_ASSESSED":
        product.pop("status")
    elif state == "PROVIDER_TIMEOUT":
        product.update(semantic_mode="FALLBACK", fallback_reason="semantic_provider_call_failed",
                       exception_class="TimeoutError")
    elif state == "INVALID_RESPONSE":
        product.update(status="MAYBE")
    else:
        raise ValueError(state)
    return U.build_packet(product, [])


# ------------------------------------------------------------------ builders
def fx(fid: str, title: str, *, breach: str = NEUTRAL_BREACH, extra_facts: Optional[dict] = None,
       times: bool = False, sem: Optional[dict] = None, state: str = "READY",
       findings=(), must=(), acceptable=(), must_not=(), zero: bool = False,
       tags=(), group: str = "", coverage_gap: str = "", certainty: Optional[dict] = None,
       evidence_kinds=()) -> dict:
    facts = dict(BASE_FACTS, alleged_breach=breach)
    if times:
        facts.update(ANPR_TIMES)
    facts.update(extra_facts or {})
    return {
        "id": fid, "title": title, "facts": facts, "findings": list(findings),
        "evidence_kinds": list(evidence_kinds),
        "packet": make_packet(sem, state) if sem is not None else None,
        "stream_state": state if sem is not None else None,
        "expect": {"must": sorted(must), "acceptable": sorted(acceptable),
                   "must_not": sorted(must_not), "zero": zero,
                   "certainty": dict(certainty or {})},
        "tags": list(tags), "group": group, "coverage_gap": coverage_gap,
    }


def _multi_visit_events():
    return [event("E1", "DEPARTURE", "The vehicle left the site during the visit"),
            event("E2", "RETURN", "The vehicle came back to the site afterwards")]


def build() -> list[dict]:
    out: list[dict] = []
    add = out.append

    # 1-3 movement ---------------------------------------------------------
    add(fx("F01", "left the site and came back (concepts only)",
           sem=semantics([concept("LEFT_SITE"), concept("RETURNED")]),
           must={"KB-ANPR-01"}, must_not={"KB-PAY-01", "KB-BREAK-01"},
           tags=["movement", "concept"], group="multi_visit"))
    add(fx("F02", "separate visits as an event sequence",
           sem=semantics(events=_multi_visit_events(), rels=[rel("E1", "PRECEDES", "E2")]),
           must={"KB-ANPR-01"}, must_not={"KB-PAY-01", "KB-BREAK-01"},
           tags=["movement", "event", "relationship"], group="multi_visit"))
    add(fx("F03", "passenger drop-off then departure",
           sem=semantics([concept("DROP_OFF")],
                         [event("E1", "ACTIVITY", "A passenger was dropped off"),
                          event("E2", "DEPARTURE", "The vehicle then left")],
                         rels=[rel("E1", "PRECEDES", "E2")]),
           must={"KB-ACT-02"}, acceptable={"KB-ACT-01", "KB-ACT-03"},
           must_not={"KB-PAY-01", "KB-BREAK-01"}, tags=["activity", "relationship"]))

    # 4-6 payment ----------------------------------------------------------
    add(fx("F04", "payment made", breach=PAYMENT_BREACH,
           sem=semantics([concept("PAYMENT_MADE")]),
           must={"KB-PAY-01"}, acceptable=PAYMENT_RELATED, tags=["payment", "concept"]))
    add(fx("F05", "payment attempted and failed", breach=PAYMENT_BREACH,
           sem=semantics([concept("PAYMENT_FAILED")],
                         atoms=[atom("A1", "payment_attempt",
                                     "A payment was attempted and did not complete")]),
           must={"KB-PAY-02", "KB-PAY-03"}, acceptable=PAYMENT_RELATED,
           tags=["payment", "concept", "atom"]))
    add(fx("F06", "registration keying issue", breach=PAYMENT_BREACH,
           sem=semantics([concept("KEYING_ERROR"), concept("PAYMENT_MADE")]),
           must={"KB-KEY-01", "KB-PAY-01"}, acceptable=PAYMENT_RELATED,
           tags=["keying", "concept"]))

    # 7-9 circumstances ----------------------------------------------------
    add(fx("F07", "vehicle breakdown",
           sem=semantics([concept("BROKEN_DOWN"), concept("IMMOBILISED")]),
           must={"KB-BREAK-01", "KB-BREAK-02"}, acceptable={"KB-BREAK-03"},
           must_not={"KB-PAY-01", "KB-ANPR-01"}, tags=["mechanical", "concept"]))
    add(fx("F08", "permit held", breach=PERMIT_BREACH,
           sem=semantics([concept("PERMIT_HELD")]),
           must={"KB-AUTH-02"}, acceptable=PERMIT_RELATED, tags=["authorisation", "concept"]))
    add(fx("F09", "access barrier failed to open",
           sem=semantics(events=[event("E1", "ACCESS_ISSUE",
                                       "The exit barrier did not open so the vehicle could not leave")],
                         atoms=[atom("A1", "access_issue",
                                     "The exit barrier did not open")]),
           must={"KB-INFRA-01"}, acceptable={"KB-GRACE-02"},
           must_not={"KB-PAY-01", "KB-BREAK-01"}, tags=["access", "event", "atom"]))

    # 10-13 notice / evidence ---------------------------------------------
    add(fx("F10", "notice timing finding (PoFA)", findings=["POFA_POSTAL_LATE"],
           extra_facts={"pofa_route": "POSTAL", "pofa_finding": "POFA_POSTAL_LATE",
                        "notice_issue_date": "2026-06-26"},
           must={"KB-POFA-02", "KB-POFA-05"}, acceptable={"KB-POFA-01"},
           must_not={"KB-PAY-01"}, tags=["notice", "legal_finding"]))
    add(fx("F11", "ANPR capture sequence incomplete", breach=OVERSTAY_BREACH, times=True,
           extra_facts={"anpr_sequence_incomplete": True},
           must={"KB-ANPR-02"}, acceptable=OVERSTAY_RELATED | ANPR_RELATED,
           tags=["notice", "fact", "anpr"]))
    add(fx("F12", "independent evidence contradicts the allegation",
           extra_facts={"independent_evidence_contradicts": True},
           evidence_kinds=["DASHCAM"], must={"KB-EV-01"}, tags=["evidence", "fact"]))
    add(fx("F13", "signage issue raised",
           extra_facts={"signage_issue_raised": True, "signage_issue_type": "CONFLICTING"},
           must={"KB-SIGN-03"}, acceptable={"KB-SIGN-01", "KB-SIGN-02", "KB-SIGN-04"},
           tags=["signage", "fact"]))

    # 14-17 polarity, noise, unknown --------------------------------------
    add(fx("F14", "payment explicitly NOT made",
           sem=semantics([concept("PAYMENT_MADE", "NEGATED")]),
           acceptable={"KB-CON-01", "KB-CON-02"},
           must_not={"KB-PAY-01", "KB-KEY-01", "KB-KEY-02"},
           tags=["negation", "concept"], certainty={}))
    add(fx("F15", "payment possibly made (uncertain)",
           sem=semantics([concept("PAYMENT_MADE", "UNCERTAIN", 0.4)]),
           acceptable={"KB-PAY-01", "KB-KEY-01", "KB-KEY-02", "KB-CON-01", "KB-CON-02"},
           tags=["uncertainty", "concept"],
           certainty={"KB-PAY-01": "UNCERTAIN"}))
    add(fx("F16", "irrelevant customer detail",
           sem=semantics([concept("CHILD_PRESENT")],
                         atoms=[atom("A1", "person_present",
                                     "A passenger in the car was listening to music")]),
           zero=True, tags=["noise", "atom"]))
    add(fx("F17", "unknown but material atom (emergency delay)",
           sem=semantics(atoms=[atom("A1", "unmapped_material",
                                     "An ambulance had to attend a relative at the entrance and "
                                     "the delay was an unpredictable emergency")]),
           must={"KB-HOSP-02"}, acceptable={"KB-HOSP-01", "KB-HOSP-03"},
           tags=["atom", "unknown_material"]))
    add(fx("F17b", "unknown atom with no module in the KB",
           sem=semantics(atoms=[atom("A1", "unmapped_material",
                                     "The vehicle was being used to carry an injured animal to a vet")]),
           zero=True, coverage_gap="no module addresses animal or veterinary circumstances",
           tags=["atom", "unknown_material", "coverage"]))

    # 18 relationships -----------------------------------------------------
    add(fx("F18a", "departure PRECEDES return", group="rel_order",
           sem=semantics(events=_multi_visit_events(), rels=[rel("E1", "PRECEDES", "E2")]),
           must={"KB-ANPR-01"}, tags=["relationship"]))
    add(fx("F18b", "return PRECEDES departure (same events, other order)", group="rel_order",
           sem=semantics(events=_multi_visit_events(), rels=[rel("E2", "PRECEDES", "E1")]),
           must_not={"KB-ANPR-01"}, tags=["relationship"]))
    add(fx("F18c", "two arrivals SEPARATE_FROM each other",
           sem=semantics(events=[event("E1", "ARRIVAL", "The vehicle arrived in the morning"),
                                 event("E2", "ARRIVAL", "The vehicle arrived again in the afternoon")],
                         rels=[rel("E1", "SEPARATE_FROM", "E2")]),
           must={"KB-ANPR-01"}, tags=["relationship"]))
    add(fx("F18d", "cause drives the delay",
           sem=semantics(events=[event("E1", "MECHANICAL", "The engine failed to restart"),
                                 event("E2", "DELAY", "The vehicle stayed longer than planned")],
                         rels=[rel("E1", "CAUSES", "E2")]),
           must={"KB-BREAK-01", "KB-BREAK-02"}, acceptable={"KB-BREAK-03"},
           tags=["relationship"]))
    add(fx("F18e", "contested events are not retrieved as affirmed",
           sem=semantics(events=[event("E1", "RETURN", "The vehicle came back"),
                                 event("E2", "DEPARTURE", "The vehicle left")],
                         rels=[rel("E1", "CONTRADICTS", "E2")]),
           must_not={"KB-ANPR-01"}, tags=["relationship", "contradiction"]))

    # 19-20 blocked customer stream ---------------------------------------
    drop = semantics([concept("DROP_OFF"), concept("BROKEN_DOWN")])
    for state in STREAM_STATES[1:]:
        add(fx(f"F19-{state}", f"customer stream {state} + independent notice candidates",
               breach=OVERSTAY_BREACH, times=True, sem=drop, state=state,
               findings=["POFA_POSTAL_LATE"],
               extra_facts={"pofa_route": "POSTAL", "pofa_finding": "POFA_POSTAL_LATE"},
               must={"KB-POFA-02", "KB-ANPR-01", "KB-TIME-01"},
               acceptable=OVERSTAY_RELATED | ANPR_RELATED | {"KB-POFA-01", "KB-POFA-05"},
               must_not={"KB-ACT-02", "KB-BREAK-01", "KB-BREAK-02", "KB-BREAK-03"},
               tags=["blocked", "independent"]))
        add(fx(f"F20-{state}", f"customer stream {state}, nothing independent",
               sem=drop, state=state, zero=True, tags=["blocked", "nothing_independent"]))
    add(fx("F19-READY", "customer stream READY + independent notice candidates",
           breach=OVERSTAY_BREACH, times=True, sem=drop, state="READY",
           findings=["POFA_POSTAL_LATE"],
           extra_facts={"pofa_route": "POSTAL", "pofa_finding": "POFA_POSTAL_LATE"},
           must={"KB-POFA-02", "KB-ANPR-01", "KB-TIME-01", "KB-ACT-02", "KB-BREAK-01",
                 "KB-BREAK-02"},
           acceptable=OVERSTAY_RELATED | ANPR_RELATED | {"KB-POFA-01", "KB-POFA-05", "KB-BREAK-03"},
           tags=["blocked", "independent", "control"]))

    # 21-25 breadth, roles -------------------------------------------------
    add(fx("F21", "no relevant module", zero=True, tags=["zero"]))
    add(fx("F22", "several genuinely relevant modules", breach=PAYMENT_BREACH,
           sem=semantics([concept("BROKEN_DOWN"), concept("PAYMENT_MADE"), concept("DROP_OFF")]),
           must={"KB-BREAK-01", "KB-BREAK-02", "KB-PAY-01", "KB-ACT-02"},
           acceptable=PAYMENT_RELATED | {"KB-BREAK-03"}, tags=["breadth"]))
    add(fx("F23", "misleading high-similarity wording beside a strong structured match",
           sem=semantics([concept("BROKEN_DOWN"), concept("IMMOBILISED")],
                         atoms=[atom("A1", "unmapped_material",
                                     "The garage invoice for the engine repair was paid by card")]),
           must={"KB-BREAK-01", "KB-BREAK-02"}, acceptable={"KB-BREAK-03"},
           must_not={"KB-PAY-02", "KB-PAY-03"}, tags=["vector_false_positive"]))
    add(fx("F24", "support-only module related to the case",
           extra_facts={"authority_mismatch_identified": True},
           must={"KB-LAND-02"}, tags=["role", "support"], certainty={}))
    add(fx("F25", "legal-conclusion module related to the case", findings=["POFA_POSTAL_LATE"],
           extra_facts={"pofa_route": "POSTAL", "pofa_finding": "POFA_POSTAL_LATE"},
           must={"KB-POFA-01", "KB-POFA-02", "KB-POFA-05"}, tags=["role", "legal_conclusion"]))

    # 26 context sensitivity ----------------------------------------------
    moved = semantics([concept("LEFT_SITE"), concept("RETURNED")])
    add(fx("F26a", "same movement, ANPR notice", breach=OVERSTAY_BREACH, times=True, sem=moved,
           must={"KB-ANPR-01", "KB-ANPR-02", "KB-ANPR-03", "KB-TIME-01"},
           acceptable=OVERSTAY_RELATED, tags=["context"], group="context"))
    add(fx("F26b", "same movement, attendant-observation notice", breach=NEUTRAL_BREACH,
           extra_facts={"observation_time": "11:20"}, sem=moved,
           acceptable={"KB-ANPR-01"}, must_not={"KB-ANPR-02", "KB-ANPR-03", "KB-TIME-01"},
           tags=["context"], group="context"))

    # 27 equivalent meaning (third member; F01 and F02 are the others) ----
    add(fx("F27", "multiple visits as a derived concept plus an event sequence",
           sem=semantics([concept("MULTIPLE_VISITS")], _multi_visit_events(),
                         rels=[rel("E1", "PRECEDES", "E2")]),
           must={"KB-ANPR-01"}, must_not={"KB-PAY-01", "KB-BREAK-01"},
           tags=["movement", "equivalence"], group="multi_visit"))

    # 29-30 added after probing unseen variants (see the Phase 3A report)
    add(fx("F29", "keying slip stated as an event only", breach=PAYMENT_BREACH,
           sem=semantics(events=[event("E1", "KEYING",
                                       "A character in the registration was entered wrongly")]),
           must={"KB-KEY-01", "KB-KEY-02"}, acceptable=PAYMENT_RELATED,
           tags=["keying", "event"]))
    add(fx("F30", "passenger pick-up (concept with no module-readable fact)",
           sem=semantics([concept("PICK_UP")]), acceptable={"KB-ACT-02"},
           coverage_gap="ontology maps PICK_UP to pickup_activity; no KB gate reads that fact "
                        "(KB-ACT-02 reads dropoff_activity), so a pick-up concept reaches no module",
           tags=["activity", "coverage"]))

    # 28 candidate cap -----------------------------------------------------
    weak = [atom(f"A{i}", "unmapped_material",
                 w) for i, w in enumerate([
                     "The payment screen showed a message about the registration",
                     "The sign at the entrance mentioned a time limit for customers",
                     "A permit scheme was mentioned by a member of staff",
                     "The lease document mentioned allocated parking",
                     "A receipt for the visit shows the time of the purchase",
                     "The parking charge notice arrived by post after some days",
                     "Evidence from a camera shows the vehicle at the site",
                     "The barrier and the access system were mentioned",
                     "A loading and unloading activity was observed nearby",
                     "The hospital visit and the delay were discussed",
                     "A disabled passenger and extra time were discussed",
                     "The electric vehicle charging bay was busy",
                     "The managing agent and the landowner authority were discussed",
                     "Entry and exit times were queried",
                     "A payment machine and an app were both mentioned"] * 2)]
    add(fx("F28", "many weak atoms must not push out a strong structured match",
           sem=semantics([concept("BROKEN_DOWN"), concept("IMMOBILISED")], atoms=weak),
           must={"KB-BREAK-01", "KB-BREAK-02"}, tags=["cap"]))
    return out


FIXTURES = build()
BY_ID = {f["id"]: f for f in FIXTURES}
GROUPS: dict[str, list[str]] = {}
for _f in FIXTURES:
    if _f["group"]:
        GROUPS.setdefault(_f["group"], []).append(_f["id"])
