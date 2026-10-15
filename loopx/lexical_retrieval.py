"""Compatibility import for independently distributed lexical consumers.

The canonical implementation belongs to context_providers. Existing external
library consumers introduced with PR #5101 still import this path; repository
callers and packaged skill resources use the owning module directly.
"""

from .capabilities.context_providers.lexical_retrieval import (
    BM25Hit,
    BM25Scores,
    lexical_tokens,
    score_bm25,
)

__all__ = ["BM25Hit", "BM25Scores", "lexical_tokens", "score_bm25"]
