from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
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
from ..private_json import write_private_json_atomic
from . import issue_fix_surface
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
    lark_record_rows,
    record_list_is_complete,
    todo_record_list_is_authoritative,
    todo_record_entries,
)
from .sink_visibility import SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITY_SHARED, SINK_VISIBILITIES
from .sync_receipt import (
    compact_lark_kanban_sync_receipt as compact_lark_kanban_sync_receipt,
    render_lark_kanban_markdown as render_lark_kanban_markdown,
)

LARK_KANBAN_SCHEMA_VERSION = "loopx_lark_kanban_control_plane_v0"
LARK_KANBAN_HEARTBEAT_VERSION = "loopx_lark_kanban_heartbeat_v0"
LARK_KANBAN_LOCAL_CONFIG_VERSION = "loopx_lark_kanban_local_config_v0"
LARK_KANBAN_SYNC_PROJECTION_VERSION = "loopx_lark_kanban_sync_projection_v0"
DEFAULT_TABLE_NAME = "LoopX Control Plane"
DEFAULT_AGENT_ID = "codex-kanban-worker"
DEFAULT_CLI_BIN = "lark-cli"
DEFAULT_STATUS_QUEUE_VIEW = "Worker Queue"
OPERATOR_CARD_FIELDS = ["Task", "Claim", "Priority", "User Gate", "Evidence", "Status"]
_LARK_CREDENTIAL_ARGUMENTS = frozenset(
    {
        "--access-key",
        "--access-token",
        "--ak",
        "--app-secret",
        "--authorization",
        "--bearer-token",
        "--client-secret",
        "--secret-key",
        "--sk",
        "--tenant-access-token",
        "--user-access-token",
    }
)

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

TEXT_LIMIT = 4000
OUTPUT_LIMIT = 1800


CommandRunner = Callable[[list[str], Optional[Path], Optional[float]], dict[str, Any]]


