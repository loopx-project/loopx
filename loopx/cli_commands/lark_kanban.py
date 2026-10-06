from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from ..extensions.lark import LARK_EXTENSION_ID, LARK_PROJECTION_SINK_PERMISSION
from ..extensions.lark.presentation.kanban import (
    DEFAULT_AGENT_ID,
    DEFAULT_CLI_BIN,
    DEFAULT_STATUS_QUEUE_VIEW,
    DEFAULT_TABLE_NAME,
    LarkKanbanConfig,
    build_create_board_plan,
    compact_lark_kanban_sync_receipt,
    create_lark_kanban_board,
    default_lark_kanban_config_path,
    lark_kanban_doctor,
    lark_kanban_feasibility_cases,
    lark_kanban_heartbeat,
    lark_kanban_operator_card_fields,
    lark_kanban_schema_payload,
    lark_kanban_config_from_payload,
    lark_kanban_ux_task,
    read_lark_kanban_local_config,
    render_lark_kanban_markdown,
    sample_lark_kanban_task,
    seed_lark_kanban_records,
    seed_lark_kanban_task,
    setup_lark_kanban_board,
    sync_loopx_projection_to_lark_kanban,
    sync_loopx_todos_to_lark_kanban,
    use_lark_kanban_board,
)
from ..extensions.lark.presentation.explore_results import (
    sync_issue_fix_explore_on_material_change,
)
from ..extensions.lark.presentation.sink_visibility import SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITIES
from ..extensions.runtime import (
    default_extension_state_file,
    resolve_extension_activation,
)
from ..history import load_registry
from ..paths import resolve_runtime_root


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
OutputFormat = Callable[[argparse.Namespace], str]


