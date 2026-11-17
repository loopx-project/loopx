"""Lark Kanban board schema, provisioning, setup, doctor, and payload ID helpers."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import cli_transport
from . import issue_fix_surface

if TYPE_CHECKING:
    from .kanban import LarkKanbanConfig

LARK_KANBAN_SCHEMA_VERSION = "loopx_lark_kanban_control_plane_v0"
DEFAULT_TABLE_NAME = "LoopX Control Plane"
DEFAULT_STATUS_QUEUE_VIEW = "Worker Queue"
OPERATOR_CARD_FIELDS = [
    "Task",
    "Claim",
    "Priority",
    "User Gate",
    "Evidence",
    "Status",
]

STATUS_TODO = "Todo"
STATUS_CLAIMED = "Claimed"
STATUS_RUNNING = "Running"
STATUS_USER_GATE = "User Gate"
STATUS_BLOCKED = "Blocked"
STATUS_REVIEW = "Review"
STATUS_DONE = "Done"
CLAIM_UNCLAIMED = "Unclaimed"
CLAIM_HUMAN = "Human"
CLAIM_AGENT = "Agent"

_SELECT_HUES = {
    STATUS_TODO: "Gray",
    STATUS_CLAIMED: "Blue",
    STATUS_RUNNING: "Orange",
    STATUS_USER_GATE: "Purple",
    STATUS_BLOCKED: "Red",
    STATUS_REVIEW: "Wathet",
    STATUS_DONE: "Green",
    CLAIM_UNCLAIMED: "Gray",
    CLAIM_HUMAN: "Green",
    CLAIM_AGENT: "Blue",
    "P0": "Red",
    "P1": "Orange",
    "P2": "Blue",
    "P3": "Gray",
    "advancement_task": "Blue",
    "continuous_monitor": "Wathet",
    "user_gate": "Purple",
    "blocker": "Red",
}


def _select_options(names: list[str]) -> list[dict[str, str]]:
    return [
        {"name": name, "hue": _SELECT_HUES.get(name, "Blue"), "lightness": "Light"}
        for name in names
    ]


def _text_field(name: str) -> dict[str, Any]:
    return {"name": name, "type": "text", "style": {"type": "plain"}}


def _select_field(name: str, options: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "type": "select",
        "multiple": False,
        "options": _select_options(options),
    }


def _date_field(name: str, field_type: str = "datetime") -> dict[str, Any]:
    return {"name": name, "type": field_type, "style": {"format": "yyyy-MM-dd HH:mm"}}


def lark_kanban_field_definitions() -> list[dict[str, Any]]:
    return [
        _text_field("Task"),
        _select_field(
            "Status",
            [
                STATUS_TODO,
                STATUS_CLAIMED,
                STATUS_RUNNING,
                STATUS_USER_GATE,
                STATUS_BLOCKED,
                STATUS_REVIEW,
                STATUS_DONE,
            ],
        ),
        _select_field("Claim", [CLAIM_UNCLAIMED, CLAIM_HUMAN, CLAIM_AGENT]),
        _text_field("Claimed By"),
        _select_field("Priority", ["P0", "P1", "P2", "P3"]),
        _select_field(
            "Task Class",
            ["advancement_task", "continuous_monitor", "user_gate", "blocker"],
        ),
        *(_text_field(n) for n in ("Action Kind", "LoopX Goal ID", "LoopX Todo ID")),
        *issue_fix_surface.issue_fix_field_definitions(_select_options),
        *(
            _text_field(n)
            for n in (
                "Scope",
                "User Gate",
                "Handoff",
                "Evidence",
                "Run History",
                "Worker Command",
                "Workdir",
                "Last Error",
            )
        ),
        {
            "name": "Last Result Code",
            "type": "number",
            "style": {
                "type": "plain",
                "precision": 0,
                "percentage": False,
                "thousands_separator": False,
            },
        },
        _date_field("Last Heartbeat"),
        _date_field("Created At", "created_at"),
        _date_field("Updated At", "updated_at"),
    ]


def lark_kanban_views() -> list[dict[str, str]]:
    return [
        {"name": "All Tasks", "type": "grid"},
        {"name": DEFAULT_STATUS_QUEUE_VIEW, "type": "grid"},
        {"name": "User Gates", "type": "grid"},
        {"name": "Kanban", "type": "kanban"},
        *issue_fix_surface.issue_fix_views(),
    ]


def lark_kanban_schema_payload(*, table_name: str = DEFAULT_TABLE_NAME) -> dict[str, Any]:
    from . import kanban

    return {
        "ok": True,
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "table_name": table_name,
        "source_of_truth": "loopx_todos_projected_to_lark_base",
        "adapter_role": "status_tracker_claim_surface",
        "loopx_mapping": {
            "todo": "Task row with Status=Todo and Claim=Unclaimed",
            "claim": "Claim single-selection plus Claimed By text",
            "user_gate": "Status=User Gate and concrete question in User Gate",
            "handoff": "Handoff text field",
            "evidence": "Evidence text field",
            "run_history": "Run History append-style compact text field",
            "quota": "omitted in v0 prototype; assume no quota limit",
            "scope": "Scope text field; advisory in v0 prototype",
            "issue_fix_outcome": (
                "First-class issue row with repository, issue, PR, route, "
                "stage, validation, and outcome dimensions."
            ),
            "issue_fix_metric": (
                "Monthly Impact row with repository baseline, current value, "
                "delta, ratio lineage, source link, freshness, and explicit missing data."
            ),
        },
        "fields": kanban.lark_kanban_field_definitions(),
        "views": kanban.lark_kanban_views(),
        "operator_view": {
            "kanban_card_fields": OPERATOR_CARD_FIELDS,
            "issue_fix_card_fields": issue_fix_surface.ISSUE_FIX_CARD_FIELDS,
            "issue_fix_views": [
                issue_fix_surface.DEFAULT_ISSUE_FIX_GRID_VIEW,
                issue_fix_surface.DEFAULT_ISSUE_FIX_KANBAN_VIEW,
            ],
            "issue_fix_metrics_view": issue_fix_surface.DEFAULT_ISSUE_FIX_METRICS_VIEW,
            "issue_fix_metric_fields": issue_fix_surface.ISSUE_FIX_METRIC_FIELDS,
            "reason": (
                "Keep the human-facing Kanban card small; retain the full task "
                "context in the record detail and All Tasks grid."
            ),
            "configuration_note": (
                "lark-cli 1.0.56 exposes +view-set-visible-fields, so setup can "
                "write the compact Kanban card field list directly."
            ),
        },
        "heartbeat_model": {
            "direct_lark_trigger": False,
            "trigger_reason": (
                "Feishu Base workflow cannot reach this local edge worker without "
                "a reachable callback or daemon bridge in the current environment."
            ),
            "fallback": (
                "agent heartbeat polls Worker Queue, soft-claims one task, "
                "runs worker command, writes evidence"
            ),
        },
        "task_spawning_model": {
            "board_creates_tasks": False,
            "rule": (
                "Long-running Codex sessions may claim visible Kanban rows, but "
                "new, split, successor, or superseding work must be written through "
                "LoopX todo lifecycle/intake commands and then synced back to Lark."
            ),
            "commands": [
                "loopx todo add",
                "loopx todo complete --next-agent-todo",
                "loopx todo supersede --next-agent-todo",
                "complex_request_intake_v0",
            ],
        },
    }


def lark_kanban_operator_card_fields() -> list[str]:
    return list(OPERATOR_CARD_FIELDS)


def _base_cmd(
    cli_bin: str,
    subcommand: str,
    identity: str,
    *extra: str,
    base_token: str | None = None,
    table_id: str | None = None,
) -> list[str]:
    args = [cli_bin, "base", subcommand, "--as", identity]
    if base_token is not None:
        args.extend(["--base-token", base_token])
    if table_id is not None:
        args.extend(["--table-id", table_id])
    return [*args, *extra]


def _table_create_cmd(
    cli_bin: str,
    identity: str,
    base_token: str,
    table_name: str,
    fields_json: str,
    views_json: str,
) -> list[str]:
    return _base_cmd(
        cli_bin, "+table-create", identity, "--name", table_name,
        "--fields", fields_json, "--view", views_json, base_token=base_token,
    )


def _permission_grant_cmd(cli_bin: str, base_token: str, user_open_id: str) -> list[str]:
    return [
        cli_bin, "drive", "permission.members", "create", "--as", "bot",
        "--params", json.dumps(
            {"token": base_token, "type": "bitable", "need_notification": False},
            ensure_ascii=False,
        ),
        "--data", json.dumps(
            {
                "member_id": user_open_id,
                "member_type": "openid",
                "perm": "full_access",
                "perm_type": "container",
                "type": "user",
            },
            ensure_ascii=False,
        ),
    ]


def build_create_board_plan(
    *,
    base_name: str,
    table_name: str,
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN,
    identity: str = "bot",
    base_token: str | None = None,
    user_open_id: str | None = None,
) -> list[list[str]]:
    from . import kanban

    fields_json = json.dumps(kanban.lark_kanban_field_definitions(), ensure_ascii=False)
    views = kanban.lark_kanban_views()
    views_json = json.dumps(views, ensure_ascii=False)
    commands: list[list[str]] = []
    if not base_token:
        commands.append(
            _base_cmd(
                cli_bin, "+base-create", identity,
                "--name", base_name, "--table-name", table_name, "--fields", fields_json,
            )
        )
        commands.append(
            _base_cmd(
                cli_bin, "+base-block-list", identity, "--type", "table",
                base_token="<base-token-from-create>",
            )
        )
    token = base_token or "<base-token-from-create>"
    table_ref = "<table-id-from-create>"
    if base_token:
        commands.append(_table_create_cmd(cli_bin, identity, token, table_name, fields_json, views_json))
    else:
        commands.extend(
            [
                _base_cmd(cli_bin, "+view-create", identity, "--json", views_json, base_token=token, table_id=table_ref),
                _base_cmd(cli_bin, "+view-list", identity, base_token=token, table_id=table_ref),
            ]
        )
    if user_open_id:
        commands.append(_permission_grant_cmd(cli_bin, token, user_open_id))
    commands.extend(
        _view_configuration_commands(
            cli_bin=cli_bin, identity=identity, base_token=token, table_id=table_ref,
            view_ids={view["name"]: view["name"] for view in views},
        )
    )
    return commands


def create_lark_kanban_board(
    *,
    base_name: str,
    table_name: str = DEFAULT_TABLE_NAME,
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN,
    identity: str = "bot",
    base_token: str | None = None,
    user_open_id: str | None = None,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, Any]:
    from . import kanban

    commands: list[dict[str, Any]] = []
    effective_base_token = base_token
    table_id: str | None = None
    if not effective_base_token:
        create = kanban.cli_transport.run_command(
            _base_cmd(cli_bin, "+base-create", identity, "--name", base_name),
            execute=execute, runner=runner,
        )
        commands.append(create)
        if create.get("executed"):
            if not create.get("ok"):
                return _board_payload(False, commands, effective_base_token, table_id)
            effective_base_token = _extract_base_token(create.get("json"))
            if not effective_base_token:
                commands.append(
                    {
                        "command": "extract Base token from base-create result",
                        "executed": True, "ok": False, "returncode": None,
                        "stdout": "", "stderr": "base-create did not return a usable Base token", "json": None,
                    }
                )
                return _board_payload(False, commands, effective_base_token, table_id)
    token = effective_base_token or "<base-token-from-create>"
    views = kanban.lark_kanban_views()
    table_create = kanban.cli_transport.run_command(
        _table_create_cmd(
            cli_bin, identity, token, table_name,
            json.dumps(kanban.lark_kanban_field_definitions(), ensure_ascii=False),
            json.dumps(views, ensure_ascii=False),
        ),
        execute=execute, runner=runner,
    )
    commands.append(table_create)
    if table_create.get("executed"):
        if not table_create.get("ok"):
            return _board_payload(False, commands, effective_base_token, table_id)
        table_id = _extract_table_id(table_create.get("json")) or table_name
    table_ref = table_id or table_name
    pending_commands: list[list[str]] = []
    if user_open_id:
        pending_commands.append(_permission_grant_cmd(cli_bin, token, user_open_id))
    pending_commands.extend(
        _view_configuration_commands(
            cli_bin=cli_bin, identity=identity, base_token=token, table_id=table_ref,
            view_ids={view["name"]: view["name"] for view in views},
        )
    )
    for command in pending_commands:
        result = kanban.cli_transport.run_command(command, execute=execute, runner=runner)
        commands.append(result)
        if result.get("executed") and not result.get("ok"):
            return _board_payload(False, commands, effective_base_token, table_id)
    return _board_payload(True, commands, effective_base_token, table_id)


def _board_payload(
    ok: bool, commands: list[dict[str, Any]], base_token: str | None, table_id: str | None
) -> dict[str, Any]:
    return {
        "ok": ok,
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "base_token": base_token,
        "table_id": table_id,
        "commands": commands,
        "error": None if ok else next(
            (cli_transport.command_error(item) for item in commands if not item.get("ok")), "unknown"
        ),
    }


def _walk_first(payload: object, matcher: Callable[[dict[str, Any]], str | None]) -> str | None:
    if isinstance(payload, dict):
        matched = matcher(payload)
        if matched:
            return matched
        for value in payload.values():
            found = _walk_first(value, matcher)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _walk_first(value, matcher)
            if found:
                return found
    return None


def _extract_base_token(parsed: object) -> str | None:
    return _find_first_string(parsed, ("base_token", "app_token"))


def _extract_table_id(parsed: object) -> str | None:
    return _find_first_string(parsed, ("table_id", "id"), required_prefix="tbl")


def _find_first_string(
    payload: object, keys: tuple[str, ...], required_prefix: str | None = None
) -> str | None:
    return _walk_first(payload, lambda item: _string_id_from_dict(item, keys, prefix=required_prefix))


def _extract_created_record_id(parsed: object) -> str | None:
    return _find_first_record_id(parsed)


def _match_record_id(item: dict[str, Any]) -> str | None:
    ids = item.get("record_id_list")
    if isinstance(ids, list) and ids:
        return str(ids[0])
    return _string_id_from_dict(item, ("record_id", "id"), prefix="rec")


def _find_first_record_id(payload: object) -> str | None:
    return _walk_first(payload, _match_record_id)


def _extract_table_id_from_blocks(parsed: object, *, table_name: str | None = None) -> str | None:
    blocks = _extract_list_from_payload(parsed, ("blocks", "items", "data"))
    table_blocks = [
        item for item in blocks
        if isinstance(item, dict)
        and str(item.get("type") or item.get("resource_type") or "").lower()
        in {"table", "bitable_table", ""}
    ]
    if table_name:
        for item in table_blocks:
            if str(item.get("name") or item.get("block_name") or "").strip() == table_name:
                return _string_id_from_dict(item, ("id", "block_id", "table_id"), prefix="tbl")
    for item in table_blocks:
        table_id = _string_id_from_dict(item, ("id", "block_id", "table_id"), prefix="tbl")
        if table_id:
            return table_id
    return _extract_table_id(parsed)


def _extract_view_ids(parsed: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in _extract_list_from_payload(parsed, ("views", "items", "data")):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("view_name") or "").strip()
        view_id = (
            _string_id_from_dict(item, ("view_id", "id"), prefix="vew")
            or _string_id_from_dict(item, ("view_id", "id"), prefix=None)
        )
        if name and view_id:
            result[name] = view_id
    return result


def _extract_list_from_payload(payload: object, keys: tuple[str, ...]) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for container in (payload, payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if isinstance(value, list):
                return value
    return []


def _string_id_from_dict(
    item: Mapping[str, object], keys: tuple[str, ...], *, prefix: str | None
) -> str | None:
    for key in keys:
        value = item.get(key)
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if candidate and (prefix is None or candidate.startswith(prefix)):
            return candidate
    return None


def use_lark_kanban_board(
    *,
    config_path: Path,
    base_url: str | None = None,
    base_token: str | None = None,
    table_id: str | None = None,
    view_id: str | None = None,
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN,
    identity: str = "user",
) -> dict[str, Any]:
    from . import kanban

    parsed = kanban.parse_lark_base_url(base_url) if base_url else {}
    return kanban.save_lark_kanban_board_config(
        config_path,
        base_token=str(base_token or parsed.get("base_token") or "").strip(),
        table_id=str(table_id or parsed.get("table_id") or "").strip(),
        view_id=str(view_id or parsed.get("view_id") or DEFAULT_STATUS_QUEUE_VIEW).strip(),
        base_url=base_url,
        cli_bin=cli_bin,
        identity=identity,
    )


def setup_lark_kanban_board(
    *,
    config_path: Path,
    base_name: str,
    table_name: str = DEFAULT_TABLE_NAME,
    base_url: str | None = None,
    base_token: str | None = None,
    table_id: str | None = None,
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN,
    identity: str = "user",
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, Any]:
    from . import kanban

    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    existing = kanban.read_lark_kanban_local_config(config_path)
    raw_board = existing.get("board")
    existing_board: dict[str, Any] = raw_board if isinstance(raw_board, dict) else {}
    parsed_url = kanban.parse_lark_base_url(base_url) if base_url else {}
    effective_base_token = str(
        base_token or parsed_url.get("base_token") or existing_board.get("base_token") or ""
    ).strip()
    effective_table_id = str(
        table_id or parsed_url.get("table_id") or existing_board.get("table_id") or ""
    ).strip()
    raw_view_ids = existing_board.get("view_ids")
    view_ids: dict[str, str] = (
        {str(k): str(v) for k, v in raw_view_ids.items()} if isinstance(raw_view_ids, dict) else {}
    )

    preflight = _lark_kanban_preflight(cli_bin=cli_bin, identity=identity, runner=runner)
    commands.extend(preflight["commands"])
    if execute and not preflight["identity_available"]:
        return _setup_payload(
            False, execute, config_path, commands, preflight["warnings"],
            effective_base_token or None, effective_table_id or None,
            error=_identity_error(identity),
        )
    warnings.extend(preflight["warnings"])

    created_base = False
    created_table = False
    created_field_names: list[str] = []
    updated_field_names: list[str] = []
    config_payload: dict[str, Any] | None = None

    def save_usable_config() -> None:
        nonlocal config_payload
        if not execute or not effective_base_token or not effective_table_id:
            return
        config_payload = kanban.save_lark_kanban_board_config(
            config_path,
            base_token=effective_base_token, table_id=effective_table_id,
            view_id=view_ids.get(DEFAULT_STATUS_QUEUE_VIEW) or DEFAULT_STATUS_QUEUE_VIEW,
            base_url=base_url, base_name=base_name, table_name=table_name,
            cli_bin=cli_bin, identity=identity, view_ids=view_ids,
        )

    def setup_fail(error: str | None = None) -> dict[str, Any]:
        return _setup_payload(
            False, execute, config_path, commands, warnings,
            effective_base_token, effective_table_id, error=error,
        )

    def partial_enrichment_payload(command_result: dict[str, Any]) -> dict[str, Any]:
        error = cli_transport.command_error(command_result)
        warnings.append(f"usable board config saved; view enrichment skipped: {error}")
        return _setup_payload(
            True, execute, config_path, commands, warnings,
            effective_base_token, effective_table_id,
            config=config_payload, partial=True, enrichment_error=error,
        )

    def fail_or_partial(command_result: dict[str, Any]) -> dict[str, Any]:
        return partial_enrichment_payload(command_result) if config_payload else setup_fail()

    definitions = kanban.lark_kanban_field_definitions()
    fields_json = json.dumps(definitions, ensure_ascii=False)
    views = kanban.lark_kanban_views()
    views_json = json.dumps(views, ensure_ascii=False)

    if not effective_base_token:
        created_base = True
        create = kanban.cli_transport.run_command(
            _base_cmd(
                cli_bin, "+base-create", identity,
                "--name", base_name, "--table-name", table_name, "--fields", fields_json,
            ),
            execute=execute, runner=runner,
        )
        commands.append(create)
        if execute:
            if not create.get("ok"):
                return setup_fail()
            effective_base_token = _extract_base_token(create.get("json")) or ""
            effective_table_id = _extract_table_id(create.get("json")) or effective_table_id
            if not effective_base_token:
                return setup_fail("base-create did not return a usable Base token")
        else:
            effective_base_token = "<base-token-from-create>"

    if not effective_table_id:
        if created_base:
            blocks = kanban.cli_transport.run_command(
                _base_cmd(
                    cli_bin, "+base-block-list", identity, "--type", "table",
                    base_token=effective_base_token,
                ),
                execute=execute, runner=runner,
            )
            commands.append(blocks)
            if execute:
                if not blocks.get("ok"):
                    return setup_fail()
                effective_table_id = _extract_table_id_from_blocks(
                    blocks.get("json"), table_name=table_name
                ) or ""
                if not effective_table_id:
                    return setup_fail("base-block-list did not return a usable table id")
            else:
                effective_table_id = "<table-id-from-base-block-list>"
        else:
            created_table = True
            table_create = kanban.cli_transport.run_command(
                _table_create_cmd(
                    cli_bin, identity, effective_base_token, table_name, fields_json, views_json
                ),
                execute=execute, runner=runner,
            )
            commands.append(table_create)
            if execute:
                if not table_create.get("ok"):
                    return setup_fail()
                effective_table_id = _extract_table_id(table_create.get("json")) or ""
                if not effective_table_id:
                    return setup_fail("table-create did not return a usable table id")
            else:
                effective_table_id = "<table-id-from-table-create>"

    save_usable_config()

    if execute and not created_base and not created_table:
        field_list = kanban.cli_transport.run_command(
            _base_cmd(
                cli_bin, "+field-list", identity, "--format", "json", "--limit", "200",
                base_token=effective_base_token, table_id=effective_table_id,
            ),
            execute=True, runner=runner,
        )
        commands.append(field_list)
        if not field_list.get("ok"):
            return partial_enrichment_payload(field_list)
        for definition in kanban.issue_fix_surface.missing_field_definitions(
            field_list.get("json"), definitions
        ):
            field_create = kanban.cli_transport.run_command(
                _base_cmd(
                    cli_bin, "+field-create", identity,
                    "--json", json.dumps(definition, ensure_ascii=False),
                    base_token=effective_base_token, table_id=effective_table_id,
                ),
                execute=True, runner=runner,
            )
            commands.append(field_create)
            if not field_create.get("ok"):
                return partial_enrichment_payload(field_create)
            created_field_names.append(str(definition.get("name") or ""))
        for migration in kanban.issue_fix_surface.field_definition_migrations(
            field_list.get("json"), definitions
        ):
            field_update = kanban.cli_transport.run_command(
                _base_cmd(
                    cli_bin, "+field-update", identity,
                    "--field-id", str(migration["field_id"]),
                    "--json", json.dumps(migration["definition"], ensure_ascii=False),
                    "--yes",
                    base_token=effective_base_token, table_id=effective_table_id,
                ),
                execute=True, runner=runner,
            )
            commands.append(field_update)
            if not field_update.get("ok"):
                return partial_enrichment_payload(field_update)
            updated_field_names.append(str(migration["name"]))

    if execute:
        view_ids = _refresh_view_ids(
            cli_bin=cli_bin, identity=identity, base_token=effective_base_token,
            table_id=effective_table_id, commands=commands, runner=runner,
        )
        save_usable_config()

    missing_view_names = [view["name"] for view in views if view["name"] not in view_ids]
    if missing_view_names and (created_base or not created_table):
        view_create = kanban.cli_transport.run_command(
            _base_cmd(
                cli_bin, "+view-create", identity, "--json",
                json.dumps([v for v in views if v["name"] in missing_view_names], ensure_ascii=False),
                base_token=effective_base_token, table_id=effective_table_id,
            ),
            execute=execute, runner=runner,
        )
        commands.append(view_create)
        if execute and not view_create.get("ok"):
            return fail_or_partial(view_create)
        if execute:
            view_ids = _refresh_view_ids(
                cli_bin=cli_bin, identity=identity, base_token=effective_base_token,
                table_id=effective_table_id, commands=commands, runner=runner,
            )
            save_usable_config()

    if not view_ids:
        view_ids = {view["name"]: view["name"] for view in views}
    for command in _view_configuration_commands(
        cli_bin=cli_bin, identity=identity, base_token=effective_base_token,
        table_id=effective_table_id, view_ids=view_ids,
    ):
        result = kanban.cli_transport.run_command(command, execute=execute, runner=runner)
        commands.append(result)
        if execute and not result.get("ok"):
            return fail_or_partial(result)

    save_usable_config()
    board_summary = config_payload.get("board", {}) if config_payload else {}
    return {
        "ok": True,
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "execute": execute,
        "config_path": str(config_path),
        "created_base": created_base,
        "created_table": created_table,
        "created_fields": created_field_names,
        "updated_fields": updated_field_names,
        "base_token": effective_base_token,
        "table_id": effective_table_id,
        "view_ids": view_ids,
        "operator_card_fields": OPERATOR_CARD_FIELDS,
        "commands": commands,
        "warnings": warnings,
        "config": config_payload,
        "next_commands": kanban._next_lark_kanban_commands(
            board_summary if isinstance(board_summary, dict) else {}
        ),
    }


def _setup_payload(
    ok: bool,
    execute: bool,
    config_path: Path,
    commands: list[dict[str, Any]],
    warnings: list[str],
    base_token: str | None,
    table_id: str | None,
    *,
    error: str | None = None,
    config: dict[str, Any] | None = None,
    partial: bool = False,
    enrichment_error: str | None = None,
) -> dict[str, Any]:
    from . import kanban

    payload: dict[str, Any] = {
        "ok": ok,
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "execute": execute,
        "config_path": str(config_path),
        "base_token": base_token,
        "table_id": table_id,
        "commands": commands,
        "warnings": warnings,
    }
    if config:
        payload["config"] = config
        board_summary = config.get("board", {})
        payload["next_commands"] = kanban._next_lark_kanban_commands(
            board_summary if isinstance(board_summary, dict) else {}
        )
    if partial:
        payload["partial"] = True
        payload["enrichment_ok"] = False
        payload["enrichment_error"] = enrichment_error or next(
            (cli_transport.command_error(item) for item in commands if not item.get("ok")), "unknown"
        )
    elif not ok:
        payload["error"] = error or next(
            (cli_transport.command_error(item) for item in commands if not item.get("ok")), "unknown"
        )
    return payload


def _refresh_view_ids(
    *,
    cli_bin: str,
    identity: str,
    base_token: str,
    table_id: str,
    commands: list[dict[str, Any]],
    runner: cli_transport.CommandRunner,
) -> dict[str, str]:
    from . import kanban

    view_list = kanban.cli_transport.run_command(
        _base_cmd(cli_bin, "+view-list", identity, base_token=base_token, table_id=table_id),
        execute=True, runner=runner,
    )
    commands.append(view_list)
    return _extract_view_ids(view_list.get("json")) if view_list.get("ok") else {}


def _view_configuration_commands(
    *,
    cli_bin: str,
    identity: str,
    base_token: str,
    table_id: str,
    view_ids: dict[str, str],
) -> list[list[str]]:
    return issue_fix_surface.view_configuration_commands(
        cli_bin=cli_bin, identity=identity, base_token=base_token, table_id=table_id,
        view_ids=view_ids, worker_view_name=DEFAULT_STATUS_QUEUE_VIEW,
        todo_statuses=[STATUS_TODO, STATUS_CLAIMED], user_gate_status=STATUS_USER_GATE,
        operator_card_fields=OPERATOR_CARD_FIELDS,
    )


def _lark_kanban_schema_check(parsed: object) -> dict[str, Any]:
    from . import kanban

    definitions = kanban.lark_kanban_field_definitions()
    missing = kanban.issue_fix_surface.missing_field_definitions(parsed, definitions)
    migrations = kanban.issue_fix_surface.field_definition_migrations(parsed, definitions)
    missing_names = [str(item.get("name") or "") for item in missing]
    incompatible_names = [str(item.get("name") or "") for item in migrations]
    migration_required = bool(missing_names or incompatible_names)
    return {
        "ok": not migration_required,
        "status": "stale" if migration_required else "ready",
        "expected_field_count": len(definitions),
        "observed_field_count": len(kanban.issue_fix_surface.extract_field_names(parsed)),
        "missing_fields": missing_names,
        "incompatible_fields": incompatible_names,
        "migration_required": migration_required,
        "remediation_command": "loopx lark-kanban setup --execute" if migration_required else None,
    }


def _inspect_lark_kanban_schema(
    config: LarkKanbanConfig,
    *,
    commands: list[dict[str, Any]],
    runner: cli_transport.CommandRunner,
) -> dict[str, Any]:
    from . import kanban

    field_list = kanban.cli_transport.run_command(
        _base_cmd(
            config.cli_bin, "+field-list", config.identity, "--format", "json", "--limit", "200",
            base_token=config.base_token, table_id=config.table_id,
        ),
        execute=True, runner=runner,
    )
    commands.append(field_list)
    if not field_list.get("ok"):
        return {
            "ok": False,
            "status": "unavailable",
            "migration_required": None,
            "missing_fields": [],
            "incompatible_fields": [],
            "remediation_command": "loopx lark-kanban doctor --require-board",
            "error": cli_transport.command_error(field_list),
        }
    return _lark_kanban_schema_check(field_list.get("json"))


def _schema_check_error(schema_check: Mapping[str, object]) -> str:
    if schema_check.get("status") == "stale":
        raw_missing = schema_check.get("missing_fields")
        raw_inc = schema_check.get("incompatible_fields")
        missing = ", ".join(str(x) for x in raw_missing) if isinstance(raw_missing, list) and raw_missing else "none"
        incompatible = ", ".join(str(x) for x in raw_inc) if isinstance(raw_inc, list) and raw_inc else "none"
        return (
            "Lark Kanban schema is stale "
            f"(missing: {missing}; incompatible: {incompatible}); run "
            f"`{schema_check['remediation_command']}` before sync"
        )
    return (
        "Lark Kanban schema could not be inspected before sync: "
        f"{schema_check.get('error') or 'unknown error'}"
    )


def lark_kanban_doctor(
    *,
    config_path: Path,
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN,
    identity: str = "user",
    check_board: bool = True,
    require_board: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, Any]:
    from . import kanban

    commands: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    schema_check: dict[str, Any] | None = None
    preflight = _lark_kanban_preflight(cli_bin=cli_bin, identity=identity, runner=runner)
    commands.extend(preflight["commands"])
    for warning in preflight["warnings"]:
        issues.append({"severity": "warning", "message": warning})
    if not preflight["cli_ok"]:
        issues.append({"severity": "error", "message": f"{cli_bin} is not runnable"})
    if not preflight["identity_available"]:
        issues.append({"severity": "error", "message": _identity_error(identity)})

    config_payload = kanban.read_lark_kanban_local_config(config_path)
    board_config = kanban.lark_kanban_config_from_payload(config_payload)
    if not config_payload.get("exists"):
        issues.append(
            {
                "severity": "error" if require_board else "warning",
                "message": f"no local board config at {config_path}; run lark-kanban setup or use",
            }
        )
    elif not board_config:
        issues.append({"severity": "error", "message": f"local board config is incomplete: {config_path}"})

    if check_board and board_config and preflight["identity_available"]:
        for command in (
            _base_cmd(board_config.cli_bin, "+base-get", board_config.identity, base_token=board_config.base_token),
            _base_cmd(board_config.cli_bin, "+view-list", board_config.identity, base_token=board_config.base_token, table_id=board_config.table_id),
        ):
            result = kanban.cli_transport.run_command(command, execute=True, runner=runner)
            commands.append(result)
            if not result.get("ok"):
                issues.append({"severity": "error", "message": cli_transport.command_error(result)})
        if not any(issue["severity"] == "error" for issue in issues):
            schema_check = _inspect_lark_kanban_schema(board_config, commands=commands, runner=runner)
            if not schema_check.get("ok"):
                issues.append({"severity": "error", "message": _schema_check_error(schema_check)})

    return {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "schema_version": "loopx_lark_kanban_doctor_v0",
        "config_path": str(config_path),
        "identity": identity,
        "issues": issues,
        "schema_check": schema_check,
        "config": config_payload,
        "commands": commands,
    }


def _lark_kanban_preflight(
    *,
    cli_bin: str,
    identity: str,
    runner: cli_transport.CommandRunner,
) -> dict[str, Any]:
    from . import kanban

    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    version = kanban.cli_transport.run_command([cli_bin, "--version"], execute=True, runner=runner)
    commands.append(version)
    cli_ok = bool(version.get("ok"))
    version_tuple = _version_tuple(str(version.get("stdout") or ""))
    if cli_ok and version_tuple and version_tuple < (1, 0, 56):
        warnings.append("lark-cli should be upgraded to at least 1.0.56 for setup visible-field support")
    auth = kanban.cli_transport.run_command([cli_bin, "auth", "status"], execute=True, runner=runner)
    commands.append(auth)
    identity_available = _identity_available(auth.get("json"), identity)
    for subcommand in ("+base-create", "+base-block-list", "+view-list", "+view-set-group", "+view-set-visible-fields"):
        help_command = [cli_bin, "base", subcommand, "--help"]
        result = kanban.cli_transport.run_command(help_command, execute=True, runner=runner)
        commands.append(result)
        if not result.get("ok"):
            warnings.append(f"missing lark-cli shortcut: {shlex.join(help_command[2:-1])}")
    return {
        "commands": commands,
        "warnings": warnings,
        "cli_ok": cli_ok,
        "identity_available": identity_available,
    }


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.search(r"\d+(?:\.\d+)+", text)
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def _identity_available(parsed: object, identity: str) -> bool:
    if not isinstance(parsed, dict):
        return False
    raw_identities = parsed.get("identities")
    identities: dict[str, Any] = raw_identities if isinstance(raw_identities, dict) else {}
    if identity == "auto":
        active = str(parsed.get("identity") or "").strip()
        active_status = identities.get(active)
        if active and isinstance(active_status, dict):
            return bool(active_status.get("available"))
        return any(isinstance(item, dict) and bool(item.get("available")) for item in identities.values())
    status = identities.get(identity)
    return isinstance(status, dict) and bool(status.get("available"))


def _identity_error(identity: str) -> str:
    if identity == "user":
        return "lark-cli user identity is unavailable; run `lark-cli auth login --domain base --recommend`"
    return f"lark-cli identity {identity!r} is unavailable; run `lark-cli auth status`"
