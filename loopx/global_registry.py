from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
import copy
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
from typing import TypedDict

from .authority import compact_authority_registry
from .control_plane.coordination.shadow_management import shadow_maintenance_lock_target
from .control_plane.effect_runtime import CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
from .control_plane.projects.contract import validate_project_record_bindings
from .control_plane.projects.registry_codec import load_registry
from .control_plane.runtime.time import now_local_iso
from .file_lock import exclusive_cross_runtime_file_lock
from .paths import (
    global_registry_path,
    resolve_runtime_root,
    select_default_runtime_root,
    validate_goal_id_path_segment,
)
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


class GlobalRegistryMutation(TypedDict):
    before: dict[str, object]
    after: dict[str, object]
    receipt: dict[str, object]
    backup_path: str | None
    wrote: bool


@dataclass(frozen=True, slots=True)
class GlobalRegistryReduction:
    payload: dict[str, object]
    receipt: dict[str, object]
    backup_label: str | None = None


def now_local() -> str:
    return now_local_iso()


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


def _load_global_registry(path: Path) -> dict[str, object]:
    return read_json(path) if path.exists() else {}


def _list_items(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _str_items(value: object) -> list[str]:
    return [str(item) for item in _list_items(value)]


def _dict_items(value: object) -> list[dict[str, object]]:
    return [item for item in _list_items(value) if isinstance(item, dict)]


def _equal_except_key(
    left: Mapping[str, object], right: Mapping[str, object], excluded: str
) -> bool:
    return {k: v for k, v in left.items() if k != excluded} == {
        k: v for k, v in right.items() if k != excluded
    }


def _resolve_source_text(value: object) -> str:
    text = str(value)
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return text


def _registry_projects(registry: Mapping[str, object], *, source: str) -> list[object]:
    projects = registry.get("projects")
    if projects is None:
        return []
    if not isinstance(projects, list):
        raise ValueError(f"{source} projects must be a list")
    return projects


def _goals_by_id(entries: Sequence[object]) -> dict[str, Mapping[str, object]]:
    return {
        str(item.get("id")): item
        for item in entries
        if isinstance(item, Mapping) and item.get("id")
    }


def _global_registry_backup_path(global_path: Path, label: str) -> Path:
    timestamp = now_local().replace(":", "").replace("-", "")
    return global_path.with_name(f"{global_path.name}.{label}-{timestamp}.bak")


def _mutate_global_registry_locked(
    global_path: Path,
    reducer: Callable[[dict[str, object]], GlobalRegistryReduction],
) -> GlobalRegistryMutation:
    current = _load_global_registry(global_path)
    reduction = reducer(copy.deepcopy(current))
    if not isinstance(reduction, GlobalRegistryReduction):
        raise TypeError("global registry reducer must return GlobalRegistryReduction")
    if not isinstance(reduction.payload, dict):
        raise TypeError("global registry reducer payload must be a JSON object")

    wrote = reduction.payload != current
    backup_path: str | None = None
    if wrote and reduction.backup_label and global_path.exists():
        backup = _global_registry_backup_path(global_path, reduction.backup_label)
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
    reducer: Callable[[dict[str, object]], GlobalRegistryReduction],
) -> GlobalRegistryMutation:
    """Apply one authoritative global-registry read-modify-write transaction."""

    with exclusive_cross_runtime_file_lock(global_path, operation=operation):
        return _mutate_global_registry_locked(global_path, reducer)


def global_write_denied_payload(
    *,
    registry_path: Path,
    global_path: Path,
    runtime_root: Path,
    dry_run: bool,
    goals: Sequence[Mapping[str, object]],
    merged_goals: Sequence[object],
    actions: Sequence[str],
    attempted_goal_ids: Sequence[str],
    route_collisions: Sequence[Mapping[str, object]],
    allow_route_replacement: bool,
    backup_path: str | None,
    synced_at: str,
    exc: BaseException,
    writability: Mapping[str, object] | None = None,
) -> dict[str, object]:
    writability_dict = dict(writability) if writability else {}
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
        "attempted_goal_ids": list(attempted_goal_ids),
        "actions": list(actions),
        "route_collisions": [dict(collision) for collision in route_collisions],
        "route_replacement_allowed": allow_route_replacement,
        "backup_path": backup_path,
        "updated_at": synced_at,
        "wrote": False,
        "write_denied": True,
        "error_kind": "global_registry_write_denied",
        "errno": getattr(exc, "errno", None),
        "error": str(exc),
        "project_registry_usable": True,
        "fallback_registry": str(registry_path),
        "global_registry_writability": writability_dict,
        "requires_global_registry_repair": True,
        "requires_host_permission": bool(
            writability_dict.get("requires_host_permission") or is_write_denied_error(exc)
        ),
        "recommended_action": (
            f"Fix write access for `{global_path}` and rerun `loopx sync-global` "
            f"from `{registry_path}`. Project-local state is still available through "
            f"`loopx --registry {registry_path} ...`, but shared status/quota is not healthy "
            "until the global registry can be written."
        ),
    }


