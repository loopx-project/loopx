from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.context_providers import build_context_provider
from loopx.capabilities.context_providers.bm25 import (
    BM25ContextProvider,
    han_bigram_tokens,
)
from loopx.capabilities.context_providers.lexical_retrieval import score_bm25


def corpus(tmp_path: Path, documents=None) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "local_bm25_corpus_v0",
                "scope_ref": "corpus:test",
                "documents": documents
                if documents is not None
                else [
                    {"id": "alpha", "content": "alpha alpha beta"},
                    {"id": "other", "content": "beta gamma"},
                ],
            }
        )
    )
    return path


def retrieve(provider, **overrides):
    return provider.retrieve(
        **(
            {
                "namespace": "decision_context",
                "scope_ref": "corpus:test",
                "query": "alpha",
                "query_summary": "Lexical source evidence",
                "max_results": 3,
                "timeout_seconds": 5,
                "observed_at": "2026-01-01T00:00:00Z",
            }
            | overrides
        )
    )


def test_factory_reuses_shared_scorer_and_returns_transient_source_with_safe_receipt(
    tmp_path,
):
    path = corpus(tmp_path)
    provider = build_context_provider({"provider": "bm25", "corpus_path": str(path)})
    result = retrieve(provider)
    assert result.status == "completed" and len(result.items) == 1
    assert result.items[0].content == "alpha alpha beta"
    assert (
        result.items[0].score
        == score_bm25(["alpha alpha beta", "beta gamma"], "alpha").documents[0].score
    )
    public = json.dumps(result.public_packet())
    assert str(path) not in public and "alpha alpha beta" not in public
    assert "bm25:" not in public
    assert result.public_packet()["external_writes_performed"] is False


def test_canonical_words_and_explicit_han_analysis_are_separate_treatments(tmp_path):
    path = corpus(tmp_path, [{"id": "state", "content": "状态机 CLI_output"}])
    assert not retrieve(BM25ContextProvider(corpus_path=path), query="态机").items
    assert retrieve(
        BM25ContextProvider(corpus_path=path, han_bigrams=True), query="态机"
    ).items
    assert han_bigram_tokens("CLI输出 ＡＢＣ as_const") == [
        "cli",
        "输出",
        "abc",
        "as",
        "const",
    ]


def test_scope_missing_corrupt_and_empty_corpora_are_distinct(tmp_path):
    path = corpus(tmp_path)
    provider = BM25ContextProvider(corpus_path=path)
    assert (
        retrieve(provider, scope_ref="corpus:foreign").reason_code == "scope_mismatch"
    )
    assert retrieve(provider, query="unknown").status == "completed"
    assert not retrieve(provider, query="unknown").items
    path.write_text("{invalid")
    assert retrieve(provider).reason_code == "corpus_unavailable"
    path.unlink()
    assert retrieve(provider).reason_code == "corpus_unavailable"
    corpus(tmp_path, [])
    assert retrieve(provider).status == "completed" and not retrieve(provider).items


@pytest.mark.parametrize(
    "patch",
    [
        {"max_results": True},
        {"max_results": 9},
        {"timeout_seconds": float("nan")},
        {"query": "x" * 1001},
    ],
)
def test_invalid_request_bounds_fail_before_corpus_access(tmp_path, patch):
    with pytest.raises(ValueError):
        retrieve(BM25ContextProvider(corpus_path=tmp_path / "absent"), **patch)


def test_duplicate_documents_bounds_read_only_sync_and_bad_config(tmp_path):
    path = corpus(
        tmp_path, [{"id": "same", "content": "one"}, {"id": "same", "content": "two"}]
    )
    provider = BM25ContextProvider(corpus_path=path)
    assert retrieve(provider).reason_code == "corpus_unavailable"
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert retrieve(provider).reason_code == "corpus_unavailable"
    with pytest.raises(ValueError):
        build_context_provider(
            {"provider": "bm25", "corpus_path": str(path), "han_bigrams": "false"}
        )
    with pytest.raises(ValueError):
        build_context_provider(
            {"provider": "bm25", "corpus_path": str(path), "unexpected": True}
        )
    with pytest.raises(ValueError, match="absolute"):
        build_context_provider({"provider": "bm25", "corpus_path": "ambiguous.json"})
    sync = provider.sync(
        namespace="decision_context",
        resources=[("a", "b")],
        timeout_seconds=5,
        observed_at="2026-01-01T00:00:00Z",
        execute=True,
    )
    assert sync.reason_code == "read_only_provider" and sync.write_count == 0
