from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import paths
from loopx.control_plane.projects.registry_codec import (
    ProjectRegistryProtocolError,
    load_project_registry,
)
from loopx.local_state_migration import (
    RECEIPT_NAME,
    migrate_local_state,
    rollback_local_state_migration,
)
from loopx.project_prompt import build_new_project_prompt


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path, *, projects: int = 2) -> tuple[Path, Path, list[Path]]:
    source = tmp_path / "home" / ".codex" / "loopx"
    target = tmp_path / "home" / ".loopx"
    global_goals = []
    project_roots = []
    for index in range(projects):
        project = tmp_path / f"project-{index}"
        goal_id = f"goal-{index}"
        state = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        state.write_text(f"# {goal_id}\n", encoding="utf-8")
        local_registry = project / ".loopx" / "registry.json"
        goal = {
            "id": goal_id,
            "repo": str(project),
            "state_file": f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md",
        }
        _write_json(local_registry, {"common_runtime_root": str(source), "goals": [goal]})
        global_goals.append({**goal, "source_registry": str(local_registry)})
        run = source / "goals" / goal_id / "runs" / "run.json"
        _write_json(run, {"goal_id": goal_id})
        project_roots.append(project)
    _write_json(source / "registry.global.json", {"common_runtime_root": str(source), "goals": global_goals})
    return source, target, project_roots


def _custom_state_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source, target, projects = _fixture(tmp_path, projects=1)
    project = projects[0]
    custom = project / "custom" / "STATE.md"
    custom.parent.mkdir()
    custom.write_text("custom state\n", encoding="utf-8")
    local_registry = project / ".loopx" / "registry.json"
    for path in (local_registry, source / "registry.global.json"):
        payload = _read(path)
        payload["goals"][0]["state_file"] = str(custom.relative_to(project))
        _write_json(path, payload)
    return source, target, project, custom


def test_default_route_keeps_one_existing_legacy_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", source)
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", target)
    assert paths.default_runtime_route()["status"] == "legacy"
    assert paths.resolve_runtime_root({}) == source
    assert paths.resolve_runtime_root({}, str(tmp_path / "custom")) == tmp_path / "custom"
    assert paths.registered_goal_state_file(projects[0], "goal-0", _read(projects[0] / ".loopx" / "registry.json")) == projects[0] / ".codex" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md"
    assert paths.registered_goal_state_file(tmp_path / "fresh", "new") == tmp_path / "fresh" / ".loopx" / "goals" / "new" / "ACTIVE_GOAL_STATE.md"
    _write_json(target / "registry.global.json", {"goals": []})
    assert paths.default_runtime_route()["status"] == "conflict"
    with pytest.raises(ValueError, match="Both default LoopX registries"):
        paths.resolve_runtime_root({})
    (target / "registry.global.json").unlink()
    (target / "registry.global.json").mkdir()
    assert paths.default_runtime_route()["status"] == "invalid"
    with pytest.raises(ValueError, match="not a regular file"):
        paths.resolve_runtime_root({})


def test_existing_project_prompt_keeps_registered_goal_and_runtime_routes(tmp_path: Path) -> None:
    source, _target, projects = _fixture(tmp_path, projects=1)
    project = projects[0]
    payload = build_new_project_prompt(
        project=project,
        goal_doc=project / "GOAL.md",
        goal_id="goal-0",
        objective="Continue the registered Goal",
        domain="example",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        next_probe=None,
        spawn_allowed=False,
        allowed_domains=None,
        write_scope=None,
    )
    assert f"--runtime-root {source}" in payload["quota_guard_command"]
    assert f"--runtime-root {source}" in payload["quota_spend_command"]
    assert f"--runtime-root {source}" in payload["connect_command"]
    assert ".codex/goals/goal-0/ACTIVE_GOAL_STATE.md" in payload["prompt"]


def test_project_prompt_rejects_lifecycle_only_registry(tmp_path: Path) -> None:
    _source, _target, projects = _fixture(tmp_path, projects=1)
    project = projects[0]
    registry_path = project / ".loopx" / "registry.json"
    registry = _read(registry_path)
    registry["profile_id"] = "source_session_v1"
    _write_json(registry_path, registry)

    with pytest.raises(ProjectRegistryProtocolError, match="lifecycle-only profile"):
        build_new_project_prompt(
            project=project,
            goal_doc=project / "GOAL.md",
            goal_id="goal-0",
            objective="Continue the registered Goal",
            domain="example",
            adapter_kind="read_only_project_map_v0",
            adapter_status="connected-read-only",
            next_probe=None,
            spawn_allowed=False,
            allowed_domains=None,
            write_scope=None,
        )


