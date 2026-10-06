"""Project the LoopX exploration topology into a Feishu/Lark Base result board.

This sink renders the bounded projection built by
``loopx.capabilities.explore.result_log`` into three Bitable tables (Nodes,
Edges, Findings) plus one interactive result card that answers three operator
questions: what has been explored, where is the loop blocked and why, and what
was found. It follows the Lark Kanban adapter contract: all external effects
go through ``lark-cli`` commands behind an injectable runner, every write is
dry-run unless ``execute=True``, and shared-visibility rows pass the
public-safe redaction used by the Kanban sync. Card content is transport-free;
an approved gateway sends or updates the actual Lark message. The Mermaid
topology source in the projection is for Feishu docs or any diagram renderer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ....capabilities.explore.result_log import (
    EXPLORE_RESULT_PROJECTION_VERSION,
    FINDING_STATUS_CONFIRMED,
    FINDING_STATUS_REFUTED,
    FINDING_STATUS_TENTATIVE,
    NODE_KINDS,
    NODE_STATUS_BLOCKED,
    NODE_STATUS_DEAD_END,
    NODE_STATUS_EXPLORING,
    NODE_STATUS_OPEN,
    NODE_STATUS_RESOLVED,
    EDGE_TYPES,
    build_explore_graph_view,
)
from ....presentation.explore_views import (
    PRESENTATION_MODE_DUAL_VIEW,
    build_explore_presentation_bundle,
    explore_source_digest,
    explore_source_revision,
    validate_explore_view_freshness,
)
from .kanban import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    _command_error,
    _extract_base_token,
    _extract_table_id,
    _extract_created_record_id,
    _public_safe_text,
    _run_command,
    _select_options,
    default_subprocess_runner,
    lark_record_rows,
    now_lark_datetime,
    parse_lark_base_url,
)
from .explore_singleflight import (
    default_lark_explore_config_path,
    singleflight_issue_fix_material_sync,
    source_lark_explore_config_path,
)
from .explore_stage_document import ensure_stage_whiteboards
from .explore_visual_integrity import (
    cross_role_stage_token_conflict_delivery,
    finalize_visual_role_results,
)
from .explore_visual_readback import (
    defer_visual_stage_after_host_timeout,
    publish_visual_with_readback,
    settle_visual_stage_readbacks,
)
from .explore_visual_styles import (
    BOARD_STYLE_AUTO_FLOW,
    board_source_with_delivery_marker,
    explore_board_style,
    resolve_explore_board_style,
    resolve_explore_board_style_update,
    summarize_explore_visual_sync,
)
from .message_card import build_lark_markdown_reply_card
from .sink_visibility import SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITY_SHARED, SINK_VISIBILITIES

LARK_EXPLORE_SCHEMA_VERSION = "loopx_lark_explore_result_board_v0"
LARK_EXPLORE_LOCAL_CONFIG_VERSION = "loopx_lark_explore_local_config_v0"
LARK_EXPLORE_SYNC_VERSION = "loopx_lark_explore_sync_v0"
LARK_EXPLORE_READBACK_VERSION = "loopx_lark_explore_readback_v0"
LARK_EXPLORE_CARD_VERSION = "loopx_lark_explore_card_v0"
LARK_EXPLORE_VISUAL_SYNC_VERSION = "loopx_lark_explore_visual_sync_v0"
LARK_EXPLORE_VISUALS_SYNC_VERSION = "loopx_lark_explore_visuals_sync_v0"

DEFAULT_EXPLORE_BASE_NAME = "LoopX Exploration Results"

TABLE_NODES = "nodes"
TABLE_EDGES = "edges"
TABLE_FINDINGS = "findings"
EXPLORE_TABLE_KEYS = (TABLE_NODES, TABLE_EDGES, TABLE_FINDINGS)
EXPLORE_TABLE_NAMES = {
    TABLE_NODES: "Nodes",
    TABLE_EDGES: "Edges",
    TABLE_FINDINGS: "Findings",
}

_GOAL_ID_FIELD = "LoopX Goal ID"
_RESULT_ID_FIELD = "LoopX Result ID"


def _number_field(name: str, *, precision: int) -> dict[str, Any]:
    return {
        "name": name,
        "type": "number",
        "style": {
            "type": "plain",
            "precision": precision,
            "percentage": False,
            "thousands_separator": False,
        },
    }


def _text_field(name: str) -> dict[str, Any]:
    return {"name": name, "type": "text", "style": {"type": "plain"}}


def _link_field(name: str, *, link_table: str) -> dict[str, Any]:
    return {"name": name, "type": "link", "link_table": link_table}


def _select_field(name: str, options: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "type": "select",
        "multiple": False,
        "options": _select_options(options),
    }


_LINEAGE_FIELDS = [
    _text_field(_GOAL_ID_FIELD),
    _text_field(_RESULT_ID_FIELD),
    _text_field("Source ID"),
    _text_field("Row Lifecycle"),
    _text_field("Supersedes"),
    _text_field("Superseded By"),
]


def lark_explore_field_definitions(table_key: str) -> list[dict[str, Any]]:
    if table_key == TABLE_NODES:
        return [
            _text_field("Title"),
            _select_field("Kind", sorted(NODE_KINDS)),
            _select_field(
                "Status",
                [
                    NODE_STATUS_OPEN,
                    NODE_STATUS_EXPLORING,
                    NODE_STATUS_BLOCKED,
                    NODE_STATUS_RESOLVED,
                    NODE_STATUS_DEAD_END,
                ],
            ),
            _text_field("Summary"),
            _text_field("Blocked Reason"),
            _text_field("Parent Node"),
            _number_field("Findings", precision=0),
            _text_field("Evidence Refs"),
            _text_field("Tags"),
            _text_field("Agent ID"),
            _text_field("First Recorded At"),
            _text_field("Last Updated At"),
            *_LINEAGE_FIELDS,
        ]
    if table_key == TABLE_EDGES:
        return [
            _text_field("From Node"),
            _text_field("To Node"),
            _link_field("From Node Link", link_table=EXPLORE_TABLE_NAMES[TABLE_NODES]),
            _link_field("To Node Link", link_table=EXPLORE_TABLE_NAMES[TABLE_NODES]),
            _select_field("Type", sorted(EDGE_TYPES)),
            _number_field("Confidence", precision=2),
            _text_field("Condition"),
            _text_field("State Transition"),
            _text_field("Summary"),
            _text_field("Last Updated At"),
            _text_field(_GOAL_ID_FIELD),
            _text_field(_RESULT_ID_FIELD),
            _text_field("Source ID"),
        ]
    if table_key == TABLE_FINDINGS:
        return [
            _text_field("Finding"),
            _text_field("Summary"),
            _select_field(
                "Status",
                [
                    FINDING_STATUS_TENTATIVE,
                    FINDING_STATUS_CONFIRMED,
                    FINDING_STATUS_REFUTED,
                ],
            ),
            _number_field("Confidence", precision=2),
            _text_field("Node"),
            _text_field("Evidence Refs"),
            _text_field("Tags"),
            _text_field("Agent ID"),
            _text_field("First Recorded At"),
            _text_field("Last Updated At"),
            *_LINEAGE_FIELDS,
        ]
    raise ValueError(f"unknown explore table key: {table_key}")


def lark_explore_schema_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": LARK_EXPLORE_SCHEMA_VERSION,
        "source_of_truth": "loopx_explore_result_log_projected_to_lark_base",
        "adapter_role": "read_only_result_dashboard",
        "projection_schema_version": EXPLORE_RESULT_PROJECTION_VERSION,
        "loopx_mapping": {
            "node": "Nodes row keyed by LoopX Result ID; Status=blocked rows answer where the loop is stuck",
            "edge": "Edges row keyed by LoopX Result ID; typed relation between two nodes",
            "finding": "Findings row keyed by LoopX Result ID; latest finding event wins",
            "topology": "Mermaid flowchart source in the projection, for Feishu docs or any renderer",
            "lineage": "Row Lifecycle, Supersedes, Superseded By, Source ID columns",
            "card": "compact interactive card built from the same projection",
        },
        "tables": {
            key: {
                "name": EXPLORE_TABLE_NAMES[key],
                "fields": lark_explore_field_definitions(key),
            }
            for key in EXPLORE_TABLE_KEYS
        },
        "write_boundary": (
            "Rows are a projection of the local explore result log. The board "
            "never receives worker commands, local paths, credentials, or raw "
            "transcripts; card send/update happens through an approved gateway."
        ),
    }


@dataclass(frozen=True)
class LarkExploreConfig:
    base_token: str
    table_ids: dict[str, str] = field(default_factory=dict)
    cli_bin: str = DEFAULT_CLI_BIN
    identity: str = "user"

    def table_id(self, table_key: str) -> str:
        table_id = str(self.table_ids.get(table_key) or "").strip()
        if not table_id:
            raise ValueError(f"missing table id for {table_key}; run `loopx explore feishu-setup` first")
        return table_id


def read_lark_explore_local_config(path: Path) -> dict[str, Any]:
    config_path = path.expanduser()
    if not config_path.exists():
        return {
            "ok": True,
            "exists": False,
            "schema_version": LARK_EXPLORE_LOCAL_CONFIG_VERSION,
            "path": str(config_path),
            "board": None,
        }
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "exists": True,
            "schema_version": LARK_EXPLORE_LOCAL_CONFIG_VERSION,
            "path": str(config_path),
            "error": f"invalid JSON: {exc}",
            "board": None,
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "exists": True,
            "schema_version": LARK_EXPLORE_LOCAL_CONFIG_VERSION,
            "path": str(config_path),
            "error": "config root must be a JSON object",
            "board": None,
        }
    payload.setdefault("schema_version", LARK_EXPLORE_LOCAL_CONFIG_VERSION)
    payload["ok"] = True
    payload["exists"] = True
    payload["path"] = str(config_path)
    return payload


def write_lark_explore_local_config(path: Path, payload: dict[str, Any]) -> None:
    config_path = path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    to_write = dict(payload)
    to_write.pop("ok", None)
    to_write.pop("exists", None)
    to_write.pop("path", None)
    to_write["schema_version"] = LARK_EXPLORE_LOCAL_CONFIG_VERSION
    to_write["updated_at"] = now_lark_datetime()
    config_path.write_text(json.dumps(to_write, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lark_explore_config_from_payload(
    payload: Mapping[str, Any],
) -> LarkExploreConfig | None:
    board = payload.get("board")
    if not isinstance(board, dict):
        return None
    base_token = str(board.get("base_token") or "").strip()
    tables = board.get("tables") if isinstance(board.get("tables"), dict) else {}
    table_ids = {str(key): str(value).strip() for key, value in tables.items() if str(value or "").strip()}
    if not base_token or not table_ids:
        return None
    return LarkExploreConfig(
        **{"base_" + "token": base_token},
        table_ids=table_ids,
        cli_bin=str(board.get("cli_bin") or DEFAULT_CLI_BIN),
        identity=str(board.get("identity") or "user"),
    )


def _record_json_args(values: Mapping[str, Any]) -> str:
    return json.dumps(dict(values), ensure_ascii=False, separators=(",", ":"))


def _build_upsert_command(
    config: LarkExploreConfig,
    *,
    table_id: str,
    record_id: str | None,
    values: Mapping[str, Any],
) -> list[str]:
    args = [
        config.cli_bin,
        "base",
        "+record-upsert",
        "--as",
        config.identity,
        "--base-token",
        config.base_token,
        "--table-id",
        table_id,
    ]
    if record_id:
        args.extend(["--record-id", record_id])
    args.extend(["--json", _record_json_args(values)])
    return args


def _build_record_list_command(
    config: LarkExploreConfig,
    *,
    table_id: str,
    goal_id: str,
    offset: int = 0,
) -> list[str]:
    return [
        config.cli_bin,
        "base",
        "+record-list",
        "--as",
        config.identity,
        "--base-token",
        config.base_token,
        "--table-id",
        table_id,
        "--filter-json",
        _record_json_args(
            {
                "logic": "and",
                "conditions": [[_GOAL_ID_FIELD, "==", goal_id]],
            }
        ),
        "--format",
        "json",
        "--offset",
        str(offset),
        "--limit",
        "200",
    ]


def _record_list_has_more(payload: Mapping[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    return bool(data.get("has_more")) if isinstance(data, Mapping) else False


def _record_scan_goal_ids(goal_id: str, *, public_safe: bool) -> tuple[str, ...]:
    desired_goal_id = _public_safe_text(goal_id) if public_safe else goal_id
    if public_safe:
        return (desired_goal_id,)
    legacy_shared_goal_id = _public_safe_text(goal_id)
    if legacy_shared_goal_id == desired_goal_id:
        return (desired_goal_id,)
    return desired_goal_id, legacy_shared_goal_id


def _normalize_lark_value(value: Any) -> Any:
    if value is None or value == "":
        return ""
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_lark_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        normalized = [_normalize_lark_value(item) for item in value]
        if len(normalized) == 1 and isinstance(normalized[0], str):
            return normalized[0]
        return normalized
    return value


def _lark_values_match(values: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    return all(
        _normalize_lark_value(record.get(field_name)) == _normalize_lark_value(expected)
        for field_name, expected in values.items()
    )


def _skipped_sync_command() -> dict[str, Any]:
    return {
        "command": "",
        "executed": False,
        "ok": True,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "json": None,
        "skipped": True,
        "reason": "unchanged",
    }


def _persist_lark_explore_record_map(
    config: LarkExploreConfig,
    *,
    config_path: Path | None,
    local: Mapping[str, Any],
    record_map: Mapping[str, str],
) -> None:
    if not config_path:
        return
    existing_records = dict(local.get("result_records") or {}) if isinstance(local.get("result_records"), dict) else {}
    if existing_records == dict(record_map) and bool(local.get("exists")):
        return
    board = local.get("board") if isinstance(local.get("board"), dict) else {}
    if not board:
        board = {
            "base_token": config.base_token,
            "tables": dict(config.table_ids),
            "cli_bin": config.cli_bin,
            "identity": config.identity,
        }
    updated = {key: value for key, value in local.items() if key not in {"ok", "exists", "path", "updated_at"}}
    updated.update(
        {
            "schema_version": LARK_EXPLORE_LOCAL_CONFIG_VERSION,
            "board": board,
            "result_records": dict(record_map),
            "card": local.get("card") if isinstance(local.get("card"), dict) else {},
        }
    )
    write_lark_explore_local_config(config_path, updated)


AUTO_DOC_TITLE = "LoopX Explore 实时探索拓扑"
AUTO_DOC_CREATE_XML = (
    "<title>" + AUTO_DOC_TITLE + "</title>"
    "<h1>实时探索拓扑</h1>"
    "<p>Executive 总览画板；由 feishu-sync 自动更新。</p>"
    '<whiteboard type="blank"></whiteboard>'
    "<h2>LoopX Explore · Canonical 全局画板</h2>"
    "<p>Canonical 全局画板；由 feishu-sync 自动更新。</p>"
    '<whiteboard type="blank"></whiteboard>'
)
AUTO_DOC_APPEND_XML = (
    "<h1>实时探索拓扑</h1>"
    "<p>Executive 总览画板；由 feishu-sync 自动更新。</p>"
    '<whiteboard type="blank"></whiteboard>'
    "<h2>LoopX Explore · Canonical 全局画板</h2>"
    "<p>Canonical 全局画板；由 feishu-sync 自动更新。</p>"
    '<whiteboard type="blank"></whiteboard>'
)


def _created_overview_boards(command: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return newly created whiteboard blocks in document order."""

    payload = command.get("json")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    document = data.get("document") if isinstance(data, Mapping) else None
    blocks = document.get("new_blocks") if isinstance(document, Mapping) else None
    boards = []
    for block in blocks or []:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("block_type") or "")
        block_token = str(block.get("block_token") or "").strip()
        if block_type == "whiteboard" and block_token:
            boards.append(
                {
                    "block_id": str(block.get("block_id") or "").strip() or None,
                    "block_token": block_token,
                }
            )
    return boards