def register_lark_kanban_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "lark-kanban",
        help="Use a Feishu/Lark Base Kanban board as a LoopX control-plane adapter.",
    )
    sub = parser.add_subparsers(dest="lark_kanban_command", required=True)

    schema = sub.add_parser("schema", help="Print the task-board schema and LoopX mapping.")
    add_subcommand_format(schema)
    schema.add_argument("--table-name", default=DEFAULT_TABLE_NAME)

    config = sub.add_parser("config", help="Print the local Lark Kanban board config.")
    add_subcommand_format(config)
    _add_local_config_args(config)

    use = sub.add_parser("use", help="Store an existing shared Lark Base board for this project.")
    add_subcommand_format(use)
    _add_local_config_args(use)
    use.add_argument(
        "--base-url",
        help="Shared Lark Base URL. table/view query params are reused when present.",
    )
    use.add_argument("--base-token", help="Base token when --base-url is not used.")
    use.add_argument("--table-id", help="Table id when --base-url does not include table=.")
    use.add_argument("--view-id")
    use.add_argument("--cli-bin", default=DEFAULT_CLI_BIN)
    use.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])

    setup = sub.add_parser(
        "setup",
        help="Create or reuse the project Lark Kanban board. Dry-run unless --execute.",
    )
    add_subcommand_format(setup)
    _add_local_config_args(setup)
    setup.add_argument("--base-name", default="LoopX Kanban POC")
    setup.add_argument("--table-name", default=DEFAULT_TABLE_NAME)
    setup.add_argument("--base-url", help="Reuse an existing shared Base URL.")
    setup.add_argument(
        "--base-token",
        help="Reuse an existing Base token instead of creating a new Base.",
    )
    setup.add_argument(
        "--table-id",
        help="Reuse an existing table id instead of creating/configuring a table.",
    )
    setup.add_argument("--cli-bin", default=DEFAULT_CLI_BIN)
    setup.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])
    setup.add_argument(
        "--execute",
        action="store_true",
        help="Actually run lark-cli write commands and save config.",
    )

    doctor = sub.add_parser("doctor", help="Diagnose lark-cli, auth, local config, and board reachability.")
    add_subcommand_format(doctor)
    _add_local_config_args(doctor)
    doctor.add_argument("--cli-bin", default=DEFAULT_CLI_BIN)
    doctor.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])
    doctor.add_argument("--no-board-check", action="store_true", help="Skip remote Base read checks.")
    doctor.add_argument(
        "--require-board",
        action="store_true",
        help="Fail if no local board config exists.",
    )

    plan = sub.add_parser("plan-create", help="Print lark-cli commands for creating the board.")
    add_subcommand_format(plan)
    _add_create_args(plan)

    create = sub.add_parser("create-board", help="Create the Lark Base board. Dry-run unless --execute.")
    add_subcommand_format(create)
    _add_create_args(create)
    create.add_argument("--execute", action="store_true", help="Actually run lark-cli commands.")

    seed = sub.add_parser("seed-task", help="Create one sample task row. Dry-run unless --execute.")
    add_subcommand_format(seed)
    _add_local_config_args(seed)
    _add_lark_target_args(seed)
    seed.add_argument("--goal-id", default="loopx-lark-kanban-poc")
    seed.add_argument("--worker-command", default="")
    seed.add_argument("--workdir", default="")
    seed.add_argument("--execute", action="store_true", help="Actually upsert the sample record.")

    cases = sub.add_parser(
        "seed-cases",
        help="Seed the UX optimization task plus feasibility cases. Dry-run unless --execute.",
    )
    add_subcommand_format(cases)
    _add_local_config_args(cases)
    _add_lark_target_args(cases)
    cases.add_argument("--goal-id", default="loopx-lark-kanban-ux")
    cases.add_argument("--worker-command", default="")
    cases.add_argument("--workdir", default="")
    cases.add_argument("--execute", action="store_true", help="Actually upsert the records.")

    sync = sub.add_parser(
        "sync-loopx-todos",
        help=(
            "Sync a goal's active LoopX todos and derived issue-fix outcomes into "
            "the configured board. Dry-run unless --execute."
        ),
    )
    add_subcommand_format(sync)
    _add_local_config_args(sync)
    _add_lark_target_args(sync)
    sync.add_argument("--goal-id", required=True)
    sync.add_argument("--agent-id", help="Only sync todos claimed by or blocking this agent id.")
    sync.add_argument("--project")
    sync.add_argument("--state-file")
    sync.add_argument("--include-done", action="store_true")
    sync.add_argument(
        "--limit",
        type=int,
        default=50,
        help=(
            "Maximum active todo rows to sync. Derived issue-fix outcomes always "
            "use the complete source projection."
        ),
    )
    sync.add_argument(
        "--include-command-details",
        action="store_true",
        help=(
            "Include full generated commands, provider stdout/stderr, parsed JSON, "
            "and record values. The default returns a compact operator receipt."
        ),
    )
    sync.add_argument(
        "--execute",
        action="store_true",
        help="Actually upsert records and remember record ids.",
    )

    projection = sub.add_parser(
        "sync-projection",
        help="Sync a LoopX status/quota/frontstage projection into the configured board. Dry-run unless --execute.",
    )
    add_subcommand_format(projection)
    _add_local_config_args(projection)
    _add_lark_target_args(projection)
    projection.add_argument(
        "--projection-file",
        required=True,
        help="Projection JSON file, inline object, or '-' for stdin.",
    )
    projection.add_argument(
        "--goal-id",
        help="Only sync this goal id; defaults from the projection payload.",
    )
    projection.add_argument(
        "--agent-id",
        help="Only sync rows claimed by, blocking, or projected for this agent id.",
    )
    projection.add_argument("--source-id", help="Stable source namespace used in synthetic row ids.")
    projection.add_argument(
        "--sink-visibility",
        choices=sorted(SINK_VISIBILITIES),
        default=SINK_VISIBILITY_OWNER_ONLY,
        help="Use shared to redact local paths, private links, and external ids before writing projection rows.",
    )
    projection.add_argument("--include-done", action="store_true")
    projection.add_argument("--limit", type=int, default=50)
    projection.add_argument(
        "--reconcile-source",
        action="store_true",
        help=(
            "Preview or execute orphan retirement only inside the projection's "
            "stable source namespace. Requires --source-snapshot-complete and "
            "--include-done; agent-filtered or row-limited inputs are rejected."
        ),
    )
    projection.add_argument(
        "--source-snapshot-complete",
        action="store_true",
        help="Attest that the projection contains the complete source namespace.",
    )
    projection.add_argument(
        "--execute",
        action="store_true",
        help="Actually upsert records and remember record ids.",
    )

    heartbeat = sub.add_parser(
        "heartbeat",
        help="Poll one Kanban task, claim it, optionally run a worker command, and write evidence.",
    )
    add_subcommand_format(heartbeat)
    _add_local_config_args(heartbeat)
    _add_lark_target_args(heartbeat)
    heartbeat.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    heartbeat.add_argument(
        "--fixture",
        help="Optional record-list JSON fixture. When set, no list command is run.",
    )
    heartbeat.add_argument(
        "--worker-command",
        help="Override the row's Worker Command for this heartbeat.",
    )
    heartbeat.add_argument(
        "--allow-command-prefix",
        action="append",
        default=[],
        help="Allowed prefix for --execute-worker, e.g. python3 or codex exec. Repeatable.",
    )
    heartbeat.add_argument(
        "--execute-lark",
        action="store_true",
        help="Actually call lark-cli for record list/claim/writeback.",
    )
    heartbeat.add_argument(
        "--execute-worker",
        action="store_true",
        help="Actually run the worker command after claim. Requires --allow-command-prefix.",
    )
    heartbeat.add_argument(
        "--complete-on-success",
        action="store_true",
        help="Set Status=Done after successful worker execution. Default is Review.",
    )
    heartbeat.add_argument(
        "--worker-timeout-seconds",
        type=float,
        default=600.0,
        help="Timeout for the worker command.",
    )


