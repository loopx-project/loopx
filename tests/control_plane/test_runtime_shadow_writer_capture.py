from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.control_plane.work_items.task_lease import (
    acquire_task_lease,
    release_task_lease,
    renew_task_lease,
    transfer_task_lease,
)
from loopx.todos import add_goal_todo, update_goal_todo


GOAL_ID = "runtime-shadow-writer"


def _fixture(
    tmp_path: Path,
    *,
    enabled: bool,
    handoff_mode: str = "hard_lease",
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        f"handoff_mode: {handoff_mode}\n"
        "updated_at: 2026-09-04T00:00:00+00:00\n"
        "---\n\n## Agent Todo\n\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    coordination: dict[str, object] = {
        "agent_model": "peer_v1",
        "registered_agents": ["agent-a", "agent-b"],
    }
    if enabled:
        coordination["runtime_shadow"] = {
            "enabled": True,
            "schema_version": "loopx_coordination_runtime_shadow_config_v0",
            "provider": "file_v0",
        }
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": coordination,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if enabled:
        boot = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--format", "json", "coordination-shadow", "bootstrap", "--goal-id", GOAL_ID, "--execute"],
            cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, timeout=30)
        assert boot.returncode == 0, boot.stdout + boot.stderr
    return registry, state, runtime_root


def test_runtime_shadow_todo_writer_captures_full_records_and_reuses_one_store(
    tmp_path: Path,
) -> None:
    registry, _state, runtime_root = _fixture(tmp_path, enabled=True)

    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Retain complete canonical Todo fields.",
        task_class="advancement_task",
        action_kind="implement",
        claimed_by="agent-a",
    )
    updated = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(added["todo_id"]),
        note="transaction-bound",
        agent_id="agent-a",
    )

    assert added["coordination_runtime_shadow"]["outcome"] == "delivered"
    assert updated["coordination_runtime_shadow"]["outcome"] == "delivered"
    view = adapter.read_local_authority_shadow(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        scan_limit=10,
    )
    head = view["head"]
    assert head["schema_version"] == "loopx_coordination_runtime_shadow_projection_v0"
    assert head["todos"][0]["text"] == "Retain complete canonical Todo fields."
    assert head["todos"][0]["note"] == "transaction-bound"
    assert head["todo_read_model"]["todo_count"] == 1
    assert view["cursor"] == "3"
    assert not (runtime_root / "authority-shadow" / "file" / GOAL_ID).exists()


def test_runtime_shadow_todo_claim_preserves_claim_write_class(tmp_path: Path) -> None:
    registry, _state, runtime_root = _fixture(
        tmp_path,
        enabled=True,
        handoff_mode="legacy",
    )
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Qualify the legacy claim path.",
        task_class="advancement_task",
    )

    claimed = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(added["todo_id"]),
        claimed_by="agent-a",
        agent_id="agent-a",
        claim_only=True,
    )

    assert claimed["coordination_runtime_shadow"]["outcome"] == "delivered"
    view = adapter.read_local_authority_shadow(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        scan_limit=10,
    )
    receipt = view["proof"]["transactions"][-1]["receipts"][0]
    assert receipt["write_class"] == "todo_claim"


def test_runtime_shadow_todo_writer_is_zero_effect_by_default(tmp_path: Path) -> None:
    registry, _state, runtime_root = _fixture(tmp_path, enabled=False)

    result = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Default-off capture.",
        task_class="advancement_task",
    )

    assert result["coordination_runtime_shadow"]["outcome"] == "no_transaction"
    assert result["coordination_runtime_shadow"]["reason_code"] == "shadow_disabled"
    assert not (runtime_root / "authority-shadow").exists()


@pytest.mark.parametrize("configuration_cleared", [False, True])
def test_runtime_shadow_native_lease_writers_capture_complete_records(
    tmp_path: Path, configuration_cleared: bool,
) -> None:
    registry, _state, runtime_root = _fixture(tmp_path, enabled=True)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Capture the native lease transaction.",
        task_class="advancement_task",
    )
    todo_id = str(added["todo_id"])
    if configuration_cleared:
        config = json.loads(registry.read_text(encoding="utf-8"))
        config["goals"][0]["coordination"].pop("runtime_shadow")
        registry.write_text(json.dumps(config), encoding="utf-8")

    acquired = acquire_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        owner="agent-a",
        idempotency_key="runtime-shadow-lease",
        write_scopes=["loopx/**"],
        ttl_seconds=120,
    )
    renewed = renew_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        owner="agent-a",
        idempotency_key="runtime-shadow-lease",
        ttl_seconds=180,
        expected_version=int(acquired["lease"]["version"]),
    )
    transferred = transfer_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        owner="agent-a",
        idempotency_key="runtime-shadow-lease",
        new_owner="agent-b",
        new_idempotency_key="runtime-shadow-lease-b",
        ttl_seconds=180,
        expected_version=int(renewed["lease"]["version"]),
    )
    released = release_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        owner="agent-b",
        idempotency_key="runtime-shadow-lease-b",
        expected_version=int(transferred["lease"]["version"]),
    )

    assert acquired["coordination_runtime_shadow"]["outcome"] == "delivered"
    assert renewed["coordination_runtime_shadow"]["outcome"] == "delivered"
    assert transferred["coordination_runtime_shadow"]["outcome"] == "delivered"
    assert released["coordination_runtime_shadow"]["outcome"] == "delivered"
    view = adapter.read_local_authority_shadow(runtime_root=runtime_root, goal_id=GOAL_ID)
    lease = view["head"]["leases"][0]
    assert lease["goal_id"] == GOAL_ID
    assert lease["owner"] == "agent-b"
    assert lease["idempotency_key"] == "runtime-shadow-lease-b"
    assert lease["write_scopes"] == ["loopx/**"]
    assert lease["status"] == "released"


def test_runtime_shadow_native_lease_writer_is_zero_effect_by_default(tmp_path: Path) -> None:
    registry, _state, runtime_root = _fixture(tmp_path, enabled=False)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Do not capture an unconfigured lease.",
        task_class="advancement_task",
        claimed_by="agent-a",
    )

    acquired = acquire_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=str(added["todo_id"]),
        owner="agent-a",
        idempotency_key="default-off-lease",
        write_scopes=["loopx/**"],
        ttl_seconds=120,
    )

    assert "coordination_runtime_shadow_capture" not in acquired
    assert "coordination_runtime_shadow" not in acquired
    assert not (runtime_root / "authority-shadow").exists()