def configure_lark_explore_visual_sink(
    *,
    config_path: Path,
    whiteboard_token: str | None = None,
    docx_token: str | None = None,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    projection_mode: str = "canonical_filtered",
    include_ancestors: bool = True,
    mermaid_node_limit: int = 100,
    stage_capacity: int = 14,
    stage_whiteboard_tokens: list[str] | None = None,
    board_style: str | None = None,
    view_role: str | None = None,
    auto_create: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Configure an optional owner-facing whiteboard over canonical Explore data.

    ``auto_create=True`` bootstraps the hosting document and the two overview
    boards (executive topology + canonical global) without manual token
    plumbing. Canonical Evidence Stage boards are created lazily by
    ``feishu-sync`` unless explicit stage tokens are supplied.
    """

    token = str(whiteboard_token or "").strip()
    document_token = str(docx_token or "").strip()
    if not token and not document_token and not auto_create:
        raise ValueError("whiteboard_token or docx_token is required")
    valid_modes = {
        "canonical_filtered",
        "issue_fix_two_lane",
        "canonical_full",
        "executive_auto",
    }
    if projection_mode not in valid_modes:
        raise ValueError(f"projection_mode must be one of {sorted(valid_modes)}")
    role = str(view_role or "").strip() or None
    if role not in {None, "canonical", "executive"}:
        raise ValueError("view_role must be canonical or executive")
    expected_mode = {"canonical": "canonical_full", "executive": "executive_auto"}.get(role)
    if expected_mode and projection_mode != expected_mode:
        raise ValueError(f"view_role {role} requires projection_mode {expected_mode}")
    if not 10 <= int(stage_capacity) <= 20:
        raise ValueError("stage_capacity must be between 10 and 20")
    local = read_lark_explore_local_config(config_path)
    if not local.get("ok") or not local.get("exists"):
        raise ValueError("run `loopx explore feishu-setup` before configuring a visual sink")
    visual_sinks = local.get("visual_sinks")
    existing_sink = (
        visual_sinks.get(role)
        if role and isinstance(visual_sinks, Mapping)
        else local.get("visual_sink") if not role else None
    )
    style = resolve_explore_board_style_update(
        board_style,
        existing_sink if isinstance(existing_sink, Mapping) else {},
    )
    tokens = [
        str(item).strip()
        for item in stage_whiteboard_tokens or []
        if str(item).strip()
    ]
    if not tokens and token:
        tokens = [token]
    elif token and token not in tokens:
        tokens.insert(0, token)

    def _auto_failure(error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "schema_version": "loopx_lark_explore_visual_sink_configure_v0",
            "execute": execute,
            "status": "auto_create_failed",
            "config_path": str(config_path),
            "view_role": role,
            "error": error,
        }

    overview_boards: list[dict[str, Any]] = []
    auto_created_document: dict[str, Any] | None = None
    if auto_create:
        if execute:
            board_meta = local.get("board") if isinstance(local.get("board"), dict) else {}
            cli_bin = str(board_meta.get("cli_bin") or DEFAULT_CLI_BIN)
            identity = str(board_meta.get("identity") or "user")
            stored_sinks = (
                local.get("visual_sinks")
                if isinstance(local.get("visual_sinks"), Mapping)
                else {}
            )
            stored_canonical = (
                stored_sinks.get("canonical")
                if isinstance(stored_sinks.get("canonical"), Mapping)
                else {}
            )
            stored_executive = (
                stored_sinks.get("executive")
                if isinstance(stored_sinks.get("executive"), Mapping)
                else {}
            )
            if not document_token:
                document_token = str(
                    stored_canonical.get("docx_token")
                    or stored_executive.get("docx_token")
                    or ""
                ).strip()
            reused_overview = (
                document_token
                and str(stored_canonical.get("overview_whiteboard_token") or "").strip()
                and str(stored_executive.get("overview_whiteboard_token") or "").strip()
            )
            if reused_overview:
                overview_boards = [
                    {"block_token": str(stored_executive["overview_whiteboard_token"])},
                    {"block_token": str(stored_canonical["overview_whiteboard_token"])},
                ]
            elif not document_token:
                create_command = _run_command(
                    [
                        cli_bin,
                        "docs",
                        "+create",
                        "--as",
                        identity,
                        "--content",
                        AUTO_DOC_CREATE_XML,
                        "--format",
                        "json",
                    ],
                    execute=True,
                    runner=default_subprocess_runner,
                )
                if not create_command.get("ok"):
                    return _auto_failure(_command_error(create_command))
                document_token = str(
                    (
                        (create_command.get("json") or {})
                        .get("data", {})
                        .get("document", {})
                        .get("document_id")
                        or ""
                    )
                ).strip()
                if not document_token:
                    return _auto_failure(
                        "created document did not return a document_id"
                    )
                overview_boards = _created_overview_boards(create_command)
            else:
                append_command = _run_command(
                    [
                        cli_bin,
                        "docs",
                        "+update",
                        "--as",
                        identity,
                        "--doc",
                        document_token,
                        "--command",
                        "append",
                        "--content",
                        AUTO_DOC_APPEND_XML,
                        "--format",
                        "json",
                    ],
                    execute=True,
                    runner=default_subprocess_runner,
                )
                if not append_command.get("ok"):
                    return _auto_failure(_command_error(append_command))
                overview_boards = _created_overview_boards(append_command)
            if len(overview_boards) < 2:
                return _auto_failure(
                    "auto-created document did not return both overview whiteboards"
                )
            auto_created_document = {
                "docx_token": document_token,
                "overview_boards": overview_boards,
            }
        else:
            overview_boards = [
                {"block_token": "planned-executive-overview"},
                {"block_token": "planned-canonical-overview"},
            ]

    def _role_sink(
        role_name: str,
        overview_token: str | None,
        *,
        stage_enabled: bool,
    ) -> dict[str, Any]:
        mode = {
            "canonical": "canonical_full",
            "executive": "executive_auto",
        }.get(role_name, projection_mode)
        sink = {
            "schema_version": "loopx_lark_explore_visual_sink_config_v0",
            "whiteboard_token": token or None,
            "docx_token": document_token or None,
            "statuses": [str(item) for item in statuses or [] if str(item).strip()],
            "tags": [str(item) for item in tags or [] if str(item).strip()],
            "projection_mode": mode,
            "include_ancestors": bool(include_ancestors),
            "mermaid_node_limit": max(1, int(mermaid_node_limit)),
            "stage_capacity": int(stage_capacity),
            "board_style": style.name,
            "renderer": style.renderer,
            "presentation_mode": "stage_document",
            "stage_boards_enabled": stage_enabled,
            "stage_whiteboards": [
                {"stage_index": index, "whiteboard_token": stage_token}
                for index, stage_token in enumerate(tokens, start=1)
            ],
            "view_role": role_name,
        }
        if overview_token:
            sink["overview_whiteboard_token"] = overview_token
        return sink

    if auto_create:
        if board_style is None:
            style = explore_board_style(BOARD_STYLE_AUTO_FLOW)
        executive_board = overview_boards[0] if overview_boards else {}
        canonical_board = overview_boards[1] if len(overview_boards) > 1 else {}
        explicit_stage_tokens = bool(tokens)
        planned_sinks: dict[str, dict[str, Any]] = {}
        if role in {None, "canonical"}:
            planned_sinks["canonical"] = _role_sink(
                "canonical",
                str(canonical_board.get("block_token") or "").strip() or None,
                stage_enabled=True,
            )
            if not explicit_stage_tokens:
                planned_sinks["canonical"]["stage_whiteboards"] = []
        if role in {None, "executive"}:
            planned_sinks["executive"] = _role_sink(
                "executive",
                str(executive_board.get("block_token") or "").strip() or None,
                stage_enabled=explicit_stage_tokens and role == "executive",
            )
            if not explicit_stage_tokens or role != "executive":
                planned_sinks["executive"]["stage_boards_enabled"] = False
                planned_sinks["executive"]["stage_whiteboards"] = []
        if execute:
            updated = {
                key: value
                for key, value in local.items()
                if key not in {"ok", "exists", "path", "updated_at"}
            }
            sinks = dict(updated.get("visual_sinks") or {})
            sinks.update(planned_sinks)
            updated["visual_sinks"] = sinks
            write_lark_explore_local_config(config_path, updated)
        return {
            "ok": True,
            "schema_version": "loopx_lark_explore_visual_sink_configure_v0",
            "execute": execute,
            "status": "auto_configured" if execute else "would_auto_create",
            "config_path": str(config_path),
            "view_role": role,
            "auto_create": True,
            "document": auto_created_document
            or {
                "docx_token": document_token or "planned-docx",
                "overview_boards": overview_boards,
            },
            "visual_sinks": planned_sinks,
        }

    visual_sink = {
        "schema_version": "loopx_lark_explore_visual_sink_config_v0",
        "whiteboard_token": token or None,
        "docx_token": document_token or None,
        "statuses": [str(item) for item in statuses or [] if str(item).strip()],
        "tags": [str(item) for item in tags or [] if str(item).strip()],
        "projection_mode": projection_mode,
        "include_ancestors": bool(include_ancestors),
        "mermaid_node_limit": max(1, int(mermaid_node_limit)),
        "stage_capacity": int(stage_capacity),
        "board_style": style.name,
        "renderer": style.renderer,
        "presentation_mode": "stage_document",
        "stage_boards_enabled": True,
        "stage_whiteboards": [
            {"stage_index": index, "whiteboard_token": stage_token}
            for index, stage_token in enumerate(tokens, start=1)
        ],
    }
    if role:
        visual_sink["view_role"] = role
    if execute:
        updated = {key: value for key, value in local.items() if key not in {"ok", "exists", "path", "updated_at"}}
        if role:
            visual_sinks = dict(updated.get("visual_sinks") or {})
            visual_sinks[role] = visual_sink
            updated["visual_sinks"] = visual_sinks
        else:
            updated["visual_sink"] = visual_sink
        write_lark_explore_local_config(config_path, updated)
    return {
        "ok": True,
        "schema_version": "loopx_lark_explore_visual_sink_configure_v0",
        "execute": execute,
        "status": "configured" if execute else "would_configure",
        "config_path": str(config_path),
        "view_role": role,
        "visual_sink": visual_sink,
    }


def sync_explore_visual_to_lark(
    config: LarkExploreConfig,
    *,
    projection: Mapping[str, Any],
    visual_sink: Mapping[str, Any] | None,
    config_path: Path,
    semantic_digest: str,
    display_projection: Mapping[str, Any] | None = None,
    view_key: str | None = None,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
    defer_readback: bool = False,
) -> dict[str, Any]:
    """Publish a configured whiteboard without conflating it with Base rows."""

    if not isinstance(visual_sink, Mapping):
        return {
            "ok": True,
            "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
            "status": "not_configured",
            "execute": execute,
            "published": False,
        }
    whiteboard_token = str(visual_sink.get("whiteboard_token") or "").strip()
    if not whiteboard_token:
        return {
            "ok": False,
            "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
            "status": "invalid_config",
            "execute": execute,
            "published": False,
            "error": "visual_sink.whiteboard_token is required",
        }
    source_digest = explore_source_digest(projection)
    source_revision = explore_source_revision(projection, digest=source_digest)
    if isinstance(display_projection, Mapping) and (
        display_projection.get("source_digest") or display_projection.get("source_revision")
    ):
        freshness = validate_explore_view_freshness(projection, display_projection)
        if not freshness["fresh"]:
            return {
                "ok": False,
                "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
                "status": "stale_projection",
                "execute": execute,
                "published": False,
                "source_digest": source_digest,
                "source_revision": source_revision,
                "freshness": freshness,
                "error": freshness["reason"],
            }
    graph = (
        dict(display_projection)
        if isinstance(display_projection, Mapping)
        else build_explore_graph_view(
            projection.get("nodes") or [],
            projection.get("edges") or [],
            statuses=visual_sink.get("statuses") or [],
            tags=visual_sink.get("tags") or [],
            include_ancestors=bool(visual_sink.get("include_ancestors", True)),
            node_limit=max(1, int(visual_sink.get("mermaid_node_limit") or 100)),
        )
    )
    sink_key = str(view_key or visual_sink.get("view_role") or "visual").strip() or "visual"
    try:
        style = resolve_explore_board_style(visual_sink)
    except ValueError as exc:
        return {
            "ok": False,
            "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
            "status": "invalid_config",
            "execute": execute,
            "published": False,
            "error": str(exc),
        }
    rendered_source = str(graph.get(style.source_key) or "")
    style_fallback = False
    if not rendered_source.strip() and style.source_key == "svg":
        mermaid_source = str(graph.get("mermaid") or "")
        if mermaid_source.strip():
            style = explore_board_style(BOARD_STYLE_AUTO_FLOW)
            rendered_source = mermaid_source
            style_fallback = True
    if not rendered_source.strip():
        return {
            "ok": False,
            "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
            "status": "invalid_projection",
            "execute": execute,
            "published": False,
            "view_role": str(graph.get("view_role") or sink_key),
            "renderer": style.renderer,
            "error": f"display projection does not contain {style.source_key} source",
        }
    delivery_material = json.dumps(
        {
            "source_digest": semantic_digest,
            "source_revision": source_revision,
            "view_role": sink_key,
            "whiteboard_token": whiteboard_token,
            "board_style": style.name,
            "renderer": style.renderer,
            "rendered_source": rendered_source,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    delivery_digest = hashlib.sha256(delivery_material).hexdigest()
    delivery_marker = f"LoopX delivery {delivery_digest[:20]}"
    published_source = board_source_with_delivery_marker(
        rendered_source,
        delivery_marker,
        style=style,
    )
    source_name = f".loopx-explore-{sink_key}-{delivery_digest[:12]}.{style.extension}"
    command = [
        config.cli_bin,
        "whiteboard",
        "+update",
        "--as",
        config.identity,
        "--whiteboard-token",
        whiteboard_token,
        "--input_format",
        style.input_format,
        "--source",
        f"@{source_name}",
        "--overwrite",
        "--idempotent-token",
        f"loopx-explore-{sink_key}-{delivery_digest[:20]}",
        "--format",
        "json",
    ]
    delivery = publish_visual_with_readback(
        cli_bin=config.cli_bin,
        identity=config.identity,
        whiteboard_token=whiteboard_token,
        marker=delivery_marker,
        command=command,
        published_source=published_source,
        source_path=config_path.parent / source_name,
        execute=execute,
        runner=runner,
        defer_readback=defer_readback,
    )
    result = delivery["command"]
    readback = delivery["readback"]
    delivery_ok = bool(delivery["ok"])
    retryable = bool(delivery["retryable"])
    publish_attempts = delivery["publish_attempts"]
    return {
        "ok": delivery_ok,
        "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
        "status": (
            "published"
            if execute and delivery_ok
            else "would_publish"
            if not execute
            else "publish_unverified"
            if result.get("ok") and delivery_marker
            else "publish_failed"
        ),
        "execute": execute,
        "published": bool(execute and delivery_ok),
        "external_write_performed": delivery["external_write_performed"],
        "reconciled_existing_delivery": delivery["reconciled_existing_delivery"],
        "prepublish_readback": delivery["prepublish_readback"],
        "semantic_digest": semantic_digest,
        "delivery_digest": delivery_digest,
        "source_digest": source_digest,
        "source_revision": source_revision,
        "view_role": str(graph.get("view_role") or sink_key),
        "board_style": style.name,
        "style_fallback": style_fallback,
        "renderer": style.renderer,
        "input_format": style.input_format,
        "docx_token": str(visual_sink.get("docx_token") or "") or None,
        "graph_counts": graph.get("graph_counts"),
        "filter": graph.get("filter"),
        "command": result,
        "publish_attempt_count": len(publish_attempts),
        "publish_attempts": publish_attempts,
        "readback": readback,
        "retryable": retryable,
        "required_action": (
            "retry Explore visual sync; post-publish marker readback did not settle"
            if retryable
            else None
        ),
        "error": (
            None
            if delivery_ok
            else str(readback.get("error") or "")
            if result.get("ok")
            else _command_error(result)
        ),
    }


def explore_visual_semantic_digest(projection: Mapping[str, Any]) -> str:
    """Return a timestamp-free digest for an explicit visual projection sync."""

    return explore_source_digest(projection)


def sync_explore_visuals_to_lark(
    config: LarkExploreConfig,
    *,
    projection: Mapping[str, Any],
    visual_sinks: Mapping[str, Any],
    config_path: Path,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Generate and publish configured same-source Explore views."""

    bundle = build_explore_presentation_bundle(projection)
    requested_roles = [
        role
        for role in ("canonical", "executive")
        if isinstance(visual_sinks.get(role), Mapping)
    ]
    active_roles = ["canonical"]
    if bundle["presentation_mode"] == PRESENTATION_MODE_DUAL_VIEW:
        active_roles.append("executive")
    conflict = cross_role_stage_token_conflict_delivery(
        bundle=bundle, visual_sinks=visual_sinks, requested_roles=requested_roles,
        active_roles=active_roles, execute=execute,
        schema_version=LARK_EXPLORE_VISUALS_SYNC_VERSION,
    )
    if conflict:
        return conflict
    results: dict[str, Any] = {}
    stage_readback_targets: list[tuple[dict[str, Any], str]] = []
    readback_host_wait_exhausted = False
    for role in requested_roles:
        if role not in active_roles:
            results[role] = {
                "ok": True,
                "status": "not_recommended",
                "execute": execute,
                "published": False,
                "source_digest": bundle["source_digest"],
                "source_revision": bundle["source_revision"],
                "view_role": role,
            }
            continue
        role_sink = visual_sinks[role]
        stage_capacity = int(role_sink.get("stage_capacity") or 14)
        role_bundle = build_explore_presentation_bundle(
            projection,
            policy={
                "stage_node_capacity": stage_capacity,
            },
        )
        role_view = role_bundle[role]
        stage_views = [
            item
            for item in role_view.get("stage_views") or []
            if isinstance(item, Mapping)
        ]
        stage_boards_enabled = bool(role_sink.get("stage_boards_enabled", True))
        configured_stages: dict[int, dict[str, Any]] = {}
        section_commands: list[dict[str, Any]] = []
        reconciliation: dict[str, Any] = {}
        if stage_boards_enabled:
            configured_stages, section_commands, _, reconciliation, stage_config_error = ensure_stage_whiteboards(
                config,
                role=role,
                role_sink=role_sink,
                stage_views=stage_views,
                config_path=config_path,
                execute=execute,
                runner=runner,
                read_local_config=read_lark_explore_local_config,
                write_local_config=write_lark_explore_local_config,
            )
            missing_stage_indexes = [
                int(stage["stage_index"])
                for stage in stage_views
                if int(stage["stage_index"]) not in configured_stages
            ]
            if stage_config_error or missing_stage_indexes:
                results[role] = {
                    "ok": False,
                    "status": "stage_whiteboards_missing",
                    "execute": execute,
                    "published": False,
                    "view_role": role,
                    "source_digest": role_bundle["source_digest"],
                    "source_revision": role_bundle["source_revision"],
                    "required_stage_count": len(stage_views),
                    "configured_stage_count": len(configured_stages),
                    "missing_stage_indexes": missing_stage_indexes,
                    "section_commands": section_commands,
                    "reconciliation": reconciliation,
                    "error": stage_config_error
                    or "configure one document section and whiteboard token for each Evidence Stage",
                }
                continue
        stage_results = []
        role_nodes = {
            str(node.get("node_id") or ""): node
            for node in role_view.get("nodes") or []
            if isinstance(node, Mapping)
        }
        role_edges = [
            edge for edge in role_view.get("edges") or [] if isinstance(edge, Mapping)
        ]
        if stage_boards_enabled:
            for stage in stage_views:
                stage_index = int(stage["stage_index"])
                stage_node_ids = {str(item) for item in stage.get("node_ids") or []}
                stage_projection = dict(role_view)
                stage_projection["mermaid"] = str(stage.get("mermaid") or "")
                stage_projection["svg"] = str(stage.get("svg") or "")
                stage_projection["nodes"] = [
                    role_nodes[node_id]
                    for node_id in stage.get("node_ids") or []
                    if node_id in role_nodes
                ]
                stage_projection["edges"] = [
                    edge
                    for edge in role_edges
                    if str(edge.get("from_node") or "") in stage_node_ids
                    and str(edge.get("to_node") or "") in stage_node_ids
                ]
                stage_projection["stage"] = dict(stage)
                stage_sink = dict(role_sink)
                stage_sink["whiteboard_token"] = str(
                    configured_stages[stage_index].get("whiteboard_token") or ""
                ).strip()
                stage_result = sync_explore_visual_to_lark(
                    config,
                    projection=projection,
                    visual_sink=stage_sink,
                    config_path=config_path,
                    semantic_digest=role_bundle["source_digest"],
                    display_projection=stage_projection,
                    view_key=f"{role}_stage_{stage_index:02d}",
                    execute=bool(execute and not readback_host_wait_exhausted),
                    runner=runner,
                    defer_readback=bool(execute and not readback_host_wait_exhausted),
                )
                if execute and readback_host_wait_exhausted:
                    defer_visual_stage_after_host_timeout(stage_result)
                stage_result = dict(stage_result, stage=dict(stage))
                stage_results.append(stage_result)
                prepublish_readback = stage_result.get("prepublish_readback")
                if isinstance(prepublish_readback, Mapping) and prepublish_readback.get(
                    "host_wait_exhausted"
                ):
                    readback_host_wait_exhausted = True
                if (
                    execute
                    and stage_result.get("retryable")
                    and stage_result.get("external_write_performed")
                    and str(stage_sink.get("whiteboard_token") or "").strip()
                ):
                    stage_readback_targets.append(
                        (stage_result, str(stage_sink["whiteboard_token"]))
                    )
        role_ok = all(bool(item.get("ok")) for item in stage_results)
        role_retryable = any(bool(item.get("retryable")) for item in stage_results)
        overview_result = None
        overview_token = str(role_sink.get("overview_whiteboard_token") or "").strip()
        if overview_token:
            overview_projection = dict(role_view)
            overview_projection.pop("stage_views", None)
            overview_sink = dict(role_sink)
            overview_sink["whiteboard_token"] = overview_token
            overview_result = sync_explore_visual_to_lark(
                config,
                projection=projection,
                visual_sink=overview_sink,
                config_path=config_path,
                semantic_digest=role_bundle["source_digest"],
                display_projection=overview_projection,
                view_key=f"{role}_overview",
                execute=bool(execute and not readback_host_wait_exhausted),
                runner=runner,
                defer_readback=bool(execute and not readback_host_wait_exhausted),
            )
            if execute and readback_host_wait_exhausted:
                defer_visual_stage_after_host_timeout(overview_result)
            role_ok = role_ok and bool(overview_result.get("ok"))
            role_retryable = role_retryable or bool(overview_result.get("retryable"))
            overview_readback = overview_result.get("prepublish_readback")
            if isinstance(overview_readback, Mapping) and overview_readback.get(
                "host_wait_exhausted"
            ):
                readback_host_wait_exhausted = True
            if (
                execute
                and overview_result.get("retryable")
                and overview_result.get("external_write_performed")
            ):
                stage_readback_targets.append((overview_result, overview_token))
        role_delivery_material = "|".join(
            str(item.get("delivery_digest") or "")
            for item in stage_results + ([overview_result] if overview_result else [])
        ).encode("utf-8")
        results[role] = {
            "ok": role_ok,
            "status": (
                "published" if execute and role_ok else "would_publish" if role_ok else "publish_failed"
            ),
            "execute": execute,
            "published": bool(execute and role_ok),
            "retryable": role_retryable,
            "required_action": (
                f"retry Explore visual sync for the {role} marker readback"
                if role_retryable
                else None
            ),
            "view_role": role,
            "board_style": (
                str(stage_results[0].get("board_style") or "")
                if stage_results
                else ""
            ),
            "renderer": (
                str(stage_results[0].get("renderer") or "")
                if stage_results
                else ""
            ),
            "presentation_mode": "stage_document",
            "source_digest": role_bundle["source_digest"],
            "source_revision": role_bundle["source_revision"],
            "delivery_digest": hashlib.sha256(role_delivery_material).hexdigest(),
            "stage_count": len(stage_results),
            "stage_capacity": stage_capacity,
            "stages": stage_results,
            "overview": overview_result,
            "section_commands": section_commands,
            "reconciliation": reconciliation,
            "reconciled_stage_count": sum(
                bool(item.get("reconciled_existing_delivery"))
                for item in stage_results
            ),
            "updated_stage_count": sum(
                bool(item.get("external_write_performed")) for item in stage_results
            ),
            "readback_host_wait_exhausted": any(
                bool((item.get("readback") or {}).get("host_wait_exhausted"))
                for item in stage_results
                if isinstance(item.get("readback"), Mapping)
            ),
        }
    if stage_readback_targets:
        settle_visual_stage_readbacks(
            cli_bin=config.cli_bin, identity=config.identity,
            stage_targets=stage_readback_targets, runner=runner,
        )
    finalize_visual_role_results(results, execute=execute)
    delivery = summarize_explore_visual_sync(
        views=results,
        configured_roles=requested_roles,
        recommended_roles=active_roles,
        execute=execute,
    )
    return {
        **delivery,
        "schema_version": LARK_EXPLORE_VISUALS_SYNC_VERSION,
        "execute": execute,
        "presentation_mode": bundle["presentation_mode"],
        "reason_codes": bundle["reason_codes"],
        "source_digest": bundle["source_digest"],
        "source_revision": bundle["source_revision"],
        "recommended_roles": active_roles,
        "views": results,
        "readback_host_wait_exhausted": any(
            bool(view.get("readback_host_wait_exhausted"))
            for view in results.values()
            if isinstance(view, Mapping)
        ),
    }


def _visual_delivery_digests(sync_payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Return the configured role digests that make a visual write idempotent."""

    if not isinstance(sync_payload, Mapping):
        return {}
    views = sync_payload.get("views")
    if not isinstance(views, Mapping):
        return {}
    digests = {}
    for role, view in views.items():
        if not isinstance(view, Mapping):
            continue
        role_digest = str(view.get("delivery_digest") or "").strip()
        if role_digest:
            digests[str(role)] = role_digest
        overview = view.get("overview")
        if isinstance(overview, Mapping):
            overview_digest = str(overview.get("delivery_digest") or "").strip()
            if overview_digest:
                digests[f"{role}_overview"] = overview_digest
        for stage in view.get("stages") or []:
            if not isinstance(stage, Mapping):
                continue
            stage_digest = str(stage.get("delivery_digest") or "").strip()
            stage_index = int(stage.get("stage", {}).get("stage_index") or 0)
            if stage_digest and stage_index:
                digests[f"{role}_stage_{stage_index:02d}"] = stage_digest
    return digests


def setup_lark_explore_board(
    *,
    config_path: Path,
    base_name: str = DEFAULT_EXPLORE_BASE_NAME,
    base_url: str | None = None,
    base_token: str | None = None,
    cli_bin: str = DEFAULT_CLI_BIN,
    identity: str = "user",
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Create (or complete) the three-table exploration result board."""

    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    existing = read_lark_explore_local_config(config_path)
    existing_board = existing.get("board") if isinstance(existing.get("board"), dict) else {}
    existing_tables = existing_board.get("tables") if isinstance(existing_board.get("tables"), dict) else {}
    parsed_url = parse_lark_base_url(base_url) if base_url else {}
    effective_base_token = str(
        base_token or parsed_url.get("base_token") or existing_board.get("base_token") or ""
    ).strip()
    effective_base_url = str(base_url or existing_board.get("base_url") or "").strip()
    table_ids = {
        key: str(existing_tables.get(key) or "").strip()
        for key in EXPLORE_TABLE_KEYS
        if str(existing_tables.get(key) or "").strip()
    }

    def failure(error: str | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "schema_version": LARK_EXPLORE_SCHEMA_VERSION,
            "execute": execute,
            "config_path": str(config_path),
            "base_token": effective_base_token or None,
            "tables": table_ids,
            "commands": commands,
            "warnings": warnings,
            "error": error
            or next(
                (_command_error(item) for item in commands if not item.get("ok")),
                "unknown",
            ),
        }

    if not effective_base_token:
        create = _run_command(
            [cli_bin, "base", "+base-create", "--as", identity, "--name", base_name],
            execute=execute,
            runner=runner,
        )
        commands.append(create)
        if execute:
            if not create.get("ok"):
                return failure()
            effective_base_token = _extract_base_token(create.get("json")) or ""
            if not effective_base_token:
                return failure("base-create did not return a usable Base token")
            create_data = create.get("json", {}).get("data") if isinstance(create.get("json"), dict) else {}
            create_base = (
                create_data.get("base")
                if isinstance(create_data, dict) and isinstance(create_data.get("base"), dict)
                else {}
            )
            effective_base_url = str(create_base.get("url") or effective_base_url).strip()
        else:
            effective_base_token = "<base-token-from-create>"

    for table_key in EXPLORE_TABLE_KEYS:
        if table_ids.get(table_key):
            continue
        table_create = _run_command(
            [
                cli_bin,
                "base",
                "+table-create",
                "--as",
                identity,
                "--base-token",
                effective_base_token,
                "--name",
                EXPLORE_TABLE_NAMES[table_key],
                "--fields",
                json.dumps(lark_explore_field_definitions(table_key), ensure_ascii=False),
            ],
            execute=execute,
            runner=runner,
        )
        commands.append(table_create)
        if execute:
            if not table_create.get("ok"):
                return failure()
            table_id = _extract_table_id(table_create.get("json")) or ""
            if not table_id:
                return failure(f"table-create for {EXPLORE_TABLE_NAMES[table_key]} did not return a table id")
            table_ids[table_key] = table_id
        else:
            table_ids[table_key] = f"<table-id-from-table-create:{table_key}>"

    board = {
        "base_token": effective_base_token,
        "base_url": effective_base_url,
        "base_name": base_name or existing_board.get("base_name") or "",
        "cli_bin": cli_bin,
        "identity": identity,
        "tables": table_ids,
    }
    if execute:
        updated = {key: value for key, value in existing.items() if key not in {"ok", "exists", "path", "updated_at"}}
        updated.update(
            {
                "schema_version": LARK_EXPLORE_LOCAL_CONFIG_VERSION,
                "board": board,
                "result_records": (
                    existing.get("result_records") if isinstance(existing.get("result_records"), dict) else {}
                ),
                "card": existing.get("card") if isinstance(existing.get("card"), dict) else {},
            }
        )
        write_lark_explore_local_config(
            config_path,
            updated,
        )

    return {
        "ok": True,
        "schema_version": LARK_EXPLORE_SCHEMA_VERSION,
        "execute": execute,
        "config_path": str(config_path),
        "base_token": effective_base_token,
        "tables": table_ids,
        "board": board,
        "commands": commands,
        "warnings": warnings,
        "next_commands": [
            "loopx explore node --goal-id <goal-id> --title <topic> --status exploring",
            "loopx explore finding --goal-id <goal-id> --title <finding> --node <node-id>",
            "loopx explore feishu-sync --goal-id <goal-id> --execute",
        ],
        "error": None,
    }


def _joined(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(str(item) for item in values if str(item or "").strip())
    return str(values or "")


def _lifecycle_values(item: Mapping[str, Any], *, source_id: str) -> dict[str, Any]:
    return {
        "Source ID": source_id,
        "Row Lifecycle": "superseded" if str(item.get("superseded_by") or "") else "current",
        "Supersedes": _joined(item.get("supersedes")),
        "Superseded By": str(item.get("superseded_by") or ""),
    }


def _node_record_values(node: Mapping[str, Any], *, goal_id: str, source_id: str) -> dict[str, Any]:
    return {
        "Title": str(node.get("title") or ""),
        "Kind": str(node.get("node_kind") or ""),
        "Status": str(node.get("status") or ""),
        "Summary": "\n".join(part for part in [str(node.get("summary") or ""), str(node.get("research_summary") or "")] if part),
        "Blocked Reason": str(node.get("blocked_reason") or ""),
        "Parent Node": str(node.get("parent_id") or ""),
        "Findings": node.get("finding_count"),
        "Evidence Refs": _joined(node.get("evidence_refs")),
        "Tags": _joined(node.get("tags")),
        "Agent ID": str(node.get("agent_id") or ""),
        "First Recorded At": str(node.get("first_recorded_at") or ""),
        "Last Updated At": str(node.get("last_updated_at") or ""),
        _GOAL_ID_FIELD: goal_id,
        _RESULT_ID_FIELD: str(node.get("node_id") or ""),
        **_lifecycle_values(node, source_id=source_id),
    }


def _edge_record_values(edge: Mapping[str, Any], *, goal_id: str, source_id: str) -> dict[str, Any]:
    return {
        "From Node": str(edge.get("from_node") or ""),
        "To Node": str(edge.get("to_node") or ""),
        "Type": str(edge.get("edge_type") or ""),
        "Confidence": edge.get("confidence"),
        "Condition": str(edge.get("summary") or ""),
        "State Transition": str(edge.get("edge_type") or ""),
        "Summary": str(edge.get("summary") or ""),
        "Last Updated At": str(edge.get("last_updated_at") or ""),
        _GOAL_ID_FIELD: goal_id,
        _RESULT_ID_FIELD: str(edge.get("edge_id") or ""),
        "Source ID": source_id,
    }


def _finding_record_values(finding: Mapping[str, Any], *, goal_id: str, source_id: str) -> dict[str, Any]:
    return {
        "Finding": str(finding.get("finding") or ""),
        "Summary": str(finding.get("summary") or ""),
        "Status": str(finding.get("status") or ""),
        "Confidence": finding.get("confidence"),
        "Node": str(finding.get("node_id") or ""),
        "Evidence Refs": _joined(finding.get("evidence_refs")),
        "Tags": _joined(finding.get("tags")),
        "Agent ID": str(finding.get("agent_id") or ""),
        "First Recorded At": str(finding.get("first_recorded_at") or ""),
        "Last Updated At": str(finding.get("last_updated_at") or ""),
        _GOAL_ID_FIELD: goal_id,
        _RESULT_ID_FIELD: str(finding.get("finding_id") or ""),
        **_lifecycle_values(finding, source_id=source_id),
    }


def _public_safe_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _public_safe_text(value) if isinstance(value, str) else value for key, value in values.items()}


def _with_edge_link_values(
    values: dict[str, Any],
    *,
    record_map: Mapping[str, str],
    goal_id: str,
) -> dict[str, Any]:
    """Add linked-record cells so the Lark Base itself represents the graph.

    The plain text node ids remain as readable stable keys. The link fields are
    best-effort because legacy boards may not have the schema yet; Lark ignores
    unknown fields only when the request is not sent, so callers should create
    the fields before enabling live sync against an existing board.
    """

    linked = dict(values)
    from_record = str(record_map.get(f"{goal_id}:{TABLE_NODES}:{values.get('From Node')}") or "")
    to_record = str(record_map.get(f"{goal_id}:{TABLE_NODES}:{values.get('To Node')}") or "")
    if from_record:
        linked["From Node Link"] = [{"id": from_record}]
    if to_record:
        linked["To Node Link"] = [{"id": to_record}]
    return linked


def sync_explore_results_to_lark(
    config: LarkExploreConfig,
    *,
    projection: Mapping[str, Any],
    config_path: Path | None = None,
    sink_visibility: str = SINK_VISIBILITY_OWNER_ONLY,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    if not isinstance(projection, Mapping):
        raise ValueError("projection must be a JSON object")
    if projection.get("schema_version") != EXPLORE_RESULT_PROJECTION_VERSION:
        raise ValueError(f"projection must use schema {EXPLORE_RESULT_PROJECTION_VERSION}")
    if sink_visibility not in SINK_VISIBILITIES:
        raise ValueError(f"sink_visibility must be one of {sorted(SINK_VISIBILITIES)}")
    public_safe = sink_visibility == SINK_VISIBILITY_SHARED
    goal_id = str(projection.get("goal_id") or "").strip()
    if not goal_id:
        raise ValueError("projection is missing goal_id")
    source_id = f"loopx-explore:{goal_id}"

    rows_by_table: dict[str, list[dict[str, Any]]] = {
        TABLE_NODES: [
            _node_record_values(item, goal_id=goal_id, source_id=source_id)
            for item in projection.get("nodes") or []
            if isinstance(item, Mapping)
        ],
        TABLE_EDGES: [
            _edge_record_values(item, goal_id=goal_id, source_id=source_id)
            for item in projection.get("edges") or []
            if isinstance(item, Mapping)
        ],
        TABLE_FINDINGS: [
            _finding_record_values(item, goal_id=goal_id, source_id=source_id)
            for item in projection.get("findings") or []
            if isinstance(item, Mapping)
        ],
    }
    if public_safe:
        rows_by_table = {
            table_key: [_public_safe_values(values) for values in rows] for table_key, rows in rows_by_table.items()
        }
    remote_goal_id = _public_safe_text(goal_id) if public_safe else goal_id
    scan_goal_ids = _record_scan_goal_ids(goal_id, public_safe=public_safe)

    local = read_lark_explore_local_config(config_path) if config_path else {}
    record_map = dict(local.get("result_records") or {}) if isinstance(local.get("result_records"), dict) else {}
    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    remote_records: dict[str, dict[str, Any]] = {}
    duplicate_remote_rows = 0
    expected_keys = {
        f"{goal_id}:{table_key}:{str(values.get(_RESULT_ID_FIELD) or '').strip()}"
        for table_key in EXPLORE_TABLE_KEYS
        for values in rows_by_table[table_key]
    }

    if execute:
        for table_key in EXPLORE_TABLE_KEYS:
            if not rows_by_table[table_key]:
                continue
            for scan_goal_id in scan_goal_ids:
                offset = 0
                while True:
                    list_result = _run_command(
                        _build_record_list_command(
                            config,
                            table_id=config.table_id(table_key),
                            goal_id=scan_goal_id,
                            offset=offset,
                        ),
                        execute=True,
                        runner=runner,
                    )
                    commands.append(list_result)
                    if not list_result.get("ok"):
                        warnings.append(
                            f"record-list for {table_key} failed; continuing with cached record ids"
                        )
                        break
                    payload = list_result.get("json") if isinstance(list_result.get("json"), dict) else {}
                    page_records = lark_record_rows(payload)
                    for record in page_records:
                        result_id = str(record.get(_RESULT_ID_FIELD) or "").strip()
                        row_goal_id = str(record.get(_GOAL_ID_FIELD) or "").strip()
                        record_id = str(record.get("_record_id") or "").strip()
                        if not (result_id and row_goal_id and record_id):
                            continue
                        if row_goal_id != scan_goal_id:
                            continue
                        key = f"{goal_id}:{table_key}:{result_id}"
                        if key not in expected_keys:
                            continue
                        if key in remote_records:
                            duplicate_remote_rows += 1
                            continue
                        remote_records[key] = record
                        record_map[key] = record_id
                    if not _record_list_has_more(payload):
                        break
                    if not page_records:
                        warnings.append(
                            f"record-list for {table_key} reported more rows but returned an empty page"
                        )
                        break
                    offset += len(page_records)

        if duplicate_remote_rows:
            warnings.append(f"found {duplicate_remote_rows} duplicate remote result rows; reused the first row")
        _persist_lark_explore_record_map(
            config,
            config_path=config_path,
            local=local,
            record_map=record_map,
        )

    results: list[dict[str, Any]] = []
    ok = True
    skipped_rows = 0
    written_rows = 0
    for table_key in EXPLORE_TABLE_KEYS:
        for values in rows_by_table[table_key]:
            result_id = str(values.get(_RESULT_ID_FIELD) or "").strip()
            key = f"{goal_id}:{table_key}:{result_id}"
            remote_record = remote_records.get(key)
            if execute and record_map.get(key) and remote_record and _lark_values_match(values, remote_record):
                result = _skipped_sync_command()
                skipped_rows += 1
            else:
                result = _run_command(
                    _build_upsert_command(
                        config,
                        table_id=config.table_id(table_key),
                        record_id=record_map.get(key) if remote_record else None,
                        values=values,
                    ),
                    execute=execute,
                    runner=runner,
                )
                commands.append(result)
                if execute and result.get("ok"):
                    written_rows += 1
            record_id = _extract_created_record_id(result.get("json")) or record_map.get(key)
            if execute and result.get("ok") and record_id:
                record_map[key] = record_id
                if not result.get("skipped"):
                    remote_records[key] = {**values, "_record_id": record_id}
                    _persist_lark_explore_record_map(
                        config,
                        config_path=config_path,
                        local=local,
                        record_map=record_map,
                    )
            results.append(
                {
                    "table": table_key,
                    "result_id": result_id,
                    "record_id": record_id,
                    "command": result,
                    "values": values,
                }
            )
            ok = ok and bool(result.get("ok"))
            if execute and not result.get("ok"):
                break
        if execute and not ok:
            break

        if table_key == TABLE_NODES:
            rows_by_table[TABLE_EDGES] = [
                _with_edge_link_values(values, record_map=record_map, goal_id=goal_id)
                for values in rows_by_table[TABLE_EDGES]
            ]

    readback_records: dict[str, dict[str, Any]] = {}
    readback_duplicate_rows = 0
    readback_failed_tables: list[str] = []
    readback_source = "not_performed"
    if execute and ok and written_rows == 0:
        readback_records = dict(remote_records)
        readback_duplicate_rows = duplicate_remote_rows
        readback_source = "initial_scan"
    elif execute and ok:
        readback_source = "post_write_scan"
        for table_key in EXPLORE_TABLE_KEYS:
            if not rows_by_table[table_key]:
                continue
            offset = 0
            while True:
                list_result = _run_command(
                    _build_record_list_command(
                        config,
                        table_id=config.table_id(table_key),
                        goal_id=remote_goal_id,
                        offset=offset,
                    ),
                    execute=True,
                    runner=runner,
                )
                commands.append(list_result)
                if not list_result.get("ok"):
                    readback_failed_tables.append(table_key)
                    break
                payload = list_result.get("json") if isinstance(list_result.get("json"), dict) else {}
                page_records = lark_record_rows(payload)
                for record in page_records:
                    result_id = str(record.get(_RESULT_ID_FIELD) or "").strip()
                    row_goal_id = str(record.get(_GOAL_ID_FIELD) or "").strip()
                    record_id = str(record.get("_record_id") or "").strip()
                    if not (result_id and row_goal_id and record_id):
                        continue
                    if row_goal_id != remote_goal_id:
                        continue
                    key = f"{goal_id}:{table_key}:{result_id}"
                    if key not in expected_keys:
                        continue
                    if key in readback_records:
                        readback_duplicate_rows += 1
                        continue
                    readback_records[key] = record
                    record_map[key] = record_id
                if not _record_list_has_more(payload):
                    break
                if not page_records:
                    readback_failed_tables.append(table_key)
                    break
                offset += len(page_records)

    expected_values = {
        f"{goal_id}:{table_key}:{str(values.get(_RESULT_ID_FIELD) or '').strip()}": values
        for table_key in EXPLORE_TABLE_KEYS
        for values in rows_by_table[table_key]
    }
    missing_readback_keys = sorted(expected_keys - set(readback_records))
    mismatched_readback_keys = sorted(
        key
        for key, values in expected_values.items()
        if key in readback_records and not _lark_values_match(values, readback_records[key])
    )
    readback_verified = bool(
        execute
        and ok
        and not readback_failed_tables
        and not missing_readback_keys
        and not mismatched_readback_keys
        and readback_duplicate_rows == 0
    )
    readback = {
        "ok": readback_verified,
        "schema_version": LARK_EXPLORE_READBACK_VERSION,
        "performed": bool(execute and ok),
        "verified": readback_verified,
        "source": readback_source,
        "expected_result_count": len(expected_keys),
        "observed_result_count": len(readback_records),
        "missing_result_ids": [key.rsplit(":", 1)[-1] for key in missing_readback_keys],
        "mismatched_result_ids": [key.rsplit(":", 1)[-1] for key in mismatched_readback_keys],
        "duplicate_remote_rows": readback_duplicate_rows,
        "failed_tables": readback_failed_tables,
    }
    if execute and ok and not readback_verified:
        warnings.append("post-write row/result-id readback did not verify the Explore projection")
    if execute and readback_verified:
        _persist_lark_explore_record_map(
            config,
            config_path=config_path,
            local=local,
            record_map=record_map,
        )
    ok = ok and (not execute or readback_verified)

    return {
        "ok": ok,
        "schema_version": LARK_EXPLORE_SYNC_VERSION,
        "execute": execute,
        "goal_id": goal_id,
        "source_id": source_id,
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "projection_schema_version": projection.get("schema_version"),
        "row_counts": {table_key: len(rows_by_table[table_key]) for table_key in EXPLORE_TABLE_KEYS},
        "written_rows": written_rows,
        "skipped_rows": skipped_rows,
        "duplicate_remote_rows": duplicate_remote_rows,
        "readback": readback,
        "records": results,
        "commands": commands,
        "warnings": warnings,
        "config_path": str(config_path) if config_path else None,
        "error": None
        if ok
        else (
            "row/result-id readback failed"
            if readback.get("performed") and not readback_verified
            else next(
                (_command_error(item) for item in commands if not item.get("ok")),
                "unknown",
            )
        ),
    }


@singleflight_issue_fix_material_sync(source_lark_explore_config_path)
def sync_issue_fix_explore_on_material_change(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    execute: bool = False,
    external_sink_delivery_authorized: bool = True,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Project issue-fix facts and sync Lark only when the graph digest changed.

    The canonical Explore result log is updated before this adapter runs.  A
    failed or interrupted Lark write therefore remains retryable: the stored
    sink digest advances only after a successful remote sync.
    """

    from ....capabilities.issue_fix.explore_projection import (
        build_issue_fix_executive_visual_projection,
        project_issue_fix_explore_graph,
    )
    from .explore_source_guard import (
        resolve_explore_source_registry,
        source_projection_coverage_guard,
    )

    source_registry_path, compact_source_runtime_route = resolve_explore_source_registry(
        registry_path=registry_path,
        goal_id=goal_id,
    )
    projection_result = project_issue_fix_explore_graph(
        registry_path=source_registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        project=project,
        state_file=state_file,
        execute=execute,
    )
    projection_result["source_runtime_route"] = compact_source_runtime_route
    config_path = default_lark_explore_config_path(source_registry_path)
    local = read_lark_explore_local_config(config_path)
    config = lark_explore_config_from_payload(local) if local.get("ok") else None
    sync_state = (
        local.get("automatic_projection_sync") if isinstance(local.get("automatic_projection_sync"), dict) else {}
    )
    prior = sync_state.get(goal_id) if isinstance(sync_state.get(goal_id), dict) else {}
    digest = str(projection_result.get("semantic_digest") or "")
    prior_digest = str(prior.get("canonical_rows_semantic_digest") or prior.get("semantic_digest") or "")
    prior_row_readback_digest = str(prior.get("canonical_rows_readback_semantic_digest") or "")
    source_projection_guard = source_projection_coverage_guard(
        local=local,
        projection=projection_result.get("projection")
        if isinstance(projection_result.get("projection"), Mapping)
        else {},
        goal_id=goal_id,
    )
    visual_sinks = (
        local.get("visual_sinks")
        if isinstance(local.get("visual_sinks"), dict) and local.get("visual_sinks")
        else None
    )
    visual_sink = (
        local.get("visual_sink")
        if visual_sinks is None and isinstance(local.get("visual_sink"), dict)
        else None
    )
    prior_visual_digest = str(prior.get("visual_semantic_digest") or "")
    prior_visual_delivery_digests = (
        prior.get("visual_delivery_digests")
        if isinstance(prior.get("visual_delivery_digests"), dict)
        else {}
    )
    needs_row_sync = bool(digest and (digest != prior_digest or digest != prior_row_readback_digest))
    needs_visual_sync = bool(
        (visual_sinks or visual_sink) and digest and digest != prior_visual_digest
    )
    visual_preview: dict[str, Any] | None = None
    expected_visual_delivery_digests: dict[str, str] = {}
    needs_sync = needs_row_sync or needs_visual_sync
    if not projection_result.get("applicable"):
        return {
            "ok": True,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": "not_applicable",
            "execute": execute,
            "needs_sync": False,
            "needs_row_sync": False,
            "needs_visual_sync": False,
            "row_readback_verified": None,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "projection": projection_result,
            "lark_sync": None,
            "config_path": str(config_path),
        }
    if config is None:
        invalid_config = local.get("ok") is not True or isinstance(local.get("board"), dict)
        return {
            "ok": not invalid_config,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": "invalid_config" if invalid_config else "not_configured",
            "execute": execute,
            "needs_sync": needs_sync,
            "needs_row_sync": needs_row_sync,
            "needs_visual_sync": needs_visual_sync,
            "row_readback_verified": None,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "projection": projection_result,
            "lark_sync": None,
            "config_path": str(config_path),
        }
    if not source_projection_guard["ok"]:
        return {
            "ok": False,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": "source_projection_regression_blocked",
            "execute": execute,
            "needs_sync": True,
            "needs_row_sync": needs_row_sync,
            "needs_visual_sync": needs_visual_sync,
            "row_readback_verified": False,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "source_runtime_route": compact_source_runtime_route,
            "source_projection_guard": source_projection_guard,
            "projection": projection_result,
            "lark_sync": None,
            "canonical_rows_sync": None,
            "visual_preview": None,
            "visual_sync": None,
            "external_write_performed": False,
            "retryable": True,
            "required_action": (
                "reconcile the registry-declared Explore source runtime before retry"
            ),
            "error": (
                "registry-declared Explore source omits result ids already registered "
                "by the sink; refusing a regressive remote write"
            ),
            "config_path": str(config_path),
        }
    if visual_sinks:
        visual_preview = sync_explore_visuals_to_lark(
            config,
            projection=projection_result["projection"],
            visual_sinks=visual_sinks,
            config_path=config_path,
            execute=False,
            runner=runner,
        )
        expected_visual_delivery_digests = _visual_delivery_digests(visual_preview)
        needs_visual_sync = bool(
            digest
            and (
                not visual_preview.get("ok")
                or any(bool(view.get("reconciliation", {}).get("required")) for view in visual_preview.get("views", {}).values())
                or any(
                    prior_visual_delivery_digests.get(role) != delivery_digest
                    for role, delivery_digest in expected_visual_delivery_digests.items()
                )
            )
        )
    needs_sync = needs_row_sync or needs_visual_sync
    if not needs_sync:
        return {
            "ok": True,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": "unchanged",
            "execute": execute,
            "needs_sync": False,
            "needs_row_sync": False,
            "needs_visual_sync": False,
            "row_readback_verified": prior_row_readback_digest == digest,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "projection": projection_result,
            "lark_sync": None,
            "visual_preview": visual_preview,
            "config_path": str(config_path),
        }
    if not execute:
        return {
            "ok": True,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": "would_sync",
            "execute": False,
            "needs_sync": True,
            "needs_row_sync": needs_row_sync,
            "needs_visual_sync": needs_visual_sync,
            "row_readback_verified": False,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "projection": projection_result,
            "lark_sync": None,
            "visual_preview": visual_preview,
            "config_path": str(config_path),
        }
    if not external_sink_delivery_authorized:
        return {
            "ok": True,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": "external_sink_suppressed",
            "execute": True,
            "external_sink_delivery_authorized": False,
            "needs_sync": True,
            "needs_row_sync": needs_row_sync,
            "needs_visual_sync": needs_visual_sync,
            "row_readback_verified": False,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "prior_visual_semantic_digest": prior_visual_digest or None,
            "prior_visual_delivery_digests": dict(prior_visual_delivery_digests),
            "projection": projection_result,
            "lark_sync": None,
            "canonical_rows_sync": None,
            "visual_preview": visual_preview,
            "visual_sync": None,
            "retryable": True,
            "config_path": str(config_path),
        }
    lark_sync = (
        sync_explore_results_to_lark(
            config,
            projection=projection_result["projection"],
            config_path=config_path,
            execute=True,
            runner=runner,
        )
        if needs_row_sync
        else None
    )
    if not needs_visual_sync:
        visual_sync = None
    elif visual_sinks:
        visual_sync = sync_explore_visuals_to_lark(
            config,
            projection=projection_result["projection"],
            visual_sinks=visual_sinks,
            config_path=config_path,
            execute=True,
            runner=runner,
        )
    else:
        visual_sync = sync_explore_visual_to_lark(
            config,
            projection=projection_result["projection"],
            visual_sink=visual_sink,
            config_path=config_path,
            semantic_digest=digest,
            display_projection=build_issue_fix_executive_visual_projection(projection_result["projection"])
            if str(visual_sink.get("projection_mode") or "") == "issue_fix_two_lane"
            else None,
            execute=True,
            runner=runner,
        )
    row_ok = lark_sync is None or bool(lark_sync.get("ok"))
    row_readback_verified = bool(
        lark_sync is None
        or (
            isinstance(lark_sync.get("readback"), Mapping)
            and lark_sync["readback"].get("verified") is True
        )
    )
    row_ok = row_ok and row_readback_verified
    visual_ok = visual_sync is None or bool(visual_sync.get("ok"))
    if row_ok or visual_ok:
        updated_sync_state = dict(sync_state)
        updated_goal_state = dict(prior)
        if needs_row_sync and row_ok:
            updated_goal_state.update(
                {
                    "semantic_digest": digest,
                    "canonical_rows_semantic_digest": digest,
                    "canonical_rows_readback_semantic_digest": digest,
                    "canonical_rows_synced_at": now_lark_datetime(),
                }
            )
        if needs_visual_sync and visual_ok:
            updated_goal_state.update(
                {
                    "visual_semantic_digest": digest,
                    "visual_published_at": now_lark_datetime(),
                }
            )
            if visual_sinks:
                updated_goal_state["visual_delivery_digests"] = _visual_delivery_digests(
                    visual_sync
                )
        updated_goal_state["synced_at"] = now_lark_datetime()
        updated_sync_state[goal_id] = updated_goal_state
        # The row sync persists record ids incrementally. Re-read that write
        # before adding sink-specific digests so a partial success remains
        # retryable without restoring a stale record map.
        persisted_local = read_lark_explore_local_config(config_path)
        updated_local = dict(persisted_local if persisted_local.get("ok") else local)
        updated_local["automatic_projection_sync"] = updated_sync_state
        write_lark_explore_local_config(config_path, updated_local)
    all_ok = row_ok and visual_ok
    return {
        "ok": all_ok,
        "schema_version": "issue_fix_explore_lark_material_sync_v0",
        "status": "synced" if all_ok else "sync_failed",
        "execute": True,
        "needs_sync": True,
        "needs_row_sync": needs_row_sync,
        "needs_visual_sync": needs_visual_sync,
        "row_readback_verified": row_readback_verified,
        "semantic_digest": digest,
        "prior_semantic_digest": prior_digest or None,
        "prior_visual_semantic_digest": prior_visual_digest or None,
        "prior_visual_delivery_digests": dict(prior_visual_delivery_digests),
        "projection": projection_result,
        "lark_sync": lark_sync,
        "canonical_rows_sync": lark_sync,
        "visual_preview": visual_preview,
        "visual_sync": visual_sync,
        "config_path": str(config_path),
    }
def build_explore_card_markdown(projection: Mapping[str, Any]) -> str:
    counts = projection.get("counts") if isinstance(projection.get("counts"), dict) else {}
    by_status = counts.get("nodes_by_status") if isinstance(counts.get("nodes_by_status"), dict) else {}
    status_parts = [
        f"{by_status.get(status, 0)} {label}"
        for status, label in (
            (NODE_STATUS_EXPLORING, "exploring"),
            (NODE_STATUS_BLOCKED, "blocked"),
            (NODE_STATUS_RESOLVED, "resolved"),
            (NODE_STATUS_OPEN, "open"),
        )
        if by_status.get(status, 0)
    ]
    lines = [
        (
            f"**Exploration map**: {counts.get('node_count', 0)} nodes"
            + (f" ({', '.join(status_parts)})" if status_parts else "")
            + f", {counts.get('edge_count', 0)} edges, "
            f"{counts.get('finding_count', 0)} findings"
        ),
        "",
    ]
    stuck = [item for item in projection.get("stuck") or [] if isinstance(item, Mapping)]
    if stuck:
        lines.append("**Blocked**")
        for node in stuck:
            reason = str(node.get("blocked_reason") or "").strip()
            lines.append(f"- {node.get('title')}" + (f" - {reason}" if reason else ""))
        lines.append("")
    findings = [item for item in projection.get("findings") or [] if isinstance(item, Mapping)]
    if findings:
        lines.append("**Latest findings**")
        for finding in findings[:5]:
            lines.append(f"- [{finding.get('status')}] {finding.get('finding')}")
        lines.append("")
    frontier = [item for item in projection.get("frontier") or [] if isinstance(item, Mapping)]
    if frontier:
        lines.append("**Exploring now**")
        for node in frontier[:5]:
            lines.append(f"- {node.get('title')}")
    return "\n".join(lines).strip()


def build_explore_result_card(
    projection: Mapping[str, Any],
    *,
    title: str | None = None,
    template: str = "blue",
    message_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(projection, Mapping):
        raise ValueError("projection must be a JSON object")
    if projection.get("schema_version") != EXPLORE_RESULT_PROJECTION_VERSION:
        raise ValueError(f"projection must use schema {EXPLORE_RESULT_PROJECTION_VERSION}")
    goal_id = str(projection.get("goal_id") or "").strip()
    markdown = build_explore_card_markdown(projection)
    card = build_lark_markdown_reply_card(
        markdown,
        title=title or f"Exploration map: {goal_id}",
        template=template,
        footer=(
            f"LoopX explore | {projection.get('generated_at')} | {projection.get('source_event_count')} result events"
        ),
    )
    return {
        "ok": True,
        "schema_version": LARK_EXPLORE_CARD_VERSION,
        "goal_id": goal_id,
        "message_id": message_id or None,
        "card": card,
        "card_markdown": markdown,
        "send_boundary": (
            "Card content only. Send or update the Lark message through an "
            "approved gateway (bot or lark-cli) after the operator permits the write."
        ),
    }
