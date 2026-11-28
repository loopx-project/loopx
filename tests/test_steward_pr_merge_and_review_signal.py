"""Tests for GitHub PR exact-head review signals and protected Steward PR merges."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
import json
import subprocess
from typing import Any

import pytest

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.github_pr_evidence import (
    _normalize_graphql_pr_node,
    inspect_github_pull_request,
    observe_github_repository_prs,
    parse_github_pr_target,
    summarize_github_pr_signal,
)
from loopx.capabilities.native_chat.steward_monitor import _observe_github_repository_prs
from loopx.capabilities.native_chat.steward_pr_merge import pr_merge_preview
from loopx.chat_actions import _digest
from test_chat_team_plan_action import _fixture
from test_native_steward_private import finish, steward  # noqa: F811,F401


def _valid_bilingual_body(
    *,
    head_oid: str,
    verdict: str,
    reviewer: str,
    author_owned: bool = False,
) -> str:
    header = (
        "Approval conclusion (author-owned PR; GitHub blocks formal self-approval)"
        if author_owned and verdict == "APPROVE"
        else "Request changes conclusion (author-owned PR; GitHub blocks formal self-review)"
        if author_owned and verdict == "REQUEST_CHANGES"
        else "Exact-head review conclusion"
    )
    return (
        f"Reviewer: {reviewer}\n\n"
        f"{header}\n\n"
        "## 动机 / Motivation\n\n"
        "Verify that the repository control plane and pull request state remain synchronized across exact head commits and review conclusions.\n\n"
        "## 改动思路 / Approach\n\n"
        "Bind every preview and confirmation digest to the exact pull request head commit and reviewer conclusion so stale or unverified comments cannot authorize a protected merge.\n\n"
        "## 具体改动 / Changes\n\n"
        f"Exact head `{head_oid}` evaluated across Python and TypeScript control-plane modules, repository slug normalization helpers, exact-head review verification, and end-to-end confirmation round trips with negative drift checks.\n\n"
        "## 对主干的风险 / Risks\n\n"
        "Low risk to main branch stability because protected merges remain gated behind explicit owner confirmation, repository authorization scopes, head commit matching, and fail-closed stale detection on upstream changes.\n\n"
        "## 我的整体评价 / Verdict\n\n"
        f"All tested code paths satisfy the repository quality bar at exact head commit `{head_oid}` with clear negative boundary protection and no unverified side effects.\n\n"
        f"English Verdict: {verdict}\n"
    )


def _sample_pr(
    *,
    number: int = 42,
    head_oid: str = "a" * 40,
    author: str = "loopx-agent",
    review_decision: str = "REVIEW_REQUIRED",
    merge_state: str = "BLOCKED",
    mergeable: str = "MERGEABLE",
    state: str = "OPEN",
    merge_commit: dict[str, Any] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"PR #{number}",
        "url": f"https://github.com/org/repo/pull/{number}",
        "state": state,
        "headRefOid": head_oid,
        "headRefName": "codex/test-pr",
        "baseRefName": "main",
        "isDraft": False,
        "author": {"login": author},
        "reviewDecision": review_decision,
        "mergeStateStatus": merge_state,
        "mergeable": mergeable,
        "updatedAt": "2030-01-01T09:00:00Z",
        "mergedAt": "2030-01-01T09:10:00Z" if state == "MERGED" else None,
        "mergeCommit": merge_commit,
        "reviews": reviews or [],
        "statusCheckRollup": checks
        or [{"conclusion": "SUCCESS", "status": "COMPLETED", "name": "unit-tests"}],
    }


def test_parse_github_pr_target_shapes() -> None:
    assert parse_github_pr_target("https://github.com/org/repo/pull/42") == ("org/repo", 42)
    assert parse_github_pr_target("git:github.com/org/repo#42") == ("org/repo", 42)
    assert parse_github_pr_target("org/repo#42") == ("org/repo", 42)
    assert parse_github_pr_target("#42", default_repository="org/repo") == ("org/repo", 42)
    with pytest.raises(ValueError):
        parse_github_pr_target("#42")
    with pytest.raises(ValueError):
        parse_github_pr_target("merge PR 42 and also 43")


def test_summarize_pr_distinguishes_gpt_gemini_fallback_stale_casual_and_conflict() -> None:
    head = "b" * 40
    old = "0" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    gemini_decl = "model_agent · Gemini 3.1 Pro · Google"

    # Author-owned exact-head GPT APPROVE fallback vs Gemini self-review on the same head
    row = _sample_pr(
        head_oid=head,
        reviews=[
            {
                "id": "PRR_stale",
                "author": {"login": "reviewer-gpt"},
                "state": "APPROVED",
                "commit": {"oid": old},
                "submittedAt": "2030-01-01T08:00:00Z",
                "body": _valid_bilingual_body(head_oid=old, verdict="APPROVE", reviewer=gpt_decl),
            },
            {
                "id": "PRR_casual",
                "author": {"login": "random-user"},
                "state": "COMMENTED",
                "commit": {"oid": head},
                "submittedAt": "2030-01-01T08:10:00Z",
                "body": "approve",
            },
            {
                "id": "PRR_gemini",
                "author": {"login": "loopx-agent"},
                "state": "COMMENTED",
                "commit": {"oid": head},
                "submittedAt": "2030-01-01T08:20:00Z",
                "body": _valid_bilingual_body(
                    head_oid=head, verdict="APPROVE", reviewer=gemini_decl, author_owned=True
                ),
            },
            {
                "id": "PRR_gpt",
                "author": {"login": "loopx-agent"},
                "state": "COMMENTED",
                "commit": {"oid": head},
                "submittedAt": "2030-01-01T08:30:00Z",
                "body": _valid_bilingual_body(
                    head_oid=head, verdict="APPROVE", reviewer=gpt_decl, author_owned=True
                ),
            },
        ],
        checks=[
            {"conclusion": "SUCCESS", "status": "COMPLETED", "name": "ci-pass"},
            {"conclusion": "FAILURE", "status": "COMPLETED", "name": "pages-build"},
        ],
    )
    summary = summarize_github_pr_signal(row)
    rs = summary["review_summary"]
    assert rs["stale_review_count"] == 1
    assert rs["has_review_conflict"] is False
    assert rs["effective_verdict"] == "APPROVE"
    assert summary["bypassed_gates"] == ["REVIEW_REQUIRED", "BLOCKED_MERGE_STATE", "FAILING_CHECKS"]
    assert summary["checks_summary"] == {"total": 2, "pass_count": 1, "fail_count": 1, "pending_count": 0}
    casual = next(item for item in rs["exact_head_reviews"] if item["id"] == "PRR_casual")
    assert casual["valid_verdict_review"] is False and casual["formal_github_verdict"] is None
    decls = [item["reviewer_declaration"] for item in rs["exact_head_reviews"] if item["valid_verdict_review"]]
    assert gemini_decl in decls and gpt_decl in decls

    # Author-owned REQUEST_CHANGES fallback under REVIEW_REQUIRED
    req_changes = summarize_github_pr_signal(
        _sample_pr(
            head_oid=head,
            reviews=[
                {
                    "id": "PRR_rc",
                    "author": {"login": "loopx-agent"},
                    "state": "COMMENTED",
                    "commit": {"oid": head},
                    "submittedAt": "2030-01-01T08:40:00Z",
                    "body": _valid_bilingual_body(
                        head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl, author_owned=True
                    ),
                }
            ],
        )
    )
    assert req_changes["review_summary"]["effective_verdict"] == "REQUEST_CHANGES"

    # Conflicting exact-head reviews force conflict + REQUEST_CHANGES
    conflicted = summarize_github_pr_signal(
        _sample_pr(
            head_oid=head,
            reviews=[
                row["reviews"][3],
                {
                    "id": "PRR_block",
                    "author": {"login": "reviewer-two"},
                    "state": "CHANGES_REQUESTED",
                    "commit": {"oid": head},
                    "submittedAt": "2030-01-01T08:45:00Z",
                    "body": _valid_bilingual_body(
                        head_oid=head, verdict="REQUEST_CHANGES", reviewer=gemini_decl
                    ),
                },
            ],
        )
    )
    assert conflicted["review_summary"]["has_review_conflict"] is True
    assert conflicted["review_summary"]["effective_verdict"] == "REQUEST_CHANGES"


def _graphql_page(
    nodes: list[dict[str, Any]],
    *,
    has_next_page: bool = False,
    end_cursor: str | None = None,
    total_count: int | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    page = {
        "totalCount": len(nodes) if total_count is None else total_count,
        "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
        "nodes": nodes,
    }
    repository = {"pullRequests": page}
    if history is not None:
        repository.update({f"previousPrs{index}": previous for index, previous in enumerate(history)})
        history.append(page)
    return {"data": {"repository": repository}}


def _graphql_reviews(nodes: list[Any], **metadata: Any) -> dict[str, Any]:
    return {"nodes": nodes, "totalCount": len(nodes), "pageInfo": {"hasPreviousPage": False}, **metadata}


def _older_comment_row(initial: dict[str, Any], comments: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": initial["number"], "headRefOid": initial["headRefOid"],
        "updatedAt": initial["updatedAt"], "reviewDecision": initial["reviewDecision"],
        "latestComments": initial["comments"], "comments": comments,
    }


def _comment_readback_rows(cmd: list[str], pages: dict[str, Any]) -> dict[str, Any]:
    return {
        alias: pages[cursor]
        for arg in cmd if arg.startswith("previousComments")
        for alias, cursor in [arg.split("=", 1)]
    }


def _graphql_node(row: dict[str, Any], *, contexts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    copied = dict(row)
    default_contexts = copied.pop("statusCheckRollup", [])
    rollup_contexts = contexts if contexts is not None else default_contexts
    rollup_contexts = [{"id": f"check-{index}", **context} for index, context in enumerate(rollup_contexts)]
    copied["reviews"] = _graphql_reviews(copied.get("reviews") or [])
    copied.setdefault("comments", {"nodes": [], "pageInfo": {"hasPreviousPage": False}})
    copied["commits"] = {
        "nodes": [
            {
                "commit": {
                    "oid": copied["headRefOid"],
                    "statusCheckRollup": {
                        "contexts": {
                            "nodes": rollup_contexts,
                            "totalCount": len(rollup_contexts),
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        ]
    }
    return copied


@pytest.mark.parametrize(
    ("case", "failure_count"),
    [
        ("foreign_failure", None),
        ("missing_commit_oid", None),
        ("missing_rollup", None),
        ("missing_contexts", None),
        ("unreadable_context", None),
        ("truncated_checks", None),
        ("missing_page_info", None),
        ("invalid_page_info", None),
        ("missing_total", None),
        ("wrong_total", None),
        ("over_cap", None),
        ("missing_check_id", None),
        ("duplicate_check_id", None),
        ("full_page", 1),
        ("distinct_ids_same_name", 1),
        ("stale_conclusion", None),
        ("completed_without_conclusion", None),
        ("unknown_run_status", None),
        ("requested_run", 0),
        ("expected_context", 0),
        ("failure_and_unclassified_check", None),
        ("matching_failure", 1),
        ("trusted_top_level", 0),
        ("confirmed_empty", 0),
        ("nullable_rollup", 0),
        ("draft_excluded", None),
    ],
)
def test_steward_attributes_graphql_checks_to_the_observed_head(case: str, failure_count: int | None) -> None:
    head = "a" * 40
    failure = {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}
    success = {**failure, "conclusion": "SUCCESS"}
    checks = {
        "truncated_checks": [success] * 30,
        "over_cap": [failure] + [success] * 30,
        "full_page": [failure] + [success] * 29,
        "distinct_ids_same_name": [failure, success],
        "duplicate_check_id": [{**failure, "id": "duplicate"}, {**success, "id": "duplicate"}],
        "stale_conclusion": [{**failure, "conclusion": "STALE"}],
        "completed_without_conclusion": [{**failure, "conclusion": None}],
        "unknown_run_status": [{**failure, "status": "FUTURE_STATUS", "conclusion": None}],
        "requested_run": [{**failure, "status": "REQUESTED", "conclusion": None}],
        "expected_context": [{"__typename": "StatusContext", "context": "ci", "state": "EXPECTED"}],
        "failure_and_unclassified_check": [failure, {**failure, "conclusion": "STALE"}],
    }.get(case, [failure])
    node = _graphql_node(_sample_pr(number=42, head_oid=head), contexts=checks)
    commit = node["commits"]["nodes"][0]["commit"]
    contexts = commit["statusCheckRollup"]["contexts"]
    if case in {"foreign_failure", "trusted_top_level", "draft_excluded"}:
        commit["oid"] = "b" * 40
    elif case == "matching_failure":
        commit["oid"] = head.upper()
    elif case == "missing_commit_oid":
        del commit["oid"]
    elif case == "missing_rollup":
        del commit["statusCheckRollup"]
    elif case == "missing_contexts":
        commit["statusCheckRollup"] = {}
    elif case == "unreadable_context":
        contexts["nodes"] = [None]
    elif case == "truncated_checks":
        contexts["totalCount"] = 31
        contexts["pageInfo"]["hasNextPage"] = True
    elif case == "missing_page_info":
        del contexts["pageInfo"]
    elif case == "invalid_page_info":
        contexts["pageInfo"]["hasNextPage"] = "false"
    elif case == "missing_total":
        del contexts["totalCount"]
    elif case == "wrong_total":
        contexts["totalCount"] = 2
    elif case == "missing_check_id":
        del contexts["nodes"][0]["id"]
    elif case == "confirmed_empty":
        contexts["nodes"] = []
        contexts["totalCount"] = 0
    elif case == "nullable_rollup":
        commit["statusCheckRollup"] = None

    if case == "trusted_top_level":
        node["statusCheckRollup"] = [{"conclusion": "SUCCESS"}]
    if case == "draft_excluded":
        node["isDraft"] = True

    def runner(cmd, **_kwargs):
        assert not any(arg.startswith("before=") for arg in cmd)
        query = next(arg for arg in cmd if arg.startswith("query="))
        selection = query.split("contexts(first: 30) {", 1)[1].split("... on CheckRun", 1)[0]
        assert {"totalCount", "hasNextPage"}.issubset(selection.split())
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([node])), stderr="")

    snapshot = observe_github_repository_prs("org/repo", runner=runner)
    assert snapshot["status"] == "ok"
    scope = {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
             "include_inbox": False, "pr_filter": {"exclude_drafts": True}}
    for active_scope in (None, scope):
        observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            "repository_snapshot": snapshot, "signal_scope": active_scope, "previous_hash": "sha256:prior",
        })
        if case == "draft_excluded" and active_scope is not None:
            assert observation["observation_incomplete"] is False
            assert observation["scoped_repository_items"] == []
        elif failure_count is None:
            assert observation["observation_incomplete"] is True
            assert observation["result_hash"] == "sha256:prior"
            assert observation["material_change"] is False
        else:
            assert observation["observation_incomplete"] is False
            assert observation["material_change"] is True

    item = snapshot["items"][0]
    if failure_count is None:
        assert item["checks_coverage_incomplete"] is True
        assert item["checks_summary"] is None
        assert ("FAILING_CHECKS" in item["bypassed_gates"]) is (case == "failure_and_unclassified_check")
    else:
        assert item.get("checks_coverage_incomplete") is None
        summary = item["checks_summary"]
        assert summary["fail_count"] == failure_count
        assert summary["total"] == summary["pass_count"] + summary["fail_count"] + summary["pending_count"]
        assert ("FAILING_CHECKS" in item["bypassed_gates"]) is (failure_count > 0)
        if case in {"requested_run", "expected_context"}:
            assert summary["pending_count"] == 1
            assert "PENDING_CHECKS" in item["bypassed_gates"]
        if case == "distinct_ids_same_name":
            assert item["checks_summary"]["total"] == 2
            assert item["checks_summary"]["pass_count"] == 1


@pytest.mark.parametrize(
    ("commit_oid", "nested_clock", "direct_clocks", "draft", "decision"),
    [
        (None, {"committedDate": "2030-01-03T07:00:00Z"}, {}, False, "hold"),
        (" ", {"pushedDate": "2030-01-03T07:00:00Z"}, {}, False, "hold"),
        (42, {"pushedDate": "2030-01-03T07:00:00Z"}, {}, False, "hold"),
        (None, {"committedDate": "2030-01-01T09:00:00+01:00"}, {}, False, "include"),
        (None, {"committedDate": "2030-01-03T07:00:00Z"},
         {"headCommitCommittedAt": "2030-01-01T08:00:00Z"}, False, "include"),
        (None, {"committedDate": "2030-01-03T07:00:00Z"},
         {"headCommitPushedAt": "2030-01-03T07:30:00Z"}, False, "exclude"),
        ("b" * 40, {"pushedDate": "2030-01-03T07:00:00Z"}, {}, False, "include"),
        ("A" * 40, {"pushedDate": "2030-01-03T07:00:00Z"}, {}, False, "exclude"),
        (None, {"committedDate": "2030-01-03T07:00:00Z"}, {}, True, "exclude"),
    ],
    ids=["missing-oid", "blank-oid", "nontext-oid", "same-instant", "direct-wins",
         "dominated-by-trusted-push", "foreign", "matching", "excluded-draft"],
)
def test_steward_publishes_stalled_scope_only_with_sufficient_head_clock_evidence(
    commit_oid: object, nested_clock: dict[str, str], direct_clocks: dict[str, str], draft: bool, decision: str,
) -> None:
    head = "a" * 40
    review_time = "2030-01-01T08:00:00Z"
    review = {
        "id": "request-changes", "state": "CHANGES_REQUESTED", "commit": {"oid": head},
        "author": {"login": "maintainer"}, "submittedAt": review_time,
    }
    node = _graphql_node(_sample_pr(
        head_oid=head, author="contributor", review_decision="CHANGES_REQUESTED", reviews=[review],
    ))
    node["statusCheckRollup"] = []
    node["updatedAt"] = "2030-01-03T07:55:00Z"
    node["comments"]["nodes"] = [{"createdAt": "2030-01-03T06:00:00Z", "author": {"login": "contributor"}}]
    commit = node["commits"]["nodes"][0]["commit"]
    reads = []

    def runner(cmd, **_kwargs):
        reads.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([node])), stderr="")

    scope = {
        "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
        "pr_filter": {"effective_verdicts": ["REQUEST_CHANGES"], "min_stalled_hours": 24,
                      "author_mode": "exclude", "authors": ["maintainer"], "exclude_drafts": True},
    }

    def observe(snapshot, previous_hash=None):
        return ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            "repository_snapshot": snapshot, "signal_scope": scope,
            "observed_at": "2030-01-03T08:00:00Z", "previous_hash": previous_hash,
        })

    included = observe(observe_github_repository_prs("org/repo", runner=runner))
    excluded = observe({"status": "ok", "items": []})
    assert included["scoped_repository_items"][0]["stalled_bucket"] == "awaiting_maintainer_reply"
    assert included["result_hash"] != excluded["result_hash"]

    if commit_oid is None:
        del commit["oid"]
    else:
        commit["oid"] = commit_oid
    commit.update(nested_clock)
    node.update(direct_clocks, isDraft=draft)
    snapshot = observe_github_repository_prs("org/repo", runner=runner)
    item = snapshot["items"][0]
    assert item["checks_summary"]["total"] == 0
    assert len(reads) == 2

    if decision == "hold":
        assert item.get("stalled_since_unknown") is True
        assert item["stalled_since"] is None
        assert item["author_replied_after_request_changes"] is False
        for prior in (included, excluded):
            observation = observe(snapshot, prior["result_hash"])
            assert observation["observation_incomplete"] is True
            assert observation["result_hash"] == prior["result_hash"]
            assert observation["material_change"] is False
    else:
        observation = observe(snapshot, included["result_hash"])
        assert observation["observation_incomplete"] is False
        expected = included if decision == "include" else excluded
        assert observation["result_hash"] == expected["result_hash"]


def test_observe_github_repository_prs_enriches_signal_summary(monkeypatch) -> None:
    head_99 = "c" * 40
    head_101 = "1" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    pr_99 = _graphql_node(
        _sample_pr(
            number=99,
            head_oid=head_99,
            reviews=[
                {
                    "id": "PRR_99",
                    "author": {"login": "loopx-agent"},
                    "state": "COMMENTED",
                    "commit": {"oid": head_99},
                    "submittedAt": "2030-01-01T08:30:00Z",
                    "body": _valid_bilingual_body(
                        head_oid=head_99, verdict="REQUEST_CHANGES", reviewer=gpt_decl, author_owned=True
                    ),
                }
            ],
        )
    )
    pr_101 = _graphql_node(
        _sample_pr(number=101, head_oid=head_101),
        contexts=[
            {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "StatusContext", "context": "gate", "state": "FAILURE"},
        ],
    )
    seen_cursors: list[str | None] = []
    history = []

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["gh", "api", "graphql"]
        cursor_args = [arg for arg in cmd if arg.startswith("cursor=")]
        cursor = cursor_args[0].split("=", 1)[1] if cursor_args else None
        seen_cursors.append(cursor)
        if cursor is None:
            body = _graphql_page([pr_101], has_next_page=True, end_cursor="cursor-page-2", total_count=2, history=history)
        else:
            assert cursor == "cursor-page-2"
            # Include duplicate PR 101 across page shift + new PR 99 to test deduplication & sorting.
            body = _graphql_page([pr_101, pr_99], has_next_page=False, end_cursor="cursor-end", history=history)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
    observed = _observe_github_repository_prs("org/repo")
    assert seen_cursors == [None, "cursor-page-2"]
    assert observed["status"] == "ok" and [item["number"] for item in observed["items"]] == [99, 101]
    assert observed["items"][0]["review_summary"]["effective_verdict"] == "REQUEST_CHANGES"
    assert observed["items"][1]["checks_summary"] == {
        "total": 2,
        "pass_count": 1,
        "fail_count": 1,
        "pending_count": 0,
    }


def test_observe_github_repository_prs_fails_closed_on_page_error(monkeypatch) -> None:
    calls = 0

    def fake_run(cmd, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            body = _graphql_page([_graphql_node(_sample_pr(number=10))], has_next_page=True, end_cursor="p2")
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="502 Bad Gateway")

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
    observed = _observe_github_repository_prs("org/repo")
    assert observed == {"status": "unavailable", "repository": "org/repo"}
    assert calls == 2

    def graphql_errors_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"errors": [{"message": "timeout"}]}), stderr="")

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", graphql_errors_run)
    assert _observe_github_repository_prs("org/repo") == {"status": "unavailable", "repository": "org/repo"}


@pytest.mark.parametrize("terminal_page", [True, False])
def test_observe_github_repository_prs_requires_completion_at_page_cap(monkeypatch, terminal_page) -> None:
    requested_cursors = []
    history = []

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["gh", "api", "graphql"]
        assert kwargs["timeout"] == 20
        requested_cursors.append(next((arg for arg in cmd if arg.startswith("cursor=")), None))
        page_number = len(requested_cursors)
        assert page_number <= 5
        body = _graphql_page(
            [_graphql_node(_sample_pr(number=page_number))],
            has_next_page=page_number < 5 or not terminal_page,
            end_cursor=f"page-{page_number}",
            total_count=5 if terminal_page else 6, history=history,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
    observed = _observe_github_repository_prs("org/repo", backfill_comments=True)

    assert requested_cursors == [None, "cursor=page-1", "cursor=page-2", "cursor=page-3", "cursor=page-4"]
    if terminal_page:
        assert observed["status"] == "ok"
        assert [item["number"] for item in observed["items"]] == [1, 2, 3, 4, 5]
    else:
        assert observed == {"status": "unavailable", "repository": "org/repo"}


@pytest.mark.parametrize("page_info", [None, {}, {"hasNextPage": "false"}, {"hasNextPage": True, "endCursor": 3}])
def test_observe_github_repository_prs_rejects_unverified_page_info(monkeypatch, page_info) -> None:
    def fake_run(cmd, **_kwargs):
        assert not any(arg.startswith("cursor=") for arg in cmd)
        body = _graphql_page([_graphql_node(_sample_pr())])
        body["data"]["repository"]["pullRequests"]["pageInfo"] = page_info
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)

    assert _observe_github_repository_prs("org/repo") == {"status": "unavailable", "repository": "org/repo"}


def test_observe_github_repository_prs_rejects_a_cursor_cycle(monkeypatch) -> None:
    remaining_cursors = iter(["cursor-a", "cursor-b", "cursor-a"])
    history = []

    def fake_run(cmd, **_kwargs):
        cursor = next(remaining_cursors)
        body = _graphql_page([_graphql_node(_sample_pr())], has_next_page=True, end_cursor=cursor, history=history)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)

    assert _observe_github_repository_prs("org/repo") == {"status": "unavailable", "repository": "org/repo"}


@pytest.mark.parametrize(
    "invalid_node",
    [
        pytest.param(None, id="null-node"),
        pytest.param("PR-42", id="non-object"),
        pytest.param({}, id="missing-number"),
        pytest.param({"number": None}, id="null-number"),
        pytest.param({"number": 0}, id="zero-number"),
        pytest.param({"number": -2}, id="negative-number"),
        pytest.param({"number": True}, id="boolean-number"),
        pytest.param({"number": 42.5}, id="fractional-number"),
        pytest.param({"number": "42"}, id="string-number"),
    ],
)
def test_observe_github_repository_prs_discards_partial_list_if_a_node_is_invalid(
    monkeypatch, invalid_node,
) -> None:
    first = _graphql_page(
        [_graphql_node(_sample_pr(number=1))], has_next_page=True, end_cursor="p2", total_count=2
    )
    terminal = _graphql_page([_graphql_node(_sample_pr(number=2))], total_count=2)
    terminal["data"]["repository"]["pullRequests"]["nodes"].append(invalid_node)

    def fake_run(cmd, **_kwargs):
        payload = terminal if "cursor=p2" in cmd else first
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)

    assert _observe_github_repository_prs("org/repo") == {"status": "unavailable", "repository": "org/repo"}


def test_observe_github_repository_prs_accepts_a_verified_empty_list(monkeypatch) -> None:
    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([])))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)

    assert _observe_github_repository_prs("org/repo") == {"status": "ok", "repository": "org/repo", "items": []}


@pytest.mark.parametrize(
    ("first_count", "last_count", "last_numbers", "expected_numbers"),
    [
        pytest.param(3, 3, [2, 3], [1, 2, 3], id="complete-with-overlap"),
        pytest.param(3, 3, [2], None, id="overlap-hides-a-missing-pr"),
        pytest.param(3, 2, [2], None, id="terminal-count-matches-after-drift"),
        pytest.param(2, 3, [2, 3], None, id="terminal-count-matches-after-growth"),
        pytest.param(2, 2, [2, 3], None, id="more-distinct-prs-than-reported"),
    ],
)
def test_observe_github_repository_prs_requires_stable_distinct_coverage(
    monkeypatch, first_count, last_count, last_numbers, expected_numbers,
) -> None:
    history = []
    first = _graphql_page(
        [_graphql_node(_sample_pr(number=1)), _graphql_node(_sample_pr(number=2))],
        has_next_page=True, end_cursor="p2", total_count=first_count, history=history,
    )
    terminal = _graphql_page(
        [_graphql_node(_sample_pr(number=number)) for number in last_numbers], total_count=last_count, history=history,
    )

    def fake_run(cmd, **_kwargs):
        payload = terminal if "cursor=p2" in cmd else first
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
    observed = _observe_github_repository_prs("org/repo")

    if expected_numbers is None:
        assert observed == {"status": "unavailable", "repository": "org/repo"}
    else:
        assert observed["status"] == "ok"
        assert [item["number"] for item in observed["items"]] == expected_numbers


@pytest.mark.parametrize("drift", [
    "number", "order", "headRefOid", "updatedAt", "reviewDecision", "cursor", "missing", "unchanged",
])
def test_steward_list_revalidates_all_consumed_windows_before_publishing(drift: str) -> None:
    requests = []
    history = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        cursor = next((arg.removeprefix("cursor=") for arg in cmd if arg.startswith("cursor=")), None)
        requests.append(cursor)
        first = (len(requests) - 1) * 20 + 1
        last = min(first + 20, 42)
        rows = [_graphql_node(_sample_pr(number=number)) for number in range(first, last)]
        payload = _graphql_page(rows, has_next_page=last < 42, end_cursor=f"end-{last - 1}",
                                total_count=41, history=history)
        if cursor == "end-40" and drift != "unchanged":
            repository = payload["data"]["repository"]
            original = repository["previousPrs0"]
            readback = {**original, "nodes": [dict(row) for row in original["nodes"]]}
            value_at_drift = {
                "number": 42, "headRefOid": "b" * 40, "updatedAt": "2030-01-01T10:00:00Z",
                "reviewDecision": "APPROVED",
            }
            if drift in value_at_drift:
                readback["nodes"][0][drift] = value_at_drift[drift]
            elif drift == "order":
                readback["nodes"].reverse()
            elif drift == "cursor":
                readback["pageInfo"] = {**original["pageInfo"], "endCursor": "another-window"}
            if drift == "missing":
                del repository["previousPrs0"]
            else:
                repository["previousPrs0"] = readback
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner, backfill_comments=True)
    assert requests == [None, "end-20", "end-40"]
    if drift == "unchanged":
        assert snapshot["status"] == "ok"
        assert [row["number"] for row in snapshot["items"]] == list(range(1, 42))
    else:
        assert snapshot == {"status": "unavailable", "repository": "org/repo"}
        observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            "repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior",
            "signal_scope": {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
                             "include_inbox": False, "pr_filter": {"exclude_drafts": True}},
        })
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:verified-prior"
        assert observation["material_change"] is False


@pytest.mark.parametrize("case", [
    "changed", "cleared-failure", "missing", "foreign", "unclassified", "truncated", "excluded", "trusted-direct",
    "fresh-overlap", "same-tally",
])
def test_steward_terminal_list_readback_corroborates_earlier_head_checks(case: str) -> None:
    from copy import deepcopy

    success = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
    pending = {"__typename": "StatusContext", "state": "PENDING"}
    failure = {**success, "conclusion": "FAILURE"}
    first_page = [_graphql_node(_sample_pr(number=number), contexts=[success]) for number in range(1, 21)]
    second_page = [_graphql_node(_sample_pr(number=number), contexts=[success, pending]) for number in range(21, 41)]
    terminal = [_graphql_node(_sample_pr(number=41), contexts=[failure])]
    old_pr = first_page[0]
    if case == "excluded":
        old_pr["isDraft"] = True
    elif case == "cleared-failure":
        first_page[0] = _graphql_node(_sample_pr(number=1), contexts=[failure])
    elif case == "trusted-direct":
        old_pr["statusCheckRollup"] = [success]
    elif case == "fresh-overlap":
        terminal.append(_graphql_node(_sample_pr(number=1), contexts=[success]))
    history = []
    reads = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        cursor = next((part.removeprefix("cursor=") for part in cmd if part.startswith("cursor=")), None)
        reads.append(cursor)
        query = next(part for part in cmd if part.startswith("query="))
        if cursor is None:
            payload = _graphql_page(first_page, has_next_page=True, end_cursor="first", total_count=41, history=history)
        elif cursor == "first":
            assert "commits(last: 1)" not in query.split("previousPrs0:", 1)[1]
            payload = _graphql_page(second_page, has_next_page=True, end_cursor="second", total_count=41, history=history)
        else:
            assert cursor == "second"
            assert "contexts(first: 1)" in query.split("previousPrs0:", 1)[1].split("previousPrs1:", 1)[0]
            assert "contexts(first: 2)" in query.split("previousPrs1:", 1)[1]
            payload = _graphql_page(terminal, total_count=41, history=history)
            repository = payload["data"]["repository"]
            readback = deepcopy(repository["previousPrs0"])
            row = readback["nodes"][0]
            commit = row["commits"]["nodes"][0]["commit"]
            contexts = commit["statusCheckRollup"]["contexts"]
            if case in {"missing", "trusted-direct"}:
                del row["commits"]
                row.pop("statusCheckRollup", None)
            elif case == "foreign":
                commit["oid"] = "b" * 40
            elif case == "unclassified":
                contexts["nodes"][0]["conclusion"] = None
            elif case == "truncated":
                contexts["totalCount"] = 2
                contexts["pageInfo"]["hasNextPage"] = True
            elif case == "same-tally":
                contexts["nodes"][0]["id"] = "replacement-check"
                contexts["nodes"][0]["conclusion"] = "NEUTRAL"
            elif case == "cleared-failure":
                contexts["nodes"][0]["conclusion"] = "SUCCESS"
            else:
                contexts["nodes"][0]["conclusion"] = "FAILURE"
            repository["previousPrs0"] = readback
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner)
    assert snapshot["status"] == "ok" and reads == [None, "first", "second"]
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior",
        "signal_scope": {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
                         "include_inbox": False, "pr_filter": {"exclude_drafts": True}},
    })
    if case in {"changed", "cleared-failure", "missing", "foreign", "unclassified", "truncated"}:
        assert snapshot["items"][0]["checks_coverage_incomplete"] is True
        assert "FAILING_CHECKS" not in snapshot["items"][0]["bypassed_gates"]
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:verified-prior"
        assert observation["material_change"] is False
    else:
        assert observation["observation_incomplete"] is False
        numbers = [row["number"] for row in observation["scoped_repository_items"]]
        assert (1 in numbers) is (case != "excluded")
        failures = [row["number"] for row in observation["scoped_repository_items"] if "FAILING_CHECKS" in row["bypassed_gates"]]
        assert failures == [41]


@pytest.mark.parametrize("case", [
    "body-drift", "missing", "count-drift", "excluded", "fresh-overlap", "stable",
    "stable-truncated", "changed-truncated",
])
def test_steward_terminal_list_readback_corroborates_earlier_reviews(case: str) -> None:
    from copy import deepcopy

    head = "a" * 40
    gpt = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    gemini = "model_agent · gemini-3.1-pro · Google (runtime_reported, high)"

    def approval(identifier: str, declaration: str) -> dict[str, Any]:
        return {
            "id": identifier, "state": "APPROVED", "author": {"login": "reviewer"},
            "commit": {"oid": head}, "submittedAt": "2030-01-01T08:00:00Z",
            "body": _valid_bilingual_body(head_oid=head, verdict="APPROVE", reviewer=declaration),
        }

    def reviewed_pr(number: int, reviews: list[dict[str, Any]]) -> dict[str, Any]:
        return _graphql_node(_sample_pr(number=number, head_oid=head, reviews=reviews, review_decision="APPROVED"))

    earlier = reviewed_pr(1, [approval("newest", gpt)])
    if case.endswith("-truncated"):
        earlier["reviews"]["totalCount"] = 2
        earlier["reviews"]["pageInfo"] = {"hasPreviousPage": True, "startCursor": "review-cursor"}
    if case == "excluded":
        earlier["isDraft"] = True
    first_page = [earlier, *(_graphql_node(_sample_pr(number=number)) for number in range(2, 21))]
    second_page = [reviewed_pr(21, [approval("r1", gemini), approval("r2", gemini)]),
                   *(_graphql_node(_sample_pr(number=number)) for number in range(22, 41))]
    terminal = [reviewed_pr(41, [approval("tail", gpt)])]
    if case == "fresh-overlap":
        terminal.append(reviewed_pr(1, [approval("newest", gemini)]))
    history = []
    list_reads = []
    review_reads = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        if "before=review-cursor" in cmd:
            review_reads.append("review-cursor")
            pr_data = {key: earlier[key] for key in ("number", "headRefOid", "updatedAt", "reviewDecision")}
            pr_data["latestReviews"] = earlier["reviews"]
            pr_data["reviews"] = _graphql_reviews([approval("older", gemini)], totalCount=2)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"data": {"repository": {"pullRequest": pr_data}}}))
        cursor = next((part.removeprefix("cursor=") for part in cmd if part.startswith("cursor=")), None)
        list_reads.append(cursor)
        query = next(part for part in cmd if part.startswith("query="))
        if cursor is None:
            payload = _graphql_page(first_page, has_next_page=True, end_cursor="first", total_count=41, history=history)
        elif cursor == "first":
            assert "reviews(last:" not in query.split("previousPrs0:", 1)[1]
            payload = _graphql_page(second_page, has_next_page=True, end_cursor="second", total_count=41, history=history)
        else:
            assert cursor == "second"
            assert "reviews(last: 1)" in query.split("previousPrs0:", 1)[1].split("previousPrs1:", 1)[0]
            assert "reviews(last: 2)" in query.split("previousPrs1:", 1)[1]
            payload = _graphql_page(terminal, total_count=41, history=history)
            repository = payload["data"]["repository"]
            readback = deepcopy(repository["previousPrs0"])
            row = readback["nodes"][0]
            if case == "missing":
                del row["reviews"]
            elif case == "count-drift":
                row["reviews"]["totalCount"] += 1
            elif case not in {"stable", "stable-truncated"}:
                row["reviews"]["nodes"][0]["body"] = approval("newest", gemini)["body"]
            repository["previousPrs0"] = readback
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs(
        "org/repo", runner=runner, select_review_backfill=lambda candidates, _: {row["number"] for row in candidates},
    )
    assert snapshot["status"] == "ok" and list_reads == [None, "first", "second"]
    assert review_reads == (["review-cursor"] if case == "stable-truncated" else [])
    scope = {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
             "pr_filter": {"required_approver_declarations": ["gpt"], "exclude_drafts": True}}
    params = {"repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior", "signal_scope": scope}
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", params)
    if case in {"body-drift", "missing", "count-drift", "changed-truncated"}:
        assert snapshot["items"][0]["review_readback_incomplete"] is True
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:verified-prior"
        assert observation["material_change"] is False
        unscoped = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            **params, "signal_scope": None,
        })
        assert unscoped["observation_incomplete"] is True and unscoped["result_hash"] == "sha256:verified-prior"
    else:
        assert observation["observation_incomplete"] is False
        numbers = [row["number"] for row in observation["scoped_repository_items"]]
        assert numbers == ([41] if case in {"excluded", "fresh-overlap"} else [1, 41])
        assert snapshot["items"][0].get("review_coverage_incomplete") is (True if case == "excluded" else None)


@pytest.mark.parametrize("case", [
    "actor-drift", "missing", "boundary-drift", "excluded-draft", "young-without-bypass", "fresh-overlap",
    "stable", "stable-truncated", "changed-truncated", "no-anchor",
])
def test_steward_terminal_list_readback_corroborates_earlier_reply_evidence(case: str) -> None:
    from copy import deepcopy

    head = "a" * 40
    review = {
        "id": "change-request", "state": "CHANGES_REQUESTED", "author": {"login": "maintainer"},
        "commit": {"oid": head}, "submittedAt": "2030-01-01T08:00:00Z",
    }
    reply = {"createdAt": "2030-01-01T09:00:00Z", "author": {"login": "contributor"}}

    def pr(number: int, *, reviews: list[dict[str, Any]], comments: list[dict[str, Any]]) -> dict[str, Any]:
        row = _graphql_node(_sample_pr(number=number, head_oid=head, author="contributor", reviews=reviews,
                                       review_decision="CHANGES_REQUESTED"))
        row["comments"] = {"nodes": comments, "pageInfo": {"hasPreviousPage": False}}
        row["updatedAt"] = "2030-01-01T10:00:00Z"
        return row

    earlier = pr(1, reviews=[] if case == "no-anchor" else [review], comments=[reply])
    if case.endswith("-truncated"):
        earlier["comments"] = {"nodes": [], "pageInfo": {"hasPreviousPage": True, "startCursor": "comment-cursor"}}
    if case == "excluded-draft":
        earlier["isDraft"] = True
    first_page = [earlier, *(pr(number, reviews=[], comments=[]) for number in range(2, 21))]
    second_page = [pr(21, reviews=[], comments=[reply, reply]), *(pr(number, reviews=[], comments=[]) for number in range(22, 41))]
    terminal = [pr(41, reviews=[{**review, "submittedAt": "2029-12-30T08:00:00Z"}], comments=[reply])]
    if case == "fresh-overlap":
        terminal.append(pr(1, reviews=[review], comments=[]))
    history = []
    list_reads = []
    comment_reads = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        if "before=comment-cursor" in cmd:
            comment_reads.append("comment-cursor")
            pr_data = {key: earlier[key] for key in ("number", "headRefOid", "updatedAt", "reviewDecision")}
            pr_data["latestComments"] = earlier["comments"]
            pr_data["comments"] = {"nodes": [reply], "pageInfo": {"hasPreviousPage": False}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"data": {"repository": {"pullRequest": pr_data}}}))
        cursor = next((part.removeprefix("cursor=") for part in cmd if part.startswith("cursor=")), None)
        list_reads.append(cursor)
        query = next(part for part in cmd if part.startswith("query="))
        if cursor is None:
            payload = _graphql_page(first_page, has_next_page=True, end_cursor="first", total_count=41, history=history)
        elif cursor == "first":
            assert "comments(last:" not in query.split("previousPrs0:", 1)[1]
            payload = _graphql_page(second_page, has_next_page=True, end_cursor="second", total_count=41, history=history)
        else:
            assert cursor == "second"
            assert "comments(last: 1)" in query.split("previousPrs0:", 1)[1].split("previousPrs1:", 1)[0]
            assert "comments(last: 2)" in query.split("previousPrs1:", 1)[1]
            payload = _graphql_page(terminal, total_count=41, history=history)
            repository = payload["data"]["repository"]
            readback = deepcopy(repository["previousPrs0"])
            row = readback["nodes"][0]
            if case == "missing":
                del row["comments"]
            elif case in {"boundary-drift", "changed-truncated"}:
                row["comments"]["pageInfo"] = {"hasPreviousPage": True, "startCursor": "different-cursor"}
            elif case not in {"stable", "stable-truncated"}:
                row["comments"]["nodes"][0]["author"] = {"login": "observer"}
            repository["previousPrs0"] = readback
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner, backfill_comments=True)
    assert snapshot["status"] == "ok" and list_reads == [None, "first", "second"]
    assert comment_reads == (["comment-cursor"] if case == "stable-truncated" else [])
    author_mode = "include" if case == "young-without-bypass" else "exclude"
    scope = {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
             "pr_filter": {"min_stalled_hours": 24, "exclude_drafts": True, "author_mode": author_mode,
                           "authors": ["contributor" if author_mode == "include" else "maintainer"]}}
    params = {"repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior", "signal_scope": scope,
              "observed_at": "2030-01-01T12:00:00Z"}
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", params)
    if case in {"actor-drift", "missing", "boundary-drift", "changed-truncated"}:
        assert snapshot["items"][0]["comments_coverage_incomplete"] is True
        assert snapshot["items"][0]["author_replied_after_request_changes"] is False
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:verified-prior" and observation["material_change"] is False
        unscoped = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            **params, "signal_scope": None,
        })
        assert unscoped["observation_incomplete"] is False
    else:
        assert observation["observation_incomplete"] is False
        numbers = [row["number"] for row in observation["scoped_repository_items"]]
        assert numbers == ([1, 41] if case in {"stable", "stable-truncated"} else [41])


@pytest.mark.parametrize("case", [
    "mergeability-drift", "draft-drift", "author-drift", "unknown-mergeability", "missing-mergeability",
    "missing-draft", "unreadable-author", "unknown-excluded-by-draft", "missing-draft-excluded-by-conflict",
    "unknown-excluded-by-review", "author-changes-review", "unreadable-review-author",
    "unreadable-review-author-excluded-by-draft",
])
def test_steward_uses_terminal_scope_fields_and_holds_only_on_decisive_unknowns(case: str) -> None:
    from copy import deepcopy

    checks_conflicts = case in {
        "mergeability-drift", "unknown-mergeability", "missing-mergeability",
        "unknown-excluded-by-draft", "missing-draft-excluded-by-conflict", "unknown-excluded-by-review",
    }
    checks_drafts = case in {
        "draft-drift", "missing-draft", "unknown-excluded-by-draft", "missing-draft-excluded-by-conflict",
        "unreadable-review-author-excluded-by-draft",
    }
    checks_author = case in {"author-drift", "unreadable-author"}
    checks_review_author = case in {
        "author-changes-review", "unreadable-review-author", "unreadable-review-author-excluded-by-draft",
    }
    scope_filter = {"exclude_conflicting": checks_conflicts, "exclude_drafts": checks_drafts}
    if checks_author:
        scope_filter.update(author_mode="include", authors=["selected"])
    if checks_review_author:
        scope_filter["effective_verdicts"] = ["APPROVE"]

    def pr(number: int) -> dict[str, Any]:
        eligible = number in {1, 21}
        node = _graphql_node(_sample_pr(
            number=number, mergeable="MERGEABLE" if eligible else "CONFLICTING",
            author="selected" if eligible else "outside",
        ))
        node["isDraft"] = not eligible
        if (eligible and checks_review_author) or (number == 1 and case == "unknown-excluded-by-review"):
            review = {"id": "approval", "state": "APPROVED", "author": {"login": "reviewer"},
                      "commit": {"oid": node["headRefOid"]}, "submittedAt": "2030-01-01T10:00:00Z"}
            if case == "unknown-excluded-by-review":
                review["body"] = _valid_bilingual_body(
                    head_oid=node["headRefOid"], verdict="REQUEST_CHANGES", reviewer="model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)",
                )
            node["reviews"] = _graphql_reviews([review])
        return node

    first = [pr(number) for number in range(1, 21)]
    readback = deepcopy(first)
    field_values = {
        "mergeability-drift": {"mergeable": "CONFLICTING"},
        "draft-drift": {"isDraft": True},
        "author-drift": {"author": {"login": "outside"}},
        "unknown-mergeability": {"mergeable": "UNKNOWN"},
        "unreadable-author": {"author": "selected"},
        "unknown-excluded-by-draft": {"mergeable": "UNKNOWN", "isDraft": True},
        "missing-draft-excluded-by-conflict": {"mergeable": "CONFLICTING"},
        "unknown-excluded-by-review": {"mergeable": "UNKNOWN"},
        "author-changes-review": {"author": {"login": "reviewer"}},
        "unreadable-review-author": {"author": None},
        "unreadable-review-author-excluded-by-draft": {"author": None, "isDraft": True},
    }
    readback[0].update(field_values.get(case, {}))
    if case == "missing-mergeability":
        del readback[0]["mergeable"]
    if case in {"missing-draft", "missing-draft-excluded-by-conflict"}:
        del readback[0]["isDraft"]
    reads = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        tail = "cursor=tail" in cmd
        reads.append(tail)
        if tail:
            query = next(part for part in cmd if part.startswith("query="))
            alias = query.split("previousPrs0:", 1)[1].split("fragment StewardPrWindowRow", 1)[0]
            assert "...StewardPrTerminalFields" in alias
            payload = _graphql_page([pr(21)], total_count=21)
            prior_window = _graphql_page(readback, has_next_page=True, end_cursor="tail", total_count=21)
            payload["data"]["repository"]["previousPrs0"] = prior_window["data"]["repository"]["pullRequests"]
        else:
            payload = _graphql_page(first, has_next_page=True, end_cursor="tail", total_count=21)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner)
    assert snapshot["status"] == "ok" and reads == [False, True]
    assert snapshot["items"][0]["isDraft"] == readback[0].get("isDraft")
    assert snapshot["items"][0]["mergeable"] == readback[0].get("mergeable")
    assert snapshot["items"][0]["author"] == (None if case == "unreadable-author" else readback[0].get("author"))
    params = {"repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior",
              "signal_scope": {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
                               "include_inbox": False, "pr_filter": scope_filter}}
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", params)
    if case in {"unknown-mergeability", "missing-mergeability", "missing-draft", "unreadable-author", "unreadable-review-author"}:
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:verified-prior" and observation["material_change"] is False
        assert ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            **params, "signal_scope": None,
        })["observation_incomplete"] is False
    else:
        assert observation["observation_incomplete"] is False
        assert [row["number"] for row in observation["scoped_repository_items"]] == [21]


@pytest.mark.parametrize(
    ("earlier", "terminal", "excluded", "expected_gate"),
    [
        pytest.param("BLOCKED", "CLEAN", False, None, id="cleared"),
        pytest.param("CLEAN", "BLOCKED", False, "BLOCKED_MERGE_STATE", id="new-block"),
        pytest.param("BLOCKED", "UNSTABLE", False, "UNSTABLE_MERGE_STATE", id="unstable"),
        pytest.param("BLOCKED", "BEHIND", False, None, id="known-behind"),
        pytest.param("BLOCKED", None, False, None, id="missing"),
        pytest.param("BLOCKED", "UNKNOWN", False, None, id="unknown"),
        pytest.param("BLOCKED", {"state": "CLEAN"}, False, None, id="malformed"),
        pytest.param("BLOCKED", "FUTURE", False, None, id="unsupported"),
        pytest.param("BLOCKED", "UNKNOWN", True, None, id="excluded-draft"),
    ],
)
def test_steward_publishes_merge_gates_from_the_terminal_readback(
    earlier: str, terminal: object, excluded: bool, expected_gate: str | None,
) -> None:
    from copy import deepcopy

    checks = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]
    first = [_graphql_node(_sample_pr(number=1, merge_state=earlier), contexts=checks)]
    first += [_graphql_node(_sample_pr(number=number, merge_state="CLEAN")) for number in range(2, 21)]
    last = _graphql_node(_sample_pr(number=21, merge_state="BLOCKED"))
    readback = deepcopy(first)
    if terminal is None:
        del readback[0]["mergeStateStatus"]
    else:
        readback[0]["mergeStateStatus"] = terminal
    readback[0]["isDraft"] = excluded
    reads = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        tail = "cursor=tail" in cmd
        reads.append(tail)
        if tail:
            query = next(part for part in cmd if part.startswith("query="))
            assert "...StewardPrTerminalFields" in query.split("previousPrs0:", 1)[1].split("fragment ", 1)[0]
            assert "mergeStateStatus" in query.split("fragment StewardPrTerminalFields", 1)[1].split("}", 1)[0]
            payload = _graphql_page([last], total_count=21)
            window = _graphql_page(readback, has_next_page=True, end_cursor="tail", total_count=21)
            payload["data"]["repository"]["previousPrs0"] = window["data"]["repository"]["pullRequests"]
        else:
            payload = _graphql_page(first, has_next_page=True, end_cursor="tail", total_count=21)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner)
    assert snapshot["status"] == "ok" and reads == [False, True]
    first_item = snapshot["items"][0]
    assert first_item["mergeStateStatus"] == terminal
    expected_gates = {"REVIEW_REQUIRED", "FAILING_CHECKS"}
    if expected_gate is not None:
        expected_gates.add(expected_gate)
    assert set(first_item["bypassed_gates"]) == expected_gates
    params = {"repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior",
              "signal_scope": {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
                               "include_inbox": False, "pr_filter": {"exclude_drafts": True}}}
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", params)
    if not excluded and (not isinstance(terminal, str) or terminal in {"UNKNOWN", "FUTURE"}):
        assert first_item["merge_state_coverage_incomplete"] is True
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:verified-prior" and observation["material_change"] is False
    else:
        assert observation["observation_incomplete"] is False
        items = observation["scoped_repository_items"]
        assert (1 in {item["number"] for item in items}) is not excluded
        assert [item["number"] for item in items if "BLOCKED_MERGE_STATE" in item["bypassed_gates"]] == (
            [1, 21] if expected_gate == "BLOCKED_MERGE_STATE" else [21]
        )


def test_steward_unknown_native_merge_state_preserves_prior_without_reinterpreting_legacy_signals() -> None:
    row = _sample_pr(merge_state="UNKNOWN")

    def runner(cmd, **_kwargs):
        payload = _graphql_page([_graphql_node(row)])
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    native = observe_github_repository_prs("org/repo", runner=runner)
    previous_hash = "sha256:verified-prior"
    native_result = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": native, "previous_hash": previous_hash,
    })
    assert native["items"][0]["merge_state_coverage_incomplete"] is True
    assert native_result["observation_incomplete"] is True
    assert native_result["result_hash"] == previous_hash and native_result["material_change"] is False

    legacy = summarize_github_pr_signal(row)
    assert "merge_state_coverage_incomplete" not in legacy
    legacy_result = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": {"status": "ok", "items": [legacy]}, "previous_hash": previous_hash,
    })
    assert legacy_result["observation_incomplete"] is False


@pytest.mark.parametrize(
    ("field", "terminal", "excluded"),
    [
        pytest.param("title", "Retitled pull request", False, id="title"),
        pytest.param("url", "https://github.com/org/renamed/pull/1", False, id="url"),
        pytest.param("headRefName", "topic-renamed", False, id="head-ref"),
        pytest.param("baseRefName", "release", False, id="base-ref"),
        pytest.param("title", None, False, id="missing-title"),
        pytest.param("headRefName", " \t", False, id="blank-head-ref"),
        pytest.param("url", {"value": "link"}, False, id="malformed-url"),
        pytest.param("baseRefName", None, True, id="missing-base-ref-excluded-draft"),
    ],
)
def test_steward_uses_terminal_display_metadata_before_publishing(field: str, terminal: object, excluded: bool) -> None:
    from copy import deepcopy

    first = [_graphql_node(_sample_pr(number=number)) for number in range(1, 21)]
    last = _graphql_node(_sample_pr(number=21))
    readback = deepcopy(first)
    if terminal is None:
        del readback[0][field]
    else:
        readback[0][field] = terminal
    readback[0]["isDraft"] = excluded
    reads = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        tail = "cursor=tail" in cmd
        reads.append(tail)
        if tail:
            query = next(part for part in cmd if part.startswith("query="))
            assert "...StewardPrTerminalFields" in query.split("previousPrs0:", 1)[1].split("fragment ", 1)[0]
            fragment = query.split("fragment StewardPrTerminalFields", 1)[1].split("\n}", 1)[0]
            assert all(name in fragment.split() for name in ("title", "url", "headRefName", "baseRefName"))
            payload = _graphql_page([last], total_count=21)
            window = _graphql_page(readback, has_next_page=True, end_cursor="tail", total_count=21)
            payload["data"]["repository"]["previousPrs0"] = window["data"]["repository"]["pullRequests"]
        else:
            payload = _graphql_page(first, has_next_page=True, end_cursor="tail", total_count=21)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner)
    assert snapshot["status"] == "ok" and reads == [False, True]
    first_item = snapshot["items"][0]
    assert first_item[field] == terminal
    incomplete = not isinstance(terminal, str) or not terminal.strip()
    assert first_item.get("pr_metadata_coverage_incomplete", False) is incomplete
    assert first_item["mergeStateStatus"] == "BLOCKED"
    params = {"repository_snapshot": snapshot, "previous_hash": "sha256:verified-prior",
              "signal_scope": {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
                               "include_inbox": False, "pr_filter": {"exclude_drafts": True}}}
    result = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", params)
    if incomplete and not excluded:
        assert result["observation_incomplete"] is True
        assert result["result_hash"] == "sha256:verified-prior" and result["material_change"] is False
    else:
        assert result["observation_incomplete"] is False
        items = result["scoped_repository_items"]
        assert [item["number"] for item in items] == list(range(2 if excluded else 1, 22))
        if not excluded:
            assert items[0][field] == terminal


def test_steward_unreadable_fresh_native_metadata_holds_without_reinterpreting_legacy_signals() -> None:
    row = _sample_pr()
    del row["title"]

    def runner(cmd, **_kwargs):
        payload = _graphql_page([_graphql_node(row)])
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    native = observe_github_repository_prs("org/repo", runner=runner)
    previous_hash = "sha256:verified-prior"
    native_result = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": native, "previous_hash": previous_hash,
    })
    assert native_result["observation_incomplete"] is True
    assert native_result["result_hash"] == previous_hash and native_result["material_change"] is False

    verified_repo = {"status": "ok", "items": [summarize_github_pr_signal(_sample_pr())]}
    portfolio_scope = {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": True,
                       "include_inbox": False, "pr_filter": {}}
    prior = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": verified_repo, "signal_scope": portfolio_scope,
        "portfolio_observation": {"result_hash": "portfolio-before"},
    })
    changed_portfolio = {"signal_scope": portfolio_scope, "previous_hash": prior["result_hash"],
                         "portfolio_observation": {"result_hash": "portfolio-after"}}
    expected = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        **changed_portfolio, "repository_snapshot": verified_repo,
    })
    independent = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        **changed_portfolio, "repository_snapshot": native, "previous_repository_snapshot": verified_repo,
    })
    assert independent["repository_observation_incomplete"] is True
    assert independent["material_change"] is True and independent["observation_incomplete"] is False
    assert independent["result_hash"] == expected["result_hash"]
    assert "scoped_repository_items" not in independent

    legacy = summarize_github_pr_signal(row)
    assert "pr_metadata_coverage_incomplete" not in legacy
    legacy_result = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": {"status": "ok", "items": [legacy]}, "previous_hash": previous_hash,
    })
    assert legacy_result["observation_incomplete"] is False


@pytest.mark.parametrize("invalid_count", [None, -1, True, "1", 1.0])
def test_observe_github_repository_prs_rejects_unverified_total_count(monkeypatch, invalid_count) -> None:
    def fake_run(cmd, **_kwargs):
        payload = _graphql_page([_graphql_node(_sample_pr())])
        connection = payload["data"]["repository"]["pullRequests"]
        if invalid_count is None:
            del connection["totalCount"]
        else:
            connection["totalCount"] = invalid_count
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)

    assert _observe_github_repository_prs("org/repo") == {"status": "unavailable", "repository": "org/repo"}


@pytest.mark.parametrize(
    ("total", "expected_sizes"),
    [
        (100, [20, 20, 20, 20, 20]),
        (101, [20, 25, 25, 25, 25]),
        (120, [20, 25, 25, 25, 25]),
        (121, [20]),
    ],
)
def test_observe_github_repository_prs_covers_overflow_within_five_pages(
    monkeypatch, total, expected_sizes,
) -> None:
    requests: list[int] = []
    read_count = 0
    history = []

    def fake_run(cmd, **kwargs):
        nonlocal read_count
        assert kwargs["timeout"] == 20
        size = int(next((arg.split("=", 1)[1] for arg in cmd if arg.startswith("pageSize=")), "20"))
        query = next(arg for arg in cmd if arg.startswith("query="))
        for index, previous_size in enumerate(requests):
            alias = query.split(f"previousPrs{index}: pullRequests(", 1)[1].split("nodes {", 1)[0]
            assert f"first: {previous_size}, after: $previousCursor{index}" in alias
            if index > 0:
                assert f"previousCursor{index}=cursor-{sum(requests[:index])}" in cmd
        requests.append(size)
        limit = min(read_count + size, total)
        nodes = [_graphql_node(_sample_pr(number=number)) for number in range(read_count + 1, limit + 1)]
        read_count = limit
        page = _graphql_page(
            nodes, has_next_page=limit < total, end_cursor=f"cursor-{limit}", total_count=total, history=history,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(page))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)

    observed = _observe_github_repository_prs("org/repo")
    if total <= 120:
        assert observed["status"] == "ok"
        assert [item["number"] for item in observed["items"]] == list(range(1, total + 1))
    else:
        assert observed == {"status": "unavailable", "repository": "org/repo"}
    assert requests == expected_sizes


def test_observe_github_repository_prs_caps_comment_backfill_across_overflow_scan(monkeypatch) -> None:
    head = "9" * 40
    review = {
        "id": "PRR_review",
        "state": "CHANGES_REQUESTED",
        "submittedAt": "2030-01-01T08:00:00Z",
        "author": {"login": "maintainer"},
        "commit": {"oid": head},
    }
    backfilled: list[str] = []
    read_count = 0
    initial_nodes = {}
    comment_pages = {}
    history = []

    def fake_run(cmd, **_kwargs):
        nonlocal read_count
        cursor = next((arg.split("=", 1)[1] for arg in cmd if arg.startswith("before=")), None)
        if cursor is not None:
            backfilled.append(next(arg for arg in cmd if arg.startswith("number=")))
            comments = {"pageInfo": {"hasPreviousPage": True, "startCursor": cursor + "-older"}, "nodes": []}
            number = int(backfilled[-1].removeprefix("number="))
            pr_data = _older_comment_row(initial_nodes[number], comments)
            pr_data.update(_comment_readback_rows(cmd, comment_pages.setdefault(number, {})))
            comment_pages[number][cursor] = comments
            payload = {"data": {"repository": {"pullRequest": pr_data}}}
        else:
            size = int(next((arg.split("=", 1)[1] for arg in cmd if arg.startswith("pageSize=")), "20"))
            limit = min(read_count + size, 101)
            nodes = []
            for number in range(read_count + 1, limit + 1):
                node = _graphql_node(_sample_pr(
                    number=number, head_oid=head, review_decision="CHANGES_REQUESTED", reviews=[review],
                ))
                node["comments"] = {"pageInfo": {"hasPreviousPage": True, "startCursor": "recent"}, "nodes": []}
                initial_nodes[number] = node
                nodes.append(node)
            read_count = limit
            payload = _graphql_page(
                nodes, has_next_page=limit < 101, end_cursor=f"cursor-{limit}", total_count=101, history=history,
            )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
    observed = _observe_github_repository_prs("org/repo", backfill_comments=True)

    assert observed["status"] == "ok"
    assert len(observed["items"]) == 101
    assert len(backfilled) == 300
    assert "number=101" not in backfilled
    assert observed["items"][-1]["comments_coverage_incomplete"] is True


@pytest.mark.parametrize("last_page_seconds", [20, 21])
def test_observe_github_repository_prs_rejects_late_terminal_page(monkeypatch, last_page_seconds) -> None:
    elapsed = 0
    requested_timeouts = []
    history = []
    monkeypatch.setattr("loopx.capabilities.native_chat.github_pr_evidence.monotonic", lambda: elapsed, raising=False)

    def fake_run(cmd, **kwargs):
        nonlocal elapsed
        requested_timeouts.append(kwargs["timeout"])
        page = len(requested_timeouts)
        elapsed += last_page_seconds if page == 5 else 20
        response = _graphql_page(
            [_graphql_node(_sample_pr(number=page))],
            has_next_page=page < 5, end_cursor=f"cursor-{page}", total_count=5, history=history,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(response))

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
    observed = _observe_github_repository_prs("org/repo")

    assert requested_timeouts == [20] * 5
    if last_page_seconds == 20:
        assert observed["status"] == "ok"
        assert [item["number"] for item in observed["items"]] == [1, 2, 3, 4, 5]
    else:
        assert observed == {"status": "unavailable", "repository": "org/repo"}


@pytest.mark.parametrize("comment_seconds", [5, 6])
def test_observe_github_repository_prs_shares_elapsed_budget_with_comment_reads(
    monkeypatch, comment_seconds,
) -> None:
    elapsed = 0
    list_pages = 0
    comment_timeouts = []
    initial_nodes = {}
    history = []
    head = "9" * 40
    review = {
        "id": "PRR_review", "state": "CHANGES_REQUESTED", "submittedAt": "2030-01-01T08:00:00Z",
        "author": {"login": "maintainer"}, "commit": {"oid": head},
    }
    monkeypatch.setattr("loopx.capabilities.native_chat.github_pr_evidence.monotonic", lambda: elapsed, raising=False)

    def fake_run(cmd, **kwargs):
        nonlocal elapsed, list_pages
        if any(arg.startswith("before=") for arg in cmd):
            comment_timeouts.append(kwargs["timeout"])
            elapsed += comment_seconds
            comments = {"pageInfo": {"hasPreviousPage": False, "startCursor": "older"}, "nodes": []}
            response = {"data": {"repository": {"pullRequest": _older_comment_row(initial_nodes[1], comments)}}}
        else:
            list_pages += 1
            elapsed += 19
            node = _graphql_node(_sample_pr(
                number=list_pages, head_oid=head, author=f"author-{list_pages}",
                reviews=[review] if list_pages <= 2 else [],
            ))
            node["comments"] = {"pageInfo": {"hasPreviousPage": True, "startCursor": "recent"}, "nodes": []}
            initial_nodes[list_pages] = node
            response = _graphql_page(
                [node], has_next_page=list_pages < 5, end_cursor=f"cursor-{list_pages}", total_count=5, history=history,
            )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(response))

    with monkeypatch.context() as provider:
        provider.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_run)
        observed = _observe_github_repository_prs("org/repo", backfill_comments=True)

    assert list_pages == 5
    assert comment_timeouts == [5]
    assert observed["status"] == "ok"
    assert [item["number"] for item in observed["items"]] == [1, 2, 3, 4, 5]
    assert observed["items"][0]["comments_coverage_incomplete"] is (comment_seconds > 5)
    assert observed["items"][1]["comments_coverage_incomplete"] is True

    observation = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "repository_snapshot": observed,
            "previous_hash": "sha256:prior-result",
            "observed_at": "2030-01-03T08:00:00Z",
            "signal_scope": {
                "schema_version": "steward_signal_monitor_scope_v0",
                "include_portfolio": False,
                "include_inbox": False,
                "pr_filter": {"author_mode": "include", "authors": ["author-1"], "min_stalled_hours": 24},
            },
        },
    )
    assert observation["observation_incomplete"] is (comment_seconds > 5)
    if comment_seconds > 5:
        assert observation["result_hash"] == "sha256:prior-result"
        assert observation["material_change"] is False


@pytest.mark.parametrize(
    ("case", "expected_reads", "covered"),
    [
        ("one_page", 1, True),
        ("multiple_pages", 2, True),
        ("nullable_decision", 1, True),
        ("decision_drift", 1, False),
        ("age_drift", 1, False),
        ("last_page_drift", 2, False),
        ("first_window_state_changed", 1, False),
        ("first_window_body_changed", 1, False),
        ("first_window_cursor_changed", 1, False),
        ("first_window_reordered", 1, False),
        ("missing_first_window", 1, False),
        ("last_page_window_changed", 2, False),
        ("consumed_page_drift", 2, False),
        ("missing_consumed_page", 2, False),
        ("first_consumed_page_drift_on_last_read", 3, False),
        ("missing_initial_timestamp", 0, False),
        ("missing_older_decision", 1, False),
        ("invalid_timestamp", 0, False),
        ("head_changed", 1, False),
        ("count_drift", 1, False),
        ("duplicate_row", 1, False),
        ("unreadable_row", 1, False),
        ("unknown_terminal", 1, False),
        ("repeated_cursor", 1, False),
        ("late_page", 1, False),
        ("author_excluded", 0, False),
        ("over_budget", 0, False),
        ("shared_budget", 1, True),
        ("per_pr_budget", 3, True),
    ],
)
def test_steward_recovers_older_reviews_within_the_scan_budget(
    monkeypatch, case: str, expected_reads: int, covered: bool,
) -> None:
    from loopx.capabilities.native_chat.steward_monitor_signals import _read_repository_snapshot

    head = "a" * 40
    observed_time = datetime(2030, 1, 3, 8, 0, tzinfo=timezone.utc)
    elapsed = 0.0
    monkeypatch.setattr("loopx.capabilities.native_chat.github_pr_evidence.monotonic", lambda: elapsed)
    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor_signals.now_utc", lambda: observed_time)
    if case == "shared_budget":
        monkeypatch.setattr("loopx.capabilities.native_chat.github_pr_evidence._GITHUB_PR_BACKFILL_BUDGET", 1)

    def review(review_id: str, state: str = "COMMENTED") -> dict[str, Any]:
        return {
            "id": review_id, "state": state, "commit": {"oid": head},
            "author": {"login": "maintainer" if state == "CHANGES_REQUESTED" else "visitor"},
            "submittedAt": "2030-01-01T08:00:00Z", "body": "",
        }

    newest = [review(f"new-{i}") for i in range(20)]
    if case in {"age_drift", "first_window_state_changed"}:
        newest[-1] = {**review("new-19", "CHANGES_REQUESTED"),
                      "author": {"login": "recent-reviewer"}, "submittedAt": "2030-01-03T07:00:00Z"}
    older_count = {"multiple_pages": 21, "last_page_drift": 21, "last_page_window_changed": 21, "repeated_cursor": 21,
                   "consumed_page_drift": 21, "missing_consumed_page": 21, "first_consumed_page_drift_on_last_read": 41,
                   "over_budget": 61, "per_pr_budget": 60}.get(case, 1)
    count = len(newest) + older_count
    older = [review(f"old-{i}") for i in range(older_count - 1)]
    older.append(review("active-rc", "DISMISSED" if case == "decision_drift" else "CHANGES_REQUESTED"))
    if case in {"consumed_page_drift", "first_consumed_page_drift_on_last_read"}:
        older[-2] = {**review("middle-rc", "CHANGES_REQUESTED"),
                     "author": {"login": "recent-reviewer"}, "submittedAt": "2030-01-03T07:00:00Z"}
    node = _graphql_node(_sample_pr(
        number=42, head_oid=head, author="contributor", review_decision="CHANGES_REQUESTED", reviews=newest,
    ))
    node["reviews"] = _graphql_reviews(
        newest, totalCount=count, pageInfo={"hasPreviousPage": True, "startCursor": "recent"},
    )
    node["updatedAt"] = "2030-01-03T07:30:00Z"
    if case in {"nullable_decision", "missing_older_decision"}:
        node["reviewDecision"] = None
    if case == "missing_initial_timestamp":
        del node["updatedAt"]
    elif case == "invalid_timestamp":
        node["updatedAt"] = "unreadable"
    comments_blocked = case in {"shared_budget", "per_pr_budget"}
    if comments_blocked:
        node["comments"]["pageInfo"] = {"hasPreviousPage": True, "startCursor": "comments"}
    scope = {
        "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
        "pr_filter": {
            "effective_verdicts": ["REQUEST_CHANGES"], "min_stalled_hours": 24,
            "author_mode": "include", "authors": ["other" if case == "author_excluded" else "contributor"],
        },
    }
    if case == "decision_drift":
        del scope["pr_filter"]["min_stalled_hours"]
    cursors = []
    prior_pages = []

    def runner(cmd, **kwargs):
        nonlocal elapsed
        cursor = next((arg.removeprefix("before=") for arg in cmd if arg.startswith("before=")), None)
        if cursor is None:
            payload = _graphql_page([node])
        else:
            review_cursors = ["recent", "earlier", "oldest"]
            assert cursor in review_cursors, "the fixture must not read comments"
            query = next(arg for arg in cmd if arg.startswith("query="))
            revision_fields = query.split("pullRequest(number: $number) {", 1)[1].split("reviews(", 1)[0]
            assert {"updatedAt", "reviewDecision"}.issubset(revision_fields.split())
            assert "latestReviews: reviews(last: 20) { ...StewardReviewPage }" in query
            assert "reviews(last: 20, before: $before) { ...StewardReviewPage }" in query
            assert query.count("reviews(last: 20") == len(cursors) + 2
            assert [arg for arg in cmd if arg.startswith("previousReviews")] == [
                f"previousReviews{index}={previous}" for index, previous in enumerate(cursors)
            ]
            for index in range(len(cursors)):
                alias = f"previousReviews{index}"
                assert f"${alias}: String!" in query
                assert f"{alias}: reviews(last: 20, before: ${alias}) {{ ...StewardReviewPage }}" in query
            assert kwargs["timeout"] == 20
            cursors.append(cursor)
            page_index = review_cursors.index(cursor)
            end = len(older) - 20 * page_index
            start = max(0, end - 20)
            page_nodes = older[start:end]
            page_info = {"hasPreviousPage": start > 0}
            if start > 0:
                page_info["startCursor"] = review_cursors[page_index + 1]
            if case == "duplicate_row":
                page_nodes = [newest[0]]
            elif case == "unreadable_row":
                page_nodes = [None]
            elif case == "unknown_terminal":
                page_info = {"hasPreviousPage": None}
            elif case == "repeated_cursor":
                page_info["startCursor"] = cursor
            elif case == "late_page":
                elapsed = 101
            pr_data = {
                "number": 42, "headRefOid": "b" * 40 if case == "head_changed" else head,
                "updatedAt": node.get("updatedAt"), "reviewDecision": node["reviewDecision"],
                "latestReviews": node["reviews"],
                "reviews": _graphql_reviews(page_nodes, totalCount=count + (case == "count_drift"), pageInfo=page_info),
            }
            if case == "decision_drift" or (case == "last_page_drift" and cursor == "earlier"):
                pr_data["reviewDecision"] = "REVIEW_REQUIRED"
            elif case == "age_drift":
                pr_data["updatedAt"] = observed_time.isoformat().replace("+00:00", "Z")
            elif case == "missing_older_decision":
                del pr_data["reviewDecision"]
            if case == "missing_first_window":
                del pr_data["latestReviews"]
            elif case == "first_window_state_changed":
                pr_data["latestReviews"] = {**node["reviews"], "nodes": [
                    *newest[:-1], {**newest[-1], "state": "DISMISSED"},
                ]}
            elif case == "first_window_body_changed" or (case == "last_page_window_changed" and cursor == "earlier"):
                pr_data["latestReviews"] = {**node["reviews"], "nodes": [
                    *newest[:-1], {**newest[-1], "body": "Review body changed"},
                ]}
            elif case == "first_window_cursor_changed":
                pr_data["latestReviews"] = {
                    **node["reviews"], "pageInfo": {"hasPreviousPage": True, "startCursor": "changed-boundary"},
                }
            elif case == "first_window_reordered":
                pr_data["latestReviews"] = {**node["reviews"], "nodes": newest[::-1]}
            for index, prior_page in enumerate(prior_pages):
                pr_data[f"previousReviews{index}"] = prior_page
            if case == "missing_consumed_page" and cursor == "earlier":
                del pr_data["previousReviews0"]
            if (case == "consumed_page_drift" and cursor == "earlier") or (
                case == "first_consumed_page_drift_on_last_read" and cursor == "oldest"
            ):
                pr_data["previousReviews0"] = {**prior_pages[0], "nodes": [
                    {**row, "state": "DISMISSED"} if row["id"] == "middle-rc" else row
                    for row in prior_pages[0]["nodes"]
                ]}
            prior_pages.append(pr_data["reviews"])
            payload = {"data": {"repository": {"pullRequest": pr_data}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    snapshot = _read_repository_snapshot(
        {"task_repository": "git:github.com/org/repo"}, scope,
        partial(observe_github_repository_prs, runner=runner), ChatConversationBindings._core,
    )
    assert snapshot is not None
    assert snapshot["status"] == "ok"
    assert len(cursors) == expected_reads
    item = snapshot["items"][0]
    assert item.get("review_coverage_incomplete") is (None if covered else True)
    assert len(item["review_summary"]["exact_head_reviews"]) == (count if covered else len(newest))
    observed = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot, "signal_scope": scope,
        "observed_at": observed_time.isoformat(), "previous_hash": "sha256:prior",
    })
    usable = (covered and not comments_blocked) or case == "author_excluded"
    assert observed["observation_incomplete"] is not usable
    if not usable:
        assert observed["result_hash"] == "sha256:prior"
        assert observed["material_change"] is False
    else:
        numbers = [row["number"] for row in observed["scoped_repository_items"]]
        assert numbers == ([] if case == "author_excluded" else [42])


@pytest.mark.parametrize("case", [
    "same_head_author", "same_head_maintainer", "changed_head", "missing_head",
    "wrong_number", "missing_number", "wrong_number_type", "draft",
])
def test_steward_requires_matching_pr_and_head_for_older_comments(monkeypatch, case: str) -> None:
    from loopx.capabilities.native_chat.steward_monitor_signals import _read_repository_snapshot

    head = "a" * 40
    observed_at = datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor_signals.now_utc", lambda: observed_at)
    review = {
        "id": "review", "state": "CHANGES_REQUESTED", "submittedAt": "2030-01-01T08:00:00Z",
        "author": {"login": "maintainer"}, "commit": {"oid": head},
    }
    node = _graphql_node(_sample_pr(
        number=42, head_oid=head, author="contributor", review_decision="CHANGES_REQUESTED", reviews=[review],
    ))
    node["isDraft"] = case == "draft"
    node["comments"] = {"nodes": [], "pageInfo": {"hasPreviousPage": True, "startCursor": "recent"}}
    scope = {
        "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
        "pr_filter": {"effective_verdicts": ["REQUEST_CHANGES"], "exclude_drafts": True, "min_stalled_hours": 24,
                      "author_mode": "exclude", "authors": ["maintainer"]},
    }
    reads = []

    def runner(cmd, **kwargs):
        if "before=recent" not in cmd:
            payload = _graphql_page([node])
        else:
            query = next(arg for arg in cmd if arg.startswith("query="))
            identity_fields = query.split("pullRequest(number: $number) {", 1)[1].split("comments(", 1)[0]
            assert {"number", "headRefOid"}.issubset(identity_fields.split())
            reads.append(kwargs["timeout"])
            reply_login = "maintainer" if case == "same_head_maintainer" else "contributor"
            pr_data = _older_comment_row(node, {"pageInfo": {"hasPreviousPage": False}, "nodes": [
                    {"createdAt": "2030-01-01T09:00:00Z", "author": {"login": reply_login},
                     "authorAssociation": "MEMBER"},
                ]})
            if case == "changed_head":
                pr_data["headRefOid"] = "b" * 40
            elif case == "missing_head":
                del pr_data["headRefOid"]
            elif case == "wrong_number":
                pr_data["number"] = 43
            elif case == "missing_number":
                del pr_data["number"]
            elif case == "wrong_number_type":
                pr_data["number"] = 42.0
            payload = {"data": {"repository": {"pullRequest": pr_data}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    snapshot = _read_repository_snapshot(
        {"task_repository": "git:github.com/org/repo"}, scope,
        partial(observe_github_repository_prs, runner=runner), ChatConversationBindings._core,
    )
    assert snapshot is not None and snapshot["status"] == "ok"
    assert reads == ([] if case == "draft" else [20])
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot, "signal_scope": scope,
        "observed_at": observed_at.isoformat(), "previous_hash": "sha256:prior",
    })
    usable = case in {"same_head_author", "same_head_maintainer", "draft"}
    assert observation["observation_incomplete"] is not usable
    if usable:
        numbers = [item["number"] for item in observation["scoped_repository_items"]]
        assert numbers == ([42] if case == "same_head_author" else [])
    else:
        assert snapshot["items"][0]["author_replied_after_request_changes"] is False
        assert observation["result_hash"] == "sha256:prior"
        assert observation["material_change"] is False


@pytest.mark.parametrize("case", [
    "updated-at", "review-decision", "missing-latest", "changed-latest", "changed-latest-cursor",
    "missing-consumed", "changed-consumed", "oversized-older-page", "consistent",
])
def test_steward_requires_comment_revision_and_consumed_windows_to_remain_consistent(case: str) -> None:
    head = "a" * 40
    node = _graphql_node(_sample_pr(head_oid=head, author="contributor", review_decision="CHANGES_REQUESTED", reviews=[{
        "id": "request-changes", "state": "CHANGES_REQUESTED", "commit": {"oid": head},
        "author": {"login": "maintainer"}, "submittedAt": "2030-01-01T08:00:00Z",
    }]))
    visitor = {"createdAt": "2030-01-01T09:00:00Z", "author": {"login": "observer"}}
    author = {**visitor, "author": {"login": "contributor"}}
    node["comments"] = {"nodes": [visitor], "pageInfo": {"hasPreviousPage": True, "startCursor": "first"}}
    consumed = {"nodes": [visitor], "pageInfo": {"hasPreviousPage": True, "startCursor": "second"}}
    terminal = {"nodes": [author], "pageInfo": {"hasPreviousPage": False}}
    reads = []

    def runner(cmd, **_kwargs):
        cursor = next((arg.removeprefix("before=") for arg in cmd if arg.startswith("before=")), None)
        reads.append(cursor)
        if cursor is None:
            payload = _graphql_page([node])
        else:
            query = next(arg for arg in cmd if arg.startswith("query="))
            assert "latestComments: comments(last: 20)" in query
            pr_data = _older_comment_row(node, consumed if cursor == "first" else terminal)
            if cursor == "second":
                assert "previousComments0=first" in cmd
                assert "previousComments0: comments(last: 50, before: $previousComments0)" in query
                pr_data["previousComments0"] = consumed
                if case == "updated-at":
                    pr_data["updatedAt"] = "2030-01-01T09:01:00Z"
                elif case == "review-decision":
                    pr_data["reviewDecision"] = "APPROVED"
                elif case == "missing-latest":
                    del pr_data["latestComments"]
                elif case == "changed-latest":
                    pr_data["latestComments"] = {**node["comments"], "nodes": [author]}
                elif case == "changed-latest-cursor":
                    pr_data["latestComments"] = {
                        **node["comments"], "pageInfo": {"hasPreviousPage": True, "startCursor": "other"},
                    }
                elif case == "missing-consumed":
                    del pr_data["previousComments0"]
                elif case == "changed-consumed":
                    pr_data["previousComments0"] = {**consumed, "nodes": [author]}
                elif case == "oversized-older-page":
                    pr_data["comments"] = {**terminal, "nodes": [author] * 51}
            payload = {"data": {"repository": {"pullRequest": pr_data}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    snapshot = observe_github_repository_prs("org/repo", runner=runner, backfill_comments=True)
    assert reads == [None, "first", "second"]
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot, "previous_hash": "sha256:prior",
        "observed_at": "2030-01-01T10:00:00Z",
        "signal_scope": {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
                         "include_inbox": False, "pr_filter": {"min_stalled_hours": 24,
                             "author_mode": "exclude", "authors": ["maintainer"]}},
    })
    if case == "consistent":
        assert observation["observation_incomplete"] is False
        assert observation["scoped_repository_items"][0]["stalled_bucket"] == "awaiting_maintainer_reply"
    else:
        assert observation["observation_incomplete"] is True
        assert observation["result_hash"] == "sha256:prior"
        assert observation["material_change"] is False


@pytest.mark.parametrize(
    ("pages", "expected_reads", "expected_prs"),
    [
        pytest.param([("B", None), ("A", None), (None, "contributor")], ["A", "B"], None, id="cycle"),
        pytest.param([("A", None)], ["A"], None, id="self-loop"),
        pytest.param([("B", None), ("A", "contributor")], ["A", "B"], [42], id="independent-reply"),
        pytest.param([("B", None), ("C", None), (None, None)], ["A", "B", "C"], [], id="terminal-at-page-cap"),
    ],
)
def test_steward_comment_backfill_never_reuses_a_cursor(monkeypatch, pages, expected_reads, expected_prs) -> None:
    from loopx.capabilities.native_chat.steward_monitor_signals import _read_repository_snapshot

    head = "a" * 40
    observed_at = datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor_signals.now_utc", lambda: observed_at)
    review = {
        "id": "review", "state": "CHANGES_REQUESTED", "submittedAt": "2030-01-01T08:00:00Z",
        "author": {"login": "maintainer"}, "commit": {"oid": head},
    }
    node = _graphql_node(_sample_pr(
        number=42, head_oid=head, author="contributor", review_decision="CHANGES_REQUESTED", reviews=[review],
    ))
    node["comments"] = {"nodes": [], "pageInfo": {"hasPreviousPage": True, "startCursor": "A"}}
    scope = {
        "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
        "pr_filter": {"effective_verdicts": ["REQUEST_CHANGES"], "min_stalled_hours": 24,
                      "author_mode": "exclude", "authors": ["maintainer"]},
    }
    reads = []
    comment_pages = {}

    def runner(cmd, **_kwargs):
        before = next((arg.removeprefix("before=") for arg in cmd if arg.startswith("before=")), None)
        if before is None:
            payload = _graphql_page([node])
        else:
            next_cursor, login = pages[len(reads)]
            reads.append(before)
            replies = [{"createdAt": "2030-01-01T09:00:00Z", "author": {"login": login}}] if login else []
            comments = {"nodes": replies, "pageInfo": {
                "hasPreviousPage": next_cursor is not None, "startCursor": next_cursor,
            }}
            pr_data = _older_comment_row(node, comments)
            pr_data.update(_comment_readback_rows(cmd, comment_pages))
            comment_pages[before] = comments
            payload = {"data": {"repository": {"pullRequest": pr_data}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    snapshot = _read_repository_snapshot(
        {"task_repository": "git:github.com/org/repo"}, scope,
        partial(observe_github_repository_prs, runner=runner), ChatConversationBindings._core,
    )
    assert snapshot is not None and snapshot["status"] == "ok"
    observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot, "signal_scope": scope,
        "observed_at": observed_at.isoformat(), "previous_hash": "sha256:prior",
    })
    assert observation["observation_incomplete"] is (expected_prs is None)
    if expected_prs is None:
        assert observation["result_hash"] == "sha256:prior"
        assert observation["material_change"] is False
    else:
        assert [item["number"] for item in observation["scoped_repository_items"]] == expected_prs
    assert reads == expected_reads


@pytest.mark.parametrize(
    ("page_kind", "page_info", "evidence", "expected_complete"),
    [
        pytest.param("initial", {"startCursor": "recent"}, None, False, id="initial-missing-flag"),
        pytest.param("older", {}, None, False, id="older-missing-flag"),
        pytest.param("older", None, None, False, id="older-missing-page-info"),
        pytest.param("older", {"hasPreviousPage": "false", "startCursor": "older"}, None, False, id="string-flag"),
        pytest.param("older", {"hasPreviousPage": 0}, None, False, id="numeric-flag"),
        pytest.param("older", {"hasPreviousPage": True, "startCursor": 7}, None, False, id="invalid-next-cursor"),
        pytest.param("older", {"hasPreviousPage": False}, None, True, id="verified-end"),
        pytest.param("older", {}, "anchor", True, id="missing-flag-with-anchor"),
        pytest.param("older", None, "author", True, id="missing-page-info-with-author-reply"),
        pytest.param("initial", {"hasPreviousPage": False}, "null-row", False, id="initial-null-row"),
        pytest.param("older", {"hasPreviousPage": False}, "null-row", False, id="older-null-row"),
        pytest.param("initial", {"hasPreviousPage": False}, "gap-after-author", False, id="gap-after-author"),
        pytest.param("older", {"hasPreviousPage": False}, "gap-before-author", False, id="gap-before-author"),
        pytest.param("initial", {"hasPreviousPage": False}, "missing-nodes", False, id="missing-nodes"),
        pytest.param("older", {"hasPreviousPage": False}, "invalid-time", False, id="unreadable-timestamp"),
        pytest.param("initial", {"hasPreviousPage": False}, "same-role-undated", True, id="initial-compatible-timestamp"),
        pytest.param("older", {"hasPreviousPage": False}, "same-role-undated", True, id="older-compatible-timestamp"),
        pytest.param("initial", {"hasPreviousPage": False}, "missing-connection", False, id="missing-connection"),
        pytest.param("initial", {"hasPreviousPage": False, "startCursor": None}, None, True, id="initial-verified-empty"),
        pytest.param("initial", {"hasPreviousPage": True, "startCursor": "recent"}, "author", True, id="verified-reply"),
    ],
)
def test_steward_requires_readable_comment_page_for_coverage(
    page_kind, page_info, evidence, expected_complete,
) -> None:
    head = "a" * 40
    node = _graphql_node(_sample_pr(
        head_oid=head,
        author="member",
        reviews=[{
            "id": "PRR_review", "state": "CHANGES_REQUESTED", "submittedAt": "2030-01-01T08:00:00Z",
            "author": {"login": "maintainer"}, "commit": {"oid": head},
        }],
    ))
    rows: list[object] = []
    if evidence == "anchor":
        rows.append({"createdAt": "2030-01-01T08:00:00Z"})
    elif evidence in {"author", "gap-before-author", "gap-after-author", "invalid-time", "same-role-undated"}:
        rows.append({
            "createdAt": "2030-01-01T09:00:00Z",
            "authorAssociation": "NONE",
            "author": {"__typename": "User", "login": "member"},
        })
    if evidence == "gap-before-author":
        rows.insert(0, None)
    elif evidence in {"null-row", "gap-after-author"}:
        rows.append(None)
    elif evidence == "invalid-time":
        rows.append({"createdAt": "invalid", "author": {"login": "maintainer"}})
    elif evidence == "same-role-undated":
        rows.append({"createdAt": None, "author": {"login": "member"}})

    response_page: dict[str, object] = {"nodes": rows, "pageInfo": page_info}
    if evidence == "missing-nodes":
        del response_page["nodes"]
    node["comments"] = response_page if page_kind == "initial" else {
        "nodes": [], "pageInfo": {"hasPreviousPage": True, "startCursor": "recent"},
    }
    if evidence == "missing-connection":
        del node["comments"]
    requested_cursors = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        cursor = next((arg for arg in cmd if arg.startswith("before=")), None)
        if cursor is None:
            response = _graphql_page([node])
        else:
            requested_cursors.append(cursor)
            response = {"data": {"repository": {"pullRequest": _older_comment_row(node, response_page)}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(response))

    snapshot = observe_github_repository_prs("org/repo", runner=runner, backfill_comments=True)
    assert requested_cursors == (["before=recent"] if page_kind == "older" else [])
    assert snapshot["status"] == "ok"
    assert snapshot["items"][0]["comments_coverage_incomplete"] is (not expected_complete)
    assert snapshot["items"][0]["author_replied_after_request_changes"] is (evidence in {"author", "same-role-undated"})

    observed = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot,
        "previous_hash": "sha256:verified-prior",
        "observed_at": "2030-01-02T08:00:00Z",
        "signal_scope": {
            "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
            "include_inbox": False, "pr_filter": {"min_stalled_hours": 24},
        },
    })
    assert observed["observation_incomplete"] is (not expected_complete)
    assert observed["material_change"] is expected_complete
    if not expected_complete:
        assert observed["result_hash"] == "sha256:verified-prior"


@pytest.mark.parametrize(
    ("source", "known_login", "actor", "reply_time", "expected", "creator"),
    [
        pytest.param("latest", "member", None, "09:30", "uncertain", "member", id="null-latest-actor"),
        pytest.param("older", "member", None, "09:30", "uncertain", "member", id="null-backfilled-actor"),
        pytest.param("review", "member", None, "09:30", "uncertain", "member", id="null-commented-review-actor"),
        pytest.param("latest", "member", {"login": None}, "09:30", "uncertain", "member", id="missing-login"),
        pytest.param("latest", "maintainer", None, "09:00", "uncertain", "member", id="unknown-ties-maintainer"),
        pytest.param("latest", "member", None, "09:00", "author", "member", id="known-author-wins-tie"),
        pytest.param("older", "member", None, "08:30", "author", "member", id="verified-newer-author"),
        pytest.param("truncated", "member", None, "08:30", "author", "member", id="verified-reply-covers-truncated-suffix"),
        pytest.param("latest", "member", None, "08:00", "author", "member", id="unknown-at-anchor"),
        pytest.param("latest", "member", {"login": "member"}, "09:30", "author", "member", id="verified-author"),
        pytest.param("latest", "member", {"login": "maintainer"}, "09:30", "maintainer", "member", id="verified-maintainer"),
        pytest.param("latest", "member", {"__typename": "Bot"}, "09:30", "author", "member", id="known-bot"),
        pytest.param("latest", None, {"login": "member"}, "09:30", "uncertain", None, id="null-pr-creator"),
        pytest.param("older", None, {"login": "member"}, "09:30", "uncertain", None, id="null-creator-older-reply"),
        pytest.param("review", None, {"login": "member"}, "09:30", "uncertain", None, id="null-creator-review-reply"),
        pytest.param("latest", None, {"login": "member"}, "09:30", "uncertain", {"login": None}, id="null-creator-login"),
        pytest.param("latest", None, {"login": "member"}, "09:30", "author", " MeMbEr ", id="matching-creator"),
        pytest.param("latest", None, {"login": "member"}, "09:30", "maintainer", "different", id="different-creator"),
        pytest.param("latest", None, {"__typename": "Bot"}, "09:30", "no-reply", None, id="null-creator-known-bot"),
        pytest.param("latest", None, {"login": "member"}, "08:00", "no-reply", None, id="null-creator-pre-anchor"),
        pytest.param("latest", None, {"login": "other"}, "09:30", "excluded-author", "other", id="known-excluded-creator"),
    ],
)
def test_steward_requires_actor_evidence_when_a_reply_can_change_the_result(
    source, known_login, actor, reply_time, expected, creator,
) -> None:
    head = "a" * 40
    node = _graphql_node(_sample_pr(
        head_oid=head,
        author="member",
        reviews=[{
            "id": "PRR_changes", "state": "CHANGES_REQUESTED", "submittedAt": "2030-01-01T08:00:00Z",
            "author": {"login": "maintainer"}, "commit": {"oid": head},
        }],
    ))
    node["author"] = {"login": creator} if isinstance(creator, str) else creator
    replies = []
    if known_login is not None:
        replies.append({
            "createdAt": "2030-01-01T09:00:00Z", "authorAssociation": "MEMBER",
            "author": {"login": known_login},
        })
    submitted = f"2030-01-01T{reply_time}:00Z"
    candidate = {"author": actor, "authorAssociation": "MEMBER"}
    if source == "review":
        node["reviews"]["nodes"].append({
            **candidate, "id": "PRR_comment", "state": "COMMENTED",
            "submittedAt": submitted, "commit": {"oid": head},
        })
        node["reviews"]["totalCount"] += 1
    else:
        replies.append({**candidate, "createdAt": submitted})
    finished = {"nodes": replies, "pageInfo": {"hasPreviousPage": False}}
    if source == "truncated":
        finished["pageInfo"] = {"hasPreviousPage": True, "startCursor": "recent"}
    node["comments"] = (
        {"nodes": [], "pageInfo": {"hasPreviousPage": True, "startCursor": "recent"}}
        if source == "older" else finished
    )
    requested_cursors = []

    def runner(cmd, **kwargs):
        assert kwargs["timeout"] == 20
        cursor = next((arg for arg in cmd if arg.startswith("before=")), None)
        if cursor is None:
            response = _graphql_page([node])
        else:
            requested_cursors.append(cursor)
            response = {"data": {"repository": {"pullRequest": _older_comment_row(node, finished)}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(response))

    snapshot = observe_github_repository_prs("org/repo", runner=runner, backfill_comments=source == "older")
    assert requested_cursors == (["before=recent"] if source == "older" else [])
    assert snapshot["status"] == "ok"
    assert snapshot["items"][0]["comments_coverage_incomplete"] is (expected == "uncertain")
    assert snapshot["items"][0]["author_replied_after_request_changes"] is (expected in {"author", "excluded-author"})

    observed = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
        "repository_snapshot": snapshot,
        "previous_hash": "sha256:verified-prior",
        "observed_at": "2030-01-01T10:00:00Z",
        "signal_scope": {
            "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
            "include_inbox": False, "pr_filter": {"min_stalled_hours": 24, "author_mode": "exclude", "authors": ["other"]},
        },
    })
    assert observed["observation_incomplete"] is (expected == "uncertain")
    if expected == "uncertain":
        assert observed["result_hash"] == "sha256:verified-prior"
        assert observed["material_change"] is False
    else:
        buckets = [row["stalled_bucket"] for row in observed["scoped_repository_items"]]
        assert buckets == (["awaiting_maintainer_reply"] if expected == "author" else [])


def test_inspect_github_pull_request_attaches_rest_html_url() -> None:
    head = "d" * 40
    pr_row = _sample_pr(
        number=42,
        head_oid=head,
        reviews=[
            {
                "id": "PRR_node_42",
                "author": {"login": "gpt-reviewer"},
                "state": "APPROVED",
                "commit": {"oid": head},
                "submittedAt": "2030-01-01T08:30:00Z",
                "body": _valid_bilingual_body(
                    head_oid=head, verdict="APPROVE", reviewer="model_agent · gpt-6.2-sol · OpenAI"
                ),
            }
        ],
    )
    rest_reviews = [
        {
            "id": 998877,
            "node_id": "PRR_node_42",
            "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-998877",
            "user": {"login": "gpt-reviewer"},
            "state": "APPROVED",
            "commit_id": head,
            "submitted_at": "2030-01-01T08:30:00Z",
        }
    ]

    def runner(cmd, **_kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(pr_row), stderr="")
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(rest_reviews), stderr="")
        raise AssertionError(cmd)

    inspected = inspect_github_pull_request("org/repo", 42, runner=runner)
    assert inspected is not None
    exact = inspected["review_summary"]["exact_head_reviews"][0]
    assert exact["url"] == "https://github.com/org/repo/pull/42#pullrequestreview-998877"


def test_bare_graphql_comments_cannot_replace_an_observed_author_reply() -> None:
    head = "a" * 40
    node = _graphql_node(_sample_pr(head_oid=head, author="member", reviews=[{
        "id": "PRR_review", "state": "CHANGES_REQUESTED", "submittedAt": "2030-01-01T08:00:00Z",
        "author": {"login": "maintainer"}, "commit": {"oid": head},
    }]))
    reply = {"createdAt": "2030-01-01T09:00:00Z", "author": {"__typename": "User", "login": "member"}}
    scope = {"schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False,
             "include_inbox": False, "pr_filter": {"min_stalled_hours": 24}}

    def observe(comments, previous_hash=None):
        def runner(cmd, **_kwargs):
            response = _graphql_page([{**node, "comments": comments}])
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(response))

        snapshot = observe_github_repository_prs("org/repo", runner=runner)
        observation = ChatConversationBindings._core("collaboration.steward.monitor.signal_observation", {
            "repository_snapshot": snapshot, "previous_hash": previous_hash, "signal_scope": scope,
            "observed_at": "2030-01-02T09:00:00Z",
        })
        return snapshot["items"][0], observation

    verified, baseline = observe({"nodes": [reply], "pageInfo": {"hasPreviousPage": False, "startCursor": "reply"}})
    assert verified["author_replied_after_request_changes"] is True
    assert baseline["observation_incomplete"] is False

    malformed, observation = observe([], baseline["result_hash"])
    assert malformed["comments_coverage_incomplete"] is True
    assert observation["observation_incomplete"] is True
    assert observation["material_change"] is False
    assert observation["result_hash"] == baseline["result_hash"]


@pytest.mark.parametrize("unclassified_conclusion", ["STALE", None], ids=["stale", "missing-conclusion"])
def test_incomplete_checks_keep_confirmed_gates_in_protected_merge_preview(unclassified_conclusion) -> None:
    head = "a" * 40
    row = _sample_pr(head_oid=head, review_decision="APPROVED", merge_state="CLEAN", reviews=[{
        "id": "PRR_approve", "author": {"login": "reviewer"}, "state": "APPROVED", "commit": {"oid": head},
        "url": "https://github.com/org/repo/pull/42#pullrequestreview-1",
    }], checks=[
        {"status": "COMPLETED", "conclusion": unclassified_conclusion},
        {"status": "COMPLETED", "conclusion": "FAILURE"},
        {"status": "IN_PROGRESS"},
    ])

    def runner(cmd, **_kwargs):
        assert cmd[:3] == ["gh", "pr", "view"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(row))

    inspected = inspect_github_pull_request("org/repo", 42, runner=runner)
    assert inspected is not None and inspected["checks_summary"] is None
    assert inspected["checks_coverage_incomplete"] is True
    assert set(inspected["bypassed_gates"]) == {"FAILING_CHECKS", "PENDING_CHECKS"}

    frozen, _, _ = pr_merge_preview({"repository": "org/repo", "pr_number": 42}, digest_fn=_digest, runner=runner)
    assert set(frozen["bypassed_gates"]) == {"FAILING_CHECKS", "PENDING_CHECKS"}
    assert frozen["admin_bypass"] is True


def _steward_with_repo(steward_comp, tmp_path):
    store, runtime, *_ = steward_comp
    _project, registry, _ = _fixture(tmp_path / "canonical", runtime_root=store.root.parent)
    data = json.loads(registry.read_text())
    data["goals"][0]["task_repository"] = "git:github.com/org/repo"
    runtime.registry_path.write_text(json.dumps(data))
    return steward_comp


def test_steward_protected_merge_confirm_and_private_chat_report(steward, tmp_path, monkeypatch) -> None:  # noqa: F811
    store, runtime, provider, transport, _, _, _ = _steward_with_repo(steward, tmp_path)
    head = "e" * 40
    merge_commit = "f" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    state_box = {"merged": False, "merge_calls": []}

    pr_open = _sample_pr(
        number=42,
        head_oid=head,
        review_decision="REVIEW_REQUIRED",
        merge_state="BLOCKED",
        reviews=[
            {
                "id": "PRR_gpt_42",
                "author": {"login": "loopx-agent"},
                "state": "COMMENTED",
                "commit": {"oid": head},
                "submittedAt": "2030-01-01T08:30:00Z",
                "body": _valid_bilingual_body(
                    head_oid=head, verdict="APPROVE", reviewer=gpt_decl, author_owned=True
                ),
            }
        ],
    )
    rest_reviews = [
        {
            "node_id": "PRR_gpt_42",
            "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-4201",
        }
    ]
    pr_merged = _sample_pr(
        number=42,
        head_oid=head,
        state="MERGED",
        merge_commit={"oid": merge_commit},
        reviews=pr_open["reviews"],
    )

    def fake_gh(cmd, **_kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            payload = pr_merged if state_box["merged"] else pr_open
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(rest_reviews), stderr="")
        if cmd[:3] == ["gh", "pr", "merge"]:
            state_box["merge_calls"].append(list(cmd))
            state_box["merged"] = True
            return subprocess.CompletedProcess(cmd, 0, stdout="merged\n", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr("loopx.chat_actions.subprocess.run", fake_gh)

    response = {
        "schema_version": "loopx_chat_agent_response_v0",
        "message": "PR #42 已通过 exact-head GPT 评审，准备管理员合并预览。",
        "proposals": [],
        "protected_action": {
            "operation": "merge",
            "target": "https://github.com/org/repo/pull/42",
            "summary": "Admin bypass squash merge PR #42 after GPT exact-head APPROVE",
        },
        "gate": None,
    }
    monkeypatch.setattr("loopx.chat_runtime.CodexAppServerAdapter.start_turn", lambda *_a, **_kw: response)
    transport.admit("steward-app", provider.event("steward-app", "ask-merge", "合并 PR #42"))
    original = transport.core.pending()[0]
    assert finish(runtime, original)["status"] == "completed"
    transport.reconcile()

    assert state_box["merge_calls"] == []
    preview = transport.core.actions.store.list()[0]
    assert preview["action_kind"] == "pr.merge"
    assert preview["permission_classification"] == "protected"
    assert preview["status"] == "preview_ready"
    assert any(
        profile == "steward-app"
        and "待确认受保护合并：https://github.com/org/repo/pull/42" in text
        and f"/confirm {preview['proposal_id']}" in text
        and "REVIEW_REQUIRED" in text
        and "https://github.com/org/repo/pull/42#pullrequestreview-4201" in text
        for profile, text in provider.writes
    )

    # Confirm executes gh pr merge with exact head match and --admin, then reports in private chat
    cmd = f"/confirm {preview['proposal_id']}"
    transport.admit("steward-app", provider.event("steward-app", "confirm-merge", cmd))
    transport.reconcile()
    assert len(state_box["merge_calls"]) == 1
    merge_cmd = state_box["merge_calls"][0]
    assert "--admin" in merge_cmd
    assert "--match-head-commit" in merge_cmd and head in merge_cmd
    applied = transport.core.actions.load(preview["proposal_id"])
    assert applied["status"] == "applied"
    assert applied["receipt"]["resource_ids"]["merge_commit_oid"] == merge_commit
    assert any(
        profile == "steward-app"
        and "已核实合入 PR：https://github.com/org/repo/pull/42" in text
        and merge_commit in text
        and "https://github.com/org/repo/pull/42#pullrequestreview-4201" in text
        and "REVIEW_REQUIRED" in text
        for profile, text in provider.writes
    )


def test_steward_protected_merge_head_drift_and_crash_recovery(steward, tmp_path, monkeypatch) -> None:  # noqa: F811
    store, runtime, provider, transport, _, _, _ = _steward_with_repo(steward, tmp_path)
    head_v1 = "1" * 40
    head_v2 = "2" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    current = {"head": head_v1, "state": "OPEN", "merge_commit": None, "merge_calls": 0}

    def fake_gh(cmd, **_kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            pr_num = int(cmd[3])
            review_head = head_v1 if pr_num == 42 else head_v2
            row = _sample_pr(
                number=pr_num,
                head_oid=current["head"],
                state=current["state"],
                merge_commit={"oid": current["merge_commit"]} if current["merge_commit"] else None,
                reviews=[
                    {
                        "id": f"PRR_{pr_num}",
                        "author": {"login": "gpt-reviewer"},
                        "state": "APPROVED",
                        "commit": {"oid": review_head},
                        "submittedAt": "2030-01-01T08:30:00Z",
                        "body": _valid_bilingual_body(head_oid=review_head, verdict="APPROVE", reviewer=gpt_decl),
                    }
                ],
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(row), stderr="")
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        if cmd[:3] == ["gh", "pr", "merge"]:
            current["merge_calls"] += 1
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr("loopx.chat_actions.subprocess.run", fake_gh)

    response = {
        "schema_version": "loopx_chat_agent_response_v0",
        "message": "预览合入 PR #42。",
        "proposals": [],
        "protected_action": {"operation": "merge", "target": "org/repo#42", "summary": "Merge #42"},
        "gate": None,
    }
    monkeypatch.setattr("loopx.chat_runtime.CodexAppServerAdapter.start_turn", lambda *_a, **_kw: response)
    transport.admit("steward-app", provider.event("steward-app", "drift-req", "合并 42"))
    assert finish(runtime, transport.core.pending()[0])["status"] == "completed"
    transport.reconcile()

    preview = transport.core.actions.store.list()[0]
    # Drift head commit before /confirm
    current["head"] = head_v2
    transport.admit("steward-app", provider.event("steward-app", "confirm-drift", f"/confirm {preview['proposal_id']}"))
    transport.reconcile()
    assert current["merge_calls"] == 0
    assert transport.core.actions.load(preview["proposal_id"])["status"] == "stale"
    assert any("受保护合并预览已失效" in text for _, text in provider.writes)

    # Crash recovery with merge_attempted checkpoint reconciles already-MERGED PR without second merge
    current["head"] = head_v1
    recovery_preview = transport.core.actions.preview(
        {
            "action_kind": "pr.merge",
            "summary": "Recover PR #42",
            "idempotency_key": "recover-pr-42",
            "context": preview["context"],
            "normalized_parameters": {"repository": "org/repo", "pr_number": 42, "admin_bypass": True},
        }
    )
    transport.core.actions.store.start_apply(recovery_preview["proposal_id"])
    transport.core.actions.store.save_checkpoint(
        recovery_preview["proposal_id"],
        step="merge_attempted",
        receipt={"repository": "org/repo", "pr_number": 42, "head_oid": head_v1, "admin_bypass": True},
    )
    current["state"] = "MERGED"
    current["merge_commit"] = "9" * 40
    transport.admit(
        "steward-app",
        provider.event("steward-app", "confirm-recover", f"/confirm {recovery_preview['proposal_id']}"),
    )
    transport.reconcile()
    assert current["merge_calls"] == 0
    recovered = transport.core.actions.load(recovery_preview["proposal_id"])
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == "pr_merged_recovered"
    assert recovered["receipt"]["resource_ids"]["merge_commit_oid"] == "9" * 40


@pytest.mark.parametrize(
    ("dated_author_reply", "undated_role", "field", "timestamp", "expected"),
    [
        (True, "maintainer", "comment", None, (False, True)),
        (True, "maintainer", "comment", "invalid", (False, True)),
        (True, "maintainer", "review", None, (False, True)),
        (True, "author", "comment", None, (True, False)),
        (True, "visitor", "comment", None, (True, False)),
        (True, "unknown", "comment", None, (False, True)),
        (False, "author", "comment", None, (False, True)),
        (False, "maintainer", "comment", None, (False, False)),
    ],
)
def test_unreadable_reply_time_only_blocks_when_it_could_change_author_reply(
    dated_author_reply, undated_role, field, timestamp, expected,
) -> None:
    head = "d" * 40
    reviewer = {"login": "maintainer"}
    author = {"login": "contributor"}
    roles = {
        "maintainer": {"author": reviewer, "authorAssociation": "MEMBER"},
        "author": {"author": author},
        "visitor": {"author": {"login": "visitor"}, "authorAssociation": "NONE"},
        "unknown": {"author": None},
    }
    reviews = [{
        "id": "rc", "state": "CHANGES_REQUESTED", "author": reviewer,
        "commit": {"oid": head}, "submittedAt": "2030-01-01T08:00:00Z",
    }]
    comments = [{"author": author, "createdAt": "2030-01-01T09:00:00Z"}] if dated_author_reply else []
    if field == "review":
        reviews.append({
            **roles[undated_role], "id": "reply", "state": "COMMENTED",
            "commit": {"oid": head}, "submittedAt": timestamp,
        })
    else:
        comments.append({**roles[undated_role], "createdAt": timestamp})
    row = _sample_pr(head_oid=head, author="contributor", review_decision="CHANGES_REQUESTED", reviews=reviews)
    summary = summarize_github_pr_signal({**row, "comments": comments, "commentsHasPreviousPage": False})
    assert (summary["author_replied_after_request_changes"], summary["comments_coverage_incomplete"]) == expected


def test_summarize_pr_stalled_since_and_author_reply_detection(monkeypatch) -> None:
    head = "e" * 40
    old = "0" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"

    # Mergify bot comment bumps updatedAt without resetting stalled_since or flagging author reply.
    bot_bumped = summarize_github_pr_signal(
        {
            **_sample_pr(
                number=301,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[
                    {
                        "id": "PRR_rc1",
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T08:00:00Z",
                        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                    }
                ],
            ),
            "updatedAt": "2030-01-02T00:00:00Z",
            "comments": [
                {"createdAt": "2030-01-01T09:00:00Z", "author": {"login": "mergify[bot]"}},
            ],
        }
    )
    assert bot_bumped["stalled_since"] == "2030-01-01T08:00:00Z"
    assert bot_bumped["author_replied_after_request_changes"] is False
    assert bot_bumped["review_summary"]["latest_request_changes_review_id"] == "PRR_rc1"

    # External author replies after REQUEST_CHANGES -> awaiting maintainer reply; maintainer reply resets it.
    author_replied = summarize_github_pr_signal(
        {
            **_sample_pr(
                number=302,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[
                    {
                        "id": "PRR_rc2",
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T08:00:00Z",
                        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                    }
                ],
            ),
            "comments": [
                {"createdAt": "2030-01-01T10:00:00Z", "author": {"login": "ext-contributor"}},
                {"createdAt": "2030-01-01T10:30:00Z", "author": {"login": "mergify[bot]"}},
            ],
        }
    )
    assert author_replied["author_replied_after_request_changes"] is True

    maintainer_replied = summarize_github_pr_signal(
        {
            **_sample_pr(
                number=302,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[
                    {
                        "id": "PRR_rc2",
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T08:00:00Z",
                        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                    },
                    {
                        "id": "PRR_reply",
                        "author": {"login": "maintainer"},
                        "state": "COMMENTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T11:00:00Z",
                        "body": "Please keep the existing state field unchanged.",
                    },
                ],
            ),
            "comments": [
                {"createdAt": "2030-01-01T10:00:00Z", "author": {"login": "ext-contributor"}},
            ],
        }
    )
    assert maintainer_replied["author_replied_after_request_changes"] is False

    # Visitor (authorAssociation=NONE), suffixless Bot (__typename=Bot), and unknown commenter never clear author reply;
    # another maintainer (authorAssociation=MEMBER) DOES clear it.
    visitor_and_bot_ignored = summarize_github_pr_signal(
        {
            **_sample_pr(
                number=305,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[
                    {
                        "id": "PRR_rc5",
                        "author": {"__typename": "User", "login": "orig-reviewer"},
                        "authorAssociation": "MEMBER",
                        "state": "CHANGES_REQUESTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T08:00:00Z",
                        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                    },
                    {
                        "id": "PRR_bystander",
                        "author": {"__typename": "User", "login": "random-reviewer"},
                        "authorAssociation": "CONTRIBUTOR",
                        "state": "COMMENTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T11:30:00Z",
                        "body": "Looks interesting!",
                    },
                ],
            ),
            "comments": [
                {"createdAt": "2030-01-01T10:00:00Z", "authorAssociation": "NONE", "author": {"__typename": "User", "login": "ext-contributor"}},
                {"createdAt": "2030-01-01T10:20:00Z", "authorAssociation": "NONE", "author": {"__typename": "User", "login": "random-visitor"}},
                {"createdAt": "2030-01-01T10:40:00Z", "authorAssociation": "NONE", "author": {"__typename": "Bot", "login": "custom-ci-helper"}},
                {"createdAt": "2030-01-01T11:00:00Z", "author": {"login": "missing-association-user"}},
            ],
        }
    )
    assert visitor_and_bot_ignored["author_replied_after_request_changes"] is True

    other_maintainer_replied = summarize_github_pr_signal(
        {
            **_sample_pr(
                number=305,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[
                    {
                        "id": "PRR_rc5",
                        "author": {"__typename": "User", "login": "orig-reviewer"},
                        "authorAssociation": "MEMBER",
                        "state": "CHANGES_REQUESTED",
                        "commit": {"oid": head},
                        "submittedAt": "2030-01-01T08:00:00Z",
                        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                    },
                ],
            ),
            "comments": [
                {"createdAt": "2030-01-01T10:00:00Z", "authorAssociation": "NONE", "author": {"__typename": "User", "login": "ext-contributor"}},
                {"createdAt": "2030-01-01T12:00:00Z", "authorAssociation": "MEMBER", "author": {"__typename": "User", "login": "second-maintainer"}},
            ],
        }
    )
    assert other_maintainer_replied["author_replied_after_request_changes"] is False

    # Stale-commit CHANGES_REQUESTED after new head commit push leaves stalled_since as None.
    stale_after_push = summarize_github_pr_signal(
        _sample_pr(
            number=303,
            head_oid=head,
            author="ext-contributor",
            review_decision="CHANGES_REQUESTED",
            reviews=[
                {
                    "id": "PRR_stale_rc",
                    "author": {"login": "maintainer"},
                    "state": "CHANGES_REQUESTED",
                    "commit": {"oid": old},
                    "submittedAt": "2030-01-01T08:00:00Z",
                    "body": _valid_bilingual_body(head_oid=old, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                }
            ],
        )
    )
    assert stale_after_push["stalled_since"] is None
    assert stale_after_push["review_summary"]["latest_request_changes_review_id"] is None
    assert stale_after_push["author_replied_after_request_changes"] is False

    # GraphQL page normalization retains comments and head commit pushedDate.
    gql_node = _graphql_node(
        _sample_pr(
            number=304,
            head_oid=head,
            author="ext-contributor",
            review_decision="CHANGES_REQUESTED",
            reviews=[
                {
                    "id": "PRR_rc4",
                    "author": {"login": "maintainer"},
                    "state": "CHANGES_REQUESTED",
                    "commit": {"oid": head},
                    "submittedAt": "2030-01-01T08:00:00Z",
                    "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
                }
            ],
        )
    )
    gql_node["comments"] = {
        "nodes": [
            {"createdAt": "2030-01-01T12:00:00Z", "authorAssociation": "NONE", "author": {"__typename": "User", "login": "ext-contributor"}},
            {"createdAt": "2030-01-01T12:15:00Z", "authorAssociation": "NONE", "author": {"__typename": "User", "login": "random-visitor"}},
            {"createdAt": "2030-01-01T12:30:00Z", "authorAssociation": "NONE", "author": {"__typename": "Bot", "login": "custom-ci-helper"}},
        ]
    }
    gql_node["commits"]["nodes"][0]["commit"]["oid"] = head
    gql_node["commits"]["nodes"][0]["commit"]["committedDate"] = "2030-01-01T07:00:00Z"
    gql_node["commits"]["nodes"][0]["commit"]["pushedDate"] = "2030-01-01T09:00:00Z"

    def fake_gql(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([gql_node])), stderr="")

    monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", fake_gql)
    observed = _observe_github_repository_prs("org/repo")
    assert observed["status"] == "ok"
    item = observed["items"][0]
    assert item["stalled_since"] == "2030-01-01T09:00:00Z"
    assert item["author_replied_after_request_changes"] is True
    assert item["comments_coverage_incomplete"] is False
    assert item["review_summary"]["comments_coverage_incomplete"] is False
    assert item["review_summary"]["latest_request_changes_review_id"] == "PRR_rc4"


def test_observe_github_prs_backfills_evicted_author_reply_and_preserves_incomplete_coverage(
    monkeypatch,
) -> None:
    head = "f" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    rc_review = {
        "id": "PRR_rc_401",
        "author": {"__typename": "User", "login": "maintainer"},
        "authorAssociation": "MEMBER",
        "state": "CHANGES_REQUESTED",
        "commit": {"oid": head},
        "submittedAt": "2030-01-01T08:00:00Z",
        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
    }

    def _visitor_comments(count: int, *, start_minute: int = 1, prefix_hour: str = "10") -> list[dict[str, Any]]:
        return [
            {
                "createdAt": f"2030-01-01T{prefix_hour}:{start_minute + idx:02d}:00Z",
                "authorAssociation": "NONE",
                "author": {"__typename": "User", "login": f"visitor-{idx}"},
            }
            for idx in range(count)
        ]

    author_comment = {
        "createdAt": "2030-01-01T09:00:00Z",
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "ext-contributor"},
    }
    maintainer_comment = {
        "createdAt": "2030-01-01T09:30:00Z",
        "authorAssociation": "MEMBER",
        "author": {"__typename": "User", "login": "maintainer"},
    }

    # Normalization defaults vs pageInfo propagation.
    plain_norm = _normalize_graphql_pr_node(_sample_pr(number=400, head_oid=head))
    assert plain_norm["commentsHasPreviousPage"] is True
    assert plain_norm["commentsStartCursor"] is None

    # Pre-anchor comment inside truncated window proves completeness without backfill.
    pre_anchor_summary = summarize_github_pr_signal(
        {
            **_sample_pr(number=400, head_oid=head, author="ext-contributor", reviews=[rc_review]),
            "commentsHasPreviousPage": True,
            "commentsStartCursor": "c0",
            "comments": [
                {"createdAt": "2030-01-01T07:30:00Z", "authorAssociation": "NONE", "author": {"login": "old-visitor"}},
                *_visitor_comments(19),
            ],
        }
    )
    assert pre_anchor_summary["comments_coverage_incomplete"] is False
    assert pre_anchor_summary["review_summary"]["comments_coverage_incomplete"] is False

    # No exact-head REQUEST_CHANGES means comments_coverage_incomplete is always False.
    no_rc_summary = summarize_github_pr_signal(
        {
            **_sample_pr(number=400, head_oid=head, author="ext-contributor", reviews=[]),
            "commentsHasPreviousPage": True,
            "comments": _visitor_comments(20),
        }
    )
    assert no_rc_summary["comments_coverage_incomplete"] is False

    def _base_node(*, updated_at: str, comments_conn: dict[str, Any]) -> dict[str, Any]:
        node = _graphql_node(
            _sample_pr(
                number=401,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[rc_review],
            )
        )
        node["updatedAt"] = updated_at
        node["comments"] = comments_conn
        return node

    external_scope = {
        "schema_version": "steward_signal_monitor_scope_v0",
        "include_portfolio": False,
        "include_inbox": False,
        "pr_filter": {
            "exclude_drafts": True,
            "effective_verdicts": ["REQUEST_CHANGES"],
            "author_mode": "exclude",
            "authors": ["maintainer"],
            "min_stalled_hours": 24,
        },
    }

    # 1. Snapshot 1 -> awaiting_maintainer_reply; Snapshot 2 evicts author reply into page 2 ->
    #    backfill recovers author_replied_after_request_changes=True and identical TS result_hash.
    snap1_node = _base_node(
        updated_at="2030-01-01T10:19:00Z",
        comments_conn={
            "pageInfo": {"hasPreviousPage": False, "startCursor": "c1"},
            "nodes": [author_comment, *_visitor_comments(19)],
        },
    )
    snap2_node = _base_node(
        updated_at="2030-01-01T10:20:00Z",
        comments_conn={
            "pageInfo": {"hasPreviousPage": True, "startCursor": "c20"},
            "nodes": _visitor_comments(20),
        },
    )

    def _observe_with(fake_runner, *, backfill_comments: bool = True) -> dict[str, Any]:
        with monkeypatch.context() as mp:
            if backfill_comments:
                mp.setattr(
                    "loopx.capabilities.native_chat.steward_monitor.subprocess.run",
                    fake_runner,
                )
                return _observe_github_repository_prs("org/repo", backfill_comments=True)
            mp.setattr(
                "loopx.capabilities.native_chat.steward_monitor.subprocess.run",
                fake_runner,
            )
            return _observe_github_repository_prs("org/repo")

    calls_snap1: list[list[str]] = []
    repo_snap1 = _observe_with(
        lambda cmd, **_kw: (
            calls_snap1.append(list(cmd))
            or subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([snap1_node])), stderr="")
        )
    )
    assert len(calls_snap1) == 1
    assert repo_snap1["items"][0]["author_replied_after_request_changes"] is True
    assert repo_snap1["items"][0]["comments_coverage_incomplete"] is False
    obs1 = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": external_scope,
            "repository_snapshot": repo_snap1,
            "observed_at": "2030-01-01T11:00:00Z",
            "previous_hash": None,
        },
    )
    assert obs1["observation_incomplete"] is False
    assert obs1["scoped_repository_items"][0]["stalled_bucket"] == "awaiting_maintainer_reply"

    calls_snap2: list[list[str]] = []

    def run_snap2(cmd, **_kwargs):
        calls_snap2.append(list(cmd))
        if any(arg == "before=c20" for arg in cmd):
            assert "-F" in cmd and "number=401" in cmd
            comments = {"pageInfo": {"hasPreviousPage": False, "startCursor": "c1"}, "nodes": [author_comment]}
            body = {"data": {"repository": {"pullRequest": _older_comment_row(snap2_node, comments)}}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([snap2_node])), stderr="")

    repo_snap2 = _observe_with(run_snap2)
    assert len(calls_snap2) == 2
    assert repo_snap2["items"][0]["author_replied_after_request_changes"] is True
    assert repo_snap2["items"][0]["comments_coverage_incomplete"] is False
    assert repo_snap2["items"][0]["review_summary"]["comments_coverage_incomplete"] is False
    obs2 = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": external_scope,
            "repository_snapshot": repo_snap2,
            "observed_at": "2030-01-01T11:05:00Z",
            "previous_hash": obs1["result_hash"],
        },
    )
    assert obs2["result_hash"] == obs1["result_hash"]
    assert obs2["material_change"] is False
    assert obs2["observation_incomplete"] is False

    # 2a. Maintainer (MEMBER) reply in visible page 1 (hasPreviousPage=True) requires 0 backfill calls.
    page1_maintainer_node = _base_node(
        updated_at="2030-01-01T10:25:00Z",
        comments_conn={
            "pageInfo": {"hasPreviousPage": True, "startCursor": "c20"},
            "nodes": [maintainer_comment, *_visitor_comments(19)],
        },
    )
    calls_maintainer_p1: list[list[str]] = []
    repo_maintainer_p1 = _observe_with(
        lambda cmd, **_kw: (
            calls_maintainer_p1.append(list(cmd))
            or subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([page1_maintainer_node])), stderr="")
        )
    )
    assert len(calls_maintainer_p1) == 1
    assert repo_maintainer_p1["items"][0]["author_replied_after_request_changes"] is False
    assert repo_maintainer_p1["items"][0]["comments_coverage_incomplete"] is False
    obs_maintainer_p1 = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": external_scope,
            "repository_snapshot": repo_maintainer_p1,
            "observed_at": "2030-01-01T11:10:00Z",
            "previous_hash": obs1["result_hash"],
        },
    )
    assert obs_maintainer_p1["material_change"] is True
    assert obs_maintainer_p1["observation_incomplete"] is False
    assert obs_maintainer_p1["scoped_repository_items"] == []

    # 2b. Maintainer reply after author on backfill page 2 exits awaiting_maintainer_reply.
    calls_maintainer_p2: list[list[str]] = []

    def run_maintainer_p2(cmd, **_kwargs):
        calls_maintainer_p2.append(list(cmd))
        if any(arg == "before=c20" for arg in cmd):
            comments = {
                "pageInfo": {"hasPreviousPage": True, "startCursor": "c0"},
                "nodes": [author_comment, maintainer_comment],
            }
            body = {"data": {"repository": {"pullRequest": _older_comment_row(snap2_node, comments)}}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([snap2_node])), stderr="")

    repo_maintainer_p2 = _observe_with(run_maintainer_p2)
    assert len(calls_maintainer_p2) == 2
    assert repo_maintainer_p2["items"][0]["author_replied_after_request_changes"] is False
    assert repo_maintainer_p2["items"][0]["comments_coverage_incomplete"] is False
    obs_maintainer_p2 = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": external_scope,
            "repository_snapshot": repo_maintainer_p2,
            "observed_at": "2030-01-01T11:15:00Z",
            "previous_hash": obs1["result_hash"],
        },
    )
    assert obs_maintainer_p2["material_change"] is True
    assert obs_maintainer_p2["observation_incomplete"] is False
    assert obs_maintainer_p2["scoped_repository_items"] == []

    # 3. Backfill budget exhausted (3 pages of visitor comments, still hasPreviousPage=True) ->
    #    comments_coverage_incomplete=True and TS retains previous_hash with observation_incomplete=True.
    backfill_cursors: list[str] = []
    comment_pages = {}

    def run_exhausted(cmd, **_kwargs):
        before_args = [arg.split("=", 1)[1] for arg in cmd if arg.startswith("before=")]
        if before_args:
            cursor = before_args[0]
            backfill_cursors.append(cursor)
            next_map = {"c20": ("c70", "11"), "c70": ("c120", "12"), "c120": ("c170", "13")}
            next_cursor, hour = next_map[cursor]
            comments = {
                "pageInfo": {"hasPreviousPage": True, "startCursor": next_cursor},
                "nodes": _visitor_comments(5, prefix_hour=hour),
            }
            pr_data = _older_comment_row(snap2_node, comments)
            pr_data.update(_comment_readback_rows(cmd, comment_pages))
            comment_pages[cursor] = comments
            body = {"data": {"repository": {"pullRequest": pr_data}}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([snap2_node])), stderr="")

    repo_exhausted = _observe_with(run_exhausted)
    assert backfill_cursors == ["c20", "c70", "c120"]
    assert repo_exhausted["status"] == "ok"
    assert repo_exhausted["items"][0]["comments_coverage_incomplete"] is True
    assert repo_exhausted["items"][0]["review_summary"]["comments_coverage_incomplete"] is True
    obs_exhausted = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": external_scope,
            "repository_snapshot": repo_exhausted,
            "observed_at": "2030-01-01T11:20:00Z",
            "previous_hash": obs1["result_hash"],
        },
    )
    assert obs_exhausted == {
        "result_hash": obs1["result_hash"],
        "material_change": False,
        "observation_incomplete": True,
        "own_monitor_bookkeeping_excluded": True,
    }

    # Backfill RPC error also fails closed with comments_coverage_incomplete=True.
    def run_backfill_error(cmd, **_kwargs):
        if any(arg.startswith("before=") for arg in cmd):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([snap2_node])), stderr="")

    repo_rpc_err = _observe_with(run_backfill_error)
    assert repo_rpc_err["status"] == "ok"
    assert repo_rpc_err["items"][0]["comments_coverage_incomplete"] is True
    obs_rpc_err = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": external_scope,
            "repository_snapshot": repo_rpc_err,
            "observed_at": "2030-01-01T11:25:00Z",
            "previous_hash": obs1["result_hash"],
        },
    )
    assert obs_rpc_err["observation_incomplete"] is True
    assert obs_rpc_err["result_hash"] == obs1["result_hash"]

    # Default backfill_comments=False invokes zero comment backfill RPCs even when hasPreviousPage=True.
    default_calls: list[list[str]] = []
    repo_default_no_backfill = _observe_with(
        lambda cmd, **_kw: (
            default_calls.append(list(cmd))
            or subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([snap2_node])), stderr="")
        ),
        backfill_comments=False,
    )
    assert len(default_calls) == 1
    assert not any(arg.startswith("before=") for cmd in default_calls for arg in cmd)
    assert repo_default_no_backfill["items"][0]["comments_coverage_incomplete"] is True


def test_steward_monitor_gates_backfill_and_preserves_non_age_hashes(
    steward, tmp_path, monkeypatch,  # noqa: F811
) -> None:
    from test_native_steward_managed_mode import original, storage
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.capabilities.native_chat.steward_monitor_turns import steward_monitor_snapshot_path

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _store, runtime, _provider, transport, binding, _capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    context = {"authorization_scope_id": "scope_test", "goals": [{"id": actor["goal_id"], "status": "active"}]}
    monitor = StewardMonitor(runtime)
    monkeypatch.setattr(
        monitor.bindings,
        "steward_scope",
        lambda _session: [actor["goal_id"]],
    )
    monkeypatch.setattr(
        monitor.bindings,
        "_core",
        lambda op, params: (
            {"result_hash": "sha256:portfolio-fixed"}
            if op == "collaboration.steward.monitor.observation"
            else ChatConversationBindings._core(op, params)
        ),
    )

    head = "c" * 40
    gpt_decl = "model_agent · gpt-6.2-sol · OpenAI (runtime_reported, max)"
    rc_review = {
        "id": "PRR_rc_501",
        "author": {"__typename": "User", "login": "maintainer"},
        "authorAssociation": "MEMBER",
        "state": "CHANGES_REQUESTED",
        "commit": {"oid": head},
        "submittedAt": "2030-01-01T08:00:00Z",
        "body": _valid_bilingual_body(head_oid=head, verdict="REQUEST_CHANGES", reviewer=gpt_decl),
    }
    author_comment = {
        "createdAt": "2030-01-01T09:00:00Z",
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "ext-contributor"},
    }

    def _visitors(count: int) -> list[dict[str, Any]]:
        return [
            {
                "createdAt": f"2030-01-01T10:{1 + idx:02d}:00Z",
                "authorAssociation": "NONE",
                "author": {"__typename": "User", "login": f"visitor-{idx}"},
            }
            for idx in range(count)
        ]

    def _pr_node(*, updated_at: str, comments_conn: dict[str, Any]) -> dict[str, Any]:
        node = _graphql_node(
            _sample_pr(
                number=501,
                head_oid=head,
                author="ext-contributor",
                review_decision="CHANGES_REQUESTED",
                reviews=[rc_review],
            )
        )
        node["updatedAt"] = updated_at
        node["comments"] = comments_conn
        return node

    complete_node = _pr_node(
        updated_at="2030-01-01T10:19:00Z",
        comments_conn={
            "pageInfo": {"hasPreviousPage": False, "startCursor": "c1"},
            "nodes": [author_comment, *_visitors(19)],
        },
    )
    truncated_node = _pr_node(
        updated_at="2030-01-01T10:19:00Z",
        comments_conn={
            "pageInfo": {"hasPreviousPage": True, "startCursor": "c20"},
            "nodes": _visitors(20),
        },
    )
    updated_at_changed_node = _pr_node(
        updated_at="2030-01-01T10:30:00Z",
        comments_conn={
            "pageInfo": {"hasPreviousPage": True, "startCursor": "c20"},
            "nodes": _visitors(20),
        },
    )

    def _create_monitor(target_key: str, signal_scope: dict[str, Any] | None) -> dict[str, Any]:
        actions = transport.core.actions
        params_dict: dict[str, Any] = {
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
            "target": f"Monitor {target_key}",
            "target_key": target_key,
            "task_repository": "org/repo",
            "cadence": "every_2_hours",
            "timezone": "Asia/Shanghai",
            "stop_condition": "watch_only",
        }
        if signal_scope is not None:
            params_dict["signal_scope"] = signal_scope
        preview = actions.preview({
            "action_kind": "monitor.create",
            "summary": f"Create {target_key}",
            "normalized_parameters": params_dict,
            "context": {"kind": "goal", "goal_id": actor["goal_id"]},
            "idempotency_key": f"create-{target_key}",
        })
        assert preview["status"] == "preview_ready"
        todo_id = actions.apply(preview["proposal_id"])["proposal"]["receipt"]["resource_ids"]["todo_id"]
        return next(row for row in monitor.todos(actor) if row["todo_id"] == todo_id)

    def _collect_with_gh(todo_row: dict[str, Any], gh_runner) -> tuple[dict[str, Any], list[str], list[list[str]]]:
        seen_cmds: list[list[str]] = []
        real_subprocess_run = subprocess.run

        def wrapped(cmd, **kwargs):
            if cmd and cmd[0] == "gh":
                seen_cmds.append(list(cmd))
                return gh_runner(cmd, **kwargs)
            return real_subprocess_run(cmd, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr("loopx.capabilities.native_chat.steward_monitor.subprocess.run", wrapped)
            obs, succ = monitor._collect_signal_monitor(actor, context, todo_row)
        return obs, succ, seen_cmds

    # 1. Pre-#9207 persisted hash parity + unscoped (signal_scope=None) monitor:
    #    0 backfill calls, no false wakeup on truncated window, real updatedAt change wakes up.
    unscoped_todo = _create_monitor("steward-unscoped-no-backfill", None)
    pre9207_item = {
        "number": 501,
        "title": "PR #501",
        "url": "https://github.com/org/repo/pull/501",
        "headRefOid": head,
        "headRefName": "codex/test-pr",
        "baseRefName": "main",
        "isDraft": False,
        "author": {"login": "ext-contributor"},
        "reviewDecision": "CHANGES_REQUESTED",
        "mergeStateStatus": "BLOCKED",
        "mergeable": "MERGEABLE",
        "updatedAt": "2030-01-01T10:19:00Z",
        "checks_summary": {"total": 1, "pass_count": 1, "fail_count": 0, "pending_count": 0},
        "review_summary": {
            "effective_verdict": "REQUEST_CHANGES",
            "has_review_conflict": False,
            "stale_review_count": 0,
            "exact_head_reviews": [
                {
                    "id": "PRR_rc_501",
                    "url": None,
                    "author_login": "maintainer",
                    "author_is_pr_author": False,
                    "state": "CHANGES_REQUESTED",
                    "verdict": "REQUEST_CHANGES",
                    "formal_github_verdict": "REQUEST_CHANGES",
                    "author_owned_fallback": False,
                    "bilingual_valid": True,
                    "valid_verdict_review": True,
                    "reviewer_declaration": gpt_decl,
                    "submitted_at": "2030-01-01T08:00:00Z",
                }
            ],
        },
        "bypassed_gates": ["BLOCKED_MERGE_STATE"],
    }
    pre9207_unscoped_obs = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "portfolio_observation": {"result_hash": "sha256:portfolio-fixed"},
            "inbox_items": [],
            "repository_snapshot": {"status": "ok", "repository": "org/repo", "items": [pre9207_item]},
            "previous_hash": None,
        },
    )
    unscoped_todo_with_pre9207 = {**unscoped_todo, "result_hash": pre9207_unscoped_obs["result_hash"]}

    obs_unscoped_complete, succ_u1, cmds_u1 = _collect_with_gh(
        unscoped_todo_with_pre9207,
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([complete_node])), stderr=""),
    )
    assert len(cmds_u1) == 1
    assert obs_unscoped_complete["result_hash"] == pre9207_unscoped_obs["result_hash"]
    assert obs_unscoped_complete["material_change"] is False
    assert obs_unscoped_complete["observation_incomplete"] is False
    assert succ_u1 == []

    # Truncated suffix (even if backfill RPC would fail!) executes 0 backfill RPCs and keeps hash unchanged.
    def _fail_if_backfill(node_to_return):
        def _runner(cmd, **_kw):
            if any(arg.startswith("before=") for arg in cmd):
                raise AssertionError("this fixture must not run a backfill RPC")
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([node_to_return])), stderr="")
        return _runner

    obs_unscoped_truncated, succ_u2, cmds_u2 = _collect_with_gh(
        unscoped_todo_with_pre9207,
        _fail_if_backfill(truncated_node),
    )
    assert len(cmds_u2) == 1
    assert obs_unscoped_truncated["result_hash"] == pre9207_unscoped_obs["result_hash"]
    assert obs_unscoped_truncated["material_change"] is False
    assert obs_unscoped_truncated["observation_incomplete"] is False
    assert succ_u2 == []

    # Real updatedAt change wakes up unscoped monitor with --next-agent-todo.
    obs_unscoped_updated, succ_u3, _ = _collect_with_gh(
        unscoped_todo_with_pre9207,
        _fail_if_backfill(updated_at_changed_node),
    )
    assert obs_unscoped_updated["material_change"] is True
    assert obs_unscoped_updated["observation_incomplete"] is False
    assert "--next-agent-todo" in succ_u3

    # 2. Scoped monitor WITHOUT min_stalled_hours: 0 backfill calls, pre-#9207 parity, no false wakeup,
    #    and real updatedAt change wakes up.
    no_age_scope = {
        "schema_version": "steward_signal_monitor_scope_v0",
        "include_portfolio": False,
        "include_inbox": False,
        "pr_filter": {
            "exclude_drafts": True,
            "effective_verdicts": ["REQUEST_CHANGES"],
            "author_mode": "exclude",
            "authors": ["maintainer"],
        },
    }
    no_age_todo = _create_monitor("steward-no-age-no-backfill", no_age_scope)
    pre9207_no_age_obs = ChatConversationBindings._core(
        "collaboration.steward.monitor.signal_observation",
        {
            "signal_scope": no_age_scope,
            "repository_snapshot": {"status": "ok", "repository": "org/repo", "items": [pre9207_item]},
            "observed_at": "2030-01-01T11:00:00Z",
            "previous_hash": None,
        },
    )
    no_age_todo_with_pre = {**no_age_todo, "result_hash": pre9207_no_age_obs["result_hash"]}

    with monkeypatch.context() as provider:
        provider.setattr(
            "loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
            lambda _repo: {"status": "ok", "repository": "org/repo", "items": [pre9207_item]},
        )
        legacy_obs, legacy_succ = monitor._collect_signal_monitor(actor, context, no_age_todo_with_pre)
    assert legacy_obs["observation_incomplete"] is False
    assert legacy_obs["result_hash"] == pre9207_no_age_obs["result_hash"]
    assert legacy_obs["material_change"] is False
    assert legacy_succ == []

    obs_na1, succ_na1, cmds_na1 = _collect_with_gh(no_age_todo_with_pre, _fail_if_backfill(truncated_node))
    assert len(cmds_na1) == 1
    assert obs_na1["result_hash"] == pre9207_no_age_obs["result_hash"]
    assert obs_na1["material_change"] is False
    assert obs_na1["observation_incomplete"] is False
    assert succ_na1 == []

    obs_na_rec, succ_na_rec, cmds_na_rec = _collect_with_gh(no_age_todo_with_pre, _fail_if_backfill(complete_node))
    assert len(cmds_na_rec) == 1
    assert obs_na_rec["result_hash"] == pre9207_no_age_obs["result_hash"]
    assert obs_na_rec["material_change"] is False
    assert succ_na_rec == []

    obs_na2, succ_na2, _ = _collect_with_gh(no_age_todo_with_pre, _fail_if_backfill(updated_at_changed_node))
    assert obs_na2["material_change"] is True
    assert obs_na2["observation_incomplete"] is False
    assert "--next-agent-todo" in succ_na2

    snapshot_path = steward_monitor_snapshot_path(
        runtime.coordination_runtime_root, actor["goal_id"], no_age_todo["todo_id"],
    )
    verified_snapshot = snapshot_path.read_bytes()
    for items in (None, [None], [{"title": "Missing number"}], [pre9207_item, pre9207_item]):
        with monkeypatch.context() as provider:
            provider.setattr(
                "loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
                lambda _repo, items=items: {"status": "ok", "repository": "org/repo", "items": items},
            )
            with pytest.raises(RuntimeError, match="temporarily unavailable"):
                monitor._collect_signal_monitor(
                    actor, context, {**no_age_todo, "result_hash": obs_na2["result_hash"]},
                )
        assert snapshot_path.read_bytes() == verified_snapshot

    # 3. Scoped monitor WITH min_stalled_hours > 0 DOES invoke backfill RPC and preserves state on failure.
    age_scope = {
        **no_age_scope,
        "pr_filter": {**no_age_scope["pr_filter"], "min_stalled_hours": 24},
    }
    age_todo = _create_monitor("steward-age-backfill", age_scope)

    # A lower-numbered draft used to consume the remaining read window.
    elapsed = 0
    pr_nodes = [
        {**truncated_node, "number": 500, "isDraft": True},
        truncated_node,
        *({**complete_node, "number": number, "isDraft": True} for number in (502, 503, 504)),
    ]
    pr_pages = iter(pr_nodes)
    history = []

    def _age_backfill_runner(cmd, **_kw):
        nonlocal elapsed
        if any(arg == "before=c20" for arg in cmd):
            elapsed += 5
            comments = {"pageInfo": {"hasPreviousPage": False, "startCursor": "c1"}, "nodes": [author_comment]}
            payload = {"data": {"repository": {"pullRequest": _older_comment_row(truncated_node, comments)}}}
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        elapsed += 19
        page = _graphql_page(
            [next(pr_pages)], has_next_page=elapsed < 95, end_cursor=f"page-{elapsed}", total_count=5, history=history,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(page), stderr="")

    with monkeypatch.context() as clock:
        clock.setattr("loopx.capabilities.native_chat.github_pr_evidence.monotonic", lambda: elapsed)
        obs_age, succ_age, cmds_age = _collect_with_gh(age_todo, _age_backfill_runner)
    assert len(cmds_age) == 6
    backfill_targets = [arg for cmd in cmds_age for arg in cmd if arg.startswith("number=")]
    assert backfill_targets == ["number=501"]
    assert obs_age["material_change"] is True
    assert obs_age["observation_incomplete"] is False
    assert obs_age["scoped_repository_items"][0]["stalled_bucket"] == "awaiting_maintainer_reply"
    assert "--next-agent-todo" in succ_age

    # The PR reaches 24 hours during the last five seconds reserved for backfill.
    crossing_scope = {
        **age_scope,
        "pr_filter": {**age_scope["pr_filter"], "author_mode": "include", "authors": ["ext-contributor"]},
    }
    crossing_todo = _create_monitor("steward-age-crosses-during-backfill", crossing_scope)
    started_at = datetime(2030, 1, 2, 7, 58, 20, tzinfo=timezone.utc)
    elapsed = 0
    pr_pages = iter(pr_nodes)
    history.clear()
    with monkeypatch.context() as clock:
        clock.setattr("loopx.capabilities.native_chat.github_pr_evidence.monotonic", lambda: elapsed)
        clock.setattr(
            "loopx.capabilities.native_chat.steward_monitor_signals.now_utc",
            lambda: started_at + timedelta(seconds=elapsed),
        )
        crossed, _, crossed_cmds = _collect_with_gh(crossing_todo, _age_backfill_runner)
    assert [arg for cmd in crossed_cmds for arg in cmd if arg.startswith("number=")] == ["number=501"]
    assert crossed["observation_incomplete"] is False
    assert crossed["scoped_repository_items"][0]["stalled_threshold_met"] is True
    assert crossed["scoped_repository_items"][0]["stalled_bucket"] == "24h_to_72h"

    # And backfill failure on age-filtered monitor fails closed (RuntimeError -> no hash overwrite).
    age_todo_with_hash = {**age_todo, "result_hash": obs_age["result_hash"]}
    age_snapshot_path = steward_monitor_snapshot_path(
        runtime.coordination_runtime_root, actor["goal_id"], age_todo["todo_id"],
    )
    verified_age_snapshot = age_snapshot_path.read_bytes()

    def _read_review_times(reviews, **metadata):
        def runner(cmd, **_kw):
            assert all(not arg.startswith("before=") for arg in cmd)
            query = next(arg for arg in cmd if arg.startswith("query="))
            assert "...StewardReviewPage" in query
            review_fields = query.split("fragment StewardReviewPage on PullRequestReviewConnection {", 1)[1]
            assert "totalCount" in review_fields and "hasPreviousPage" in review_fields
            node = {**complete_node, "reviews": _graphql_reviews(reviews, **metadata)}
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(_graphql_page([node])), stderr="",
            )
        return runner

    for unresolved_reviews in (
        [{**rc_review, "submittedAt": None}],
        [{**rc_review, "submittedAt": "unreadable"}],
        [{**rc_review, "submittedAt": "0001-01-01T00:00:00+01:00"}],
        [rc_review, {**rc_review, "id": "PRR_undated", "submittedAt": None}],
    ):
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            _collect_with_gh(age_todo_with_hash, _read_review_times(unresolved_reviews))
        assert age_snapshot_path.read_bytes() == verified_age_snapshot

    for reviews, metadata in (
        ([], {"pageInfo": {"hasPreviousPage": True}, "totalCount": 21}),
        ([None], {}),
        ([rc_review], {"pageInfo": {"hasPreviousPage": "false"}}),
        ([rc_review], {"totalCount": 2}),
        ([rc_review, rc_review], {}),
        ([{**rc_review, "commit": None}], {}),
    ):
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            _collect_with_gh(age_todo_with_hash, _read_review_times(reviews, **metadata))
        assert age_snapshot_path.read_bytes() == verified_age_snapshot

    stale, _, _ = _collect_with_gh(
        age_todo_with_hash, _read_review_times([{**rc_review, "commit": {"oid": "b" * 40}}]),
    )
    assert stale["observation_incomplete"] is False
    assert stale["material_change"] is True
    assert stale["scoped_repository_items"] == []

    maintainer_reply = {
        "author": {"__typename": "User", "login": "maintainer"},
        "authorAssociation": "MEMBER",
        "createdAt": "2030-01-01T10:00:00Z",
    }

    def _with_maintainer_reply(reply):
        return {**complete_node, "comments": {
            **complete_node["comments"], "nodes": [author_comment, reply],
        }}

    with monkeypatch.context() as clock:
        clock.setattr(
            "loopx.capabilities.native_chat.steward_monitor_signals.now_utc",
            lambda: datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc),
        )
        known, _, _ = _collect_with_gh(
            age_todo, _fail_if_backfill(_with_maintainer_reply(maintainer_reply)),
        )
        assert known["observation_incomplete"] is False
        assert known["scoped_repository_items"] == []
        known_snapshot = age_snapshot_path.read_bytes()
        for timestamp in (None, "invalid"):
            with pytest.raises(RuntimeError, match="temporarily unavailable"):
                _collect_with_gh(
                    {**age_todo, "result_hash": known["result_hash"]},
                    _fail_if_backfill(_with_maintainer_reply({**maintainer_reply, "createdAt": timestamp})),
                )
            assert age_snapshot_path.read_bytes() == known_snapshot

        undated_review = {
            "author": maintainer_reply["author"], "authorAssociation": "MEMBER",
            "id": "PRR_comment_undated", "state": "COMMENTED", "commit": {"oid": head}, "submittedAt": None,
        }
        node = {**complete_node, "reviews": _graphql_reviews([rc_review, undated_review])}
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            _collect_with_gh(
                {**age_todo, "result_hash": known["result_hash"]}, _fail_if_backfill(node),
            )
        assert age_snapshot_path.read_bytes() == known_snapshot

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        _collect_with_gh(
            age_todo_with_hash,
            lambda cmd, **_kw: (
                subprocess.CompletedProcess(cmd, 1, stdout="", stderr="502")
                if any(arg.startswith("before=") for arg in cmd)
                else subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([truncated_node])), stderr="")
            ),
        )
