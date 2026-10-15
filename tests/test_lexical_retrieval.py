"""Neutral scoring and caller-owned relevance boundaries."""

from loopx.capabilities.context_providers.lexical_retrieval import lexical_tokens, score_bm25


def test_rare_multiple_terms_beat_repeated_generic_word():
    result = score_bm25(
        ["lease recovery proof", "lease " * 20, "unrelated"], "lease recovery absent"
    )
    assert result.documents[0].score > result.documents[1].score > 0
    assert result.documents[2].score == 0
    assert result.documents[0].matched_terms == ("lease", "recovery")
    assert result.unmatched_terms == ("absent",)


def test_zero_empty_and_generator_corpora_keep_input_identity():
    assert score_bm25([], "missing").documents == ()
    assert score_bm25([], "missing").unmatched_terms == ("missing",)
    result = score_bm25((text for text in ["", "cash", "cash"]), "cash")
    assert len(result.documents) == 3
    assert result.documents[0].score == 0
    assert result.documents[1] == result.documents[2]
    assert all(hit.score == 0 for hit in score_bm25(["cash"], "").documents)


def test_tokens_are_literal_and_query_repetition_does_not_boost_scores():
    assert lexical_tokens("STALE_Proof [现金] C++") == ["stale", "proof", "现金", "c"]
    assert score_bm25(["cash"], "cash cash") == score_bm25(["cash"], "cash")
    assert score_bm25(["equity:ABC"], "CompanyName").documents[0].score == 0


def test_hand_calculated_default_and_explicit_word_analysis_are_identical():
    from math import log

    documents = ["alpha alpha beta", "beta gamma"]
    # Independent BM25 calculation: N=2, df=1, tf=2, dl=3, avgdl=2.5.
    expected = log(2) * 4.4 / (2 + 1.2 * (0.25 + 0.75 * 3 / 2.5))
    result = score_bm25(documents, "alpha")
    assert abs(result.documents[0].score - expected) < 1e-12
    assert result == score_bm25(documents, "alpha", tokenizer=lexical_tokens)


def test_optional_analysis_is_used_by_query_and_documents_without_another_ranker():
    seen = []

    def tokens(text):
        seen.append(text)
        return list(text)

    result = score_bm25(["状态机", "状态"], "态机", tokenizer=tokens)
    assert seen == ["态机", "状态机", "状态"]
    assert result.documents[0].score > result.documents[1].score
    assert score_bm25(["状态机"], "态机").documents[0].score == 0


def test_legacy_library_import_reexports_the_single_provider_owned_scorer():
    from loopx import lexical_retrieval as legacy
    from loopx.capabilities.context_providers import lexical_retrieval as owner

    # Independently distributed consumers keep their import and exact objects;
    # the root module must not maintain another formula or result type.
    assert legacy.score_bm25 is owner.score_bm25
    assert legacy.lexical_tokens is owner.lexical_tokens
    assert legacy.BM25Hit is owner.BM25Hit
    assert legacy.BM25Scores is owner.BM25Scores
