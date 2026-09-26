from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .control_plane.projects.registry_codec import project_registry_transaction
from .control_plane.runtime.time import now_local_iso, utc_timestamp
from .global_registry import GlobalRegistryReduction, mutate_global_registry
from .history import load_registry
from .paths import global_registry_path, resolve_runtime_root, select_default_runtime_root
from .registry import read_json, registry_goals
from .runtime import validate_goal_id_path_segment


def _now_local() -> str:
    return now_local_iso()


def _timestamp() -> str:
    return utc_timestamp()


def _copy_backup(path: Path, *, label: str, dry_run: bool) -> str | None:
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.{label}-{_timestamp()}.bak")
    if not dry_run:
        shutil.copy2(path, backup_path)
    return str(backup_path)


def _resolve_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser())


def _same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if left is None or right is None:
        return False
    return _resolve_path(Path(str(left))) == _resolve_path(Path(str(right)))


def _project_root_from_registry(registry_path: Path) -> Path:
    parent = registry_path.expanduser().parent
    if parent.name == ".loopx":
        return parent.parent
    return parent


def _resolve_state_file(goal: dict[str, Any], *, registry_path: Path) -> Path | None:
    state_file = goal.get("state_file")
    if not state_file:
        return None
    path = Path(str(state_file)).expanduser()
    if path.is_absolute():
        return path
    repo = (
        Path(str(goal.get("repo"))).expanduser()
        if goal.get("repo")
        else _project_root_from_registry(registry_path)
    )
    return repo / path


def _unique_destination(path: Path) -> Path:
    candidate = path
    suffix = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}-{suffix}")
        suffix += 1
    return candidate


def _archive_state_directory(
    *,
    goal: dict[str, Any],
    registry_path: Path,
    archive_root: Path,
    timestamp: str,
    dry_run: bool,
) -> dict[str, Any]:
    goal_id = str(goal.get("id") or "")
    state_file = _resolve_state_file(goal, registry_path=registry_path)
    if state_file is None:
        return {
            "goal_id": goal_id,
            "action": "no-state-file-recorded",
            "archived": False,
        }
    project_root = _project_root_from_registry(registry_path).resolve()
    state_dir = state_file.parent
    try:
        state_dir_resolved = state_dir.resolve()
        state_dir_resolved.relative_to(project_root)
    except (OSError, ValueError):
        return {
            "goal_id": goal_id,
            "state_file": str(state_file),
            "action": "kept-external-state",
            "archived": False,
            "warning": "state file is outside the project root; kept in place",
        }
    if not state_dir.exists():
        return {
            "goal_id": goal_id,
            "state_file": str(state_file),
            "state_dir": str(state_dir),
            "action": "state-dir-missing",
            "archived": False,
        }
    destination = _unique_destination(archive_root / timestamp / "goals" / goal_id)
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(state_dir), str(destination))
    return {
        "goal_id": goal_id,
        "state_file": str(state_file),
        "state_dir": str(state_dir),
        "archive_path": str(destination),
        "action": "would-move" if dry_run else "moved",
        "archived": not dry_run,
    }


def _selected_goals(
    registry: dict[str, Any],
    *,
    requested_goal_ids: list[str] | None,
) -> list[dict[str, Any]]:
    goals = registry_goals(registry)
    if not requested_goal_ids:
        return goals
    requested = {
        validate_goal_id_path_segment(goal_id) for goal_id in requested_goal_ids
    }
    found = {str(goal.get("id")) for goal in goals if str(goal.get("id")) in requested}
    missing = sorted(requested - found)
    if missing:
        raise ValueError(f"goal id not found in project registry: {', '.join(missing)}")
    return [goal for goal in goals if str(goal.get("id")) in requested]


def _remove_local_goals(
    registry: dict[str, Any],
    *,
    target_goal_ids: set[str],
) -> tuple[dict[str, Any], int, int]:
    goals = registry.get("goals")
    before = len(registry_goals(registry))
    if not isinstance(goals, list):
        payload = dict(registry)
        payload["goals"] = []
        return payload, before, 0
    kept = [
        item
        for item in goals
        if not (isinstance(item, dict) and str(item.get("id") or "") in target_goal_ids)
    ]
    payload = dict(registry)
    payload["updated_at"] = _now_local()
    payload["goals"] = kept
    after = len([item for item in kept if isinstance(item, dict) and item.get("id")])
    return payload, before, after


