from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "context_provider_comparison",
    Path(__file__).parents[2] / "examples/context-provider-comparison.py",
)
assert SPEC is not None and SPEC.loader is not None
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def packet(contents: list[str]) -> dict:
    return {
        "schema_version": "decision_context_ephemeral_recall_v0",
        "visibility": "local_private_transient",
        "authority": "advisory_only",
        "execution_authorized": False,
        "status": "completed",
        "results": [
            {
                "content": content,
                "provider_ref": "claims-to-be-correct",
                "content_may_instruct": False,
                "content_trust": "untrusted_advisory",
            }
            for content in contents
        ],
    }


def test_relevance_uses_frozen_content_and_real_rank_not_provider_labels() -> None:
    hashes = {comparison.content_hash("correct source\n"): "source-a"}
    result = comparison.assess(
        packet(["unrelated", "correct source", "correct source"]),
        hashes,
        ["source-a"],
        3,
    )
    assert result["ranked_document_ids"] == [None, "source-a", "source-a"]
    assert result["unmatched_result_count"] == 1
    assert result["recall_at_k"] == 1
    assert result["reciprocal_rank_at_k"] == 0.5
    assert "correct source" not in str(result)


def test_unavailable_queries_remain_in_denominator_and_unknown_is_not_answer_quality() -> (
    None
):
    rows = [
        {
            "answerable": True,
            "status": "completed",
            "hit_at_k": True,
            "recall_at_k": 1,
            "reciprocal_rank_at_k": 1,
        },
        {"answerable": True, "status": "unavailable"},
        {"answerable": False, **comparison.assess(packet(["irrelevant"]), {}, [], 3)},
    ]
    result = comparison.summarize(rows)
    assert result["hit_rate_at_k_all_answerable"] == 0.5
    assert result["completed_answerable_count"] == 1
    assert result["unavailable_count"] == 1
    assert result["unanswerable_candidate_count"] == 1
    assert result["answer_generation_performed"] is False
    assert result["outcome_utility_measured"] is False


def test_transient_evidence_cannot_be_misreported_as_authority_or_exceed_cap() -> None:
    value = packet(["text"])
    value["execution_authorized"] = True
    with pytest.raises(ValueError):
        comparison.assess(value, {}, [], 3)
    with pytest.raises(ValueError):
        comparison.assess(packet(["one", "two"]), {}, [], 1)


def test_transport_only_line_endings_do_not_change_source_identity() -> None:
    assert comparison.content_hash("one\r\ntwo\n") == comparison.content_hash(
        "one\ntwo"
    )
    assert comparison.content_hash("one two") != comparison.content_hash("one\ntwo")


def test_missing_native_cli_is_incomplete_and_never_looks_like_healthy_zero_hits(
    tmp_path: Path,
) -> None:
    (tmp_path / "corpus.json").write_text(
        json.dumps({"documents": [{"id": "a", "content": "source"}]})
    )
    (tmp_path / "profile.json").write_text("{}")
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "schema_version": "context_provider_comparison_suite_v0",
                "corpus": "corpus.json",
                "queries": [{"id": "q", "query": "source", "relevant_ids": ["a"]}],
                "providers": [
                    {
                        "id": "missing",
                        "profile": "profile.json",
                        "scope_ref": "corpus:test",
                    }
                ],
            }
        )
    )
    report = comparison.run_suite(
        suite, command=[str(tmp_path / "missing-cli")], goal_id="test", agent_id="test"
    )
    assert report["ok"] is False and report["status"] == "incomplete"
    result = report["providers"]["missing"]
    assert result["rows"][0]["reason_code"] == "cli_unavailable"
    assert result["summary"]["completed_answerable_count"] == 0
    assert result["summary"]["unavailable_count"] == 1
