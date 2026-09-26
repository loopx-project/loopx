"""Neutral scoring and caller-owned relevance boundaries."""

from loopx.lexical_retrieval import lexical_tokens, score_bm25


def test_rare_multiple_terms_beat_repeated_generic_word():
    result = score_bm25(["lease recovery proof", "lease " * 20, "unrelated"],
                        "lease recovery absent")
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