def _remove_global_goals(
    global_registry: dict[str, Any],
    *,
    source_registry: Path,
    target_goal_ids: set[str],
) -> tuple[dict[str, Any], list[str], list[str], int, int]:
    goals = global_registry.get("goals")
    before = len(registry_goals(global_registry))
    if not isinstance(goals, list):
        payload = dict(global_registry)
        payload["goals"] = []
        return payload, [], [], before, 0

    kept: list[Any] = []
    removed: list[str] = []
    skipped_route_mismatch: list[str] = []
    for item in goals:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        goal_id = str(item.get("id") or "")
        if goal_id not in target_goal_ids:
            kept.append(item)
            continue
        if _same_path(item.get("source_registry"), source_registry):
            removed.append(goal_id)
            continue
        skipped_route_mismatch.append(goal_id)
        kept.append(item)

    payload = dict(global_registry)
    payload["schema_version"] = str(payload.get("schema_version") or "0.1")
    payload["updated_at"] = _now_local()
    payload["goals"] = kept
    after = len([item for item in kept if isinstance(item, dict) and item.get("id")])
    return payload, removed, skipped_route_mismatch, before, after


def _uninstall_global_registry_reduction(
    current: dict[str, Any],
    *,
    source_registry: Path,
    target_goal_ids: set[str],
) -> GlobalRegistryReduction:
    payload, removed, skipped, before, after = _remove_global_goals(
        current,
        source_registry=source_registry,
        target_goal_ids=target_goal_ids,
    )
    if not removed:
        payload = current
    return GlobalRegistryReduction(
        payload=payload,
        receipt={
            "removed": removed,
            "skipped_route_mismatch": skipped,
            "goal_count_before": before,
            "goal_count_after": after,
        },
        backup_label="project-uninstall-backup" if removed else None,
    )


def uninstall_project(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_ids: list[str] | None,
    archive_state: bool,
    remove_empty_registry: bool,
    execute: bool,
) -> dict[str, Any]:
    registry_path = registry_path.expanduser()
    if not registry_path.exists():
        raise FileNotFoundError(f"project registry does not exist: {registry_path}")
    registry_path = registry_path.resolve()
    project_registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(project_registry, runtime_root_override)
    global_path = global_registry_path(runtime_root)
    if global_path.exists() and registry_path == global_path.resolve():
        raise ValueError(
            "uninstall-project refused to operate on the global registry; "
            "run it from a project root or pass --registry <project>/.loopx/registry.json"
        )

    selected = _selected_goals(project_registry, requested_goal_ids=goal_ids)
    if not selected:
        raise ValueError("project registry contains no goals to uninstall")

    target_goal_ids = {str(goal.get("id")) for goal in selected if goal.get("id")}
    timestamp = _timestamp()
    dry_run = not execute
    new_project_registry, local_before, local_after = _remove_local_goals(
        project_registry,
        target_goal_ids=target_goal_ids,
    )

    global_preview = _uninstall_global_registry_reduction(
        read_json(global_path) if global_path.exists() else {},
        source_registry=registry_path,
        target_goal_ids=target_goal_ids,
    )
    global_receipt = global_preview.receipt
    global_removed = global_receipt["removed"]
    skipped_route_mismatch = global_receipt["skipped_route_mismatch"]
    global_before = global_receipt["goal_count_before"]
    global_after = global_receipt["goal_count_after"]

    archive_root = registry_path.parent / "archived-project-state"
    state_actions = (
        [
            _archive_state_directory(
                goal=goal,
                registry_path=registry_path,
                archive_root=archive_root,
                timestamp=timestamp,
                dry_run=dry_run,
            )
            for goal in selected
        ]
        if archive_state
        else [
            {
                "goal_id": str(goal.get("id")),
                "action": "kept",
                "archived": False,
            }
            for goal in selected
        ]
    )

    local_backup_path = _copy_backup(
        registry_path, label="project-uninstall-backup", dry_run=dry_run
    )
    global_backup_path = (
        _copy_backup(global_path, label="project-uninstall-backup", dry_run=dry_run)
        if dry_run and global_removed
        else None
    )

    wrote_local_registry = False
    removed_local_registry_file = False
    wrote_global_registry = False
    if execute:
        with project_registry_transaction(
            registry_path,
            operation="project_uninstall_local_registry",
        ) as transaction:
            locked_registry, local_before, local_after = _remove_local_goals(
                transaction.payload_copy(),
                target_goal_ids=target_goal_ids,
            )
            if remove_empty_registry and local_after == 0:
                removed_local_registry_file = transaction.remove()
            else:
                wrote_local_registry = transaction.commit(locked_registry)
        global_mutation = mutate_global_registry(
            global_path,
            "project_uninstall_global_registry",
            lambda current: _uninstall_global_registry_reduction(
                current,
                source_registry=registry_path,
                target_goal_ids=target_goal_ids,
            ),
        )
        global_receipt = global_mutation["receipt"]
        global_removed = global_receipt["removed"]
        skipped_route_mismatch = global_receipt["skipped_route_mismatch"]
        global_before = global_receipt["goal_count_before"]
        global_after = global_receipt["goal_count_after"]
        global_backup_path = global_mutation["backup_path"]
        wrote_global_registry = bool(global_mutation["wrote"])

    return {
        "ok": True,
        "schema_version": "loopx_project_uninstall_v0",
        "dry_run": dry_run,
        "execute": execute,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root or select_default_runtime_root()),
        "global_registry": str(global_path),
        "goal_ids": sorted(target_goal_ids),
        "local_registry_goal_count_before": local_before,
        "local_registry_goal_count_after": local_after,
        "local_registry_backup_path": local_backup_path,
        "wrote_local_registry": wrote_local_registry,
        "removed_local_registry_file": removed_local_registry_file,
        "global_registry_goal_count_before": global_before,
        "global_registry_goal_count_after": global_after,
        "global_registry_removed_goal_ids": sorted(set(global_removed)),
        "global_registry_skipped_route_mismatch_goal_ids": sorted(
            set(skipped_route_mismatch)
        ),
        "global_registry_backup_path": global_backup_path,
        "wrote_global_registry": wrote_global_registry,
        "archive_state": archive_state,
        "state_actions": state_actions,
        "actions": [
            {
                "path": str(registry_path),
                "action": (
                    "would-remove-file"
                    if remove_empty_registry and local_after == 0
                    else "would-write"
                )
                if dry_run
                else ("removed-file" if removed_local_registry_file else "wrote"),
            },
            {
                "path": str(global_path),
                "action": "would-write"
                if dry_run and global_removed
                else ("wrote" if wrote_global_registry else "kept"),
            },
        ],
        "warnings": (
            [
                "matching global route was not found; only project-local state will be changed"
            ]
            if not global_removed
            else []
        )
        + (
            [
                "one or more global goals with the same id were kept because source_registry did not match this project"
            ]
            if skipped_route_mismatch
            else []
        ),
    }