def _add_local_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config-path",
        help="Local board config path. Defaults beside the LoopX registry.",
    )


def _add_create_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-name", default="LoopX Lark Kanban Control Plane POC")
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME)
    parser.add_argument(
        "--base-token",
        help="Use an existing Base token instead of creating a new Base.",
    )
    parser.add_argument(
        "--user-open-id",
        help="Grant this user full_access to a newly created/existing Base.",
    )
    parser.add_argument("--cli-bin", default=DEFAULT_CLI_BIN)
    parser.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])


def _add_lark_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-token")
    parser.add_argument("--table-id")
    parser.add_argument("--view-id")
    parser.add_argument("--cli-bin", default=DEFAULT_CLI_BIN)
    parser.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])


def _target_config(args: argparse.Namespace, *, config_path: Path) -> LarkKanbanConfig:
    stored = lark_kanban_config_from_payload(read_lark_kanban_local_config(config_path))
    base_token = args.base_token or (stored.base_token if stored else None)
    table_id = args.table_id or (stored.table_id if stored else None)
    view_id = args.view_id or (stored.view_id if stored else DEFAULT_STATUS_QUEUE_VIEW)
    cli_bin = args.cli_bin or (stored.cli_bin if stored else DEFAULT_CLI_BIN)
    identity = args.identity or (stored.identity if stored else "user")
    if not base_token or not table_id:
        raise ValueError("lark-kanban target requires --base-token/--table-id or local config from setup/use")
    return LarkKanbanConfig(
        **{"base_" + "token": base_token},
        table_id=table_id,
        view_id=view_id,
        cli_bin=cli_bin,
        identity=identity,
    )


