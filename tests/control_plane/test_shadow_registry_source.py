"""Real registry/source races must reject before opening a capture lineage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.file_lock import exclusive_cross_runtime_file_lock
from loopx.control_plane.coordination.runtime_shadow import (
    build_runtime_shadow_source_snapshot,
    bootstrap_coordination_runtime_shadow,
)
from loopx.control_plane.coordination.shadow_management import (
    ShadowManagementError,
    read_shadow_management_state,
)


def workspace(root: Path):
    state = root / "state.md"
    state.write_text(
        "---\ngoal_id: witness-goal\nhandoff_mode: hard_lease\n---\n## Agent Todo\n"
    )
    runtime, registry = root / "runtime", root / "registry.json"
    goal = {
        "id": "witness-goal",
        "repo": str(root),
        "state_file": state.name,
        "coordination": {
            "registered_agents": ["agent-a"],
            "runtime_shadow": {
                "schema_version": "loopx_coordination_runtime_shadow_config_v0",
                "enabled": True,
                "provider": "file_v0",
            },
        },
    }
    data = {"common_runtime_root": str(runtime), "goals": [goal]}
    registry.write_text(json.dumps(data))
    return state, runtime, registry, goal, data


def capture(state, runtime, registry, goal):
    return build_runtime_shadow_source_snapshot(
        goal=goal, runtime_root=runtime, state_path=state, registry_path=registry
    )


def bootstrap(runtime, goal, projection, snapshot):
    return bootstrap_coordination_runtime_shadow(
        goal=goal,
        runtime_root=runtime,
        goal_id=goal["id"],
        operation_id="bootstrap:registry-witness",
        source_version="state:1",
        projection=projection,
        source_snapshot=snapshot,
        goal_ref=None,
    )


@pytest.mark.parametrize(
    "change",
    ["delete_goal", "move_state", "move_runtime", "remove_agent", "disable_capture"],
)
def test_stale_registry_rejects_before_bootstrap(tmp_path: Path, change: str):
    state, runtime, registry, goal, data = workspace(tmp_path)
    projection, snapshot = capture(state, runtime, registry, goal)
    original_state = state.read_bytes()
    changed = json.loads(json.dumps(data))
    if change == "delete_goal":
        changed["goals"] = []
    elif change == "move_state":
        changed["goals"][0]["state_file"] = "replacement.md"
    elif change == "move_runtime":
        changed["common_runtime_root"] = str(tmp_path / "replacement")
    elif change == "remove_agent":
        changed["goals"][0]["coordination"]["registered_agents"] = []
    else:
        changed["goals"][0]["coordination"]["runtime_shadow"]["enabled"] = False
    registry.write_text(json.dumps(changed))
    result = bootstrap(runtime, goal, projection, snapshot)
    assert result["status"] == "failed", result
    assert result["reason_code"] == "source_registry_changed_retry"
    assert read_shadow_management_state(runtime, goal["id"]) is None
    assert state.read_bytes() == original_state


def test_stale_caller_goal_is_not_bound_to_a_new_registry_digest(tmp_path: Path):
    state, runtime, registry, goal, data = workspace(tmp_path)
    changed = json.loads(json.dumps(data))
    changed["goals"][0]["coordination"]["registered_agents"] = []
    registry.write_text(json.dumps(changed))
    with pytest.raises(ShadowManagementError, match="source_registry_changed_retry"):
        capture(state, runtime, registry, goal)


def test_registry_change_during_python_projection_is_rejected(
    tmp_path: Path, monkeypatch
):
    from loopx.control_plane.coordination import runtime_shadow

    state, runtime, registry, goal, data = workspace(tmp_path)
    original = runtime_shadow._build_runtime_shadow_source_snapshot

    def racing_projection(**arguments):
        result = original(**arguments)
        registry.write_text(json.dumps({**data, "goals": []}))
        return result

    monkeypatch.setattr(
        runtime_shadow, "_build_runtime_shadow_source_snapshot", racing_projection
    )
    with pytest.raises(ValueError, match="registration changed"):
        capture(state, runtime, registry, goal)


def test_python_registry_writer_cannot_deadlock_native_bootstrap(tmp_path: Path):
    state, runtime, registry, goal, _ = workspace(tmp_path)
    projection, snapshot = capture(state, runtime, registry, goal)
    with exclusive_cross_runtime_file_lock(registry, operation="registry-source-race"):
        result = bootstrap(runtime, goal, projection, snapshot)
        assert result["reason_code"] == "source_registry_busy_retry", result
        assert read_shadow_management_state(runtime, goal["id"]) is None
    assert bootstrap(runtime, goal, projection, snapshot)["status"] == "applied"


def test_strict_registry_envelope_keeps_existing_codec(tmp_path: Path):
    from loopx.control_plane.projects.registry_codec import (
        _payload_digest,
        STRICT_SCHEMA_VERSION,
    )

    state, runtime, registry, goal, data = workspace(tmp_path)
    registry.write_text(
        json.dumps(
            [
                {
                    "schema_version": STRICT_SCHEMA_VERSION,
                    "minimum_writer_protocol": "goal_instance_v1",
                    "payload_sha256": _payload_digest(data),
                },
                data,
            ]
        )
    )
    projection, snapshot = capture(state, runtime, registry, goal)
    assert bootstrap(runtime, goal, projection, snapshot)["status"] == "applied"


@pytest.mark.parametrize("writer", ["sync", "activation", "deletion"])
def test_global_registry_writers_respect_native_promotion_marker(tmp_path, writer):
    from loopx.file_lock import exclusive_mutation_file_lock, LockAcquireTimeoutError
    from loopx.global_registry import sync_project_registry_to_global
    from loopx.control_plane.goals.activation_service import set_goal_activation_state
    from loopx.control_plane.goals.deletion_service import delete_stopped_goal

    _state, runtime, registry, goal, _data = workspace(tmp_path)
    global_path = runtime / "registry.global.json"

    def sync():
        return sync_project_registry_to_global(
            registry_path=registry, runtime_root_override=str(runtime), dry_run=False
        )

    assert sync()["ok"]
    if writer == "deletion":
        assert set_goal_activation_state(
            registry_path=global_path,
            goal_id=goal["id"],
            state="stopped",
            actor_kind="owner",
            execute=True,
        )["ok"]
    action = (
        sync
        if writer == "sync"
        else (
            lambda: set_goal_activation_state(
                registry_path=global_path,
                goal_id=goal["id"],
                state="stopped",
                actor_kind="owner",
                execute=True,
            )
        )
        if writer == "activation"
        else (
            lambda: delete_stopped_goal(
                registry_path=global_path, goal_id=goal["id"], execute=True
            )
        )
    )
    before = (registry.read_bytes(), global_path.read_bytes())
    # This is the exact marker protocol held by native promotion. No mock can
    # make a kernel-only writer pass this exclusion test.
    with exclusive_mutation_file_lock(
        global_path, operation="native-promotion-fixture"
    ):
        with pytest.raises(LockAcquireTimeoutError):
            action()
        assert (registry.read_bytes(), global_path.read_bytes()) == before
    assert action()["ok"]