def sanitize_goal_for_global(
    goal: Mapping[str, object], *, source_registry: Path, synced_at: str
) -> dict[str, object]:
    copied = dict(copy.deepcopy(goal))
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


def same_source_registry(
    existing: Mapping[str, object], incoming: Mapping[str, object]
) -> bool:
    existing_source = existing.get("source_registry")
    incoming_source = incoming.get("source_registry")
    return bool(existing_source and incoming_source) and (
        _resolve_source_text(existing_source) == _resolve_source_text(incoming_source)
    )


def _resolved_route_value(goal: Mapping[str, object], field: str) -> str | None:
    value = goal.get(field)
    if value is None:
        return None
    return _resolve_source_text(value) if field == "source_registry" else str(value)


def route_snapshot(goal: Mapping[str, object] | None) -> dict[str, str | None]:
    if not isinstance(goal, Mapping):
        return {field: None for field in ROUTE_FIELDS}
    return {field: _resolved_route_value(goal, field) for field in ROUTE_FIELDS}


def route_collision(
    existing: Mapping[str, object], incoming: Mapping[str, object]
) -> dict[str, object] | None:
    existing_route, incoming_route = route_snapshot(existing), route_snapshot(incoming)
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


def collision_message(collision: Mapping[str, object]) -> str:
    goal_id = collision.get("goal_id") or "<unknown>"
    fields = ", ".join(_str_items(collision.get("changed_fields")))
    return (
        f"global route collision for goal_id {goal_id}: {fields} would change. "
        "Use the existing source_registry to register agents, choose a new --fork-goal id, "
        "or rerun with --replace-state to write a global registry backup and replace the route."
    )


def preserve_attention_override(
    existing: Mapping[str, object], incoming: Mapping[str, object]
) -> dict[str, object]:
    merged = {
        key: value for key, value in incoming.items() if key != "clear_attention_override"
    }
    if incoming.get("clear_attention_override") or same_source_registry(existing, incoming):
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
    existing: Sequence[object],
    incoming: Sequence[Mapping[str, object]],
    *,
    allow_route_replacement: bool = False,
    unchanged_siblings: Sequence[Mapping[str, object]] | None = None,
) -> tuple[list[object], list[str], list[str], list[dict[str, object]]]:
    merged: list[object] = []
    seen_incoming = {str(goal.get("id")) for goal in incoming if goal.get("id")}
    sibling_by_id = _goals_by_id(unchanged_siblings or ())
    existing_by_id = _goals_by_id(existing)
    actions: list[str] = []
    synced_ids: list[str] = []
    collisions: list[dict[str, object]] = []
    resolved_incoming: dict[str, dict[str, object]] = {}
    added_goals: list[dict[str, object]] = []
    incoming_changed = False

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
            merged_goal = preserve_attention_override(existing_goal, goal)
            if "synced_at" in existing_goal and _equal_except_key(
                existing_goal, merged_goal, "synced_at"
            ):
                merged_goal["synced_at"] = existing_goal["synced_at"]
            else:
                incoming_changed = True
            resolved_incoming[goal_id] = merged_goal
        else:
            incoming_changed = True
            added_goals.append(goal if isinstance(goal, dict) else dict(goal))
        actions.append(f"{goal_id}:{action}")
        synced_ids.append(goal_id)

    for item in existing:
        if isinstance(item, dict):
            goal_id = str(item.get("id") or "")
            if str(item.get("id")) in seen_incoming:
                if goal_id in resolved_incoming:
                    merged.append(resolved_incoming.pop(goal_id))
                continue
            sibling = sibling_by_id.get(goal_id)
            if (
                sibling is not None
                and (incoming_changed or not item.get("synced_at"))
                and same_source_registry(item, sibling)
                and preserve_attention_override({}, {**item, "synced_at": ""})
                == preserve_attention_override(item, {**sibling, "synced_at": ""})
            ):
                item = {**item, "synced_at": sibling["synced_at"]}
        merged.append(item)

    merged.extend(added_goals)
    return merged, actions, synced_ids, collisions


