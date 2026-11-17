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

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cli_transport import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    command_error,
    run_command as run_command,
    default_subprocess_runner as default_subprocess_runner,
)
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
)
from ..private_json import write_private_json_atomic
from .kanban import (
    _credential_safe_lark_values,
    _extract_base_token,
    _extract_table_id,
    _extract_created_record_id,
    _public_safe_lark_values,
    _public_safe_text,
    _redact_credential_shapes,
    _select_options,
    lark_record_rows,
    now_lark_datetime,
    parse_lark_base_url,
)
from .explore_record_values import (
    _GOAL_ID_FIELD, _RESULT_ID_FIELD,
    _node_record_values, _edge_record_values, _finding_record_values,
)
from .explore_singleflight import (
    default_lark_explore_config_path,
    singleflight_issue_fix_material_sync,
    source_lark_explore_config_path,
)
from .explore_visual_sink import (
    AUTO_DOC_APPEND_XML as AUTO_DOC_APPEND_XML,
    AUTO_DOC_CREATE_XML as AUTO_DOC_CREATE_XML,
    AUTO_DOC_TITLE as AUTO_DOC_TITLE,
    LARK_EXPLORE_VISUAL_SYNC_VERSION as LARK_EXPLORE_VISUAL_SYNC_VERSION,
    LARK_EXPLORE_VISUALS_SYNC_VERSION as LARK_EXPLORE_VISUALS_SYNC_VERSION,
    _created_overview_boards as _created_overview_boards,
    _redact_explore_display_records as _redact_explore_display_records,
    _redact_explore_display_values as _redact_explore_display_values,
    _redact_explore_projection_display as _redact_explore_projection_display,
    _redact_rendered_display_spans as _redact_rendered_display_spans,
    _redact_visual_display_text as _redact_visual_display_text,
    _visual_delivery_digests as _visual_delivery_digests,
    configure_lark_explore_visual_sink as configure_lark_explore_visual_sink,
    editable_local_explore_config,
    explore_visual_semantic_digest as explore_visual_semantic_digest,
    sync_explore_visual_to_lark as sync_explore_visual_to_lark,
    sync_explore_visuals_to_lark as sync_explore_visuals_to_lark,
)
from .message_card import build_lark_markdown_reply_card
from .sink_visibility import SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITY_SHARED, SINK_VISIBILITIES