def _select_options(names: list[str]) -> list[dict[str, str]]:
    hues = {
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
    return [
        {
            "name": name,
            "hue": hues.get(name, "Blue"),
            "lightness": "Light",
        }
        for name in names
    ]


def lark_kanban_field_definitions() -> list[dict[str, Any]]:
    return [
        {"name": "Task", "type": "text", "style": {"type": "plain"}},
        {
            "name": "Status",
            "type": "select",
            "multiple": False,
            "options": _select_options(
                [
                    STATUS_TODO,
                    STATUS_CLAIMED,
                    STATUS_RUNNING,
                    STATUS_USER_GATE,
                    STATUS_BLOCKED,
                    STATUS_REVIEW,
                    STATUS_DONE,
                ]
            ),
        },
        {
            "name": "Claim",
            "type": "select",
            "multiple": False,
            "options": _select_options([CLAIM_UNCLAIMED, CLAIM_HUMAN, CLAIM_AGENT]),
        },
        {"name": "Claimed By", "type": "text", "style": {"type": "plain"}},
        {
            "name": "Priority",
            "type": "select",
            "multiple": False,
            "options": _select_options(["P0", "P1", "P2", "P3"]),
        },
        {
            "name": "Task Class",
            "type": "select",
            "multiple": False,
            "options": _select_options(
                ["advancement_task", "continuous_monitor", "user_gate", "blocker"]
            ),
        },
        {"name": "Action Kind", "type": "text", "style": {"type": "plain"}},
        {"name": "LoopX Goal ID", "type": "text", "style": {"type": "plain"}},
        {"name": "LoopX Todo ID", "type": "text", "style": {"type": "plain"}},
        *issue_fix_surface.issue_fix_field_definitions(_select_options),
        {"name": "Scope", "type": "text", "style": {"type": "plain"}},
        {"name": "User Gate", "type": "text", "style": {"type": "plain"}},
        {"name": "Handoff", "type": "text", "style": {"type": "plain"}},
        {"name": "Evidence", "type": "text", "style": {"type": "plain"}},
        {"name": "Run History", "type": "text", "style": {"type": "plain"}},
        {"name": "Worker Command", "type": "text", "style": {"type": "plain"}},
        {"name": "Workdir", "type": "text", "style": {"type": "plain"}},
        {"name": "Last Error", "type": "text", "style": {"type": "plain"}},
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
        {
            "name": "Last Heartbeat",
            "type": "datetime",
            "style": {"format": "yyyy-MM-dd HH:mm"},
        },
        {
            "name": "Created At",
            "type": "created_at",
            "style": {"format": "yyyy-MM-dd HH:mm"},
        },
        {
            "name": "Updated At",
            "type": "updated_at",
            "style": {"format": "yyyy-MM-dd HH:mm"},
        },
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
            "issue_fix_outcome": "First-class issue row with repository, issue, PR, route, stage, validation, and outcome dimensions.",
            "issue_fix_metric": "Monthly Impact row with repository baseline, current value, delta, ratio lineage, source link, freshness, and explicit missing data.",
        },
        "fields": lark_kanban_field_definitions(),
        "views": lark_kanban_views(),
        "operator_view": {
            "kanban_card_fields": OPERATOR_CARD_FIELDS,
            "issue_fix_card_fields": issue_fix_surface.ISSUE_FIX_CARD_FIELDS,
            "issue_fix_views": [issue_fix_surface.DEFAULT_ISSUE_FIX_GRID_VIEW, issue_fix_surface.DEFAULT_ISSUE_FIX_KANBAN_VIEW],
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
            "fallback": "agent heartbeat polls Worker Queue, soft-claims one task, runs worker command, writes evidence",
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


def sample_lark_kanban_task(
    *,
    goal_id: str = "loopx-lark-kanban-poc",
    worker_command: str = "",
    workdir: str = "",
) -> dict[str, Any]:
    return {
        "Task": "POC: triage a public issue and produce a reviewable handoff",
        "Status": STATUS_TODO,
        "Claim": CLAIM_UNCLAIMED,
        "Claimed By": "",
        "Priority": "P1",
        "Task Class": "advancement_task",
        "Action Kind": "analyze",
        "LoopX Goal ID": goal_id,
        "LoopX Todo ID": "todo_lark_kanban_poc",
        "Scope": "public repo read/write prototype; no credentials, no private logs",
        "User Gate": "",
        "Handoff": "Start from the Worker Command. Return compact evidence and next review step.",
        "Evidence": "",
        "Run History": "",
        "Worker Command": worker_command,
        "Workdir": workdir,
        "Last Error": "",
        "Last Result Code": None,
    }


def lark_kanban_operator_card_fields() -> list[str]:
    return list(OPERATOR_CARD_FIELDS)


def lark_kanban_ux_task(
    *,
    goal_id: str = "loopx-lark-kanban-ux",
    worker_command: str = "",
    workdir: str = "",
) -> dict[str, Any]:
    return {
        "Task": "Optimize LoopX Kanban control-plane UX",
        "Status": STATUS_USER_GATE,
        "Claim": CLAIM_HUMAN,
        "Claimed By": "",
        "Priority": "P1",
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
        "Run History": "",
        "Worker Command": worker_command,
        "Workdir": workdir,
        "Last Error": "",
        "Last Result Code": None,
    }


def lark_kanban_feasibility_cases(
    *,
    goal_id: str = "loopx-lark-kanban-feasibility",
    workdir: str = "",
) -> list[dict[str, Any]]:
    common = {
        "Status": STATUS_REVIEW,
        "Claim": CLAIM_AGENT,
        "Claimed By": DEFAULT_AGENT_ID,
        "Priority": "P1",
        "LoopX Goal ID": goal_id,
        "Workdir": workdir,
        "Last Error": "",
        "Last Result Code": 0,
    }
    return [
        {
            **common,
            "Task": "Case: notes.zaynjarvis.com LoopX architecture decision",
            "Task Class": "advancement_task",
            "Action Kind": "publish_decision_note",
            "LoopX Todo ID": "todo_case_notes_arch_decision",
            "Scope": (
                "Publish a public-safe decision note describing LoopX axioms, "
                "control-plane shape, and Lark Kanban adapter tradeoffs."
            ),
            "User Gate": "Human approves final wording before publishing.",
            "Handoff": (
                "Draft the architecture/decision note, keep source-of-truth "
                "fields in the board, and publish only a concise public version."
            ),
            "Evidence": (
                "Feasible: the board row captures task, gate, scope, handoff, "
                "and public evidence pointer for a notes.zaynjarvis.com publish lane."
            ),
            "Run History": "case seeded: public decision note lane is representable",
            "Worker Command": "",
        },
        {
            **common,
            "Task": "Case: P1/P2 human gate timeout with default fallback",
            "Task Class": "user_gate",
            "Action Kind": "decide_with_timeout",
            "LoopX Todo ID": "todo_case_gate_timeout_fallback",
            "Scope": (
                "Model a human decision that should not block the loop forever; "
                "P1/P2 gates can fall back to a default after timeout."
            ),
            "User Gate": (
                "Choose explicit decision, or allow the default fallback to fire "
                "after the configured timeout."
            ),
            "Handoff": (
                "Record the gate question in User Gate; record fallback policy "
                "in Handoff; apply only via loop state transition."
            ),
            "Evidence": (
                "Feasible: User Gate plus Handoff can separate human choice from "
                "the structured state transition that actually changes memory."
            ),
            "Run History": "case seeded: gate timeout/fallback lane is representable",
            "Worker Command": "",
        },
        {
            **common,
            "Task": "Case: cross-session compact memory through OV",
            "Task Class": "continuous_monitor",
            "Action Kind": "memory_handoff",
            "LoopX Todo ID": "todo_case_ov_compact_memory",
            "Scope": (
                "Carry loop context across sessions through an external compact "
                "memory surface instead of relying on raw transcript recall."
            ),
            "User Gate": "Confirm which facts are durable enough to enter compact memory.",
            "Handoff": (
                "Store concise decisions and state deltas externally; keep raw "
                "session details out of the Kanban card."
            ),
            "Evidence": (
                "Feasible: the Kanban row can point to compact memory writes while "
                "remaining a simple operator control surface."
            ),
            "Run History": "case seeded: OV compact-memory lane is representable",
            "Worker Command": "",
        },
        {
            **common,
            "Task": "Case: output quality vs token and attention cost eval",
            "Task Class": "advancement_task",
            "Action Kind": "define_eval",
            "LoopX Todo ID": "todo_case_quality_cost_eval",
            "Scope": (
                "Define a loop benchmark that scores delivered output against "
                "token spend and human attention cost."
            ),
            "User Gate": "Approve the cost dimensions before using the eval as a gate.",
            "Handoff": (
                "Use Evidence for results, Run History for compact attempts, and "
                "Priority to decide whether a rerun is worth more attention."
            ),
            "Evidence": (
                "Feasible: Base fields support an eval lane that keeps quality, "
                "token cost, and attention cost visible without bloating the card."
            ),
            "Run History": "case seeded: quality/cost eval lane is representable",
            "Worker Command": "",
        },
    ]


def default_subprocess_runner(
    args: list[str],
    cwd: Path | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": False,
    }


def _run_command(
    args: list[str],
    *,
    execute: bool,
    runner: CommandRunner = default_subprocess_runner,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    credential_argument = next(
        (
            argument.split("=", 1)[0].lower()
            for argument in args
            if argument.split("=", 1)[0].lower() in _LARK_CREDENTIAL_ARGUMENTS
        ),
        None,
    )
    if credential_argument:
        raise ValueError(
            f"{credential_argument} must remain in lark-cli auth; "
            "the Kanban adapter never accepts inline credentials"
        )
    command = shlex.join(args)
    if not execute:
        return {
            "command": command,
            "executed": False,
            "ok": True,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "json": None,
        }
    try:
        result = runner(args, cwd, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "executed": True,
            "ok": False,
            "returncode": None,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "timed_out": True,
            "json": None,
        }
    except OSError as exc:
        return {
            "command": command,
            "executed": True,
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "json": None,
        }
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    parsed = _parse_json(stdout)
    ok = int(result.get("returncode") or 0) == 0 and _parsed_ok(parsed)
    return {
        "command": command,
        "executed": True,
        "ok": ok,
        "returncode": result.get("returncode"),
        "stdout": stdout,
        "stderr": stderr,
        "json": parsed,
    }


def _parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _parsed_ok(parsed: Any) -> bool:
    if parsed is None:
        return True
    if not isinstance(parsed, dict):
        return True
    if parsed.get("ok") is False:
        return False
    if parsed.get("code") not in (None, 0):
        return False
    return True


def _command_error(command_result: dict[str, Any]) -> str:
    parsed = command_result.get("json")
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        if parsed.get("msg") and parsed.get("code") not in (None, 0):
            return str(parsed.get("msg"))
    stderr = " ".join(str(command_result.get("stderr") or "").split())
    if stderr:
        return stderr[:OUTPUT_LIMIT]
    stdout = " ".join(str(command_result.get("stdout") or "").split())
    return stdout[:OUTPUT_LIMIT]


def _compact_text(value: Any, *, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _public_safe_text(value: Any, *, limit: int = TEXT_LIMIT) -> str:
    text = _compact_text(value, limit=limit)
    patterns = (
        (re.compile(r"file://[^\s,;)]+", re.IGNORECASE), "[local-path-redacted]"),
        (
            re.compile(r"(?<![A-Za-z0-9_.-])(?:~|/(?:Users|private|var|tmp|Volumes))/[^\s,;)]+"),
            "[local-path-redacted]",
        ),
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
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return _compact_text(text, limit=limit)


def _public_safe_lark_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _public_safe_text(value) if isinstance(value, str) else value
        for key, value in values.items()
    }


def now_lark_datetime(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def append_history(existing: Any, entry: str, *, limit: int = TEXT_LIMIT) -> str:
    prior = str(existing or "").strip()
    combined = f"{prior}\n{entry}".strip() if prior else entry
    if len(combined) <= limit:
        return combined
    return combined[-limit:]


def normalize_select_value(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or "")
    return str(value or "")


def choose_heartbeat_task(
    records: list[dict[str, Any]],
    *,
    agent_id: str,
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
    if not stripped:
        return False
    if not prefixes:
        return False
    return any(stripped == prefix or stripped.startswith(prefix + " ") for prefix in prefixes)


def _record_json_args(values: dict[str, Any]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class LarkKanbanConfig:
    base_token: str
    table_id: str
    view_id: str | None = DEFAULT_STATUS_QUEUE_VIEW
    cli_bin: str = DEFAULT_CLI_BIN
    identity: str = "user"


def default_lark_kanban_config_path(registry_path: Path | None = None) -> Path:
    if registry_path is not None:
        expanded = registry_path.expanduser()
        if expanded.parent.name == ".loopx":
            return expanded.parent / "lark-kanban.json"
    return Path.cwd() / ".loopx" / "lark-kanban.json"


def read_lark_kanban_local_config(path: Path) -> dict[str, Any]:
    config_path = path.expanduser()
    if not config_path.exists():
        return {
            "ok": True,
            "exists": False,
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "path": str(config_path),
            "board": None,
        }
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "exists": True,
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "path": str(config_path),
            "error": f"invalid JSON: {exc}",
            "board": None,
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "exists": True,
            "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
            "path": str(config_path),
            "error": "config root must be a JSON object",
            "board": None,
        }
    payload.setdefault("schema_version", LARK_KANBAN_LOCAL_CONFIG_VERSION)
    payload["ok"] = True
    payload["exists"] = True
    payload["path"] = str(config_path)
    return payload


def write_lark_kanban_local_config(path: Path, payload: dict[str, Any]) -> None:
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
    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]
    base_token = ""
    for index, part in enumerate(parts):
        if part in {"base", "bitable"} and index + 1 < len(parts):
            base_token = parts[index + 1]
            break
    if not base_token and parts:
        base_token = parts[-1]
    table_id = _first_query_value(query, "table") or _first_query_value(query, "table_id")
    view_id = _first_query_value(query, "view") or _first_query_value(query, "view_id")
    return {
        "base_token": base_token,
        "table_id": table_id,
        "view_id": view_id,
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
    cli_bin: str = DEFAULT_CLI_BIN,
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
    board = dict(payload.get("board") or {})
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
                **(board.get("view_ids") if isinstance(board.get("view_ids"), dict) else {}),
                **(view_ids or {}),
            },
        }
    )
    payload = {
        "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
        "board": board,
        "todo_records": payload.get("todo_records") if isinstance(payload.get("todo_records"), dict) else {},
    }
    write_lark_kanban_local_config(config_path, payload)
    return {
        "ok": True,
        "schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION,
        "path": str(config_path),
        "board": board,
        "next_commands": _next_lark_kanban_commands(board),
    }


def lark_kanban_config_from_payload(payload: dict[str, Any]) -> LarkKanbanConfig | None:
    board = payload.get("board")
    if not isinstance(board, dict):
        return None
    base_token = str(board.get("base_token") or "").strip()
    table_id = str(board.get("table_id") or "").strip()
    if not base_token or not table_id:
        return None
    return LarkKanbanConfig(
        **{"base_" + "token": base_token},
        table_id=table_id,
        view_id=str(board.get("view_id") or DEFAULT_STATUS_QUEUE_VIEW),
        cli_bin=str(board.get("cli_bin") or DEFAULT_CLI_BIN),
        identity=str(board.get("identity") or "user"),
    )


def _next_lark_kanban_commands(board: dict[str, Any]) -> list[str]:
    return [
        "loopx lark-kanban doctor",
        "loopx lark-kanban sync-loopx-todos --goal-id <goal-id> --execute",
        (
            "loopx lark-kanban heartbeat --execute-lark --agent-id "
            f"{DEFAULT_AGENT_ID} --allow-command-prefix 'codex exec'"
        ),
    ]


def build_record_upsert_command(
    config: LarkKanbanConfig,
    *,
    record_id: str | None,
    values: dict[str, Any],
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
        config.table_id,
    ]
    if record_id:
        args.extend(["--record-id", record_id])
    args.extend(["--json", _record_json_args(values)])
    return args


def build_record_list_command(config: LarkKanbanConfig) -> list[str]:
    args = [
        config.cli_bin,
        "base",
        "+record-list",
        "--as",
        config.identity,
        "--base-token",
        config.base_token,
        "--table-id",
        config.table_id,
        "--format",
        "json",
        "--offset",
        "0",
        "--limit",
        "200",
    ]
    if config.view_id:
        args.extend(["--view-id", config.view_id])
    return args


def build_create_board_plan(
    *,
    base_name: str,
    table_name: str,
    cli_bin: str = DEFAULT_CLI_BIN,
    identity: str = "bot",
    base_token: str | None = None,
    user_open_id: str | None = None,
) -> list[list[str]]:
    commands: list[list[str]] = []
    if not base_token:
        commands.append(
            [
                cli_bin,
                "base",
                "+base-create",
                "--as",
                identity,
                "--name",
                base_name,
                "--table-name",
                table_name,
                "--fields",
                json.dumps(lark_kanban_field_definitions(), ensure_ascii=False),
            ]
        )
        commands.append(
            [
                cli_bin,
                "base",
                "+base-block-list",
                "--as",
                identity,
                "--base-token",
                "<base-token-from-create>",
                "--type",
                "table",
            ]
        )
    token = base_token or "<base-token-from-create>"
    table_ref = "<table-id-from-create>"
    if base_token:
        commands.append(
            [
                cli_bin,
                "base",
                "+table-create",
                "--as",
                identity,
                "--base-token",
                token,
                "--name",
                table_name,
                "--fields",
                json.dumps(lark_kanban_field_definitions(), ensure_ascii=False),
                "--view",
                json.dumps(lark_kanban_views(), ensure_ascii=False),
            ]
        )
    else:
        commands.extend(
            [
                [
                    cli_bin,
                    "base",
                    "+view-create",
                    "--as",
                    identity,
                    "--base-token",
                    token,
                    "--table-id",
                    table_ref,
                    "--json",
                    json.dumps(lark_kanban_views(), ensure_ascii=False),
                ],
                [
                    cli_bin,
                    "base",
                    "+view-list",
                    "--as",
                    identity,
                    "--base-token",
                    token,
                    "--table-id",
                    table_ref,
                ],
            ]
        )
    if user_open_id:
        commands.append(
            [
                cli_bin,
                "drive",
                "permission.members",
                "create",
                "--as",
                "bot",
                "--params",
                json.dumps(
                    {
                        "token": token,
                        "type": "bitable",
                        "need_notification": False,
                    },
                    ensure_ascii=False,
                ),
                "--data",
                json.dumps(
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
        )
    commands.extend(_view_configuration_commands(cli_bin=cli_bin, identity=identity, base_token=token, table_id=table_ref, view_ids={view["name"]: view["name"] for view in lark_kanban_views()}))
    return commands


def create_lark_kanban_board(
    *,
    base_name: str,
    table_name: str = DEFAULT_TABLE_NAME,
    cli_bin: str = DEFAULT_CLI_BIN,
    identity: str = "bot",
    base_token: str | None = None,
    user_open_id: str | None = None,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    effective_base_token = base_token
    table_id: str | None = None
    if not effective_base_token:
        create = _run_command(
            [cli_bin, "base", "+base-create", "--as", identity, "--name", base_name],
            execute=execute,
            runner=runner,
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
                        "executed": True,
                        "ok": False,
                        "returncode": None,
                        "stdout": "",
                        "stderr": "base-create did not return a usable Base token",
                        "json": None,
                    }
                )
                return _board_payload(False, commands, effective_base_token, table_id)
    token = effective_base_token or "<base-token-from-create>"
    table_create = _run_command(
        [
            cli_bin,
            "base",
            "+table-create",
            "--as",
            identity,
            "--base-token",
            token,
            "--name",
            table_name,
            "--fields",
            json.dumps(lark_kanban_field_definitions(), ensure_ascii=False),
            "--view",
            json.dumps(lark_kanban_views(), ensure_ascii=False),
        ],
        execute=execute,
        runner=runner,
    )
    commands.append(table_create)
    if table_create.get("executed"):
        if not table_create.get("ok"):
            return _board_payload(False, commands, effective_base_token, table_id)
        table_id = _extract_table_id(table_create.get("json")) or table_name
    table_ref = table_id or table_name
    if user_open_id:
        grant = _run_command(
            [
                cli_bin,
                "drive",
                "permission.members",
                "create",
                "--as",
                "bot",
                "--params",
                json.dumps(
                    {
                        "token": token,
                        "type": "bitable",
                        "need_notification": False,
                    },
                    ensure_ascii=False,
                ),
                "--data",
                json.dumps(
                    {
                        "member_id": user_open_id,
                        "member_type": "openid",
                        "perm": "full_access",
                        "perm_type": "container",
                        "type": "user",
                    },
                    ensure_ascii=False,
                ),
            ],
            execute=execute,
            runner=runner,
        )
        commands.append(grant)
        if grant.get("executed") and not grant.get("ok"):
            return _board_payload(False, commands, effective_base_token, table_id)
    for command in _view_configuration_commands(cli_bin=cli_bin, identity=identity, base_token=token, table_id=table_ref, view_ids={view["name"]: view["name"] for view in lark_kanban_views()}):
        result = _run_command(command, execute=execute, runner=runner)
        commands.append(result)
        if result.get("executed") and not result.get("ok"):
            return _board_payload(False, commands, effective_base_token, table_id)
    return _board_payload(True, commands, effective_base_token, table_id)


def _board_payload(
    ok: bool,
    commands: list[dict[str, Any]],
    base_token: str | None,
    table_id: str | None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "base_token": base_token,
        "table_id": table_id,
        "commands": commands,
        "error": None if ok else next((_command_error(item) for item in commands if not item.get("ok")), "unknown"),
    }


def _extract_base_token(parsed: Any) -> str | None:
    return _find_first_string(parsed, ("base_token", "app_token"))


def _extract_table_id(parsed: Any) -> str | None:
    return _find_first_string(parsed, ("table_id", "id"), required_prefix="tbl")


def _find_first_string(payload: Any, keys: tuple[str, ...], required_prefix: str | None = None) -> str | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
                if required_prefix is None or candidate.startswith(required_prefix):
                    return candidate
        for value in payload.values():
            found = _find_first_string(value, keys, required_prefix=required_prefix)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _find_first_string(value, keys, required_prefix=required_prefix)
            if found:
                return found
    return None


def _extract_created_record_id(parsed: Any) -> str | None:
    return _find_first_record_id(parsed)


def _find_first_record_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        ids = payload.get("record_id_list")
        if isinstance(ids, list) and ids:
            return str(ids[0])
        for key in ("record_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip().startswith("rec"):
                return value.strip()
        for value in payload.values():
            found = _find_first_record_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _find_first_record_id(value)
            if found:
                return found
    return None


def _extract_table_id_from_blocks(parsed: Any, *, table_name: str | None = None) -> str | None:
    blocks = _extract_list_from_payload(parsed, ("blocks", "items", "data"))
    table_blocks = [
        item
        for item in blocks
        if isinstance(item, dict)
        and str(item.get("type") or item.get("resource_type") or "").lower() in {"table", "bitable_table", ""}
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


def _extract_view_ids(parsed: Any) -> dict[str, str]:
    views = _extract_list_from_payload(parsed, ("views", "items", "data"))
    result: dict[str, str] = {}
    for item in views:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("view_name") or "").strip()
        view_id = _string_id_from_dict(item, ("view_id", "id"), prefix="vew") or _string_id_from_dict(
            item,
            ("view_id", "id"),
            prefix=None,
        )
        if name and view_id:
            result[name] = view_id
    return result


def _extract_list_from_payload(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _string_id_from_dict(item: dict[str, Any], keys: tuple[str, ...], *, prefix: str | None) -> str | None:
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
    cli_bin: str = DEFAULT_CLI_BIN,
    identity: str = "user",
) -> dict[str, Any]:
    parsed = parse_lark_base_url(base_url) if base_url else {}
    effective_base_token = str(base_token or parsed.get("base_token") or "").strip()
    effective_table_id = str(table_id or parsed.get("table_id") or "").strip()
    effective_view_id = str(view_id or parsed.get("view_id") or DEFAULT_STATUS_QUEUE_VIEW).strip()
    return save_lark_kanban_board_config(
        config_path,
        **{"base_" + "token": effective_base_token},
        table_id=effective_table_id,
        view_id=effective_view_id,
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
    cli_bin: str = DEFAULT_CLI_BIN,
    identity: str = "user",
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    existing = read_lark_kanban_local_config(config_path)
    existing_board = existing.get("board") if isinstance(existing.get("board"), dict) else {}
    parsed_url = parse_lark_base_url(base_url) if base_url else {}
    effective_base_token = str(
        base_token or parsed_url.get("base_token") or existing_board.get("base_token") or ""
    ).strip()
    effective_table_id = str(table_id or parsed_url.get("table_id") or existing_board.get("table_id") or "").strip()
    view_ids = existing_board.get("view_ids") if isinstance(existing_board.get("view_ids"), dict) else {}

    preflight = _lark_kanban_preflight(cli_bin=cli_bin, identity=identity, runner=runner)
    commands.extend(preflight["commands"])
    if execute and not preflight["identity_available"]:
        return {
            "ok": False,
            "schema_version": LARK_KANBAN_SCHEMA_VERSION,
            "execute": execute,
            "config_path": str(config_path),
            "base_token": effective_base_token or None,
            "table_id": effective_table_id or None,
            "commands": commands,
            "warnings": preflight["warnings"],
            "error": _identity_error(identity),
        }
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
        config_payload = save_lark_kanban_board_config(
            config_path,
            **{"base_" + "token": effective_base_token},
            table_id=effective_table_id,
            view_id=view_ids.get(DEFAULT_STATUS_QUEUE_VIEW) or DEFAULT_STATUS_QUEUE_VIEW,
            base_url=base_url,
            base_name=base_name,
            table_name=table_name,
            cli_bin=cli_bin,
            identity=identity,
            view_ids=view_ids,
        )

    def partial_enrichment_payload(command_result: dict[str, Any]) -> dict[str, Any]:
        error = _command_error(command_result)
        warnings.append(f"usable board config saved; view enrichment skipped: {error}")
        return _setup_payload(
            True,
            execute,
            config_path,
            commands,
            warnings,
            effective_base_token,
            effective_table_id,
            config=config_payload,
            partial=True,
            enrichment_error=error,
        )

    if not effective_base_token:
        created_base = True
        create = _run_command(
            [
                cli_bin,
                "base",
                "+base-create",
                "--as",
                identity,
                "--name",
                base_name,
                "--table-name",
                table_name,
                "--fields",
                json.dumps(lark_kanban_field_definitions(), ensure_ascii=False),
            ],
            execute=execute,
            runner=runner,
        )
        commands.append(create)
        if execute:
            if not create.get("ok"):
                return _setup_payload(False, execute, config_path, commands, warnings, effective_base_token, effective_table_id)
            effective_base_token = _extract_base_token(create.get("json")) or ""
            effective_table_id = _extract_table_id(create.get("json")) or effective_table_id
            if not effective_base_token:
                return _setup_payload(
                    False,
                    execute,
                    config_path,
                    commands,
                    warnings,
                    effective_base_token,
                    effective_table_id,
                    error="base-create did not return a usable Base token",
                )
        else:
            effective_base_token = "<base-token-from-create>"
    if not effective_table_id:
        if created_base:
            blocks = _run_command(
                [
                    cli_bin,
                    "base",
                    "+base-block-list",
                    "--as",
                    identity,
                    "--base-token",
                    effective_base_token,
                    "--type",
                    "table",
                ],
                execute=execute,
                runner=runner,
            )
            commands.append(blocks)
            if execute:
                if not blocks.get("ok"):
                    return _setup_payload(False, execute, config_path, commands, warnings, effective_base_token, effective_table_id)
                effective_table_id = _extract_table_id_from_blocks(blocks.get("json"), table_name=table_name) or ""
                if not effective_table_id:
                    return _setup_payload(
                        False,
                        execute,
                        config_path,
                        commands,
                        warnings,
                        effective_base_token,
                        effective_table_id,
                        error="base-block-list did not return a usable table id",
                    )
            else:
                effective_table_id = "<table-id-from-base-block-list>"
        else:
            created_table = True
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
                    table_name,
                    "--fields",
                    json.dumps(lark_kanban_field_definitions(), ensure_ascii=False),
                    "--view",
                    json.dumps(lark_kanban_views(), ensure_ascii=False),
                ],
                execute=execute,
                runner=runner,
            )
            commands.append(table_create)
            if execute:
                if not table_create.get("ok"):
                    return _setup_payload(False, execute, config_path, commands, warnings, effective_base_token, effective_table_id)
                effective_table_id = _extract_table_id(table_create.get("json")) or ""
                if not effective_table_id:
                    return _setup_payload(
                        False,
                        execute,
                        config_path,
                        commands,
                        warnings,
                        effective_base_token,
                        effective_table_id,
                        error="table-create did not return a usable table id",
                    )
            else:
                effective_table_id = "<table-id-from-table-create>"

    save_usable_config()

    if execute and not created_base and not created_table:
        field_list = _run_command(
            [cli_bin, "base", "+field-list", "--as", identity, "--base-token", effective_base_token, "--table-id", effective_table_id, "--format", "json", "--limit", "200"],
            execute=True,
            runner=runner,
        )
        commands.append(field_list)
        if not field_list.get("ok"):
            return partial_enrichment_payload(field_list)
        for definition in issue_fix_surface.missing_field_definitions(field_list.get("json"), lark_kanban_field_definitions()):
            field_create = _run_command(
                [cli_bin, "base", "+field-create", "--as", identity, "--base-token", effective_base_token, "--table-id", effective_table_id, "--json", json.dumps(definition, ensure_ascii=False)],
                execute=True,
                runner=runner,
            )
            commands.append(field_create)
            if not field_create.get("ok"):
                return partial_enrichment_payload(field_create)
            created_field_names.append(str(definition.get("name") or ""))
        for migration in issue_fix_surface.field_definition_migrations(
            field_list.get("json"), lark_kanban_field_definitions()
        ):
            field_update = _run_command(
                [
                    cli_bin,
                    "base",
                    "+field-update",
                    "--as",
                    identity,
                    "--base-token",
                    effective_base_token,
                    "--table-id",
                    effective_table_id,
                    "--field-id",
                    str(migration["field_id"]),
                    "--json",
                    json.dumps(migration["definition"], ensure_ascii=False),
                    "--yes",
                ],
                execute=True,
                runner=runner,
            )
            commands.append(field_update)
            if not field_update.get("ok"):
                return partial_enrichment_payload(field_update)
            updated_field_names.append(str(migration["name"]))

    if execute:
        view_ids = _refresh_view_ids(
            cli_bin=cli_bin,
            identity=identity,
            **{"base_" + "token": effective_base_token},
            table_id=effective_table_id,
            commands=commands,
            runner=runner,
        )
        save_usable_config()
    else:
        view_ids = {str(name): str(value) for name, value in view_ids.items()}

    missing_view_names = [view["name"] for view in lark_kanban_views() if view["name"] not in view_ids]
    should_create_views = bool(missing_view_names) and (created_base or not created_table)
    if should_create_views:
        view_create = _run_command(
            [
                cli_bin,
                "base",
                "+view-create",
                "--as",
                identity,
                "--base-token",
                effective_base_token,
                "--table-id",
                effective_table_id,
                "--json",
                json.dumps(
                    [view for view in lark_kanban_views() if view["name"] in missing_view_names],
                    ensure_ascii=False,
                ),
            ],
            execute=execute,
            runner=runner,
        )
        commands.append(view_create)
        if execute and not view_create.get("ok"):
            if config_payload:
                return partial_enrichment_payload(view_create)
            return _setup_payload(False, execute, config_path, commands, warnings, effective_base_token, effective_table_id)
        if execute:
            view_ids = _refresh_view_ids(
                cli_bin=cli_bin,
                identity=identity,
                **{"base_" + "token": effective_base_token},
                table_id=effective_table_id,
                commands=commands,
                runner=runner,
            )
            save_usable_config()

    if not view_ids:
        view_ids = {view["name"]: view["name"] for view in lark_kanban_views()}
    for command in _view_configuration_commands(
        cli_bin=cli_bin,
        identity=identity,
        **{"base_" + "token": effective_base_token},
        table_id=effective_table_id,
        view_ids=view_ids,
    ):
        result = _run_command(command, execute=execute, runner=runner)
        commands.append(result)
        if execute and not result.get("ok"):
            if config_payload:
                return partial_enrichment_payload(result)
            return _setup_payload(False, execute, config_path, commands, warnings, effective_base_token, effective_table_id)

    save_usable_config()
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
        "next_commands": _next_lark_kanban_commands(config_payload.get("board", {}) if config_payload else {}),
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
    payload = {
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
        payload["next_commands"] = _next_lark_kanban_commands(config.get("board", {}))
    if partial:
        payload["partial"] = True
        payload["enrichment_ok"] = False
        payload["enrichment_error"] = enrichment_error or next(
            (_command_error(item) for item in commands if not item.get("ok")),
            "unknown",
        )
    elif not ok:
        payload["error"] = error or next((_command_error(item) for item in commands if not item.get("ok")), "unknown")
    return payload


def _refresh_view_ids(
    *,
    cli_bin: str,
    identity: str,
    base_token: str,
    table_id: str,
    commands: list[dict[str, Any]],
    runner: CommandRunner,
) -> dict[str, str]:
    view_list = _run_command(
        [
            cli_bin,
            "base",
            "+view-list",
            "--as",
            identity,
            "--base-token",
            base_token,
            "--table-id",
            table_id,
        ],
        execute=True,
        runner=runner,
    )
    commands.append(view_list)
    if not view_list.get("ok"):
        return {}
    return _extract_view_ids(view_list.get("json"))


def _view_configuration_commands(
    *,
    cli_bin: str,
    identity: str,
    base_token: str,
    table_id: str,
    view_ids: dict[str, str],
) -> list[list[str]]:
    return issue_fix_surface.view_configuration_commands(cli_bin=cli_bin, identity=identity, base_token=base_token, table_id=table_id, view_ids=view_ids, worker_view_name=DEFAULT_STATUS_QUEUE_VIEW, todo_statuses=[STATUS_TODO, STATUS_CLAIMED], user_gate_status=STATUS_USER_GATE, operator_card_fields=OPERATOR_CARD_FIELDS)


def _lark_kanban_schema_check(parsed: Any) -> dict[str, Any]:
    definitions = lark_kanban_field_definitions()
    missing = issue_fix_surface.missing_field_definitions(parsed, definitions)
    migrations = issue_fix_surface.field_definition_migrations(parsed, definitions)
    missing_names = [str(item.get("name") or "") for item in missing]
    incompatible_names = [str(item.get("name") or "") for item in migrations]
    migration_required = bool(missing_names or incompatible_names)
    return {
        "ok": not migration_required,
        "status": "stale" if migration_required else "ready",
        "expected_field_count": len(definitions),
        "observed_field_count": len(issue_fix_surface.extract_field_names(parsed)),
        "missing_fields": missing_names,
        "incompatible_fields": incompatible_names,
        "migration_required": migration_required,
        "remediation_command": (
            "loopx lark-kanban setup --execute" if migration_required else None
        ),
    }


def _inspect_lark_kanban_schema(
    config: LarkKanbanConfig,
    *,
    commands: list[dict[str, Any]],
    runner: CommandRunner,
) -> dict[str, Any]:
    field_list = _run_command(
        [
            config.cli_bin,
            "base",
            "+field-list",
            "--as",
            config.identity,
            "--base-token",
            config.base_token,
            "--table-id",
            config.table_id,
            "--format",
            "json",
            "--limit",
            "200",
        ],
        execute=True,
        runner=runner,
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
            "error": _command_error(field_list),
        }
    return _lark_kanban_schema_check(field_list.get("json"))


def _schema_check_error(schema_check: dict[str, Any]) -> str:
    if schema_check.get("status") == "stale":
        missing = ", ".join(schema_check.get("missing_fields") or []) or "none"
        incompatible = (
            ", ".join(schema_check.get("incompatible_fields") or []) or "none"
        )
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
    cli_bin: str = DEFAULT_CLI_BIN,
    identity: str = "user",
    check_board: bool = True,
    require_board: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
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

    config_payload = read_lark_kanban_local_config(config_path)
    board_config = lark_kanban_config_from_payload(config_payload)
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
            [
                board_config.cli_bin,
                "base",
                "+base-get",
                "--as",
                board_config.identity,
                "--base-token",
                board_config.base_token,
            ],
            [
                board_config.cli_bin,
                "base",
                "+view-list",
                "--as",
                board_config.identity,
                "--base-token",
                board_config.base_token,
                "--table-id",
                board_config.table_id,
            ],
        ):
            result = _run_command(command, execute=True, runner=runner)
            commands.append(result)
            if not result.get("ok"):
                issues.append({"severity": "error", "message": _command_error(result)})
        if not any(issue["severity"] == "error" for issue in issues):
            schema_check = _inspect_lark_kanban_schema(
                board_config,
                commands=commands,
                runner=runner,
            )
            if not schema_check.get("ok"):
                issues.append(
                    {
                        "severity": "error",
                        "message": _schema_check_error(schema_check),
                    }
                )

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
    runner: CommandRunner,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    version = _run_command([cli_bin, "--version"], execute=True, runner=runner)
    commands.append(version)
    cli_ok = bool(version.get("ok"))
    version_tuple = _version_tuple(str(version.get("stdout") or ""))
    if cli_ok and version_tuple and version_tuple < (1, 0, 56):
        warnings.append("lark-cli should be upgraded to at least 1.0.56 for setup visible-field support")
    auth = _run_command([cli_bin, "auth", "status"], execute=True, runner=runner)
    commands.append(auth)
    identity_available = _identity_available(auth.get("json"), identity)
    for help_command in (
        [cli_bin, "base", "+base-create", "--help"],
        [cli_bin, "base", "+base-block-list", "--help"],
        [cli_bin, "base", "+view-list", "--help"],
        [cli_bin, "base", "+view-set-group", "--help"],
        [cli_bin, "base", "+view-set-visible-fields", "--help"],
    ):
        result = _run_command(help_command, execute=True, runner=runner)
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
    if not match:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _identity_available(parsed: Any, identity: str) -> bool:
    if not isinstance(parsed, dict):
        return False
    identities = parsed.get("identities") if isinstance(parsed.get("identities"), dict) else {}
    if identity == "auto":
        active = str(parsed.get("identity") or "").strip()
        if active and isinstance(identities.get(active), dict):
            return bool(identities[active].get("available"))
        return any(isinstance(item, dict) and bool(item.get("available")) for item in identities.values())
    status = identities.get(identity)
    return isinstance(status, dict) and bool(status.get("available"))


def _identity_error(identity: str) -> str:
    if identity == "user":
        return "lark-cli user identity is unavailable; run `lark-cli auth login --domain base --recommend`"
    return f"lark-cli identity {identity!r} is unavailable; run `lark-cli auth status`"


def _lark_record_from_projection_block(
    block: dict[str, Any],
    *,
    goal_id: str,
    source_id: str,
    public_safe: bool = False,
) -> dict[str, Any]:
    priority = str(block.get("priority") or "P2")
    values = _lark_record_from_todo_block(
        block,
        goal_id=goal_id,
        state_file=Path(f"{source_id}.projection"),
        priority=priority,
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
        superseded_by = ", ".join(_as_text_list(_projection_lifecycle_value(block, "superseded_by"))) or "current projection row"
        default_handoff = f"Row lifecycle {lifecycle_state}; continue with {superseded_by}."
    values.update(
        {
            "LoopX Goal ID": goal_id,
            "LoopX Todo ID": str(block.get("todo_id") or ""),
            "Scope": _compact_text(block.get("scope") or block.get("source_section") or f"projection_source={source_id}"),
            "Handoff": _compact_text(block.get("handoff") or default_handoff),
            "Evidence": _compact_text("; ".join(evidence_parts)),
            "Run History": _compact_text(
                "; ".join(
                    part
                    for part in [
                        f"synced from LoopX projection source={source_id} status={block.get('status') or 'open'}",
                        f"row_lifecycle={lifecycle_state}" if lifecycle_state else "",
                    ]
                    if part
                )
            ),
        }
    )
    values.update(issue_fix_surface.issue_fix_record_values(block, compact_text=_compact_text))
    return _public_safe_lark_values(values) if public_safe else values


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
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    if not isinstance(projection, dict):
        raise ValueError("projection must be a JSON object")
    if sink_visibility not in SINK_VISIBILITIES:
        raise ValueError(f"sink_visibility must be one of {sorted(SINK_VISIBILITIES)}")
    public_safe = sink_visibility == SINK_VISIBILITY_SHARED
    resolved_source_id, namespace_warnings = _projection_namespace(projection, source_id)
    resolved_goal_id, rows, warnings = _projection_rows_from_payload(
        projection,
        goal_id=goal_id,
        agent_id=agent_id,
        source_id=resolved_source_id,
        include_done=include_done,
        limit=limit,
    )
    warnings = [*namespace_warnings, *warnings]

    complete_rows, complete_warnings = rows, []
    if reconcile_source:
        _, complete_rows, complete_warnings = _projection_rows_from_payload(
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

    commands: list[dict[str, Any]] = []
    schema_check: dict[str, Any] | None = None
    if execute:
        schema_check = _inspect_lark_kanban_schema(
            config,
            commands=commands,
            runner=runner,
        )
        if not schema_check.get("ok"):
            return {
                "ok": False,
                "schema_version": LARK_KANBAN_SYNC_PROJECTION_VERSION,
                "execute": execute,
                "goal_id": resolved_goal_id,
                "agent_id": agent_id,
                "source_id": resolved_source_id,
                "sink_visibility": sink_visibility,
                "public_safe_redaction": public_safe,
                "projection_schema_version": projection.get("schema_version"),
                "row_count": len(rows),
                "records": [],
                "commands": commands,
                "warnings": warnings,
                "config_path": str(config_path) if config_path else None,
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
        raise ValueError(
            "source reconcile requires a complete remote record list"
        )

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

    results: list[dict[str, Any]] = []
    ok = True
    for row in rows:
        row_goal_id = str(row.get("goal_id") or resolved_goal_id)
        todo_id = str(row.get("todo_id") or "").strip()
        key = f"{row_goal_id}:{todo_id}"
        values = _lark_record_from_projection_block(
            row,
            goal_id=row_goal_id,
            source_id=resolved_source_id,
            public_safe=public_safe,
        )
        result = _run_command(
            build_record_upsert_command(config, record_id=record_map.get(key), values=values),
            execute=execute,
            runner=runner,
        )
        commands.append(result)
        record_id = _extract_created_record_id(result.get("json")) or record_map.get(key)
        if execute and result.get("ok") and record_id:
            record_map[key] = record_id
        results.append(
            {
                "todo_id": todo_id,
                "record_id": record_id,
                "command": result,
                "values": values,
            }
        )
        ok = ok and bool(result.get("ok"))
        if execute and not result.get("ok"):
            break

    executed_remote_delete_count = 0
    if ok and reconcile_source and reconcile_plan["remote_delete_record_ids"]:
        delete_result = _run_command(
            build_record_delete_command(
                config,
                record_ids=reconcile_plan["remote_delete_record_ids"],
            ),
            execute=execute,
            runner=runner,
        )
        commands.append(delete_result)
        ok = ok and bool(delete_result.get("ok"))
        if execute and delete_result.get("ok"):
            executed_remote_delete_count = reconcile_plan["remote_delete_count"]

    removed_local_mapping_count = 0
    if execute and ok and reconcile_source:
        for key in reconcile_plan["local_mapping_keys_to_remove"]:
            if key in record_map:
                removed_local_mapping_count += 1
                record_map.pop(key, None)

    if execute and ok:
        _persist_lark_todo_record_map(config, config_path=config_path, local=local, record_map=record_map)

    return {
        "ok": ok,
        "schema_version": LARK_KANBAN_SYNC_PROJECTION_VERSION,
        "execute": execute,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "source_id": resolved_source_id,
        "sink_visibility": sink_visibility,
        "public_safe_redaction": public_safe,
        "projection_schema_version": projection.get("schema_version"),
        "schema_check": schema_check,
        "row_count": len(rows),
        "source_reconcile": {
            **reconcile_plan,
            "requested": reconcile_source,
            "source_snapshot_complete": source_snapshot_complete,
            "mode": "execute" if reconcile_source and execute else (
                "preview" if reconcile_source else "disabled"
            ),
            "executed_remote_delete_count": executed_remote_delete_count,
            "removed_local_mapping_count": removed_local_mapping_count,
        },
        "records": results,
        "commands": commands,
        "warnings": warnings,
        "config_path": str(config_path) if config_path else None,
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
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    from ....capabilities.issue_fix.outcome_projection import (
        build_issue_fix_outcome_collection_from_domain_state,
    )
    from ....control_plane.todos.todo_semantics import todo_priority_label
    from ....todos import resolve_todo_state_path, section_bounds, todo_blocks

    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    lines = resolved_state_file.read_text(encoding="utf-8").splitlines()
    todos: list[dict[str, Any]] = []
    for role in ("user", "agent"):
        bounds = section_bounds(lines, role)
        if not bounds:
            continue
        start, end, heading = bounds
        for block in todo_blocks(lines, start, end, role=role, source_section=heading):
            raw_status = str(block.get("status") or "").strip()
            status = normalize_todo_status(raw_status)
            if (block.get("done") or status == TODO_STATUS_DONE) and not include_done:
                continue
            candidate = {**block, "role": role, "source_section": heading}
            if not todo_matches_agent_scope(candidate, agent_id):
                continue
            todos.append(candidate)
    todos = todos[:limit]

    outcome_projection = build_issue_fix_outcome_collection_from_domain_state(
        goal_id=goal_id,
        project=resolved_project or project or Path("."),
    )
    outcome_source_id, outcome_namespace_warnings = _projection_namespace(
        outcome_projection,
        None,
    )
    outcome_source_count = len(outcome_projection.get("issue_fix_outcomes") or [])
    _, outcome_rows, outcome_row_warnings = _projection_rows_from_payload(
        outcome_projection,
        goal_id=goal_id,
        agent_id=agent_id,
        source_id=outcome_source_id,
        include_done=True,
        limit=outcome_source_count,
    )
    outcome_warnings = [
        *outcome_namespace_warnings,
        *list(outcome_projection.get("warnings") or []),
        *outcome_row_warnings,
    ]

    commands: list[dict[str, Any]] = []
    schema_check: dict[str, Any] | None = None
    if execute:
        schema_check = _inspect_lark_kanban_schema(
            config,
            commands=commands,
            runner=runner,
        )
        if not schema_check.get("ok"):
            return {
                "ok": False,
                "schema_version": "loopx_lark_kanban_sync_todos_v0",
                "execute": execute,
                "goal_id": goal_id,
                "agent_id": agent_id,
                "base_token": config.base_token,
                "table_id": config.table_id,
                "view_id": config.view_id,
                "project": str(resolved_project) if resolved_project else None,
                "state_file": str(resolved_state_file),
                "todo_count": len(todos),
                "issue_fix_outcome_count": len(outcome_rows),
                "limit_policy": {
                    "todo_rows": limit,
                    "issue_fix_outcomes": "complete_source_projection",
                },
                "issue_fix_source_counts": outcome_projection.get("source_counts"),
                "warnings": outcome_warnings,
                "records": [],
                "commands": commands,
                "config_path": str(config_path) if config_path else None,
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

    results: list[dict[str, Any]] = []
    successful_write_count = 0
    ok = True
    for block in todos:
        todo_id = str(block.get("todo_id") or "").strip()
        key = f"{goal_id}:{todo_id}"
        values = _lark_record_from_todo_block(
            block,
            goal_id=goal_id,
            state_file=resolved_state_file,
            priority=todo_priority_label(block, text_mode="prefix") or "P2",
        )
        result = _run_command(
            build_record_upsert_command(config, record_id=record_map.get(key), values=values),
            execute=execute,
            runner=runner,
        )
        commands.append(result)
        record_id = _extract_created_record_id(result.get("json")) or record_map.get(key)
        write_succeeded = bool(
            execute and result.get("executed") and result.get("ok")
        )
        if write_succeeded:
            successful_write_count += 1
        if execute and result.get("ok") and record_id:
            record_map[key] = record_id
        results.append(
            {
                "todo_id": todo_id,
                "record_id": record_id,
                "command": result,
                "values": values,
            }
        )
        ok = ok and bool(result.get("ok"))
        if execute and not result.get("ok"):
            break

    if ok:
        for row in outcome_rows:
            row_goal_id = str(row.get("goal_id") or goal_id)
            todo_id = str(row.get("todo_id") or "").strip()
            key = f"{row_goal_id}:{todo_id}"
            values = _lark_record_from_projection_block(
                row,
                goal_id=row_goal_id,
                source_id=outcome_source_id,
            )
            result = _run_command(
                build_record_upsert_command(
                    config,
                    record_id=record_map.get(key),
                    values=values,
                ),
                execute=execute,
                runner=runner,
            )
            commands.append(result)
            record_id = (
                _extract_created_record_id(result.get("json")) or record_map.get(key)
            )
            if execute and result.get("ok") and record_id:
                record_map[key] = record_id
            write_succeeded = bool(
                execute and result.get("executed") and result.get("ok")
            )
            if write_succeeded:
                successful_write_count += 1
            results.append(
                {
                    "todo_id": todo_id,
                    "record_id": record_id,
                    "command": result,
                    "values": values,
                }
            )
            ok = ok and bool(result.get("ok"))
            if execute and not result.get("ok"):
                break

    if execute and successful_write_count:
        _persist_lark_todo_record_map(config, config_path=config_path, local=local, record_map=record_map)

    return {
        "ok": ok,
        "schema_version": "loopx_lark_kanban_sync_todos_v0",
        "execute": execute,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "base_token": config.base_token,
        "table_id": config.table_id,
        "view_id": config.view_id,
        "project": str(resolved_project) if resolved_project else None,
        "state_file": str(resolved_state_file),
        "todo_count": len(todos),
        "issue_fix_outcome_count": len(outcome_rows),
        "limit_policy": {
            "todo_rows": limit, "issue_fix_outcomes": "complete_source_projection",
        },
        "issue_fix_source_counts": outcome_projection.get("source_counts"),
        "schema_check": schema_check,
        "warnings": outcome_warnings,
        "records": results,
        "commands": commands,
        "config_path": str(config_path) if config_path else None,
        "successful_write_count": successful_write_count,
        "external_write_performed": bool(successful_write_count),
    }


def _load_lark_todo_record_map(
    config: LarkKanbanConfig, *, config_path: Path | None, execute: bool,
    inspect_remote: bool,
    commands: list[dict[str, Any]], runner: CommandRunner,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, str]], bool | None]:
    local = read_lark_kanban_local_config(config_path) if config_path else {}
    record_map = _todo_record_map_from_local_config(local)
    if not execute and not inspect_remote:
        return local, record_map, [], None

    list_config = LarkKanbanConfig(
        **{"base_" + "token": config.base_token},
        table_id=config.table_id,
        view_id=None,
        cli_bin=config.cli_bin,
        identity=config.identity,
    )
    list_result = _run_command(build_record_list_command(list_config), execute=True, runner=runner)
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
            preferred_record_id = record_map.get(key)
            record_map[key] = (
                preferred_record_id
                if preferred_record_id in record_ids
                else sorted(record_ids)[0]
            )
        remote_complete = record_list_is_complete(parsed_records)
    return local, record_map, remote_records, remote_complete


def _todo_record_map_from_local_config(local: dict[str, Any]) -> dict[str, str]:
    records = local.get("todo_records") if isinstance(local, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(key): str(value) for key, value in records.items() if str(key).strip() and str(value).strip()}


def _persist_lark_todo_record_map(
    config: LarkKanbanConfig, *, config_path: Path | None,
    local: dict[str, Any], record_map: dict[str, str],
) -> None:
    if not config_path:
        return
    board = local.get("board") if isinstance(local.get("board"), dict) else {}
    if not board:
        board = {
            "base_token": config.base_token,
            "table_id": config.table_id,
            "view_id": config.view_id,
            "cli_bin": config.cli_bin,
            "identity": config.identity,
        }
    payload = {"schema_version": LARK_KANBAN_LOCAL_CONFIG_VERSION, "board": board, "todo_records": record_map}
    write_lark_kanban_local_config(config_path, payload)


def _lark_record_from_todo_block(
    block: dict[str, Any],
    *,
    goal_id: str,
    state_file: Path,
    priority: str,
) -> dict[str, Any]:
    role = str(block.get("role") or "agent")
    raw_status = str(block.get("status") or "").strip()
    status = normalize_todo_status(raw_status) or TODO_STATUS_OPEN
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
    claim = CLAIM_UNCLAIMED
    if lark_status == STATUS_USER_GATE:
        claim = CLAIM_HUMAN
    elif claimed_by:
        claim = CLAIM_AGENT
    scope = block.get("required_write_scopes")
    if isinstance(scope, list):
        scope_text = ", ".join(str(item) for item in scope)
    else:
        scope_text = str(scope or "")
    evidence = str(block.get("evidence") or block.get("reason") or block.get("note") or "")
    text = str(block.get("text") or "")
    return {
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
        "Evidence": evidence,
        "Run History": f"synced from LoopX todo status={status}",
        "Worker Command": "",
        "Workdir": "",
        "Last Error": "",
        "Last Result Code": None,
    }


def seed_lark_kanban_task(
    config: LarkKanbanConfig,
    *,
    task: dict[str, Any],
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    result = _run_command(
        build_record_upsert_command(config, record_id=None, values=task),
        execute=execute,
        runner=runner,
    )
    record_id = _extract_created_record_id(result.get("json"))
    return {
        "ok": bool(result.get("ok")),
        "schema_version": LARK_KANBAN_SCHEMA_VERSION,
        "record_id": record_id,
        "command": result,
        "task": task,
    }


def seed_lark_kanban_records(
    config: LarkKanbanConfig,
    *,
    records: list[dict[str, Any]],
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    ok = True
    for record in records:
        result = seed_lark_kanban_task(
            config,
            task=record,
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
        "created_record_ids": [item.get("record_id") for item in results],
        "records": results,
    }

def lark_kanban_heartbeat(
    config: LarkKanbanConfig,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    fixture: dict[str, Any] | None = None,
    worker_command: str | None = None,
    execute_lark: bool = False,
    execute_worker: bool = False,
    complete_on_success: bool = False,
    allowed_command_prefixes: list[str] | None = None,
    runner: CommandRunner = default_subprocess_runner,
    now: datetime | None = None,
    worker_timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    if fixture is None:
        list_result = _run_command(
            build_record_list_command(config),
            execute=execute_lark,
            runner=runner,
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
        parsed = list_result.get("json") if execute_lark else {"data": {"fields": [], "data": []}}
    else:
        parsed = fixture
    records = lark_record_rows(parsed if isinstance(parsed, dict) else {})
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
    claim_values = {
        "Status": STATUS_CLAIMED,
        "Claim": CLAIM_AGENT,
        "Claimed By": agent_id,
        "Last Heartbeat": timestamp,
        "Run History": append_history(task.get("Run History"), f"{timestamp} {agent_id}: claimed"),
    }
    claim_result = _run_command(
        build_record_upsert_command(config, record_id=record_id, values=claim_values),
        execute=execute_lark,
        runner=runner,
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
    if not command:
        worker = {
            "attempted": False,
            "executed": False,
            "ok": False,
            "reason": "worker_command_missing",
            "command": "",
        }
        final_status = STATUS_USER_GATE
        evidence = "worker command missing; user must provide Worker Command or heartbeat --worker-command"
        result_code = None
    elif not execute_worker:
        worker = {
            "attempted": True,
            "executed": False,
            "ok": True,
            "reason": "execute_worker_not_set",
            "command": command,
        }
        final_status = STATUS_CLAIMED
        evidence = f"dry-run worker command: {command}"
        result_code = None
    elif not allowed_worker_command(command, allowed_command_prefixes or []):
        worker = {
            "attempted": True,
            "executed": False,
            "ok": False,
            "reason": "worker_command_prefix_not_allowed",
            "command": command,
            "allowed_prefixes": allowed_command_prefixes or [],
        }
        final_status = STATUS_BLOCKED
        evidence = "worker command blocked by prefix allowlist"
        result_code = None
    else:
        workdir = Path(str(task.get("Workdir") or ".")).expanduser()
        worker_args = shlex.split(command)
        worker_result = _run_command(
            worker_args,
            execute=True,
            runner=runner,
            cwd=workdir,
            timeout_seconds=worker_timeout_seconds,
        )
        worker = {
            "attempted": True,
            "executed": True,
            "ok": bool(worker_result.get("ok")),
            "reason": "completed" if worker_result.get("ok") else "failed",
            "command": command,
            "returncode": worker_result.get("returncode"),
            "stdout": _compact_text(worker_result.get("stdout"), limit=OUTPUT_LIMIT),
            "stderr": _compact_text(worker_result.get("stderr"), limit=OUTPUT_LIMIT),
        }
        final_status = STATUS_DONE if worker_result.get("ok") and complete_on_success else STATUS_REVIEW
        if not worker_result.get("ok"):
            final_status = STATUS_BLOCKED
        evidence = _compact_text(
            worker.get("stdout") or worker.get("stderr") or worker.get("reason"),
            limit=OUTPUT_LIMIT,
        )
        result_code = worker_result.get("returncode")
    finish_values = {
        "Status": final_status,
        "Claim": CLAIM_AGENT,
        "Claimed By": agent_id,
        "Last Heartbeat": timestamp,
        "Evidence": evidence,
        "Last Result Code": result_code,
        "Last Error": "" if worker.get("ok") else _compact_text(worker.get("stderr") or worker.get("reason")),
        "Run History": append_history(
            claim_values["Run History"],
            f"{timestamp} {agent_id}: worker {worker.get('reason')} -> {final_status}",
        ),
        "Handoff": _handoff_for_status(final_status, agent_id=agent_id),
    }
    finish_result = _run_command(
        build_record_upsert_command(config, record_id=record_id, values=finish_values),
        execute=execute_lark,
        runner=runner,
    )
    commands.append(finish_result)
    ok = bool(worker.get("ok")) and (not execute_lark or finish_result.get("ok"))
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
    if status == STATUS_DONE:
        return f"{agent_id} completed the task; review evidence and close any external issue/PR bookkeeping."
    if status == STATUS_REVIEW:
        return f"{agent_id} produced evidence; human/controller review should decide merge or next todo."
    if status == STATUS_USER_GATE:
        return "User input is required before this task can proceed."
    if status == STATUS_BLOCKED:
        return f"{agent_id} hit a blocker; inspect Last Error and decide repair/retry/reassign."
    return f"{agent_id} claimed the task; heartbeat can continue."


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