def merge_project_entries(
    existing: Sequence[object],
    incoming: Sequence[Mapping[str, object]],
) -> list[object]:
    incoming_by_id = _project_entries_by_id(incoming, source="source registry")
    existing_by_id = _project_entries_by_id(existing, source="global registry")
    for project_id, project in incoming_by_id.items():
        existing_project = existing_by_id.get(project_id)
        if existing_project is not None and existing_project != project:
            raise ValueError(
                f"global ProjectRecord conflicts with source registry: {project_id}"
            )
    merged: list[object] = []
    seen_existing: set[str] = set()
    for item in existing:
        if isinstance(item, dict):
            project_id = str(item.get("project_id") or "")
            if project_id in incoming_by_id:
                merged.append(incoming_by_id[project_id])
                seen_existing.add(project_id)
                continue
        merged.append(item)
    merged.extend(
        project
        for project_id, project in incoming_by_id.items()
        if project_id not in seen_existing
    )
    return merged


def _project_entries_by_id(
    entries: Sequence[object], *, source: str
) -> dict[str, dict[str, object]]:
    projects: dict[str, dict[str, object]] = {}
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


def _sync_global_registry_reduction(
    current: Mapping[str, object],
    incoming: Sequence[Mapping[str, object]],
    incoming_projects: Sequence[Mapping[str, object]],
    *,
    allow_route_replacement: bool,
    schema_version_fallback: str,
    runtime_root: Path,
    synced_at: str,
    unchanged_siblings: Sequence[Mapping[str, object]] | None = None,
) -> GlobalRegistryReduction:
    """Reduce one global-registry snapshot with sanitized incoming goals."""

    existing_goals = current.get("goals")
    merged_goals, actions, synced_ids, collisions = merge_goal_entries(
        existing_goals if isinstance(existing_goals, list) else [],
        incoming,
        allow_route_replacement=allow_route_replacement,
        unchanged_siblings=unchanged_siblings,
    )
    merged_projects = merge_project_entries(
        _registry_projects(current, source="global registry"), incoming_projects
    )
    payload = dict(current)
    payload["schema_version"] = str(
        payload.get("schema_version") or schema_version_fallback or "0.1"
    )
    payload["updated_at"] = synced_at
    payload["common_runtime_root"] = str(runtime_root or select_default_runtime_root())
    payload["registry_role"] = "global-local"
    if merged_projects:
        payload["projects"] = merged_projects
    payload["goals"] = merged_goals
    if "updated_at" in current and _equal_except_key(payload, current, "updated_at"):
        payload["updated_at"] = current["updated_at"]
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
    goal: Mapping[str, object], field: str, *, global_path: Path
) -> Path | None:
    value = goal.get(field)
    if not value:
        return None
    try:
        path = Path(str(value)).expanduser()
    except (OSError, RuntimeError):
        # An alias that no longer expands cannot name a live path; the record
        # keeps the raw value so a later reader sees what was declared.
        return None
    if path.is_absolute():
        return path
    repo = goal.get("repo")
    return (Path(str(repo)).expanduser() if repo else global_path.parent) / path


