#!/usr/bin/env python3
"""Smoke-test authority-gated, verified issue-fix reviewer notification."""

from __future__ import annotations

import errno
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.issue_fix.reviewer_request import ISSUE_FIX_REVIEWER_REQUEST_SCHEMA_VERSION  # noqa: E402
from loopx.capabilities.issue_fix.reviewer_cli import (  # noqa: E402
    handle_issue_fix_reviewer_command,
)
from loopx.capabilities.issue_fix.cli import (  # noqa: E402
    _materialize_goal_reviewer_notification_lifecycle,
)
from loopx.capabilities.issue_fix.reviewer_notification import (  # noqa: E402
    reviewer_notification_idempotency_key,
    reviewer_notification_queue_from_state,
    reviewer_notification_receipts_from_state,
    with_reviewer_notification_state,
    with_reviewer_notification_receipts,
)
from loopx.capabilities.issue_fix.reviewer_notification_drain import (  # noqa: E402
    drain_issue_fix_reviewer_notification_queue,
)
from loopx.extensions.process_runtime import remove_owned_tree  # noqa: E402
from loopx.extensions.lark.reviewer_notification import (  # noqa: E402
    lark_reviewer_notification_sink,
)
from loopx.capabilities.issue_fix.pr_lifecycle import (  # noqa: E402
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from loopx.capabilities.issue_fix.reviewer_recommendation import (  # noqa: E402
    ISSUE_FIX_REVIEWER_SOURCES_INPUT_SCHEMA_VERSION,
)
from loopx.domain_packs.issue_fix import (  # noqa: E402
    default_issue_fix_domain_state_ledger_path,
    persist_issue_fix_reviewer_notification_receipts,
    persist_issue_fix_reviewer_notification_state,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from loopx.extensions.bundled import bundled_extension_manifest  # noqa: E402
from loopx.extensions.runtime import (  # noqa: E402
    default_extension_state_file,
    install_extension,
)
from examples.issue_fix_reviewer_notification_test_support import (  # noqa: E402
    build_issue_fix_reviewer_request_packet,
)

PRIVATE_PATTERNS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/"),
    re.compile(r"/tmp/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
    re.compile(r"oc_[A-Za-z0-9_-]+"),
    re.compile(r"ou_[A-Za-z0-9_-]+"),
    re.compile(r"private-lark-profile"),
)


def run_git(repo: Path, *args: str, author: str = "Fixture Author") -> None:
    login = author.lower().replace(" ", "-")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{login}@users.noreply.github.com",
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": f"{login}@users.noreply.github.com",
    }
    subprocess.run(
        ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str, *, author: str) -> None:
    run_git(repo, "add", "-A", author=author)
    run_git(repo, "commit", "-m", message, author=author)


def reviewer_comment(
    login: str = "service-owner",
    *,
    url: str = "https://github.com/owner/repo/pull/42#issuecomment-1001",
) -> dict[str, Any]:
    return {
        "author": {"login": "current-author"},
        "body": (
            f"@{login} could you please review?\n\n"
            "<!-- loopx: issue-fix-reviewer-notification "
            f"reviewer=@{login} -->"
        ),
        "url": url,
    }


def semantic_reviewer_comment(
    login: str = "Service-Owner",
    *,
    body: str | None = None,
    url: str = "https://github.com/owner/repo/pull/42#issuecomment-1002",
) -> dict[str, Any]:
    return {
        "author": {"login": "current-author"},
        "body": body
        or (
            f"@{login} could you please take a look when convenient? Thanks! "
            "@fallback-owner FYI."
        ),
        "url": url,
    }


def metadata(
    *,
    requested: list[str] | None = None,
    comments: list[dict[str, Any]] | None = None,
    reviewed: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "author": {"login": "current-author"},
        "closingIssuesReferences": [
            {
                "number": 40,
                "title": "Consistency check accepts unsupported file URIs",
            }
        ],
        "comments": comments or [],
        "isDraft": False,
        "reviewRequests": [{"login": login} for login in (requested or [])],
        "reviews": [{"author": {"login": login}} for login in (reviewed or [])],
        "reviewDecision": "REVIEW_REQUIRED",
        "state": "OPEN",
        "title": "fix: reject file URI for consistency check",
        "url": "https://github.com/owner/repo/pull/42",
    }


class FakeGitHubRunner:
    def __init__(
        self,
        *,
        before: dict[str, Any],
        after: dict[str, Any] | None = None,
        edit_returncode: int = 0,
        edit_stderr: str = "",
        comment_returncode: int = 0,
        comment_stdout: str = "",
    ) -> None:
        self.before = before
        self.after = after if after is not None else before
        self.edit_returncode = edit_returncode
        self.edit_stderr = edit_stderr
        self.comment_returncode = comment_returncode
        self.comment_stdout = comment_stdout
        self.calls: list[list[str]] = []
        self.edits = 0
        self.comments = 0

    def __call__(self, args: list[str]) -> dict[str, Any]:
        command = list(args)
        self.calls.append(command)
        if command[:3] == ["gh", "pr", "view"]:
            payload = self.after if self.edits or self.comments else self.before
            return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}
        if command[:3] == ["gh", "pr", "edit"]:
            self.edits += 1
            return {
                "returncode": self.edit_returncode,
                "stdout": "",
                "stderr": self.edit_stderr,
            }
        if command[:3] == ["gh", "pr", "comment"]:
            self.comments += 1
            return {
                "returncode": self.comment_returncode,
                "stdout": self.comment_stdout,
                "stderr": (
                    "comment provider failure" if self.comment_returncode else ""
                ),
            }
        raise AssertionError(command)


