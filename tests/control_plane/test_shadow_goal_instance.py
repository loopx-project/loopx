from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.control_plane.coordination.runtime_shadow import (
    bootstrap_coordination_runtime_shadow,
    build_runtime_shadow_source_snapshot,
    rollback_coordination_runtime_shadow,
)
from loopx.control_plane.coordination.runtime_shadow_writer_adapter import (
    begin_todo_runtime_shadow_capture,
    write_captured_todo_state,
)
from loopx.control_plane.coordination.shadow_management import (
    ShadowManagementError,
    read_shadow_management_state,
)
from loopx.control_plane.coordination.shadow_goal_scope import shadow_goal_scope
from loopx.control_plane.goals.source_session_recreation import (
    RecreateGoalRequest,
    recreate_goal_instance,
)
from loopx.control_plane.goals.source_session_registration import (
    FreshSourceSessionRegistration,
    register_fresh_source_session_project,
)
from loopx.control_plane.projects.registry_codec import load_project_registry
from loopx.registry import find_registry_goal


GOAL_ID = "shadow-lifetime"
AGENT_ID = "shadow-worker"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _register(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_root = tmp_path / "runtime"
    registry_path = project_root / ".loopx" / "registry.json"
    result = register_fresh_source_session_project(
        FreshSourceSessionRegistration(
            registry_path=registry_path,
            runtime_root=runtime_root,
            operation_id="register-shadow-lifetime",
            project_id="project",
            goal_id=GOAL_ID,
            objective="Qualify exact shadow delivery.",
            non_goals=[],
            acceptance=["A replacement cannot drain the retired Goal outbox."],
            unknowns=[],
            next_effect="Bootstrap the exact shadow.",
            stop_condition="Stop when the exact drain is fenced.",
            project_record={
                "id": "project",
                "kind": "work",
                "path": str(project_root),
            },
            goal_record={
                "id": GOAL_ID,
                "project_id": "project",
                "title": "Shadow lifetime",
                "status": "active",
                "repo": str(project_root),
                "state_file": "GOAL.md",
                "coordination": {
                    "registered_agents": [AGENT_ID],
                    "runtime_shadow": {
                        "schema_version": (
                            "loopx_coordination_runtime_shadow_config_v0"
                        ),
                        "enabled": True,
                        "provider": "file_v0",
                    },
                },
            },
            state_file=project_root / "GOAL.md",
        )
    )
    goal_ref = dict(result["goal_ref"])
    return registry_path, runtime_root, project_root / "GOAL.md", goal_ref


def _bootstrap(
    registry_path: Path,
    runtime_root: Path,
    state_path: Path,
    goal_ref: dict[str, str],
    operation_id: str,
) -> dict[str, object]:
    registry = load_project_registry(registry_path)
    goal = find_registry_goal(registry, GOAL_ID)
    assert goal is not None
    projection, snapshot = build_runtime_shadow_source_snapshot(
        goal=goal,
        runtime_root=runtime_root,
        state_path=state_path,
        registry_path=registry_path,
    )
    result = bootstrap_coordination_runtime_shadow(
        goal=goal,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        operation_id=operation_id,
        source_version=operation_id,
        projection=projection,
        source_snapshot=snapshot,
        goal_ref=goal_ref,
    )
    assert result["status"] in {"applied", "recovered", "replayed"}, result
    return result


def _add_todo(
    registry_path: Path,
    runtime_root: Path,
    state_path: Path,
    text: str,
    todo_id: str,
) -> str:
    original = state_path.read_text(encoding="utf-8")
    heading = "## Agent Todo\n"
    planned = original.replace(
        heading,
        heading
        + f"\n- [ ] {text}\n"
        + f"  <!-- loopx:todo todo_id={todo_id} status=open "
        + f"claimed_by={AGENT_ID} -->\n",
        1,
    )
    capture = begin_todo_runtime_shadow_capture(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        state_path=state_path,
        write_class="todo_add",
        original_text=original,
    )
    write_captured_todo_state(
        capture,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        state_path=state_path,
        text=planned,
    )
    assert capture.outcome.entry_id is not None
    return capture.outcome.entry_id


def _outbox_bytes(runtime_root: Path) -> dict[str, bytes]:
    root = runtime_root / "authority-shadow" / "outbox" / GOAL_ID
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_source_session_bootstrap_is_reachable_from_shipped_cli(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, _state_path, goal_ref = _register(tmp_path)

    command = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "coordination-shadow",
            "bootstrap",
            "--goal-id",
            GOAL_ID,
            "--execute",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "LOOPX_USAGE_PING": "0"},
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )

    assert command.returncode == 0, command.stdout + command.stderr
    assert json.loads(command.stdout)["bootstrap"]["status"] in {
        "applied",
        "recovered",
        "replayed",
    }
    state = read_shadow_management_state(runtime_root, GOAL_ID)
    assert state is not None
    assert state["schema_version"] == "loopx_shadow_management_state_v2"
    assert state["binding"]["goal_ref"] == goal_ref


