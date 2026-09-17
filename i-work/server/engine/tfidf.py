"""TF-IDF retrieval for offloaded blocks — zero-dependency degraded search.

No pgvector/embedding is available in the codebase, so recall is backed by a
character n-gram TF-IDF + cosine similarity retriever. Chinese is tokenized as
character unigram + bigram (no jieba); ASCII as whitespace/punctuation-split
words, case-normalized. Pure in-memory, rebuilt per session (block count is
small, cost is negligible).
"""

from __future__ import annotations

import math
import re
from collections import Counter

_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    """Tokenize text into n-grams: CJK unigram+bigram, ASCII words."""
    if not text:
        return []
    tokens: list[str] = []
    for seg in re.split(r"([一-鿿]+)", text):
        if not seg:
            continue
        if _CJK_RE.fullmatch(seg):
            chars = list(seg)
            tokens.extend(chars)
            tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
        else:
            tokens.extend(w.lower() for w in _ASCII_TOKEN_RE.findall(seg))
    return tokens


class TFIDFRetriever:
    """Cosine-similarity TF-IDF search over a list of text blocks."""

    def __init__(self, blocks: list | None = None):
        self._idf: dict[str, float] = {}
        self._block_vectors: list[tuple[str, dict[str, float]]] = []
        if blocks is not None:
            self.build(blocks)

    def build(self, blocks) -> None:
        """Index blocks (objects with .block_id and .content)."""
        docs: list[tuple[str, list[str]]] = []
        for b in blocks:
            bid = getattr(b, "block_id", str(id(b)))
            docs.append((bid, tokenize(getattr(b, "content", "") or "")))

        n = len(docs)
        df: Counter = Counter()
        term_counts: list[Counter] = []
        for _, toks in docs:
            c = Counter(toks)
            term_counts.append(c)
            df.update(c.keys())

        self._idf = {
            term: math.log((n + 1) / (cnt + 1)) + 1.0
            for term, cnt in df.items()
        }

        self._block_vectors = []
        for (bid, _), tc in zip(docs, term_counts):
            vec: dict[str, float] = {}
            norm = 0.0
            for term, cnt in tc.items():
                w = cnt * self._idf[term]
                vec[term] = w
                norm += w * w
            norm = math.sqrt(norm)
            if norm > 0:
                vec = {t: w / norm for t, w in vec.items()}
            self._block_vectors.append((bid, vec))

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Return top_k (block_id, score) sorted by cosine similarity desc."""
        q_counter = Counter(tokenize(query))
        q_vec: dict[str, float] = {}
        q_norm = 0.0
        for term, cnt in q_counter.items():
            if term in self._idf:
                w = cnt * self._idf[term]
                q_vec[term] = w
                q_norm += w * w
        q_norm = math.sqrt(q_norm)
        if q_norm == 0:
            return []
        q_vec = {t: w / q_norm for t, w in q_vec.items()}

        scored: list[tuple[str, float]] = []
        for bid, vec in self._block_vectors:
            small, large = (q_vec, vec) if len(q_vec) < len(vec) else (vec, q_vec)
            score = sum(w * large[t] for t, w in small.items() if t in large)
            scored.append((bid, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