LARK_EXPLORE_SCHEMA_VERSION = "loopx_lark_explore_result_board_v0"
LARK_EXPLORE_LOCAL_CONFIG_VERSION = "loopx_lark_explore_local_config_v0"
LARK_EXPLORE_SYNC_VERSION = "loopx_lark_explore_sync_v0"
LARK_EXPLORE_READBACK_VERSION = "loopx_lark_explore_readback_v0"
LARK_EXPLORE_CARD_VERSION = "loopx_lark_explore_card_v0"

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
    to_write = dict(payload)
    to_write.pop("ok", None)
    to_write.pop("exists", None)
    to_write.pop("path", None)
    to_write["schema_version"] = LARK_EXPLORE_LOCAL_CONFIG_VERSION
    to_write["updated_at"] = now_lark_datetime()
    write_private_json_atomic(config_path, to_write)


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
    safe_values = _credential_safe_lark_values(dict(values))
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
    args.extend(["--json", _record_json_args(safe_values)])
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
    updated = editable_local_explore_config(local)
    updated.update(
        {
            "schema_version": LARK_EXPLORE_LOCAL_CONFIG_VERSION,
            "board": board,
            "result_records": dict(record_map),
            "card": local.get("card") if isinstance(local.get("card"), dict) else {},
        }
    )
    write_lark_explore_local_config(config_path, updated)


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
                (command_error(item) for item in commands if not item.get("ok")),
                "unknown",
            ),
        }

    if not effective_base_token:
        create = run_command(
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
        table_create = run_command(
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
        updated = editable_local_explore_config(existing)
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


def _public_safe_values(values: dict[str, Any]) -> dict[str, Any]:
    return _public_safe_lark_values(values)


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


def _row_result_key(goal_id: str, table_key: str, values: Mapping[str, Any]) -> tuple[str, str]:
    result_id = str(values.get(_RESULT_ID_FIELD) or "").strip()
    return result_id, f"{goal_id}:{table_key}:{result_id}"


def _scan_explore_goal_records(
    config: LarkExploreConfig,
    *,
    rows_by_table: Mapping[str, list[dict[str, Any]]],
    goal_id: str,
    scan_goal_ids: tuple[str, ...],
    expected_keys: set[str],
    record_map: dict[str, str],
    commands: list[dict[str, Any]],
    runner: CommandRunner,
) -> tuple[dict[str, dict[str, Any]], int, list[tuple[str, str]]]:
    found: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    failures: list[tuple[str, str]] = []
    for table_key in EXPLORE_TABLE_KEYS:
        if not rows_by_table[table_key]:
            continue
        for scan_goal_id in scan_goal_ids:
            offset = 0
            while True:
                list_result = run_command(
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
                    failures.append((table_key, "failed"))
                    break
                payload = list_result.get("json") if isinstance(list_result.get("json"), dict) else {}
                page_records = lark_record_rows(payload)
                for record in page_records:
                    result_id = str(record.get(_RESULT_ID_FIELD) or "").strip()
                    row_goal_id = str(record.get(_GOAL_ID_FIELD) or "").strip()
                    record_id = str(record.get("_record_id") or "").strip()
                    if not (result_id and row_goal_id and record_id) or row_goal_id != scan_goal_id:
                        continue
                    key = f"{goal_id}:{table_key}:{result_id}"
                    if key not in expected_keys:
                        continue
                    if key in found:
                        duplicate_rows += 1
                        continue
                    found[key] = record
                    record_map[key] = record_id
                if not _record_list_has_more(payload):
                    break
                if not page_records:
                    failures.append((table_key, "empty_page"))
                    break
                offset += len(page_records)
    return found, duplicate_rows, failures


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
    rows_by_table = {
        table_key: [_credential_safe_lark_values(values) for values in rows]
        for table_key, rows in rows_by_table.items()
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
        _row_result_key(goal_id, table_key, values)[1]
        for table_key in EXPLORE_TABLE_KEYS
        for values in rows_by_table[table_key]
    }

    if execute:
        remote_records, duplicate_remote_rows, scan_failures = _scan_explore_goal_records(
            config,
            rows_by_table=rows_by_table,
            goal_id=goal_id,
            scan_goal_ids=scan_goal_ids,
            expected_keys=expected_keys,
            record_map=record_map,
            commands=commands,
            runner=runner,
        )
        for table_key, failure_kind in scan_failures:
            warnings.append(
                f"record-list for {table_key} failed; continuing with cached record ids"
                if failure_kind == "failed"
                else f"record-list for {table_key} reported more rows but returned an empty page"
            )
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
            result_id, key = _row_result_key(goal_id, table_key, values)
            remote_record = remote_records.get(key)
            if execute and record_map.get(key) and remote_record and _lark_values_match(values, remote_record):
                result = _skipped_sync_command()
                skipped_rows += 1
            else:
                result = run_command(
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
        readback_records, readback_duplicate_rows, readback_failures = _scan_explore_goal_records(
            config,
            rows_by_table=rows_by_table,
            goal_id=goal_id,
            scan_goal_ids=(remote_goal_id,),
            expected_keys=expected_keys,
            record_map=record_map,
            commands=commands,
            runner=runner,
        )
        readback_failed_tables = [table_key for table_key, _ in readback_failures]

    expected_values = {
        _row_result_key(goal_id, table_key, values)[1]: values
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
                (command_error(item) for item in commands if not item.get("ok")),
                "unknown",
            )
        ),
    }


@singleflight_issue_fix_material_sync(source_lark_explore_config_path)
def sync_issue_fix_explore_on_material_change(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_override: str | None = None,
    agent_id: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute: bool = False,
    external_sink_delivery_authorized: bool = True,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Sync changed graph digests after the canonical Explore log is updated.
    Failed/interrupted Lark writes remain retryable; advance the stored sink digest only after successful remote sync.
    """

    if sink_visibility not in SINK_VISIBILITIES:
        raise ValueError(f"sink_visibility must be one of {sorted(SINK_VISIBILITIES)}")

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
        runtime_root_override=runtime_root_override,
    )
    projection_result = project_issue_fix_explore_graph(
        registry_path=source_registry_path,
        goal_id=goal_id,
        runtime_root_override=runtime_root_override,
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

    def _sync_packet(
        status: str,
        *,
        ok: bool = True,
        exec_flag: bool = execute,
        needs: bool = needs_sync,
        row_sync: bool = needs_row_sync,
        vis_sync: bool = needs_visual_sync,
        readback_verified: bool | None = False,
        lark_sync: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "schema_version": "issue_fix_explore_lark_material_sync_v0",
            "status": status,
            "execute": exec_flag,
            "needs_sync": needs,
            "needs_row_sync": row_sync,
            "needs_visual_sync": vis_sync,
            "row_readback_verified": readback_verified,
            "semantic_digest": digest,
            "prior_semantic_digest": prior_digest or None,
            "projection": projection_result,
            "lark_sync": lark_sync,
            "config_path": str(config_path),
            **extra,
        }

    if not projection_result.get("applicable"):
        return _sync_packet(
            "not_applicable", needs=False, row_sync=False, vis_sync=False, readback_verified=None
        )
    if config is None:
        invalid_config = local.get("ok") is not True or isinstance(local.get("board"), dict)
        return _sync_packet(
            "invalid_config" if invalid_config else "not_configured",
            ok=not invalid_config,
            readback_verified=None,
        )
    if not source_projection_guard["ok"]:
        return _sync_packet(
            "source_projection_regression_blocked",
            ok=False,
            needs=True,
            source_runtime_route=compact_source_runtime_route,
            source_projection_guard=source_projection_guard,
            canonical_rows_sync=None,
            visual_preview=None,
            visual_sync=None,
            external_write_performed=False,
            retryable=True,
            required_action="reconcile the registry-declared Explore source runtime before retry",
            error=(
                "registry-declared Explore source omits result ids already registered "
                "by the sink; refusing a regressive remote write"
            ),
        )
    if visual_sinks:
        visual_preview = sync_explore_visuals_to_lark(
            config,
            projection=projection_result["projection"],
            visual_sinks=visual_sinks,
            config_path=config_path,
            sink_visibility=sink_visibility,
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
        return _sync_packet(
            "unchanged",
            needs=False,
            row_sync=False,
            vis_sync=False,
            readback_verified=prior_row_readback_digest == digest,
            visual_preview=visual_preview,
        )
    if not execute:
        return _sync_packet("would_sync", exec_flag=False, needs=True, visual_preview=visual_preview)
    if not external_sink_delivery_authorized:
        return _sync_packet(
            "external_sink_suppressed",
            exec_flag=True,
            external_sink_delivery_authorized=False,
            needs=True,
            prior_visual_semantic_digest=prior_visual_digest or None,
            prior_visual_delivery_digests=dict(prior_visual_delivery_digests),
            canonical_rows_sync=None,
            visual_preview=visual_preview,
            visual_sync=None,
            retryable=True,
        )
    lark_sync = (
        sync_explore_results_to_lark(
            config,
            projection=projection_result["projection"],
            config_path=config_path,
            sink_visibility=sink_visibility,
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
            sink_visibility=sink_visibility,
            execute=True,
            runner=runner,
        )
    else:
        assert isinstance(visual_sink, Mapping)
        display_projection = None
        if str(visual_sink.get("projection_mode") or "") == "issue_fix_two_lane":
            display_projection = build_issue_fix_executive_visual_projection(
                _redact_explore_projection_display(
                    projection_result["projection"],
                    public_safe=sink_visibility == SINK_VISIBILITY_SHARED,
                )
            )
        visual_sync = sync_explore_visual_to_lark(
            config,
            projection=projection_result["projection"],
            visual_sink=visual_sink,
            config_path=config_path,
            semantic_digest=digest,
            display_projection=display_projection,
            sink_visibility=sink_visibility,
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
    return _sync_packet(
        "synced" if all_ok else "sync_failed",
        ok=all_ok,
        exec_flag=True,
        needs=True,
        readback_verified=row_readback_verified,
        prior_visual_semantic_digest=prior_visual_digest or None,
        prior_visual_delivery_digests=dict(prior_visual_delivery_digests),
        lark_sync=lark_sync,
        canonical_rows_sync=lark_sync,
        visual_preview=visual_preview,
        visual_sync=visual_sync,
    )
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
    return _redact_credential_shapes("\n".join(lines).strip())


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
        title=_redact_credential_shapes(title or f"Exploration map: {goal_id}"),
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