def _retire_closeout_payload(
    *,
    runtime_root: Path,
    retired_goal: Mapping[str, object],
    global_path: Path,
) -> dict[str, object]:
    """Run the retire hook after the committed removal, best effort only.

    The Goal is already gone from the registry, so nothing here may raise past
    the retirement receipt. With no command configured the record's declared
    path is never resolved, and one unusable record cannot stop the others.
    """

    from .lifecycle.goal_closeout import (
        GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
        goal_closeout_configuration,
        run_goal_closeout,
    )

    goal_id = str(retired_goal.get("id") or "").strip()
    unconfigured = {
        "schema_version": GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
        "goal_id": goal_id,
        "trigger": "retire-global-goal",
        "terminal_reason": "retired",
        "status": "not_configured",
        "ledger_recorded": False,
    }
    try:
        try:
            validate_goal_id_path_segment(goal_id)
        except ValueError:
            # Let the shared executor report the invalid id as it always has.
            return run_goal_closeout(
                runtime_root=runtime_root,
                goal_id=goal_id,
                repo=None,
                state_file=None,
                terminal_reason="retired",
                trigger="retire-global-goal",
            )
        configuration, configuration_error = goal_closeout_configuration(runtime_root)
        if configuration_error is None and not (configuration or {}).get("command"):
            return unconfigured
        state_file = _global_goal_path(
            retired_goal,
            "state_file",
            global_path=global_path,
        )
        if state_file is None:
            state_file = runtime_root / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        goal_runtime_dir = runtime_root / "goals" / goal_id
        repo = retired_goal.get("repo")
        return run_goal_closeout(
            runtime_root=runtime_root,
            goal_id=goal_id,
            repo=str(repo) if repo else None,
            state_file=str(state_file),
            terminal_reason="retired",
            trigger="retire-global-goal",
            ledger_dir=(
                goal_runtime_dir
                if goal_runtime_dir.is_dir()
                else runtime_root / "closeout" / goal_id
            ),
        )
    except Exception as exc:  # the terminal command already finished
        return {
            **unconfigured,
            "status": "preparation_error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _retire_global_registry_reduction(
    current: Mapping[str, object],
    *,
    requested_ids: Sequence[str],
    global_path: Path,
    updated_at: str,
) -> GlobalRegistryReduction:
    raw_goals = current.get("goals")
    current_goals = raw_goals if isinstance(raw_goals, list) else []
    matches_by_id: dict[str, list[dict[str, object]]] = {
        goal_id: [
            goal
            for goal in current_goals
            if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id
        ]
        for goal_id in requested_ids
    }
    missing_ids = [gid for gid, matches in matches_by_id.items() if not matches]
    duplicate_ids = [gid for gid, matches in matches_by_id.items() if len(matches) > 1]
    if missing_ids:
        raise ValueError(f"goal_id not found in global registry: {', '.join(missing_ids)}")
    if duplicate_ids:
        raise ValueError(
            "global registry contains duplicate goal ids; deduplicate before retirement: "
            + ", ".join(duplicate_ids)
        )

    inspections: list[dict[str, object]] = []
    blocked_ids: list[str] = []
    for goal_id in requested_ids:
        goal = matches_by_id[goal_id][0]
        source_path = _global_goal_path(goal, "source_registry", global_path=global_path)
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
            "retired_goals": [
                dict(matches_by_id[goal_id][0]) for goal_id in requested_ids
            ],
        },
        backup_label="retire-goal-backup",
    )


def retire_global_registry_goals(
    *,
    runtime_root_override: str | None,
    goal_ids: Sequence[str],
    execute: bool,
) -> dict[str, object]:
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

    def reduce_retire(current: Mapping[str, object]) -> GlobalRegistryReduction:
        return _retire_global_registry_reduction(
            current, requested_ids=requested_ids, global_path=global_path, updated_at=updated_at
        )

    receipt = reduce_retire(_load_global_registry(global_path)).receipt
    backup_path: str | None = str(
        _global_registry_backup_path(global_path, "retire-goal-backup")
    )
    writability: dict[str, object] = {}

    def result_payload(ok: bool, **extra: object) -> dict[str, object]:
        return {
            "ok": ok,
            "schema_version": "loopx_global_goal_retirement_v0",
            "dry_run": False if not ok else not execute,
            "execute": execute,
            "global_registry": str(global_path),
            "runtime_root": str(runtime_root),
            "requested_goal_ids": requested_ids,
            "retired_goal_ids": requested_ids if (ok and execute) else [],
            "inspections": receipt["inspections"],
            "backup_path": backup_path if ok else None,
            "backup_written": bool(backup_path) if (ok and execute) else False,
            "wrote": execute if ok else False,
            "global_registry_writability": writability,
            **extra,
        }

    if execute:
        writability = probe_registry_write_path(global_path, create_parent=True)
        if not writability.get("ok"):
            return result_payload(
                False,
                error=str(writability.get("error") or "global registry is not writable"),
                recommended_action=writability.get("recommended_action"),
            )

        canonical_runtime_root = global_path.expanduser().resolve().parent
        with ExitStack() as stack:
            for goal_id in sorted(requested_ids):
                stack.enter_context(
                    exclusive_cross_runtime_file_lock(
                        shadow_maintenance_lock_target(canonical_runtime_root, goal_id),
                        operation="retire_global_registry_goal_canonical",
                        timeout_seconds=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
                    )
                )
            mutation = mutate_global_registry(
                global_path, "retire_global_registry_goals", reduce_retire
            )
        receipt = mutation["receipt"]
        backup_path = mutation["backup_path"]

    closeouts: list[dict[str, object]] = []
    if execute:
        for retired_goal in _dict_items(receipt.get("retired_goals")):
            if not str(retired_goal.get("id") or "").strip():
                continue
            # The removal is committed. Each record prepares its own inputs, so
            # one unusable path stays a per-record diagnostic.
            closeouts.append(
                _retire_closeout_payload(
                    runtime_root=runtime_root,
                    retired_goal=retired_goal,
                    global_path=global_path,
                )
            )

    return result_payload(
        True,
        planned_retired_goal_ids=requested_ids,
        global_goal_count_before=receipt["global_goal_count_before"],
        global_goal_count_after=receipt["global_goal_count_after"],
        updated_at=updated_at,
        closeouts=closeouts,
    )