def test_source_session_bootstrap_uses_the_current_typed_goal_scope(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, state_path, goal_ref = _register(tmp_path)
    goal = find_registry_goal(load_project_registry(registry_path), GOAL_ID)
    assert goal is not None
    projection, snapshot = build_runtime_shadow_source_snapshot(
        goal=goal,
        runtime_root=runtime_root,
        state_path=state_path,
        registry_path=registry_path,
    )
    with shadow_goal_scope(registry_path, goal_id=GOAL_ID) as scope:
        result = bootstrap_coordination_runtime_shadow(
            goal=scope.goal,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            operation_id="bootstrap:implicit-goal-ref",
            source_version="bootstrap:implicit-goal-ref",
            projection=projection,
            source_snapshot=snapshot,
            goal_ref=scope.goal_ref,
        )

    assert result["status"] in {"applied", "recovered", "replayed"}, result
    _add_todo(
        registry_path,
        runtime_root,
        state_path,
        "Capture work after an implicit exact bootstrap.",
        "todo_shadow_implicit",
    )
    state = read_shadow_management_state(runtime_root, GOAL_ID)
    assert state is not None
    assert state["binding"]["goal_ref"] == goal_ref


def test_recreated_goal_cannot_capture_or_drain_the_retired_shadow(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, state_path, goal_a = _register(tmp_path)
    first = _bootstrap(
        registry_path,
        runtime_root,
        state_path,
        goal_a,
        "bootstrap:goal-a",
    )
    _add_todo(
        registry_path,
        runtime_root,
        state_path,
        "Persist Goal A work before replacement.",
        "todo_shadow_a",
    )
    pending_a = _outbox_bytes(runtime_root)
    in_flight_original = state_path.read_text(encoding="utf-8")
    in_flight_a = begin_todo_runtime_shadow_capture(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        state_path=state_path,
        write_class="todo_update",
        original_text=in_flight_original,
    )

    recreated = recreate_goal_instance(
        RecreateGoalRequest(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            goal_instance_id=goal_a["goal_instance_id"],
            operation_id="recreate-shadow-lifetime",
        )
    )
    goal_b = dict(recreated["goal_ref"])
    assert goal_b != goal_a

    with pytest.raises(ShadowManagementError, match="retired Goal instance"):
        write_captured_todo_state(
            in_flight_a,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            state_path=state_path,
            text=in_flight_original.replace(
                "Persist Goal A",
                "Continue retired Goal A",
            ),
        )
    assert state_path.read_text(encoding="utf-8") == in_flight_original

    stale = adapter.drain_local_authority_shadow_outbox(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        max_entries=10,
        budget_seconds=10,
        lock_timeout_seconds=2,
    )
    assert stale.outcome == "stopped"
    assert stale.reason_code == "stale_goal_instance"
    assert stale.delivered == 0
    assert _outbox_bytes(runtime_root) == pending_a

    original = state_path.read_text(encoding="utf-8")
    capture_b = begin_todo_runtime_shadow_capture(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        state_path=state_path,
        write_class="todo_add",
        original_text=original,
    )
    with pytest.raises(ShadowManagementError, match="durable shadow preparation"):
        write_captured_todo_state(
            capture_b,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            state_path=state_path,
            text=original.replace("Persist Goal A", "Mutate Goal B"),
        )
    assert state_path.read_text(encoding="utf-8") == original

    goal = find_registry_goal(load_project_registry(registry_path), GOAL_ID)
    assert goal is not None
    retired = rollback_coordination_runtime_shadow(
        goal=goal,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        operation_id="rollback:goal-a",
        expected_provider_revision=str(first["provider_revision"]),
        projection={},
        source_snapshot={"state_path": str(state_path)},
    )
    assert retired["status"] == "applied", retired
    archived = Path(str(retired["outbox_archive_path"]))
    assert {
        str(path.relative_to(archived)): path.read_bytes()
        for path in archived.rglob("*")
        if path.is_file()
    } == pending_a

    _bootstrap(
        registry_path,
        runtime_root,
        state_path,
        goal_b,
        "bootstrap:goal-b",
    )
    _add_todo(
        registry_path,
        runtime_root,
        state_path,
        "Persist Goal B work after replacement.",
        "todo_shadow_b",
    )
    current = adapter.drain_local_authority_shadow_outbox(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        max_entries=10,
        budget_seconds=10,
        lock_timeout_seconds=2,
    )
    assert current.outcome == "drained"
    assert current.delivered == 1
    replay = adapter.drain_local_authority_shadow_outbox(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        max_entries=10,
        budget_seconds=10,
        lock_timeout_seconds=2,
    )
    assert replay.outcome == "nothing_pending"
