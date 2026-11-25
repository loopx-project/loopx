"""Lark Kanban projection reconciliation and LoopX Todo synchronization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from ....presentation.projection_source_reconcile import (
    plan_projection_source_reconcile,
    validate_projection_source_reconcile_request,
)
from .. import cli_transport as cli_transport
from . import kanban_board as kanban_board
from .kanban_board import _extract_created_record_id, _schema_check_error
from .kanban_config import (
    LARK_KANBAN_SYNC_PROJECTION_VERSION,
    LarkKanbanConfig,
    _validate_sink_visibility,
    build_record_upsert_command,
)
from .kanban_records import (
    _collect_goal_todo_items,
    _json_dict,
    _load_lark_todo_record_map,
    _persist_lark_todo_record_map,
    _projection_rows_api,
    _projection_upsert_items,
)
from .record_io import build_record_delete_command
from .sink_visibility import SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITY_SHARED


class LarkKanbanTodoSyncResult(TypedDict):
    ok: bool
    schema_version: str
    execute: bool
    goal_id: str
    agent_id: str | None
    sink_visibility: str
    public_safe_redaction: bool
    base_token: str
    table_id: str
    view_id: str | None
    project: str | None
    state_file: str
    todo_count: int
    issue_fix_outcome_count: int
    limit_policy: dict[str, object]
    issue_fix_source_counts: object
    warnings: list[str]
    config_path: str | None
    schema_check: dict[str, object] | None
    records: list[dict[str, object]]
    commands: list[dict[str, object]]
    successful_write_count: int
    external_write_performed: bool
    error: NotRequired[str]


class _SchemaInspectorAdapter(Protocol):
    def _inspect_lark_kanban_schema(
        self,
        config: LarkKanbanConfig,
        *,
        commands: list[dict[str, object]],
        runner: cli_transport.CommandRunner,
    ) -> dict[str, object]: ...


def _inspect_lark_kanban_schema_typed(
    config: LarkKanbanConfig,
    *,
    commands: list[dict[str, object]],
    runner: cli_transport.CommandRunner,
) -> dict[str, object]:
    board_api = cast(_SchemaInspectorAdapter, kanban_board)
    return board_api._inspect_lark_kanban_schema(
        config, commands=commands, runner=runner
    )


def _upsert_record_batch(
    config: LarkKanbanConfig,
    items: Sequence[tuple[str, str, Mapping[str, object]]],
    *,
    record_map: dict[str, str],
    commands: list[dict[str, object]],
    results: list[dict[str, object]],
    execute: bool,
    runner: cli_transport.CommandRunner,
) -> tuple[bool, int]:
    ok = True
    successful_write_count = 0
    for key, todo_id, values in items:
        result = _json_dict(
            cli_transport.run_command(
                build_record_upsert_command(config, record_id=record_map.get(key), values=values),
                execute=execute,
                runner=runner,
            )
        )
        commands.append(result)
        record_id = _extract_created_record_id(result.get("json")) or record_map.get(key)
        if execute and result.get("executed") and result.get("ok"):
            successful_write_count += 1
        if execute and result.get("ok") and record_id:
            record_map[key] = record_id
        results.append(
            {"todo_id": todo_id, "record_id": record_id, "command": result, "values": dict(values)}
        )
        ok = ok and bool(result.get("ok"))
        if execute and not result.get("ok"):
            break
    return ok, successful_write_count


def sync_loopx_projection_to_lark_kanban(
    config: LarkKanbanConfig,
    *,
    projection: Mapping[str, object],
    goal_id: str | None = None,
    agent_id: str | None = None,
    source_id: str | None = None,
    config_path: Path | None = None,
    include_done: bool = False,
    limit: int = 50,
    sink_visibility: str = SINK_VISIBILITY_OWNER_ONLY,
    reconcile_source: bool = False,
    source_snapshot_complete: bool = False,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, object]:
    if not isinstance(projection, dict):
        raise ValueError("projection must be a JSON object")
    rows_api = _projection_rows_api()
    public_safe = _validate_sink_visibility(sink_visibility)
    resolved_source_id, namespace_warnings = rows_api.projection_namespace(projection, source_id)
    resolved_goal_id, rows, warnings = rows_api.projection_rows_from_payload(
        projection,
        goal_id=goal_id,
        agent_id=agent_id,
        source_id=resolved_source_id,
        include_done=include_done,
        limit=limit,
    )
    warnings = [*namespace_warnings, *warnings]

    complete_rows = rows
    complete_warnings: list[str] = []
    if reconcile_source:
        _, complete_rows, complete_warnings = rows_api.projection_rows_from_payload(
            projection,
            goal_id=goal_id,
            agent_id=None,
            source_id=resolved_source_id,
            include_done=True,
            limit=2**31 - 1,
        )
    validate_projection_source_reconcile_request(
        requested=reconcile_source,
        source_snapshot_complete=source_snapshot_complete,
        agent_id=agent_id,
        include_done=include_done,
        namespace_warnings=namespace_warnings,
        selected_row_count=len(rows),
        complete_row_count=len(complete_rows),
        complete_warnings=complete_warnings,
    )

    commands: list[dict[str, object]] = []
    schema_check: dict[str, object] | None = None
    base_payload: dict[str, object] = {
        "schema_version": LARK_KANBAN_SYNC_PROJECTION_VERSION,
        "execute": execute,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "source_id": resolved_source_id,
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "projection_schema_version": projection.get("schema_version"),
        "row_count": len(rows),
        "warnings": warnings,
        "config_path": str(config_path) if config_path else None,
    }
    if execute:
        schema_check = _inspect_lark_kanban_schema_typed(config, commands=commands, runner=runner)
        if not schema_check.get("ok"):
            return {
                **base_payload,
                "ok": False,
                "records": [],
                "commands": commands,
                "schema_check": schema_check,
                "error": _schema_check_error(schema_check),
            }

    local, record_map, remote_records, remote_list_complete = _load_lark_todo_record_map(
        config,
        config_path=config_path,
        execute=execute,
        inspect_remote=reconcile_source,
        commands=commands,
        runner=runner,
    )
    if reconcile_source and not remote_list_complete:
        raise ValueError("source reconcile requires a complete remote record list")

    desired_keys = {
        f"{str(row.get('goal_id') or resolved_goal_id)}:{str(row.get('todo_id') or '').strip()}"
        for row in rows
    }
    reconcile_plan = (
        plan_projection_source_reconcile(
            goal_id=resolved_goal_id,
            source_id=resolved_source_id,
            desired_keys=sorted(desired_keys),
            local_record_map=record_map,
            remote_records=remote_records,
        )
        if reconcile_source
        else {
            "schema_version": "projection_source_reconcile_plan_v0",
            "goal_id": resolved_goal_id,
            "source_id": resolved_source_id,
            "remote_orphan_record_ids": [],
            "remote_duplicate_record_ids": [],
            "remote_delete_record_ids": [],
            "local_mapping_keys_to_remove": [],
            "remote_delete_count": 0,
            "local_mapping_delete_count": 0,
        }
    )

    results: list[dict[str, object]] = []
    ok, _ = _upsert_record_batch(
        config,
        _projection_upsert_items(
            rows, default_goal_id=resolved_goal_id, source_id=resolved_source_id, public_safe=public_safe
        ),
        record_map=record_map,
        commands=commands,
        results=results,
        execute=execute,
        runner=runner,
    )

    executed_remote_delete_count = 0
    delete_ids = reconcile_plan.get("remote_delete_record_ids")
    if ok and reconcile_source and isinstance(delete_ids, list) and delete_ids:
        delete_result = _json_dict(
            cli_transport.run_command(
                build_record_delete_command(config, record_ids=[str(item) for item in delete_ids]),
                execute=execute,
                runner=runner,
            )
        )
        commands.append(delete_result)
        ok = ok and bool(delete_result.get("ok"))
        if execute and delete_result.get("ok"):
            raw_delete_count = reconcile_plan.get("remote_delete_count")
            executed_remote_delete_count = raw_delete_count if isinstance(raw_delete_count, int) else 0

    removed_local_mapping_count = 0
    local_keys = reconcile_plan.get("local_mapping_keys_to_remove")
    if execute and ok and reconcile_source and isinstance(local_keys, list):
        for key in local_keys:
            if str(key) in record_map:
                removed_local_mapping_count += 1
                record_map.pop(str(key), None)

    if execute and ok:
        _persist_lark_todo_record_map(config, config_path=config_path, local=local, record_map=record_map)

    return {
        **base_payload,
        "ok": ok,
        "schema_check": schema_check,
        "source_reconcile": {
            **reconcile_plan,
            "requested": reconcile_source,
            "source_snapshot_complete": source_snapshot_complete,
            "mode": "execute" if reconcile_source and execute else ("preview" if reconcile_source else "disabled"),
            "executed_remote_delete_count": executed_remote_delete_count,
            "removed_local_mapping_count": removed_local_mapping_count,
        },
        "records": results,
        "commands": commands,
    }


def sync_loopx_todos_to_lark_kanban(
    config: LarkKanbanConfig,
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None = None,
    config_path: Path | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    include_done: bool = False,
    limit: int = 50,
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> LarkKanbanTodoSyncResult:
    from ....capabilities.issue_fix.outcome_projection import (
        build_issue_fix_outcome_collection_from_domain_state,
    )
    from ....control_plane.todos.path_resolution import resolve_todo_state_path

    rows_api = _projection_rows_api()
    public_safe = _validate_sink_visibility(sink_visibility)
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path, goal_id=goal_id, project=project, state_file=state_file
    )
    lines = resolved_state_file.read_text(encoding="utf-8").splitlines()
    todo_items = _collect_goal_todo_items(
        lines,
        goal_id=goal_id,
        state_file=resolved_state_file,
        agent_id=agent_id,
        include_done=include_done,
        limit=limit,
        public_safe=public_safe,
    )

    outcome_projection = _json_dict(
        build_issue_fix_outcome_collection_from_domain_state(
            goal_id=goal_id, project=resolved_project or project or Path(".")
        )
    )
    outcome_source_id, outcome_namespace_warnings = rows_api.projection_namespace(
        outcome_projection, None
    )
    raw_outcomes = outcome_projection.get("issue_fix_outcomes")
    outcome_source_count = len(raw_outcomes) if isinstance(raw_outcomes, Sequence) else 0
    _, outcome_rows, outcome_row_warnings = rows_api.projection_rows_from_payload(
        outcome_projection,
        goal_id=goal_id,
        agent_id=agent_id,
        source_id=outcome_source_id,
        include_done=True,
        limit=outcome_source_count,
    )
    raw_outcome_warnings = outcome_projection.get("warnings")
    outcome_warnings: list[str] = [
        *outcome_namespace_warnings,
        *(
            [str(item) for item in raw_outcome_warnings]
            if isinstance(raw_outcome_warnings, Sequence)
            and not isinstance(raw_outcome_warnings, (str, bytes))
            else []
        ),
        *outcome_row_warnings,
    ]

    commands: list[dict[str, object]] = []
    schema_check: dict[str, object] | None = None
    project_str = str(resolved_project) if resolved_project else None
    state_file_str = str(resolved_state_file)
    config_path_str = str(config_path) if config_path else None
    limit_policy: dict[str, object] = {
        "todo_rows": limit,
        "issue_fix_outcomes": "complete_source_projection",
    }
    source_counts = outcome_projection.get("source_counts")
    if execute:
        schema_check = _inspect_lark_kanban_schema_typed(config, commands=commands, runner=runner)
        if not schema_check.get("ok"):
            return {
                "ok": False,
                "schema_version": "loopx_lark_kanban_sync_todos_v0",
                "execute": execute,
                "goal_id": goal_id,
                "agent_id": agent_id,
                "sink_visibility": sink_visibility,
                "public_safe_redaction": public_safe,
                "base_token": config.base_token,
                "table_id": config.table_id,
                "view_id": config.view_id,
                "project": project_str,
                "state_file": state_file_str,
                "todo_count": len(todo_items),
                "issue_fix_outcome_count": len(outcome_rows),
                "limit_policy": limit_policy,
                "issue_fix_source_counts": source_counts,
                "warnings": outcome_warnings,
                "config_path": config_path_str,
                "records": [],
                "commands": commands,
                "schema_check": schema_check,
                "successful_write_count": 0,
                "external_write_performed": False,
                "error": _schema_check_error(schema_check),
            }

    local, record_map, _, _ = _load_lark_todo_record_map(
        config,
        config_path=config_path,
        execute=execute,
        inspect_remote=False,
        commands=commands,
        runner=runner,
    )

    results: list[dict[str, object]] = []
    ok, successful_write_count = _upsert_record_batch(
        config,
        todo_items,
        record_map=record_map,
        commands=commands,
        results=results,
        execute=execute,
        runner=runner,
    )
    if ok and outcome_rows:
        ok, outcome_writes = _upsert_record_batch(
            config,
            _projection_upsert_items(
                outcome_rows, default_goal_id=goal_id, source_id=outcome_source_id, public_safe=public_safe
            ),
            record_map=record_map,
            commands=commands,
            results=results,
            execute=execute,
            runner=runner,
        )
        successful_write_count += outcome_writes

    if execute and successful_write_count:
        _persist_lark_todo_record_map(config, config_path=config_path, local=local, record_map=record_map)

    return {
        "ok": ok,
        "schema_version": "loopx_lark_kanban_sync_todos_v0",
        "execute": execute,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "base_token": config.base_token,
        "table_id": config.table_id,
        "view_id": config.view_id,
        "project": project_str,
        "state_file": state_file_str,
        "todo_count": len(todo_items),
        "issue_fix_outcome_count": len(outcome_rows),
        "limit_policy": limit_policy,
        "issue_fix_source_counts": source_counts,
        "warnings": outcome_warnings,
        "config_path": config_path_str,
        "schema_check": schema_check,
        "records": results,
        "commands": commands,
        "successful_write_count": successful_write_count,
        "external_write_performed": bool(successful_write_count),
    }