class FakeCombinedRunner:
    def __init__(self, github: FakeGitHubRunner) -> None:
        self.github = github
        self.lark_calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> dict[str, Any]:
        command = list(args)
        if command[0] == "gh":
            return self.github(command)
        if command[-4:] == ["auth", "status", "--verify", "--json"]:
            self.lark_calls.append(command)
            if command[2] == "fixture-reader-profile":
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {"identities": {"user": {"available": True, "verified": True}}}
                    ),
                    "stderr": "",
                }
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "identities": {
                            "bot": {
                                "available": True,
                                "verified": True,
                                "appName": "Project Review Bot",
                            }
                        }
                    }
                ),
                "stderr": "",
            }
        if "chat.members" in command and "get" in command:
            self.lark_calls.append(command)
            return {
                "returncode": 0,
                "stdout": json.dumps({"items": [{"member_id": "ou_private_member"}]}),
                "stderr": "",
            }
        if "+messages-search" in command:
            self.lark_calls.append(command)
            return {
                "returncode": 0,
                "stdout": json.dumps({"items": [{"body": {"content": "another PR"}}]}),
                "stderr": "",
            }
        if "+messages-send" in command:
            self.lark_calls.append(command)
            if "--dry-run" in command:
                content = command[command.index("--content") + 1]
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {"api": [{"body": {"content": content}}]}
                    ),
                    "stderr": "",
                }
            return {
                "returncode": 0,
                "stdout": json.dumps({"data": {"message_id": "om_fixture"}}),
                "stderr": "",
            }
        if "+messages-mget" in command:
            self.lark_calls.append(command)
            send_call = next(
                call for call in self.lark_calls if "+messages-send" in call
            )
            content = send_call[send_call.index("--content") + 1]
            outbound_text = json.loads(content)["text"]
            readback_content = json.dumps(
                {
                    "text": re.sub(
                        r'<at open_id="ou_private_member">.*?</at>',
                        "@_user_1",
                        outbound_text,
                    )
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            assert "请帮忙 review PR #42（修复 #40）" in content, content
            assert "reject file URI for consistency check" in content, content
            assert "loopx-reviewer-notification" not in content, content
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "items": [
                            {
                                "message_id": "om_fixture",
                                "body": {"content": readback_content},
                                "mentions": [
                                    {
                                        "key": "@_user_1",
                                        "id": {"open_id": "ou_private_member"},
                                        "name": "Service Owner",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                "stderr": "",
            }
        raise AssertionError(command)


def assert_public_safe(packet: dict[str, Any]) -> None:
    text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    for pattern in PRIVATE_PATTERNS:
        assert not pattern.search(text), pattern.pattern
    assert "@users.noreply.github.com" not in text
    assert "provider failure" not in text
    assert packet["local_paths_captured"] is False
    assert packet["raw_provider_payload_captured"] is False
    assert packet["raw_git_output_captured"] is False
    assert packet["commit_emails_captured"] is False


def fake_notification_adapter(**kwargs: Any) -> dict[str, Any]:
    assert kwargs["execute"] is False
    return {
        "ok": True,
        "schema_version": "issue_fix_reviewer_notification_sink_result_v0",
        "sink_kind": "fixture_channel",
        "status": "preview_ready",
        "reviewer_handles": list(kwargs["reviewer_handles"]),
        "resolved_reviewer_count": len(kwargs["reviewer_handles"]),
        "idempotency_key": None,
        "identity_scope": "project_dedicated",
        "external_write_authority_asserted": False,
        "external_write_performed": False,
        "verification_performed": False,
        "notification_verified": False,
        "bot_identity_verified": False,
        "reader_identity_verified": False,
        "private_destination_captured": False,
        "private_member_ids_captured": False,
        "private_bot_profile_captured": False,
        "raw_provider_payload_captured": False,
    }


def main() -> int:
    path = Path(tempfile.mkdtemp(prefix="loopx-reviewer-request-"))
    try:
        run_git(path, "init", "-b", "main")
        write(
            path / ".github/CODEOWNERS",
            (
                "* @fallback-owner\n"
                "/src/service.py @current-author @release-bot @service-owner\n"
            ),
        )
        write(path / "src/service.py", "VALUE = 1\n")
        commit(path, "Add service", author="History Owner")
        run_git(path, "checkout", "-b", "feature/reviewer-request")
        write(path / "src/service.py", "VALUE = 2\n")
        commit(path, "Fix service", author="Current Author")

        runner = FakeGitHubRunner(
            before=metadata(),
            after=metadata(requested=["service-owner"]),
        )
        packet = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=runner,
        )
        assert packet["schema_version"] == ISSUE_FIX_REVIEWER_REQUEST_SCHEMA_VERSION
        assert packet["ok"] is True, packet
        assert packet["author_handle"] == "@current-author"
        assert packet["author_exclusion_verified"] is True
        assert packet["selected_reviewers"] == ["@service-owner"], packet
        assert "@release-bot" not in packet["selected_reviewers"]
        assert packet["requested_reviewers"] == ["@service-owner"], packet
        assert packet["review_request_performed"] is True
        assert packet["review_request_verified"] is True
        assert packet["notified_reviewers"] == ["@service-owner"]
        assert packet["reviewer_notification_mode"] == "formal_request"
        assert packet["reviewer_notification_verified"] is True
        assert packet["external_writes_performed"] is True
        assert packet["transition"]["decision"] == "monitor_continuation"
        assert runner.edits == 1
        assert ["--add-reviewer", "service-owner"] == runner.calls[1][-2:]
        assert_public_safe(packet)

        sinks_input = {
            "schema_version": "issue_fix_reviewer_notification_sinks_input_v0",
            "receipts": [],
            "sinks": [
                {
                    "sink_kind": "lark_chat",
                    "sink_instance_key": "fixture-review-lane",
                    "identity_scope": "project_dedicated",
                    "bot_profile": "private-lark-profile",
                    "bot_display_name": "Project Review Bot",
                    "destination_id": "oc_private_destination",
                    "reviewer_identities": {
                        "@service-owner": {
                            "member_id": "ou_private_member",
                            "display_name": "Service Owner",
                        }
                    },
                }
            ],
        }
        combined = FakeCombinedRunner(
            FakeGitHubRunner(
                before=metadata(),
                after=metadata(requested=["service-owner"]),
            )
        )
        with_secondary = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=sinks_input,
            notification_sink_adapters={"lark_chat": lark_reviewer_notification_sink},
            execute=True,
            runner=combined,
        )
        assert with_secondary["ok"] is True, with_secondary
        assert with_secondary["reviewer_notification_verified"] is True
        assert with_secondary["secondary_notification_status"] == "sent_verified"
        assert with_secondary["secondary_notification_verified"] is True
        assert with_secondary["secondary_notifications"]["receipts"]
        assert len(combined.lark_calls) == 4
        assert_public_safe(with_secondary)

        windowed_sinks_input = json.loads(json.dumps(sinks_input))
        windowed_sinks_input["delivery_policy"] = {
            "timezone": "Asia/Shanghai",
            "allowed_local_time": {"start": "09:00", "end": "21:00"},
            "outside_window": "queue_without_send",
        }
        trusted_clock_runner = FakeCombinedRunner(
            FakeGitHubRunner(
                before=metadata(),
                after=metadata(requested=["service-owner"]),
            )
        )
        trusted_clock = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=windowed_sinks_input,
            notification_sink_adapters={"lark_chat": lark_reviewer_notification_sink},
            execute=True,
            generated_at="2026-07-10T02:30:00Z",
            notification_delivery_observed_at="2026-07-10T14:30:00Z",
            runner=trusted_clock_runner,
        )
        assert trusted_clock["generated_at"] == "2026-07-10T02:30:00Z"
        assert trusted_clock["secondary_notification_status"] == ("queued_until_window")
        assert trusted_clock["secondary_notifications"]["queued_receipts"]
        assert trusted_clock_runner.lark_calls == []
        assert_public_safe(trusted_clock)

        fallback_sink_input = json.loads(json.dumps(sinks_input))
        fallback_sink_input["sinks"][0]["reviewer_identities"] = {
            "@history-owner": {
                "member_id": "ou_private_member",
                "display_name": "History Owner",
            }
        }
        fallback_combined = FakeCombinedRunner(
            FakeGitHubRunner(before=metadata(requested=["service-owner"]))
        )
        secondary_fallback = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=fallback_sink_input,
            execute=True,
            runner=fallback_combined,
        )
        assert secondary_fallback["ok"] is True, secondary_fallback
        assert secondary_fallback["selected_reviewers"] == []
        assert secondary_fallback["notified_reviewers"] == ["@service-owner"]
        assert secondary_fallback["secondary_notification_primary_targets"] == [
            "@service-owner"
        ]
        assert secondary_fallback["secondary_notification_candidate_pool"][:2] == [
            "@service-owner",
            "@history-owner",
        ], secondary_fallback
        assert secondary_fallback["secondary_notification_targets"] == []
        assert secondary_fallback["secondary_notification_fallback_used"] is False
        assert secondary_fallback["secondary_notification_status"] == (
            "skipped_reviewer_unavailable"
        )
        assert secondary_fallback["secondary_notification_verified"] is False
        assert fallback_combined.github.edits == 0
        assert fallback_combined.lark_calls == []
        assert_public_safe(secondary_fallback)

        covered_same_runner = FakeCombinedRunner(
            FakeGitHubRunner(before=metadata(requested=["service-owner"]))
        )
        covered_same = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=sinks_input,
            notification_sink_adapters={"lark_chat": lark_reviewer_notification_sink},
            execute=True,
            runner=covered_same_runner,
        )
        assert covered_same["ok"] is True, covered_same
        assert covered_same["selected_reviewers"] == []
        assert covered_same["secondary_notification_primary_targets"] == [
            "@service-owner"
        ]
        assert covered_same["secondary_notification_targets"] == ["@service-owner"]
        assert covered_same["secondary_notification_fallback_used"] is False
        assert covered_same["secondary_notification_status"] == "sent_verified"
        assert len(covered_same_runner.lark_calls) == 4
        assert_public_safe(covered_same)

        provider_neutral_fallback = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input={
                "schema_version": "issue_fix_reviewer_notification_sinks_input_v0",
                "receipts": [],
                "sinks": [
                    {
                        "sink_kind": "fixture_channel",
                        "reviewer_identities": {
                            "@history-owner": {"identity": "fixture"}
                        },
                    }
                ],
            },
            notification_sink_adapters={"fixture_channel": fake_notification_adapter},
            provider_payload=metadata(requested=["service-owner"]),
        )
        assert provider_neutral_fallback["ok"] is True, provider_neutral_fallback
        assert provider_neutral_fallback["secondary_notification_targets"] == []
        assert (
            provider_neutral_fallback["secondary_notification_fallback_used"] is False
        )
        assert provider_neutral_fallback["secondary_notification_status"] == (
            "skipped_reviewer_unavailable"
        )
        assert "secondary_notifications" not in provider_neutral_fallback
        assert_public_safe(provider_neutral_fallback)

        reviewed_combined = FakeCombinedRunner(
            FakeGitHubRunner(before=metadata(reviewed=["service-owner"]))
        )
        already_reviewed = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=sinks_input,
            notification_sink_adapters={"lark_chat": lark_reviewer_notification_sink},
            execute=True,
            runner=reviewed_combined,
        )
        assert already_reviewed["ok"] is True, already_reviewed
        assert already_reviewed["reviewer_notification_verified"] is True
        assert already_reviewed["secondary_notification_status"] == (
            "skipped_reviewer_already_reviewed"
        )
        assert already_reviewed["secondary_notification_verified"] is False
        assert reviewed_combined.lark_calls == []
        assert_public_safe(already_reviewed)

        already_runner = FakeGitHubRunner(before=metadata(requested=["service-owner"]))
        already = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=already_runner,
        )
        assert already["ok"] is True, already
        assert already["selected_reviewers"] == []
        assert already["notified_reviewers"] == ["@service-owner"]
        assert already["reviewer_notification_mode"] == "formal_request"
        assert already["reviewer_notification_verified"] is True
        assert already["external_writes_performed"] is False
        assert already["transition"]["action_kind"].endswith("already_covered")
        assert already_runner.edits == 0
        assert_public_safe(already)

        fallback_url = "https://github.com/owner/repo/pull/42#issuecomment-1001"
        permission_runner = FakeGitHubRunner(
            before=metadata(),
            after=metadata(
                comments=[
                    semantic_reviewer_comment(
                        url=fallback_url,
                        body=(
                            "@service-owner 请协助 review：本 PR 修复 "
                            "#40「Consistency check accepts unsupported file URIs」，"
                            "改动是「fix: reject file URI for consistency check」。"
                            "必要性、验证与风险已写在 PR 描述中，谢谢！"
                        ),
                    )
                ]
            ),
            edit_returncode=1,
            edit_stderr="HTTP 404: Not Found",
        )
        fallback = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=permission_runner,
        )
        assert fallback["ok"] is True, fallback
        assert fallback["selected_reviewers"] == ["@service-owner"]
        assert fallback["requested_reviewers"] == []
        assert fallback["notified_reviewers"] == ["@service-owner"]
        assert fallback["review_request_performed"] is False
        assert fallback["review_request_verified"] is False
        assert fallback["reviewer_notification_mode"] == "comment_fallback"
        assert fallback["reviewer_notification_verified"] is True
        assert fallback["comment_fallback_performed"] is True
        assert fallback["comment_fallback_verified"] is True
        assert fallback["reviewer_comment_url"] == fallback_url
        assert fallback["external_writes_performed"] is True
        assert fallback["transition"]["action_kind"].endswith(
            "comment_fallback_verified"
        )
        assert permission_runner.edits == 1
        assert permission_runner.comments == 1
        comment_call = permission_runner.calls[2]
        comment_body = comment_call[comment_call.index("--body") + 1]
        assert "@service-owner" in comment_body
        assert "#40" in comment_body
        assert "Consistency check accepts unsupported file URIs" in comment_body
        assert "fix: reject file URI for consistency check" in comment_body
        assert "必要性、验证与风险已写在 PR 描述中" in comment_body
        assert "issue-fix-reviewer-notification" not in comment_body
        assert_public_safe(fallback)

        fallback_retry_runner = FakeGitHubRunner(before=permission_runner.after)
        fallback_retry = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=fallback_retry_runner,
        )
        assert fallback_retry["ok"] is True, fallback_retry
        assert fallback_retry["selected_reviewers"] == []
        assert fallback_retry["existing_comment_notified_reviewers"] == [
            "@service-owner"
        ]
        assert fallback_retry["notified_reviewers"] == ["@service-owner"]
        assert fallback_retry["reviewer_notification_mode"] == (
            "existing_review_comment"
        )
        assert fallback_retry["reviewer_notification_verified"] is True
        assert fallback_retry["comment_fallback_performed"] is False
        assert fallback_retry["comment_fallback_verified"] is False
        assert fallback_retry["existing_comment_notification_verified"] is True
        assert fallback_retry["reviewer_comment_url"] == fallback_url
        assert fallback_retry["external_writes_performed"] is False
        assert fallback_retry["transition"]["action_kind"].endswith("already_covered")
        assert fallback_retry_runner.edits == 0
        assert fallback_retry_runner.comments == 0
        assert_public_safe(fallback_retry)

        semantic_url = "https://github.com/owner/repo/pull/42#issuecomment-1002"
        semantic_retry_runner = FakeGitHubRunner(
            before=metadata(comments=[semantic_reviewer_comment(url=semantic_url)])
        )
        semantic_retry = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=semantic_retry_runner,
        )
        assert semantic_retry["ok"] is True, semantic_retry
        assert semantic_retry["selected_reviewers"] == []
        assert semantic_retry["existing_marker_comment_notified_reviewers"] == []
        assert semantic_retry["existing_semantic_comment_notified_reviewers"] == [
            "@service-owner"
        ]
        assert semantic_retry["existing_comment_notified_reviewers"] == [
            "@service-owner"
        ]
        assert semantic_retry["notified_reviewers"] == ["@service-owner"]
        assert semantic_retry["reviewer_notification_mode"] == (
            "existing_review_comment"
        )
        assert semantic_retry["reviewer_notification_verified"] is True
        assert semantic_retry["existing_comment_notification_verified"] is True
        assert semantic_retry["comment_fallback_performed"] is False
        assert semantic_retry["comment_fallback_verified"] is False
        assert semantic_retry["reviewer_comment_url"] == semantic_url
        assert semantic_retry["external_writes_performed"] is False
        assert semantic_retry["transition"]["action_kind"].endswith("already_covered")
        assert (
            "existing explicit review-request comment"
            in semantic_retry["transition"]["reason"]
        )
        assert semantic_retry_runner.edits == 0
        assert semantic_retry_runner.comments == 0
        assert_public_safe(semantic_retry)

        multi_semantic_url = "https://github.com/owner/repo/pull/42#issuecomment-1003"
        multi_semantic_runner = FakeGitHubRunner(
            before=metadata(
                comments=[
                    semantic_reviewer_comment(
                        url=multi_semantic_url,
                        body=(
                            "Hi @service-owner @t0saki, could you please review "
                            "this focused fix when convenient?"
                        ),
                    )
                ]
            )
        )
        multi_semantic = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=multi_semantic_runner,
        )
        assert multi_semantic["ok"] is True, multi_semantic
        assert multi_semantic["selected_reviewers"] == []
        assert multi_semantic["existing_semantic_comment_notified_reviewers"] == [
            "@service-owner",
            "@t0saki",
        ]
        assert multi_semantic["reviewer_notification_mode"] == (
            "existing_review_comment"
        )
        assert multi_semantic["reviewer_comment_url"] == multi_semantic_url
        assert multi_semantic["external_writes_performed"] is False
        assert multi_semantic_runner.edits == 0
        assert multi_semantic_runner.comments == 0
        assert_public_safe(multi_semantic)

        bounded_semantic_runner = FakeGitHubRunner(
            before=metadata(
                comments=[
                    semantic_reviewer_comment(
                        body=(
                            "@fallback-owner; @service-owner, could you please "
                            "review this focused fix?"
                        ),
                    )
                ]
            )
        )
        bounded_semantic = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=bounded_semantic_runner,
        )
        assert bounded_semantic["existing_semantic_comment_notified_reviewers"] == [
            "@service-owner"
        ]
        assert bounded_semantic["external_writes_performed"] is False
        assert bounded_semantic_runner.edits == 0
        assert bounded_semantic_runner.comments == 0
        assert_public_safe(bounded_semantic)

        discussion_comment = semantic_reviewer_comment(
            login="service-owner",
            body=(
                "@service-owner thanks for the context. The release owner will "
                "decide who should review this later."
            ),
        )
        discussion_runner = FakeGitHubRunner(
            before=metadata(comments=[discussion_comment]),
            after=metadata(requested=["service-owner"], comments=[discussion_comment]),
        )
        discussion = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=discussion_runner,
        )
        assert discussion["ok"] is True, discussion
        assert discussion["existing_semantic_comment_notified_reviewers"] == []
        assert discussion["selected_reviewers"] == ["@service-owner"]
        assert discussion["review_request_verified"] is True
        assert discussion_runner.edits == 1
        assert discussion_runner.comments == 0
        assert_public_safe(discussion)

        comment_blocked_runner = FakeGitHubRunner(
            before=metadata(),
            edit_returncode=1,
            edit_stderr="HTTP 403: Resource not accessible by integration",
            comment_returncode=1,
        )
        comment_blocked = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=comment_blocked_runner,
        )
        assert comment_blocked["ok"] is False
        assert comment_blocked["blocker"] == "github_reviewer_comment_fallback_failed"
        assert comment_blocked["comment_fallback_performed"] is False
        assert comment_blocked["external_writes_performed"] is False
        assert comment_blocked_runner.edits == 1
        assert comment_blocked_runner.comments == 1
        assert_public_safe(comment_blocked)

        failed_runner = FakeGitHubRunner(
            before=metadata(),
            edit_returncode=1,
            edit_stderr="provider failure",
        )
        failed = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            execute=True,
            runner=failed_runner,
        )
        assert failed["ok"] is False
        assert failed["blocker"] == "github_review_request_failed"
        assert failed["selected_reviewers"] == ["@service-owner"]
        assert failed["external_writes_performed"] is False
        assert failed["transition"]["decision"] == "blocker"
        assert failed_runner.comments == 0
        assert_public_safe(failed)

        failed_combined = FakeCombinedRunner(
            FakeGitHubRunner(
                before=metadata(),
                edit_returncode=1,
                edit_stderr="provider failure",
            )
        )
        failed_with_lark = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=sinks_input,
            notification_sink_adapters={"lark_chat": lark_reviewer_notification_sink},
            execute=True,
            runner=failed_combined,
        )
        assert failed_with_lark["ok"] is False, failed_with_lark
        assert failed_with_lark["blocker"] == "github_review_request_failed"
        assert failed_with_lark["secondary_notification_status"] == "sent_verified"
        assert failed_with_lark["secondary_notification_verified"] is True
        assert failed_with_lark["external_writes_performed"] is True
        assert len(failed_combined.lark_calls) == 4
        assert_public_safe(failed_with_lark)

        preview = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            provider_payload=metadata(),
        )
        assert preview["ok"] is True, preview
        assert preview["selected_reviewers"] == ["@service-owner"]
        assert preview["external_write_authority_asserted"] is False
        assert preview["external_writes_performed"] is False
        assert preview["transition"]["action_kind"] == "issue_fix_request_top_reviewer"
        assert_public_safe(preview)

        reviewer_sources = {
            "schema_version": ISSUE_FIX_REVIEWER_SOURCES_INPUT_SCHEMA_VERSION,
            "sources": [
                {
                    "source_id": "public-maintainer-map",
                    "source_kind": "maintainer_map",
                    "reference": "https://github.com/owner/repo/issues/10",
                    "trust": "verified",
                    "freshness": "current",
                    "observed_at": "2026-07-10T00:00:00Z",
                    "routes": [
                        {
                            "route_id": "map-only-module",
                            "match_kind": "path_prefix",
                            "pattern": "src/map_only.py",
                            "primary_reviewers": ["@map-owner"],
                            "fallback_reviewers": ["@map-backup"],
                        }
                    ],
                }
            ],
        }
        source_preview = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            changed_files=["src/map_only.py"],
            base_ref="main",
            exclude_reviewers=["@fallback-owner"],
            reviewer_sources_input=reviewer_sources,
            provider_payload=metadata(),
        )
        assert source_preview["ok"] is True, source_preview
        assert source_preview["selected_reviewers"] == ["@map-owner"]
        assert source_preview["reviewer_source_count"] == 1
        assert source_preview["reviewer_source_refs"] == [
            "https://github.com/owner/repo/issues/10"
        ]
        source_candidate = source_preview["recommendation_candidates"][0]
        assert source_candidate["reviewer_handle"] == "@map-owner"
        assert "repository_declared_primary_contact" in source_candidate["reason_codes"]
        assert_public_safe(source_preview)

        try:
            build_issue_fix_reviewer_request_packet(
                repo_path=path,
                url="https://github.com/owner/repo/pull/42",
                base_ref="main",
                provider_payload=metadata(),
                execute=True,
                runner=FakeGitHubRunner(before=metadata()),
            )
        except ValueError as exc:
            assert "preview-only" in str(exc), exc
        else:
            raise AssertionError("execute mode must not trust supplied PR metadata")

        unsafe_preview = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
        )
        assert unsafe_preview["ok"] is False
        assert unsafe_preview["blocker"].endswith("required_for_safe_preview")
        assert unsafe_preview["selected_reviewers"] == [], unsafe_preview
        assert unsafe_preview["external_writes_performed"] is False
        assert_public_safe(unsafe_preview)

        incomplete_preview = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            provider_payload={},
        )
        assert incomplete_preview["ok"] is False
        assert incomplete_preview["blocker"] == "github_pr_author_unavailable"
        assert incomplete_preview["selected_reviewers"] == []
        assert_public_safe(incomplete_preview)

        author_only_preview = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            provider_payload={"author": {"login": "current-author"}},
        )
        assert author_only_preview["ok"] is False
        assert author_only_preview["blocker"] == "github_pr_state_unavailable"
        assert author_only_preview["selected_reviewers"] == []
        assert_public_safe(author_only_preview)

        metadata_path = path / "pr-metadata.json"
        write(metadata_path, json.dumps(metadata()))
        reviewer_sources_path = path / "reviewer-sources.json"
        write(reviewer_sources_path, json.dumps(reviewer_sources))
        cli_sinks_input = json.loads(json.dumps(sinks_input))
        cli_sinks_input["sinks"][0]["reviewer_identities"] = {
            "@map-owner": {
                "member_id": "ou_private_member",
                "display_name": "Map Owner",
            }
        }
        notification_sinks_path = path / "notification-sinks.json"
        write(notification_sinks_path, json.dumps(cli_sinks_input))
        runtime_root = path / ".runtime"
        install_extension(
            bundled_extension_manifest("loopx-lark"),
            state_file=default_extension_state_file(runtime_root),
            execute=True,
        )
        loopx_cli = [
            sys.executable, "-m", "loopx.cli", "--runtime-root",
            str(runtime_root), "--format", "json",
        ]
        reviewer_cli_args = [
            *loopx_cli,
            "issue-fix",
            "reviewer-request",
            "--url",
            "https://github.com/owner/repo/pull/42",
            "--repo-path",
            str(path),
            "--base-ref",
            "main",
            "--changed-file",
            "src/map_only.py",
            "--exclude-reviewer",
            "@fallback-owner",
            "--reviewer-sources-json",
            str(reviewer_sources_path),
            "--metadata-json",
            str(metadata_path),
            "--notification-sinks-json",
            str(notification_sinks_path),
        ]
        cli = subprocess.run(
            reviewer_cli_args,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        cli_packet = json.loads(cli.stdout)
        assert cli_packet["selected_reviewers"] == ["@map-owner"]
        assert cli_packet["reviewer_source_count"] == 1
        assert cli_packet["external_writes_performed"] is False
        assert cli_packet["secondary_notification_status"] == "preview_ready"
        assert cli_packet["extension_activation"]["required_permissions"] == [
            "lark.inbox.read",
            "lark.inbox.write",
            "lark.reply.send",
            "lark.reviewer_notification.send",
        ]
        assert (
            cli_packet["secondary_notifications"]["results"][0][
                "private_destination_captured"
            ]
            is False
        )
        assert_public_safe(cli_packet)

        goal_id = "reviewer-request-default-fixture"
        goal_config = json.loads(json.dumps(cli_sinks_input))
        goal_sink = goal_config["sinks"][0]
        goal_sink.pop("bot_profile")
        goal_sink.update(
            {
                "reader_profile": "fixture-reader-profile",
                "reader_identity": "user",
                "sender_profile": "fixture-sender-profile",
                "sender_identity": "bot",
            }
        )
        goal_config_path = (
            path / ".loopx/config/issue-fix/reviewer-notification-sinks.json"
        )
        write(goal_config_path, json.dumps(goal_config))
        reward_goal_id = "reviewer-request-reward-preview-fixture"
        reward_config_path = path / ".loopx/config/reward-memory/experiment.json"
        reward_fixture = json.loads(
            (
                ROOT / "examples/fixtures/reward-memory-ingest-event.public.json"
            ).read_text(encoding="utf-8")
        )
        reward_fixture["corpus"]["scope"]["surface_ids"] = ["reviewer_artifact.summary"]
        reward_fixture["corpus"]["freshness"] = {
            "mode": "source_truth_bound",
            "source_revision": None,
        }
        reward_fixture["standing_policy"]["scope"]["surface_ids"] = [
            "reviewer_artifact.summary"
        ]
        reward_corpus_id = reward_fixture["corpus"]["corpus_id"]
        reward_binding = reward_fixture["provider_binding"]
        reward_project_binding = {
            key: value
            for key, value in reward_binding.items()
            if key not in {"corpus_id", "scope_ref"}
        }
        reward_project_binding["corpus_scopes"] = [
            {
                "corpus_id": reward_corpus_id,
                "scope_ref": reward_binding["scope_ref"],
            }
        ]
        reward_config = {
            "schema_version": "reward_memory_experiment_config_v1",
            "project_provider_binding": reward_project_binding,
            "corpora": [
                {
                    "corpus": reward_fixture["corpus"],
                    "standing_policy": reward_fixture["standing_policy"],
                }
            ],
            "surfaces": [
                {
                    "surface_id": "reviewer_artifact.summary",
                    "adapter": reward_fixture["adapter"],
                    "corpus_ids": [reward_corpus_id],
                    "ingest_corpus_id": reward_corpus_id,
                    "recall_profile": {
                        "profile_id": "reviewer_summary_fixture_v1",
                        "mode": "function_boundary",
                        "max_queries": 1,
                        "limit": 5,
                    },
                }
            ],
            "automation": {
                "automatic_recall": True,
                "automatic_ingest": False,
                "fail_open": True,
            },
        }
        reward_config_text = json.dumps(reward_config)
        write(reward_config_path, reward_config_text)
        reward_config_digest = (
            f"sha256:{hashlib.sha256(reward_config_path.read_bytes()).hexdigest()}"
        )
        registry = path / ".loopx/registry.json"
        write(
            registry,
            json.dumps(
                {
                    "schema_version": 1,
                    "goals": [
                        {
                            "id": goal_id,
                            "repo": str(path),
                            "control_plane": {
                                "issue_fix": {
                                    "reviewer_notification": {
                                        "enabled": True,
                                        "config_path": (
                                            ".loopx/config/issue-fix/"
                                            "reviewer-notification-sinks.json"
                                        ),
                                    }
                                }
                            },
                        },
                        {
                            "id": reward_goal_id,
                            "repo": str(path),
                            "coordination": {
                                "registered_agents": ["fixture-review-agent"]
                            },
                            "control_plane": {
                                "reward_memory": {
                                    "enabled": True,
                                    "experimental": True,
                                    "config_path": (
                                        ".loopx/config/reward-memory/experiment.json"
                                    ),
                                    "enabled_agents": ["fixture-review-agent"],
                                    "config_digest": reward_config_digest,
                                    "enablement_receipts": {
                                        "fixture-review-agent": {
                                            "schema_version": "reward_memory_enablement_receipt_v0",
                                            "status": "verified",
                                            "goal_id": reward_goal_id,
                                            "agent_id": "fixture-review-agent",
                                            "config_digest": reward_config_digest,
                                            "provider_id": reward_project_binding["provider_id"],
                                            "isolation_mode": "explicit_shared",
                                            "actor_binding_verified": False,
                                            "writability_verified": True,
                                            "exact_readback_verified": True,
                                        }
                                    },
                                }
                            },
                        },
                    ],
                }
            ),
        )
        legacy_request_lifecycle_path = default_issue_fix_domain_state_ledger_path(
            project=path, goal_id=goal_id
        )
        legacy_request_row = build_issue_fix_pr_lifecycle_monitor_packet(
            url="https://github.com/owner/repo/pull/42",
            provider_payload={"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED"},
        )
        legacy_request_row["reviewer_notification_queue"] = [
            {
                "schema_version": "issue_fix_reviewer_notification_queue_receipt_v0",
                "idempotency_key": "sha256:" + "f" * 64,
                "sink_kind": "lark_chat",
                "reviewer_handles": ["@map-owner"],
                "queued_at": "2026-07-17T18:00:00Z",
                "not_before": "2026-07-18T01:00:00Z",
                "timezone": "Asia/Shanghai",
                "allowed_local_time": {"start": "09:00", "end": "21:00"},
                "status": "queued",
            }
        ]
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(legacy_request_lifecycle_path, legacy_request_row)
        goal_default_cli = subprocess.run(
            [
                *loopx_cli,
                "--registry",
                str(registry),
                "issue-fix",
                "reviewer-request",
                "--url",
                "https://github.com/owner/repo/pull/42",
                "--repo-path",
                str(path),
                "--base-ref",
                "main",
                "--changed-file",
                "src/map_only.py",
                "--exclude-reviewer",
                "@fallback-owner",
                "--reviewer-sources-json",
                str(reviewer_sources_path),
                "--metadata-json",
                str(metadata_path),
                "--goal-id",
                goal_id,
                "--project",
                str(path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        goal_default_packet = json.loads(goal_default_cli.stdout)
        assert goal_default_packet["secondary_notification_source"] == "goal_default"
        assert goal_default_packet["secondary_notification_status"] == "not_configured"
        assert goal_default_packet["secondary_notification_blocker"] == (
            "reviewer_notification_queue_v1_migration_required"
        )
        assert goal_default_packet["selected_reviewers"] == ["@map-owner"]
        assert goal_default_packet["ok"] is True
        assert goal_default_packet["review_request_performed"] is False
        legacy_request_lifecycle_path.unlink()
        assert_public_safe(goal_default_packet)

        reward_application_calls: list[dict[str, Any]] = []

        def fake_reward_application(
            base_artifact: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            reward_application_calls.append(dict(kwargs))
            config = kwargs["experiment_config"]
            assert config["automation"]["automatic_recall"] is True
            assert config["schema_version"] == "reward_memory_experiment_config_v1"
            summary = str(kwargs["reviewer_summary"])
            artifact = {
                "schema_version": "issue_fix_reviewer_artifact_v0",
                **base_artifact,
                "summary": summary,
            }
            return {
                "ok": True,
                "schema_version": (
                    "issue_fix_reviewer_artifact_reward_memory_application_v0"
                ),
                "surface_id": "reviewer_artifact.summary",
                "reviewer_artifact": artifact,
                "recall": {
                    "status": "completed",
                    "provider_call_count": 1,
                    "result_readback_verified": True,
                    "external_writes_performed": False,
                },
                "application": {
                    "status": "applied",
                    "receipt": {
                        "schema_version": "reward_memory_application_receipt_v0",
                        "current_artifact_verified": True,
                        "result_readback_verified": True,
                        "memory_ref_digests": ["sha256:" + "a" * 64],
                    },
                },
                "shared_core": "loopx.capabilities.reward_memory.application",
                "notification_gate": {
                    "status": "ready",
                    "passed": True,
                    "summary": summary,
                    "external_writes_performed": False,
                },
                "external_writes_performed": False,
            }

        reward_args = argparse.Namespace(
            issue_fix_command="reviewer-request",
            execute=False,
            metadata_json=str(metadata_path),
            notification_sinks_json=None,
            goal_id=reward_goal_id,
            project=str(path),
            url="https://github.com/owner/repo/pull/42",
            repo_path=str(path),
            changed_file=["src/map_only.py"],
            base_ref="main",
            history_limit=40,
            max_candidates=5,
            max_reviewers=1,
            exclude_reviewer=["@fallback-owner"],
            exclude_author_name=[],
            identity_map_json=None,
            reviewer_sources_json=str(reviewer_sources_path),
            agent_id="fixture-review-agent",
            reviewer_summary="修复文件 URI 校验并保留精确回读证据",
            reviewer_summary_reasoning=("当前 PR 身份、摘要语义和验证范围均已核对。"),
        )
        with patch(
            "loopx.capabilities.issue_fix.reviewer_request."
            "run_issue_fix_reviewer_artifact_automatic_reward_memory",
            side_effect=fake_reward_application,
        ):
            handled = handle_issue_fix_reviewer_command(
                reward_args,
                registry_path=registry,
                generated_at="2026-07-16T08:00:00Z",
                delivery_observed_at="2026-07-16T08:00:00Z",
            )
        assert handled is not None
        reward_preview, _ = handled
        assert len(reward_application_calls) == 1
        assert reward_preview["reward_memory_experiment_status"] == "available"
        assert reward_preview["secondary_notification_source"] == "not_configured"
        assert reward_preview["secondary_notification_status"] == "not_configured"
        assert reward_preview["reviewer_artifact_reward_memory_required"] is False
        assert reward_preview["reviewer_artifact_reward_memory_status"] == "ready"
        assert (
            reward_preview["reviewer_artifact_reward_memory_preview"][
                "notification_gate"
            ]["passed"]
            is True
        )
        assert reward_preview["external_writes_performed"] is False
        assert_public_safe(reward_preview)

        disabled_reward_config = json.loads(
            reward_config_path.read_text(encoding="utf-8")
        )
        disabled_reward_config["automation"]["automatic_recall"] = False
        write(reward_config_path, json.dumps(disabled_reward_config))
        disabled_handled = handle_issue_fix_reviewer_command(
            reward_args,
            registry_path=registry,
            generated_at="2026-07-16T08:01:00Z",
            delivery_observed_at="2026-07-16T08:01:00Z",
        )
        assert disabled_handled is not None
        disabled_preview, _ = disabled_handled
        if disabled_preview.get("reward_memory_experiment_status") == "enablement_stale":
            # Editing the local config without refreshing its registry digest is
            # an intentional fail-closed enablement boundary.
            assert "reviewer_artifact_reward_memory_preview" not in disabled_preview
        else:
            disabled_application = disabled_preview[
                "reviewer_artifact_reward_memory_preview"
            ]
            assert disabled_preview["reviewer_artifact_reward_memory_status"] == "blocked"
            assert disabled_application["automatic_recall"] is False
            assert disabled_application["recall"]["status"] == "disabled"
            assert disabled_application["telemetry"]["provider_call_count"] == 0

        lifecycle_path = default_issue_fix_domain_state_ledger_path(
            project=path,
            goal_id=goal_id,
        )
        invalid_execute_cli = subprocess.run(
            [
                *loopx_cli,
                "--registry",
                str(registry),
                "issue-fix",
                "reviewer-request",
                "--url",
                "https://github.com/owner/repo/pull/42",
                "--repo-path",
                str(path),
                "--changed-file",
                "src/map_only.py",
                "--metadata-json",
                str(metadata_path),
                "--goal-id",
                goal_id,
                "--project",
                str(path),
                "--execute",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert invalid_execute_cli.returncode != 0
        assert "preview-only" in invalid_execute_cli.stdout
        assert not lifecycle_path.exists()

        unrelated_cwd = path / "unrelated-control-plane"
        write(
            unrelated_cwd / ".loopx/registry.json",
            json.dumps({"schema_version": 1, "goals": []}),
        )
        project_registry_cli = subprocess.run(
            [
                *loopx_cli,
                "issue-fix",
                "reviewer-request",
                "--url",
                "https://github.com/owner/repo/pull/42",
                "--repo-path",
                str(path),
                "--base-ref",
                "main",
                "--changed-file",
                "src/map_only.py",
                "--exclude-reviewer",
                "@fallback-owner",
                "--reviewer-sources-json",
                str(reviewer_sources_path),
                "--metadata-json",
                str(metadata_path),
                "--goal-id",
                goal_id,
                "--project",
                str(path),
            ],
            cwd=unrelated_cwd,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert project_registry_cli.returncode == 0, project_registry_cli.stdout
        project_registry_packet = json.loads(project_registry_cli.stdout)
        assert (
            project_registry_packet["secondary_notification_source"] == "goal_default"
        )
        assert (
            project_registry_packet["secondary_notification_status"] == "preview_ready"
        )
        assert_public_safe(project_registry_packet)

        lifecycle = _materialize_goal_reviewer_notification_lifecycle(
            ledger_path=lifecycle_path,
            url="https://github.com/owner/repo/pull/42",
            provider_payload={
                "state": "OPEN",
                "reviewDecision": "REVIEW_REQUIRED",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
                "closingIssuesReferences": [{"number": 40}],
            },
        )
        assert lifecycle["observation"]["issue_ref"] == "issues_40"
        assert lifecycle["domain_state_projection"]["write_performed"] is True
        repeated_materialize = _materialize_goal_reviewer_notification_lifecycle(
            ledger_path=lifecycle_path,
            url="https://github.com/owner/repo/pull/42",
            provider_payload={
                "state": "OPEN",
                "reviewDecision": "REVIEW_REQUIRED",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
                "closingIssuesReferences": [{"number": 40}],
            },
        )
        assert (
            repeated_materialize["domain_state_projection"]["write_performed"] is False
        )
        legacy_lifecycle = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        legacy_lifecycle.pop("maintainer_correction_body_captured")
        lifecycle_path.write_text(
            json.dumps(legacy_lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        goal_execute_runner = FakeCombinedRunner(
            FakeGitHubRunner(
                before=metadata(),
                after=metadata(requested=["map-owner"]),
            )
        )
        goal_execute = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            changed_files=["src/map_only.py"],
            base_ref="main",
            exclude_reviewers=["@fallback-owner"],
            reviewer_sources_input=reviewer_sources,
            notification_sinks_input=goal_config,
            execute=True,
            runner=goal_execute_runner,
        )
        assert goal_execute["secondary_notification_status"] == "sent_verified"
        assert len(goal_execute_runner.lark_calls) == 8
        receipt = goal_execute["secondary_notifications"]["receipts"][0]
        receipt_write = persist_issue_fix_reviewer_notification_receipts(
            lifecycle_path,
            legacy_lifecycle,
            [receipt],
        )
        assert receipt_write["write_performed"] is True, receipt_write
        stored_lifecycle = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert stored_lifecycle["reviewer_notification_receipts"] == [receipt]
        assert stored_lifecycle["maintainer_correction_body_captured"] is False
        explicit_scoped_cli = subprocess.run(
            [
                *loopx_cli,
                "--registry",
                str(registry),
                "issue-fix",
                "reviewer-request",
                "--url",
                "https://github.com/owner/repo/pull/42",
                "--repo-path",
                str(path),
                "--base-ref",
                "main",
                "--changed-file",
                "src/map_only.py",
                "--exclude-reviewer",
                "@fallback-owner",
                "--reviewer-sources-json",
                str(reviewer_sources_path),
                "--metadata-json",
                str(metadata_path),
                "--notification-sinks-json",
                str(goal_config_path),
                "--goal-id",
                goal_id,
                "--project",
                str(path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        explicit_scoped_packet = json.loads(explicit_scoped_cli.stdout)
        assert explicit_scoped_packet["secondary_notification_source"] == "explicit"
        assert (
            explicit_scoped_packet["secondary_notification_status"]
            == "already_notified"
        )
        assert explicit_scoped_packet["external_writes_performed"] is False
        assert_public_safe(explicit_scoped_packet)
        unsafe_lifecycle = json.loads(json.dumps(stored_lifecycle))
        unsafe_lifecycle["maintainer_correction_body_captured"] = True
        try:
            persist_issue_fix_reviewer_notification_receipts(
                lifecycle_path,
                unsafe_lifecycle,
                ["sha256:" + "f" * 64],
            )
        except ValueError as exc:
            assert "maintainer_correction_body_captured=false" in str(exc)
        else:
            raise AssertionError("truthy legacy capture flag must remain blocked")
        later_monitor = build_issue_fix_pr_lifecycle_monitor_packet(
            url="https://github.com/owner/repo/pull/42",
            issue_ref="issues_40",
            provider_payload={
                "state": "OPEN",
                "reviewDecision": "REVIEW_REQUIRED",
                "mergeStateStatus": "BLOCKED",
                "statusCheckRollup": [{"name": "focused", "conclusion": "SUCCESS"}],
            },
        )
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(lifecycle_path, later_monitor)
        stored_lifecycle = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert stored_lifecycle["reviewer_notification_receipts"] == [receipt]
        retry_config = with_reviewer_notification_receipts(
            goal_config,
            reviewer_notification_receipts_from_state(stored_lifecycle),
        )
        restarted_runner = FakeCombinedRunner(
            FakeGitHubRunner(before=metadata(requested=["map-owner"]))
        )
        restarted = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            changed_files=["src/map_only.py"],
            base_ref="main",
            exclude_reviewers=["@fallback-owner"],
            reviewer_sources_input=reviewer_sources,
            notification_sinks_input=retry_config,
            execute=True,
            runner=restarted_runner,
        )
        assert restarted["secondary_notification_status"] == "already_notified"
        assert restarted["secondary_notification_verified"] is True
        assert restarted_runner.lark_calls == []
        repeated_receipt_write = persist_issue_fix_reviewer_notification_receipts(
            lifecycle_path,
            stored_lifecycle,
            [receipt],
        )
        assert repeated_receipt_write["status"] == "unchanged"
        assert repeated_receipt_write["write_performed"] is False
        assert_public_safe(restarted)

        queued_key = "sha256:" + "b" * 64
        queued_receipt = {
            "schema_version": "issue_fix_reviewer_notification_queue_receipt_v1",
            "idempotency_key": queued_key,
            "sink_kind": "lark_chat",
            "reviewer_handles": ["@map-owner"],
            "message_summary": "修复一致性检查对文件 URI 的错误接受行为",
            "summary_policy_status": "reward_memory_verified",
            "queued_at": "2026-07-10T14:30:00Z",
            "not_before": "2026-07-11T01:00:00Z",
            "timezone": "Asia/Shanghai",
            "allowed_local_time": {"start": "09:00", "end": "21:00"},
            "status": "queued",
        }
        queue_write = persist_issue_fix_reviewer_notification_state(
            lifecycle_path,
            stored_lifecycle,
            receipts=[],
            queued_receipts=[queued_receipt],
        )
        assert queue_write["write_performed"] is True, queue_write
        stored_with_queue = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert reviewer_notification_queue_from_state(stored_with_queue) == [
            queued_receipt
        ]
        restored_config = with_reviewer_notification_state(
            goal_config,
            reviewer_notification_receipts_from_state(stored_with_queue),
            reviewer_notification_queue_from_state(stored_with_queue),
        )
        assert restored_config["queued_receipts"] == [queued_receipt]
        repeated_queue_write = persist_issue_fix_reviewer_notification_state(
            lifecycle_path,
            stored_with_queue,
            receipts=[],
            queued_receipts=[queued_receipt],
        )
        assert repeated_queue_write["status"] == "unchanged"
        assert repeated_queue_write["write_performed"] is False

        queue_clear = persist_issue_fix_reviewer_notification_state(
            lifecycle_path,
            stored_with_queue,
            receipts=[queued_key],
            queued_receipts=[],
        )
        assert queue_clear["write_performed"] is True, queue_clear
        stored_after_send = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert queued_key in stored_after_send["reviewer_notification_receipts"]
        assert stored_after_send["reviewer_notification_queue"] == []

        replacement_a = {
            **queued_receipt,
            "idempotency_key": "sha256:" + "c" * 64,
            "reviewer_handles": ["@service-owner"],
        }
        replacement_b = {
            **queued_receipt,
            "idempotency_key": "sha256:" + "d" * 64,
            "reviewer_handles": ["@history-owner"],
        }
        persist_issue_fix_reviewer_notification_state(
            lifecycle_path,
            stored_after_send,
            receipts=[],
            queued_receipts=[replacement_a],
        )
        stored_with_replacement_a = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        queue_replace = persist_issue_fix_reviewer_notification_state(
            lifecycle_path,
            stored_with_replacement_a,
            receipts=[],
            queued_receipts=[replacement_b],
            replace_queued_receipts=True,
        )
        assert queue_replace["write_performed"] is True, queue_replace
        assert queue_replace["queue_reconciliation"]["mode"] == "replace_unsent"
        assert queue_replace["queue_reconciliation"]["cancelled_count"] == 1
        stored_with_replacement_b = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert reviewer_notification_queue_from_state(stored_with_replacement_b) == [
            replacement_b
        ]
        queue_cancel = persist_issue_fix_reviewer_notification_state(
            lifecycle_path,
            stored_with_replacement_b,
            receipts=[],
            queued_receipts=[],
            replace_queued_receipts=True,
        )
        assert queue_cancel["write_performed"] is True, queue_cancel
        assert queue_cancel["queue_reconciliation"]["cancelled_count"] == 1
        stored_after_cancel = json.loads(
            lifecycle_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert stored_after_cancel["reviewer_notification_queue"] == []

        drain_ledger = path / ".loopx/domain-state/drain/pr-lifecycle.jsonl"
        drain_calls: list[dict[str, Any]] = []
        drain_sink_two = {
            **goal_sink,
            "sink_instance_key": " openviking-reviewer-group-secondary ",
        }
        drain_removed_sink = {
            **goal_sink,
            "sink_instance_key": "removed-reviewer-lane",
        }
        drain_goal_config = {
            **goal_config,
            "sinks": [goal_sink, drain_sink_two],
        }

        empty_drain = drain_issue_fix_reviewer_notification_queue(
            ledger_path=path / ".loopx/domain-state/empty/pr-lifecycle.jsonl",
            sinks_input=drain_goal_config,
            execute=True,
            delivery_observed_at="2026-07-18T01:01:00Z",
        )
        assert empty_drain["status"] == "no_due_notifications", empty_drain
        assert empty_drain["external_reads_performed"] is False

        legacy_ledger = path / ".loopx/domain-state/legacy/pr-lifecycle.jsonl"
        legacy_row = build_issue_fix_pr_lifecycle_monitor_packet(
            url="https://github.com/owner/repo/pull/100",
            provider_payload={
                "state": "OPEN",
                "reviewDecision": "REVIEW_REQUIRED",
                "mergeStateStatus": "BLOCKED",
                "statusCheckRollup": [],
            },
        )
        legacy_row["reviewer_notification_queue"] = [
            {
                "schema_version": (
                    "issue_fix_reviewer_notification_queue_receipt_v0"
                ),
                "idempotency_key": "sha256:" + "e" * 64,
                "sink_kind": "lark_chat",
                "reviewer_handles": ["@map-owner"],
                "queued_at": "2026-07-17T18:00:00Z",
                "not_before": "2026-07-18T01:00:00Z",
                "timezone": "Asia/Shanghai",
                "allowed_local_time": {"start": "09:00", "end": "21:00"},
                "status": "queued",
            }
        ]
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(legacy_ledger, legacy_row)
        legacy_drain = drain_issue_fix_reviewer_notification_queue(
            ledger_path=legacy_ledger,
            sinks_input=drain_goal_config,
            execute=True,
            delivery_observed_at="2026-07-18T01:01:00Z",
        )
        assert legacy_drain["status"] == "blocked", legacy_drain
        assert legacy_drain["blocker"] == (
            "reviewer_notification_queue_v1_migration_required"
        )
        assert legacy_drain["legacy_queue_receipt_count"] == 1
        assert legacy_drain["external_reads_performed"] is False

        def drain_adapter(**kwargs: Any) -> dict[str, Any]:
            assert kwargs["execute"] is True
            assert "修复" in kwargs["pr_title"]
            key = reviewer_notification_idempotency_key(
                repo=kwargs["repo"],
                pr_number=kwargs["pr_number"],
                sink_kind=kwargs["sink"]["sink_kind"],
                sink_instance_key=kwargs["sink"]["sink_instance_key"],
                reviewer_handles=kwargs["reviewer_handles"],
            )
            drain_calls.append(
                {
                    "number": kwargs["pr_number"],
                    "summary": kwargs["pr_title"],
                    "sink": kwargs["sink"]["sink_instance_key"],
                    "reviewers": list(kwargs["reviewer_handles"]),
                }
            )
            return {
                "ok": True,
                "schema_version": "issue_fix_reviewer_notification_sink_result_v0",
                "sink_kind": "lark_chat",
                "status": "sent_verified",
                "reviewer_handles": list(kwargs["reviewer_handles"]),
                "resolved_reviewer_count": len(kwargs["reviewer_handles"]),
                "idempotency_key": key,
                "identity_scope": "project_dedicated",
                "external_write_authority_asserted": True,
                "external_write_performed": True,
                "verification_performed": True,
                "notification_verified": True,
                "bot_identity_verified": True,
                "reader_identity_verified": True,
                "private_destination_captured": False,
                "private_member_ids_captured": False,
                "private_bot_profile_captured": False,
                "raw_provider_payload_captured": False,
            }

        drain_live = {
            101: {
                "author_handle": "@author-a",
                "reviewed_by": ["@reviewed-owner"],
                "requested_reviewers": ["@map-owner"],
                "comment_notified_reviewers": [],
                "state": "OPEN",
                "review_decision": "REVIEW_REQUIRED",
                "state_bucket": "checks_pending",
                "is_draft": False,
                "linked_issue_refs": ["#91"],
            },
            102: {
                "author_handle": "@author-b",
                "reviewed_by": [],
                "requested_reviewers": ["@map-owner"],
                "comment_notified_reviewers": [],
                "state": "OPEN",
                "review_decision": "APPROVED",
                "state_bucket": "ready_to_merge",
                "is_draft": False,
                "linked_issue_refs": ["#92"],
            },
            103: {
                "author_handle": "@author-c",
                "reviewed_by": [],
                "requested_reviewers": ["@map-owner"],
                "comment_notified_reviewers": [],
                "state": "OPEN",
                "review_decision": "REVIEW_REQUIRED",
                "state_bucket": "review_required",
                "is_draft": False,
                "linked_issue_refs": ["#93"],
            },
            104: {
                "author_handle": "@author-d",
                "reviewed_by": [],
                "requested_reviewers": ["@other-owner"],
                "comment_notified_reviewers": [],
                "state": "OPEN",
                "review_decision": "REVIEW_REQUIRED",
                "state_bucket": "review_required",
                "is_draft": False,
                "linked_issue_refs": ["#94"],
            },
        }

        def drain_metadata_loader(
            *, repo: str, number: int, runner: Any = None
        ) -> tuple[dict[str, Any] | None, str | None]:
            assert repo == "owner/repo"
            return dict(drain_live[number]), None

        for number, not_before, summary in (
            (101, "2026-07-18T01:00:00Z", "修复队列到点后无人消费的问题"),
            (102, "2026-07-18T01:00:00Z", "修复已批准请求仍重复提醒的问题"),
            (103, "2026-07-19T01:00:00Z", "修复未来窗口被提前发送的问题"),
            (104, "2026-07-18T01:00:00Z", "修复 reviewer 换人后误提醒旧人的问题"),
        ):
            lifecycle_row = build_issue_fix_pr_lifecycle_monitor_packet(
                url=f"https://github.com/owner/repo/pull/{number}",
                provider_payload={
                    "state": "OPEN",
                    "reviewDecision": "REVIEW_REQUIRED",
                    "mergeStateStatus": "BLOCKED",
                    "statusCheckRollup": [],
                },
            )
            upsert_issue_fix_pr_lifecycle_ledger_jsonl(drain_ledger, lifecycle_row)
            queue_receipts = []
            sinks = (
                [goal_sink, drain_sink_two, drain_removed_sink]
                if number == 101
                else [goal_sink]
            )
            for sink in sinks:
                reviewers = (
                    ["@reviewed-owner", "@removed-owner", "@map-owner"]
                    if number == 101
                    else ["@map-owner"]
                )
                key = reviewer_notification_idempotency_key(
                    repo="owner/repo",
                    pr_number=number,
                    sink_kind="lark_chat",
                    sink_instance_key=sink["sink_instance_key"].strip(),
                    reviewer_handles=reviewers,
                )
                queue_receipts.append(
                    {
                        "schema_version": "issue_fix_reviewer_notification_queue_receipt_v1",
                        "idempotency_key": key,
                        "sink_kind": "lark_chat",
                        "reviewer_handles": reviewers,
                        "message_summary": summary,
                        "summary_policy_status": "reward_memory_verified",
                        "queued_at": "2026-07-17T18:00:00Z",
                        "not_before": not_before,
                        "timezone": "Asia/Shanghai",
                        "allowed_local_time": {"start": "09:00", "end": "21:00"},
                        "status": "queued",
                    }
                )
            persist_issue_fix_reviewer_notification_state(
                drain_ledger,
                lifecycle_row,
                receipts=[],
                queued_receipts=queue_receipts,
            )

        drained = drain_issue_fix_reviewer_notification_queue(
            ledger_path=drain_ledger,
            sinks_input=drain_goal_config,
            execute=True,
            delivery_observed_at="2026-07-18T01:01:00Z",
            metadata_loader=drain_metadata_loader,
            sink_adapters={"lark_chat": drain_adapter},
        )
        assert drained["ok"] is True, drained
        assert drained["grouping_scope"] == "review_required_state_bucket"
        assert drained["monitor_granularity"] == "one_monitor_per_state_bucket"
        assert drained["notification_granularity"] == "one_pr_per_message"
        assert drained["due_pr_count"] == 3
        assert drained["verified_pr_count"] == 1
        assert drained["cancelled_pr_count"] == 2
        assert drained["cancelled_sink_receipt_count"] == 1
        assert drained["not_due_receipt_count"] == 1
        pr_101 = next(item for item in drained["items"] if item["pr_ref"] == "#101")
        assert pr_101["inactive_reviewer_handles"] == ["@removed-owner"]
        stored_drain_rows = [
            json.loads(line)
            for line in drain_ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        pr_102_row = next(
            row
            for row in stored_drain_rows
            if row["observation"]["pr_ref"] == "pull_102"
        )
        assert pr_102_row["observation"]["review_decision"] == "APPROVED"
        assert (
            pr_102_row["grouped_monitor_projection"]["state_bucket"]
            == "ready_to_merge"
        )
        pr_101_row = next(
            row
            for row in stored_drain_rows
            if row["observation"]["pr_ref"] == "pull_101"
        )
        assert (
            pr_101_row["grouped_monitor_projection"]["state_bucket"]
            == "checks_pending"
        )
        assert drain_calls == [
            {
                "number": 101,
                "summary": "修复队列到点后无人消费的问题",
                "sink": "fixture-review-lane",
                "reviewers": ["@map-owner"],
            },
            {
                "number": 101,
                "summary": "修复队列到点后无人消费的问题",
                "sink": " openviking-reviewer-group-secondary ",
                "reviewers": ["@map-owner"],
            },
        ], drain_calls
        repeated_drain = drain_issue_fix_reviewer_notification_queue(
            ledger_path=drain_ledger,
            sinks_input=drain_goal_config,
            execute=True,
            delivery_observed_at="2026-07-18T01:02:00Z",
            metadata_loader=drain_metadata_loader,
            sink_adapters={"lark_chat": drain_adapter},
        )
        assert repeated_drain["status"] == "no_due_notifications"
        assert repeated_drain["due_pr_count"] == 0
        assert len(drain_calls) == 2
        held_drain = drain_issue_fix_reviewer_notification_queue(
            ledger_path=drain_ledger,
            sinks_input=drain_goal_config,
            execute=True,
            delivery_observed_at="2026-07-19T15:00:00Z",
            metadata_loader=drain_metadata_loader,
            sink_adapters={"lark_chat": drain_adapter},
        )
        assert held_drain["status"] == "held_outside_delivery_window", held_drain
        assert held_drain["held_pr_count"] == 1
        assert held_drain["external_reads_performed"] is False
        assert len(drain_calls) == 2
        resumed_drain = drain_issue_fix_reviewer_notification_queue(
            ledger_path=drain_ledger,
            sinks_input=drain_goal_config,
            execute=True,
            delivery_observed_at="2026-07-20T01:01:00Z",
            metadata_loader=drain_metadata_loader,
            sink_adapters={"lark_chat": drain_adapter},
        )
        assert resumed_drain["status"] == "drained_verified", resumed_drain
        assert resumed_drain["verified_pr_count"] == 1
        assert drain_calls[-1]["number"] == 103
        assert len(drain_calls) == 3

        write(path / ".loopx/config/semantic-preference.json", "{}")
        gated_combined = FakeCombinedRunner(FakeGitHubRunner(before=metadata()))
        missing_publication_evidence = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            notification_sinks_input=sinks_input,
            execute=True,
            runner=gated_combined,
        )
        assert missing_publication_evidence["ok"] is False
        assert missing_publication_evidence["blocker"] == (
            "pr_description_recall_evidence_required"
        )
        assert missing_publication_evidence["selected_reviewers"] == []
        assert missing_publication_evidence["external_writes_performed"] is False
        assert missing_publication_evidence["pr_description_publication_gate"][
            "required_evidence_inputs"
        ] == ["pr_description_build_json"]
        assert missing_publication_evidence["secondary_notification_status"] == (
            "skipped_reviewer_unavailable"
        )
        assert gated_combined.github.edits == 0
        assert gated_combined.github.comments == 0
        assert gated_combined.lark_calls == []
        assert_public_safe(missing_publication_evidence)

        publication_build = {
            "schema_version": "issue_fix_pr_description_build_v0",
            "semantic_preference": {
                "surface": "issue_fix.pr_description",
                "recall_executed": True,
                "recall_status": "completed",
            },
        }
        verified_runner = FakeGitHubRunner(
            before=metadata(),
            after=metadata(requested=["service-owner"]),
        )
        verified_publication = build_issue_fix_reviewer_request_packet(
            repo_path=path,
            url="https://github.com/owner/repo/pull/42",
            base_ref="main",
            pr_description_build=publication_build,
            execute=True,
            runner=verified_runner,
        )
        assert verified_publication["ok"] is True, verified_publication
        assert verified_publication["pr_description_publication_gate"]["status"] == (
            "verified"
        )
        assert verified_publication["review_request_verified"] is True
        assert verified_runner.edits == 1
        assert_public_safe(verified_publication)

        subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py"
                ),
            ],
            cwd=ROOT,
            check=True,
        )
    finally:
        for attempt in range(10):
            try:
                remove_owned_tree(path)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY or attempt == 9:
                    raise
                time.sleep(0.05)

    print("issue-fix-reviewer-request-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