def handle_lark_kanban_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.command != "lark-kanban":
        return None
    fmt = output_format(args)
    config_path = (
        Path(args.config_path).expanduser()
        if getattr(args, "config_path", None)
        else default_lark_kanban_config_path(registry_path)
    )
    try:
        activation_runtime_root = (
            Path(runtime_root_arg).expanduser()
            if runtime_root_arg is not None
            else resolve_runtime_root(load_registry(registry_path), None)
        )
        activation = resolve_extension_activation(
            LARK_EXTENSION_ID,
            state_file=default_extension_state_file(activation_runtime_root),
            required_permissions=(LARK_PROJECTION_SINK_PERMISSION,),
        )
        if args.lark_kanban_command == "schema":
            payload = lark_kanban_schema_payload(table_name=args.table_name)
        elif args.lark_kanban_command == "config":
            payload = read_lark_kanban_local_config(config_path)
            payload["next_commands"] = []
            if isinstance(payload.get("board"), dict):
                payload["next_commands"] = [
                    "loopx lark-kanban doctor",
                    "loopx lark-kanban sync-loopx-todos --goal-id <goal-id> --execute",
                    "loopx lark-kanban heartbeat --execute-lark",
                ]
        elif args.lark_kanban_command == "use":
            payload = use_lark_kanban_board(
                config_path=config_path,
                base_url=args.base_url,
                **{"base_" + "token": args.base_token},
                table_id=args.table_id,
                view_id=args.view_id,
                cli_bin=args.cli_bin,
                identity=args.identity,
            )
        elif args.lark_kanban_command == "setup":
            payload = setup_lark_kanban_board(
                config_path=config_path,
                base_name=args.base_name,
                table_name=args.table_name,
                base_url=args.base_url,
                **{"base_" + "token": args.base_token},
                table_id=args.table_id,
                cli_bin=args.cli_bin,
                identity=args.identity,
                execute=bool(args.execute),
            )
        elif args.lark_kanban_command == "doctor":
            payload = lark_kanban_doctor(
                config_path=config_path,
                cli_bin=args.cli_bin,
                identity=args.identity,
                check_board=not bool(args.no_board_check),
                require_board=bool(args.require_board),
            )
        elif args.lark_kanban_command == "plan-create":
            commands = build_create_board_plan(
                base_name=args.base_name,
                table_name=args.table_name,
                cli_bin=args.cli_bin,
                identity=args.identity,
                **{"base_" + "token": args.base_token},
                user_open_id=args.user_open_id,
            )
            payload = {
                "ok": True,
                "schema_version": "loopx_lark_kanban_create_plan_v0",
                "execute": False,
                "commands": [
                    {
                        "command": " ".join(_shell_quote(part) for part in command),
                        "executed": False,
                        "ok": True,
                    }
                    for command in commands
                ],
            }
        elif args.lark_kanban_command == "create-board":
            payload = create_lark_kanban_board(
                base_name=args.base_name,
                table_name=args.table_name,
                cli_bin=args.cli_bin,
                identity=args.identity,
                **{"base_" + "token": args.base_token},
                user_open_id=args.user_open_id,
                execute=bool(args.execute),
            )
            payload["execute"] = bool(args.execute)
        elif args.lark_kanban_command == "seed-task":
            task = sample_lark_kanban_task(
                goal_id=args.goal_id,
                worker_command=args.worker_command,
                workdir=args.workdir,
            )
            payload = seed_lark_kanban_task(
                _target_config(args, config_path=config_path),
                task=task,
                execute=bool(args.execute),
            )
            payload["execute"] = bool(args.execute)
        elif args.lark_kanban_command == "seed-cases":
            task = lark_kanban_ux_task(
                goal_id=args.goal_id,
                worker_command=args.worker_command,
                workdir=args.workdir,
            )
            records = [task] + lark_kanban_feasibility_cases(
                goal_id=f"{args.goal_id}-cases",
                workdir=args.workdir,
            )
            payload = seed_lark_kanban_records(
                _target_config(args, config_path=config_path),
                records=records,
                execute=bool(args.execute),
            )
            payload["execute"] = bool(args.execute)
            payload["operator_card_fields"] = lark_kanban_operator_card_fields()
        elif args.lark_kanban_command == "sync-loopx-todos":
            target_config = _target_config(args, config_path=config_path)
            payload = sync_loopx_todos_to_lark_kanban(
                target_config,
                registry_path=registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                config_path=config_path,
                project=Path(args.project).expanduser() if args.project else None,
                state_file=Path(args.state_file).expanduser() if args.state_file else None,
                include_done=bool(args.include_done),
                limit=args.limit,
                execute=bool(args.execute),
            )
            try:
                if not payload.get("ok"):
                    raise RuntimeError("primary Lark Kanban todo sync did not complete")
                explore_sync = sync_issue_fix_explore_on_material_change(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    project=Path(args.project).expanduser() if args.project else None,
                    state_file=Path(args.state_file).expanduser() if args.state_file else None,
                    execute=bool(args.execute),
                )
                projection_result = (
                    explore_sync.get("projection") if isinstance(explore_sync.get("projection"), dict) else {}
                )
                lark_sync = explore_sync.get("lark_sync") if isinstance(explore_sync.get("lark_sync"), dict) else {}
                visual_sync = (
                    explore_sync.get("visual_sync") if isinstance(explore_sync.get("visual_sync"), dict) else {}
                )
                payload["issue_fix_explore_projection"] = {
                    "ok": projection_result.get("ok"),
                    "applicable": projection_result.get("applicable"),
                    "material_change": projection_result.get("material_change"),
                    "candidate_event_count": projection_result.get("candidate_event_count"),
                    "material_event_count": projection_result.get("material_event_count"),
                    "appended_event_count": projection_result.get("appended_event_count"),
                    "semantic_digest": projection_result.get("semantic_digest"),
                    "counts": projection_result.get("counts"),
                    "source_runtime_route": projection_result.get(
                        "source_runtime_route"
                    ),
                }
                explore_status = str(explore_sync.get("status") or "")
                payload["issue_fix_explore_lark_sync"] = {
                    "ok": explore_sync.get("ok"),
                    "status": explore_sync.get("status"),
                    "needs_sync": explore_sync.get("needs_sync"),
                    "needs_row_sync": explore_sync.get("needs_row_sync"),
                    "needs_visual_sync": explore_sync.get("needs_visual_sync"),
                    "semantic_digest": explore_sync.get("semantic_digest"),
                    "canonical_rows_status": (
                        "source_projection_regression_blocked"
                        if explore_status == "source_projection_regression_blocked"
                        else "synced"
                        if lark_sync.get("ok")
                        else "unchanged"
                        if not explore_sync.get("needs_row_sync")
                        else "sync_failed"
                    ),
                    "written_rows": lark_sync.get("written_rows"),
                    "skipped_rows": lark_sync.get("skipped_rows"),
                    "duplicate_remote_rows": lark_sync.get("duplicate_remote_rows"),
                    "visual_status": (
                        "source_projection_regression_blocked"
                        if explore_status == "source_projection_regression_blocked"
                        else visual_sync.get("status")
                        or (
                            "unchanged"
                            if not explore_sync.get("needs_visual_sync")
                            else "publish_failed"
                        )
                    ),
                    "visual_published": visual_sync.get("published"),
                    "visual_graph_counts": visual_sync.get("graph_counts"),
                    "source_runtime_route": explore_sync.get(
                        "source_runtime_route"
                    ),
                    "source_projection_guard": explore_sync.get(
                        "source_projection_guard"
                    ),
                    "external_write_performed": explore_sync.get(
                        "external_write_performed"
                    ),
                    "required_action": explore_sync.get("required_action"),
                    "error": (
                        explore_sync.get("error")
                        or lark_sync.get("error")
                        or visual_sync.get("error")
                    ),
                }
                payload["ok"] = bool(payload.get("ok")) and bool(explore_sync.get("ok"))
            except Exception as exc:
                payload["ok"] = False
                payload["issue_fix_explore_projection"] = {
                    "ok": False,
                    "status": "skipped" if payload.get("error") else "failed",
                    "error": str(exc),
                }
            payload = compact_lark_kanban_sync_receipt(
                payload,
                include_command_details=bool(args.include_command_details),
            )
        elif args.lark_kanban_command == "sync-projection":
            payload = sync_loopx_projection_to_lark_kanban(
                _target_config(args, config_path=config_path),
                projection=_load_fixture(args.projection_file),
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                source_id=args.source_id,
                sink_visibility=args.sink_visibility,
                reconcile_source=bool(args.reconcile_source),
                source_snapshot_complete=bool(args.source_snapshot_complete),
                config_path=config_path,
                include_done=bool(args.include_done),
                limit=args.limit,
                execute=bool(args.execute),
            )
        elif args.lark_kanban_command == "heartbeat":
            fixture = _load_fixture(args.fixture) if args.fixture else None
            payload = lark_kanban_heartbeat(
                _target_config(args, config_path=config_path),
                agent_id=args.agent_id,
                fixture=fixture,
                worker_command=args.worker_command,
                execute_lark=bool(args.execute_lark),
                execute_worker=bool(args.execute_worker),
                complete_on_success=bool(args.complete_on_success),
                allowed_command_prefixes=args.allow_command_prefix,
                worker_timeout_seconds=args.worker_timeout_seconds,
            )
        else:
            raise ValueError(f"unknown lark-kanban command: {args.lark_kanban_command}")
        if payload.get("ok"):
            payload["extension_activation"] = activation
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_lark_kanban_error_v0",
            "error": str(exc),
        }
    print_payload(payload, fmt, render_lark_kanban_markdown)
    return 0 if payload.get("ok") else 1


def _load_fixture(path: str) -> dict[str, object]:
    stripped = path.lstrip()
    if path == "-":
        raw = sys.stdin.read()
    elif stripped.startswith("{"):
        raw = path
    else:
        raw = Path(path).expanduser().read_text(encoding="utf-8")
    if len(raw) > 1_048_576:
        raise ValueError("fixture JSON exceeds the 1 MiB limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("fixture must be a JSON object")
    return payload


def _shell_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)