def render_project_uninstall_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Project Uninstall",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- execute: `{payload.get('execute')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- global_registry: `{payload.get('global_registry')}`",
        f"- goals: `{', '.join(payload.get('goal_ids') or [])}`",
        f"- local_registry_goal_count: `{payload.get('local_registry_goal_count_before')} -> {payload.get('local_registry_goal_count_after')}`",
        f"- global_registry_goal_count: `{payload.get('global_registry_goal_count_before')} -> {payload.get('global_registry_goal_count_after')}`",
        f"- wrote_local_registry: `{payload.get('wrote_local_registry')}`",
        f"- wrote_global_registry: `{payload.get('wrote_global_registry')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    if payload.get("local_registry_backup_path"):
        lines.append(
            f"- local_registry_backup_path: `{payload.get('local_registry_backup_path')}`"
        )
    if payload.get("global_registry_backup_path"):
        lines.append(
            f"- global_registry_backup_path: `{payload.get('global_registry_backup_path')}`"
        )

    removed = payload.get("global_registry_removed_goal_ids") or []
    if removed:
        lines.extend(["", "## Removed Global Routes"])
        lines.extend(f"- {goal_id}" for goal_id in removed)
    state_actions = (
        payload.get("state_actions")
        if isinstance(payload.get("state_actions"), list)
        else []
    )
    if state_actions:
        lines.extend(["", "## Local State"])
        for action in state_actions:
            if not isinstance(action, dict):
                continue
            suffix = (
                f" -> `{action.get('archive_path')}`"
                if action.get("archive_path")
                else ""
            )
            lines.append(f"- {action.get('goal_id')}: `{action.get('action')}`{suffix}")
            if action.get("warning"):
                lines.append(f"  - warning: {action.get('warning')}")
    warnings = (
        payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    )
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    if payload.get("dry_run"):
        lines.extend(["", "Run again with `--execute` to disconnect this project."])
    return "\n".join(lines)
