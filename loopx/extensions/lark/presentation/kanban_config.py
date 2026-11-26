"""Shared Lark Kanban configuration, redaction, fixture, and command helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ....public_safe_text import (
    HOME_RELATIVE_PATH_PATTERN,
    PRESENTATION_LOCAL_PATH_PATTERNS,
    SECRET_LIKE_SURFACE_PATTERN,
    SINK_COMPOUND_CREDENTIAL_ASSIGNMENT_PATTERN,
)
from .. import cli_transport as cli_transport
from ..private_json import write_private_json_atomic
from .kanban_board import (
    CLAIM_UNCLAIMED,
    DEFAULT_STATUS_QUEUE_VIEW,
    CLAIM_HUMAN,
    CLAIM_AGENT,
    STATUS_BLOCKED,
    STATUS_CLAIMED,
    STATUS_DONE,
    STATUS_REVIEW,
    STATUS_RUNNING,
    STATUS_TODO,
    STATUS_USER_GATE,
    _base_cmd,
)
from .sink_visibility import SINK_VISIBILITIES, SINK_VISIBILITY_SHARED

LARK_KANBAN_HEARTBEAT_VERSION = "loopx_lark_kanban_heartbeat_v0"
LARK_KANBAN_LOCAL_CONFIG_VERSION = "loopx_lark_kanban_local_config_v0"
LARK_KANBAN_SYNC_PROJECTION_VERSION = "loopx_lark_kanban_sync_projection_v0"
DEFAULT_AGENT_ID = "codex-kanban-worker"
TEXT_LIMIT = 4000


def _task_record(overrides: Mapping[str, object]) -> dict[str, object]:
    base: dict[str, object] = {
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
) -> dict[str, object]:
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
) -> dict[str, object]:
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
) -> list[dict[str, object]]:
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


def _coerce_mapping_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _coerce_mapping_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_mapping_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_coerce_mapping_value(item) for item in value)
    return value


def _coerce_mapping_tree(mapping: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _coerce_mapping_value(item) for key, item in mapping.items()}


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
    if isinstance(value, Mapping):
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
        value,
        text_fn=_public_safe_text,
        key_fn=lambda k: _public_safe_text(str(k)),
        check_structural_id=True,
        preserve_external_id=preserve_external_id,
    )


def _public_safe_lark_values(values: Mapping[str, object]) -> dict[str, object]:
    credential_safe = _credential_safe_lark_values(values)
    return {key: _public_safe_value(value) for key, value in credential_safe.items()}


def _credential_safe_value(value: object) -> object:
    return _transform_lark_value(
        value,
        text_fn=_redact_credential_shapes,
        key_fn=_redact_credential_shapes,
    )


def _credential_safe_lark_values(values: Mapping[str, object]) -> dict[str, object]:
    return {key: _credential_safe_value(value) for key, value in values.items()}


def _sink_safe_lark_values(values: Mapping[str, object], *, public_safe: bool) -> dict[str, object]:
    safe_values = _credential_safe_lark_values(values)
    return _public_safe_lark_values(safe_values) if public_safe else safe_values


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
    records: Sequence[Mapping[str, object]], *, agent_id: str
) -> dict[str, object] | None:
    own_claim: dict[str, object] | None = None
    unclaimed: dict[str, object] | None = None
    for record in records:
        status = normalize_select_value(record.get("Status"))
        claim = normalize_select_value(record.get("Claim"))
        claimed_by = str(record.get("Claimed By") or "").strip()
        if status in {STATUS_DONE, STATUS_BLOCKED, STATUS_REVIEW, STATUS_USER_GATE}:
            continue
        if claimed_by == agent_id and status in {STATUS_CLAIMED, STATUS_RUNNING, STATUS_TODO}:
            if own_claim is None:
                own_claim = record if isinstance(record, dict) else dict(record)
            continue
        if status == STATUS_TODO and claim in {"", CLAIM_UNCLAIMED} and not claimed_by:
            if unclaimed is None:
                unclaimed = record if isinstance(record, dict) else dict(record)
    return own_claim or unclaimed


def allowed_worker_command(command: str, prefixes: Sequence[str]) -> bool:
    stripped = command.strip()
    return bool(stripped and prefixes) and any(
        stripped == prefix or stripped.startswith(prefix + " ") for prefix in prefixes
    )


def _record_json_args(values: Mapping[str, object]) -> str:
    return json.dumps(dict(values), ensure_ascii=False, separators=(",", ":"))


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
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": error is None,
        "exists": exists,
        "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
        "path": str(config_path),
        "board": None,
    }
    if error is not None:
        payload["error"] = error
    return payload


def read_lark_kanban_local_config(path: Path) -> dict[str, object]:
    config_path = path.expanduser()
    if not config_path.exists():
        return _local_config_stub(config_path, exists=False)
    try:
        raw_payload: object = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _local_config_stub(config_path, exists=True, error=f"invalid JSON: {exc}")
    if not isinstance(raw_payload, dict):
        return _local_config_stub(config_path, exists=True, error="config root must be a JSON object")
    payload: dict[str, object] = {str(key): value for key, value in raw_payload.items()}
    payload.setdefault("schema_version", LARK_KANBAN_LOCAL_CONFIG_VERSION)
    payload["ok"] = True
    payload["exists"] = True
    payload["path"] = str(config_path)
    return payload


def write_lark_kanban_local_config(path: Path, payload: Mapping[str, object]) -> None:
    config_path = path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    to_write: dict[str, object] = _coerce_mapping_tree(payload)
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


def _first_query_value(query: Mapping[str, Sequence[str]], key: str) -> str:
    values = query.get(key) or ()
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
    view_ids: Mapping[str, object] | None = None,
    merge_existing: bool = True,
) -> dict[str, object]:
    if not base_token:
        raise ValueError("base token is required")
    if not table_id:
        raise ValueError("table id is required")
    config_path = path.expanduser()
    existing = read_lark_kanban_local_config(config_path) if merge_existing else {}
    payload: Mapping[str, object] = existing if existing.get("ok") else {}
    raw_board = payload.get("board")
    board: dict[str, object] = (
        _coerce_mapping_tree(raw_board) if isinstance(raw_board, Mapping) else {}
    )
    existing_views = board.get("view_ids")
    merged_views: dict[str, object] = (
        _coerce_mapping_tree(existing_views)
        if isinstance(existing_views, Mapping)
        else {}
    )
    if view_ids:
        merged_views.update(_coerce_mapping_tree(view_ids))
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
            "view_ids": merged_views,
        }
    )
    raw_records = payload.get("todo_records")
    write_lark_kanban_local_config(
        config_path,
        {
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "board": board,
            "todo_records": _coerce_mapping_tree(raw_records) if isinstance(raw_records, Mapping) else {},
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
    if not isinstance(board, Mapping):
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
    del board
    return [
        "loopx lark-kanban doctor",
        "loopx lark-kanban sync-loopx-todos --goal-id <goal-id> --execute",
        (
            "loopx lark-kanban heartbeat --execute-lark --agent-id "
            f"{DEFAULT_AGENT_ID} --allow-command-prefix 'codex exec'"
        ),
    ]


def build_record_upsert_command(
    config: LarkKanbanConfig, *, record_id: str | None, values: Mapping[str, object]
) -> list[str]:
    safe_values = _credential_safe_lark_values(values)
    extra = (["--record-id", record_id] if record_id else []) + [
        "--json",
        _record_json_args(safe_values),
    ]
    return _base_cmd(
        config.cli_bin,
        "+record-upsert",
        config.identity,
        *extra,
        base_token=config.base_token,
        table_id=config.table_id,
    )


def build_record_list_command(config: LarkKanbanConfig) -> list[str]:
    extra = ["--format", "json", "--offset", "0", "--limit", "200"]
    if config.view_id:
        extra.extend(["--view-id", config.view_id])
    return _base_cmd(
        config.cli_bin,
        "+record-list",
        config.identity,
        *extra,
        base_token=config.base_token,
        table_id=config.table_id,
    )
