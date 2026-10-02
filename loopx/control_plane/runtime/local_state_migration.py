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
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..projects.registry_codec import load_registry, project_registry_transaction
from ...paths import (
    DEFAULT_PROJECT_GOALS,
    DEFAULT_RUNTIME_ROOT,
    LEGACY_PROJECT_GOALS,
    LEGACY_RUNTIME_ROOT,
    GLOBAL_REGISTRY_FILENAME,
)
from ...runtime import validate_goal_id_path_segment


LOCAL_STATE_MIGRATION_SCHEMA = "loopx_local_state_migration_v1"
RECEIPT_NAME = "migration-receipt.json"


class _MigrationStatus(str, Enum):
    MIGRATING = "migrating"
    MIGRATED = "migrated"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


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


def _registry_bytes(payload: dict[str, Any]) -> bytes:
    """One UTF-8/LF producer for global registry and receipt fingerprints."""

    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_registry(path: Path, payload: dict[str, Any]) -> None:
    # Stage outside the runtime tree: a killed writer must not leave a
    # temporary file that invalidates that tree's recovery fingerprint.
    _require_unlinked_directory_chain(path.parent, label="migration write")
    if _is_redirected_path(path):
        raise ValueError(f"migration write is a symlink or junction: {path}")
    descriptor, name = tempfile.mkstemp(prefix=".loopx-migration-", dir=path.parent.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_registry_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
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


@dataclass
class _MigrationWrites:
    """Expected registry payloads for final migration verification."""

    project_registries: dict[Path, dict[str, Any]] = field(default_factory=dict)
    global_registry: dict[str, Any] | None = None


def _write_verified_backup(plan: dict[str, Any]) -> None:
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


def _verify_migrated_state(plan: dict[str, Any], writes: _MigrationWrites) -> None:
    target = Path(plan["target_runtime_root"])
    backup = Path(plan["backup_dir"])
    entries = plan["entries"]
    global_path = target / GLOBAL_REGISTRY_FILENAME
    if _read_registry(global_path) != writes.global_registry:
        raise ValueError("global registry changed during migration")
    for registry_path, expected in writes.project_registries.items():
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


def _prepare_recovery_receipt(plan: dict[str, Any]) -> dict[str, Any]:
    """Bind the expected registry bytes before the first authoritative move."""

    backup = Path(plan["backup_dir"])
    source = Path(plan["source_runtime_root"])
    target = Path(plan["target_runtime_root"])
    entries = []
    for index, entry in enumerate(plan["entries"]):
        prepared = dict(entry)
        if entry["kind"] == "runtime":
            updated = _rewrite_registry(
                _read_registry(backup / "snapshot" / str(index) / GLOBAL_REGISTRY_FILENAME),
                project=None, source_root=source, target_root=target,
            )
            prepared["updated_registry_digest"] = hashlib.sha256(b"file\0" + _registry_bytes(updated)).hexdigest()
        elif entry["kind"] == "registry":
            original = backup / "snapshot" / str(index)
            expected = backup / "expected-registries" / str(index)
            _copy(original, expected)
            updated = _rewrite_registry(
                _read_registry(original), project=Path(entry["source"]).parent.parent,
                source_root=source, target_root=target,
            )
            _write_project_registry(expected, updated)
            prepared["updated_registry_digest"] = _digest(expected)
        entries.append(prepared)
    receipt = {**plan, "dry_run": False, "status": "migrating", "entries": entries}
    _write_registry(backup / RECEIPT_NAME, receipt)
    return receipt


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
    _write_verified_backup(plan)
    # A verified snapshot and operation intent must survive before any route
    # changes. Recovery uses physical locations and fingerprints, not a lost
    # in-memory list of completed effects.
    recovery_receipt = _prepare_recovery_receipt(plan)

    writes = _MigrationWrites()
    effects_started = False
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
            effects_started = True
            old.rename(new)
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
            effects_started = True
            _write_project_registry(registry_path, updated)
            writes.project_registries[registry_path] = updated
        _require_unlinked_move_routes(source, target, label="runtime migration")
        if target.exists():
            raise FileExistsError(f"target runtime root reappeared: {target}")
        effects_started = True
        source.rename(target)
        global_path = target / GLOBAL_REGISTRY_FILENAME
        _require_unlinked_directory_chain(target, label="migrated runtime root")
        if _is_redirected_path(global_path):
            raise ValueError(f"migrated global registry is a symlink or junction: {global_path}")
        updated_global = _rewrite_registry(
            _read_registry(global_path), project=None, source_root=source, target_root=target
        )
        _write_registry(global_path, updated_global)
        writes.global_registry = updated_global
        _verify_migrated_state(plan, writes)
        after = [
            {**entry, "after_digest": _digest(Path(entry["target"]))}
            for entry in recovery_receipt["entries"]
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
        rollback_errors = []
        if effects_started:
            try:
                rollback_local_state_migration(backup / RECEIPT_NAME, execute=True)
            except (OSError, ValueError) as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        else:
            _write_registry(backup / RECEIPT_NAME, {**recovery_receipt, "status": "rolled_back"})
        if rollback_errors:
            raise RuntimeError(
                f"migration failed: {exc}; automatic rollback incomplete; backup={backup}; "
                + "; ".join(rollback_errors)
            ) from exc
        raise RuntimeError(f"migration failed and original routes were restored; backup={backup}: {exc}") from exc


def _read_recovery_receipt(receipt_path: Path) -> dict[str, Any]:
    _require_backup_path(receipt_path.parent)
    if _is_redirected_path(receipt_path):
        raise ValueError(f"backup receipt is a symlink or junction: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != LOCAL_STATE_MIGRATION_SCHEMA
        or receipt.get("status") not in {status.value for status in _MigrationStatus}
    ):
        raise ValueError("receipt does not describe a recoverable local state migration")
    backup = receipt_path.parent
    _require_unlinked_directory_chain(backup / "snapshot", label="backup snapshot")
    plan_path = backup / "plan.json"
    if _is_redirected_path(plan_path):
        raise ValueError("migration backup plan is redirected")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    entries = receipt.get("entries")
    if not isinstance(entries, list) or not isinstance(plan, dict):
        raise ValueError("migration receipt has no entries")
    for key in ("plan_id", "source_runtime_root", "target_runtime_root", "backup_dir"):
        if receipt.get(key) != plan.get(key):
            raise ValueError("migration receipt disagrees with its backup plan")
    original_entries = [
        {key: entry[key] for key in ("kind", "source", "target", "digest")}
        for entry in entries
    ]
    binding = {
        "source": receipt["source_runtime_root"],
        "target": receipt["target_runtime_root"], "entries": original_entries,
    }
    if (
        str(backup) != receipt["backup_dir"] or original_entries != plan.get("entries")
        or hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest() != receipt["plan_id"]
    ):
        raise ValueError("migration receipt paths or plan fingerprint changed")
    return receipt


def _require_unchanged_runtime(receipt: dict[str, Any], active: Path) -> None:
    before = Path(receipt["backup_dir"]) / "snapshot" / "0"
    if {child.name for child in before.iterdir()} != {child.name for child in active.iterdir()}:
        raise ValueError(f"migrated state changed; automatic rollback is unsafe: {active}")
    for child in before.iterdir():
        current = active / child.name
        if child.name != GLOBAL_REGISTRY_FILENAME:
            if _digest(current) != _digest(child):
                raise ValueError(f"migrated state changed; automatic rollback is unsafe: {current}")
            continue
        updated = _rewrite_registry(
            _read_registry(child), project=None,
            source_root=Path(receipt["source_runtime_root"]),
            target_root=Path(receipt["target_runtime_root"]),
        )
        runtime_entry = receipt["entries"][0]
        if "updated_registry_digest" in runtime_entry:
            updated_digests = {runtime_entry["updated_registry_digest"]}
        else:
            # Earlier v1 receipts used the host's text-mode newline translation.
            # Their complete target digest is still enforced before rollback;
            # resumed legacy operations accept only these two producer forms.
            updated_bytes = _registry_bytes(updated)
            updated_digests = {
                hashlib.sha256(b"file\0" + variant).hexdigest()
                for variant in (updated_bytes, updated_bytes.replace(b"\n", b"\r\n"))
            }
        if _digest(current) not in {_digest(child), *updated_digests}:
            raise ValueError(f"global registry changed; automatic rollback is unsafe: {current}")


def _recovery_entry_route(receipt: dict[str, Any], index: int) -> Path:
    entry = receipt["entries"][index]
    old, new = Path(entry["source"]), Path(entry["target"])
    snapshot = Path(receipt["backup_dir"]) / "snapshot" / str(index)
    if _digest(snapshot) != entry["digest"]:
        raise ValueError(f"migration backup changed: {snapshot}")
    if entry["kind"] == "registry":
        _require_project_registry_path(old)
        current_digest = _digest(old)
        expected = entry.get("updated_registry_digest", entry.get("after_digest"))
        if current_digest not in {entry["digest"], expected}:
            raise ValueError(f"project registry changed; automatic rollback is unsafe: {old}")
        active = old
    else:
        _require_unlinked_move_routes(new, old, label="migration rollback")
        if old.exists() and (new.exists() or receipt["status"] == "migrated"):
            raise FileExistsError(f"legacy path has reappeared: {old}")
        active = old if old.exists() else new
        if entry["kind"] == "runtime":
            _require_unchanged_runtime(receipt, active)
        elif entry["kind"] == "goal":
            if _digest(active) != entry["digest"]:
                raise ValueError(f"migrated state changed; automatic rollback is unsafe: {active}")
        else:
            raise ValueError("migration receipt contains an unsupported entry kind")
    if receipt["status"] == "migrated" and _digest(active) != entry["after_digest"]:
        raise ValueError(f"migrated state changed; automatic rollback is unsafe: {active}")
    if receipt["status"] == "rolled_back" and (active != old or _digest(old) != entry["digest"]):
        raise ValueError(f"restored state changed; automatic rollback is unsafe: {old}")
    return active


def _restore_snapshot_file(snapshot: Path, destination: Path, *, expected_digest: str) -> None:
    """An interrupted copy must leave either complete original or updated bytes."""

    _require_unlinked_directory_chain(snapshot.parent, label="backup snapshot")
    _require_unlinked_directory_chain(destination.parent, label="registry recovery")
    if _is_redirected_path(snapshot) or _is_redirected_path(destination):
        raise ValueError("registry recovery has a symlink or junction leaf")
    if _digest(snapshot) != expected_digest:
        raise ValueError(f"migration backup changed: {snapshot}")
    before = _digest(destination)
    descriptor, name = tempfile.mkstemp(prefix=".loopx-rollback-", dir=destination.parent.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copy2(snapshot, temporary)
        if _digest(temporary) != expected_digest or _digest(snapshot) != expected_digest:
            raise ValueError(f"registry recovery copy changed: {snapshot}")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        _require_unlinked_directory_chain(destination.parent, label="registry recovery")
        if _is_redirected_path(destination):
            raise ValueError("registry recovery destination is redirected")
        if _digest(destination) != before:
            raise ValueError(f"registry changed during recovery: {destination}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def rollback_local_state_migration(receipt_path: Path, *, execute: bool = False) -> dict[str, Any]:
    receipt_path = _absolute(receipt_path)
    receipt = _read_recovery_receipt(receipt_path)
    entries = receipt["entries"]
    # Validate the whole operation before moving anything, then revalidate each
    # effect. A partial attempt never licenses overwriting unrelated new state.
    for index in range(len(entries)):
        _recovery_entry_route(receipt, index)
    result = {
        "ok": True, "schema_version": LOCAL_STATE_MIGRATION_SCHEMA,
        "dry_run": not execute, "status": "rollback_ready", "receipt": str(receipt_path),
    }
    if receipt["status"] == "rolled_back":
        return {**result, "status": "rolled_back"}
    if not execute:
        return result
    receipt = {**receipt, "status": "rolling_back"}
    _write_registry(receipt_path, receipt)
    for kind in ("runtime", "goal"):
        for index in reversed(range(len(entries))):
            entry = entries[index]
            if entry["kind"] != kind:
                continue
            active = _recovery_entry_route(receipt, index)
            old = Path(entry["source"])
            if active != old:
                active.rename(old)
    backup = receipt_path.parent
    for index, entry in enumerate(entries):
        if entry["kind"] == "registry":
            _recovery_entry_route(receipt, index)
            _restore_snapshot_file(
                backup / "snapshot" / str(index), Path(entry["source"]),
                expected_digest=entry["digest"],
            )
    _recovery_entry_route(receipt, 0)
    source_registry = Path(receipt["source_runtime_root"]) / GLOBAL_REGISTRY_FILENAME
    global_snapshot = backup / "snapshot" / "0" / GLOBAL_REGISTRY_FILENAME
    _restore_snapshot_file(global_snapshot, source_registry, expected_digest=_digest(global_snapshot))
    restored = {**receipt, "status": "rolled_back"}
    for index in range(len(entries)):
        _recovery_entry_route(restored, index)
    _write_registry(receipt_path, restored)
    return {**result, "dry_run": False, "status": "rolled_back"}


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
