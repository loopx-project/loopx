from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
from ....presentation.projection_source_reconcile import (
    plan_projection_source_reconcile,
    validate_projection_source_reconcile_request,
)
from ....public_safe_text import (
    HOME_RELATIVE_PATH_PATTERN,
    PRESENTATION_LOCAL_PATH_PATTERNS,
    SECRET_LIKE_SURFACE_PATTERN,
    SINK_COMPOUND_CREDENTIAL_ASSIGNMENT_PATTERN,
)
from .. import cli_transport as cli_transport
from ..private_json import write_private_json_atomic
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
    _base_cmd,
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
from .projection_rows import (
    projection_lifecycle_parts as _projection_lifecycle_parts,
    projection_lifecycle_state as _projection_lifecycle_state,
    projection_lifecycle_value as _projection_lifecycle_value,
    projection_namespace as _projection_namespace,
    projection_rows_from_payload as _projection_rows_from_payload,
    projection_text_list as _as_text_list,
    todo_matches_agent_scope,
)
from .record_io import (
    build_record_delete_command,
    lark_record_rows as lark_record_rows,
    record_list_is_complete,
    todo_record_entries,
    todo_record_list_is_authoritative,
)
from .sink_visibility import (
    SINK_VISIBILITIES,
    SINK_VISIBILITY_OWNER_ONLY,
    SINK_VISIBILITY_SHARED,
)
from .sync_receipt import (
    compact_lark_kanban_sync_receipt as compact_lark_kanban_sync_receipt,
    render_lark_kanban_markdown as render_lark_kanban_markdown,
)

LARK_KANBAN_HEARTBEAT_VERSION = "loopx_lark_kanban_heartbeat_v0"
LARK_KANBAN_LOCAL_CONFIG_VERSION = "loopx_lark_kanban_local_config_v0"
LARK_KANBAN_SYNC_PROJECTION_VERSION = "loopx_lark_kanban_sync_projection_v0"
DEFAULT_AGENT_ID = "codex-kanban-worker"
TEXT_LIMIT = 4000


def _task_record(overrides: Mapping[str, object]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Task": "",
        "Status": STATUS_TODO,
        "Claim": CLAIM_UNCLAIMED,
        "Claimed By": "",
        "Priority": "P1",
        "Task Class": "advancement_task",
        "Action Kind": "",
        "LoopX Goal ID": "",
        "LoopX Todo ID": "",
        "Scope": "",
        "User Gate": "",
        "Handoff": "",
        "Evidence": "",
        "Run History": "",
        "Worker Command": "",
        "Workdir": "",
        "Last Error": "",
        "Last Result Code": None,
    }
    base.update(overrides)
    return base


def sample_lark_kanban_task(
    *,
    goal_id: str = "loopx-lark-kanban-poc",
    worker_command: str = "",
    workdir: str = "",
) -> dict[str, Any]:
    return _task_record(
        {
            "Task": "POC: triage a public issue and produce a reviewable handoff",
            "Action Kind": "analyze",
            "LoopX Goal ID": goal_id,
            "LoopX Todo ID": "todo_lark_kanban_poc",
            "Scope": "public repo read/write prototype; no credentials, no private logs",
            "Handoff": "Start from the Worker Command. Return compact evidence and next review step.",
            "Worker Command": worker_command,
            "Workdir": workdir,
        }
    )


def lark_kanban_ux_task(
    *,
    goal_id: str = "loopx-lark-kanban-ux",
    worker_command: str = "",
    workdir: str = "",
) -> dict[str, Any]:
    return _task_record(
        {
            "Task": "Optimize LoopX Kanban control-plane UX",
            "Status": STATUS_USER_GATE,
            "Claim": CLAIM_HUMAN,
            "Task Class": "user_gate",
            "Action Kind": "decide",
            "LoopX Goal ID": goal_id,
            "LoopX Todo ID": "todo_lark_kanban_ux",
            "Scope": (
                "Use LoopX itself to reduce operator attention cost while preserving "
                "complete structured context in Lark Base."
            ),
            "User Gate": (
                "Approve the simplified Kanban card profile and heartbeat/subagent "
                "worker model for this prototype."
            ),
            "Handoff": (
                "After approval, move this row to Todo/Unclaimed and let an agent "
                "claim it, produce evidence, and leave the row in Review."
            ),
            "Evidence": "Awaiting human gate pass.",
            "Worker Command": worker_command,
            "Workdir": workdir,
        }
    )


