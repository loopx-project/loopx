from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.state_migration import migrate_legacy_state


def test_migration_rejects_target_goal_id_collisions_before_writing(
    tmp_path: Path,
) -> None:
    legacy_runtime = tmp_path / "legacy-runtime"
    target_runtime = tmp_path / "target-runtime"
    source_repos = {
        goal_id: tmp_path / f"{goal_id}-source" for goal_id in ("goal-a", "goal-b")
    }
    target_repos = {
        goal_id: tmp_path / f"{goal_id}-target" for goal_id in ("goal-a", "goal-b")
    }
    goals = []
    for goal_id, source_repo in source_repos.items():
        source_repo.mkdir()
        (source_repo / "ACTIVE_GOAL_STATE.md").write_text(
            f"---\ngoal_id: {goal_id}\n---\n",
            encoding="utf-8",
        )
        source_runtime = legacy_runtime / "goals" / goal_id
        source_runtime.mkdir(parents=True)
        (source_runtime / f"{goal_id}.json").write_text(
            json.dumps({"goal_id": goal_id}),
            encoding="utf-8",
        )
        goals.append(
            {
                "id": goal_id,
                "status": "active",
                "repo": str(source_repo),
                "state_file": "ACTIVE_GOAL_STATE.md",
            }
        )

    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": goals}),
        encoding="utf-8",
    )
    target_registry = tmp_path / "target-registry.json"
    original_target_registry = b'{"schema_version":"0.1","goals":[]}\n'
    target_registry.write_bytes(original_target_registry)

    with pytest.raises(
        ValueError,
        match=r"goal-a and goal-b map to the same target goal id: goal-b",
    ):
        migrate_legacy_state(
            legacy_registry_path=legacy_registry,
            target_registry_path=target_registry,
            legacy_runtime_root=legacy_runtime,
            target_runtime_root=target_runtime,
            goal_ids=["goal-a", "goal-b"],
            goal_id_map={"goal-a": "goal-b"},
            path_map={
                str(source_repos[goal_id]): str(target_repos[goal_id])
                for goal_id in source_repos
            },
            copy_active_state=True,
            copy_runtime=True,
            execute=True,
        )

    assert target_registry.read_bytes() == original_target_registry
    assert not any(
        (target_repo / "ACTIVE_GOAL_STATE.md").exists()
        for target_repo in target_repos.values()
    )
    assert not (target_runtime / "goals" / "goal-b").exists()


def _active_state_migration_fixture(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    source_state = repo / ".codex" / "goals" / "goal-a" / "ACTIVE_GOAL_STATE.md"
    source_state.parent.mkdir(parents=True)
    source_state.write_text("## Agent Todo\n\n- [ ] legacy todo\n", encoding="utf-8")
    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "repo": repo,
        "legacy_registry": legacy_registry,
        "target_registry": repo / ".loopx" / "registry.json",
        "target_state": repo / ".loopx" / "goals" / "goal-a" / "ACTIVE_GOAL_STATE.md",
        "target_runtime": tmp_path / "target-runtime",
    }


def _migrate_active_state(paths: dict[str, Path], tmp_path: Path, *, execute: bool) -> dict:
    return migrate_legacy_state(
        legacy_registry_path=paths["legacy_registry"],
        target_registry_path=paths["target_registry"],
        legacy_runtime_root=tmp_path / "legacy-runtime",
        target_runtime_root=paths["target_runtime"],
        goal_ids=["goal-a"],
        goal_id_map={},
        path_map={".codex/goals": ".loopx/goals"},
        copy_active_state=True,
        copy_runtime=False,
        execute=execute,
    )


def test_repeated_migration_does_not_overwrite_diverged_target_active_state(
    tmp_path: Path,
) -> None:
    paths = _active_state_migration_fixture(tmp_path)
    first = _migrate_active_state(paths, tmp_path, execute=True)
    assert first["active_state"][0]["copied"] is True
    # An identical rerun is idempotent.
    assert _migrate_active_state(paths, tmp_path, execute=True)["ok"] is True

    progressed = "## Agent Todo\n\n- [x] legacy todo\n- [ ] work added after migration\n"
    paths["target_state"].write_text(progressed, encoding="utf-8")
    registry_before = paths["target_registry"].read_bytes()

    for execute in (False, True):
        with pytest.raises(ValueError, match="already exists with different content"):
            _migrate_active_state(paths, tmp_path, execute=execute)

    assert paths["target_state"].read_text(encoding="utf-8") == progressed
    assert paths["target_registry"].read_bytes() == registry_before
