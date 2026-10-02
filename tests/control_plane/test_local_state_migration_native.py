"""Native Goals keep working after an offline runtime/state path migration."""

import json
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.runtime.local_state_migration import (
    RECEIPT_NAME,
    migrate_local_state,
    rollback_local_state_migration,
)
from loopx.todos import add_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_migrated_native_goal_can_read_and_advance_without_losing_rollback_safety(
    tmp_path: Path, monkeypatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    original, source, _state = promoted_create_fixture(tmp_path, provider=provider)
    payload = json.loads(original.read_text(encoding="utf-8"))
    registry = Path(payload["goals"][0]["repo"]) / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(original.read_bytes())

    def add(operation: str) -> dict:
        return add_goal_todo(
            registry_path=registry, goal_id="goal-a", role="agent",
            text=f"Validate synthetic artifact {operation}", claimed_by="agent-a", agent_id="agent-a",
            operation_id=operation, validation_label="synthetic artifact",
            validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(0)"]),
        )

    def read(root: Path) -> dict:
        result = read_canonical_todos_if_promoted(runtime_root=root, goal_id="goal-a")
        assert result is not None
        return result

    try:
        assert add("before-migration")["ok"] is True
        payload = json.loads(registry.read_text(encoding="utf-8"))
        (source / "registry.global.json").write_text(json.dumps({
            **payload, "goals": [{**payload["goals"][0], "source_registry": str(registry)}],
        }), encoding="utf-8")
        before = read(source)
        assert len(before["todos"]) == 1
        assert restart_effect_runtime()["status"] == "stopped"
        target = tmp_path / "migrated-runtime"
        preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
        receipt = migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            execute=True, expected_plan_id=preview["plan_id"],
        )
        after = read(target)
        assert after["provider_revision"] == before["provider_revision"]
        assert [row["todo_id"] for row in after["todos"]] == [row["todo_id"] for row in before["todos"]]
        receipt_path = Path(receipt["backup_dir"]) / RECEIPT_NAME
        assert rollback_local_state_migration(receipt_path)["status"] == "rollback_ready"
        assert add("after-migration")["ok"] is True
        current = read(target)
        assert len(current["todos"]) == 2
        with pytest.raises(ValueError, match="migrated state changed"):
            rollback_local_state_migration(receipt_path)
        assert read(target)["provider_revision"] == current["provider_revision"]
    finally:
        restart_effect_runtime()
