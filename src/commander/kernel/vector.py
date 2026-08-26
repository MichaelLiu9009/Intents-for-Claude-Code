"""M24 vector surface v1: mechanical embedder (character 1+2-gram
bag + cosine).

Single-function interface —— swapping the embedder (local model /
embed API) only ever swaps embed(); recall / aggregation / the two
panes don't move at all. The source of truth always lives in
SQLite, vectors are a derived artifact and never persisted:
mechanical recompute is free; only once an embed API ships does it
need a vectors cache table (v15 contingency plan). Zero tokenization
for CJK: 1-gram catches single-character hits, 2-gram supplies word
discrimination.
"""
import math

_PUNCT = set(" \t\r\n,。,.、;;::!!??()()[]【】{}\"'`——-·…~/\\|<>")


def _grams(text: str) -> list[str]:
    chars = [c.lower() for c in text if c not in _PUNCT]
    return chars + [a + b for a, b in zip(chars, chars[1:])]


def embed(text: str) -> dict[str, float]:
    """Text → L2-normalized 1+2-gram bag-of-grams vector (empty text
    → empty vector)."""
    v: dict[str, float] = {}
    for g in _grams(text or ""):
        v[g] = v.get(g, 0.0) + 1.0
    n = math.sqrt(sum(x * x for x in v.values()))
    if n > 0:
        for g in v:
            v[g] /= n
    return v


def sim(a: dict[str, float], b: dict[str, float]) -> float:
    """cosine (both sides already normalized → dot product); iterate
    over the shorter side."""
    if len(b) < len(a):
        a, b = b, a
    return sum(w * b[g] for g, w in a.items() if g in b)
