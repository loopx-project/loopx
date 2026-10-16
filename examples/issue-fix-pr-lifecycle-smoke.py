#!/usr/bin/env python3
"""Smoke-test issue-fix PR lifecycle projection and domain-state writeback."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.issue_fix.pr_lifecycle import (  # noqa: E402
    ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION,
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.todos import list_goal_todos  # noqa: E402
from loopx.domain_packs.issue_fix import (  # noqa: E402
    default_issue_fix_domain_state_ledger_path,
    issue_fix_pr_lifecycle_ledger_key,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from loopx.rollout_event_log import (  # noqa: E402
    load_rollout_events,
    rollout_event_log_path,
)
from loopx.status import parse_active_state_todos  # noqa: E402


PRIVATE_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

FORBIDDEN_VALUES = [
    "raw issue body text that must stay gated",
    "full issue comment text that must stay gated",
    "raw provider response payload",
    "private check log",
    "secret-value",
    "credential-value",
]


def assert_public_safe(payload: dict[str, Any] | str) -> None:
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AssertionError(f"payload matched private pattern {pattern.pattern!r}")
    leaked = [value for value in FORBIDDEN_VALUES if value in text]
    assert not leaked, leaked


def run_cli(
    args: list[str],
    *,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    control_plane_args: list[str] = []
    if registry_path is not None:
        control_plane_args.extend(["--registry", str(registry_path)])
    if runtime_root is not None:
        control_plane_args.extend(["--runtime-root", str(runtime_root)])
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            *control_plane_args,
            "--format",
            "json",
            *args,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def assert_packet_shape(packet: dict[str, Any]) -> None:
    assert packet["ok"] is True, packet
    assert packet["schema_version"] == ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION
    assert packet["external_writes_performed"] is False
    assert packet["todo_write_performed"] is False
    assert packet["issue_body_captured"] is False
    assert packet["comment_bodies_captured"] is False
    assert packet["response_payloads_captured"] is False
    assert packet["raw_check_logs_captured"] is False
    assert packet["local_paths_captured"] is False
    assert packet["private_repo_state_read"] is False
    assert packet["observation"]["body_captured"] is False
    assert packet["observation"]["comment_bodies_captured"] is False
    assert packet["observation"]["log_output_captured"] is False
    assert packet["transition"]["would_write"] is False
    assert packet["transition"]["requires_execute_flag"] is True
    grouped = packet["grouped_monitor_projection"]
    assert grouped["schema_version"] == "issue_fix_pr_grouped_monitor_projection_v1"
    assert grouped["creates_per_pr_continuous_monitor_todo"] is False
    assert grouped["per_pr_material_action"] == "one_shot_advancement_todo"
    assert grouped["external_notification_granularity"] == "one_pr_per_message"
    assert grouped["todo_write_performed"] is False
    assert packet["domain_state_projection"]["domain_pack"] == "issue_fix"
    assert packet["domain_state_projection"]["path_recorded"] is False
    assert packet["validation"]["ok"] is True, packet
    assert_public_safe(packet)


def check_lifecycle(provider: str) -> None:
    merged = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1715",
        provider_payload={
            "state": "MERGED",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "UNKNOWN",
            "statusCheckRollup": [
                {"name": "Full Public Smokes", "conclusion": "SUCCESS"}
            ],
            "body": "raw issue body text that must stay gated",
            "comments": ["full issue comment text that must stay gated"],
            "raw": "raw provider response payload",
        },
    )
    assert_packet_shape(merged)
    assert merged["transition"]["decision"] == "no_followup", merged
    assert merged["transition"]["action_kind"] == "issue_fix_pr_merged_no_followup"
    assert merged["transition"]["terminal_state_precedence"] is True
    assert merged["writeback_contract"]["monitor_quiet_skip_allowed"] is False
    assert merged["grouped_monitor_projection"]["state_bucket"] == "terminal"
    assert merged["grouped_monitor_projection"]["member_operation"] == "remove"
    assert merged["grouped_monitor_projection"]["target_key"] is None

    failing = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1715",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [
                {"name": "Full Public Smokes", "conclusion": "FAILURE"}
            ],
            "check_log": "private check log",
        },
    )
    assert_packet_shape(failing)
    assert failing["transition"]["decision"] == "runnable_successor", failing
    assert failing["transition"]["action_kind"] == "issue_fix_ci_failure_replan"
    assert failing["first_screen"]["agent_can_continue"] is True
    assert failing["grouped_monitor_projection"]["state_bucket"] == "checks_failed"

    requested = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1715",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "CHANGES_REQUESTED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        },
    )
    assert_packet_shape(requested)
    assert requested["transition"]["decision"] == "runnable_successor", requested
    assert requested["transition"]["action_kind"] == "issue_fix_review_changes_replan"
    assert requested["grouped_monitor_projection"]["state_bucket"] == (
        "changes_requested"
    )

    blocked_pending = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1715",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "BLOCKED",
            "statusCheckRollup": [{"name": "lint", "status": "IN_PROGRESS"}],
        },
    )
    assert_packet_shape(blocked_pending)
    assert blocked_pending["transition"]["decision"] == "monitor_continuation"
    assert blocked_pending["transition"]["action_kind"] == (
        "issue_fix_pr_checks_pending_monitor"
    )
    assert blocked_pending["transition"]["material_change"] is False
    assert blocked_pending["grouped_monitor_projection"]["target_key"] == (
        "github-pr-state-huangruiteng--loopx-checks-pending"
    )

    stale = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1715",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "BEHIND",
            "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        },
    )
    assert_packet_shape(stale)
    assert stale["transition"]["decision"] == "runnable_successor"
    assert stale["transition"]["action_kind"] == (
        "issue_fix_branch_or_merge_blocker_replan"
    )
    assert stale["grouped_monitor_projection"]["state_bucket"] == "branch_blocked"

    quiet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1715",
        issue_ref="issues_1700",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "createdAt": "2026-06-23T00:00:00Z",
            "headRefOid": "a" * 40,
            "commits": [{"oid": "a" * 40}],
            "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        },
    )
    assert_packet_shape(quiet)
    assert quiet["transition"]["decision"] == "monitor_continuation", quiet
    assert quiet["observation"]["issue_ref"] == "issues_1700", quiet
    assert quiet["transition"]["material_change"] is False
    assert quiet["writeback_contract"]["monitor_quiet_skip_allowed"] is True
    assert quiet["first_push_ci"]["status"] == "PASSING", quiet
    assert quiet["first_push_ci"]["pr_ref"] == "pull_1715", quiet
    assert quiet["grouped_monitor_projection"] == (
        quiet["grouped_monitor_projection"]
        | {
            "state_bucket": "review_required",
            "target_key": "github-pr-state-huangruiteng--loopx-review-required",
            "action_kind": "issue_fix_pr_state_review_required_monitor",
            "member_key": "huangruiteng/loopx#1715",
            "member_operation": "upsert",
            "materialize_nonempty_bucket_monitor": True,
        }
    )
    same_bucket_peer = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1717",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        },
    )
    assert_packet_shape(same_bucket_peer)
    assert same_bucket_peer["grouped_monitor_projection"]["target_key"] == (
        quiet["grouped_monitor_projection"]["target_key"]
    )
    assert same_bucket_peer["grouped_monitor_projection"]["member_key"] != (
        quiet["grouped_monitor_projection"]["member_key"]
    )

    approved = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/1716",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "APPROVED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        },
    )
    assert_packet_shape(approved)
    assert approved["grouped_monitor_projection"]["state_bucket"] == "ready_to_merge"
    assert approved["grouped_monitor_projection"]["target_key"] == (
        "github-pr-state-huangruiteng--loopx-ready-to-merge"
    )
    for alias in ("#1700", "issue_1700", "issues/1700", "issue 1700"):
        alias_packet = build_issue_fix_pr_lifecycle_monitor_packet(
            url="https://github.com/huangruiteng/loopx/pull/1715",
            issue_ref=alias,
            provider_payload={"state": "OPEN"},
        )
        assert alias_packet["observation"]["issue_ref"] == "issues_1700", alias_packet
    repo_ref = build_issue_fix_pr_lifecycle_monitor_packet(
        repo="huangruiteng/loopx",
        pr_ref="pull_1715",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        },
    )
    assert_packet_shape(repo_ref)
    assert repo_ref["observation"]["kind"] == "pull_request", repo_ref
    assert repo_ref["observation"]["pr_ref"] == "pull_1715", repo_ref

    with tempfile.TemporaryDirectory(prefix="loopx-issue-fix-pr-lifecycle-") as tmpdir:
        project = Path(tmpdir)
        runtime_root = project / "runtime"
        registry_path = project / ".loopx" / "registry.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "common_runtime_root": str(runtime_root),
                    "goals": [
                        {
                            "id": "example-goal",
                            "status": "active",
                            "repo": str(project),
                            "state_file": ".codex/goals/example-goal/ACTIVE_GOAL_STATE.md",
                            "adapter": {
                                "kind": "read_only_project_map_v0",
                                "status": "connected-read-only",
                            },
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["codex-test", "codex-review"],
                            },
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_file = (
            project
            / ".codex"
            / "goals"
            / "example-goal"
            / "ACTIVE_GOAL_STATE.md"
        )
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            "# Active Goal State\n\n"
            "## User Todo / Owner Review Reading Queue\n\n"
            "- [ ] Approve public PR merge.\n"
            "  <!-- loopx:todo todo_id=todo_merge_gate_1716 status=open "
            "task_class=user_gate decision_scope=direction:action:merge_pr_1716 "
            "blocks_agent=codex-test -->\n\n"
            "- [ ] Review the validated public PR.\n"
            "  <!-- loopx:todo todo_id=todo_review_action_1716 status=open "
            "task_class=user_action bound_agent=codex-test -->\n\n"
            "## Agent Todo\n\n",
            encoding="utf-8",
        )
        import_fixture_authority(registry_path, "example-goal", provider=provider)

        ledger = default_issue_fix_domain_state_ledger_path(
            project=project,
            goal_id="example-goal",
        )
        key = issue_fix_pr_lifecycle_ledger_key(quiet)
        assert key == {"repo": "huangruiteng/loopx", "pr_ref": "pull_1715"}
        result = upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, quiet)
        assert result["status"] == "inserted", result
        first_inode = ledger.stat().st_ino
        result = upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, quiet)
        assert result["status"] == "unchanged", result
        assert result["write_performed"] is False, result
        assert ledger.stat().st_ino == first_inode
        assert quiet["domain_state_projection"]["write_performed"] is False, quiet
        assert quiet["domain_state_projection"]["write_skipped_reason"] == (
            "observation_fingerprint_unchanged"
        ), quiet
        rows = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows) == 1, rows
        assert rows[0]["domain_state_key"] == key, rows
        assert_public_safe(rows[0])

        metadata_path = project / "pr.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "state": "OPEN",
                    "reviewDecision": "REVIEW_REQUIRED",
                    "mergeStateStatus": "CLEAN",
                    "createdAt": "2026-06-23T00:00:00Z",
                    "headRefOid": "a" * 40,
                    "commits": [{"oid": "a" * 40}],
                    "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
                }
            ),
            encoding="utf-8",
        )
        invoked_after = datetime.now(timezone.utc).replace(microsecond=0)
        cli_result = run_cli(
            [
                "issue-fix",
                "pr-lifecycle",
                "--url",
                "https://github.com/huangruiteng/loopx/pull/1715",
                "--metadata-json",
                str(metadata_path),
                "--issue-ref",
                "issues_1700",
                "--goal-id",
                "example-goal",
                "--project",
                str(project),
            ]
        )
        cli_packet = json.loads(cli_result.stdout)
        completed_before = datetime.now(timezone.utc).replace(microsecond=0)
        assert_packet_shape(cli_packet)
        cli_generated_at = datetime.fromisoformat(
            str(cli_packet["generated_at"]).replace("Z", "+00:00")
        )
        assert invoked_after <= cli_generated_at <= completed_before, cli_packet
        assert cli_packet["generated_at"] != "2026-06-23T00:00:00Z", cli_packet
        assert cli_packet["domain_state_projection"]["write_performed"] is False
        write_result = cli_packet["domain_state_projection"]["write_result"]
        assert write_result["status"] == "unchanged", write_result
        assert write_result["write_performed"] is False, write_result
        assert ledger.stat().st_ino == first_inode
        assert write_result["path_recorded"] is False, write_result
        persisted_rows = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        assert len(persisted_rows) == 1, persisted_rows
        persisted_projection = persisted_rows[0]["domain_state_projection"]
        assert persisted_projection["write_performed"] is True, persisted_rows[0]
        assert "write_result" not in persisted_projection, persisted_rows[0]
        assert_public_safe(cli_packet)

        explicit_time = "2026-06-23T00:00:00Z"
        explicit_result = run_cli(
            [
                "issue-fix",
                "pr-lifecycle",
                "--url",
                "https://github.com/huangruiteng/loopx/pull/1715",
                "--metadata-json",
                str(metadata_path),
                "--generated-at",
                explicit_time,
                "--no-write-domain-state",
            ]
        )
        explicit_packet = json.loads(explicit_result.stdout)
        assert explicit_packet["generated_at"] == explicit_time, explicit_packet

        merged_metadata_path = project / "merged-pr.json"
        merged_metadata_path.write_text(
            json.dumps(
                {
                    "state": "MERGED",
                    "mergedAt": "2026-06-24T00:00:00Z",
                    "reviewDecision": "APPROVED",
                    "statusCheckRollup": [
                        {"name": "lint", "conclusion": "SUCCESS"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        gate_open_metadata_path = project / "gate-open-pr.json"
        gate_open_metadata_path.write_text(
            json.dumps(
                {
                    "state": "OPEN",
                    "reviewDecision": "APPROVED",
                    "statusCheckRollup": [
                        {"name": "lint", "conclusion": "SUCCESS"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        gate_merged_metadata_path = project / "gate-merged-pr.json"
        gate_merged_metadata_path.write_text(
            json.dumps(
                {
                    "state": "MERGED",
                    "mergedAt": "2026-06-25T00:00:00Z",
                    "reviewDecision": "APPROVED",
                    "statusCheckRollup": [
                        {"name": "lint", "conclusion": "SUCCESS"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        gate_base_args = [
            "issue-fix",
            "pr-gate-reconcile",
            "--url",
            "https://github.com/huangruiteng/loopx/pull/1716",
            "--goal-id",
            "example-goal",
            "--todo-id",
            "todo_merge_gate_1716",
            "--agent-id",
            "codex-test",
            "--project",
            str(project),
        ]
        open_gate = json.loads(
            run_cli(
                [
                    *gate_base_args,
                    "--metadata-json",
                    str(gate_open_metadata_path),
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert open_gate["terminal"] is False, open_gate
        assert open_gate["write_performed"] is False, open_gate
        assert open_gate["skip_reason"] == "pr_not_terminal", open_gate
        assert "status=open" in state_file.read_text(encoding="utf-8")

        preview_gate = json.loads(
            run_cli(
                [
                    *gate_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert preview_gate["terminal"] is True, preview_gate
        assert preview_gate["would_reconcile"] is True, preview_gate
        assert preview_gate["write_performed"] is False, preview_gate
        assert preview_gate["skip_reason"] == "execute_required", preview_gate

        reconciled_gate = json.loads(
            run_cli(
                [
                    *gate_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert reconciled_gate["reconciled"] is True, reconciled_gate
        assert reconciled_gate["write_performed"] is True, reconciled_gate
        assert reconciled_gate["todo_completion"]["status"] == "done", reconciled_gate
        mutation_authority = reconciled_gate["todo_completion"][
            "mutation_authority"
        ]
        assert mutation_authority["authority_mode"] == "registered_peer_actor", reconciled_gate
        assert reconciled_gate["rollout_event"]["appended"] is True, reconciled_gate
        assert "status=done" in state_file.read_text(encoding="utf-8")
        gate_projection = parse_active_state_todos(
            state_file.read_text(encoding="utf-8")
        )
        gate_item = next(
            item
            for item in gate_projection["user_todos"]["items"]
            if item["todo_id"] == "todo_merge_gate_1716"
        )
        assert gate_item["status"] == "done", gate_item
        assert "state=MERGED" in gate_item["evidence"], gate_item
        assert_public_safe(reconciled_gate)

        repeated_gate = json.loads(
            run_cli(
                [
                    *gate_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert repeated_gate["already_reconciled"] is True, repeated_gate
        assert repeated_gate["write_performed"] is False, repeated_gate
        assert repeated_gate["rollout_event"]["already_recorded"] is True, repeated_gate

        review_base_args = [
            "issue-fix",
            "pr-review-reconcile",
            "--url",
            "https://github.com/huangruiteng/loopx/pull/1716",
            "--goal-id",
            "example-goal",
            "--todo-id",
            "todo_review_action_1716",
            "--agent-id",
            "codex-test",
            "--project",
            str(project),
        ]
        unacknowledged_review = json.loads(
            run_cli(
                [
                    *review_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert unacknowledged_review["terminal"] is False, unacknowledged_review
        assert unacknowledged_review["owner_acknowledged"] is False, (
            unacknowledged_review
        )
        assert unacknowledged_review["external_read_performed"] is False, (
            unacknowledged_review
        )
        assert unacknowledged_review["write_performed"] is False, (
            unacknowledged_review
        )
        assert unacknowledged_review["skip_reason"] == "ack_receipt_missing", (
            unacknowledged_review
        )

        open_review = json.loads(
            run_cli(
                [
                    *review_base_args,
                    "--metadata-json",
                    str(gate_open_metadata_path),
                    "--owner-acknowledged",
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert open_review["terminal"] is False, open_review
        assert open_review["owner_acknowledged"] is True, open_review
        assert open_review["ack_receipt_status"] == "matched", open_review
        assert open_review["ack_receipt_id"], open_review
        assert open_review["write_performed"] is False, open_review
        assert open_review["skip_reason"] == "pr_not_terminal", open_review

        preview_review = json.loads(
            run_cli(
                [
                    *review_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                    "--owner-acknowledged",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert preview_review["would_reconcile"] is True, preview_review
        assert preview_review["write_performed"] is False, preview_review
        assert preview_review["skip_reason"] == "execute_required", preview_review
        assert preview_review["ack_receipt_id"] == open_review["ack_receipt_id"], (
            preview_review
        )

        reconciled_review = json.loads(
            run_cli(
                [
                    *review_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert reconciled_review["reconciled"] is True, reconciled_review
        assert reconciled_review["write_performed"] is True, reconciled_review
        assert reconciled_review["todo_completion"]["status"] == "done", (
            reconciled_review
        )
        assert reconciled_review["rollout_event"]["already_recorded"] is True, (
            reconciled_review
        )
        review_projection = parse_active_state_todos(
            state_file.read_text(encoding="utf-8")
        )
        review_item = next(
            item
            for item in review_projection["user_todos"]["items"]
            if item["todo_id"] == "todo_review_action_1716"
        )
        assert review_item["status"] == "done", review_item
        assert review_item["no_followup"] is True, review_item
        assert "state=MERGED" in review_item["evidence"], review_item
        assert_public_safe(reconciled_review)

        repeated_review = json.loads(
            run_cli(
                [
                    *review_base_args,
                    "--metadata-json",
                    str(gate_merged_metadata_path),
                    "--execute",
                ],
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert repeated_review["already_reconciled"] is True, repeated_review
        assert repeated_review["write_performed"] is False, repeated_review

        merged_args = [
            "issue-fix",
            "pr-lifecycle",
            "--url",
            "https://github.com/huangruiteng/loopx/pull/1715",
            "--metadata-json",
            str(merged_metadata_path),
            "--goal-id",
            "example-goal",
            "--project",
            str(project),
        ]
        first_merged = json.loads(
            run_cli(
                merged_args,
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert first_merged["rollout_event"]["recorded"] is True, first_merged
        assert first_merged["rollout_event"]["appended"] is True, first_merged
        second_merged = json.loads(
            run_cli(
                merged_args,
                registry_path=registry_path,
                runtime_root=runtime_root,
            ).stdout
        )
        assert second_merged["rollout_event"]["already_recorded"] is True, second_merged
        assert second_merged["rollout_event"]["appended"] is False, second_merged
        assert second_merged["rollout_event"]["event_id"] == (
            first_merged["rollout_event"]["event_id"]
        )
        rollout_events = load_rollout_events(
            rollout_event_log_path(runtime_root, "example-goal")
        )
        merge_events = [
            event
            for event in rollout_events
            if event.get("event_kind") == "pr_merge"
        ]
        assert len(merge_events) == 2, rollout_events
        assert {
            event["code_refs"]["pr_ref"] for event in merge_events
        } == {"huangruiteng/loopx#1715", "huangruiteng/loopx#1716"}
        parsed = parse_active_state_todos(
            "## Agent Todo\n\n"
            "- [ ] Resume after merge.\n"
            "  <!-- loopx:todo todo_id=todo_resume status=open "
            "task_class=advancement_task "
            "task_repository=git:github.com/huangruiteng/loopx "
            "resume_when=pr_merged:#1715 -->\n",
            rollout_events=rollout_events,
        )
        resume_item = parsed["agent_todos"]["items"][0]
        assert resume_item["resume_ready"] is True, resume_item
        assert resume_item["resume_condition"]["matched_pr_ref"] == "huangruiteng/loopx#1715"

        material_result = upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, failing)
        assert material_result["status"] == "updated", material_result
        assert material_result["write_performed"] is True, material_result
        assert failing["domain_state_projection"]["write_performed"] is True, failing
        material_rows = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        assert material_rows[0]["observation_fingerprint"] == failing[
            "observation_fingerprint"
        ], material_rows
        assert material_rows[0]["observation_fingerprint"] != quiet[
            "observation_fingerprint"
        ], material_rows
        assert material_rows[0]["first_push_ci"] == quiet["first_push_ci"], (
            material_rows
        )

        native = list_goal_todos(registry_path=registry_path, goal_id="example-goal")
        assert native["authority_read"]["decision_read_from_provider"] is True, native
        assert native["authority_read"]["legacy_fallback_used"] is False, native
        rows = {item["todo_id"]: item for item in native["todos"]}
        for todo_id in ("todo_merge_gate_1716", "todo_review_action_1716"):
            assert rows[todo_id]["status"] == "done", rows[todo_id]
            assert rows[todo_id]["last_actor_agent_id"] == "codex-test", rows[todo_id]


def main() -> int:
    for provider in ("file", "sqlite"):
        check_lifecycle(provider)
    print("issue-fix-pr-lifecycle-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