def test_new_default_route_rejects_orphaned_legacy_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    legacy = paths.legacy_goal_state_file(project, "goal-one")
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old authority\n", encoding="utf-8")
    with pytest.raises(ValueError, match="legacy Goal state exists"):
        paths.require_single_goal_state_route(
            project, "goal-one", paths.default_goal_state_file(project, "goal-one")
        )
    result = subprocess.run(
        [
            sys.executable, "-m", "loopx.cli",
            "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
            "bootstrap", "--project", str(project), "--goal-id", "goal-one",
            "--objective", "Continue the old Goal", "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "legacy Goal state exists" in result.stdout
    assert not paths.default_goal_state_file(project, "goal-one").exists()


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_strict_project_registry(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    envelope = [
        {"schema_version": "loopx_project_registry_envelope_v1",
         "minimum_writer_protocol": "goal_instance_v1",
         "payload_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest()},
        payload,
    ]
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_strict_project_registry_keeps_legacy_route_and_wire_format(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)
    project = projects[0]
    local_registry = project / ".loopx" / "registry.json"
    _write_strict_project_registry(local_registry, _read(local_registry))
    before = local_registry.read_bytes()

    prompt = build_new_project_prompt(
        project=project, goal_doc=project / "GOAL.md", goal_id="goal-0",
        objective="Continue the registered Goal", domain="example",
        adapter_kind="read_only_project_map_v0", adapter_status="connected-read-only",
        next_probe=None, spawn_allowed=False, allowed_domains=None, write_scope=None,
    )
    assert f"--runtime-root {source}" in prompt["quota_guard_command"]
    assert ".codex/goals/goal-0/ACTIVE_GOAL_STATE.md" in prompt["prompt"]

    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    migrated = load_project_registry(local_registry)
    assert migrated["common_runtime_root"] == str(target)
    assert migrated["goals"][0]["state_file"] == ".loopx/goals/goal-0/ACTIVE_GOAL_STATE.md"
    assert isinstance(json.loads(local_registry.read_text(encoding="utf-8")), list)

    rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME, execute=True)
    assert local_registry.read_bytes() == before
    assert source.exists() and not target.exists()


def _extension_cli_result(
    home: Path, *arguments: str, runtime_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "loopx.cli", "--format", "json"]
    if runtime_root is not None:
        command.extend(("--runtime-root", str(runtime_root)))
    command.extend(("extension", *arguments))
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"LOOPX_RUNTIME_ROOT", "LOOPX_REGISTRY"}
    }
    env["HOME"] = str(home)
    return subprocess.run(
        command, cwd=home, env=env, text=True, capture_output=True, check=False,
    )


def _extension_cli(
    home: Path, *arguments: str, runtime_root: Path | None = None,
) -> dict[str, object]:
    result = _extension_cli_result(home, *arguments, runtime_root=runtime_root)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    return payload


def _list_extensions(home: Path, *, runtime_root: Path | None = None) -> list[dict[str, object]]:
    return _extension_cli(home, "list", runtime_root=runtime_root)["extensions"]


def test_extension_cli_follows_legacy_execute_and_rollback_routes(tmp_path: Path) -> None:
    source, target, _projects = _fixture(tmp_path, projects=1)
    home = tmp_path / "home"
    extension_state = {
        "schema_version": "loopx_extension_state_v0",
        "extensions": {"example": {
            "id": "example", "enabled": True, "active_revision": "rev-1", "revisions": [],
        }},
    }
    _write_json(source / "extensions" / "state.json", extension_state)
    assert _list_extensions(home) == _list_extensions(home, runtime_root=source)
    assert _list_extensions(home)[0]["id"] == "example"

    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    assert not source.exists()
    assert _read(target / "extensions" / "state.json") == extension_state
    assert _list_extensions(home) == _list_extensions(home, runtime_root=target)
    assert _list_extensions(home)[0]["id"] == "example"
    assert not (source / "extensions" / "state.json").exists()

    rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME, execute=True)
    assert _list_extensions(home) == _list_extensions(home, runtime_root=source)
    assert _list_extensions(home)[0]["id"] == "example"
    assert not target.exists()


