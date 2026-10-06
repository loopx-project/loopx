from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from ..capabilities.explore.result_log import NODE_STATUSES
from ..extensions.lark.presentation.explore_results import (
    DEFAULT_EXPLORE_BASE_NAME,
    EXPLORE_TABLE_KEYS,
    LarkExploreConfig,
    build_explore_result_card,
    configure_lark_explore_visual_sink,
    explore_visual_semantic_digest,
    lark_explore_config_from_payload,
    read_lark_explore_local_config,
    setup_lark_explore_board,
    sync_explore_results_to_lark,
    sync_explore_visual_to_lark,
    sync_explore_visuals_to_lark,
    write_lark_explore_local_config,
)
from ..extensions.lark.presentation.explore_singleflight import (
    explore_feishu_sync_busy_packet,
    explore_feishu_sync_singleflight,
)
from ..extensions.lark.presentation.kanban import DEFAULT_CLI_BIN
from ..extensions.lark.presentation.sink_visibility import SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITIES


SubcommandArg = Callable[[argparse.ArgumentParser], None]
ProjectionFor = Callable[..., dict[str, object]]

FEISHU_SINK_EXPLORE_COMMANDS = frozenset(
    {"feishu-setup", "feishu-visual-configure", "feishu-sync", "feishu-card"}
)


