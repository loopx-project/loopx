"""Tests for GitHub PR exact-head review signals and protected Steward PR merges."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from loopx.capabilities.native_chat.github_pr_evidence import (
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
    gql_node["comments"] = {"nodes": [{"createdAt": "2030-01-01T12:00:00Z", "author": {"login": "ext-contributor"}}]}
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
    assert item["review_summary"]["latest_request_changes_review_id"] == "PRR_rc4"
