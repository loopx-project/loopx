"""Lark Kanban record mapping and local todo-record cache persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from ....control_plane.todos.contract import (
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED,
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_BLOCKER,
    TODO_TASK_CLASS_USER_GATE,
    normalize_explicit_todo_task_class,
    normalize_todo_status,
)
from .. import cli_transport as cli_transport
from . import issue_fix_surface as issue_fix_surface
from . import projection_rows as projection_rows
from .kanban_board import (
    CLAIM_AGENT,
    CLAIM_HUMAN,
    CLAIM_UNCLAIMED,
    STATUS_BLOCKED,
    STATUS_CLAIMED,
    STATUS_DONE,
    STATUS_TODO,
    STATUS_USER_GATE,
)
from .kanban_config import (
    LARK_KANBAN_LOCAL_CONFIG_VERSION,
    LarkKanbanConfig,
    _compact_text,
    _credential_safe_lark_values,
    _public_safe_lark_values,
    _sink_safe_lark_values,
    build_record_list_command,
    read_lark_kanban_local_config,
    write_lark_kanban_local_config,
)
from .record_io import (
    record_list_is_complete,
    todo_record_entries,
    todo_record_list_is_authoritative,
)


class _ProjectionRowsAdapter(Protocol):
    def projection_lifecycle_parts(
        self, item: Mapping[str, object], *, source_id: str
    ) -> list[str]: ...
    def projection_lifecycle_state(self, item: Mapping[str, object]) -> str: ...
    def projection_lifecycle_value(
        self, item: Mapping[str, object], key: str
    ) -> object: ...
    def projection_namespace(
        self, projection: Mapping[str, object], source_id: str | None
    ) -> tuple[str, list[str]]: ...
    def projection_rows_from_payload(
        self,
        projection: Mapping[str, object],
        *,
        goal_id: str | None,
        agent_id: str | None,
        source_id: str,
        include_done: bool,
        limit: int,
    ) -> tuple[str, list[dict[str, object]], list[str]]: ...
    def projection_text_list(self, value: object) -> list[str]: ...
    def todo_matches_agent_scope(
        self, block: Mapping[str, object], agent_id: str | None
    ) -> bool: ...


def _projection_rows_api() -> _ProjectionRowsAdapter:
    return cast(_ProjectionRowsAdapter, projection_rows)


def _json_dict(value: Mapping[str, object]) -> dict[str, object]:
    return dict(value)


def _lark_record_from_projection_block(
    block: Mapping[str, object],
    *,
    goal_id: str,
    source_id: str,
    public_safe: bool = False,
) -> dict[str, object]:
    rows_api = _projection_rows_api()
    values = _lark_record_from_todo_block(
        block,
        goal_id=goal_id,
        state_file=Path(f"{source_id}.projection"),
        priority=str(block.get("priority") or "P2"),
    )
    original_id = str(block.get("original_todo_id") or "")
    evidence_parts = [
        str(block.get("evidence") or block.get("reason") or block.get("note") or "").strip(),
        f"source_id={source_id}",
    ]
    if original_id:
        evidence_parts.append(f"original_todo_id={original_id}")
    evidence_parts.extend(rows_api.projection_lifecycle_parts(block, source_id=source_id))
    evidence_parts = list(dict.fromkeys(part for part in evidence_parts if part))
    lifecycle_state = rows_api.projection_lifecycle_state(block)
    default_handoff = f"Synced from LoopX projection source={source_id}"
    if lifecycle_state in {"superseded", "migrated", "retired"}:
        superseded_by = (
            ", ".join(
                rows_api.projection_text_list(
                    rows_api.projection_lifecycle_value(block, "superseded_by")
                )
            )
            or "current projection row"
        )
        default_handoff = f"Row lifecycle {lifecycle_state}; continue with {superseded_by}."
    values.update(
        {
            "LoopX Goal ID": goal_id,
            "LoopX Todo ID": str(block.get("todo_id") or ""),
            "Scope": _compact_text(
                block.get("scope") or block.get("source_section") or f"projection_source={source_id}"
            ),
            "Handoff": _compact_text(block.get("handoff") or default_handoff),
            "Evidence": _compact_text("; ".join(evidence_parts)),
            "Run History": _compact_text(
                "; ".join(
                    part
                    for part in (
                        f"synced from LoopX projection source={source_id} status={block.get('status') or 'open'}",
                        f"row_lifecycle={lifecycle_state}" if lifecycle_state else "",
                    )
                    if part
                )
            ),
        }
    )
    values.update(issue_fix_surface.issue_fix_record_values(block, compact_text=_compact_text))
    return _sink_safe_lark_values(values, public_safe=public_safe)


def _projection_upsert_items(
    rows: Sequence[Mapping[str, object]],
    *,
    default_goal_id: str,
    source_id: str,
    public_safe: bool,
) -> list[tuple[str, str, dict[str, object]]]:
    items: list[tuple[str, str, dict[str, object]]] = []
    for row in rows:
        row_goal_id = str(row.get("goal_id") or default_goal_id)
        todo_id = str(row.get("todo_id") or "").strip()
        values = _lark_record_from_projection_block(
            row, goal_id=row_goal_id, source_id=source_id, public_safe=public_safe
        )
        items.append((f"{row_goal_id}:{todo_id}", todo_id, values))
    return items


def _collect_goal_todo_items(
    lines: list[str],
    *,
    goal_id: str,
    state_file: Path,
    agent_id: str | None,
    include_done: bool,
    limit: int,
    public_safe: bool,
) -> list[tuple[str, str, dict[str, object]]]:
    from ....control_plane.todos.active_state_editing import section_bounds, todo_blocks
    from ....control_plane.todos.todo_semantics import todo_priority_label

    rows_api = _projection_rows_api()
    selected: list[tuple[dict[str, object], str]] = []
    for role in ("user", "agent"):
        bounds = section_bounds(lines, role)
        if not bounds:
            continue
        start, end, heading = bounds
        for block in todo_blocks(lines, start, end, role=role, source_section=heading):
            status = normalize_todo_status(str(block.get("status") or "").strip())
            if (block.get("done") or status == TODO_STATUS_DONE) and not include_done:
                continue
            candidate: dict[str, object] = {
                **{str(k): v for k, v in block.items()},
                "role": role,
                "source_section": heading,
            }
            if rows_api.todo_matches_agent_scope(candidate, agent_id):
                priority = todo_priority_label(block, text_mode="prefix") or "P2"
                selected.append((candidate, priority))
    selected = selected[:limit]

    todo_items: list[tuple[str, str, dict[str, object]]] = []
    for candidate, priority in selected:
        todo_id = str(candidate.get("todo_id") or "").strip()
        values = _lark_record_from_todo_block(
            candidate,
            goal_id=goal_id,
            state_file=state_file,
            priority=priority,
        )
        if public_safe:
            values = _public_safe_lark_values(values)
        todo_items.append((f"{goal_id}:{todo_id}", todo_id, values))
    return todo_items


def _load_lark_todo_record_map(
    config: LarkKanbanConfig,
    *,
    config_path: Path | None,
    execute: bool,
    inspect_remote: bool,
    commands: list[dict[str, object]],
    runner: cli_transport.CommandRunner,
) -> tuple[dict[str, object], dict[str, str], list[dict[str, str]], bool | None]:
    local: dict[str, object] = read_lark_kanban_local_config(config_path) if config_path else {}
    record_map = _todo_record_map_from_local_config(local)
    if not execute and not inspect_remote:
        return local, record_map, [], None

    list_config = LarkKanbanConfig(
        base_token=config.base_token,
        table_id=config.table_id,
        view_id=None,
        cli_bin=config.cli_bin,
        identity=config.identity,
    )
    list_result = _json_dict(
        cli_transport.run_command(
            build_record_list_command(list_config), execute=True, runner=runner
        )
    )
    commands.append(list_result)
    remote_records: list[dict[str, str]] = []
    remote_complete = False
    if list_result.get("ok"):
        parsed_records = list_result.get("json")
        remote_records = todo_record_entries(parsed_records)
        remote_by_key: dict[str, list[str]] = {}
        for item in remote_records:
            remote_by_key.setdefault(item["key"], []).append(item["record_id"])
        if todo_record_list_is_authoritative(parsed_records):
            record_map = {
                key: record_id
                for key, record_id in record_map.items()
                if record_id in remote_by_key.get(key, [])
            }
        for key, record_ids in remote_by_key.items():
            preferred = record_map.get(key)
            record_map[key] = preferred if preferred in record_ids else sorted(record_ids)[0]
        remote_complete = record_list_is_complete(parsed_records)
    return local, record_map, remote_records, remote_complete


def _todo_record_map_from_local_config(local: Mapping[str, object]) -> dict[str, str]:
    records = local.get("todo_records") if isinstance(local, Mapping) else None
    if not isinstance(records, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in records.items()
        if str(key).strip() and str(value).strip()
    }


def _persist_lark_todo_record_map(
    config: LarkKanbanConfig,
    *,
    config_path: Path | None,
    local: Mapping[str, object],
    record_map: Mapping[str, str],
) -> None:
    if not config_path:
        return
    raw_board = local.get("board")
    board: Mapping[str, object] = (
        raw_board
        if isinstance(raw_board, dict) and raw_board
        else {
            "base_token": config.base_token,
            "table_id": config.table_id,
            "view_id": config.view_id,
            "cli_bin": config.cli_bin,
            "identity": config.identity,
        }
    )
    write_lark_kanban_local_config(
        config_path,
        {
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "board": board,
            "todo_records": dict(record_map),
        },
    )


def _lark_record_from_todo_block(
    block: Mapping[str, object], *, goal_id: str, state_file: Path, priority: str
) -> dict[str, object]:
    role = str(block.get("role") or "agent")
    status = normalize_todo_status(str(block.get("status") or "").strip()) or TODO_STATUS_OPEN
    task_class = normalize_explicit_todo_task_class(block.get("task_class")) or (
        TODO_TASK_CLASS_USER_GATE if role == "user" else TODO_TASK_CLASS_ADVANCEMENT
    )
    claimed_by = str(block.get("claimed_by") or "").strip()
    lark_status = STATUS_TODO
    if block.get("done") or status == TODO_STATUS_DONE:
        lark_status = STATUS_DONE
    elif status == TODO_STATUS_BLOCKED or task_class == TODO_TASK_CLASS_BLOCKER:
        lark_status = STATUS_BLOCKED
    elif role == "user" or task_class == TODO_TASK_CLASS_USER_GATE or status == TODO_STATUS_DEFERRED:
        lark_status = STATUS_USER_GATE
    elif claimed_by:
        lark_status = STATUS_CLAIMED
    claim = (
        CLAIM_HUMAN
        if lark_status == STATUS_USER_GATE
        else (CLAIM_AGENT if claimed_by else CLAIM_UNCLAIMED)
    )
    scope = block.get("required_write_scopes")
    scope_text = ", ".join(str(item) for item in scope) if isinstance(scope, list) else str(scope or "")
    text = str(block.get("text") or "")
    values: dict[str, object] = {
        "Task": text,
        "Status": lark_status,
        "Claim": claim,
        "Claimed By": claimed_by,
        "Priority": priority if priority in {"P0", "P1", "P2", "P3"} else "P2",
        "Task Class": task_class,
        "Action Kind": str(block.get("action_kind") or "sync_loopx_todo"),
        "LoopX Goal ID": goal_id,
        "LoopX Todo ID": str(block.get("todo_id") or ""),
        **issue_fix_surface.todo_record_values(),
        "Scope": scope_text,
        "User Gate": text if lark_status == STATUS_USER_GATE else "",
        "Handoff": f"Synced from LoopX active state: {state_file.name}",
        "Evidence": str(block.get("evidence") or block.get("reason") or block.get("note") or ""),
        "Run History": f"synced from LoopX todo status={status}",
        "Worker Command": "",
        "Workdir": "",
        "Last Error": "",
        "Last Result Code": None,
    }
    return _credential_safe_lark_values(values)
