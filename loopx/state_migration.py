from __future__ import annotations

import copy
from contextlib import ExitStack
import json
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import find_registry_goal
from .file_lock import exclusive_cross_runtime_file_lock
from .paths import resolve_runtime_root
from .control_plane.projects.registry_codec import (
    ProjectRegistryTransaction,
    load_project_registry,
    project_registry_transaction,
    require_runtime_compatible_project_registry,
)
from .control_plane.runtime.document_io import atomic_write_state_text
from .control_plane.coordination.legacy_writer_fence import legacy_coordination_todo_lock_path, require_legacy_state_replacement_allowed
from .control_plane.work_items.task_lease import task_lease_lock_path
from .registry import registry_goals
from .control_plane.coordination.coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA as AUTHORITY_SHADOW_CONFIG_SCHEMA,
)


LEGACY_RUNTIME_ROOT = Path.home() / ".codex" / "goal-harness"
LEGACY_GLOBAL_REGISTRY = LEGACY_RUNTIME_ROOT / "registry.global.json"
MIGRATION_SHADOW_SEED_EVIDENCE_SCHEMA = "loopx_state_migration_shadow_seed_evidence_v0"


def now_local() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def parse_key_value_map(items: list[str] | None, *, flag_name: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{flag_name} expects OLD=NEW, got: {item}")
        old, new = item.split("=", 1)
        old = old.strip()
        new = new.strip()
        if not old or not new:
            raise ValueError(f"{flag_name} expects non-empty OLD=NEW, got: {item}")
        mapping[old] = new
    return mapping


def read_json_object(path: Path) -> dict[str, Any]:
    with path.expanduser().open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def legacy_registry_goal_ids(path: Path) -> list[str]:
    registry = read_json_object(path.expanduser())
    return [str(goal.get("id")) for goal in registry_goals(registry) if goal.get("id")]


def rewrite_text(text: str, *, goal_id_map: dict[str, str], path_map: dict[str, str]) -> str:
    rewritten = text
    replacements: list[tuple[str, str]] = []
    replacements.extend(sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True))
    replacements.extend(sorted(goal_id_map.items(), key=lambda item: len(item[0]), reverse=True))
    replacements.extend(
        [
            (".goal-harness", ".loopx"),
            ("Goal Harness", "LoopX"),
            ("goal-harness", "loopx"),
            ("GOAL_HARNESS", "LOOPX"),
            ("goal_harness", "loopx"),
        ]
    )
    for old, new in replacements:
        rewritten = rewritten.replace(old, new)
    return rewritten