def register_explore_feishu_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: SubcommandArg,
    add_config_path_arg: SubcommandArg,
    add_projection_limit_args: SubcommandArg,
) -> None:
    setup = subparsers.add_parser(
        "feishu-setup",
        help="Create the Nodes/Edges/Findings Lark Base tables. Dry-run unless --execute.",
    )
    add_subcommand_format(setup)
    add_config_path_arg(setup)
    setup.add_argument("--base-name", default=DEFAULT_EXPLORE_BASE_NAME)
    setup.add_argument("--base-url", help="Reuse an existing shared Base URL.")
    setup.add_argument("--base-token", help="Reuse an existing Base token.")
    setup.add_argument("--cli-bin", default=DEFAULT_CLI_BIN)
    setup.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])
    setup.add_argument("--execute", action="store_true", help="Actually run lark-cli write commands.")

    visual = subparsers.add_parser(
        "feishu-visual-configure",
        help="Configure an optional owner-facing Explore whiteboard. Dry-run unless --execute.",
    )
    add_subcommand_format(visual)
    add_config_path_arg(visual)
    visual.add_argument(
        "--whiteboard-token",
        help=(
            "Existing Stage 01 whiteboard token. Optional when --docx-token "
            "is set; missing stage boards are created in the document."
        ),
    )
    visual.add_argument("--docx-token")
    visual.add_argument(
        "--status",
        dest="visual_statuses",
        action="append",
        choices=sorted(NODE_STATUSES),
        default=[],
    )
    visual.add_argument("--tag", dest="visual_tags", action="append", default=[])
    visual.add_argument(
        "--projection-mode",
        choices=[
            "canonical_filtered",
            "issue_fix_two_lane",
            "canonical_full",
            "executive_auto",
        ],
    )
    visual.add_argument(
        "--view-role",
        choices=["canonical", "executive"],
        help="Configure one role in a same-source dual-view presentation.",
    )
    visual.add_argument(
        "--include-ancestors",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    visual.add_argument("--mermaid-node-limit", type=int, default=100)
    visual.add_argument(
        "--board-style",
        choices=["auto_flow", "semantic_lane_columns"],
        help=(
            "Whiteboard layout style. auto_flow uses Mermaid's generic graph "
            "layout; semantic_lane_columns fixes semantic lanes into "
            "left/right columns while retaining real directed edges. Omit to "
            "preserve an existing role's style; new roles default to auto_flow."
        ),
    )
    visual.add_argument(
        "--stage-capacity",
        type=int,
        default=14,
        help="Maximum topology nodes per Evidence Stage whiteboard (10-20).",
    )
    visual.add_argument(
        "--stage-whiteboard-token",
        action="append",
        default=[],
        help=(
            "Existing whiteboard token for one Evidence Stage, in stage order. "
            "Repeat for multiple stages; the primary --whiteboard-token remains "
            "the Stage 01 compatibility fallback."
        ),
    )
    visual.add_argument(
        "--auto-create",
        action="store_true",
        help=(
            "Create the hosting document and both overview boards "
            "(executive topology + canonical global) on first run; stage boards "
            "are created by feishu-sync. No manual whiteboard tokens needed."
        ),
    )
    visual.add_argument("--execute", action="store_true")

    sync = subparsers.add_parser(
        "feishu-sync",
        help="Upsert the goal's result projection into the Lark board. Dry-run unless --execute.",
    )
    add_subcommand_format(sync)
    add_config_path_arg(sync)
    sync.add_argument("--goal-id", required=True)
    add_projection_limit_args(sync)
    sync.add_argument("--base-token")
    for table_key in EXPLORE_TABLE_KEYS:
        sync.add_argument(f"--table-id-{table_key}", dest=f"table_id_{table_key}")
    sync.add_argument("--cli-bin")
    sync.add_argument("--as", dest="identity", default="user", choices=["bot", "user", "auto"])
    sync.add_argument(
        "--sink-visibility",
        choices=sorted(SINK_VISIBILITIES),
        default=SINK_VISIBILITY_OWNER_ONLY,
        help="Use shared to redact private links and external ids before writing rows.",
    )
    sync.add_argument(
        "--execute",
        action="store_true",
        help="Actually upsert records and remember record ids.",
    )

    card = subparsers.add_parser(
        "feishu-card",
        help="Build the result-card content for a gateway to send or update.",
    )
    add_subcommand_format(card)
    add_config_path_arg(card)
    card.add_argument("--goal-id", required=True)
    add_projection_limit_args(card)
    card.add_argument("--title")
    card.add_argument("--template", default="blue")
    card.add_argument("--message-id", help="Existing card message id (om_...) for updates.")
    card.add_argument(
        "--remember-message-id",
        action="store_true",
        help="Persist --message-id in the local board config.",
    )
    card.add_argument("--card-file", help="Also write the card JSON to this local file.")


def _target_config(args: argparse.Namespace, *, config_path: Path) -> LarkExploreConfig:
    stored = lark_explore_config_from_payload(read_lark_explore_local_config(config_path))
    base_token = args.base_token or (stored.base_token if stored else None)
    table_ids = dict(stored.table_ids) if stored else {}
    for table_key in EXPLORE_TABLE_KEYS:
        override = getattr(args, f"table_id_{table_key}", None)
        if override:
            table_ids[table_key] = override
    if not base_token or not all(table_ids.get(key) for key in EXPLORE_TABLE_KEYS):
        raise ValueError("explore feishu target requires --base-token/--table-id-* or local config from feishu-setup")
    return LarkExploreConfig(
        **{"base_" + "token": base_token},
        table_ids=table_ids,
        cli_bin=args.cli_bin or (stored.cli_bin if stored else DEFAULT_CLI_BIN),
        identity=args.identity or (stored.identity if stored else "user"),
    )


def handle_explore_feishu_command(
    args: argparse.Namespace,
    *,
    config_path: Path,
    runtime_root: Path,
    projection_for: ProjectionFor,
) -> dict[str, object]:
    if args.explore_command == "feishu-setup":
        return setup_lark_explore_board(
            config_path=config_path,
            base_name=args.base_name,
            base_url=args.base_url,
            **{"base_" + "token": args.base_token},
            cli_bin=args.cli_bin,
            identity=args.identity,
            execute=bool(args.execute),
        )
    if args.explore_command == "feishu-visual-configure":
        return configure_lark_explore_visual_sink(
            config_path=config_path,
            whiteboard_token=args.whiteboard_token,
            docx_token=args.docx_token,
            statuses=args.visual_statuses,
            tags=args.visual_tags,
            projection_mode=args.projection_mode
            or {
                "canonical": "canonical_full",
                "executive": "executive_auto",
            }.get(args.view_role, "canonical_filtered"),
            include_ancestors=bool(args.include_ancestors),
            mermaid_node_limit=args.mermaid_node_limit,
            stage_capacity=args.stage_capacity,
            stage_whiteboard_tokens=args.stage_whiteboard_token,
            board_style=args.board_style,
            view_role=args.view_role,
            auto_create=bool(args.auto_create),
            execute=bool(args.execute),
        )
    if args.explore_command == "feishu-sync":
        with explore_feishu_sync_singleflight(
            config_path=config_path,
            execute=bool(args.execute),
        ) as acquired:
            if not acquired:
                payload = explore_feishu_sync_busy_packet()
                payload["visual_sync"] = {
                    "ok": False,
                    "status": "not_attempted_sync_busy",
                    "execute": True,
                    "published": False,
                    "external_write_performed": False,
                }
                return payload
            projection = projection_for(args, runtime_root=runtime_root)
            target = _target_config(args, config_path=config_path)
            payload = sync_explore_results_to_lark(
                target,
                projection=projection,
                config_path=config_path,
                sink_visibility=args.sink_visibility,
                execute=bool(args.execute),
            )
            local = read_lark_explore_local_config(config_path)
            visual_sinks = local.get("visual_sinks")
            if isinstance(visual_sinks, dict) and visual_sinks:
                visual_projection = projection_for(
                    args,
                    runtime_root=runtime_root,
                    finding_limit_override=-1,
                )
                payload["visual_sync"] = sync_explore_visuals_to_lark(
                    target,
                    projection=visual_projection,
                    visual_sinks=visual_sinks,
                    config_path=config_path,
                    execute=bool(args.execute),
                )
            else:
                payload["visual_sync"] = sync_explore_visual_to_lark(
                    target,
                    projection=projection,
                    visual_sink=local.get("visual_sink")
                    if isinstance(local.get("visual_sink"), dict)
                    else None,
                    config_path=config_path,
                    semantic_digest=explore_visual_semantic_digest(projection),
                    execute=bool(args.execute),
                )
            payload["ok"] = bool(payload.get("ok")) and bool(
                payload["visual_sync"].get("ok")
            )
            return payload
    if args.explore_command == "feishu-card":
        projection = projection_for(args, runtime_root=runtime_root)
        local = read_lark_explore_local_config(config_path)
        stored_card = local.get("card") if isinstance(local.get("card"), dict) else {}
        message_id = args.message_id or str(stored_card.get("message_id") or "") or None
        payload = build_explore_result_card(
            projection,
            title=args.title,
            template=args.template,
            message_id=message_id,
        )
        if args.remember_message_id and args.message_id:
            write_lark_explore_local_config(
                config_path,
                {
                    **{key: value for key, value in local.items() if key not in {"ok", "exists", "path"}},
                    "card": {**stored_card, "message_id": args.message_id},
                },
            )
            payload["message_id_remembered"] = True
        if args.card_file:
            card_file = Path(args.card_file).expanduser()
            card_file.parent.mkdir(parents=True, exist_ok=True)
            card_file.write_text(
                json.dumps(payload["card"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            payload["card_file"] = str(card_file)
        return payload
    raise ValueError(f"unknown explore feishu command: {args.explore_command}")