def render_global_goal_retirement_markdown(payload: Mapping[str, object]) -> str:
    lines = [
        "# LoopX Global Goal Retirement",
        "",
        *[
            f"- {key}: `{payload.get(key)}`"
            for key in ("ok", "dry_run", "global_registry", "wrote", "backup_path")
        ],
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    if planned := _str_items(payload.get("planned_retired_goal_ids")):
        lines.extend(["", "## Explicit Goals", *(f"- `{goal_id}`" for goal_id in planned)])
    return "\n".join(lines)


def _load_sync_route(
    registry_path: Path, runtime_root_override: str | None
) -> tuple[Path, dict[str, object], Path, Path]:
    source = registry_path.expanduser()
    if not source.exists():
        raise FileNotFoundError(f"registry file does not exist: {source}")
    payload = load_registry(source)
    runtime_root = resolve_runtime_root(payload, runtime_root_override)
    return source, payload, runtime_root, global_registry_path(runtime_root)


def _unpack_sync_receipt(
    receipt: Mapping[str, object],
) -> tuple[list[object], list[str], list[str], list[dict[str, object]]]:
    return (
        _list_items(receipt.get("merged_goals")),
        _str_items(receipt.get("actions")),
        _str_items(receipt.get("synced_ids")),
        _dict_items(receipt.get("collisions")),
    )


def _sync_project_registry_to_global_once(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None = None,
    dry_run: bool = False,
    allow_route_replacement: bool = False,
    _global_registry_lock_held: bool = False,
    _expected_global_registry: Path | None = None,
    _lock_write_denied: OSError | None = None,
) -> dict[str, object]:
    registry_path, project_registry, runtime_root, global_path = _load_sync_route(
        registry_path, runtime_root_override
    )
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

    all_source_goals = registry_goals(project_registry)
    goals = (
        [goal for goal in all_source_goals if str(goal.get("id")) == goal_id]
        if goal_id
        else all_source_goals
    )
    if goal_id and not goals:
        raise ValueError(f"goal id not found in source registry: {goal_id}")

    synced_at = now_local()
    incoming = [
        sanitize_goal_for_global(goal, source_registry=registry_path, synced_at=synced_at)
        for goal in goals
    ]
    unchanged_siblings = (
        [
            sanitize_goal_for_global(
                sibling, source_registry=registry_path, synced_at=synced_at
            )
            for sibling in all_source_goals
            if str(sibling.get("id")) != goal_id
        ]
        if goal_id
        else []
    )
    project_ids = {
        str(goal.get("project_id"))
        for goal in goals
        if str(goal.get("project_id") or "").strip()
    }
    projects_by_id = _project_entries_by_id(
        _registry_projects(project_registry, source="source registry"),
        source="source registry",
    )
    missing_project_ids = sorted(project_ids - projects_by_id.keys())
    if missing_project_ids:
        raise ValueError(
            "goal references a missing ProjectRecord: " + ", ".join(missing_project_ids)
        )
    incoming_projects = [projects_by_id[project_id] for project_id in sorted(project_ids)]
    schema_version_fallback = str(project_registry.get("schema_version") or "0.1")

    def reduce_current(current: Mapping[str, object]) -> GlobalRegistryReduction:
        return _sync_global_registry_reduction(
            current,
            incoming,
            incoming_projects,
            allow_route_replacement=allow_route_replacement,
            schema_version_fallback=schema_version_fallback,
            runtime_root=runtime_root,
            synced_at=synced_at,
            unchanged_siblings=unchanged_siblings,
        )

    merged_goals, actions, synced_ids, collisions = _unpack_sync_receipt(
        reduce_current(_load_global_registry(global_path)).receipt
    )
    backup_path: str | None = None
    writability: dict[str, object] | None = None

    def write_denied(exc: BaseException) -> dict[str, object]:
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

    if not dry_run:
        writability = probe_registry_write_path(global_path, create_parent=True)
        if _lock_write_denied is not None or not writability.get("ok"):
            raw_errno = writability.get("errno")
            exc = _lock_write_denied or PermissionError(
                raw_errno if isinstance(raw_errno, int) else errno.EPERM,
                str(writability.get("error") or "global registry is not writable"),
                str(global_path),
            )
            return write_denied(exc)

    try:
        if dry_run:
            if collisions and allow_route_replacement:
                backup_path = str(
                    _global_registry_backup_path(global_path, "route-collision-backup")
                )
        else:
            mutation = (
                _mutate_global_registry_locked(global_path, reduce_current)
                if _global_registry_lock_held
                else mutate_global_registry(
                    global_path, "sync_global_registry", reduce_current
                )
            )
            merged_goals, actions, synced_ids, collisions = _unpack_sync_receipt(
                mutation["receipt"]
            )
            backup_path = mutation["backup_path"]
    except OSError as exc:
        if not is_write_denied_error(exc):
            raise
        return write_denied(exc)

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
) -> dict[str, object]:
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

    source_registry, _, _, target_registry = _load_sync_route(
        registry_path, runtime_root_override
    )
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
            _global_registry_lock_held=False,
        )
    with ExitStack() as lock:
        lock_write_denied: OSError | None = None
        try:
            lock.enter_context(
                exclusive_cross_runtime_file_lock(
                    target_registry, operation="sync_global_registry"
                )
            )
        except OSError as exc:
            if not is_write_denied_error(exc):
                raise
            # A later writable probe cannot authorize an unlocked retry.
            lock_write_denied = exc
        return _sync_project_registry_to_global_once(
            registry_path=source_registry,
            runtime_root_override=runtime_root_override,
            goal_id=goal_id,
            dry_run=False,
            allow_route_replacement=allow_route_replacement,
            _global_registry_lock_held=lock_write_denied is None,
            _expected_global_registry=target_registry,
            _lock_write_denied=lock_write_denied,
        )