def test_extension_cli_uses_fresh_loopx_default(tmp_path: Path) -> None:
    home = tmp_path / "fresh-home"
    _write_json(home / ".loopx" / "extensions" / "state.json", {
        "schema_version": "loopx_extension_state_v0",
        "extensions": {"fresh": {
            "id": "fresh", "enabled": True, "active_revision": "rev-1", "revisions": [],
        }},
    })
    assert _list_extensions(home)[0]["id"] == "fresh"
    disabled = _extension_cli(home, "disable", "fresh", "--execute")
    assert disabled["changed"] is True
    assert _list_extensions(home)[0]["enabled"] is False
    assert _read(home / ".loopx" / "extensions" / "state.json")["extensions"]["fresh"]["enabled"] is False
    assert not (home / ".codex" / "loopx").exists()


def test_extension_cli_conflicting_defaults_require_an_explicit_route(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_json(home / ".codex" / "loopx" / "registry.global.json", {"goals": []})
    _write_json(home / ".loopx" / "registry.global.json", {"goals": []})
    _write_json(home / ".loopx" / "extensions" / "state.json", {
        "schema_version": "loopx_extension_state_v0",
        "extensions": {"selected": {"id": "selected", "enabled": False}},
    })

    result = _extension_cli_result(home, "list")
    assert result.returncode != 0
    assert "Both default LoopX registries exist" in result.stderr
    assert _list_extensions(home, runtime_root=home / ".loopx")[0]["id"] == "selected"


def test_preview_execute_and_verified_rollback_cover_all_registered_projects(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path)
    before = (source / "registry.global.json").read_bytes()
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert preview["dry_run"] is True
    assert preview["project_count"] == 2
    assert preview["goal_directory_count"] == 2
    assert source.exists() and not target.exists()
    assert (source / "registry.global.json").read_bytes() == before

    receipt = migrate_local_state(
        source_runtime_root=source,
        target_runtime_root=target,
        expected_plan_id=preview["plan_id"],
        execute=True,
    )
    receipt_path = Path(receipt["backup_dir"]) / RECEIPT_NAME
    assert receipt_path.exists()
    assert not source.exists()
    assert _read(target / "registry.global.json")["common_runtime_root"] == str(target)
    for index, project in enumerate(projects):
        goal_id = f"goal-{index}"
        assert not (project / ".codex" / "goals" / goal_id).exists()
        assert (project / ".loopx" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md").exists()
        local = _read(project / ".loopx" / "registry.json")
        assert local["common_runtime_root"] == str(target)
        assert local["goals"][0]["state_file"] == f".loopx/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        assert paths.resolve_runtime_root(local) == target

    assert rollback_local_state_migration(receipt_path)["status"] == "rollback_ready"
    assert rollback_local_state_migration(receipt_path, execute=True)["status"] == "rolled_back"
    assert source.exists() and not target.exists()
    assert (source / "registry.global.json").read_bytes() == before
    for index, project in enumerate(projects):
        assert (project / ".codex" / "goals" / f"goal-{index}" / "ACTIVE_GOAL_STATE.md").exists()


def test_stale_plan_and_target_conflict_leave_source_unchanged(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    state = projects[0] / ".codex" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md"
    state.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="preview changed"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target, expected_plan_id=preview["plan_id"], execute=True)
    assert source.exists() and not target.exists()
    target.mkdir()
    with pytest.raises(FileExistsError, match="target runtime root already exists"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert source.exists() and state.read_text() == "changed\n"


def test_failed_write_restores_original_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    original_write = migration._write_registry

    def fail_global_target(path: Path, payload: dict[str, object]) -> None:
        if path == target / "registry.global.json":
            raise OSError("synthetic write failure")
        original_write(path, payload)

    monkeypatch.setattr(migration, "_write_registry", fail_global_target)
    with pytest.raises(RuntimeError, match="original routes were restored"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target, expected_plan_id=preview["plan_id"], execute=True)
    assert source.exists() and not target.exists()
    assert (projects[0] / ".codex" / "goals" / "goal-0" / "ACTIVE_GOAL_STATE.md").exists()
    assert _read(source / "registry.global.json")["common_runtime_root"] == str(source)
    assert _read(projects[0] / ".loopx" / "registry.json")["common_runtime_root"] == str(source)


def test_symlink_and_changed_target_block_unsafe_migration_or_rollback(tmp_path: Path) -> None:
    source, target, _projects = _fixture(tmp_path, projects=1)
    link = source / "linked-state"
    link.symlink_to(source / "goals", target_is_directory=True)
    with pytest.raises(ValueError, match="contains a symlink"):
        migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    link.unlink()
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migrate_local_state(
        source_runtime_root=source,
        target_runtime_root=target,
        expected_plan_id=preview["plan_id"],
        execute=True,
    )
    (target / "goals" / "goal-0" / "runs" / "run.json").write_text("new run\n", encoding="utf-8")
    with pytest.raises(ValueError, match="automatic rollback is unsafe"):
        rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME, execute=True)
    assert target.exists() and not source.exists()


def test_symlinked_goal_destination_ancestor_never_writes_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    goal_parent = projects[0] / ".loopx" / "goals"
    outside = tmp_path / "outside"
    outside.mkdir()
    goal_parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert source.exists() and not target.exists()
    assert list(outside.iterdir()) == []

    goal_parent.unlink()
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    goal_parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert source.exists() and not target.exists()
    assert list(outside.iterdir()) == []

    goal_parent.unlink()
    original_copy = migration._copy
    injected = False

    def inject_after_backup(original: Path, copied: Path) -> None:
        nonlocal injected
        original_copy(original, copied)
        if not injected:
            goal_parent.symlink_to(outside, target_is_directory=True)
            injected = True

    monkeypatch.setattr(migration, "_copy", inject_after_backup)
    with pytest.raises(RuntimeError, match="original routes were restored"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert source.exists() and not target.exists()
    assert list(outside.iterdir()) == []


def test_goal_destination_uses_shared_redirect_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    goal_parent = projects[0] / ".loopx" / "goals"
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    original = migration._is_redirected_path
    monkeypatch.setattr(
        migration, "_is_redirected_path",
        lambda path: path == goal_parent or original(path),
    )

    with pytest.raises(ValueError, match="symlink or junction"):
        migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    with pytest.raises(ValueError, match="symlink or junction"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert source.exists() and not target.exists()


def test_goal_source_uses_shared_redirect_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    goal_root = projects[0] / ".codex" / "goals"
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    original = migration._is_redirected_path
    monkeypatch.setattr(
        migration, "_is_redirected_path",
        lambda path: path == goal_root or original(path),
    )

    with pytest.raises(ValueError, match="legacy Goal source.*symlink or junction"):
        migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    with pytest.raises(ValueError, match="legacy Goal source.*symlink or junction"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert source.exists() and not target.exists()


def test_goal_source_is_rechecked_immediately_before_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    project = projects[0]
    goal_root = project / ".codex" / "goals"
    target_parent = project / ".loopx" / "goals"
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    original_redirect = migration._is_redirected_path
    original_mkdir = Path.mkdir
    redirected = False

    def classify(path: Path) -> bool:
        return (redirected and path == goal_root) or original_redirect(path)

    def inject_after_target_parent(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False,
    ) -> None:
        nonlocal redirected
        original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
        if self == target_parent:
            redirected = True

    monkeypatch.setattr(migration, "_is_redirected_path", classify)
    monkeypatch.setattr(Path, "mkdir", inject_after_target_parent)
    with pytest.raises(RuntimeError, match="original routes were restored"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert redirected
    assert source.exists() and not target.exists()


def test_goal_source_is_rechecked_before_backup_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    goal_root = projects[0] / ".codex" / "goals"
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    backup = Path(preview["backup_dir"])
    original_redirect = migration._is_redirected_path
    original_mkdir = Path.mkdir
    redirected = False

    def classify(path: Path) -> bool:
        return (redirected and path == goal_root) or original_redirect(path)

    def inject_after_backup_dir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False,
    ) -> None:
        nonlocal redirected
        original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
        if self == backup:
            redirected = True

    monkeypatch.setattr(migration, "_is_redirected_path", classify)
    monkeypatch.setattr(Path, "mkdir", inject_after_backup_dir)
    with pytest.raises(ValueError, match="legacy Goal source.*symlink or junction"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert redirected
    assert source.exists() and not target.exists()


@pytest.mark.parametrize("redirected_route", ["source_parent", "target_parent"])
def test_runtime_root_rejects_redirected_ancestors_before_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirected_route: str,
) -> None:
    from loopx import local_state_migration as migration

    source, default_target, _projects = _fixture(tmp_path, projects=1)
    target = default_target if redirected_route == "source_parent" else tmp_path / "target-home" / ".loopx"
    redirected = source.parent if redirected_route == "source_parent" else target.parent
    original = migration._is_redirected_path
    monkeypatch.setattr(
        migration, "_is_redirected_path",
        lambda path: path == redirected or original(path),
    )

    with pytest.raises(ValueError, match="symlink or junction"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            backup_dir=tmp_path / "safe-backup",
        )
    assert source.exists() and not target.exists()


def test_runtime_digest_rejects_redirected_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, _projects = _fixture(tmp_path, projects=1)
    redirected = source / "goals" / "goal-0" / "runs"
    original = migration._is_redirected_path
    monkeypatch.setattr(
        migration, "_is_redirected_path",
        lambda path: path == redirected or original(path),
    )

    with pytest.raises(ValueError, match="migration source contains a symlink or junction"):
        migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert source.exists() and not target.exists()


@pytest.mark.parametrize("redirected_route", ["legacy_goal_parent", "backup_snapshot"])
def test_rollback_preview_rejects_redirected_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirected_route: str,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migration.migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    backup = Path(receipt["backup_dir"])
    redirected = (
        projects[0] / ".codex" / "goals"
        if redirected_route == "legacy_goal_parent" else backup / "snapshot"
    )
    original = migration._is_redirected_path
    monkeypatch.setattr(
        migration, "_is_redirected_path",
        lambda path: path == redirected or original(path),
    )

    with pytest.raises(ValueError, match="symlink or junction"):
        migration.rollback_local_state_migration(backup / RECEIPT_NAME)
    assert target.exists() and not source.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction regression")
def test_windows_junction_goal_source_never_reads_or_moves_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    goal_root = projects[0] / ".codex" / "goals"
    outside = tmp_path / "outside"
    outside.mkdir()
    external_root = outside / "goals"
    goal_root.rename(external_root)
    external_state = external_root / "goal-0" / "ACTIVE_GOAL_STATE.md"
    original_bytes = external_state.read_bytes()
    original_digest = migration._digest
    original_copy = migration._copy

    def reject_external_read(path: Path) -> str:
        resolved = path.resolve()
        if resolved == external_root or external_root in resolved.parents:
            raise AssertionError("migration read external Goal source bytes")
        return original_digest(path)

    def reject_external_copy(original: Path, copied: Path) -> None:
        resolved = original.resolve()
        if resolved == external_root or external_root in resolved.parents:
            raise AssertionError("migration copied external Goal source bytes")
        original_copy(original, copied)

    monkeypatch.setattr(migration, "_digest", reject_external_read)
    monkeypatch.setattr(migration, "_copy", reject_external_copy)

    def make_junction() -> None:
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(goal_root), str(external_root)],
            check=True, capture_output=True, text=True,
        )
        assert goal_root.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT

    make_junction()
    try:
        with pytest.raises(ValueError, match="legacy Goal source.*symlink or junction"):
            migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
        assert source.exists() and not target.exists()
        assert external_state.read_bytes() == original_bytes
    finally:
        goal_root.rmdir()

    external_root.rename(goal_root)
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    goal_root.rename(external_root)
    make_junction()
    try:
        with pytest.raises(ValueError, match="legacy Goal source.*symlink or junction"):
            migration.migrate_local_state(
                source_runtime_root=source, target_runtime_root=target,
                expected_plan_id=preview["plan_id"], execute=True,
            )
        assert source.exists() and not target.exists()
        assert external_state.read_bytes() == original_bytes
    finally:
        goal_root.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction regression")
def test_windows_junction_rollback_destination_never_writes_outside_project(
    tmp_path: Path,
) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    receipt = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    legacy_parent = projects[0] / ".codex" / "goals"
    legacy_parent.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(legacy_parent), str(outside)],
        check=True, capture_output=True, text=True,
    )
    assert legacy_parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    try:
        with pytest.raises(ValueError, match="migration rollback destination.*symlink or junction"):
            rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME)
        assert target.exists() and not source.exists()
        assert list(outside.iterdir()) == []
    finally:
        legacy_parent.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction regression")
