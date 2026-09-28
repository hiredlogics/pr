"""RAG layer.

Two corpora, two different jobs:

1. KB corpus (modules + approved building blocks). Retrieval is RESTRICTED to
   the module ids the Reasoning engine has already gated as eligible. Search
   only ranks and selects wording inside that set - it can never widen scope
   (enforces KB-GOV-03: no new legal proposition outside the approved KB).

2. Case corpus (the customer's own uploads: lease, tenancy, recovery report).
   Used to find and quote verbatim clauses with an offset so the validator can
   prove every quote is exact (VAL-RES).

Ranking = reciprocal-rank fusion of a lexical score (BM25-like TF-IDF here)
and a dense embedding score (pluggable Embedder; production: pgvector).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(s: str) -> list[str]:
    return _TOKEN.findall(s.lower())


class Embedder(Protocol):
    def __call__(self, texts: list[str]) -> np.ndarray: ...


@dataclass
class Doc:
    doc_id: str
    text: str
    meta: dict


class HybridRetriever:
    def __init__(self, docs: list[Doc], embedder: Optional[Embedder] = None):
        self.docs = docs
        self.embedder = embedder
        self._tf = [Counter(tokenize(d.text)) for d in docs]
        df = Counter(t for tf in self._tf for t in tf)
        n = max(len(docs), 1)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self._emb = embedder([d.text for d in docs]) if embedder and docs else None

    def _lexical(self, q: str) -> np.ndarray:
        qt = tokenize(q)
        return np.array([sum(tf.get(t, 0) * self._idf.get(t, 0) for t in qt) / (1 + math.log(1 + sum(tf.values())))
                         for tf in self._tf])

    def _dense(self, q: str) -> Optional[np.ndarray]:
        if self._emb is None:
            return None
        qv = self.embedder([q])[0]
        denom = (np.linalg.norm(self._emb, axis=1) * np.linalg.norm(qv)) + 1e-9
        return (self._emb @ qv) / denom

    def search(self, query: str, allowed_ids: Optional[set[str]] = None, k: int = 8,
               id_of: Callable[[Doc], str] = lambda d: d.meta.get("module_id", d.doc_id)) -> list[Doc]:
        idx = [i for i, d in enumerate(self.docs) if allowed_ids is None or id_of(d) in allowed_ids]
        if not idx:
            return []
        rankings = []
        lex = self._lexical(query)
        rankings.append(sorted(idx, key=lambda i: -lex[i]))
        dense = self._dense(query)
        if dense is not None:
            rankings.append(sorted(idx, key=lambda i: -dense[i]))
        rrf = Counter()
        for r in rankings:
            for rank, i in enumerate(r):
                rrf[i] += 1.0 / (60 + rank)
        return [self.docs[i] for i, _ in rrf.most_common(k)]


# ---------------------------------------------------------------- customer docs
_CLAUSE_SPLIT = re.compile(r"(?m)^\s*(?=(?:clause\s+)?\d+(?:\.\d+)*[.)]?\s)", re.I)
PARKING_TERMS = ("park", "vehicle", "car space", "bay", "garage")
REGULATION_TERMS = ("regulation", "rules", "permit", "vary", "variation", "management company may")


def find_parking_clauses(evidence_id: str, text: str) -> list[dict]:
    """Return verbatim parking clauses with char offsets. Heuristic first pass;
    production adds an LLM classifier on top, but quotes are ALWAYS the raw span."""
    out = []
    pos = 0
    for part in _CLAUSE_SPLIT.split(text):
        start = text.find(part, pos)
        pos = start + len(part)
        chunk = part.strip()
        if not chunk:
            continue
        low = chunk.lower()
        if any(t in low for t in PARKING_TERMS):
            m = re.match(r"(?:clause\s+)?(\d+(?:\.\d+)*)", chunk, re.I)
            out.append({
                "evidence_id": evidence_id,
                "clause_ref": m.group(1) if m else None,
                "text": chunk,
                "offset": start,
                "has_regulations_power": any(t in low for t in REGULATION_TERMS),
            })
    return out
