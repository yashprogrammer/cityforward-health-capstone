"""Text -> vector.

`hash`      signed feature hashing of words and word pairs. No model download, deterministic, runs anywhere (tests, CI, air-gapped
            hospital networks). Weak on synonyms, which is why retrieval is hybrid (vectors + Postgres full-text search).
`fastembed` BAAI/bge-small-en-v1.5 (384 dims) on CPU via ONNX; the container image ships the model, so PHI never leaves the VPC for embedding.
"""
import hashlib
import math
import re
from functools import lru_cache

from .config import settings

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-/]*")
_STOP = frozenset("a an and are as at be by for from has have in is it of on or that the to was were with".split())


class HashEmbedder:
    name = "hash-384"

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _features(self, text: str):
        words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOP]
        for w in words:
            yield w, 1.0
            if len(w) > 5:                       # crude stemming: share a bucket with the 5-char prefix
                yield "~" + w[:5], 0.5
        for a, b in zip(words, words[1:]):
            yield a + " " + b, 0.7

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for feat, weight in self._features(t):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
                v[h % self.dim] += weight if (h >> 40) & 1 else -weight
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class FastEmbedEmbedder:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding   # lazy import: only needed in the container image
        self.name = model
        self._m = TextEmbedding(model_name=model)
        self.dim = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._m.embed(texts)]


@lru_cache
def get_embedder():
    kind = settings().embedder
    if kind == "fastembed":
        return FastEmbedEmbedder()
    if kind == "hash":
        return HashEmbedder(settings().embed_dim)
    raise ValueError(f"unknown EMBEDDER {kind!r}")