def test_windows_junction_goal_destination_never_writes_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, projects = _fixture(tmp_path, projects=1)
    goal_parent = projects[0] / ".loopx" / "goals"
    outside = tmp_path / "outside"
    outside.mkdir()

    def make_junction() -> None:
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(goal_parent), str(outside)],
            check=True, capture_output=True, text=True,
        )
        assert goal_parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT

    make_junction()
    try:
        with pytest.raises(ValueError, match="symlink or junction"):
            migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
        assert source.exists() and not target.exists()
        assert list(outside.iterdir()) == []
    finally:
        goal_parent.rmdir()

    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    make_junction()
    try:
        with pytest.raises(ValueError, match="symlink or junction"):
            migration.migrate_local_state(
                source_runtime_root=source, target_runtime_root=target,
                expected_plan_id=preview["plan_id"], execute=True,
            )
        assert source.exists() and not target.exists()
        assert list(outside.iterdir()) == []
    finally:
        goal_parent.rmdir()

    original_copy = migration._copy
    injected = False

    def inject_after_backup(original: Path, copied: Path) -> None:
        nonlocal injected
        original_copy(original, copied)
        if not injected:
            make_junction()
            injected = True

    monkeypatch.setattr(migration, "_copy", inject_after_backup)
    try:
        with pytest.raises(RuntimeError, match="original routes were restored"):
            migration.migrate_local_state(
                source_runtime_root=source, target_runtime_root=target,
                expected_plan_id=preview["plan_id"], execute=True,
            )
        assert source.exists() and not target.exists()
        assert list(outside.iterdir()) == []
    finally:
        if goal_parent.exists():
            goal_parent.rmdir()