def rewrite_value(value: Any, *, goal_id_map: dict[str, str], path_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return rewrite_text(value, goal_id_map=goal_id_map, path_map=path_map)
    if isinstance(value, list):
        return [rewrite_value(item, goal_id_map=goal_id_map, path_map=path_map) for item in value]
    if isinstance(value, dict):
        return {
            str(key): rewrite_value(item, goal_id_map=goal_id_map, path_map=path_map)
            for key, item in value.items()
        }
    return value


def merge_goals(existing: list[Any], incoming: list[dict[str, Any]]) -> list[Any]:
    incoming_ids = {str(goal.get("id")) for goal in incoming if goal.get("id")}
    merged = [
        item
        for item in existing
        if not (isinstance(item, dict) and str(item.get("id")) in incoming_ids)
    ]
    merged.extend(incoming)
    return merged


def project_local_goal(goal: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(goal)
    for field in (
        "source_registry",
        "synced_at",
        "authority_source_count",
        "attention_override_synced_from",
    ):
        copied.pop(field, None)
    return copied


def resolve_goal_state(repo: str | None, state_file: str | None) -> Path | None:
    if not repo or not state_file:
        return None
    state_path = Path(state_file).expanduser()
    return state_path if state_path.is_absolute() else Path(repo).expanduser() / state_path


def copy_rewritten_text_file(source: Path, target: Path, *, goal_id_map: dict[str, str], path_map: dict[str, str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        shutil.copy2(source, target)
        return
    atomic_write_state_text(target, rewrite_text(text, goal_id_map=goal_id_map, path_map=path_map))


def copy_active_state_files(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    goal_id_map: dict[str, str],
    path_map: dict[str, str],
    execute: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_goal, target_goal in pairs:
        source_path = resolve_goal_state(source_goal.get("repo"), source_goal.get("state_file"))
        target_path = resolve_goal_state(target_goal.get("repo"), target_goal.get("state_file"))
        row: dict[str, Any] = {
            "goal_id": target_goal.get("id"),
            "source": str(source_path) if source_path else None,
            "target": str(target_path) if target_path else None,
            "copied": False,
        }
        if not source_path or not target_path:
            row["skipped_reason"] = "missing_state_file"
        elif not source_path.exists():
            row["skipped_reason"] = "source_state_missing"
        elif execute:
            copy_rewritten_text_file(
                source_path,
                target_path,
                goal_id_map=goal_id_map,
                path_map=path_map,
            )
            row["copied"] = True
        results.append(row)
    return results


def should_rewrite_runtime_file(path: Path) -> bool:
    return path.suffix.lower() in {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}


def copy_runtime_goal_dirs(
    *,
    legacy_runtime_root: Path,
    target_runtime_root: Path,
    goal_id_map: dict[str, str],
    selected_old_goal_ids: list[str],
    path_map: dict[str, str],
    execute: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for old_goal_id in selected_old_goal_ids:
        new_goal_id = goal_id_map.get(old_goal_id, old_goal_id)
        source_dir = legacy_runtime_root.expanduser() / "goals" / old_goal_id
        target_dir = target_runtime_root.expanduser() / "goals" / new_goal_id
        row: dict[str, Any] = {
            "source": str(source_dir),
            "target": str(target_dir),
            "goal_id": new_goal_id,
            "copied": False,
            "copied_file_count": 0,
            "skipped_existing_file_count": 0,
        }
        if not source_dir.exists():
            row["skipped_reason"] = "source_runtime_goal_missing"
        elif execute:
            for source_path in source_dir.rglob("*"):
                rel = source_path.relative_to(source_dir)
                target_path = target_dir / rel
                if source_path.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                elif target_path.exists():
                    row["skipped_existing_file_count"] += 1
                elif should_rewrite_runtime_file(source_path):
                    copy_rewritten_text_file(
                        source_path,
                        target_path,
                        goal_id_map=goal_id_map,
                        path_map=path_map,
                    )
                    row["copied_file_count"] += 1
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target_path)
                    row["copied_file_count"] += 1
            row["copied"] = True
        results.append(row)
    return results


def _uses_file_authority_shadow(goal: dict[str, Any]) -> bool:
    coordination = goal.get("coordination")
    if not isinstance(coordination, dict):
        return False
    config = coordination.get("authority_shadow")
    return (
        isinstance(config, dict)
        and config.get("schema_version") == AUTHORITY_SHADOW_CONFIG_SCHEMA
        and config.get("mode") == "file_one_way"
    )


def retired_authority_shadow_notices(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the migration response field, but never seed the retired observer."""
    return [{"schema_version": MIGRATION_SHADOW_SEED_EVIDENCE_SCHEMA,
             "goal_id": str(goal.get("id") or ""), "attempted": False,
             "outcome": "retired", "reason_code": "local_authority_shadow_retired"}
            for goal in goals if _uses_file_authority_shadow(goal)]


def migrate_legacy_state(
    *,
    legacy_registry_path: Path,
    target_registry_path: Path,
    legacy_runtime_root: Path,
    target_runtime_root: Path,
    goal_ids: list[str],
    goal_id_map: dict[str, str],
    path_map: dict[str, str],
    copy_active_state: bool,
    copy_runtime: bool,
    execute: bool,
) -> dict[str, Any]:
    legacy_registry_path = legacy_registry_path.expanduser()
    target_registry_path = target_registry_path.expanduser()
    legacy_runtime_root = legacy_runtime_root.expanduser()
    target_runtime_root = target_runtime_root.expanduser()

    if not goal_ids:
        raise ValueError("at least one --goal-id is required for explicit migration")
    if not legacy_registry_path.exists():
        raise FileNotFoundError(f"legacy registry does not exist: {legacy_registry_path}")

    with ExitStack() as locks:
        target_transaction: ProjectRegistryTransaction | None = None
        if execute:
            for path in sorted({legacy_registry_path.resolve(), target_registry_path.resolve()}, key=str):
                if path == target_registry_path.resolve():
                    target_transaction = locks.enter_context(
                        project_registry_transaction(
                            path,
                            operation="state_migration_registry",
                            create=dict,
                        )
                    )
                else:
                    locks.enter_context(
                        exclusive_cross_runtime_file_lock(
                            path,
                            operation="state_migration_registry",
                        )
                    )
        source_registry = (
            target_transaction.payload_copy()
            if target_transaction is not None
            and legacy_registry_path.resolve() == target_registry_path.resolve()
            else read_json_object(legacy_registry_path)
        )
        source_by_id = {str(goal.get("id")): goal for goal in registry_goals(source_registry)}
        missing = [goal_id for goal_id in goal_ids if goal_id not in source_by_id]
        if missing:
            raise ValueError(f"goal id not found in legacy registry: {', '.join(missing)}")

        selected_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for old_goal_id in goal_ids:
            source_goal = source_by_id[old_goal_id]
            migrated = rewrite_value(source_goal, goal_id_map=goal_id_map, path_map=path_map)
            if not isinstance(migrated, dict):
                raise ValueError(f"migrated goal is not an object: {old_goal_id}")
            migrated["id"] = goal_id_map.get(old_goal_id, str(migrated.get("id") or old_goal_id))
            selected_pairs.append((source_goal, project_local_goal(migrated)))

        source_by_target_id: dict[str, str] = {}
        for source_goal, target_goal in selected_pairs:
            source_id = str(source_goal["id"])
            target_id = str(target_goal["id"])
            prior_source_id = source_by_target_id.get(target_id)
            if prior_source_id is not None:
                raise ValueError(
                    f"{prior_source_id} and {source_id} map to the same target "
                    f"goal id: {target_id}"
                )
            source_by_target_id[target_id] = source_id

        existing_registry = (
            target_transaction.payload_copy()
            if target_transaction is not None
            else load_project_registry(target_registry_path)
            if target_registry_path.exists()
            else {}
        )
        require_runtime_compatible_project_registry(
            existing_registry,
            operation="state migration",
        )
        existing_goals = existing_registry.get("goals")
        if not isinstance(existing_goals, list):
            existing_goals = []
        incoming_goals = [target for _, target in selected_pairs]
        target_payload = dict(existing_registry)
        target_payload["schema_version"] = str(target_payload.get("schema_version") or source_registry.get("schema_version") or "0.1")
        target_payload["updated_at"] = now_local()
        target_payload["common_runtime_root"] = str(target_runtime_root)
        target_payload.pop("registry_role", None)
        target_payload["goals"] = merge_goals(existing_goals, incoming_goals)

        if execute:
            endpoints: dict[tuple[str, str], tuple[Path, str, dict[str, Any] | None, Path | None]] = {}
            for source_goal, target_goal in selected_pairs:
                old_id, new_id = str(source_goal["id"]), str(target_goal["id"])
                endpoints[(str(legacy_runtime_root.resolve()), old_id)] = (legacy_runtime_root, old_id, source_goal, resolve_goal_state(source_goal.get("repo"), source_goal.get("state_file")))
                endpoints[(str(target_runtime_root.resolve()), new_id)] = (target_runtime_root, new_id, find_registry_goal(existing_registry, new_id), resolve_goal_state(target_goal.get("repo"), target_goal.get("state_file")))
            ordered = [endpoints[key] for key in sorted(endpoints)]
            for root, identity, _goal, _state in ordered:
                locks.enter_context(exclusive_cross_runtime_file_lock(legacy_coordination_todo_lock_path(runtime_root=root, goal_id=identity), operation="state_migration_todo"))
            for state_path in sorted({entry[3].resolve() for entry in ordered if entry[3] is not None}, key=str):
                locks.enter_context(exclusive_cross_runtime_file_lock(state_path, operation="state_migration_state"))
            if copy_runtime:
                for root, identity, _goal, _state in ordered:
                    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
                    target = root / "authority-transition" / "file-v0" / f"legacy-task-lease-writer-{digest}"
                    locks.enter_context(exclusive_cross_runtime_file_lock(target, operation="state_migration_leases"))
                for root, identity, _goal, _state in ordered:
                    locks.enter_context(exclusive_cross_runtime_file_lock(task_lease_lock_path(runtime_root=root, goal_id=identity), operation="state_migration_leases"))
            for root, identity, goal, _state in ordered:
                require_legacy_state_replacement_allowed(runtime_root=root, goal_id=identity, goal=goal)
            previous_root = resolve_runtime_root(existing_registry, None, registry_path=target_registry_path)
            if previous_root != target_runtime_root:
                for previous_goal in registry_goals(existing_registry):
                    require_legacy_state_replacement_allowed(runtime_root=previous_root, goal_id=str(previous_goal["id"]), goal=previous_goal)

        active_state_results = (
            copy_active_state_files(
                selected_pairs,
                goal_id_map=goal_id_map,
                path_map=path_map,
                execute=execute,
            )
            if copy_active_state
            else []
        )
        runtime_results = (
            copy_runtime_goal_dirs(
                legacy_runtime_root=legacy_runtime_root,
                target_runtime_root=target_runtime_root,
                goal_id_map=goal_id_map,
                selected_old_goal_ids=goal_ids,
                path_map=path_map,
                execute=execute,
            )
            if copy_runtime
            else []
        )

        if execute:
            if target_transaction is None:
                raise RuntimeError(
                    "state migration target requires a registry transaction"
                )
            target_transaction.commit(target_payload)

        authority_shadow_seeds = retired_authority_shadow_notices(incoming_goals)

        return {
            "ok": True,
            "schema_version": "loopx_state_migration_v0",
            "dry_run": not execute,
            "execute": execute,
            "legacy_registry": str(legacy_registry_path),
            "target_registry": str(target_registry_path),
            "legacy_runtime_root": str(legacy_runtime_root),
            "target_runtime_root": str(target_runtime_root),
            "selected_goal_ids": goal_ids,
            "migrated_goal_ids": [goal.get("id") for goal in incoming_goals],
            "goal_id_map": goal_id_map,
            "path_map": path_map,
            "wrote_project_registry": execute,
            "project_registry_goal_count": len(target_payload.get("goals", [])),
            "active_state": active_state_results,
            "runtime_goals": runtime_results,
            "authority_shadow_seeds": authority_shadow_seeds,
        }


def render_state_migration_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX State Migration",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- legacy_registry: `{payload.get('legacy_registry')}`",
        f"- target_registry: `{payload.get('target_registry')}`",
        f"- legacy_runtime_root: `{payload.get('legacy_runtime_root')}`",
        f"- target_runtime_root: `{payload.get('target_runtime_root')}`",
        f"- selected_goal_ids: `{', '.join(payload.get('selected_goal_ids') or [])}`",
        f"- migrated_goal_ids: `{', '.join(str(item) for item in (payload.get('migrated_goal_ids') or []))}`",
        f"- wrote_project_registry: `{payload.get('wrote_project_registry')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    active_state = payload.get("active_state") or []
    if active_state:
        lines.extend(["", "## Active State"])
        for row in active_state:
            lines.append(
                f"- `{row.get('goal_id')}` copied=`{row.get('copied')}` "
                f"source=`{row.get('source')}` target=`{row.get('target')}`"
            )
            if row.get("skipped_reason"):
                lines.append(f"  - skipped_reason: `{row.get('skipped_reason')}`")
    runtime_goals = payload.get("runtime_goals") or []
    if runtime_goals:
        lines.extend(["", "## Runtime Goals"])
        for row in runtime_goals:
            lines.append(
                f"- `{row.get('goal_id')}` copied=`{row.get('copied')}` "
                f"source=`{row.get('source')}` target=`{row.get('target')}`"
            )
            if row.get("skipped_reason"):
                lines.append(f"  - skipped_reason: `{row.get('skipped_reason')}`")
    authority_shadow_seeds = payload.get("authority_shadow_seeds") or []
    if authority_shadow_seeds:
        lines.extend(["", "## Authority Shadow Seeds"])
        for row in authority_shadow_seeds:
            lines.append(
                f"- `{row.get('goal_id')}` outcome=`{row.get('outcome')}` "
                f"attempted=`{row.get('attempted')}`"
            )
            if row.get("reason_code"):
                lines.append(f"  - reason_code: `{row.get('reason_code')}`")
    global_sync = payload.get("global_sync")
    if isinstance(global_sync, dict):
        lines.extend(["", "## Global Sync"])
        lines.append(f"- ok: `{global_sync.get('ok')}`")
        lines.append(f"- dry_run: `{global_sync.get('dry_run')}`")
        lines.append(f"- wrote: `{global_sync.get('wrote')}`")
        lines.append(f"- synced_goal_ids: `{', '.join(global_sync.get('synced_goal_ids') or [])}`")
        if global_sync.get("error"):
            lines.append(f"- error: {global_sync.get('error')}")
    return "\n".join(lines)
