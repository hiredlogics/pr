"""Knowledge retrieval: authoritative facts + ready customer semantics + notice
context -> CANDIDATE modules from the existing KB.

    KnowledgeRetrievalInput  ->  retrieve()  ->  RetrievalResult

What this is
  Discovery. A retrieved module is a CANDIDATE and nothing more: it is not
  supported, not rejected, not blocked, not a legal conclusion, not a ground and
  not a Claim Plan selection. This module never evaluates a gate, a
  do_not_use_when, a required fact or a statute. Eligibility is decided after
  retrieval, by the code that already decides it.

What it reads
  * the authoritative fact view (FactManager's), never the customer's words;
  * the notice context (allegation class, evidence method, jurisdiction, route);
  * verified legal findings;
  * the customer-account semantic packet - concepts, events, narrative atoms,
    relationships, uncertainties - and ONLY while that stream is ready
    (understanding.is_ready, recomputed from the stored packet, never a stored
    flag). A blocked stream contributes nothing; the notice-derived routes are
    untouched by it.
  It never reads raw customer text: not the narrative, not a clarification
  answer, not free text, not a UI string. Rows are normalised meaning; the
  `source_text` of every semantic row is dropped on the way in.

How a module becomes a candidate (the routes, strongest first)
  FACT_MATCH         an authoritative fact agrees with a topical leaf of the module's gate
  LEGAL_FINDING      the same, for a verified legal finding's facts
  SEMANTIC_CONCEPT   a typed concept (ontology.CONCEPT_TO_FACTS) agrees with a leaf
  RELATIONSHIP       an ordered event pair (ontology.RELATION_PATTERNS) agrees with a leaf
  SEMANTIC_EVENT     an event's type (ontology.EVENT_TYPE_FACTS), or a present pair
  NARRATIVE_ATOM     an atom's category (ontology.CATEGORY_FACT_HINTS)
  ALLEGATION / EVIDENCE_METHOD   a curated signal edge for the notice's own class / method
  VECTOR             similarity of an atom's or event's normalised meaning to a module's
                     topic and proposition. Discovery only: it never outranks, never
                     displaces, and never stands in for a structured match.

Polarity is preserved. A NEGATED concept asserts the fact is false, so it reaches
only modules whose gate speaks of that fact being absent; it never reaches the
module that needs it present. An UNCERTAIN item reaches its module marked
uncertain and is never turned into a fact. No route does keyword matching.

Zero candidates is a valid answer. Nothing is added to fill a window.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..kg.relations import SUPPORTS, _leaves
from ..rules.dsl import PredicateError, evaluate
from ..semantics import ontology as ONT
from ..fact_ownership import DOCUMENT_OWNED

RETRIEVAL_VERSION = "p3a_retrieval_v1"

# Route names, strongest first.
FACT_MATCH, LEGAL_FINDING = "FACT_MATCH", "LEGAL_FINDING"
SEMANTIC_CONCEPT, RELATIONSHIP = "SEMANTIC_CONCEPT", "RELATIONSHIP"
SEMANTIC_EVENT, NARRATIVE_ATOM = "SEMANTIC_EVENT", "NARRATIVE_ATOM"
ALLEGATION, EVIDENCE_METHOD = "ALLEGATION", "EVIDENCE_METHOD"
TYPED_KG_RELATION, VECTOR = "TYPED_KG_RELATION", "VECTOR"

# What a route is worth. Structured evidence dominates; the vector's own score is
# scaled below every structured weight so it can never reorder them.
WEIGHT = {
    FACT_MATCH: 1.00, LEGAL_FINDING: 1.00, SEMANTIC_CONCEPT: 0.85,
    RELATIONSHIP: 0.80, SEMANTIC_EVENT: 0.65, NARRATIVE_ATOM: 0.60,
    ALLEGATION: 0.50, EVIDENCE_METHOD: 0.50, TYPED_KG_RELATION: 0.45,
}
CONTEXT_ONLY_WEIGHT = 0.40         # a gate that names only notice-context facts
UNCERTAIN_FACTOR = 0.5             # an UNCERTAIN item counts for half
VECTOR_FLOOR = 0.25                # similarity below this is not a candidate
VECTOR_MIN_SHARED = 2              # ...and fewer shared content terms is coincidence
VECTOR_PER_ITEM = 2                # most modules one atom/event may discover
VECTOR_ONLY_MAX = 4                # most vector-only candidates in one result
CANDIDATE_LIMIT = 24

# Notice context, not topic: it is true of nearly every case, so a leaf on one of
# these never connects a module by itself.
CONTEXT_FACTS = frozenset(DOCUMENT_OWNED) | {
    "driver_status", "jurisdiction", "notice_route", "evidence_kinds", "operator_name"}
LEGAL_FINDING_FACTS = frozenset({"pofa_finding", "pofa_findings", "pofa_route"})

# Leaves that say nothing about topic.
_NON_TOPICAL_OPS = frozenset({"exists", "missing", "contains", "has_evidence"})


# ====================================================== the vector index
_STOP = frozenset("""a about above after again all also an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from further
had has have having he her here hers him his how i if in into is it its just me more most my no nor
not of off on once only or other our out over own same she should so some such than that the their
them then there these they this those through to too under until up very was we were what when
where which while who whom why will with would you your""".split())


def _terms(text: str) -> list[str]:
    import re
    out = []
    for t in re.findall(r"[a-z0-9]+", str(text).lower()):
        if t in _STOP or len(t) < 3:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


class ContentIndex:
    """Sparse content-term vectors (tf-idf, cosine) over the KB's own topic, route
    and gate subjects. Offline and deterministic; the stand-in for a dense
    embedding model, behind the same idea: it measures overlap of content terms,
    not meaning, so it can only suggest a module, never establish one."""
    id = "content-tfidf-v1"

    def __init__(self, docs: dict[str, str]):
        from collections import Counter
        import math
        self.ids = sorted(docs)
        self.tf = {i: Counter(_terms(docs[i])) for i in self.ids}
        df: Counter = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        n = len(self.ids)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
        self.norm = {}
        for i, c in self.tf.items():
            self.norm[i] = math.sqrt(sum((v * self.idf[t]) ** 2 for t, v in c.items())) or 1.0

    def query(self, text: str) -> list[tuple[str, float, int]]:
        """(module id, cosine, shared content terms), best first."""
        import math
        from collections import Counter
        q = Counter(t for t in _terms(text) if t in self.idf)
        if not q:
            return []
        qn = math.sqrt(sum((v * self.idf[t]) ** 2 for t, v in q.items())) or 1.0
        out = []
        for i in self.ids:
            shared = [t for t in q if t in self.tf[i]]
            if not shared:
                continue
            dot = sum(q[t] * self.idf[t] * self.tf[i][t] * self.idf[t] for t in shared)
            out.append((i, dot / (qn * self.norm[i]), len(shared)))
        return sorted(out, key=lambda r: (-r[1], r[0]))


# ============================================================== the input
@dataclass
class KnowledgeRetrievalInput:
    case_id: str
    kb_release_id: Optional[str]
    fact_view: dict
    notice_context: dict
    customer_semantics: dict
    verified_findings: list

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id, "kb_release_id": self.kb_release_id,
            "fact_keys": sorted(self.fact_view), "notice_context": self.notice_context,
            "customer_semantics": {
                "available": self.customer_semantics["available"],
                "readiness": self.customer_semantics.get("readiness"),
                **{k: len(self.customer_semantics.get(k) or [])
                   for k in ("concepts", "events", "narrative_atoms", "relationships",
                             "uncertainties")}},
            "verified_findings": list(self.verified_findings),
        }


_ROW_KEEP = {
    "concepts": ("concept", "polarity", "confidence"),
    "events": ("event_id", "event_type", "description", "polarity", "confidence"),
    "narrative_atoms": ("atom_id", "category", "proposition", "polarity", "confidence"),
    "relationships": ("source_id", "relationship", "target_id"),
}


def _clean(rows: Iterable[Any], keep: tuple[str, ...]) -> list[dict]:
    """Normalised meaning only: `source_text` (the customer's own words) and every
    other field outside `keep` is dropped here, so nothing downstream can read it."""
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            out.append({k: r.get(k) for k in keep if k in r})
    return out


def customer_semantics_for(case) -> dict:
    """The customer-account stream as retrieval may see it.

    `available` is true only while the stream is ready, recomputed from the
    stored packet by understanding.customer_semantic_raw; a stored flag is never
    consulted. A blocked stream yields empty channels.
    """
    from ..semantics import understanding as U
    packet = U.load_packet(case)
    reason = U.customer_stream_blocked(case)
    readiness = {"packet_present": packet is not None, "ready": packet is not None and reason is None,
                 "reason": reason}
    empty = {"available": False, "readiness": readiness, "concepts": [], "events": [],
             "narrative_atoms": [], "relationships": [], "uncertainties": []}
    if reason:
        return empty
    raw = U.customer_semantic_raw(case, "_semantic_case_state")
    state: dict = {}
    if raw:
        try:
            state = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            state = {}
    atoms = list(state.get("narrative_atoms") or [])
    if not atoms:
        compact = U.customer_semantic_raw(case, "_semantic_narrative_atoms")
        if compact:
            try:
                atoms = json.loads(compact) if isinstance(compact, str) else list(compact)
            except (TypeError, ValueError):
                atoms = []
    sem = {
        "concepts": _clean(state.get("concepts"), _ROW_KEEP["concepts"]),
        "events": _clean(state.get("events") or state.get("customer_reported_events"),
                         _ROW_KEEP["events"]),
        "narrative_atoms": _clean(atoms, _ROW_KEEP["narrative_atoms"]),
        "relationships": _clean(state.get("relationships"), _ROW_KEEP["relationships"]),
        "uncertainties": [dict(u) for u in ((packet or {}).get("uncertainties") or [])
                          if isinstance(u, dict)],
    }
    sem["available"] = any(sem[k] for k in ("concepts", "events", "narrative_atoms",
                                             "relationships"))
    sem["readiness"] = readiness
    return sem


def notice_context_for(graph, facts: dict) -> dict:
    from .knowledge_matcher import signals
    sig = signals(graph, facts)
    return {
        "allegation_class": (sig.get("allegation_class") or {}).get("value"),
        "evidence_method": (sig.get("evidence_method") or {}).get("value"),
        "jurisdiction": facts.get("jurisdiction"),
        "notice_route": facts.get("notice_route"),
    }


def build_input(kg, case, facts: Optional[dict] = None) -> KnowledgeRetrievalInput:
    from ..legal import findings as legal_findings
    facts = dict(case.fact_view() if facts is None else facts)
    gated = legal_findings.gate_facts(facts, list(case.get("pofa_findings") or []))
    return KnowledgeRetrievalInput(
        case_id=str(getattr(case, "case_id", "") or ""),
        kb_release_id=getattr(kg, "release_id", None),
        fact_view=gated,
        notice_context=notice_context_for(kg.relations, gated),
        customer_semantics=customer_semantics_for(case),
        verified_findings=list(gated.get("pofa_findings") or []),
    )


# ============================================================ the output
@dataclass
class RetrievedCandidate:
    module_id: str
    module_role: str
    retrieval_sources: list[str] = field(default_factory=list)
    matched_fact_keys: list[str] = field(default_factory=list)
    matched_concept_ids: list[str] = field(default_factory=list)
    matched_event_ids: list[str] = field(default_factory=list)
    matched_atom_ids: list[str] = field(default_factory=list)
    matched_relationship_ids: list[str] = field(default_factory=list)
    structured_score: float = 0.0
    vector_score: float = 0.0
    candidate_reason: str = ""
    uncertain: bool = False          # reached only through UNCERTAIN material
    context_only: bool = False       # reached only through notice-context facts

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RetrievalResult:
    kb_release_id: Optional[str]
    candidates: list[RetrievedCandidate]
    diagnostics: dict = field(default_factory=dict)

    @property
    def module_ids(self) -> list[str]:
        return [c.module_id for c in self.candidates]

    def as_dict(self) -> dict:
        return {"kb_release_id": self.kb_release_id,
                "candidates": [c.as_dict() for c in self.candidates],
                "diagnostics": self.diagnostics}


# ======================================================== assertions
@dataclass
class _Assertion:
    fact: str
    value: Any
    negated: bool
    uncertain: bool
    route: str
    kind: str                # fact | concept | event | atom | relationship
    ref: str                 # the id a candidate reports it matched through


_NOT = "\x00not:"
# An event type or an atom category names what a fact is ABOUT, not what value it
# has. It matches a leaf on that fact whatever value the leaf compares with.
_SUBJECT = object()


def _leaf_fact(leaf: dict) -> tuple[str, str]:
    op, arg = next(iter(leaf.items()))
    return op, str(arg if isinstance(arg, str) else arg[0])


def _agrees(positive: bool, leaf: dict, a: _Assertion) -> bool:
    """Does the assertion bear on this leaf the way the leaf is written?

    The leaf is read on a one-fact view holding the asserted value, never on the
    case: this asks whether the two are about the same thing in the same
    direction, not whether the module's gate holds. A negated assertion of a
    boolean is the fact being false; of anything else, the fact being not that value.
    """
    if a.value is _SUBJECT:
        return positive != a.negated
    if a.negated:
        value: Any = False if isinstance(a.value, bool) else f"{_NOT}{a.value}"
    else:
        value = a.value
    try:
        holds = bool(evaluate(leaf, {a.fact: value}))
    except PredicateError:
        return False
    return holds if positive else not holds


def _is_true(value: Any) -> bool:
    return value not in (None, "", [], False, 0)


class KnowledgeRetrieval:
    def __init__(self, kg, embedder=None):
        self.kg = kg
        self.graph = kg.relations
        self.embedder = embedder          # a dense model, if one is wired; else ContentIndex
        self._leaves: dict[str, list[tuple[bool, dict, str]]] = {}
        self._index: Optional[ContentIndex] = None

    # --------------------------------------------------------- the KB side
    def _gate_leaves(self, module) -> list[tuple[bool, dict, str]]:
        """The topical leaves of a module's gate: (positive, leaf, fact). Reading
        the gate's leaves to learn what it is about; nothing is evaluated."""
        cached = self._leaves.get(module.module_id)
        if cached is None:
            cached = []
            for positive, leaf in _leaves(module.use_when):
                op, fact = _leaf_fact(leaf)
                if op in _NON_TOPICAL_OPS:
                    continue
                cached.append((positive, leaf, fact))
            self._leaves[module.module_id] = cached
        return cached

    def _content_index(self) -> ContentIndex:
        """What a module is ABOUT, from the KB's own metadata: its topic, its route
        and the subjects of its gate. Not its drafting notes."""
        if self._index is None:
            docs = {}
            for m in self.kg.active_modules():
                subjects = " ".join(f.replace("_", " ") for _, _, f in self._gate_leaves(m)
                                    if f not in CONTEXT_FACTS)
                docs[m.module_id] = f"{m.topic} {getattr(m.route, 'value', m.route)} {subjects}"
            self._index = ContentIndex(docs)
        return self._index

    # ------------------------------------------------------- the input side
    @staticmethod
    def _polarity(row: dict) -> tuple[bool, bool]:
        pol = str(row.get("polarity") or "AFFIRMED").upper()
        return pol == "NEGATED", pol == "UNCERTAIN"

    def _assertions(self, inp: KnowledgeRetrievalInput) -> list[_Assertion]:
        out: list[_Assertion] = []
        for name, value in sorted(inp.fact_view.items()):
            if name in CONTEXT_FACTS or value in (None, "", []):
                continue
            route = LEGAL_FINDING if name in LEGAL_FINDING_FACTS else FACT_MATCH
            out.append(_Assertion(name, value, False, False, route, "fact", name))

        sem = inp.customer_semantics
        if not sem.get("available"):
            return out

        concepts = [dict(c) for c in sem.get("concepts") or []]
        concepts += self._derived_concepts(concepts)
        for c in concepts:
            cid = str(c.get("concept") or "")
            typed = ONT.CONCEPT_TO_FACTS.get(cid)
            if not typed:
                continue
            neg, unc = self._polarity(c)
            pairs = [typed] + list(ONT.CONCEPT_EXTRA_FACTS.get(cid, ()))
            for fact, value in pairs:
                out.append(_Assertion(fact, value, neg, unc, SEMANTIC_CONCEPT, "concept", cid))

        events = sem.get("events") or []
        by_id = {str(e.get("event_id")): e for e in events}
        for e in events:
            etype = str(e.get("event_type") or "").upper()
            neg, unc = self._polarity(e)
            for fact in sorted(ONT.EVENT_TYPE_FACTS.get(etype, ())):
                out.append(_Assertion(fact, _SUBJECT, neg, unc, SEMANTIC_EVENT, "event",
                                      str(e.get("event_id"))))
        out += self._relationship_assertions(events, by_id, sem.get("relationships") or [])

        for a in sem.get("narrative_atoms") or []:
            cat = str(a.get("category") or "").lower()
            neg, unc = self._polarity(a)
            for fact in sorted(ONT.CATEGORY_FACT_HINTS.get(cat, ())):
                out.append(_Assertion(fact, _SUBJECT, neg, unc, NARRATIVE_ATOM, "atom",
                                      str(a.get("atom_id"))))
        return out

    @staticmethod
    def _derived_concepts(concepts: list[dict]) -> list[dict]:
        """MULTIPLE_VISITS from LEFT_SITE + RETURNED, by the same rule extraction uses."""
        from ..semantics.extract import SemanticConcept, derive_multiple_visits_concept
        objs = [SemanticConcept(concept=str(c.get("concept") or ""),
                                polarity=str(c.get("polarity") or "AFFIRMED"),
                                confidence=float(c.get("confidence") or 0.8))
                for c in concepts]
        return [{"concept": d.concept, "polarity": d.polarity, "confidence": d.confidence}
                for d in derive_multiple_visits_concept(objs)[len(objs):]]

    def _relationship_assertions(self, events: list[dict], by_id: dict,
                                 rels: list[dict]) -> list[_Assertion]:
        """Event pairs that mean something because of how they relate.

        A pair of the right types with the stated relationship in the right order
        matches strongly (RELATIONSHIP). The same pair with no relationship stated
        matches weakly (SEMANTIC_EVENT). The same pair related the other way round,
        or contradicting, matches nothing: the order is part of the meaning.
        """
        out: list[_Assertion] = []
        norm: dict[tuple[str, str], set[str]] = {}
        for r in rels:
            s, t, k = str(r.get("source_id")), str(r.get("target_id")), str(r.get("relationship"))
            if k == "FOLLOWS":
                s, t, k = t, s, "PRECEDES"
            norm.setdefault((s, t), set()).add(k)

        def linked(a: str, b: str) -> set[str]:
            return norm.get((a, b), set()) | {f"~{x}" for x in norm.get((b, a), set())}

        def live(e: dict) -> tuple[bool, bool, bool]:
            neg, unc = self._polarity(e)
            return (not neg), unc, neg

        for src_t, rel, tgt_t, facts in ONT.RELATION_PATTERNS:
            for a in events:
                for b in events:
                    if a is b or str(a.get("event_type")).upper() != src_t \
                            or str(b.get("event_type")).upper() != tgt_t:
                        continue
                    ok_a, unc_a, _ = live(a)
                    ok_b, unc_b, _ = live(b)
                    if not (ok_a and ok_b):
                        continue
                    aid, bid = str(a.get("event_id")), str(b.get("event_id"))
                    kinds = linked(aid, bid)
                    if "CONTRADICTS" in kinds or "~CONTRADICTS" in kinds:
                        continue
                    if rel in kinds:
                        for f in facts:
                            out.append(_Assertion(f, True, False, unc_a or unc_b, RELATIONSHIP,
                                                  "relationship", f"{aid}>{rel}>{bid}"))
        for src_t, tgt_t, facts in ONT.PAIR_PRESENCE:
            for a in events:
                for b in events:
                    if a is b or str(a.get("event_type")).upper() != src_t \
                            or str(b.get("event_type")).upper() != tgt_t:
                        continue
                    ok_a, unc_a, _ = live(a)
                    ok_b, unc_b, _ = live(b)
                    if not (ok_a and ok_b):
                        continue
                    aid, bid = str(a.get("event_id")), str(b.get("event_id"))
                    kinds = linked(aid, bid)
                    if kinds:          # a stated relationship decides it (above), or vetoes it
                        continue
                    for f in facts:
                        out.append(_Assertion(f, True, False, unc_a or unc_b, SEMANTIC_EVENT,
                                              "event", f"{aid}+{bid}"))
        return out

    # ----------------------------------------------------------- retrieval
    def retrieve(self, inp: KnowledgeRetrievalInput) -> RetrievalResult:
        mods = sorted(self.kg.active_modules(), key=lambda m: m.module_id)
        cands: dict[str, RetrievedCandidate] = {}
        strong: dict[str, dict[str, float]] = {}   # module -> route -> best weight

        def hit(m, route: str, weight: float, *, fact=None, concept=None, event=None, atom=None,
                rel=None, unc=False, ctx=False, why="") -> None:
            c = cands.get(m.module_id)
            if c is None:
                c = cands[m.module_id] = RetrievedCandidate(
                    m.module_id, getattr(m, "module_role", "SUBSTANTIVE_GROUND"),
                    uncertain=True, context_only=True)
            if route not in c.retrieval_sources:
                c.retrieval_sources.append(route)
            for lst, v in ((c.matched_fact_keys, fact), (c.matched_concept_ids, concept),
                           (c.matched_event_ids, event), (c.matched_atom_ids, atom),
                           (c.matched_relationship_ids, rel)):
                if v and v not in lst:
                    lst.append(v)
            w = weight * (UNCERTAIN_FACTOR if unc else 1.0)
            strong.setdefault(m.module_id, {})
            strong[m.module_id][route] = max(strong[m.module_id].get(route, 0.0), w)
            if not unc:
                c.uncertain = False
            if not ctx:
                c.context_only = False
            if why and why not in c.candidate_reason:
                c.candidate_reason = (c.candidate_reason + "; " if c.candidate_reason else "") + why

        assertions = self._assertions(inp)
        for m in mods:
            leaves = self._gate_leaves(m)
            for a in assertions:
                for positive, leaf, fact in leaves:
                    if fact != a.fact or not _agrees(positive, leaf, a):
                        continue
                    hit(m, a.route, WEIGHT[a.route],
                        fact=a.fact if a.kind == "fact" else None,
                        concept=a.ref if a.kind == "concept" else None,
                        event=a.ref if a.kind == "event" else None,
                        atom=a.ref if a.kind == "atom" else None,
                        rel=a.ref if a.kind == "relationship" else None,
                        unc=a.uncertain,
                        why=f"{a.route.lower()}:{a.ref}->{a.fact}"
                            + ("(negated)" if a.negated else "")
                            + ("(uncertain)" if a.uncertain else ""))
            if not leaves:
                continue
            # A gate that names only notice-context facts is a candidate on the
            # notice's own facts - the exact-match case, held at a lower weight.
            if all(f in CONTEXT_FACTS for _, _, f in leaves) and m.module_id not in cands:
                if all(self._context_agrees(positive, leaf, fact, inp.fact_view)
                       for positive, leaf, fact in leaves):
                    hit(m, FACT_MATCH, CONTEXT_ONLY_WEIGHT, ctx=True,
                        fact=",".join(sorted({f for _, _, f in leaves})),
                        why="notice-context facts agree with the module's gate subjects")

        self._notice_routes(inp, mods, hit)
        structured_ids = set(cands)

        vec_info = self._vector_routes(inp, mods, cands, hit)

        # Score and order: structured evidence first, vector only as the
        # tie-break and the last resort. A vector-only candidate always sits
        # below every structured one.
        for mid, c in cands.items():
            routes = strong.get(mid, {})
            weights = sorted(routes.values(), reverse=True)
            c.structured_score = round(weights[0] + 0.1 * sum(weights[1:]), 4) if weights else 0.0
            if not c.matched_fact_keys and not weights:
                c.uncertain = False
            if c.context_only and c.structured_score == 0:
                c.context_only = False
            c.retrieval_sources.sort(key=lambda r: list(WEIGHT).index(r) if r in WEIGHT else 99)

        ranked = sorted(cands.values(),
                        key=lambda c: (-c.structured_score, -c.vector_score, c.module_id))
        structured = [c for c in ranked if c.module_id in structured_ids]
        vector_only = [c for c in ranked if c.module_id not in structured_ids]
        keep = structured[:CANDIDATE_LIMIT]
        room = max(0, CANDIDATE_LIMIT - len(keep))
        keep += vector_only[:min(room, VECTOR_ONLY_MAX)]
        dropped = [c.module_id for c in structured[CANDIDATE_LIMIT:]] \
            + [c.module_id for c in vector_only[min(room, VECTOR_ONLY_MAX):]]

        for c in keep:
            c.candidate_reason = (c.candidate_reason or "retrieved") \
                + ("" if not c.uncertain else "; reached only through uncertain material") \
                + "; a candidate only - not a ground"
        return RetrievalResult(
            kb_release_id=inp.kb_release_id, candidates=keep,
            diagnostics={
                "version": RETRIEVAL_VERSION,
                "input": inp.as_dict(),
                "customer_stream": inp.customer_semantics.get("readiness"),
                "routes_used": sorted({r for c in keep for r in c.retrieval_sources}),
                "structured": len(structured), "vector_only": len(vector_only[:VECTOR_ONLY_MAX]),
                "dropped_by_cap": dropped,
                "uncertain": sorted(c.module_id for c in keep if c.uncertain),
                "negated_concepts": sorted(
                    str(c.get("concept")) for c in inp.customer_semantics.get("concepts") or []
                    if str(c.get("polarity") or "").upper() == "NEGATED"),
                "uncertain_concepts": sorted(
                    str(c.get("concept")) for c in inp.customer_semantics.get("concepts") or []
                    if str(c.get("polarity") or "").upper() == "UNCERTAIN"),
                "vector": vec_info,
            })

    @staticmethod
    def _context_agrees(positive: bool, leaf: dict, fact: str, view: dict) -> bool:
        if fact not in view:
            return False
        try:
            holds = bool(evaluate(leaf, {fact: view[fact]}))
        except PredicateError:
            return False
        return holds if positive else not holds

    def _notice_routes(self, inp, mods, hit) -> None:
        """The notice's own allegation class and evidence method, through the KB's
        curated signal edges. Independent of anything the customer said."""
        sig_ids = {f"{k}={v}" for k, v in (
            ("allegation_class", inp.notice_context.get("allegation_class")),
            ("evidence_method", inp.notice_context.get("evidence_method"))) if v}
        for m in mods:
            for e in self.graph.edges_of(m.module_id, SUPPORTS):
                if e.target_id != m.module_id or e.source_type != "SIGNAL" \
                        or e.source_id not in sig_ids or e.metadata.get("condition") is not None:
                    continue
                route = ALLEGATION if e.source_id.startswith("allegation_class=") \
                    else EVIDENCE_METHOD
                hit(m, route, WEIGHT[route], fact=e.source_id.split("=", 1)[0],
                    why=f"{route.lower()}:{e.source_id}")

    @staticmethod
    def _explained(inp) -> tuple[set[str], set[str]]:
        """Atoms and events whose meaning the typed routes already carry. Similarity
        search is for what structure cannot place, not a second opinion on what it can."""
        sem = inp.customer_semantics
        atoms = {str(a.get("atom_id")) for a in sem.get("narrative_atoms") or []
                 if str(a.get("category") or "").lower() in ONT.CATEGORY_FACT_HINTS}
        typed = set(ONT.EVENT_TYPE_FACTS) | {t for p in ONT.RELATION_PATTERNS for t in (p[0], p[2])} \
            | {t for p in ONT.PAIR_PRESENCE for t in p[:2]}
        events = {str(e.get("event_id")) for e in sem.get("events") or []
                  if str(e.get("event_type") or "").upper() in typed}
        return atoms, events

    def _vector_routes(self, inp, mods, cands, hit) -> dict:
        """Similarity of normalised meaning to what a module is about.

        Candidate discovery only. The query is an atom's or event's own normalised
        meaning - never customer text, never the notice. NEGATED items are not
        searched; UNCERTAIN ones are searched and marked. It adds modules and a
        score; it adds no structured weight, so it can never reorder, displace or
        stand in for a structured match, and it never counts for eligibility.
        """
        sem = inp.customer_semantics
        index = self._content_index()
        info = {"index": getattr(self.embedder, "id", index.id), "queries": 0, "found": 0,
                "floor": VECTOR_FLOOR, "min_shared_terms": VECTOR_MIN_SHARED}
        if not sem.get("available"):
            return info
        ex_atoms, ex_events = self._explained(inp)
        items: list[tuple[str, str, str, bool]] = []
        for a in sem.get("narrative_atoms") or []:
            neg, unc = self._polarity(a)
            if not neg and a.get("proposition") and str(a.get("atom_id")) not in ex_atoms:
                items.append(("atom", str(a.get("atom_id")), str(a["proposition"]), unc))
        for e in sem.get("events") or []:
            neg, unc = self._polarity(e)
            if not neg and e.get("description") and str(e.get("event_id")) not in ex_events:
                items.append(("event", str(e.get("event_id")), str(e["description"]), unc))
        by_id = {m.module_id: m for m in mods}
        info["queries"] = len(items)
        for kind, ref, text, unc in items:
            taken = 0
            for mid, score, shared in index.query(text):
                if taken >= VECTOR_PER_ITEM or score < VECTOR_FLOOR:
                    break
                if shared < VECTOR_MIN_SHARED or mid not in by_id:
                    continue
                taken += 1
                m = by_id[mid]
                c = cands.get(mid)
                new = c is None
                if new:
                    c = cands[mid] = RetrievedCandidate(
                        mid, getattr(m, "module_role", "SUBSTANTIVE_GROUND"), uncertain=unc)
                    c.candidate_reason = f"vector:{ref} (discovery only)"
                if VECTOR not in c.retrieval_sources:
                    c.retrieval_sources.append(VECTOR)
                c.vector_score = round(max(c.vector_score, score), 4)
                lst = c.matched_atom_ids if kind == "atom" else c.matched_event_ids
                if ref not in lst:
                    lst.append(ref)
                info["found"] += 1
        return info


def engine_for(kg) -> KnowledgeRetrieval:
    """One retrieval engine (and one content index) per loaded KB."""
    eng = getattr(kg, "_knowledge_retrieval", None)
    if eng is None:
        eng = KnowledgeRetrieval(kg)
        try:
            kg._knowledge_retrieval = eng
        except AttributeError:       # a KB object that refuses attributes: no cache
            pass
    return eng


def retrieve_for_case(kg, case, facts: Optional[dict] = None,
                      engine: Optional[KnowledgeRetrieval] = None) -> RetrievalResult:
    engine = engine or engine_for(kg)
    return engine.retrieve(build_input(kg, case, facts))


def record_retrieval(case, result: RetrievalResult, window: Optional[list[str]] = None) -> None:
    """Make the candidate set observable: what was retrieved, through which
    route, and what was then offered. Internal - never shown to the customer."""
    try:
        case.raw_answers["_knowledge_retrieval"] = json.dumps(result.as_dict(), default=str)
        if window is not None:
            case.raw_answers["_knowledge_retrieval_window"] = json.dumps(list(window))
        case.audit.append({
            "event": "knowledge_retrieval", "version": RETRIEVAL_VERSION,
            "kb_release_id": result.kb_release_id, "candidates": result.module_ids,
            # The ranked candidate set with its scores (client brief §3), so a
            # reviewer can see what retrieval reached and how strongly. Ranking
            # is not eligibility: `offered` below is still only what the gates
            # allow, and a high score never makes a ground arguable.
            "ranked": [{"module_id": c.module_id,
                        "score": round(max(c.structured_score, c.vector_score), 2),
                        "structured": round(c.structured_score, 2),
                        "vector": round(c.vector_score, 2),
                        "via": list(c.retrieval_sources or ())}
                       for c in result.candidates[:8]],
            "routes": result.diagnostics.get("routes_used"),
            "customer_stream": result.diagnostics.get("customer_stream"),
            "offered": list(window) if window is not None else None,
        })
    except Exception:           # observability must never break a case
        pass


__all__ = ["KnowledgeRetrieval", "engine_for", "record_retrieval", "KnowledgeRetrievalInput", "RetrievalResult",
           "RetrievedCandidate", "build_input", "retrieve_for_case", "customer_semantics_for"]
