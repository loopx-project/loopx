"""Read-only local corpus adapter over the existing canonical BM25 scorer.

Placement: specialized lexical/file provider, not another decision owner. The
shared scorer also serves repair and review; their defaults remain unchanged.
"""

from __future__ import annotations

import itertools
import json
import math
import time
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .lexical_retrieval import lexical_tokens, score_bm25
from .base import ContextProviderItem, ContextProviderRetrieval, ContextProviderSync


BM25_PROVIDER_ID = "bm25"
# Provider-local private corpus format; no Goal/Agent or authority state.
BM25_CORPUS_SCHEMA = "local_bm25_corpus_v0"
MAX_CORPUS_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 1000
MAX_CONTENT_CHARS = 16000


def han_bigram_tokens(text: str) -> list[str]:
    """Explicit alternative analysis; never change the shared word default."""
    terms: list[str] = []
    normalized = unicodedata.normalize("NFKC", text)
    for han, characters in itertools.groupby(
        normalized,
        key=lambda char: unicodedata.name(char, "").startswith(
            "CJK UNIFIED IDEOGRAPH-"
        ),
    ):
        run = "".join(characters)
        if han:
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
            if len(run) == 1:
                terms.append(run)
        else:
            terms.extend(lexical_tokens(run))
    return terms


def _text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or "\0" in value
    ):
        raise ValueError("BM25 text is missing or exceeds its bound")
    return value


def _corpus(path: Path) -> tuple[str, list[dict[str, str]], str]:
    from ...materials import read_bounded_material_file

    _path, content, revision, _observed_at = read_bounded_material_file(
        path, MAX_CORPUS_BYTES
    )
    raw = json.loads(content.decode("utf-8"))
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "scope_ref",
        "documents",
    }:
        raise ValueError("BM25 corpus fields are invalid")
    if raw["schema_version"] != BM25_CORPUS_SCHEMA:
        raise ValueError("BM25 corpus schema is invalid")
    scope = _text(raw["scope_ref"], maximum=2048)
    if not isinstance(raw["documents"], list) or len(raw["documents"]) > MAX_DOCUMENTS:
        raise ValueError("BM25 corpus document bound exceeded")
    ids: set[str] = set()
    documents: list[dict[str, str]] = []
    for item in raw["documents"]:
        if not isinstance(item, dict) or set(item) != {"id", "content"}:
            raise ValueError("BM25 corpus document fields are invalid")
        doc_id = _text(item["id"], maximum=200)
        if doc_id in ids:
            raise ValueError("BM25 corpus document identities must be unique")
        ids.add(doc_id)
        documents.append(
            {"id": doc_id, "content": _text(item["content"], maximum=MAX_CONTENT_CHARS)}
        )
    return scope, documents, revision


class BM25ContextProvider:
    provider_id = BM25_PROVIDER_ID

    def __init__(self, *, corpus_path: Path, han_bigrams: bool = False) -> None:
        if not isinstance(han_bigrams, bool):
            raise ValueError("BM25 han_bigrams must be boolean")
        self.corpus_path = Path(corpus_path).expanduser()
        if not self.corpus_path.is_absolute():
            raise ValueError("BM25 corpus_path must be absolute")
        self.han_bigrams = han_bigrams

    def retrieve(
        self,
        *,
        namespace: str,
        scope_ref: str,
        query: str,
        query_summary: str,
        max_results: int,
        timeout_seconds: float,
        observed_at: str,
    ) -> ContextProviderRetrieval:
        started = time.monotonic()
        _text(namespace, maximum=120)
        _text(scope_ref, maximum=2048)
        _text(query, maximum=1000)
        _text(query_summary, maximum=220)
        if type(max_results) is not int or not 1 <= max_results <= 8:
            raise ValueError("BM25 max_results must be between 1 and 8")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("BM25 timeout must be positive and finite")
        version = "shared-bm25-v0; " + (
            "han-bigrams" if self.han_bigrams else "unicode-words"
        )
        corpus_read = False

        def result(
            *, reason: str | None = None, items: tuple[ContextProviderItem, ...] = ()
        ) -> ContextProviderRetrieval:
            return ContextProviderRetrieval(
                provider=self.provider_id,
                namespace=namespace,
                status="unavailable" if reason else "completed",
                reason_code=reason,
                query_summary=query_summary,
                observed_at=observed_at,
                search_performed=reason not in {"corpus_unavailable", "scope_mismatch"},
                read_performed=corpus_read,
                items=items,
                requested_limit=max_results,
                provider_version=version,
                latency_ms=int((time.monotonic() - started) * 1000),
                visibility="private",
                target_scope_kind="provider_defined",
            )

        try:
            configured_scope, documents, revision = _corpus(self.corpus_path)
            corpus_read = True
        except (OSError, ValueError):
            return result(reason="corpus_unavailable")
        if scope_ref != configured_scope:
            return result(reason="scope_mismatch")
        scores = score_bm25(
            (row["content"] for row in documents),
            query,
            tokenizer=han_bigram_tokens if self.han_bigrams else lexical_tokens,
        )
        ranked = sorted(
            zip(documents, scores.documents, strict=True),
            key=lambda row: (-row[1].score, row[0]["id"]),
        )
        if time.monotonic() - started > timeout_seconds:
            return result(reason="provider_retrieval_timeout")
        items = tuple(
            ContextProviderItem(
                resource_ref=f"bm25:{revision}:{row['id']}",
                summary="Local corpus lexical evidence",
                content=row["content"],
                score=hit.score,
            )
            for row, hit in ranked[:max_results]
            if hit.score > 0
        )
        return result(items=items)

    def sync(
        self,
        *,
        namespace: str,
        resources: Sequence[tuple[str, str]],
        timeout_seconds: float,
        observed_at: str,
        execute: bool,
    ) -> ContextProviderSync:
        del timeout_seconds, execute
        return ContextProviderSync(
            provider=self.provider_id,
            namespace=namespace,
            status="unavailable",
            observed_at=observed_at,
            requested_count=len(resources),
            completed_count=0,
            reason_code="read_only_provider",
        )


def build_bm25_context_provider(config: Mapping[str, Any]) -> BM25ContextProvider:
    if set(config) - {"provider", "corpus_path", "han_bigrams"}:
        raise ValueError("BM25 provider config contains unsupported fields")
    path = _text(config.get("corpus_path"), maximum=4096)
    return BM25ContextProvider(
        corpus_path=Path(path).expanduser(),
        han_bigrams=config.get("han_bigrams", False),
    )
