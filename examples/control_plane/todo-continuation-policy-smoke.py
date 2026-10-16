#!/usr/bin/env python3
"""Exercise TS-owned Todo continuation policy through the public CLI."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    import_fixture_authority,
    run_json_cli,
    run_json_cli_result,
    write_fixture_registry,
)
from loopx.status import parse_active_state_todos  # noqa: E402


GOAL_ID = "continuation-policy-fixture"
PEER_ALPHA = "codex-alpha"
PEER_BETA = "codex-beta"


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Agent Todo\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="continuation-policy-fixture",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[PEER_ALPHA, PEER_BETA],
        quota_allowed_slots=None,
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, state_file


def add_todo(registry_path: Path, text: str) -> dict:
    return run_json_cli(
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--text",
        text,
        "--task-class",
        "advancement_task",
        "--claimed-by",
        PEER_ALPHA,
        registry_path=registry_path,
    )


def agent_todo(state_file: Path, todo_id: str) -> dict:
    items = parse_active_state_todos(
        state_file.read_text(encoding="utf-8")
    )["agent_todos"]["items"]
    return next(item for item in items if item["todo_id"] == todo_id)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-continuation-policy-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))

        same_peer = add_todo(registry_path, "Complete one bounded migration slice.")
        same_peer_result = run_json_cli(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            same_peer["todo_id"],
            "--claimed-by",
            PEER_ALPHA,
            "--agent-id",
            PEER_ALPHA,
            "--evidence",
            "focused validation passed",
            "--next-agent-todo",
            "Continue a read-only validation lane.",
            "--next-continuation-policy",
            "same_agent_non_delivery",
            registry_path=registry_path,
        )
        same_peer_successor = agent_todo(
            state_file, same_peer_result["next_todos"][0]["todo_id"]
        )
        assert same_peer_successor["claimed_by"] == PEER_ALPHA
        assert (
            same_peer_successor["continuation_policy"]
            == "same_agent_non_delivery"
        )
        assert same_peer_successor["unblocks_todo_id"] == same_peer["todo_id"]

        independent = add_todo(registry_path, "Prepare an independent handoff.")
        independent_result = run_json_cli(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            independent["todo_id"],
            "--claimed-by",
            PEER_ALPHA,
            "--agent-id",
            PEER_ALPHA,
            "--evidence",
            "handoff boundary validated",
            "--next-agent-todo",
            "Independently review the delivery.",
            "--next-claimed-by",
            PEER_BETA,
            "--next-excluded-agent",
            PEER_ALPHA,
            "--next-continuation-policy",
            "independent_handoff",
            registry_path=registry_path,
        )
        independent_successor = agent_todo(
            state_file, independent_result["next_todos"][0]["todo_id"]
        )
        assert independent_successor["claimed_by"] == PEER_BETA
        assert independent_successor["excluded_agents"] == [PEER_ALPHA]

        conflict = add_todo(registry_path, "Reject an impossible assignment.")
        returncode, rejected = run_json_cli_result(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            conflict["todo_id"],
            "--claimed-by",
            PEER_ALPHA,
            "--agent-id",
            PEER_ALPHA,
            "--evidence",
            "must remain unchanged",
            "--next-agent-todo",
            "Review your own delivery.",
            "--next-claimed-by",
            PEER_ALPHA,
            "--next-excluded-agent",
            PEER_ALPHA,
            registry_path=registry_path,
        )
        assert returncode == 1, rejected
        assert "cannot also appear in excluded_agents" in rejected["error"]
        assert agent_todo(state_file, conflict["todo_id"])["status"] == "open"

        missing_evidence = add_todo(registry_path, "Require self-merge evidence.")
        returncode, rejected = run_json_cli_result(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            missing_evidence["todo_id"],
            "--claimed-by",
            PEER_ALPHA,
            "--agent-id",
            PEER_ALPHA,
            "--self-merged",
            registry_path=registry_path,
        )
        assert returncode == 1, rejected
        assert "--self-merged requires --evidence" in rejected["error"], rejected
        assert agent_todo(state_file, missing_evidence["todo_id"])["status"] == "open"

    print("todo-continuation-policy-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
