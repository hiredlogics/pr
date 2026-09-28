"""Embedders for the dense half of hybrid retrieval.

`HashingEmbedder` is deterministic, offline and needs no API key, so the
pgvector index can be built and queried end to end with nothing running but
Postgres. It is a hashed bag-of-ngrams, not a semantic model: it captures
wording overlap, not meaning.

Production should swap in a real embedding model (Voyage, OpenAI, a local
sentence-transformer) behind the same `__call__(texts) -> np.ndarray`
interface and REBUILD the index, because vectors from different models are
not comparable. `kb_embeddings` rows are therefore stamped with the embedder
id that produced them (see store/kb_sync.py).
"""
from __future__ import annotations

import hashlib
import re

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    id = "hashing-v1"

    def __init__(self, dim: int = 1024, ngrams: tuple[int, ...] = (1, 2)):
        self.dim = dim
        self.ngrams = ngrams

    def __call__(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            toks = _TOKEN.findall(text.lower())
            for n in self.ngrams:
                for i in range(len(toks) - n + 1):
                    gram = " ".join(toks[i:i + n])
                    h = hashlib.blake2b(gram.encode(), digest_size=8).digest()
                    idx = int.from_bytes(h[:4], "big") % self.dim
                    sign = 1.0 if h[4] & 1 else -1.0      # signed hashing: less collision bias
                    out[row, idx] += sign
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
        return out
