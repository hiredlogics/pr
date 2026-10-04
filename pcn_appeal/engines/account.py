"""System-wide free-text → structured fact → professional drafting proposition.

Required flow
-------------
  Customer free-text / adaptive answer / narrative / comment
       ↓
  Fact extraction (this module) — meaning preserved, wording discarded
       ↓
  Verified structured Fact + provenance (source=CUSTOMER_FREE_TEXT)
       ↓
  Case intelligence / knowledge / drafting
       ↓
  Professional appeal prose (never customer questionnaire copy)

Rules
-----
  * Free text is EVIDENCE/INPUT only — never letter copy.
  * Do not invent facts while rewriting.
  * Do not drop material facts merely because they arrived as free text.
  * Do not hard-code a single allegation type; extract any supported circumstance.
  * Provenance chain is recorded: original → normalized fact → drafting proposition.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import fact_lifecycle
from ..fact_graph import same_value
from ..hypotheses import CONFIRMED, KINDS
from ..models import CaseFile, Fact, FactSource, FactStatus, SourceKind
from . import narrative

# --------------------------------------------------------------------------- extractors
# Each rule is system-wide: pattern → structured fact + professional proposition.
# Patterns are intentionally conservative (precision over recall) to avoid invention.


@dataclass(frozen=True)
class CircumstanceRule:
    """One extractable circumstance from free text."""
    fact_name: str
    value: Any
    pattern: re.Pattern[str]
    proposition: str
    # Optional: only treat as contradicting a bay-style allegation when these
    # tokens appear in alleged_breach (empty = always available as a fact).
    allegation_families: tuple[str, ...] = ()


_RULES: tuple[CircumstanceRule, ...] = (
    # Children / family occupancy
    CircumstanceRule(
        "child_occupant_present", True,
        re.compile(
            r"\b("
            r"(kids?|kidd|children|child|toddler|baby|infant|son|daughter)"
            r".{0,40}\b(in (the )?(car|vehicle)|with me|with us|remained)|"
            r"left .{0,60}\b(kids?|kidd|children|child|toddler|baby|infant)|"
            r"(brother|sister).{0,30}\b(in (the )?(car|vehicle))"
            r")",
            re.I,
        ),
        "the vehicle was being used in connection with the presence of children",
        ("child", "family", "parent"),
    ),
    # Seeking a space / arrival
    CircumstanceRule(
        "seeking_parking_space", True,
        re.compile(
            r"\b("
            r"find(ing)? (a |somewhere to )?park|"
            r"look(ing)? for (a )?(space|bay|spot)|"
            r"search(ing)? for (a )?(space|bay|parking)|"
            r"trying to (find|park)|"
            r"couldn'?t find (a )?(space|bay|spot)"
            r")",
            re.I,
        ),
        "time was spent on arrival locating a suitable parking space",
        (),
    ),
    # Breakdown / immobilisation
    CircumstanceRule(
        "vehicle_immobilised", True,
        re.compile(
            r"\b("
            r"broke down|breakdown|broken down|"
            r"flat (battery|tyre|tire)|puncture|"
            r"couldn'?t (move|drive|start)|would not start|"
            r"immobilised|immobilized|stranded"
            r")",
            re.I,
        ),
        "the vehicle became immobilised and could not be moved as intended",
        (),
    ),
    CircumstanceRule(
        "immobilisation_prevented_departure", True,
        re.compile(
            r"\b("
            r"couldn'?t (leave|exit|depart|move)|"
            r"unable to (leave|exit|depart|move)|"
            r"stuck (on site|in the car park|there)|"
            r"prevented .{0,20}(leaving|departing|exiting)"
            r")",
            re.I,
        ),
        "that immobilisation prevented the vehicle from leaving or complying on time",
        (),
    ),
    # Payment
    CircumstanceRule(
        "keying_error_type", "MINOR",
        re.compile(
            r"\b("
            r"typ(o|ed)|keying (error|mistake)|mis-?key|"
            r"(wrong|incorrect|mistyped|mis-?typed|misentered|mis-?entered)"
            r".{0,24}(reg(istration)?|vrm|plate|number plate)|"
            r"(reg(istration)?|vrm|plate).{0,24}"
            r"(typ(o|ed)|wrong|incorrect|mistake|error)"
            r")",
            re.I,
        ),
        "a registration-entry error occurred when the vehicle registration was recorded",
        (),
    ),
    CircumstanceRule(
        "payment_made", True,
        re.compile(
            r"\b("
            r"I paid|we paid|payment (was )?(made|completed|taken)|"
            r"paid (for|via|using|with)|paid (the|my) parking"
            r")",
            re.I,
        ),
        "a parking payment was made for the visit",
        (),
    ),
    CircumstanceRule(
        "payment_attempt_failed", True,
        re.compile(
            r"\b("
            r"(machine|app|meter|kiosk|pay.?station).{0,40}"
            r"(did not|didn'?t|would not|wouldn'?t|failed|fault|error|broken|out of order)|"
            r"(could not|couldn'?t|unable to) (pay|complete|make).{0,20}payment|"
            r"payment (failed|was (unsuccessful|declined)|did not go through)"
            r")",
            re.I,
        ),
        "an attempt to pay was unsuccessful because the payment facility did not work as required",
        (),
    ),
    # Multiple visits: not a rule. "Left and came back" does not say the
    # VEHICLE left; engines/narrative.py reads it as a hypothesis, which is
    # asked about, and only the customer's answer sets `multiple_visits`.
    # Disability / accessibility
    CircumstanceRule(
        "disability_extra_time", True,
        re.compile(
            r"\b("
            r"disability|disabled|blue\s*badge|accessibility|accessible|"
            r"mobility (need|issue|impairment)|wheelchair|"
            r"extra time .{0,30}(disability|disabled|badge)"
            r")",
            re.I,
        ),
        "additional time was required in connection with a disability-related need",
        ("disabled", "blue badge", "accessible"),
    ),
    CircumstanceRule(
        "blue_badge_displayed", True,
        re.compile(
            r"\b("
            r"blue\s*badge.{0,30}(displayed|shown|on (display|show))|"
            r"(displayed|showing|showed).{0,20}blue\s*badge"
            r")",
            re.I,
        ),
        "a disabled person's badge or equivalent indicator was displayed in the vehicle",
        ("disabled", "blue badge", "accessible"),
    ),
    # Permit / authorisation
    CircumstanceRule(
        "permit_held", True,
        re.compile(
            r"\b("
            r"(had|held|have|has) (a )?(valid )?permit|"
            r"permit (was )?(held|displayed|shown|valid)|"
            r"authorised|authorized to park|"
            r"permission to park"
            r")",
            re.I,
        ),
        "a valid permit or permission to park was held for the location",
        ("permit",),
    ),
    # Residential
    CircumstanceRule(
        "resident_connection_stated", True,
        re.compile(
            r"\b("
            r"I (live|lived)|we live|resident|leaseholder|tenant|"
            r"my (flat|apartment|flatmate|landlord)|our (flat|lease|tenancy)|"
            r"allocated (bay|space)|home (parking|bay)"
            r")",
            re.I,
        ),
        "the vehicle's presence was connected with residential use of the property",
        (),
    ),
    # Loading
    CircumstanceRule(
        "loading_activity", True,
        re.compile(
            r"\b(loading|unloading|delivering|delivery|dropping off|picking up (goods|parcels))\b",
            re.I,
        ),
        "the vehicle's presence was connected with genuine loading or unloading activity",
        ("loading",),
    ),
    # EV charging. "Charge" on its own is the parking charge itself - every
    # customer writes it - so the word only counts next to a vehicle, a charger
    # or an EV term. Matching it bare put an invented charging session into
    # released letters.
    CircumstanceRule(
        "ev_charging_session", True,
        re.compile(
            r"\b("
            r"electric (car|vehicle|van)s?|evs?|"
            r"charg(er|ers)|charge ?points?|"
            r"charging (point|station|bay|space|post|unit|cable|lead|session|socket)s?|"
            r"plug(ged)? in|on charge|"
            r"charg(e|ed|ing) (up )?(my|the|our|his|her|their|a) "
            r"(own )?(car|vehicle|van|ev|electric)|"
            r"(car|vehicle|van|ev) (was|is|were) (still )?charging"
            r")\b",
            re.I,
        ),
        "the vehicle was present in connection with a genuine charging session",
        # Whole terms only: the bare tokens "ev" and "charg" occur inside
        # "every", "event" and "parking charge", which made any allegation
        # look like an EV one.
        ("electric", "charging", "charger", "charge point", "ev bay", "ev charg"),
    ),
    # Disabled / reserved bay conditions met (generic)
    CircumstanceRule(
        "bay_conditions_met_accounted", True,
        re.compile(
            r"\b("
            r"entitled to (use|park)|eligible (to use|for)|"
            r"conditions (were )?met|allowed to (use|park)"
            r")",
            re.I,
        ),
        "the conditions of use for the reserved bay were met during the visit",
        ("bay", "space", "disabled", "parent", "child", "permit"),
    ),
)


@dataclass
class FreeTextExtraction:
    """One original → normalized → drafting proposition chain."""
    source: str = "CUSTOMER_FREE_TEXT"
    original: str = ""
    fact_name: str = ""
    normalized_value: Any = None
    drafting_proposition: str = ""
    relevant_to_allegation: bool = False

    def as_dict(self) -> dict[str, Any]:
        span = (self.original or "")[:240]
        return {
            "source": self.source,
            "original": (self.original or "")[:300],
            "fact_name": self.fact_name,
            "normalized_value": self.normalized_value,
            "drafting_proposition": self.drafting_proposition,
            "relevant_to_allegation": self.relevant_to_allegation,
            "answer_id": f"free_text:{self.fact_name}",
            "text_span": span,
            "extractor_version": fact_lifecycle.EXTRACTOR_VERSION,
        }


_NEGATION = re.compile(
    r"\b("
    r"no|not|never|without|didn't|did not|weren't|were not|wasn't|was not|"
    r"none|neither|nobody"
    r")\b",
    re.I,
)


def _match_negated(text: str, match: re.Match) -> bool:
    """True when a negation appears in the same clause as the match."""
    start = max(0, match.start() - 40)
    window = text[start:match.end() + 10]
    return bool(_NEGATION.search(window))


# Affirmative occupancy / eligibility facts that negated wording must not invent.
NEGATABLE = frozenset({
    "child_occupant_present", "blue_badge_displayed", "permit_held",
    "bay_conditions_met_accounted", "ev_charging_session", "payment_made",
})


def _intended_facts(texts: list[str]) -> dict[str, Any]:
    """The free-text facts this pass is about to assert, scanned without
    writing. What the clear keeps: a fact re-asserted with the same value keeps
    its node, its history and its lineage instead of being retracted and
    recreated (P8.4)."""
    out: dict[str, Any] = {}
    for raw in texts:
        text = str(raw).strip()
        if len(text) < 4:
            continue
        for rule in _RULES:
            if rule.fact_name in out:
                continue
            m = rule.pattern.search(text)
            if not m:
                continue
            if rule.fact_name in NEGATABLE and rule.value is True and _match_negated(text, m):
                continue
            out[rule.fact_name] = rule.value
    return out


def assess_material_account(case: CaseFile, llm=None) -> dict[str, Any]:
    """Extract structured facts + professional propositions from all free text.

    Safe to call repeatedly. P8.4: differential clear — only retract material
    facts that will not be re-asserted with the same value; keep lineage.

    P10.5 order: narrative → LLM-primary semantic concepts → FactManager →
    (then questions elsewhere). Semantic promotion runs before CircumstanceRule
    writes so an already-stated fact is not re-asked into existence.
    """
    texts = _collect_customer_texts(case)
    from ..semantics import extract_and_promote
    semantic = extract_and_promote(case, texts, llm=llm)
    intended = dict(_intended_facts(texts))
    intended.update(_intended_narrative(texts))
    intended.update(semantic.get("intended") or {})
    apply_fact_delta(case, intended)
    narrative.understand(case, texts)
    if not texts:
        return {"extractions": [], "propositions": [], "contradicts": False,
                "semantic": semantic}

    breach = str(case.get("alleged_breach") or "").strip().lower()
    extractions: list[FreeTextExtraction] = []
    seen_facts: set[str] = set()

    for raw in texts:
        text = str(raw).strip()
        if len(text) < 4:
            continue
        for rule in _RULES:
            if rule.fact_name in seen_facts:
                continue
            m = rule.pattern.search(text)
            if not m:
                continue
            # Negated wording must not invent affirmative occupancy/eligibility facts.
            if rule.fact_name in NEGATABLE and rule.value is True and _match_negated(text, m):
                continue
            # Do not overwrite a stronger confirmed/document value with free-text.
            existing = case.facts.get(rule.fact_name)
            if existing and existing.usable and existing.source.kind in (
                    SourceKind.DOCUMENT, SourceKind.CALCULATION) \
                    and existing.status in (
                        FactStatus.CONFIRMED, FactStatus.CORRECTED,
                        FactStatus.EXTRACTED, FactStatus.DERIVED):
                # Still record provenance that free text agreed, but keep doc value.
                pass
            else:
                case.put(Fact(
                    f"F-{rule.fact_name}", rule.fact_name, rule.value,
                    FactStatus.ANSWERED,
                    FactSource(
                        SourceKind.CUSTOMER_FREE_TEXT,
                        f"free_text:{rule.fact_name}",
                        excerpt=text[:240],
                    ),
                ))
            relevant = _relevant_to_allegation(rule, breach)
            extractions.append(FreeTextExtraction(
                original=text,
                fact_name=rule.fact_name,
                normalized_value=rule.value,
                drafting_proposition=rule.proposition,
                relevant_to_allegation=relevant,
            ))
            seen_facts.add(rule.fact_name)

    _record_described_event(case, texts)
    extractions += _confirmed_hypotheses(case, breach)
    extractions += _answered_circumstances(case, {e.fact_name for e in extractions})

    if not extractions:
        fact_lifecycle.protect_derived_facts(case)
        case.audit.append({
            "event": "material_account",
            "extractions": [],
            "note": "no extractable structured circumstances in free text",
        })
        fact_lifecycle.persist_versions(case)
        return {"extractions": [], "propositions": [], "contradicts": False}

    # Prefer propositions that address the allegation; keep others for drafting
    # when they support available grounds (do not drop merely for being free text).
    relevant_props = [e.drafting_proposition for e in extractions if e.relevant_to_allegation]
    other_props = [e.drafting_proposition for e in extractions if not e.relevant_to_allegation]
    propositions = list(dict.fromkeys(relevant_props + other_props))
    # Narrative-atom departure reason (generic; not a semantic ontology concept).
    dep = case.get("departure_reason")
    if dep and dep not in propositions:
        propositions.append(str(dep))
    for row in getattr(case, "free_text_provenance", None) or []:
        if row.get("fact_name") == "departure_reason":
            prop = row.get("drafting_proposition") or row.get("normalized_value")
            if prop and prop not in propositions:
                propositions.append(str(prop))

    contradicts = _account_contradicts_allegation(extractions, breach)
    case.put(Fact(
        "F-account_contradicts_allegation", "account_contradicts_allegation",
        bool(contradicts), FactStatus.DERIVED,
        FactSource(SourceKind.CALCULATION, "material_account"),
    ))
    case.put(Fact(
        "F-material_account_propositions", "material_account_propositions",
        propositions, FactStatus.DERIVED,
        FactSource(SourceKind.CALCULATION, "material_account"),
    ))
    if propositions:
        case.put(Fact(
            "F-material_account_proposition", "material_account_proposition",
            propositions[0], FactStatus.DERIVED,
            FactSource(SourceKind.CALCULATION, "material_account"),
        ))

    # Legacy bay flags for existing modules/blocks.
    if any(e.fact_name == "child_occupant_present" for e in extractions):
        case.put(Fact(
            "F-bay_child_occupant_accounted", "bay_child_occupant_accounted", True,
            FactStatus.DERIVED,
            FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:child_occupant_present"),
        ))

    provenance = [e.as_dict() for e in extractions]
    case.raw_answers["_material_source_texts"] = "\n".join(
        dict.fromkeys(e.original for e in extractions))[:2000]
    # P8.4: persist provenance in raw_answers so reload can restore lineage.
    try:
        case.raw_answers["_free_text_provenance"] = json.dumps(provenance)[:8000]
    except (TypeError, ValueError):
        case.raw_answers["_free_text_provenance"] = "[]"
    case.free_text_provenance = provenance
    fact_lifecycle.protect_derived_facts(case)
    case.audit.append({
        "event": "material_account",
        "provenance": provenance,
        "fact_versions": list(getattr(case, "fact_versions", None) or []),
        "propositions": propositions,
        "contradicts_allegation": contradicts,
    })
    fact_lifecycle.persist_versions(case)

    return {
        "extractions": provenance,
        "propositions": propositions,
        "contradicts": contradicts,
        "source_texts": [e.original for e in extractions],
        "semantic": semantic,
    }


# The customer telling what happened ("I parked", "we stopped"): a first-person
# account of the event. It records only that an account was given. It is not
# a disclosure and never identifies the driver: who drove is the tri-state
# driver_disclosure_to_operator fact, which free text never sets.
_DESCRIBED_EVENT = re.compile(
    r"\b(?:i|we)\s+(?:had\s+|have\s+|was\s+|were\s+)?"
    r"(?:parked|park|drove|stopped|pulled\s+(?:in|up|into)|left\s+(?:the|my|our)\s+car|arrived)\b",
    re.I)


def _record_described_event(case: CaseFile, texts: list[str]) -> None:
    for raw in texts:
        m = _DESCRIBED_EVENT.search(str(raw))
        if m and not _match_negated(str(raw), m):
            case.put(Fact(
                "F-customer_described_event", "customer_described_event", True,
                FactStatus.DERIVED,
                FactSource(SourceKind.CUSTOMER_FREE_TEXT, "free_text:customer_described_event",
                           excerpt=str(raw)[:240]),
            ), reason="account_describes_event")
            return


def _confirmed_hypotheses(case: CaseFile, breach: str) -> list[FreeTextExtraction]:
    """What the account says, once the customer has confirmed it. The answer is
    the fact (source ANSWER); the account's words are kept as its provenance so
    drafting sees the same proposition it saw when the phrase alone set it."""
    out = []
    for h in case.fact_hypotheses:
        kind = KINDS.get(h["fact_name"])
        if h["status"] != CONFIRMED or not kind or not kind.proposition:
            continue
        if case.get(h["fact_name"]) != h["possible_value"]:
            continue
        out.append(FreeTextExtraction(original=h["source_text"], fact_name=h["fact_name"],
                                      normalized_value=h["possible_value"],
                                      drafting_proposition=kind.proposition,
                                      relevant_to_allegation=True))
    return out


def _answered_circumstances(case: CaseFile, seen: set[str]) -> list[FreeTextExtraction]:
    """A circumstance the customer stated by ANSWERING a question (P4).

    Same effect as the account saying it and the customer confirming it: an
    answered "were children in the vehicle? yes" is a customer statement of the
    bay's condition, and counts towards account_contradicts_allegation exactly
    as a confirmed hypothesis does. Only a true closed-form answer to a fact a
    circumstance rule knows counts; the answer fact itself is unchanged
    (source ANSWER, owner customer).
    """
    out, breach = [], str(case.get("alleged_breach") or "").lower()
    for rule in _RULES:
        if rule.fact_name in seen or rule.value is not True:
            continue
        f = case.facts.get(rule.fact_name)
        if f is None or not f.usable or f.source.kind != SourceKind.ANSWER or f.value is not True:
            continue
        seen = seen | {rule.fact_name}
        out.append(FreeTextExtraction(original=f"answer: {rule.fact_name}", fact_name=rule.fact_name,
                                      normalized_value=True, drafting_proposition=rule.proposition,
                                      relevant_to_allegation=_relevant_to_allegation(rule, breach)))
    return out


def _relevant_to_allegation(rule: CircumstanceRule, breach: str) -> bool:
    if not breach:
        return True
    if not rule.allegation_families:
        # Always potentially material to general grounds (payment, breakdown, …).
        return True
    return any(tok in breach for tok in rule.allegation_families)


def _account_contradicts_allegation(
        extractions: list[FreeTextExtraction], breach: str) -> bool:
    if not breach:
        return False
    # Restricted-bay style: customer affirms eligibility / child / badge / permit.
    bayish = any(tok in breach for tok in (
        "bay", "space", "parent", "child", "disabled", "blue badge", "permit",
        "family", "reserved", "accompanied",
    ))
    if not bayish:
        return False
    eligibility = {
        "child_occupant_present", "blue_badge_displayed", "permit_held",
        "bay_conditions_met_accounted", "disability_extra_time", "ev_charging_session",
        "loading_activity",
    }
    return any(e.fact_name in eligibility for e in extractions)


def apply_fact_delta(case: CaseFile, intended: dict[str, Any]) -> dict:
    """P8.4: versioned delta. Same-value facts stay; corrections supersede."""
    return fact_lifecycle.apply_fact_delta(case, intended)


def _intended_narrative(texts: list[str]) -> dict[str, Any]:
    """Dry-scan narrative atoms so apply_fact_delta does not retract them."""
    from .narrative import read
    out: dict[str, Any] = {}
    for raw in texts:
        text = str(raw).strip()
        if len(text) < 4:
            continue
        out.update(read(text).facts)
    return out


def _clear_material(case: CaseFile, *, keep_names: Optional[set[str]] = None,
                    keep_values: Optional[dict[str, Any]] = None) -> None:
    """Deprecated name: reassessment now goes through apply_fact_delta."""
    apply_fact_delta(case, keep_values or {n: case.get(n) for n in (keep_names or ())})


def _collect_customer_texts(case: CaseFile) -> list[str]:
    """All free-text channels: narrative, adaptive answers, comments, descriptions."""
    out: list[str] = []
    skip = {
        "narrative", "_material_source_texts", "_free_text_provenance",
        "_fact_versions", "operator_ata", "site_postcode", "notice_route",
        "jurisdiction",
    }
    narrative = (case.raw_answers or {}).get("narrative") or ""
    if str(narrative).strip():
        out.append(str(narrative).strip())
    for name, raw in (case.raw_answers or {}).items():
        if name in skip or name.startswith("_"):
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        if text.lower() in ("yes", "no", "true", "false", "y", "n", "1", "0"):
            continue
        # Skip pure choice codes.
        if text.upper() in ("BPA", "IPC", "NOT_SHOWN", "APP", "MACHINE", "PHONE",
                            "WEBSITE", "OTHER", "NONE", "MINOR", "DIFFERENT_VEHICLE"):
            continue
        out.append(text)
    return list(dict.fromkeys(out))
