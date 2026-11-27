"""Tests for GitHub PR exact-head review signals and protected Steward PR merges."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.github_pr_evidence import (
    _normalize_graphql_pr_node,
    inspect_github_pull_request,
    parse_github_pr_target,
    summarize_github_pr_signal,
)
from loopx.capabilities.native_chat.steward_monitor import _observe_github_repository_prs
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
    nodes: list[dict[str, Any]], *, has_next_page: bool = False, end_cursor: str | None = None
) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            }
        }
    }


def _graphql_node(row: dict[str, Any], *, contexts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    copied = dict(row)
    default_contexts = copied.pop("statusCheckRollup", [])
    rollup_contexts = contexts if contexts is not None else default_contexts
    copied["reviews"] = {"nodes": copied.get("reviews") or []}
    copied["commits"] = {
        "nodes": [
            {
                "commit": {
                    "statusCheckRollup": {
                        "contexts": {
                            "nodes": rollup_contexts,
                        }
                    }
                }
            }
        ]
    }
    return copied


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

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["gh", "api", "graphql"]
        cursor_args = [arg for arg in cmd if arg.startswith("cursor=")]
        cursor = cursor_args[0].split("=", 1)[1] if cursor_args else None
        seen_cursors.append(cursor)
        if cursor is None:
            body = _graphql_page([pr_101], has_next_page=True, end_cursor="cursor-page-2")
        else:
            assert cursor == "cursor-page-2"
            # Include duplicate PR 101 across page shift + new PR 99 to test deduplication & sorting.
            body = _graphql_page([pr_101, pr_99], has_next_page=False, end_cursor="cursor-end")
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

    def fake_run(cmd, **_kwargs):
        cursor = next(remaining_cursors)
        body = _graphql_page([_graphql_node(_sample_pr())], has_next_page=True, end_cursor=cursor)
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
    first = _graphql_page([_graphql_node(_sample_pr(number=1))], has_next_page=True, end_cursor="p2")
    terminal = _graphql_page([_graphql_node(_sample_pr(number=2))])
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
    assert plain_norm["commentsHasPreviousPage"] is False
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
            body = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasPreviousPage": False, "startCursor": "c1"},
                                "nodes": [author_comment],
                            }
                        }
                    }
                }
            }
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
            body = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasPreviousPage": True, "startCursor": "c0"},
                                "nodes": [author_comment, maintainer_comment],
                            }
                        }
                    }
                }
            }
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

    def run_exhausted(cmd, **_kwargs):
        before_args = [arg.split("=", 1)[1] for arg in cmd if arg.startswith("before=")]
        if before_args:
            cursor = before_args[0]
            backfill_cursors.append(cursor)
            next_map = {"c20": ("c70", "11"), "c70": ("c120", "12"), "c120": ("c170", "13")}
            next_cursor, hour = next_map[cursor]
            body = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasPreviousPage": True, "startCursor": next_cursor},
                                "nodes": _visitor_comments(5, prefix_hour=hour),
                            }
                        }
                    }
                }
            }
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
                raise AssertionError("backfill RPC must not run without min_stalled_hours > 0")
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

    # 3. Scoped monitor WITH min_stalled_hours > 0 DOES invoke backfill RPC and preserves state on failure.
    age_scope = {
        **no_age_scope,
        "pr_filter": {**no_age_scope["pr_filter"], "min_stalled_hours": 24},
    }
    age_todo = _create_monitor("steward-age-backfill", age_scope)

    def _age_backfill_runner(cmd, **_kw):
        if any(arg == "before=c20" for arg in cmd):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "comments": {
                                        "pageInfo": {"hasPreviousPage": False, "startCursor": "c1"},
                                        "nodes": [author_comment],
                                    }
                                }
                            }
                        }
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([truncated_node])), stderr="")

    obs_age, succ_age, cmds_age = _collect_with_gh(age_todo, _age_backfill_runner)
    assert len(cmds_age) == 2
    assert any("before=c20" in cmd for cmd in cmds_age)
    assert obs_age["material_change"] is True
    assert obs_age["observation_incomplete"] is False
    assert obs_age["scoped_repository_items"][0]["stalled_bucket"] == "awaiting_maintainer_reply"
    assert "--next-agent-todo" in succ_age

    # And backfill failure on age-filtered monitor fails closed (RuntimeError -> no hash overwrite).
    age_todo_with_hash = {**age_todo, "result_hash": obs_age["result_hash"]}
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        _collect_with_gh(
            age_todo_with_hash,
            lambda cmd, **_kw: (
                subprocess.CompletedProcess(cmd, 1, stdout="", stderr="502")
                if any(arg.startswith("before=") for arg in cmd)
                else subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_graphql_page([truncated_node])), stderr="")
            ),
        )
