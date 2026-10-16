#!/usr/bin/env python3
"""Prove revision-aware, host-neutral PR review reconciliation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.issue_fix.pr_gate_reconcile import (  # noqa: E402
    reconcile_acknowledged_issue_fix_pr_reviews,
    reconcile_issue_fix_pr_review,
)
from loopx.capabilities.issue_fix.pr_review_ack import (  # noqa: E402
    record_issue_fix_pr_review_ack,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.heartbeat_prequota import run_heartbeat_pre_quota  # noqa: E402
from loopx.status import parse_active_state_todos  # noqa: E402
from loopx.todos import list_goal_todos, update_goal_todo  # noqa: E402


PR_URL = "https://github.com/huangruiteng/loopx/pull/1716"


def record_ack(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
    generated_at: str,
) -> dict[str, Any]:
    return record_issue_fix_pr_review_ack(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="example-goal",
        todo_id="todo_github_review",
        agent_id="codex-test",
        project=project,
        url=PR_URL,
        owner_acknowledged=True,
        generated_at=generated_at,
    )


def todo(state_file: Path, todo_id: str) -> dict[str, Any]:
    projection = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return next(
        item
        for item in projection["user_todos"]["items"]
        if item["todo_id"] == todo_id
    )


def reconcile_without_provider(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
    todo_id: str,
    ack_receipt: dict[str, Any] | None,
    url: str = PR_URL,
) -> dict[str, Any]:
    with patch(
        "loopx.capabilities.issue_fix.pr_gate_reconcile."
        "build_issue_fix_pr_lifecycle_monitor_packet",
        side_effect=AssertionError("invalid acknowledgement must not read provider"),
    ):
        return reconcile_issue_fix_pr_review(
            registry_path=registry_path,
            runtime_root_arg=str(runtime_root),
            goal_id="example-goal",
            todo_id=todo_id,
            agent_id="codex-test",
            project=project,
            url=url,
            ack_receipt=ack_receipt,
            fetch_metadata=True,
            execute=True,
        )


def check_reconciliation(provider: str) -> None:
    with tempfile.TemporaryDirectory(
        prefix="loopx-issue-fix-pr-review-reconcile-"
    ) as tmpdir:
        project = Path(tmpdir)
        runtime_root = project / "runtime"
        registry_path = project / ".loopx" / "registry.json"
        state_file = (
            project / ".codex" / "goals" / "example-goal" / "ACTIVE_GOAL_STATE.md"
        )
        registry_path.parent.mkdir(parents=True)
        state_file.parent.mkdir(parents=True)
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
                            "state_file": (
                                ".codex/goals/example-goal/ACTIVE_GOAL_STATE.md"
                            ),
                            "adapter": {
                                "kind": "read_only_project_map_v0",
                                "status": "connected-read-only",
                            },
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["codex-test"],
                            },
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_file.write_text(
            "# Active Goal State\n\n"
            "## User Todo / Owner Review Reading Queue\n\n"
            "- [ ] Review the exact GitHub PR.\n"
            "  <!-- loopx:todo todo_id=todo_github_review status=open "
            "task_class=user_action bound_agent=codex-test -->\n\n"
            "- [ ] Review a non-GitHub change.\n"
            "  <!-- loopx:todo todo_id=todo_other_provider status=open "
            "task_class=user_action bound_agent=codex-test -->\n\n"
            "## Agent Todo\n\n",
            encoding="utf-8",
        )

        import_fixture_authority(registry_path, "example-goal", provider=provider)

        missing = reconcile_without_provider(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            todo_id="todo_github_review",
            ack_receipt=None,
        )
        assert missing["skip_reason"] == "ack_receipt_missing", missing
        assert missing["external_read_performed"] is False, missing
        unsupported = reconcile_without_provider(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            todo_id="todo_other_provider",
            ack_receipt=None,
            url="https://gitlab.com/group/project/-/merge_requests/12",
        )
        assert unsupported["skip_reason"] == "provider_unsupported", unsupported
        assert unsupported["external_read_performed"] is False, unsupported

        first_ack = record_ack(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            generated_at="2026-07-24T01:00:00Z",
        )
        replayed_ack = record_ack(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            generated_at="2026-07-24T01:00:00Z",
        )
        assert first_ack["write_performed"] is True, first_ack
        assert replayed_ack["replayed"] is True, replayed_ack
        assert first_ack["binding"]["todo_revision"], first_ack
        assert (
            first_ack["ack_receipt"]["receipt_id"]
            == replayed_ack["ack_receipt"]["receipt_id"]
        )

        updated = update_goal_todo(
            registry_path=registry_path,
            goal_id="example-goal",
            todo_id="todo_github_review",
            role="user",
            text="Review the exact GitHub PR after its latest push.",
            agent_id="codex-test",
            project=project,
        )
        assert updated["changed"] is True, updated
        stale = reconcile_without_provider(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            todo_id="todo_github_review",
            ack_receipt=first_ack["ack_receipt"],
        )
        assert stale["skip_reason"] == "stale_ack_receipt", stale
        assert todo(state_file, "todo_github_review")["status"] == "open"

        current_ack = record_ack(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            generated_at="2026-07-24T01:01:00Z",
        )
        assert current_ack["binding"]["todo_revision"] != (
            first_ack["binding"]["todo_revision"]
        )
        with patch(
            "loopx.capabilities.issue_fix.pr_gate_reconcile."
            "build_issue_fix_pr_lifecycle_monitor_packet",
            side_effect=ValueError("private provider detail"),
        ):
            degraded = reconcile_acknowledged_issue_fix_pr_reviews(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root),
                goal_id="example-goal",
                agent_id="codex-test",
                project=project,
                fetch_metadata=True,
                execute=True,
            )
        assert degraded["degraded"] is True, degraded
        assert degraded["failure_count"] == 1, degraded
        assert degraded["results"][0]["error_category"] == (
            "provider_observation_failed"
        ), degraded
        assert "error" not in degraded["results"][0], degraded

        with patch(
            "loopx.capabilities.issue_fix.pr_gate_reconcile."
            "build_issue_fix_pr_lifecycle_monitor_packet",
            return_value={
                "observation": {
                    "repo": "huangruiteng/loopx",
                    "number": 1716,
                    "permalink": PR_URL,
                    "state": "MERGED",
                    "merged_at": "2026-07-24T01:02:00Z",
                },
                "external_reads_performed": True,
            },
        ):
            batch = reconcile_acknowledged_issue_fix_pr_reviews(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root),
                goal_id="example-goal",
                agent_id="codex-test",
                project=project,
                fetch_metadata=True,
                execute=True,
            )
        assert batch["ok"] is True, batch
        assert batch["ack_receipt_count"] == 1, batch
        assert batch["external_read_count"] == 1, batch
        assert batch["terminal_count"] == 1, batch
        assert batch["reconciled_count"] == 1, batch
        assert batch["quota_spend_required"] is False, batch
        assert todo(state_file, "todo_github_review")["status"] == "done"

        with patch(
            "loopx.capabilities.issue_fix.pr_gate_reconcile."
            "build_issue_fix_pr_lifecycle_monitor_packet",
            side_effect=AssertionError(
                "completed acknowledgement must not read provider"
            ),
        ):
            repeated = reconcile_acknowledged_issue_fix_pr_reviews(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root),
                goal_id="example-goal",
                agent_id="codex-test",
                project=project,
                fetch_metadata=True,
                execute=True,
            )
        assert repeated["already_reconciled_count"] == 1, repeated
        assert repeated["external_read_count"] == 0, repeated
        assert repeated["reconciled_count"] == 0, repeated

        prequota = run_heartbeat_pre_quota(
            registry_path=registry_path,
            runtime_root_arg=str(runtime_root),
            goal_id="example-goal",
            agent_id="codex-test",
        )
        assert prequota["ok"] is True, prequota
        assert prequota["continue_to_quota"] is True, prequota
        assert prequota["quota_spend_required"] is False, prequota
        assert prequota["degraded"] is False, prequota

        home = project / "home"
        global_registry = home / ".codex" / "loopx" / "registry.global.json"
        global_registry.parent.mkdir(parents=True)
        global_registry.write_text(
            registry_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        cli_env = dict(os.environ)
        cli_env["HOME"] = str(home)
        cli_env["PYTHONPATH"] = str(ROOT)
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "heartbeat-prequota",
                "-g",
                "example-goal",
                "-a",
                "codex-test",
            ],
            cwd=project,
            env=cli_env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cli.returncode == 0, cli.stderr
        cli_payload = json.loads(cli.stdout)
        assert cli_payload["schema_version"] == "heartbeat_pre_quota_v0", cli_payload
        assert cli_payload["continue_to_quota"] is True, cli_payload
        assert cli_payload["quota_spend_required"] is False, cli_payload

        assert todo(state_file, "todo_other_provider")["status"] == "open"
        native = list_goal_todos(registry_path=registry_path, goal_id="example-goal")
        assert native["authority_read"]["decision_read_from_provider"] is True, native
        assert native["authority_read"]["legacy_fallback_used"] is False, native
        rows = {item["todo_id"]: item for item in native["todos"]}
        assert rows["todo_github_review"]["status"] == "done"
        assert rows["todo_other_provider"]["status"] == "open"



def main() -> int:
    for provider in ("file", "sqlite"):
        check_reconciliation(provider)
    print("issue-fix-pr-review-reconcile-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
