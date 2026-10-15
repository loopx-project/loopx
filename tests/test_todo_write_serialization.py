from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
from threading import Barrier, Lock
from typing import Any, Iterator

import pytest

from loopx.control_plane.coordination import legacy_writer_fence
from loopx.control_plane.coordination.legacy_writer_fence import (
    legacy_coordination_todo_lock_path,
)
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo, update_goal_todo


GOAL_ID = "todo-write-serialization"
AGENT_ID = "fixture-agent"
INITIAL_TODO = "Update this Todo while another Todo is added."
CONCURRENT_TODO = "Preserve this concurrently added Todo."
UPDATE_REASON = "waiting-for-concurrent-dependency"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "updated_at: 2026-09-07T00:00:00+00:00\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "todo-write-serialization-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "adapter": {"kind": "fixture"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path, state_file, runtime_root


def test_concurrent_add_and_update_share_the_goal_todo_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, state_file, runtime_root = _write_fixture(tmp_path)
    target = add_goal_todo(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        role="agent",
        text=INITIAL_TODO,
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    target_id = str(target["todo_id"])
    goal_lock_path = legacy_coordination_todo_lock_path(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
    )

    arrivals = Barrier(2)
    records_guard = Lock()
    locks_by_path: dict[Path, Lock] = {}
    lock_attempts: list[tuple[Path, str | None]] = []

    @contextmanager
    def deterministic_lock(
        path: Path,
        **kwargs: Any,
    ) -> Iterator[Path]:
        with records_guard:
            path_lock = locks_by_path.setdefault(path, Lock())
            lock_attempts.append((path, kwargs.get("operation")))
        if path == goal_lock_path:
            arrivals.wait(timeout=5)
        with path_lock:
            yield path

    monkeypatch.setattr(
        legacy_writer_fence,
        "exclusive_cross_runtime_file_lock",
        deterministic_lock,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        add_future = executor.submit(
            add_goal_todo,
            registry_path=registry_path,
            goal_id=GOAL_ID,
            role="agent",
            text=CONCURRENT_TODO,
            task_class="advancement_task",
            claimed_by=AGENT_ID,
        )
        update_future = executor.submit(
            update_goal_todo,
            registry_path=registry_path,
            goal_id=GOAL_ID,
            todo_id=target_id,
            status="blocked",
            reason=UPDATE_REASON,
            agent_id=AGENT_ID,
        )
        added = add_future.result(timeout=10)
        updated = update_future.result(timeout=10)

    assert added["added"] is True, added
    assert updated["changed"] is True, updated
    assert [attempt for attempt in lock_attempts if attempt[0] == goal_lock_path] == [
        (goal_lock_path, "legacy_coordination_todo_write"),
        (goal_lock_path, "legacy_coordination_todo_write"),
    ]
    assert sorted(
        operation
        for path, operation in lock_attempts
        if path == state_file
    ) == ["todo_add", "todo_update"]

    parsed = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    items = parsed["agent_todos"]["items"]
    todo_ids = [str(item["todo_id"]) for item in items]
    assert len(todo_ids) == len(set(todo_ids)), items

    by_id = {str(item["todo_id"]): item for item in items}
    assert str(added["todo_id"]) in by_id, items
    assert by_id[str(added["todo_id"])]["text"] == f"[P1] {CONCURRENT_TODO}"
    assert by_id[target_id]["status"] == "blocked"
    assert by_id[target_id]["reason"] == UPDATE_REASON
