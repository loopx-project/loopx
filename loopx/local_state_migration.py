"""Offline, explicit migration of LoopX-owned default state paths.

The preview is read-only. Execution requires its content-bound plan id and
keeps a verified private backup. Running hosts must be stopped by the operator:
older LoopX versions do not participate in a migration-wide writer fence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .control_plane.projects.registry_codec import load_registry, project_registry_transaction
from .paths import (
    DEFAULT_PROJECT_GOALS,
    DEFAULT_RUNTIME_ROOT,
    LEGACY_PROJECT_GOALS,
    LEGACY_RUNTIME_ROOT,
    GLOBAL_REGISTRY_FILENAME,
)
from .runtime import validate_goal_id_path_segment


LOCAL_STATE_MIGRATION_SCHEMA = "loopx_local_state_migration_v1"
RECEIPT_NAME = "migration-receipt.json"


def _absolute(path: Path) -> Path:
    # Collapse lexical `..` without following a symlink before boundary checks.
    return Path(os.path.abspath(path.expanduser()))


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _is_redirected_path(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)()
    # Python 3.11 lacks Path.is_junction; Windows exposes reparse-point
    # attributes through lstat, so reject those as well.
    reparse_point = False
    if os.name == "nt":
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            attributes = 0
        reparse_point = bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    return path.is_symlink() or is_junction or reparse_point


def _require_unlinked_directory_chain(path: Path, *, label: str) -> None:
    """Reject any existing directory ancestor that redirects an I/O route."""

    for ancestor in (path, *path.parents):
        if _is_redirected_path(ancestor):
            raise ValueError(f"{label} has a symlink or junction ancestor: {ancestor}")
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError(f"{label} ancestor is not a directory: {ancestor}")


def _require_backup_path(path: Path, *, must_be_absent: bool = False) -> None:
    """Keep private backup writes on the declared, unlinked directory route."""

    _require_unlinked_directory_chain(path.parent, label="backup path")
    if _is_redirected_path(path):
        raise ValueError(f"backup path has a symlink or junction ancestor: {path}")
    if path.exists():
        if must_be_absent:
            raise FileExistsError(f"backup directory already exists: {path}")
        if not path.is_dir():
            raise ValueError(f"backup path is not a directory: {path}")


def _require_project_registry_path(path: Path) -> None:
    """Keep project registry reads and writes inside their declared project."""

    _require_unlinked_directory_chain(path.parent, label="project registry path")
    if _is_redirected_path(path):
        raise ValueError(f"project registry path has a symlink or junction leaf: {path}")
    if not path.is_file():
        raise FileNotFoundError(
            f"registered project registry is missing: {path}; "
            "restore the project route or retire its global Goal before migration"
        )


def _read_registry(path: Path) -> dict[str, Any]:
    payload = load_registry(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("goals"), list):
        raise ValueError(f"registry must contain a goals list: {path}")
    return payload


def _write_registry(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.migration.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_project_registry(path: Path, payload: dict[str, Any]) -> None:
    with project_registry_transaction(
        path, operation="migrate_local_state_project_registry",
    ) as transaction:
        transaction.commit(payload)


def _digest(path: Path) -> str:
    """Hash source bytes without traversing redirected files or directories."""

    digest = hashlib.sha256()
    if _is_redirected_path(path):
        raise ValueError(f"migration source contains a symlink or junction: {path}")
    if path.is_file():
        digest.update(b"file\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    elif path.is_dir():
        digest.update(b"dir\0")
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            digest.update(child.name.encode("utf-8") + b"\0")
            digest.update(_digest(child).encode("ascii"))
    else:
        raise FileNotFoundError(path)
    return digest.hexdigest()


def _declared_state_file(project: Path, goal: dict[str, Any]) -> Path:
    goal_id = validate_goal_id_path_segment(str(goal.get("id") or ""))
    state_text = goal.get("state_file")
    if not isinstance(state_text, str) or not state_text:
        raise ValueError(f"goal {goal_id} has no state_file")
    declared = Path(state_text).expanduser()
    return _absolute(declared if declared.is_absolute() else project / declared)


def _state_route(project: Path, goal: dict[str, Any]) -> tuple[Path, Path] | None:
    goal_id = validate_goal_id_path_segment(str(goal.get("id") or ""))
    resolved = _declared_state_file(project, goal)
    source = project / LEGACY_PROJECT_GOALS / goal_id / "ACTIVE_GOAL_STATE.md"
    target = project / DEFAULT_PROJECT_GOALS / goal_id / "ACTIVE_GOAL_STATE.md"
    if resolved == source:
        return source, target
    if _within(resolved, project / LEGACY_PROJECT_GOALS):
        raise ValueError(f"noncanonical legacy Goal state path requires manual review: {resolved}")
    return None  # Explicit custom state_file remains where its owner placed it.


def _require_goal_destination(project: Path, target_dir: Path) -> None:
    """Reject a target whose existing ancestors can redirect a Goal rename."""

    if project not in target_dir.parents:
        raise ValueError(f"Goal migration target escapes its project: {target_dir}")
    if _is_redirected_path(target_dir):
        raise ValueError(f"Goal migration target is a symlink or junction: {target_dir}")
    if target_dir.exists():
        raise ValueError(f"Goal migration target exists: {target_dir}")
    _require_unlinked_directory_chain(target_dir.parent, label="Goal migration target")


def _require_goal_source(project: Path, source_dir: Path) -> None:
    """Keep legacy Goal reads and renames on the declared physical route."""

    if source_dir.parent != project / LEGACY_PROJECT_GOALS:
        raise ValueError(f"legacy Goal source escapes its project: {source_dir}")
    _require_unlinked_directory_chain(source_dir, label="legacy Goal source")
    state_file = source_dir / "ACTIVE_GOAL_STATE.md"
    if _is_redirected_path(state_file) or not state_file.is_file():
        raise ValueError(f"legacy state file is missing or linked: {state_file}")


def _require_unlinked_move_routes(source: Path, destination: Path, *, label: str) -> None:
    """Reject redirected leaves or ancestors before a state-directory rename."""

    _require_unlinked_directory_chain(source.parent, label=f"{label} source")
    _require_unlinked_directory_chain(destination.parent, label=f"{label} destination")
    if _is_redirected_path(source) or _is_redirected_path(destination):
        raise ValueError(f"{label} has a symlink or junction leaf")


def _rewrite_registry(
    registry: dict[str, Any], *, project: Path | None, source_root: Path, target_root: Path
) -> dict[str, Any]:
    updated = json.loads(json.dumps(registry))
    declared_root = updated.get("common_runtime_root")
    if declared_root and _absolute(Path(str(declared_root))) != source_root:
        raise ValueError(f"registry declares another runtime root: {declared_root}")
    updated["common_runtime_root"] = str(target_root)
    for goal in updated["goals"]:
        if not isinstance(goal, dict):
            raise ValueError("registry goals must be objects")
        repo_text = goal.get("repo")
        goal_project = project or (_absolute(Path(str(repo_text))) if repo_text else None)
        if goal_project is None:
            raise ValueError(f"global goal {goal.get('id')} has no project route")
        route = _state_route(goal_project, goal)
        if route is None:
            continue
        source, target = route
        declared = Path(str(goal["state_file"])).expanduser()
        goal["state_file"] = (
            str(target) if declared.is_absolute() else str(DEFAULT_PROJECT_GOALS / target.parent.name / target.name)
        )
    return updated


def plan_local_state_migration(
    *,
    source_runtime_root: Path = LEGACY_RUNTIME_ROOT,
    target_runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    source = _absolute(source_runtime_root)
    target = _absolute(target_runtime_root)
    requested_backup = _absolute(backup_dir) if backup_dir is not None else None
    if source == target or _within(target, source) or _within(source, target):
        raise ValueError("source and target runtime roots must be separate")
    _require_unlinked_directory_chain(source, label="legacy runtime root")
    _require_unlinked_directory_chain(target.parent, label="target runtime root")
    if not source.is_dir():
        raise ValueError(f"legacy runtime root must be a real directory: {source}")
    if target.exists() or _is_redirected_path(target):
        raise FileExistsError(f"target runtime root already exists: {target}")
    global_path = source / GLOBAL_REGISTRY_FILENAME
    if _is_redirected_path(global_path):
        raise ValueError(f"legacy global registry is a symlink or junction: {global_path}")
    global_registry = _read_registry(global_path)
    _rewrite_registry(global_registry, project=None, source_root=source, target_root=target)

    project_registries: dict[Path, dict[str, Any]] = {}
    for goal in global_registry["goals"]:
        if not isinstance(goal, dict) or not goal.get("repo"):
            raise ValueError("each global goal needs a project repo for migration")
        project = _absolute(Path(str(goal["repo"])))
        registry_text = goal.get("source_registry")
        registry = _absolute(Path(str(registry_text))) if registry_text else project / ".loopx" / "registry.json"
        if registry != project / ".loopx" / "registry.json":
            raise ValueError(f"noncanonical project registry requires manual review: {registry}")
        if registry not in project_registries:
            _require_project_registry_path(registry)
            project_registries[registry] = _read_registry(registry)

    moves: dict[Path, Path] = {}
    for registry_path, registry in project_registries.items():
        project = registry_path.parent.parent
        _rewrite_registry(registry, project=project, source_root=source, target_root=target)
        for goal in registry["goals"]:
            route = _state_route(project, goal)
            if route is None:
                continue
            state_source, state_target = route
            source_dir = state_source.parent
            target_dir = state_target.parent
            _require_goal_source(project, source_dir)
            _require_goal_destination(project, target_dir)
            moves[source_dir] = target_dir

    # A global-only Goal still needs a matching project-local registration.
    for goal in global_registry["goals"]:
        project = _absolute(Path(str(goal["repo"])))
        registry = project_registries[project / ".loopx" / "registry.json"]
        local = next((item for item in registry["goals"] if isinstance(item, dict) and item.get("id") == goal.get("id")), None)
        if local is None or _declared_state_file(project, local) != _declared_state_file(project, goal):
            raise ValueError(f"global/local Goal route disagrees: {goal.get('id')}")

    items = [("runtime", source, target)] + [
        ("registry", path, path) for path in sorted(project_registries)
    ] + [("goal", old, new) for old, new in sorted(moves.items())]
    entries = [
        {"kind": kind, "source": str(old), "target": str(new), "digest": _digest(old)}
        for kind, old, new in items
    ]
    binding = {"source": str(source), "target": str(target), "entries": entries}
    plan_id = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    backup = requested_backup or source.parent / "loopx-local-state-backups" / plan_id[:16]
    if any(_within(backup, root) for root in (source, target, *moves, *moves.values())):
        raise ValueError("backup must be outside runtime and Goal state directories")
    _require_backup_path(backup, must_be_absent=True)
    return {
        "ok": True,
        "schema_version": LOCAL_STATE_MIGRATION_SCHEMA,
        "dry_run": True,
        "plan_id": plan_id,
        "source_runtime_root": str(source),
        "target_runtime_root": str(target),
        "backup_dir": str(backup),
        "project_count": len(project_registries),
        "goal_directory_count": len(moves),
        "entries": entries,
        "recommended_action": "Stop LoopX workers, inspect this preview, then run with --execute --expected-plan-id <plan_id>.",
    }


def _copy(source: Path, target: Path) -> None:
    _require_unlinked_directory_chain(source.parent, label="migration source")
    if _is_redirected_path(source):
        raise ValueError(f"migration source is a symlink or junction: {source}")
    _require_backup_path(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _require_backup_path(target.parent)
    if target.exists() or _is_redirected_path(target):
        raise FileExistsError(f"backup snapshot target already exists: {target}")
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def migrate_local_state(
    *,
    source_runtime_root: Path = LEGACY_RUNTIME_ROOT,
    target_runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    backup_dir: Path | None = None,
    expected_plan_id: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    plan = plan_local_state_migration(
        source_runtime_root=source_runtime_root,
        target_runtime_root=target_runtime_root,
        backup_dir=backup_dir,
    )
    if not execute:
        return plan
    if not expected_plan_id or expected_plan_id != plan["plan_id"]:
        raise ValueError("migration preview changed; rerun preview and supply its exact --expected-plan-id")

    source = Path(plan["source_runtime_root"])
    target = Path(plan["target_runtime_root"])
    backup = Path(plan["backup_dir"])
    entries = plan["entries"]
    _require_backup_path(backup, must_be_absent=True)
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    _require_backup_path(backup)
    try:
        for index, entry in enumerate(entries):
            _require_backup_path(backup)
            original = Path(entry["source"])
            if entry["kind"] == "registry":
                _require_project_registry_path(original)
            elif entry["kind"] == "goal":
                _require_goal_source(original.parent.parent.parent, original)
            if _digest(original) != entry["digest"]:
                raise ValueError(f"source changed before backup: {original}")
            copied = backup / "snapshot" / str(index)
            _copy(original, copied)
            if _digest(copied) != entry["digest"]:
                raise ValueError(f"backup verification failed for {original}")
        _require_backup_path(backup)
        (backup / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        # Keep any partial backup for inspection. No authoritative state moved.
        raise

    moved: list[tuple[Path, Path]] = []
    modified: list[Path] = []
    expected_project_registries: dict[Path, dict[str, Any]] = {}
    global_modified = False
    expected_global_registry: dict[str, Any] | None = None
    try:
        for entry in entries:
            original = Path(entry["source"])
            if entry["kind"] == "registry":
                _require_project_registry_path(original)
            elif entry["kind"] == "goal":
                _require_goal_source(original.parent.parent.parent, original)
            if _digest(original) != entry["digest"]:
                raise ValueError(f"source changed during backup: {entry['source']}")
        for entry in entries:
            if entry["kind"] != "goal":
                continue
            old, new = Path(entry["source"]), Path(entry["target"])
            project = new.parent.parent.parent
            _require_goal_source(project, old)
            _require_goal_destination(project, new)
            new.parent.mkdir(parents=True, exist_ok=True)
            _require_goal_destination(project, new)
            _require_goal_source(project, old)
            old.rename(new)
            moved.append((old, new))
        for entry in entries:
            if entry["kind"] != "registry":
                continue
            registry_path = Path(entry["source"])
            _require_project_registry_path(registry_path)
            registry = _read_registry(registry_path)
            updated = _rewrite_registry(
                registry,
                project=registry_path.parent.parent,
                source_root=source,
                target_root=target,
            )
            _require_project_registry_path(registry_path)
            _write_project_registry(registry_path, updated)
            modified.append(registry_path)
            expected_project_registries[registry_path] = updated
        _require_unlinked_move_routes(source, target, label="runtime migration")
        if target.exists():
            raise FileExistsError(f"target runtime root reappeared: {target}")
        source.rename(target)
        moved.append((source, target))
        global_path = target / GLOBAL_REGISTRY_FILENAME
        _require_unlinked_directory_chain(target, label="migrated runtime root")
        if _is_redirected_path(global_path):
            raise ValueError(f"migrated global registry is a symlink or junction: {global_path}")
        updated_global = _rewrite_registry(
            _read_registry(global_path), project=None, source_root=source, target_root=target
        )
        _write_registry(global_path, updated_global)
        global_modified = True
        expected_global_registry = updated_global
        if _read_registry(global_path) != updated_global:
            raise ValueError("global registry changed during migration")
        for registry_path, expected in expected_project_registries.items():
            _require_project_registry_path(registry_path)
            if _read_registry(registry_path) != expected:
                raise ValueError(f"project registry changed during migration: {registry_path}")
        for entry in entries:
            if entry["kind"] == "goal":
                goal_target = Path(entry["target"])
                _require_unlinked_directory_chain(goal_target.parent, label="migrated Goal state")
                if _digest(goal_target) != entry["digest"]:
                    raise ValueError(f"Goal state changed during migration: {entry['target']}")
            if entry["kind"] != "registry":
                legacy = Path(entry["source"])
                _require_unlinked_directory_chain(legacy.parent, label="legacy authority")
                if legacy.exists() or _is_redirected_path(legacy):
                    raise ValueError(f"legacy authority reappeared during migration: {legacy}")
        before_runtime = backup / "snapshot" / "0"
        before_children = {child.name for child in before_runtime.iterdir()}
        after_children = {child.name for child in target.iterdir()}
        if before_children != after_children:
            raise ValueError("runtime files changed during migration")
        for name in before_children - {GLOBAL_REGISTRY_FILENAME}:
            if _digest(before_runtime / name) != _digest(target / name):
                raise ValueError(f"runtime state changed during migration: {name}")
        after = [
            {**entry, "after_digest": _digest(Path(entry["target"]))}
            for entry in entries
        ]
        receipt = {
            **plan,
            "dry_run": False,
            "status": "migrated",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "entries": after,
        }
        _require_backup_path(backup)
        _write_registry(backup / RECEIPT_NAME, receipt)
        return receipt
    except Exception as exc:
        rollback_errors: list[str] = []
        for old, new in reversed(moved):
            try:
                _require_unlinked_move_routes(new, old, label="automatic rollback")
                if new.exists() and not old.exists():
                    new.rename(old)
                elif new.exists() and old.exists():
                    rollback_errors.append(f"both routes exist: {old} and {new}")
            except (OSError, ValueError) as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for index, entry in enumerate(entries):
            if entry["kind"] == "registry" and Path(entry["source"]) in modified:
                try:
                    registry_path = Path(entry["source"])
                    _require_project_registry_path(registry_path)
                    if _read_registry(registry_path) != expected_project_registries[registry_path]:
                        rollback_errors.append(f"project registry changed; kept for manual recovery: {registry_path}")
                        continue
                    _require_project_registry_path(registry_path)
                    _require_unlinked_directory_chain(backup / "snapshot", label="backup snapshot")
                    shutil.copy2(backup / "snapshot" / str(index), registry_path)
                except (OSError, ValueError) as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
        if global_modified:
            try:
                source_registry = source / GLOBAL_REGISTRY_FILENAME
                _require_unlinked_directory_chain(source, label="restored runtime root")
                if _is_redirected_path(source_registry):
                    raise ValueError(f"restored global registry is a symlink or junction: {source_registry}")
                if _read_registry(source_registry) != expected_global_registry:
                    rollback_errors.append(f"global registry changed; kept for manual recovery: {source_registry}")
                else:
                    _require_unlinked_directory_chain(backup / "snapshot", label="backup snapshot")
                    shutil.copy2(backup / "snapshot" / "0" / GLOBAL_REGISTRY_FILENAME, source_registry)
            except (OSError, ValueError) as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise RuntimeError(
                f"migration failed: {exc}; automatic rollback incomplete; backup={backup}; "
                + "; ".join(rollback_errors)
            ) from exc
        raise RuntimeError(f"migration failed and original routes were restored; backup={backup}: {exc}") from exc


def rollback_local_state_migration(receipt_path: Path, *, execute: bool = False) -> dict[str, Any]:
    receipt_path = _absolute(receipt_path)
    _require_backup_path(receipt_path.parent)
    if _is_redirected_path(receipt_path):
        raise ValueError(f"backup receipt is a symlink or junction: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("schema_version") != LOCAL_STATE_MIGRATION_SCHEMA or receipt.get("status") != "migrated":
        raise ValueError("receipt does not describe a completed local state migration")
    entries = receipt.get("entries")
    if not isinstance(entries, list):
        raise ValueError("migration receipt has no entries")
    backup = receipt_path.parent
    _require_unlinked_directory_chain(backup / "snapshot", label="backup snapshot")
    for index, entry in enumerate(entries):
        old, new = Path(entry["source"]), Path(entry["target"])
        if entry["kind"] == "registry":
            _require_project_registry_path(new)
        else:
            _require_unlinked_move_routes(new, old, label="migration rollback")
            if old.exists():
                raise FileExistsError(f"legacy path has reappeared: {old}")
        if _digest(new) != entry["after_digest"]:
            raise ValueError(f"migrated state changed; automatic rollback is unsafe: {new}")
        if _digest(backup / "snapshot" / str(index)) != entry["digest"]:
            raise ValueError(f"migration backup changed: {backup / 'snapshot' / str(index)}")
    result = {"ok": True, "schema_version": LOCAL_STATE_MIGRATION_SCHEMA, "dry_run": not execute, "status": "rollback_ready", "receipt": str(receipt_path)}
    if not execute:
        return result
    for entry in reversed(entries):
        if entry["kind"] == "runtime":
            old, new = Path(entry["source"]), Path(entry["target"])
            _require_unlinked_move_routes(new, old, label="migration rollback")
            if old.exists():
                raise FileExistsError(f"legacy path has reappeared: {old}")
            new.rename(old)
    for entry in reversed(entries):
        if entry["kind"] == "goal":
            old, new = Path(entry["source"]), Path(entry["target"])
            _require_unlinked_move_routes(new, old, label="migration rollback")
            if old.exists():
                raise FileExistsError(f"legacy path has reappeared: {old}")
            new.rename(old)
    for index, entry in enumerate(entries):
        if entry["kind"] == "registry":
            _require_project_registry_path(Path(entry["source"]))
            _require_unlinked_directory_chain(backup / "snapshot", label="backup snapshot")
            shutil.copy2(backup / "snapshot" / str(index), entry["source"])
    source_registry = Path(receipt["source_runtime_root"]) / GLOBAL_REGISTRY_FILENAME
    _require_unlinked_directory_chain(source_registry.parent, label="restored runtime root")
    if _is_redirected_path(source_registry):
        raise ValueError(f"restored global registry is a symlink or junction: {source_registry}")
    _require_unlinked_directory_chain(backup / "snapshot", label="backup snapshot")
    shutil.copy2(backup / "snapshot" / "0" / GLOBAL_REGISTRY_FILENAME, source_registry)
    result["dry_run"] = False
    result["status"] = "rolled_back"
    _require_backup_path(backup)
    _write_registry(receipt_path, {**receipt, "status": "rolled_back"})
    return result


def render_local_state_migration_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Local State Migration",
        f"- status: `{payload.get('status') or ('preview' if payload.get('ok') else 'failed')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- plan_id: `{payload.get('plan_id')}`",
        f"- source: `{payload.get('source_runtime_root')}`",
        f"- target: `{payload.get('target_runtime_root')}`",
        f"- backup: `{payload.get('backup_dir')}`",
        f"- projects: `{payload.get('project_count')}`; Goal directories: `{payload.get('goal_directory_count')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload['error']}")
    if payload.get("recommended_action"):
        lines.append(f"- next: {payload['recommended_action']}")
    return "\n".join(lines) + "\n"
