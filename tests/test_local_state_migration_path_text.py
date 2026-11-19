"""Migration changes owned routes while preserving declared path text."""

import json
from pathlib import Path

import pytest

from tests import test_local_state_migration as migration


def _set_declaration(source, project, text, strict):
    local = project / ".loopx" / "registry.json"
    for registry in (local, source / "registry.global.json"):
        payload = migration._read(registry)
        payload["goals"][0]["state_file"] = text
        if registry == local and strict:
            migration._write_strict_project_registry(registry, payload)
        else:
            migration._write_json(registry, payload)
    return local


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("declaration", ["portable-relative", "native-relative", "absolute"])
def test_owned_migration_path_text_and_exact_rollback(tmp_path, strict, declaration):
    source, target, projects = migration._fixture(tmp_path, projects=1)
    project = projects[0]
    relative = Path(".codex") / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md"
    text = {
        "portable-relative": ".codex/goals/goal-0/ACTIVE_GOAL_STATE.md",
        "native-relative": str(relative),
        "absolute": str(project / relative),
    }[declaration]
    local = _set_declaration(source, project, text, strict)
    original_local, original_global = local.read_bytes(), (source / "registry.global.json").read_bytes()
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert local.read_bytes() == original_local and (source / "registry.global.json").read_bytes() == original_global
    receipt = migration.migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    expected = str(project / ".loopx" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md") if declaration == "absolute" else ".loopx/goals/goal-0/ACTIVE_GOAL_STATE.md"
    for registry in (local, target / "registry.global.json"):
        payload = migration.load_project_registry(registry)
        assert payload["goals"][0]["state_file"] == expected
        assert payload["common_runtime_root"] == str(target)
    assert isinstance(json.loads(local.read_text()), list) == strict
    assert (project / ".loopx" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md").read_text() == "# goal-0\n"
    migration.rollback_local_state_migration(Path(receipt["backup_dir"]) / migration.RECEIPT_NAME, execute=True)
    assert local.read_bytes() == original_local
    assert (source / "registry.global.json").read_bytes() == original_global
    assert (project / relative).read_text() == "# goal-0\n" and not target.exists()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("native_text", [False, True])
def test_custom_path_keeps_exact_declared_text(tmp_path, strict, native_text):
    source, target, project, custom = migration._custom_state_fixture(tmp_path)
    text = str(Path("custom") / "STATE.md") if native_text else "custom/STATE.md"
    local = _set_declaration(source, project, text, strict)
    original_local, original_global = local.read_bytes(), (source / "registry.global.json").read_bytes()
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert preview["goal_directory_count"] == 0
    receipt = migration.migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    for registry in (local, target / "registry.global.json"):
        assert migration.load_project_registry(registry)["goals"][0]["state_file"] == text
    assert custom.read_text() == "custom state\n"
    migration.rollback_local_state_migration(Path(receipt["backup_dir"]) / migration.RECEIPT_NAME, execute=True)
    assert local.read_bytes() == original_local and (source / "registry.global.json").read_bytes() == original_global
    assert custom.read_text() == "custom state\n" and not target.exists()
