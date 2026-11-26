"""Public Lark Kanban facade, seeding workflows, and heartbeat execution."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from .. import cli_transport as cli_transport
from . import issue_fix_surface as issue_fix_surface
from .kanban_board import (
    CLAIM_AGENT as CLAIM_AGENT,
    CLAIM_HUMAN as CLAIM_HUMAN,
    CLAIM_UNCLAIMED as CLAIM_UNCLAIMED,
    DEFAULT_STATUS_QUEUE_VIEW as DEFAULT_STATUS_QUEUE_VIEW,
    DEFAULT_TABLE_NAME as DEFAULT_TABLE_NAME,
    LARK_KANBAN_SCHEMA_VERSION as LARK_KANBAN_SCHEMA_VERSION,
    OPERATOR_CARD_FIELDS as OPERATOR_CARD_FIELDS,
    STATUS_BLOCKED as STATUS_BLOCKED,
    STATUS_CLAIMED as STATUS_CLAIMED,
    STATUS_DONE as STATUS_DONE,
    STATUS_REVIEW as STATUS_REVIEW,
    STATUS_RUNNING as STATUS_RUNNING,
    STATUS_TODO as STATUS_TODO,
    STATUS_USER_GATE as STATUS_USER_GATE,
    _base_cmd as _base_cmd,
    _board_payload as _board_payload,
    _extract_base_token as _extract_base_token,
    _extract_created_record_id as _extract_created_record_id,
    _extract_list_from_payload as _extract_list_from_payload,
    _extract_table_id as _extract_table_id,
    _extract_table_id_from_blocks as _extract_table_id_from_blocks,
    _extract_view_ids as _extract_view_ids,
    _find_first_record_id as _find_first_record_id,
    _find_first_string as _find_first_string,
    _identity_available as _identity_available,
    _identity_error as _identity_error,
    _inspect_lark_kanban_schema as _inspect_lark_kanban_schema,
    _lark_kanban_preflight as _lark_kanban_preflight,
    _lark_kanban_schema_check as _lark_kanban_schema_check,
    _refresh_view_ids as _refresh_view_ids,
    _schema_check_error as _schema_check_error,
    _select_options as _select_options,
    _setup_payload as _setup_payload,
    _string_id_from_dict as _string_id_from_dict,
    _version_tuple as _version_tuple,
    _view_configuration_commands as _view_configuration_commands,
    build_create_board_plan as build_create_board_plan,
    create_lark_kanban_board as create_lark_kanban_board,
    lark_kanban_doctor as lark_kanban_doctor,
    lark_kanban_field_definitions as lark_kanban_field_definitions,
    lark_kanban_operator_card_fields as lark_kanban_operator_card_fields,
    lark_kanban_schema_payload as lark_kanban_schema_payload,
    lark_kanban_views as lark_kanban_views,
    setup_lark_kanban_board as setup_lark_kanban_board,
    use_lark_kanban_board as use_lark_kanban_board,
)
from .kanban_config import (
    DEFAULT_AGENT_ID as DEFAULT_AGENT_ID,
    LARK_KANBAN_HEARTBEAT_VERSION as LARK_KANBAN_HEARTBEAT_VERSION,
    LARK_KANBAN_LOCAL_CONFIG_VERSION as LARK_KANBAN_LOCAL_CONFIG_VERSION,
    LARK_KANBAN_SYNC_PROJECTION_VERSION as LARK_KANBAN_SYNC_PROJECTION_VERSION,
    TEXT_LIMIT as TEXT_LIMIT,
    LarkKanbanConfig as LarkKanbanConfig,
    _ANSI_CONTROL_SEQUENCE_PATTERN as _ANSI_CONTROL_SEQUENCE_PATTERN,
    _FEASIBILITY_CASE_SPECS as _FEASIBILITY_CASE_SPECS,
    _SHARED_REDACTION_PATTERNS as _SHARED_REDACTION_PATTERNS,
    _STRUCTURAL_ID_KEYS as _STRUCTURAL_ID_KEYS,
    _STRUCTURAL_RECORD_ID_PATTERN as _STRUCTURAL_RECORD_ID_PATTERN,
    _coerce_mapping_tree as _coerce_mapping_tree,
    _coerce_mapping_value as _coerce_mapping_value,
    _compact_text as _compact_text,
    _credential_safe_lark_values as _credential_safe_lark_values,
    _credential_safe_text as _credential_safe_text,
    _credential_safe_value as _credential_safe_value,
    _first_query_value as _first_query_value,
    _local_config_stub as _local_config_stub,
    _next_lark_kanban_commands as _next_lark_kanban_commands,
    _public_safe_lark_values as _public_safe_lark_values,
    _public_safe_text as _public_safe_text,
    _public_safe_value as _public_safe_value,
    _record_json_args as _record_json_args,
    _redact_credential_shapes as _redact_credential_shapes,
    _redact_non_credential_shapes as _redact_non_credential_shapes,
    _redact_public_safe_shapes as _redact_public_safe_shapes,
    _sink_safe_lark_values as _sink_safe_lark_values,
    _task_record as _task_record,
    _transform_lark_value as _transform_lark_value,
    _validate_sink_visibility as _validate_sink_visibility,
    allowed_worker_command as allowed_worker_command,
    append_history as append_history,
    build_record_list_command as build_record_list_command,
    build_record_upsert_command as build_record_upsert_command,
    choose_heartbeat_task as choose_heartbeat_task,
    default_lark_kanban_config_path as default_lark_kanban_config_path,
    lark_kanban_config_from_payload as lark_kanban_config_from_payload,
    lark_kanban_feasibility_cases as lark_kanban_feasibility_cases,
    lark_kanban_ux_task as lark_kanban_ux_task,
    normalize_select_value as normalize_select_value,
    now_lark_datetime as now_lark_datetime,
    parse_lark_base_url as parse_lark_base_url,
    read_lark_kanban_local_config as read_lark_kanban_local_config,
    sample_lark_kanban_task as sample_lark_kanban_task,
    save_lark_kanban_board_config as save_lark_kanban_board_config,
    write_lark_kanban_local_config as write_lark_kanban_local_config,
)
from .kanban_records import (
    _lark_record_from_projection_block as _lark_record_from_projection_block,
    _lark_record_from_todo_block as _lark_record_from_todo_block,
    _load_lark_todo_record_map as _load_lark_todo_record_map,
    _persist_lark_todo_record_map as _persist_lark_todo_record_map,
    _projection_upsert_items as _projection_upsert_items,
    _todo_record_map_from_local_config as _todo_record_map_from_local_config,
)
from .kanban_sync import (
    LarkKanbanTodoSyncResult as LarkKanbanTodoSyncResult,
    _upsert_record_batch as _upsert_record_batch,
    sync_loopx_projection_to_lark_kanban as sync_loopx_projection_to_lark_kanban,
    sync_loopx_todos_to_lark_kanban as sync_loopx_todos_to_lark_kanban,
)
from .record_io import (
    RecordTarget as RecordTarget,
    build_record_delete_command as build_record_delete_command,
    lark_record_rows as lark_record_rows,
    record_list_is_complete as record_list_is_complete,
    todo_record_entries as todo_record_entries,
    todo_record_list_is_authoritative as todo_record_list_is_authoritative,
)
from .sink_visibility import (
    SINK_VISIBILITIES as SINK_VISIBILITIES,
    SINK_VISIBILITY_OWNER_ONLY as SINK_VISIBILITY_OWNER_ONLY,
    SINK_VISIBILITY_SHARED as SINK_VISIBILITY_SHARED,
)
from .sync_receipt import (
    compact_lark_kanban_sync_receipt as compact_lark_kanban_sync_receipt,
    render_lark_kanban_markdown as render_lark_kanban_markdown,
)


def seed_lark_kanban_task(
    config: LarkKanbanConfig,
    *,
    task: Mapping[str, object],
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, object]:
    public_safe = _validate_sink_visibility(sink_visibility)
    safe_task = _sink_safe_lark_values(task, public_safe=public_safe)
    result = dict(
        cli_transport.run_command(
            build_record_upsert_command(config, record_id=None, values=safe_task),
            execute=execute,
            runner=runner,
        )
    )
    return {
        "ok": bool(result.get("ok")),
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "record_id": _extract_created_record_id(result.get("json")),
        "command": result,
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "task": safe_task,
    }


def seed_lark_kanban_records(
    config: LarkKanbanConfig,
    *,
    records: Sequence[Mapping[str, object]],
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, object]:
    public_safe = _validate_sink_visibility(sink_visibility)
    results: list[dict[str, object]] = []
    ok = True
    for record in records:
        result = seed_lark_kanban_task(
            config,
            task=record,
            sink_visibility=sink_visibility,
            execute=execute,
            runner=runner,
        )
        results.append(result)
        ok = ok and bool(result.get("ok"))
        if execute and not result.get("ok"):
            break
    return {
        "ok": ok,
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "record_count": len(records),
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "created_record_ids": [item.get("record_id") for item in results],
        "records": results,
    }


def _run_heartbeat_worker(
    task: Mapping[str, object],
    *,
    command: str,
    execute_worker: bool,
    complete_on_success: bool,
    allowed_command_prefixes: Sequence[str] | None,
    runner: cli_transport.CommandRunner,
    worker_timeout_seconds: float,
) -> tuple[dict[str, object], str, str, int | None]:
    safe_command = _redact_credential_shapes(command)
    if not command:
        return (
            {
                "attempted": False,
                "executed": False,
                "ok": False,
                "reason": "worker_command_missing",
                "command": "",
            },
            STATUS_USER_GATE,
            "worker command missing; user must provide Worker Command or heartbeat --worker-command",
            None,
        )
    if not execute_worker:
        return (
            {
                "attempted": True,
                "executed": False,
                "ok": True,
                "reason": "execute_worker_not_set",
                "command": safe_command,
            },
            STATUS_CLAIMED,
            f"dry-run worker command: {safe_command}",
            None,
        )
    if not allowed_worker_command(command, allowed_command_prefixes or []):
        return (
            {
                "attempted": True,
                "executed": False,
                "ok": False,
                "reason": "worker_command_prefix_not_allowed",
                "command": safe_command,
                "allowed_prefixes": list(allowed_command_prefixes or []),
            },
            STATUS_BLOCKED,
            "worker command blocked by prefix allowlist",
            None,
        )
    worker_result = cli_transport.run_command(
        shlex.split(command),
        execute=True,
        runner=runner,
        cwd=Path(str(task.get("Workdir") or ".")).expanduser(),
        timeout_seconds=worker_timeout_seconds,
    )
    worker_ok = bool(worker_result.get("ok"))
    worker: dict[str, object] = {
        "attempted": True,
        "executed": True,
        "ok": worker_ok,
        "reason": "completed" if worker_ok else "failed",
        "command": safe_command,
        "returncode": worker_result.get("returncode"),
        "stdout": _credential_safe_text(worker_result.get("stdout"), limit=cli_transport.OUTPUT_LIMIT),
        "stderr": _credential_safe_text(worker_result.get("stderr"), limit=cli_transport.OUTPUT_LIMIT),
    }
    final_status = (STATUS_DONE if complete_on_success else STATUS_REVIEW) if worker_ok else STATUS_BLOCKED
    evidence = _credential_safe_text(
        worker.get("stdout") or worker.get("stderr") or worker.get("reason"),
        limit=cli_transport.OUTPUT_LIMIT,
    )
    raw_code = worker_result.get("returncode")
    return worker, final_status, evidence, raw_code if isinstance(raw_code, int) else None


def lark_kanban_heartbeat(
    config: LarkKanbanConfig,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    fixture: Mapping[str, object] | None = None,
    worker_command: str | None = None,
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute_lark: bool = False,
    execute_worker: bool = False,
    complete_on_success: bool = False,
    allowed_command_prefixes: Sequence[str] | None = None,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
    now: datetime | None = None,
    worker_timeout_seconds: float = 600.0,
) -> dict[str, object]:
    public_safe = _validate_sink_visibility(sink_visibility)
    commands: list[dict[str, object]] = []
    parsed_mapping: dict[str, object] = {}
    if fixture is None:
        list_result = dict(
            cli_transport.run_command(
                build_record_list_command(config), execute=execute_lark, runner=runner
            )
        )
        commands.append(list_result)
        if execute_lark and not list_result.get("ok"):
            return _heartbeat_payload(
                ok=False,
                decision="record_list_failed",
                agent_id=agent_id,
                commands=commands,
                records=[],
            )
        raw_parsed = list_result.get("json") if execute_lark else {"data": {"fields": [], "data": []}}
        if isinstance(raw_parsed, Mapping):
            parsed_mapping = _coerce_mapping_tree(raw_parsed)
    elif isinstance(fixture, Mapping):
        parsed_mapping = _coerce_mapping_tree(fixture)
    records = lark_record_rows(parsed_mapping)
    task = choose_heartbeat_task(records, agent_id=agent_id)
    if task is None:
        return _heartbeat_payload(
            ok=True,
            decision="no_claimable_task",
            agent_id=agent_id,
            commands=commands,
            records=records,
        )
    record_id = str(task.get("_record_id") or "").strip()
    if not record_id:
        return _heartbeat_payload(
            ok=False,
            decision="selected_task_missing_record_id",
            agent_id=agent_id,
            commands=commands,
            records=records,
            selected_task=task,
        )
    timestamp = now_lark_datetime(now)
    claim_values = _sink_safe_lark_values(
        {
            "Status": STATUS_CLAIMED,
            "Claim": CLAIM_AGENT,
            "Claimed By": agent_id,
            "Last Heartbeat": timestamp,
            "Run History": append_history(task.get("Run History"), f"{timestamp} {agent_id}: claimed"),
        },
        public_safe=public_safe,
    )
    claim_result = dict(
        cli_transport.run_command(
            build_record_upsert_command(config, record_id=record_id, values=claim_values),
            execute=execute_lark,
            runner=runner,
        )
    )
    commands.append(claim_result)
    if execute_lark and not claim_result.get("ok"):
        return _heartbeat_payload(
            ok=False,
            decision="claim_failed",
            agent_id=agent_id,
            commands=commands,
            records=records,
            selected_task=task,
        )
    command = (worker_command if worker_command is not None else str(task.get("Worker Command") or "")).strip()
    worker, final_status, evidence, result_code = _run_heartbeat_worker(
        task,
        command=command,
        execute_worker=execute_worker,
        complete_on_success=complete_on_success,
        allowed_command_prefixes=allowed_command_prefixes,
        runner=runner,
        worker_timeout_seconds=worker_timeout_seconds,
    )
    finish_values = _sink_safe_lark_values(
        {
            "Status": final_status,
            "Claim": CLAIM_AGENT,
            "Claimed By": agent_id,
            "Last Heartbeat": timestamp,
            "Evidence": evidence,
            "Last Result Code": result_code,
            "Last Error": "" if worker.get("ok") else _credential_safe_text(worker.get("stderr") or worker.get("reason")),
            "Run History": append_history(
                claim_values["Run History"],
                f"{timestamp} {agent_id}: worker {worker.get('reason')} -> {final_status}",
            ),
            "Handoff": _handoff_for_status(final_status, agent_id=agent_id),
        },
        public_safe=public_safe,
    )
    finish_result = dict(
        cli_transport.run_command(
            build_record_upsert_command(config, record_id=record_id, values=finish_values),
            execute=execute_lark,
            runner=runner,
        )
    )
    commands.append(finish_result)
    ok = bool(worker.get("ok")) and (not execute_lark or bool(finish_result.get("ok")))
    return _heartbeat_payload(
        ok=ok,
        decision="task_processed",
        agent_id=agent_id,
        commands=commands,
        records=records,
        selected_task=task,
        worker=worker,
        final_status=final_status,
        writeback=finish_values,
    )


def _handoff_for_status(status: str, *, agent_id: str) -> str:
    messages = {
        STATUS_DONE: f"{agent_id} completed the task; review evidence and close any external issue/PR bookkeeping.",
        STATUS_REVIEW: f"{agent_id} produced evidence; human/controller review should decide merge or next todo.",
        STATUS_USER_GATE: "User input is required before this task can proceed.",
        STATUS_BLOCKED: f"{agent_id} hit a blocker; inspect Last Error and decide repair/retry/reassign.",
    }
    return messages.get(status, f"{agent_id} claimed the task; heartbeat can continue.")


def _heartbeat_payload(
    *,
    ok: bool,
    decision: str,
    agent_id: str,
    commands: Sequence[Mapping[str, object]],
    records: Sequence[Mapping[str, object]],
    selected_task: Mapping[str, object] | None = None,
    worker: Mapping[str, object] | None = None,
    final_status: str | None = None,
    writeback: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "ok": ok,
        "schema_version": LARK_KANBAN_HEARTBEAT_VERSION,
        "agent_id": agent_id,
        "decision": decision,
        "record_count": len(records),
        "selected_record_id": selected_task.get("_record_id") if selected_task else None,
        "selected_task": dict(selected_task) if selected_task is not None else None,
        "worker": dict(worker) if worker is not None else None,
        "final_status": final_status,
        "writeback": dict(writeback) if writeback is not None else None,
        "commands": [dict(cmd) for cmd in commands],
    }