def render_global_sync_markdown(payload: Mapping[str, object]) -> str:
    lines = [
        "# LoopX Global Registry Sync",
        "",
        *[
            f"- {key}: `{payload.get(key)}`"
            for key in (
                "ok", "dry_run", "skipped", "registry", "global_registry",
                "runtime_root", "source_goal_count", "global_goal_count",
            )
        ],
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        if payload.get("write_denied"):
            lines.extend(
                f"- {key}: `{payload.get(key)}`"
                for key in ("error_kind", "fallback_registry", "project_registry_usable")
            )
            if payload.get("recommended_action"):
                lines.append(f"- recommended_action: {payload.get('recommended_action')}")
        return "\n".join(lines)
    if payload.get("reason"):
        lines.append(f"- reason: {payload.get('reason')}")
    if payload.get("backup_path"):
        lines.append(f"- backup_path: `{payload.get('backup_path')}`")
    if collisions := _dict_items(payload.get("route_collisions")):
        lines.extend(["", "## Route Replacements", *(f"- {collision_message(c)}" for c in collisions)])
    if synced := _str_items(payload.get("synced_goal_ids")):
        lines.extend(["", "## Synced Goals", *(f"- `{goal_id}`" for goal_id in synced)])
    if actions := _str_items(payload.get("actions")):
        lines.extend(["", "## Actions", *(f"- {action}" for action in actions)])
    return "\n".join(lines)
