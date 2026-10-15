#!/usr/bin/env python3
"""Smoke-test active-state todo writers under a contended file lock."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.file_lock import exclusive_file_lock  # noqa: E402
from loopx.status import parse_active_state_todos  # noqa: E402


GOAL_ID = "todo-concurrent-write-lock-goal"
CHILD_ADD_TODO = "[P1] Child add should preserve the parent todo written under lock."
PARENT_ADD_TODO = "Parent todo written while child add waits on the lock."
UPDATE_TARGET_TODO = "[P1] Update target todo should keep unrelated concurrent additions."
PARENT_UPDATE_TODO = "Parent todo written while child update waits on the lock."


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
        "## Objective\n\n"
        "Exercise todo writer locking.\n\n"
        "## Agent Todo\n\n"
        "- [ ] Initial visible work item.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "todo-concurrency-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
                        "authority_sources": [],
                        "coordination": {
                            "registered_agents": ["codex-main-control", "codex-side-bypass"],
                            "agent_model": "peer_v1",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, state_file


def cli_args(registry_path: Path, *args: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--format",
        "json",
        *args,
    ]


def run_cli(registry_path: Path, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        cli_args(registry_path, *args),
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def start_cli(registry_path: Path, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cli_args(registry_path, *args),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_json(process: subprocess.Popen[str]) -> dict[str, Any]:
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    return json.loads(stdout)


def assert_waiting_on_lock(process: subprocess.Popen[str], label: str) -> None:
    time.sleep(0.35)
    assert process.poll() is None, (
        f"{label} process finished before the parent released the lock"
    )


def append_agent_todo(state_file: Path, text: str) -> None:
    with state_file.open("a", encoding="utf-8") as file:
        file.write(f"- [ ] {text}\n")


def agent_items(state_file: Path) -> list[dict[str, Any]]:
    parsed = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return parsed["agent_todos"]["items"]


def find_item(state_file: Path, text: str) -> dict[str, Any]:
    return next(item for item in agent_items(state_file) if item.get("text") == text)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-lock-smoke-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))

        with exclusive_file_lock(state_file):
            add_process = start_cli(
                registry_path,
                "todo",
                "add",
                "--goal-id",
                GOAL_ID,
                "--role",
                "agent",
                "--text",
                CHILD_ADD_TODO,
            )
            assert_waiting_on_lock(add_process, "todo add")
            append_agent_todo(state_file, PARENT_ADD_TODO)
        add_result = wait_json(add_process)
        assert add_result["added"] is True, add_result
        assert find_item(state_file, CHILD_ADD_TODO), state_file.read_text(encoding="utf-8")
        assert find_item(state_file, PARENT_ADD_TODO), state_file.read_text(encoding="utf-8")

        target = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            UPDATE_TARGET_TODO,
        )
        target_id = target["todo_id"]

        with exclusive_file_lock(state_file):
            update_process = start_cli(
                registry_path,
                "todo",
                "update",
                "--goal-id",
                GOAL_ID,
                "--todo-id",
                target_id,
                "--agent-id",
                "codex-side-bypass",
                "--status",
                "blocked",
                "--reason",
                "waiting-on-concurrent-state",
                "--claimed-by",
                "codex-side-bypass",
            )
            assert_waiting_on_lock(update_process, "todo update")
            append_agent_todo(state_file, PARENT_UPDATE_TODO)
        update_result = wait_json(update_process)
        assert update_result["changed"] is True, update_result
        updated_target = find_item(state_file, UPDATE_TARGET_TODO)
        assert updated_target["status"] == "blocked", updated_target
        assert updated_target["reason"] == "waiting-on-concurrent-state", updated_target
        assert updated_target["claimed_by"] == "codex-side-bypass", updated_target
        assert find_item(state_file, PARENT_UPDATE_TODO), state_file.read_text(encoding="utf-8")

    print("todo-concurrent-write-lock-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