@pytest.mark.parametrize("explicit_backup", [False, True])
def test_symlinked_backup_parent_is_rejected_before_preview_or_copy(
    tmp_path: Path, explicit_backup: bool,
) -> None:
    source, target, _projects = _fixture(tmp_path, projects=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    backup_parent = (
        tmp_path / "explicit-backups"
        if explicit_backup else source.parent / "loopx-local-state-backups"
    )
    backup_parent.symlink_to(outside, target_is_directory=True)
    backup_dir = backup_parent / "receipt" if explicit_backup else None

    with pytest.raises(ValueError, match="backup.*symlink"):
        migrate_local_state(
            source_runtime_root=source,
            target_runtime_root=target,
            backup_dir=backup_dir,
        )
    assert list(outside.iterdir()) == []
    assert source.exists() and not target.exists()


def test_backup_parent_redirected_after_plan_cannot_write_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, _projects = _fixture(tmp_path, projects=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    preview = migration.migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
    )
    original_plan = migration.plan_local_state_migration

    def plan_then_redirect(**kwargs: object) -> dict[str, object]:
        plan = original_plan(**kwargs)
        (source.parent / "loopx-local-state-backups").symlink_to(
            outside, target_is_directory=True,
        )
        return plan

    monkeypatch.setattr(migration, "plan_local_state_migration", plan_then_redirect)
    with pytest.raises(ValueError, match="backup.*symlink"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert list(outside.iterdir()) == []
    assert source.exists() and not target.exists()


def test_backup_snapshot_parent_changed_before_copy_cannot_write_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, _projects = _fixture(tmp_path, projects=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    preview = migration.migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
    )
    backup = Path(preview["backup_dir"])
    original_copy = migration._copy
    calls = 0

    def redirect_before_second_copy(original: Path, copied: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            snapshot = backup / "snapshot"
            snapshot.rename(backup / "snapshot-before-link")
            snapshot.symlink_to(outside, target_is_directory=True)
        original_copy(original, copied)

    monkeypatch.setattr(migration, "_copy", redirect_before_second_copy)
    with pytest.raises(ValueError, match="backup.*symlink"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert list(outside.iterdir()) == []
    assert source.exists() and not target.exists()


def test_explicit_real_backup_path_supports_execute_and_rollback(tmp_path: Path) -> None:
    source, target, _projects = _fixture(tmp_path, projects=1)
    backup = tmp_path / "private-backups" / "receipt"
    preview = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target, backup_dir=backup,
    )
    receipt = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target, backup_dir=backup,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    assert receipt["backup_dir"] == str(backup)
    assert (backup / RECEIPT_NAME).is_file()
    assert target.exists() and not source.exists()
    rollback_local_state_migration(backup / RECEIPT_NAME, execute=True)
    assert source.exists() and not target.exists()


def test_custom_state_keeps_its_declared_file_through_migration_and_rollback(
    tmp_path: Path,
) -> None:
    source, target, project, custom = _custom_state_fixture(tmp_path)
    local_registry = project / ".loopx" / "registry.json"
    before = local_registry.read_bytes()
    preview = migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert preview["goal_directory_count"] == 0
    receipt = migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
        expected_plan_id=preview["plan_id"], execute=True,
    )
    migrated = load_project_registry(local_registry)
    assert migrated["common_runtime_root"] == str(target)
    assert migrated["goals"][0]["state_file"] == "custom/STATE.md"
    assert custom.read_text(encoding="utf-8") == "custom state\n"
    rollback_local_state_migration(Path(receipt["backup_dir"]) / RECEIPT_NAME, execute=True)
    assert local_registry.read_bytes() == before
    assert custom.read_text(encoding="utf-8") == "custom state\n"
    assert source.exists() and not target.exists()


def test_custom_state_cannot_bypass_project_registry_ancestor_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, project, _custom = _custom_state_fixture(tmp_path)
    local_registry = project / ".loopx" / "registry.json"

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_registry = outside / "registry.json"
    outside_registry.write_bytes(local_registry.read_bytes())
    original_outside = outside_registry.read_bytes()
    project_loopx = project / ".loopx"
    parked = project / ".loopx-original"

    project_loopx.rename(parked)
    project_loopx.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="project registry.*symlink"):
        migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert outside_registry.read_bytes() == original_outside
    assert list(outside.iterdir()) == [outside_registry]
    assert source.exists() and not target.exists()

    project_loopx.unlink()
    parked.rename(project_loopx)
    preview = migration.migrate_local_state(source_runtime_root=source, target_runtime_root=target)
    assert preview["goal_directory_count"] == 0
    original_plan = migration.plan_local_state_migration

    def redirect_after_plan(**kwargs: object) -> dict[str, object]:
        plan = original_plan(**kwargs)
        project_loopx.rename(parked)
        project_loopx.symlink_to(outside, target_is_directory=True)
        return plan

    monkeypatch.setattr(migration, "plan_local_state_migration", redirect_after_plan)
    with pytest.raises(ValueError, match="project registry.*symlink"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert outside_registry.read_bytes() == original_outside
    assert list(outside.iterdir()) == [outside_registry]
    assert source.exists() and not target.exists()


def test_project_registry_redirect_after_backup_cannot_write_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import local_state_migration as migration

    source, target, project, _custom = _custom_state_fixture(tmp_path)
    local_registry = project / ".loopx" / "registry.json"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_registry = outside / "registry.json"
    outside_registry.write_bytes(local_registry.read_bytes())
    original_outside = outside_registry.read_bytes()
    preview = migration.migrate_local_state(
        source_runtime_root=source, target_runtime_root=target,
    )
    original_copy = migration._copy

    def redirect_after_registry_copy(original: Path, copied: Path) -> None:
        original_copy(original, copied)
        if original == local_registry:
            (project / ".loopx").rename(project / ".loopx-original")
            (project / ".loopx").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(migration, "_copy", redirect_after_registry_copy)
    with pytest.raises(RuntimeError, match="project registry.*symlink"):
        migration.migrate_local_state(
            source_runtime_root=source, target_runtime_root=target,
            expected_plan_id=preview["plan_id"], execute=True,
        )
    assert outside_registry.read_bytes() == original_outside
    assert list(outside.iterdir()) == [outside_registry]
    assert source.exists() and not target.exists()


def test_cli_preview_execute_and_rollback_readback(tmp_path: Path) -> None:
    source, target, projects = _fixture(tmp_path, projects=1)

    def invoke(*args: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "migrate-local-state", *args],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        return json.loads(result.stdout)

    paths_args = ("--source-runtime-root", str(source), "--target-runtime-root", str(target))
    preview = invoke(*paths_args)
    assert preview["dry_run"] is True
    receipt = invoke(*paths_args, "--execute", "--expected-plan-id", str(preview["plan_id"]))
    assert receipt["status"] == "migrated"
    assert _read(projects[0] / ".loopx" / "registry.json")["common_runtime_root"] == str(target)
    receipt_path = str(Path(str(receipt["backup_dir"])) / RECEIPT_NAME)
    assert invoke("--rollback-receipt", receipt_path)["status"] == "rollback_ready"
    assert invoke("--rollback-receipt", receipt_path, "--execute")["status"] == "rolled_back"
    assert source.exists() and not target.exists()
