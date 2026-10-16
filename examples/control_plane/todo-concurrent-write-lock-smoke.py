#!/usr/bin/env python3
"""Smoke-test concurrent canonical writes while readable projection is locked."""

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
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.status import parse_active_state_todos  # noqa: E402


GOAL_ID = "todo-concurrent-write-lock-goal"
CHILD_ADD_TODO = "[P1] Child add should preserve the concurrently committed parent todo."
PARENT_ADD_TODO = "[P1] Parent todo committed while child add awaits readable projection."
UPDATE_TARGET_TODO = "[P1] Update target todo should keep unrelated concurrent additions."
PARENT_UPDATE_TODO = "[P1] Parent todo committed while child update awaits readable projection."


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
    import_fixture_authority(registry_path, GOAL_ID)
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
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        raise AssertionError(f"{label} finished before display delivery: {stdout} {stderr}")


def authority(registry_path: Path) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    result = read_canonical_todos_if_promoted(
        runtime_root=Path(registry["common_runtime_root"]), goal_id=GOAL_ID,
    )
    assert result is not None and result["legacy_fallback_used"] is False, result
    return result


def wait_for_committed_texts(
    registry_path: Path, texts: set[str], *, blocked_todo_id: str | None = None,
) -> None:
    # The five-second display-lock deadline is unchanged. Authority commits
    # must be observable independently while both CLI callers await projection.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        rows = authority(registry_path)["todos"]
        if texts <= {row["text"] for row in rows} and (blocked_todo_id is None or
                any(row["todo_id"] == blocked_todo_id and row["status"] == "blocked" for row in rows)):
            return
        time.sleep(0.05)
    raise AssertionError("canonical writes did not commit before display delivery")


def agent_items(state_file: Path) -> list[dict[str, Any]]:
    parsed = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return parsed["agent_todos"]["items"]


def find_item(state_file: Path, text: str) -> dict[str, Any]:
    return next(item for item in agent_items(state_file) if item.get("text") == text)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-lock-smoke-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))

        child_add = ("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
                     "--text", CHILD_ADD_TODO, "--operation-id", "child-add")
        with exclusive_file_lock(state_file):
            add_process = start_cli(registry_path, *child_add)
            parent_add = start_cli(registry_path, "todo", "add", "--goal-id", GOAL_ID,
                                   "--role", "agent", "--text", PARENT_ADD_TODO,
                                   "--operation-id", "parent-add")
            assert_waiting_on_lock(add_process, "todo add")
            wait_for_committed_texts(registry_path, {CHILD_ADD_TODO, PARENT_ADD_TODO})
        assert wait_json(add_process)["added"] is True
        assert wait_json(parent_add)["added"] is True
        assert find_item(state_file, CHILD_ADD_TODO)
        assert find_item(state_file, PARENT_ADD_TODO)
        before_replay = authority(registry_path)
        run_cli(registry_path, *child_add)
        after_replay = authority(registry_path)
        assert after_replay["provider_revision"] == before_replay["provider_revision"]
        assert after_replay["todos"] == before_replay["todos"]

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

        child_update = ("todo", "update", "--goal-id", GOAL_ID, "--todo-id", target_id,
                        "--agent-id", "codex-side-bypass", "--status", "blocked",
                        "--reason", "waiting-on-concurrent-state", "--claimed-by",
                        "codex-side-bypass", "--update-operation-id", "child-update")
        with exclusive_file_lock(state_file):
            update_process = start_cli(registry_path, *child_update)
            parent_update = start_cli(registry_path, "todo", "add", "--goal-id", GOAL_ID,
                                      "--role", "agent", "--text", PARENT_UPDATE_TODO,
                                      "--operation-id", "parent-during-update")
            assert_waiting_on_lock(update_process, "todo update")
            wait_for_committed_texts(registry_path, {PARENT_UPDATE_TODO}, blocked_todo_id=target_id)
            assert next(row for row in authority(registry_path)["todos"]
                        if row["todo_id"] == target_id)["status"] == "blocked"
        assert wait_json(update_process)["changed"] is True
        assert wait_json(parent_update)["added"] is True
        updated_target = find_item(state_file, UPDATE_TARGET_TODO)
        assert updated_target["status"] == "blocked", updated_target
        assert updated_target["reason"] == "waiting-on-concurrent-state", updated_target
        assert updated_target["claimed_by"] == "codex-side-bypass", updated_target
        assert find_item(state_file, PARENT_UPDATE_TODO), state_file.read_text(encoding="utf-8")

        before_replay = authority(registry_path)
        run_cli(registry_path, *child_update)
        after_replay = authority(registry_path)
        assert after_replay["provider_revision"] == before_replay["provider_revision"]
        assert after_replay["todos"] == before_replay["todos"]
        assert len([row for row in after_replay["todos"] if row["text"] == PARENT_UPDATE_TODO]) == 1

    print("todo-concurrent-write-lock-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