_FEASIBILITY_CASE_SPECS = (
    (
        "Case: notes.zaynjarvis.com LoopX architecture decision",
        "advancement_task",
        "publish_decision_note",
        "todo_case_notes_arch_decision",
        "Publish a public-safe decision note describing LoopX axioms, control-plane shape, and Lark Kanban adapter tradeoffs.",
        "Human approves final wording before publishing.",
        "Draft the architecture/decision note, keep source-of-truth fields in the board, and publish only a concise public version.",
        "Feasible: the board row captures task, gate, scope, handoff, and public evidence pointer for a notes.zaynjarvis.com publish lane.",
        "case seeded: public decision note lane is representable",
    ),
    (
        "Case: P1/P2 human gate timeout with default fallback",
        "user_gate",
        "decide_with_timeout",
        "todo_case_gate_timeout_fallback",
        "Model a human decision that should not block the loop forever; P1/P2 gates can fall back to a default after timeout.",
        "Choose explicit decision, or allow the default fallback to fire after the configured timeout.",
        "Record the gate question in User Gate; record fallback policy in Handoff; apply only via loop state transition.",
        "Feasible: User Gate plus Handoff can separate human choice from the structured state transition that actually changes memory.",
        "case seeded: gate timeout/fallback lane is representable",
    ),
    (
        "Case: cross-session compact memory through OV",
        "continuous_monitor",
        "memory_handoff",
        "todo_case_ov_compact_memory",
        "Carry loop context across sessions through an external compact memory surface instead of relying on raw transcript recall.",
        "Confirm which facts are durable enough to enter compact memory.",
        "Store concise decisions and state deltas externally; keep raw session details out of the Kanban card.",
        "Feasible: the Kanban row can point to compact memory writes while remaining a simple operator control surface.",
        "case seeded: OV compact-memory lane is representable",
    ),
    (
        "Case: output quality vs token and attention cost eval",
        "advancement_task",
        "define_eval",
        "todo_case_quality_cost_eval",
        "Define a loop benchmark that scores delivered output against token spend and human attention cost.",
        "Approve the cost dimensions before using the eval as a gate.",
        "Use Evidence for results, Run History for compact attempts, and Priority to decide whether a rerun is worth more attention.",
        "Feasible: Base fields support an eval lane that keeps quality, token cost, and attention cost visible without bloating the card.",
        "case seeded: quality/cost eval lane is representable",
    ),
)


def lark_kanban_feasibility_cases(
    *,
    goal_id: str = "loopx-lark-kanban-feasibility",
    workdir: str = "",
) -> list[dict[str, Any]]:
    return [
        _task_record(
            {
                "Status": STATUS_REVIEW,
                "Claim": CLAIM_AGENT,
                "Claimed By": DEFAULT_AGENT_ID,
                "LoopX Goal ID": goal_id,
                "Workdir": workdir,
                "Last Result Code": 0,
                "Task": task,
                "Task Class": task_class,
                "Action Kind": action_kind,
                "LoopX Todo ID": todo_id,
                "Scope": scope,
                "User Gate": user_gate,
                "Handoff": handoff,
                "Evidence": evidence,
                "Run History": run_history,
            }
        )
        for task, task_class, action_kind, todo_id, scope, user_gate, handoff, evidence, run_history in _FEASIBILITY_CASE_SPECS
    ]


def _compact_text(value: object, *, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


_ANSI_CONTROL_SEQUENCE_PATTERN = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)?)"
)


def _redact_credential_shapes(value: object) -> str:
    text = _ANSI_CONTROL_SEQUENCE_PATTERN.sub("", str(value or ""))
    text = SECRET_LIKE_SURFACE_PATTERN.sub("[credential-redacted]", text)
    return SINK_COMPOUND_CREDENTIAL_ASSIGNMENT_PATTERN.sub("[credential-redacted]", text)


_SHARED_REDACTION_PATTERNS = (
    (re.compile(r"file://[^\s,;)]+", re.IGNORECASE), "[local-path-redacted]"),
    *((pattern, "[local-path-redacted]") for pattern in PRESENTATION_LOCAL_PATH_PATTERNS),
    (HOME_RELATIVE_PATH_PATTERN, "[local-path-redacted]"),
    (
        re.compile(
            r"https?://[^\s,;)]*(?:feishu|larksuite|internal|corp|localhost|127\.0\.0\.1)[^\s,;)]*",
            re.IGNORECASE,
        ),
        "[private-link-redacted]",
    ),
    (re.compile(r"\b(?:base|tbl|vew|rec)[A-Za-z0-9_-]{6,}\b"), "[external-id-redacted]"),
    (
        re.compile(
            r"\b(?:PRIVATE_MATERIAL|PRIVATE_REF|SECRET_REF|CREDENTIAL_REF)[A-Za-z0-9_.:-]*\b",
            re.IGNORECASE,
        ),
        "[private-ref-redacted]",
    ),
)


