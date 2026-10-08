"""Real registry/source races must reject before opening a capture lineage."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
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
from loopx.control_plane.coordination.local_authority_shadow_adapter import (
    read_local_authority_shadow,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    ProjectionValueError,
)
from tests.control_plane.shadow_e2e_fixture import REPO, workspace as cli_workspace


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


@pytest.mark.parametrize("kind", ["unsupported_name", "file_symlink", "directory_symlink", "parent_symlink", "non_file"])
def test_real_cli_rejects_unsafe_lease_source_before_bootstrap(tmp_path: Path, kind):
    ws = cli_workspace(tmp_path / "workspace", bootstrap=False)
    directory = ws.runtime / "goals" / ws.goal / "task-leases"
    directory.mkdir(parents=True)
    outside = tmp_path / "retained-source"
    outside.mkdir()
    original = json.dumps({"goal_id": ws.goal, "todo_id": "todo_retained", "status": "released", "version": 4, "lease_epoch": 2})
    retained = outside / "todo_retained.json"
    retained.write_text(original)
    if kind == "unsupported_name":
        (directory / "unexpected lease.json").write_text(original)
    elif kind == "file_symlink":
        (directory / retained.name).symlink_to(retained)
    elif kind == "non_file":
        (directory / retained.name).mkdir()
    elif kind == "directory_symlink":
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
    else:
        directory.rmdir()
        directory.parent.rmdir()
        directory.parent.symlink_to(outside, target_is_directory=True)
    result = ws.cli("coordination-shadow", "bootstrap", "--execute", success=False)
    assert result["ok"] is False, result
    assert "source_lease_inventory_invalid" in result["error"]
    assert not (ws.runtime / "authority-shadow" / "file-v0").exists()
    assert not (ws.runtime / "authority-transition").exists()
    assert retained.read_text() == original
    assert list(outside.iterdir()) == [retained]


@pytest.mark.parametrize("extension", [
    {}, {"future_extension": None}, {"future_extension": False},
    {"future_extension": {"missing_value": None, "flag": False}},
])
def test_source_bootstrap_preserves_complete_terminal_lease(tmp_path: Path, extension):
    state, runtime, registry, goal, _ = workspace(tmp_path)
    todo_id = "todo_0123456789ab"
    state.write_text(state.read_text() +
        f'- [x] Retained work\n  <!-- loopx:todo todo_id={todo_id} task_class=advancement_task -->\n')
    expected = {
        "schema_version": "task_lease_v0", "goal_id": goal["id"],
        "todo_id": todo_id, "owner": "agent-a", "version": 2,
        "lease_epoch": 1, "status": "released", "released_at": "later",
        "idempotency_key": "retained-identity", **extension,
    }
    lease = runtime / "goals" / goal["id"] / "task-leases" / f"{todo_id}.json"
    lease.parent.mkdir(parents=True)
    original = json.dumps(expected, indent=2).encode()
    lease.write_bytes(original)
    projection, snapshot = capture(state, runtime, registry, goal)
    assert projection["leases"] == [expected]
    assert snapshot["lease_inventory"] == [{
        "name": lease.name, "bytes_sha256": "sha256:" + hashlib.sha256(original).hexdigest(),
    }]
    assert bootstrap(runtime, goal, projection, snapshot)["status"] == "applied"
    # The native File provider must persist the entire record, not only the
    # fields consumed by current lease decisions. The source remains untouched.
    view = read_local_authority_shadow(runtime_root=runtime, goal_id=goal["id"], scan_limit=1)
    assert view["status"] == "loaded", view
    assert view["proof"]["transactions"][0]["projection"]["leases"] == [expected]
    assert lease.read_bytes() == original


@pytest.mark.parametrize("change", ["goal_identity", "source_bytes"])
def test_terminal_lease_source_rejects_drift_before_publication(tmp_path: Path, change):
    state, runtime, registry, goal, _ = workspace(tmp_path)
    todo_id = "todo_0123456789ab"
    state.write_text(state.read_text() +
        f'- [x] Retained work\n  <!-- loopx:todo todo_id={todo_id} task_class=advancement_task -->\n')
    lease = runtime / "goals" / goal["id"] / "task-leases" / f"{todo_id}.json"
    lease.parent.mkdir(parents=True)
    record = {"schema_version": "task_lease_v0", "goal_id": goal["id"],
        "todo_id": todo_id, "owner": "agent-a", "status": "released", "version": 2}
    lease.write_text(json.dumps(record))
    projection, snapshot = capture(state, runtime, registry, goal)
    if change == "goal_identity":
        lease.write_text(json.dumps({**record, "goal_id": "another-goal"}))
        with pytest.raises(ProjectionValueError, match="identity"):
            capture(state, runtime, registry, goal)
    else:
        lease.write_text(json.dumps(record, indent=2))
    changed_bytes = lease.read_bytes()
    result = bootstrap(runtime, goal, projection, snapshot)
    assert result["status"] == "failed", result
    assert result["reason_code"] == (
        "source_lease_identity_mismatch" if change == "goal_identity" else "source_changed_retry"
    )
    assert read_shadow_management_state(runtime, goal["id"]) is None
    assert lease.read_bytes() == changed_bytes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_terminal_lease_survives_public_cutover_and_original_recovery(tmp_path: Path, provider):
    ws = cli_workspace(tmp_path, bootstrap=False)
    todo_id = "todo_0123456789ab"
    ws.state.write_text(ws.state.read_text() +
        f'- [x] Retained work\n  <!-- loopx:todo todo_id={todo_id} task_class=advancement_task -->\n')
    expected = {
        "schema_version": "task_lease_v0", "goal_id": ws.goal, "todo_id": todo_id,
        "owner": "agent-a", "version": 2, "lease_epoch": 1, "status": "released",
        "released_at": "2026-09-01T00:00:00Z", "idempotency_key": "retained-identity",
        "future_extension": {"missing_value": None, "flag": False},
    }
    lease = ws.runtime / "goals" / ws.goal / "task-leases" / f"{todo_id}.json"
    lease.parent.mkdir(parents=True)
    original = json.dumps(expected, indent=2).encode()
    lease.write_bytes(original)
    if provider == "sqlite":
        selection = subprocess.run([os.environ.get("LOOPX_CONTROL_PLANE_NODE", "node"),
            "--no-warnings", "--experimental-strip-types", "--experimental-sqlite",
            str((REPO / "loopx/control_plane/coordination/local_authority_provider.ts").resolve()), "--runtime-root",
            str(ws.runtime), "--goal-id", ws.goal, "--execute"], cwd=REPO,
            capture_output=True, text=True, timeout=45, check=True)
        assert json.loads(selection.stdout) == {
            "ok": True, "provider": "sqlite", "changed": True, "executed": True,
        }
    assert ws.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    for index in range(3):
        ws.add(f"Independent migration work {index}")
    assert ws.drain(budget_seconds="60")["ok"] is True
    preview = ws.cli("coordination-shadow", "promote")
    assert preview["promotion"]["status"] == "preview_ready", preview
    saved = tmp_path / "reviewed.json"
    saved.write_text(json.dumps(preview))
    applied = ws.cli("coordination-shadow", "promote", "--reviewed-plan", str(saved), "--execute")
    assert applied["promotion"]["canonical_authority"] == f"{provider}_v0"
    assert ws.cli("task-lease", "inspect", "--todo-id", todo_id)["lease"] == expected
    later = ws.add("Later canonical work must survive recovery")
    ws.state.unlink()
    recovered = ws.cli("coordination-shadow", "recover-promotion", "--reviewed-plan", str(saved), "--execute")
    assert recovered["promotion"]["status"] == "replayed", recovered
    assert ws.cli("task-lease", "inspect", "--todo-id", todo_id)["lease"] == expected
    retained = ws.cli("todo", "list", "--todo-id", later["todo_id"])
    assert retained["todo"]["text"] == "Later canonical work must survive recovery"
    assert lease.read_bytes() == original
    assert not ws.state.exists()


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
