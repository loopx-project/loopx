from __future__ import annotations

import copy
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
from typing import Any, Callable

from .authority import compact_authority_registry
from .control_plane.projects.contract import validate_project_record_bindings
from .control_plane.projects.registry_codec import load_registry
from .control_plane.runtime.time import now_local_iso
from .file_lock import exclusive_cross_runtime_file_lock
from .paths import global_registry_path, resolve_runtime_root, select_default_runtime_root
from .registry import read_json, registry_goals
from .registry_writability import is_write_denied_error, probe_registry_write_path


ATTENTION_OVERRIDE_FIELDS = (
    "waiting_on",
    "attention_status",
    "operator_question",
    "recommended_action",
    "next_handoff_condition",
)

ROUTE_FIELDS = ("source_registry", "repo", "state_file")


def now_local() -> str:
    return now_local_iso()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


def _load_global_registry(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


@dataclass(frozen=True, slots=True)
class GlobalRegistryReduction:
    payload: dict[str, Any]
    receipt: dict[str, Any]
    backup_label: str | None = None


def _global_registry_backup_path(global_path: Path, label: str) -> Path:
    timestamp = now_local().replace(":", "").replace("-", "")
    return global_path.with_name(f"{global_path.name}.{label}-{timestamp}.bak")


def _mutate_global_registry_locked(
    global_path: Path,
    reducer: Callable[[dict[str, Any]], GlobalRegistryReduction],
) -> dict[str, Any]:
    current = _load_global_registry(global_path)
    reduction = reducer(copy.deepcopy(current))
    if not isinstance(reduction, GlobalRegistryReduction):
        raise TypeError(
            "global registry reducer must return GlobalRegistryReduction"
        )
    if not isinstance(reduction.payload, dict):
        raise TypeError("global registry reducer payload must be a JSON object")

    wrote = reduction.payload != current
    backup_path = None
    if wrote and reduction.backup_label and global_path.exists():
        backup = _global_registry_backup_path(
            global_path,
            reduction.backup_label,
        )
        write_json(backup, current)
        backup_path = str(backup)
    if wrote:
        write_json(global_path, reduction.payload)

    return {
        "before": current,
        "after": reduction.payload,
        "receipt": reduction.receipt,
        "backup_path": backup_path,
        "wrote": wrote,
    }


def mutate_global_registry(
    global_path: Path,
    operation: str,
    reducer: Callable[[dict[str, Any]], GlobalRegistryReduction],
) -> dict[str, Any]:
    """Apply one authoritative global-registry read-modify-write transaction."""

    with exclusive_cross_runtime_file_lock(global_path, operation=operation):
        return _mutate_global_registry_locked(global_path, reducer)


def global_write_denied_payload(
    *,
    registry_path: Path,
    global_path: Path,
    runtime_root: Path,
    dry_run: bool,
    goals: list[dict[str, Any]],
    merged_goals: list[Any],
    actions: list[str],
    attempted_goal_ids: list[str],
    route_collisions: list[dict[str, Any]],
    allow_route_replacement: bool,
    backup_path: str | None,
    synced_at: str,
    exc: BaseException,
    writability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errno_value = getattr(exc, "errno", None)
    return {
        "ok": False,
        "dry_run": dry_run,
        "skipped": False,
        "registry": str(registry_path),
        "global_registry": str(global_path),
        "runtime_root": str(runtime_root),
        "source_goal_count": len(goals),
        "global_goal_count": len(merged_goals),
        "synced_goal_ids": [],
        "attempted_goal_ids": attempted_goal_ids,
        "actions": actions,
        "route_collisions": route_collisions,
        "route_replacement_allowed": allow_route_replacement,
        "backup_path": backup_path,
        "updated_at": synced_at,
        "wrote": False,
        "write_denied": True,
        "error_kind": "global_registry_write_denied",
        "errno": errno_value,
        "error": str(exc),
        "project_registry_usable": True,
        "fallback_registry": str(registry_path),
        "global_registry_writability": writability or {},
        "requires_global_registry_repair": True,
        "requires_host_permission": bool(
            (writability or {}).get("requires_host_permission")
            or is_write_denied_error(exc)
        ),
        "recommended_action": (
            f"Fix write access for `{global_path}` and rerun `loopx sync-global` "
            f"from `{registry_path}`. Project-local state is still available through "
            f"`loopx --registry {registry_path} ...`, but shared status/quota is not healthy "
            "until the global registry can be written."
        ),
    }


def sanitize_goal_for_global(
    goal: dict[str, Any], *, source_registry: Path, synced_at: str
) -> dict[str, Any]:
    copied = copy.deepcopy(goal)
    authority_sources = copied.pop("authority_sources", [])
    repo = Path(str(copied.get("repo"))).expanduser() if copied.get("repo") else None
    authority_registry = compact_authority_registry(copied, project=repo)
    authority_registry.pop("default_entries", None)
    copied.pop("authority_registry", None)
    copied["source_registry"] = str(source_registry.expanduser().resolve())
    copied["synced_at"] = synced_at
    copied["authority_source_count"] = (
        len(authority_sources) if isinstance(authority_sources, list) else 0
    )
    copied["authority_registry"] = authority_registry
    return copied


def same_source_registry(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    existing_source = existing.get("source_registry")
    incoming_source = incoming.get("source_registry")
    if not existing_source or not incoming_source:
        return False
    try:
        return (
            Path(str(existing_source)).expanduser().resolve()
            == Path(str(incoming_source)).expanduser().resolve()
        )
    except OSError:
        return str(existing_source) == str(incoming_source)


def _resolved_route_value(goal: dict[str, Any], field: str) -> str | None:
    value = goal.get(field)
    if value is None:
        return None
    text = str(value)
    if field == "source_registry":
        try:
            return str(Path(text).expanduser().resolve())
        except OSError:
            return text
    return text


def route_snapshot(goal: dict[str, Any] | None) -> dict[str, str | None]:
    if not isinstance(goal, dict):
        return {field: None for field in ROUTE_FIELDS}
    return {field: _resolved_route_value(goal, field) for field in ROUTE_FIELDS}


def route_collision(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any] | None:
    existing_route = route_snapshot(existing)
    incoming_route = route_snapshot(incoming)
    changed = [
        field
        for field in ROUTE_FIELDS
        if existing_route.get(field)
        and incoming_route.get(field)
        and existing_route.get(field) != incoming_route.get(field)
    ]
    if not changed:
        return None
    return {
        "goal_id": str(incoming.get("id") or existing.get("id") or ""),
        "changed_fields": changed,
        "existing_route": existing_route,
        "incoming_route": incoming_route,
    }


def collision_message(collision: dict[str, Any]) -> str:
    goal_id = collision.get("goal_id") or "<unknown>"
    fields = ", ".join(collision.get("changed_fields") or [])
    return (
        f"global route collision for goal_id {goal_id}: {fields} would change. "
        "Use the existing source_registry to register agents, choose a new --fork-goal id, "
        "or rerun with --replace-state to write a global registry backup and replace the route."
    )


def preserve_attention_override(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    merged = {
        key: value
        for key, value in incoming.items()
        if key != "clear_attention_override"
    }
    if incoming.get("clear_attention_override"):
        return merged
    if same_source_registry(existing, incoming):
        return merged

    preserved = False
    for field in ATTENTION_OVERRIDE_FIELDS:
        if merged.get(field) or not existing.get(field):
            continue
        merged[field] = existing[field]
        preserved = True
    if preserved and existing.get("attention_override_synced_from"):
        merged["attention_override_synced_from"] = existing.get(
            "attention_override_synced_from"
        )
    return merged


def merge_goal_entries(
    existing: list[Any],
    incoming: list[dict[str, Any]],
    *,
    allow_route_replacement: bool = False,
) -> tuple[list[Any], list[str], list[str], list[dict[str, Any]]]:
    merged: list[Any] = []
    seen_incoming = {str(goal.get("id")) for goal in incoming if goal.get("id")}
    actions: list[str] = []
    synced_ids: list[str] = []
    collisions: list[dict[str, Any]] = []

    existing_by_id = {
        str(item.get("id")): item
        for item in existing
        if isinstance(item, dict) and item.get("id")
    }

    for item in existing:
        if isinstance(item, dict) and str(item.get("id")) in seen_incoming:
            continue
        merged.append(item)

    for goal in incoming:
        goal_id = str(goal.get("id") or "")
        if not goal_id:
            continue
        existing_goal = existing_by_id.get(goal_id)
        action = "updated" if existing_goal else "added"
        if existing_goal:
            collision = route_collision(existing_goal, goal)
            if collision:
                collisions.append(collision)
                if not allow_route_replacement:
                    raise ValueError(collision_message(collision))
                action = "replaced-route"
            goal = preserve_attention_override(existing_goal, goal)
        merged.append(goal)
        actions.append(f"{goal_id}:{action}")
        synced_ids.append(goal_id)
    return merged, actions, synced_ids, collisions


def merge_project_entries(
    existing: list[Any],
    incoming: list[dict[str, Any]],
) -> list[Any]:
    incoming_by_id = _project_entries_by_id(incoming, source="source registry")
    existing_by_id = _project_entries_by_id(existing, source="global registry")
    for project_id, project in incoming_by_id.items():
        existing_project = existing_by_id.get(project_id)
        if existing_project is not None and existing_project != project:
            raise ValueError(
                f"global ProjectRecord conflicts with source registry: {project_id}"
            )
    return [
        item
        for item in existing
        if not (
            isinstance(item, dict)
            and str(item.get("project_id") or "") in incoming_by_id
        )
    ] + list(incoming_by_id.values())


def _project_entries_by_id(
    entries: list[Any],
    *,
    source: str,
) -> dict[str, dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError(f"{source} projects entries must be JSON objects")
        project_id = str(item.get("project_id") or "").strip()
        if not project_id:
            raise ValueError(f"{source} ProjectRecord is missing project_id")
        if project_id in projects:
            raise ValueError(f"{source} has duplicate project_id: {project_id}")
        validate_project_record_bindings(item, source=source)
        projects[project_id] = item
    return projects


def _merge_global_registry_payload(
    existing: dict[str, Any],
    incoming: list[dict[str, Any]],
    incoming_projects: list[dict[str, Any]],
    *,
    allow_route_replacement: bool,
    schema_version_fallback: str,
    runtime_root: Path,
    synced_at: str,
) -> tuple[dict[str, Any], list[Any], list[str], list[str], list[dict[str, Any]]]:
    """Reduce one global-registry snapshot with sanitized incoming goals."""

    existing_goals = existing.get("goals")
    if not isinstance(existing_goals, list):
        existing_goals = []
    merged_goals, actions, synced_ids, collisions = merge_goal_entries(
        existing_goals,
        incoming,
        allow_route_replacement=allow_route_replacement,
    )
    existing_projects = existing.get("projects")
    if existing_projects is None:
        existing_projects = []
    elif not isinstance(existing_projects, list):
        raise ValueError("global registry projects must be a list")
    merged_projects = merge_project_entries(existing_projects, incoming_projects)
    payload = dict(existing)
    payload["schema_version"] = str(
        payload.get("schema_version") or schema_version_fallback or "0.1"
    )
    payload["updated_at"] = synced_at
    payload["common_runtime_root"] = str(runtime_root or select_default_runtime_root())
    payload["registry_role"] = "global-local"
    if merged_projects:
        payload["projects"] = merged_projects
    payload["goals"] = merged_goals
    return payload, merged_goals, actions, synced_ids, collisions


def _sync_global_registry_reduction(
    current: dict[str, Any],
    incoming: list[dict[str, Any]],
    incoming_projects: list[dict[str, Any]],
    *,
    allow_route_replacement: bool,
    schema_version_fallback: str,
    runtime_root: Path,
    synced_at: str,
) -> GlobalRegistryReduction:
    payload, merged_goals, actions, synced_ids, collisions = (
        _merge_global_registry_payload(
            current,
            incoming,
            incoming_projects,
            allow_route_replacement=allow_route_replacement,
            schema_version_fallback=schema_version_fallback,
            runtime_root=runtime_root,
            synced_at=synced_at,
        )
    )
    return GlobalRegistryReduction(
        payload=payload,
        receipt={
            "merged_goals": merged_goals,
            "actions": actions,
            "synced_ids": synced_ids,
            "collisions": collisions,
        },
        backup_label=(
            "route-collision-backup" if collisions and allow_route_replacement else None
        ),
    )


def _global_goal_path(
    goal: dict[str, Any],
    field: str,
    *,
    global_path: Path,
) -> Path | None:
    value = goal.get(field)
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    repo = goal.get("repo")
    if repo:
        return Path(str(repo)).expanduser() / path
    return global_path.parent / path


def _retire_global_registry_reduction(
    current: dict[str, Any],
    *,
    requested_ids: list[str],
    global_path: Path,
    updated_at: str,
) -> GlobalRegistryReduction:
    current_goals = current.get("goals")
    if not isinstance(current_goals, list):
        current_goals = []
    matches_by_id: dict[str, list[dict[str, Any]]] = {
        goal_id: [
            goal
            for goal in current_goals
            if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id
        ]
        for goal_id in requested_ids
    }
    missing_ids = [goal_id for goal_id, matches in matches_by_id.items() if not matches]
    duplicate_ids = [
        goal_id for goal_id, matches in matches_by_id.items() if len(matches) > 1
    ]
    if missing_ids:
        raise ValueError(
            f"goal_id not found in global registry: {', '.join(missing_ids)}"
        )
    if duplicate_ids:
        raise ValueError(
            "global registry contains duplicate goal ids; deduplicate before retirement: "
            + ", ".join(duplicate_ids)
        )

    inspections: list[dict[str, Any]] = []
    blocked_ids: list[str] = []
    for goal_id in requested_ids:
        goal = matches_by_id[goal_id][0]
        source_path = _global_goal_path(
            goal, "source_registry", global_path=global_path
        )
        state_path = _global_goal_path(goal, "state_file", global_path=global_path)
        source_missing = source_path is None or not source_path.exists()
        state_missing = state_path is None or not state_path.exists()
        eligible = source_missing and state_missing
        if not eligible:
            blocked_ids.append(goal_id)
        inspections.append(
            {
                "goal_id": goal_id,
                "eligible": eligible,
                "source_registry_missing": source_missing,
                "state_file_missing": state_missing,
            }
        )
    if blocked_ids:
        raise ValueError(
            "refusing to retire goal(s) with a live source_registry or state_file: "
            + ", ".join(blocked_ids)
        )

    retired = set(requested_ids)
    retained_goals = [
        goal
        for goal in current_goals
        if not (isinstance(goal, dict) and str(goal.get("id") or "") in retired)
    ]
    updated = dict(current)
    updated["updated_at"] = updated_at
    updated["goals"] = retained_goals
    return GlobalRegistryReduction(
        payload=updated,
        receipt={
            "inspections": inspections,
            "global_goal_count_before": len(current_goals),
            "global_goal_count_after": len(retained_goals),
        },
        backup_label="retire-goal-backup",
    )


def retire_global_registry_goals(
    *,
    runtime_root_override: str | None,
    goal_ids: list[str],
    execute: bool,
) -> dict[str, Any]:
    requested_ids = list(
        dict.fromkeys(goal_id.strip() for goal_id in goal_ids if goal_id.strip())
    )
    if not requested_ids:
        raise ValueError("at least one explicit --goal-id is required")

    runtime_root = (
        Path(runtime_root_override).expanduser()
        if runtime_root_override
        else select_default_runtime_root()
    )
    global_path = global_registry_path(runtime_root)
    if not global_path.exists():
        raise FileNotFoundError(f"global registry does not exist: {global_path}")

    updated_at = now_local()
    preview = _retire_global_registry_reduction(
        _load_global_registry(global_path),
        requested_ids=requested_ids,
        global_path=global_path,
        updated_at=updated_at,
    )
    receipt = preview.receipt
    backup_path = str(_global_registry_backup_path(global_path, "retire-goal-backup"))
    writability: dict[str, Any] = {}
    if execute:
        writability = probe_registry_write_path(global_path, create_parent=True)
        if not writability.get("ok"):
            return {
                "ok": False,
                "schema_version": "loopx_global_goal_retirement_v0",
                "dry_run": False,
                "execute": True,
                "global_registry": str(global_path),
                "runtime_root": str(runtime_root),
                "requested_goal_ids": requested_ids,
                "retired_goal_ids": [],
                "inspections": receipt["inspections"],
                "backup_path": None,
                "backup_written": False,
                "wrote": False,
                "global_registry_writability": writability,
                "error": str(
                    writability.get("error") or "global registry is not writable"
                ),
                "recommended_action": writability.get("recommended_action"),
            }

        mutation = mutate_global_registry(
            global_path,
            "retire_global_registry_goals",
            lambda current: _retire_global_registry_reduction(
                current,
                requested_ids=requested_ids,
                global_path=global_path,
                updated_at=updated_at,
            ),
        )
        receipt = mutation["receipt"]
        backup_path = mutation["backup_path"]

    return {
        "ok": True,
        "schema_version": "loopx_global_goal_retirement_v0",
        "dry_run": not execute,
        "execute": execute,
        "global_registry": str(global_path),
        "runtime_root": str(runtime_root),
        "requested_goal_ids": requested_ids,
        "retired_goal_ids": requested_ids if execute else [],
        "planned_retired_goal_ids": requested_ids,
        "inspections": receipt["inspections"],
        "global_goal_count_before": receipt["global_goal_count_before"],
        "global_goal_count_after": receipt["global_goal_count_after"],
        "backup_path": backup_path,
        "backup_written": bool(backup_path) if execute else False,
        "wrote": execute,
        "updated_at": updated_at,
        "global_registry_writability": writability,
    }


def render_global_goal_retirement_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Global Goal Retirement",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- global_registry: `{payload.get('global_registry')}`",
        f"- wrote: `{payload.get('wrote')}`",
        f"- backup_path: `{payload.get('backup_path')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    planned = payload.get("planned_retired_goal_ids") or []
    if planned:
        lines.extend(["", "## Explicit Goals"])
        lines.extend(f"- `{goal_id}`" for goal_id in planned)
    return "\n".join(lines)


def _sync_project_registry_to_global_once(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None = None,
    dry_run: bool = False,
    allow_route_replacement: bool = False,
    _global_registry_lock_held: bool = False,
    _expected_global_registry: Path | None = None,
) -> dict[str, Any]:
    registry_path = registry_path.expanduser()
    if not registry_path.exists():
        raise FileNotFoundError(f"registry file does not exist: {registry_path}")
    project_registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(project_registry, runtime_root_override)
    global_path = global_registry_path(runtime_root)
    if (
        _expected_global_registry is not None
        and global_path.absolute() != _expected_global_registry.absolute()
    ):
        raise ValueError(
            "global registry route changed while waiting for the write lock; retry"
        )
    if registry_path.resolve() == global_path.resolve():
        return {
            "ok": True,
            "dry_run": dry_run,
            "skipped": True,
            "reason": "source registry is already the global registry",
            "registry": str(registry_path),
            "global_registry": str(global_path),
            "runtime_root": str(runtime_root),
            "synced_goal_ids": [],
            "actions": [],
        }

    goals = registry_goals(project_registry)
    if goal_id:
        goals = [goal for goal in goals if str(goal.get("id")) == goal_id]
    if goal_id and not goals:
        raise ValueError(f"goal id not found in source registry: {goal_id}")

    synced_at = now_local()
    incoming = [
        sanitize_goal_for_global(
            goal, source_registry=registry_path, synced_at=synced_at
        )
        for goal in goals
    ]
    source_projects = project_registry.get("projects")
    if source_projects is None:
        source_projects = []
    elif not isinstance(source_projects, list):
        raise ValueError("source registry projects must be a list")
    project_ids = {
        str(goal.get("project_id"))
        for goal in goals
        if str(goal.get("project_id") or "").strip()
    }
    projects_by_id = _project_entries_by_id(
        source_projects,
        source="source registry",
    )
    missing_project_ids = sorted(project_ids - projects_by_id.keys())
    if missing_project_ids:
        raise ValueError(
            "goal references a missing ProjectRecord: " + ", ".join(missing_project_ids)
        )
    incoming_projects = [projects_by_id[project_id] for project_id in sorted(project_ids)]
    merge_kwargs: dict[str, Any] = {
        "allow_route_replacement": allow_route_replacement,
        "schema_version_fallback": str(project_registry.get("schema_version") or "0.1"),
        "runtime_root": runtime_root,
        "synced_at": synced_at,
    }
    preview = _sync_global_registry_reduction(
        _load_global_registry(global_path),
        incoming,
        incoming_projects,
        **merge_kwargs,
    )
    preview_receipt = preview.receipt
    merged_goals = preview_receipt["merged_goals"]
    actions = preview_receipt["actions"]
    synced_ids = preview_receipt["synced_ids"]
    collisions = preview_receipt["collisions"]
    backup_path = None

    writability = None
    if not dry_run:
        writability = probe_registry_write_path(global_path, create_parent=True)
        if not writability.get("ok"):
            exc = PermissionError(
                writability.get("errno")
                if isinstance(writability.get("errno"), int)
                else errno.EPERM,
                str(writability.get("error") or "global registry is not writable"),
                str(global_path),
            )
            return global_write_denied_payload(
                registry_path=registry_path,
                global_path=global_path,
                runtime_root=runtime_root,
                dry_run=dry_run,
                goals=goals,
                merged_goals=merged_goals,
                actions=actions,
                attempted_goal_ids=synced_ids,
                route_collisions=collisions,
                allow_route_replacement=allow_route_replacement,
                backup_path=backup_path,
                synced_at=synced_at,
                exc=exc,
                writability=writability,
            )

    try:
        if dry_run:
            backup_path = (
                str(
                    _global_registry_backup_path(
                        global_path,
                        "route-collision-backup",
                    )
                )
                if collisions and allow_route_replacement
                else None
            )
        else:
            def reduce_current(current: dict[str, Any]) -> GlobalRegistryReduction:
                return _sync_global_registry_reduction(
                    current,
                    incoming,
                    incoming_projects,
                    **merge_kwargs,
                )

            mutation = (
                _mutate_global_registry_locked(
                    global_path,
                    reduce_current,
                )
                if _global_registry_lock_held
                else mutate_global_registry(
                    global_path,
                    "sync_global_registry",
                    reduce_current,
                )
            )
            receipt = mutation["receipt"]
            merged_goals = receipt["merged_goals"]
            actions = receipt["actions"]
            synced_ids = receipt["synced_ids"]
            collisions = receipt["collisions"]
            backup_path = mutation["backup_path"]
    except OSError as exc:
        if not is_write_denied_error(exc):
            raise
        return global_write_denied_payload(
            registry_path=registry_path,
            global_path=global_path,
            runtime_root=runtime_root,
            dry_run=dry_run,
            goals=goals,
            merged_goals=merged_goals,
            actions=actions,
            attempted_goal_ids=synced_ids,
            route_collisions=collisions,
            allow_route_replacement=allow_route_replacement,
            backup_path=backup_path,
            synced_at=synced_at,
            exc=exc,
            writability=writability,
        )

    return {
        "ok": True,
        "dry_run": dry_run,
        "skipped": False,
        "registry": str(registry_path),
        "global_registry": str(global_path),
        "runtime_root": str(runtime_root),
        "source_goal_count": len(goals),
        "global_goal_count": len(merged_goals),
        "synced_goal_ids": synced_ids,
        "actions": actions,
        "route_collisions": collisions,
        "route_replacement_allowed": allow_route_replacement,
        "backup_path": backup_path,
        "updated_at": synced_at,
        "wrote": not dry_run,
        "global_registry_writability": writability or {},
    }


def sync_project_registry_to_global(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None = None,
    dry_run: bool = False,
    allow_route_replacement: bool = False,
    _global_registry_lock_held: bool = False,
) -> dict[str, Any]:
    """Sync a fresh source snapshot while holding the target registry lock."""

    if dry_run or _global_registry_lock_held:
        return _sync_project_registry_to_global_once(
            registry_path=registry_path,
            runtime_root_override=runtime_root_override,
            goal_id=goal_id,
            dry_run=dry_run,
            allow_route_replacement=allow_route_replacement,
            _global_registry_lock_held=_global_registry_lock_held,
        )

    source_registry = registry_path.expanduser()
    if not source_registry.exists():
        raise FileNotFoundError(
            f"registry file does not exist: {source_registry}"
        )
    source_payload = load_registry(source_registry)
    runtime_root = resolve_runtime_root(source_payload, runtime_root_override)
    target_registry = global_registry_path(runtime_root)
    if source_registry.resolve() == target_registry.resolve():
        # This route's global registry is the source registry itself, which a
        # caller such as configure-goal already owns through the source
        # transaction lock. Acquiring the cross-runtime lock here would wait
        # on this process while holding the source lock, so a single-runtime
        # route would write the source and then fail to report it. The
        # single-shot reducer already treats this route as an owned no-op.
        return _sync_project_registry_to_global_once(
            registry_path=source_registry,
            runtime_root_override=runtime_root_override,
            goal_id=goal_id,
            dry_run=False,
            allow_route_replacement=allow_route_replacement,
            _global_registry_lock_held=_global_registry_lock_held,
        )
    with exclusive_cross_runtime_file_lock(
        target_registry,
        operation="sync_global_registry",
    ):
        return _sync_project_registry_to_global_once(
            registry_path=source_registry,
            runtime_root_override=runtime_root_override,
            goal_id=goal_id,
            dry_run=False,
            allow_route_replacement=allow_route_replacement,
            _global_registry_lock_held=True,
            _expected_global_registry=target_registry,
        )


def render_global_sync_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Global Registry Sync",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- skipped: `{payload.get('skipped')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- global_registry: `{payload.get('global_registry')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- source_goal_count: `{payload.get('source_goal_count')}`",
        f"- global_goal_count: `{payload.get('global_goal_count')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        if payload.get("write_denied"):
            lines.append(f"- error_kind: `{payload.get('error_kind')}`")
            lines.append(f"- fallback_registry: `{payload.get('fallback_registry')}`")
            lines.append(
                f"- project_registry_usable: `{payload.get('project_registry_usable')}`"
            )
            if payload.get("recommended_action"):
                lines.append(
                    f"- recommended_action: {payload.get('recommended_action')}"
                )
        return "\n".join(lines)
    if payload.get("reason"):
        lines.append(f"- reason: {payload.get('reason')}")
    if payload.get("backup_path"):
        lines.append(f"- backup_path: `{payload.get('backup_path')}`")
    collisions = payload.get("route_collisions") or []
    if collisions:
        lines.extend(["", "## Route Replacements"])
        for collision in collisions:
            lines.append(f"- {collision_message(collision)}")
    synced = payload.get("synced_goal_ids") or []
    if synced:
        lines.extend(["", "## Synced Goals"])
        lines.extend(f"- `{goal_id}`" for goal_id in synced)
    actions = payload.get("actions") or []
    if actions:
        lines.extend(["", "## Actions"])
        lines.extend(f"- {action}" for action in actions)
    return "\n".join(lines)