def _redact_non_credential_shapes(text: str) -> str:
    for pattern, replacement in _SHARED_REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_public_safe_shapes(value: object) -> str:
    """Apply the shared boundary without collapsing whitespace or truncating."""

    return _redact_non_credential_shapes(_redact_credential_shapes(value))


def _credential_safe_text(value: object, *, limit: int = TEXT_LIMIT) -> str:
    return _compact_text(_redact_credential_shapes(value), limit=limit)


def _public_safe_text(value: object, *, limit: int = TEXT_LIMIT) -> str:
    text = _compact_text(_redact_credential_shapes(value), limit=limit)
    return _compact_text(_redact_non_credential_shapes(text), limit=limit)


_STRUCTURAL_ID_KEYS = frozenset({"id", "record_id", "record_ids"})
_STRUCTURAL_RECORD_ID_PATTERN = re.compile(r"^rec[A-Za-z0-9_-]{6,}$")


def _transform_lark_value(
    value: object,
    *,
    text_fn: Callable[[object], str],
    key_fn: Callable[[object], str],
    check_structural_id: bool = False,
    preserve_external_id: bool = False,
) -> object:
    if isinstance(value, str):
        if check_structural_id and preserve_external_id and _STRUCTURAL_RECORD_ID_PATTERN.fullmatch(value):
            return value
        return text_fn(value)
    if isinstance(value, dict):
        return {
            key_fn(key): _transform_lark_value(
                item,
                text_fn=text_fn,
                key_fn=key_fn,
                check_structural_id=check_structural_id,
                preserve_external_id=str(key).lower() in _STRUCTURAL_ID_KEYS,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        items = [
            _transform_lark_value(
                item,
                text_fn=text_fn,
                key_fn=key_fn,
                check_structural_id=check_structural_id,
                preserve_external_id=preserve_external_id,
            )
            for item in value
        ]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def _public_safe_value(value: object, *, preserve_external_id: bool = False) -> object:
    return _transform_lark_value(
        value, text_fn=_public_safe_text, key_fn=lambda k: _public_safe_text(str(k)),
        check_structural_id=True, preserve_external_id=preserve_external_id,
    )


def _public_safe_lark_values(values: dict[str, Any]) -> dict[str, Any]:
    credential_safe = _credential_safe_lark_values(values)
    return {key: _public_safe_value(value) for key, value in credential_safe.items()}


def _credential_safe_value(value: object) -> object:
    return _transform_lark_value(
        value, text_fn=_redact_credential_shapes, key_fn=_redact_credential_shapes
    )


def _credential_safe_lark_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _credential_safe_value(value) for key, value in values.items()}


def _sink_safe_lark_values(values: dict[str, Any], *, public_safe: bool) -> dict[str, Any]:
    values = _credential_safe_lark_values(values)
    return _public_safe_lark_values(values) if public_safe else values


def _validate_sink_visibility(sink_visibility: str) -> bool:
    if sink_visibility not in SINK_VISIBILITIES:
        raise ValueError(f"sink_visibility must be one of {sorted(SINK_VISIBILITIES)}")
    return sink_visibility == SINK_VISIBILITY_SHARED


def now_lark_datetime(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def append_history(existing: object, entry: str, *, limit: int = TEXT_LIMIT) -> str:
    prior = str(existing or "").strip()
    combined = f"{prior}\n{entry}".strip() if prior else entry
    return combined if len(combined) <= limit else combined[-limit:]


def normalize_select_value(value: object) -> str:
    return str(value[0] or "") if isinstance(value, list) and value else str(value or "")


def choose_heartbeat_task(
    records: list[dict[str, Any]], *, agent_id: str
) -> dict[str, Any] | None:
    own_claim: dict[str, Any] | None = None
    unclaimed: dict[str, Any] | None = None
    for record in records:
        status = normalize_select_value(record.get("Status"))
        claim = normalize_select_value(record.get("Claim"))
        claimed_by = str(record.get("Claimed By") or "").strip()
        if status in {STATUS_DONE, STATUS_BLOCKED, STATUS_REVIEW, STATUS_USER_GATE}:
            continue
        if claimed_by == agent_id and status in {STATUS_CLAIMED, STATUS_RUNNING, STATUS_TODO}:
            own_claim = own_claim or record
            continue
        if status == STATUS_TODO and claim in {"", CLAIM_UNCLAIMED} and not claimed_by:
            unclaimed = unclaimed or record
    return own_claim or unclaimed


def allowed_worker_command(command: str, prefixes: list[str]) -> bool:
    stripped = command.strip()
    return bool(stripped and prefixes) and any(
        stripped == prefix or stripped.startswith(prefix + " ") for prefix in prefixes
    )


def _record_json_args(values: Mapping[str, object]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class LarkKanbanConfig:
    base_token: str
    table_id: str
    view_id: str | None = DEFAULT_STATUS_QUEUE_VIEW
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN
    identity: str = "user"


def default_lark_kanban_config_path(registry_path: Path | None = None) -> Path:
    if registry_path is not None:
        expanded = registry_path.expanduser()
        if expanded.parent.name == ".loopx":
            return expanded.parent / "lark-kanban.json"
    return Path.cwd() / ".loopx" / "lark-kanban.json"


def _local_config_stub(
    config_path: Path, *, exists: bool, error: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": error is None,
        "exists": exists,
        "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
        "path": str(config_path),
        "board": None,
    }
    if error is not None:
        payload["error"] = error
    return payload


def read_lark_kanban_local_config(path: Path) -> dict[str, Any]:
    config_path = path.expanduser()
    if not config_path.exists():
        return _local_config_stub(config_path, exists=False)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _local_config_stub(config_path, exists=True, error=f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return _local_config_stub(config_path, exists=True, error="config root must be a JSON object")
    payload.setdefault("schema_version", LARK_KANBAN_LOCAL_CONFIG_VERSION)
    payload["ok"] = True
    payload["exists"] = True
    payload["path"] = str(config_path)
    return payload


def write_lark_kanban_local_config(path: Path, payload: Mapping[str, object]) -> None:
    config_path = path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    to_write = dict(payload)
    to_write.pop("ok", None)
    to_write.pop("exists", None)
    to_write["schema_version"] = LARK_KANBAN_LOCAL_CONFIG_VERSION
    to_write["updated_at"] = now_lark_datetime()
    write_private_json_atomic(config_path, to_write)


def parse_lark_base_url(base_url: str) -> dict[str, str]:
    parsed = urlparse(base_url.strip())
    query = parse_qs(parsed.query)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    base_token = ""
    for index, part in enumerate(parts):
        if part in {"base", "bitable"} and index + 1 < len(parts):
            base_token = parts[index + 1]
            break
    if not base_token and parts:
        base_token = parts[-1]
    return {
        "base_token": base_token,
        "table_id": _first_query_value(query, "table") or _first_query_value(query, "table_id"),
        "view_id": _first_query_value(query, "view") or _first_query_value(query, "view_id"),
    }


def _first_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[0]).strip() if values else ""


def save_lark_kanban_board_config(
    path: Path,
    *,
    base_token: str,
    table_id: str,
    view_id: str | None = None,
    base_url: str | None = None,
    base_name: str | None = None,
    table_name: str | None = None,
    cli_bin: str = cli_transport.DEFAULT_CLI_BIN,
    identity: str = "user",
    view_ids: dict[str, str] | None = None,
    merge_existing: bool = True,
) -> dict[str, Any]:
    if not base_token:
        raise ValueError("base token is required")
    if not table_id:
        raise ValueError("table id is required")
    config_path = path.expanduser()
    existing = read_lark_kanban_local_config(config_path) if merge_existing else {}
    payload = existing if isinstance(existing, dict) and existing.get("ok") else {}
    raw_board = payload.get("board")
    board: dict[str, Any] = dict(raw_board) if isinstance(raw_board, dict) else {}
    existing_views = board.get("view_ids")
    board.update(
        {
            "base_token": base_token,
            "table_id": table_id,
            "view_id": view_id or board.get("view_id") or DEFAULT_STATUS_QUEUE_VIEW,
            "base_url": base_url or board.get("base_url") or "",
            "base_name": base_name or board.get("base_name") or "",
            "table_name": table_name or board.get("table_name") or table_id,
            "cli_bin": cli_bin,
            "identity": identity,
            "view_ids": {
                **(existing_views if isinstance(existing_views, dict) else {}),
                **(view_ids or {}),
            },
        }
    )
    raw_records = payload.get("todo_records")
    write_lark_kanban_local_config(
        config_path,
        {
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "board": board,
            "todo_records": raw_records if isinstance(raw_records, dict) else {},
        },
    )
    return {
        "ok": True,
        "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
        "path": str(config_path),
        "board": board,
        "next_commands": _next_lark_kanban_commands(board),
    }


def lark_kanban_config_from_payload(payload: Mapping[str, object]) -> LarkKanbanConfig | None:
    board = payload.get("board")
    if not isinstance(board, dict):
        return None
    base_token = str(board.get("base_token") or "").strip()
    table_id = str(board.get("table_id") or "").strip()
    if not base_token or not table_id:
        return None
    return LarkKanbanConfig(
        base_token=base_token,
        table_id=table_id,
        view_id=str(board.get("view_id") or DEFAULT_STATUS_QUEUE_VIEW),
        cli_bin=str(board.get("cli_bin") or cli_transport.DEFAULT_CLI_BIN),
        identity=str(board.get("identity") or "user"),
    )


def _next_lark_kanban_commands(board: Mapping[str, object]) -> list[str]:
    return [
        "loopx lark-kanban doctor",
        "loopx lark-kanban sync-loopx-todos --goal-id <goal-id> --execute",
        (
            "loopx lark-kanban heartbeat --execute-lark --agent-id "
            f"{DEFAULT_AGENT_ID} --allow-command-prefix 'codex exec'"
        ),
    ]


def build_record_upsert_command(
    config: LarkKanbanConfig, *, record_id: str | None, values: dict[str, Any]
) -> list[str]:
    safe_values = _credential_safe_lark_values(values)
    extra = (["--record-id", record_id] if record_id else []) + [
        "--json", _record_json_args(safe_values)
    ]
    return _base_cmd(
        config.cli_bin, "+record-upsert", config.identity, *extra,
        base_token=config.base_token, table_id=config.table_id,
    )


def build_record_list_command(config: LarkKanbanConfig) -> list[str]:
    extra = ["--format", "json", "--offset", "0", "--limit", "200"]
    if config.view_id:
        extra.extend(["--view-id", config.view_id])
    return _base_cmd(
        config.cli_bin, "+record-list", config.identity, *extra,
        base_token=config.base_token, table_id=config.table_id,
    )


def _lark_record_from_projection_block(
    block: dict[str, Any], *, goal_id: str, source_id: str, public_safe: bool = False
) -> dict[str, Any]:
    values = _lark_record_from_todo_block(
        block, goal_id=goal_id, state_file=Path(f"{source_id}.projection"),
        priority=str(block.get("priority") or "P2"),
    )
    original_id = str(block.get("original_todo_id") or "")
    evidence_parts = [
        str(block.get("evidence") or block.get("reason") or block.get("note") or "").strip(),
        f"source_id={source_id}",
    ]
    if original_id:
        evidence_parts.append(f"original_todo_id={original_id}")
    evidence_parts.extend(_projection_lifecycle_parts(block, source_id=source_id))
    evidence_parts = list(dict.fromkeys(part for part in evidence_parts if part))
    lifecycle_state = _projection_lifecycle_state(block)
    default_handoff = f"Synced from LoopX projection source={source_id}"
    if lifecycle_state in {"superseded", "migrated", "retired"}:
        superseded_by = (
            ", ".join(_as_text_list(_projection_lifecycle_value(block, "superseded_by")))
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
    rows: list[dict[str, Any]], *, default_goal_id: str, source_id: str, public_safe: bool
) -> list[tuple[str, str, dict[str, Any]]]:
    items: list[tuple[str, str, dict[str, Any]]] = []
    for row in rows:
        row_goal_id = str(row.get("goal_id") or default_goal_id)
        todo_id = str(row.get("todo_id") or "").strip()
        values = _lark_record_from_projection_block(
            row, goal_id=row_goal_id, source_id=source_id, public_safe=public_safe
        )
        items.append((f"{row_goal_id}:{todo_id}", todo_id, values))
    return items


def _upsert_record_batch(
    config: LarkKanbanConfig,
    items: list[tuple[str, str, dict[str, Any]]],
    *,
    record_map: dict[str, str],
    commands: list[dict[str, Any]],
    results: list[dict[str, Any]],
    execute: bool,
    runner: cli_transport.CommandRunner,
) -> tuple[bool, int]:
    ok = True
    successful_write_count = 0
    for key, todo_id, values in items:
        result = cli_transport.run_command(
            build_record_upsert_command(config, record_id=record_map.get(key), values=values),
            execute=execute, runner=runner,
        )
        commands.append(result)
        record_id = _extract_created_record_id(result.get("json")) or record_map.get(key)
        if execute and result.get("executed") and result.get("ok"):
            successful_write_count += 1
        if execute and result.get("ok") and record_id:
            record_map[key] = record_id
        results.append({"todo_id": todo_id, "record_id": record_id, "command": result, "values": values})
        ok = ok and bool(result.get("ok"))
        if execute and not result.get("ok"):
            break
    return ok, successful_write_count


def sync_loopx_projection_to_lark_kanban(
    config: LarkKanbanConfig,
    *,
    projection: dict[str, Any],
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
) -> dict[str, Any]:
    if not isinstance(projection, dict):
        raise ValueError("projection must be a JSON object")
    public_safe = _validate_sink_visibility(sink_visibility)
    resolved_source_id, namespace_warnings = _projection_namespace(projection, source_id)
    resolved_goal_id, rows, warnings = _projection_rows_from_payload(
        projection, goal_id=goal_id, agent_id=agent_id,
        source_id=resolved_source_id, include_done=include_done, limit=limit,
    )
    warnings = [*namespace_warnings, *warnings]

    complete_rows = rows
    complete_warnings: list[str] = []
    if reconcile_source:
        _, complete_rows, complete_warnings = _projection_rows_from_payload(
            projection, goal_id=goal_id, agent_id=None,
            source_id=resolved_source_id, include_done=True, limit=2**31 - 1,
        )
    validate_projection_source_reconcile_request(
        requested=reconcile_source, source_snapshot_complete=source_snapshot_complete,
        agent_id=agent_id, include_done=include_done, namespace_warnings=namespace_warnings,
        selected_row_count=len(rows), complete_row_count=len(complete_rows),
        complete_warnings=complete_warnings,
    )

    commands: list[dict[str, Any]] = []
    schema_check: dict[str, Any] | None = None
    base_payload: dict[str, Any] = {
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
        schema_check = _inspect_lark_kanban_schema(config, commands=commands, runner=runner)
        if not schema_check.get("ok"):
            return {
                **base_payload, "ok": False, "records": [], "commands": commands,
                "schema_check": schema_check, "error": _schema_check_error(schema_check),
            }

    local, record_map, remote_records, remote_list_complete = _load_lark_todo_record_map(
        config, config_path=config_path, execute=execute,
        inspect_remote=reconcile_source, commands=commands, runner=runner,
    )
    if reconcile_source and not remote_list_complete:
        raise ValueError("source reconcile requires a complete remote record list")

    desired_keys = {
        f"{str(row.get('goal_id') or resolved_goal_id)}:{str(row.get('todo_id') or '').strip()}"
        for row in rows
    }
    reconcile_plan = (
        plan_projection_source_reconcile(
            goal_id=resolved_goal_id, source_id=resolved_source_id,
            desired_keys=sorted(desired_keys), local_record_map=record_map,
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

    results: list[dict[str, Any]] = []
    ok, _ = _upsert_record_batch(
        config,
        _projection_upsert_items(
            rows, default_goal_id=resolved_goal_id, source_id=resolved_source_id, public_safe=public_safe
        ),
        record_map=record_map, commands=commands, results=results, execute=execute, runner=runner,
    )

    executed_remote_delete_count = 0
    delete_ids = reconcile_plan.get("remote_delete_record_ids")
    if ok and reconcile_source and isinstance(delete_ids, list) and delete_ids:
        delete_result = cli_transport.run_command(
            build_record_delete_command(config, record_ids=[str(item) for item in delete_ids]),  # type: ignore[arg-type]
            execute=execute, runner=runner,
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
) -> dict[str, Any]:
    from ....capabilities.issue_fix.outcome_projection import (
        build_issue_fix_outcome_collection_from_domain_state,
    )
    from ....control_plane.todos.active_state_editing import section_bounds, todo_blocks
    from ....control_plane.todos.path_resolution import resolve_todo_state_path
    from ....control_plane.todos.todo_semantics import todo_priority_label

    public_safe = _validate_sink_visibility(sink_visibility)
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path, goal_id=goal_id, project=project, state_file=state_file
    )
    lines = resolved_state_file.read_text(encoding="utf-8").splitlines()
    todos: list[dict[str, Any]] = []
    for role in ("user", "agent"):
        bounds = section_bounds(lines, role)
        if not bounds:
            continue
        start, end, heading = bounds
        for block in todo_blocks(lines, start, end, role=role, source_section=heading):
            status = normalize_todo_status(str(block.get("status") or "").strip())
            if (block.get("done") or status == TODO_STATUS_DONE) and not include_done:
                continue
            candidate = {**block, "role": role, "source_section": heading}
            if todo_matches_agent_scope(candidate, agent_id):
                todos.append(candidate)
    todos = todos[:limit]

    outcome_projection = build_issue_fix_outcome_collection_from_domain_state(
        goal_id=goal_id, project=resolved_project or project or Path(".")
    )
    outcome_source_id, outcome_namespace_warnings = _projection_namespace(outcome_projection, None)
    outcome_source_count = len(outcome_projection.get("issue_fix_outcomes") or [])
    _, outcome_rows, outcome_row_warnings = _projection_rows_from_payload(
        outcome_projection, goal_id=goal_id, agent_id=agent_id,
        source_id=outcome_source_id, include_done=True, limit=outcome_source_count,
    )
    outcome_warnings = [
        *outcome_namespace_warnings,
        *list(outcome_projection.get("warnings") or []),
        *outcome_row_warnings,
    ]

    commands: list[dict[str, Any]] = []
    schema_check: dict[str, Any] | None = None
    base_payload: dict[str, Any] = {
        "schema_version": "loopx_lark_kanban_sync_todos_v0",
        "execute": execute,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "base_token": config.base_token,
        "table_id": config.table_id,
        "view_id": config.view_id,
        "project": str(resolved_project) if resolved_project else None,
        "state_file": str(resolved_state_file),
        "todo_count": len(todos),
        "issue_fix_outcome_count": len(outcome_rows),
        "limit_policy": {"todo_rows": limit, "issue_fix_outcomes": "complete_source_projection"},
        "issue_fix_source_counts": outcome_projection.get("source_counts"),
        "warnings": outcome_warnings,
        "config_path": str(config_path) if config_path else None,
    }
    if execute:
        schema_check = _inspect_lark_kanban_schema(config, commands=commands, runner=runner)
        if not schema_check.get("ok"):
            return {
                **base_payload, "ok": False, "records": [], "commands": commands,
                "schema_check": schema_check, "successful_write_count": 0,
                "external_write_performed": False, "error": _schema_check_error(schema_check),
            }

    local, record_map, _, _ = _load_lark_todo_record_map(
        config, config_path=config_path, execute=execute,
        inspect_remote=False, commands=commands, runner=runner,
    )

    todo_items: list[tuple[str, str, dict[str, Any]]] = []
    for block in todos:
        todo_id = str(block.get("todo_id") or "").strip()
        values = _lark_record_from_todo_block(
            block, goal_id=goal_id, state_file=resolved_state_file,
            priority=todo_priority_label(block, text_mode="prefix") or "P2",
        )
        if public_safe:
            values = _public_safe_lark_values(values)
        todo_items.append((f"{goal_id}:{todo_id}", todo_id, values))

    results: list[dict[str, Any]] = []
    ok, successful_write_count = _upsert_record_batch(
        config, todo_items, record_map=record_map, commands=commands,
        results=results, execute=execute, runner=runner,
    )
    if ok and outcome_rows:
        ok, outcome_writes = _upsert_record_batch(
            config,
            _projection_upsert_items(
                outcome_rows, default_goal_id=goal_id, source_id=outcome_source_id, public_safe=public_safe
            ),
            record_map=record_map, commands=commands, results=results, execute=execute, runner=runner,
        )
        successful_write_count += outcome_writes

    if execute and successful_write_count:
        _persist_lark_todo_record_map(config, config_path=config_path, local=local, record_map=record_map)

    return {
        **base_payload,
        "ok": ok,
        "schema_check": schema_check,
        "records": results,
        "commands": commands,
        "successful_write_count": successful_write_count,
        "external_write_performed": bool(successful_write_count),
    }


def _load_lark_todo_record_map(
    config: LarkKanbanConfig,
    *,
    config_path: Path | None,
    execute: bool,
    inspect_remote: bool,
    commands: list[dict[str, Any]],
    runner: cli_transport.CommandRunner,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, str]], bool | None]:
    local = read_lark_kanban_local_config(config_path) if config_path else {}
    record_map = _todo_record_map_from_local_config(local)
    if not execute and not inspect_remote:
        return local, record_map, [], None

    list_config = LarkKanbanConfig(
        base_token=config.base_token, table_id=config.table_id, view_id=None,
        cli_bin=config.cli_bin, identity=config.identity,
    )
    list_result = cli_transport.run_command(
        build_record_list_command(list_config), execute=True, runner=runner
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
    record_map: dict[str, str],
) -> None:
    if not config_path:
        return
    raw_board = local.get("board")
    board = raw_board if isinstance(raw_board, dict) and raw_board else {
        "base_token": config.base_token,
        "table_id": config.table_id,
        "view_id": config.view_id,
        "cli_bin": config.cli_bin,
        "identity": config.identity,
    }
    write_lark_kanban_local_config(
        config_path,
        {
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "board": board,
            "todo_records": record_map,
        },
    )


def _lark_record_from_todo_block(
    block: dict[str, Any], *, goal_id: str, state_file: Path, priority: str
) -> dict[str, Any]:
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
    values = {
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


def seed_lark_kanban_task(
    config: LarkKanbanConfig,
    *,
    task: dict[str, Any],
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, Any]:
    public_safe = _validate_sink_visibility(sink_visibility)
    safe_task = _sink_safe_lark_values(task, public_safe=public_safe)
    result = cli_transport.run_command(
        build_record_upsert_command(config, record_id=None, values=safe_task),
        execute=execute, runner=runner,
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
    records: list[dict[str, Any]],
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute: bool = False,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
) -> dict[str, Any]:
    public_safe = _validate_sink_visibility(sink_visibility)
    results: list[dict[str, Any]] = []
    ok = True
    for record in records:
        result = seed_lark_kanban_task(
            config, task=record, sink_visibility=sink_visibility, execute=execute, runner=runner
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
    allowed_command_prefixes: list[str] | None,
    runner: cli_transport.CommandRunner,
    worker_timeout_seconds: float,
) -> tuple[dict[str, Any], str, str, int | None]:
    safe_command = _redact_credential_shapes(command)
    if not command:
        return (
            {"attempted": False, "executed": False, "ok": False, "reason": "worker_command_missing", "command": ""},
            STATUS_USER_GATE,
            "worker command missing; user must provide Worker Command or heartbeat --worker-command",
            None,
        )
    if not execute_worker:
        return (
            {"attempted": True, "executed": False, "ok": True, "reason": "execute_worker_not_set", "command": safe_command},
            STATUS_CLAIMED,
            f"dry-run worker command: {safe_command}",
            None,
        )
    if not allowed_worker_command(command, allowed_command_prefixes or []):
        return (
            {
                "attempted": True, "executed": False, "ok": False,
                "reason": "worker_command_prefix_not_allowed", "command": safe_command,
                "allowed_prefixes": allowed_command_prefixes or [],
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
    worker: dict[str, Any] = {
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
    fixture: dict[str, Any] | None = None,
    worker_command: str | None = None,
    sink_visibility: str = SINK_VISIBILITY_SHARED,
    execute_lark: bool = False,
    execute_worker: bool = False,
    complete_on_success: bool = False,
    allowed_command_prefixes: list[str] | None = None,
    runner: cli_transport.CommandRunner = cli_transport.default_subprocess_runner,
    now: datetime | None = None,
    worker_timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    public_safe = _validate_sink_visibility(sink_visibility)
    commands: list[dict[str, Any]] = []
    if fixture is None:
        list_result = cli_transport.run_command(
            build_record_list_command(config), execute=execute_lark, runner=runner
        )
        commands.append(list_result)
        if execute_lark and not list_result.get("ok"):
            return _heartbeat_payload(
                ok=False, decision="record_list_failed", agent_id=agent_id, commands=commands, records=[]
            )
        parsed = list_result.get("json") if execute_lark else {"data": {"fields": [], "data": []}}
    else:
        parsed = fixture
    records = lark_record_rows(parsed if isinstance(parsed, dict) else {})
    task = choose_heartbeat_task(records, agent_id=agent_id)
    if task is None:
        return _heartbeat_payload(
            ok=True, decision="no_claimable_task", agent_id=agent_id, commands=commands, records=records
        )
    record_id = str(task.get("_record_id") or "").strip()
    if not record_id:
        return _heartbeat_payload(
            ok=False, decision="selected_task_missing_record_id", agent_id=agent_id,
            commands=commands, records=records, selected_task=task,
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
    claim_result = cli_transport.run_command(
        build_record_upsert_command(config, record_id=record_id, values=claim_values),
        execute=execute_lark, runner=runner,
    )
    commands.append(claim_result)
    if execute_lark and not claim_result.get("ok"):
        return _heartbeat_payload(
            ok=False, decision="claim_failed", agent_id=agent_id,
            commands=commands, records=records, selected_task=task,
        )
    command = (worker_command if worker_command is not None else str(task.get("Worker Command") or "")).strip()
    worker, final_status, evidence, result_code = _run_heartbeat_worker(
        task, command=command, execute_worker=execute_worker,
        complete_on_success=complete_on_success, allowed_command_prefixes=allowed_command_prefixes,
        runner=runner, worker_timeout_seconds=worker_timeout_seconds,
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
    finish_result = cli_transport.run_command(
        build_record_upsert_command(config, record_id=record_id, values=finish_values),
        execute=execute_lark, runner=runner,
    )
    commands.append(finish_result)
    ok = bool(worker.get("ok")) and (not execute_lark or bool(finish_result.get("ok")))
    return _heartbeat_payload(
        ok=ok, decision="task_processed", agent_id=agent_id, commands=commands, records=records,
        selected_task=task, worker=worker, final_status=final_status, writeback=finish_values,
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
    commands: list[dict[str, Any]],
    records: list[dict[str, Any]],
    selected_task: dict[str, Any] | None = None,
    worker: dict[str, Any] | None = None,
    final_status: str | None = None,
    writeback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "schema_version": LARK_KANBAN_HEARTBEAT_VERSION,
        "agent_id": agent_id,
        "decision": decision,
        "record_count": len(records),
        "selected_record_id": selected_task.get("_record_id") if selected_task else None,
        "selected_task": selected_task,
        "worker": worker,
        "final_status": final_status,
        "writeback": writeback,
        "commands": commands,
    }
