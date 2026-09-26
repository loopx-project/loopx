"""Dependency-free lexical scoring; callers own identity, authority and selection.

No storage, network, Goal state or source discovery. Scores are corpus-relative
recall order, never confidence. Kept standalone for the packaged repair skill.
"""

from __future__ import annotations

from collections import Counter
from math import log1p
import re
from typing import Iterable, NamedTuple


class BM25Hit(NamedTuple):
    score: float
    matched_terms: tuple[str, ...]


class BM25Scores(NamedTuple):
    # One entry per input document; callers retain their own keys/tie-breaks.
    documents: tuple[BM25Hit, ...]
    unmatched_terms: tuple[str, ...]


def lexical_tokens(text: str) -> list[str]:
    """Casefold Unicode words; split underscore identifiers, without aliases."""
    return re.findall(r"[^\W_]+", text.casefold())


def score_bm25(documents: Iterable[str], query: str) -> BM25Scores:
    """BM25 k1=1.2, b=.75, positive Lucene IDF; no query-frequency boost.

    Every input receives a score, including zero-match documents. Filtering,
    pagination, exact-ID precedence and stable ordering belong to the caller.
    """
    terms = set(lexical_tokens(query))
    counts = [Counter(lexical_tokens(text)) for text in documents]
    lengths = [sum(document.values()) for document in counts]
    average = sum(lengths) / len(lengths) if lengths else 1
    frequencies = Counter(term for document in counts for term in document)
    scores = []
    for document, length in zip(counts, lengths):
        matched = tuple(sorted(terms & document.keys()))
        score = sum(
            log1p((len(counts) - frequencies[term] + .5) / (frequencies[term] + .5))
            * document[term] * 2.2
            / (document[term] + 1.2 * (.25 + .75 * length / (average or 1)))
            for term in matched
        )
        scores.append(BM25Hit(score, matched))
    return BM25Scores(tuple(scores), tuple(sorted(terms - frequencies.keys())))
