#!/usr/bin/env python3
"""Refresh the bundled dashboard example from live LoopX agent rows.

The dashboard's default example should show real LoopX agent lane shapes without
committing the entire local control-plane status, absolute paths, credentials,
or private/local planning text. This exporter copies only the small public-safe
slice needed by the Agent Management panel.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.runtime.public_safety import public_safe_compact_text  # noqa: E402


DEFAULT_GOAL_ID = "loopx-meta"
DEFAULT_AGENT_IDS = (
    "codex-main-control",
    "codex-side-bypass",
    "codex-product-capability",
    "codex-value-explorer",
)
PUBLIC_REGISTRY = "$HOME/.loopx/registry.global.json"
PUBLIC_RUNTIME_ROOT = "$HOME/.loopx"
REDACTED_TEXT = "Public-safe redacted live LoopX text; inspect local status for the full row."
HOME_TEXT = str(Path.home())
HOME_NAME = Path.home().name
PRIVATE_TEXT_TERMS = [
    r"department-",
    r"department_",
    r"\.local\b",
    r"/Users/",
    r"/private/",
    r"/tmp/",
    r"AK[=_:-][A-Za-z0-9_-]{8,}",
    r"SK[=_:-][A-Za-z0-9_-]{8,}",
]
if HOME_NAME:
    PRIVATE_TEXT_TERMS.append(rf"\b{re.escape(HOME_NAME)}\b")
PRIVATE_TEXT_PATTERN = re.compile(r"(?i)(?:" + "|".join(PRIVATE_TEXT_TERMS) + r")")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal-id", default=DEFAULT_GOAL_ID)
    parser.add_argument(
        "--agent-id",
        action="append",
        default=[],
        help="Agent id to keep. Defaults to the current LoopX meta registered agents.",
    )
    parser.add_argument(
        "--status-json",
        help="Read an existing loopx status JSON file instead of invoking the CLI.",
    )
    parser.add_argument(
        "--example",
        default=str(REPO_ROOT / "examples" / "status.example.json"),
        help="Dashboard example JSON to update.",
    )
    parser.add_argument(
        "--registry",
        default=str(Path.home() / ".codex" / "loopx" / "registry.global.json"),
    )
    parser.add_argument("--runtime-root", default=str(Path.home() / ".codex" / "loopx"))
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def compact_text(value: Any, *, limit: int = 220) -> str | None:
    if isinstance(value, str):
        text = " ".join(value.strip().split())
        if len(text) > limit:
            text = f"{text[: max(0, limit - 1)]}…"
    else:
        text = public_safe_compact_text(value, limit=limit)
    if not text:
        return None
    if HOME_TEXT:
        text = text.replace(HOME_TEXT, "$HOME")
    if PRIVATE_TEXT_PATTERN.search(text):
        return REDACTED_TEXT
    return text


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [sanitize_value(child) for child in value]
    if isinstance(value, str):
        return compact_text(value, limit=260) or ""
    return value


def keep_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[field] for field in fields if row.get(field) not in (None, "", [], {})}


def sanitize_string_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    next_row = dict(row)
    for field in fields:
        if field in next_row:
            text = compact_text(next_row.get(field), limit=220)
            if text:
                next_row[field] = text
            else:
                next_row.pop(field, None)
    return next_row


def sanitize_todo(row: dict[str, Any]) -> dict[str, Any]:
    todo = keep_fields(
        row,
        (
            "goal_id",
            "index",
            "done",
            "text",
            "schema_version",
            "todo_id",
            "role",
            "status",
            "priority",
            "title",
            "archive_state",
            "source_section",
            "source",
            "task_class",
            "action_kind",
            "claimed_by",
            "agent_id",
            "evidence",
            "note",
            "updated_at",
            "latest_event_kind",
            "latest_event_at",
            "latest_event_status",
            "event_count",
            "event_kinds",
            "required_write_scopes",
            "required_capabilities",
            "workspace_ref",
        ),
    )
    todo = sanitize_string_fields(todo, ("text", "title", "evidence", "note", "action_kind"))
    if "workspace_ref" in todo and isinstance(todo["workspace_ref"], dict):
        todo["workspace_ref"] = sanitize_workspace(todo["workspace_ref"])
    return todo


def sanitize_todo_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    todo = keep_fields(
        row,
        (
            "schema_version",
            "todo_id",
            "goal_id",
            "role",
            "status",
            "priority",
            "title",
            "task_class",
            "action_kind",
            "claimed_by",
            "updated_at",
            "required_write_scopes",
            "workspace_ref",
        ),
    )
    todo = sanitize_string_fields(todo, ("title", "action_kind"))
    if "workspace_ref" in todo and isinstance(todo["workspace_ref"], dict):
        todo["workspace_ref"] = sanitize_workspace(todo["workspace_ref"])
    return todo


def sanitize_workspace(row: dict[str, Any]) -> dict[str, Any]:
    workspace = keep_fields(row, ("kind", "label", "path_safe", "branch", "write_scope"))
    workspace = sanitize_string_fields(workspace, ("kind", "label", "branch"))
    if workspace.get("path_safe") is not True:
        workspace["path_safe"] = False
    return workspace


def sanitize_handoff(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    handoff = keep_fields(
        row,
        (
            "schema_version",
            "from_agent",
            "to_agent",
            "intent",
            "summary",
            "blocker",
            "suggested_next_action",
            "evidence_refs",
            "updated_at",
        ),
    )
    return sanitize_string_fields(handoff, ("summary", "blocker", "suggested_next_action"))


def sanitize_agent(row: dict[str, Any]) -> dict[str, Any]:
    agent = keep_fields(
        row,
        (
            "agent_id",
            "role",
            "state",
            "next_action",
            "last_activity_at",
            "evidence_refs",
            "handoff_refs",
            "goal_ids",
            "workspace_ref",
            "stale_claim_hint",
        ),
    )
    agent = sanitize_string_fields(agent, ("next_action",))
    current_todo = sanitize_todo_row(row.get("current_todo"))
    if current_todo:
        agent["current_todo"] = current_todo
    handoff = sanitize_handoff(row.get("handoff_note"))
    if handoff:
        agent["handoff_note"] = handoff
    if isinstance(agent.get("workspace_ref"), dict):
        agent["workspace_ref"] = sanitize_workspace(agent["workspace_ref"])
    if isinstance(agent.get("stale_claim_hint"), dict):
        agent["stale_claim_hint"] = sanitize_string_fields(
            keep_fields(
                agent["stale_claim_hint"],
                (
                    "state",
                    "claimed_by",
                    "last_activity_at",
                    "threshold_hours",
                    "reason",
                    "recommended_operator_action",
                ),
            ),
            ("reason", "recommended_operator_action"),
        )
    return agent


def sanitize_run(row: dict[str, Any]) -> dict[str, Any]:
    run = keep_fields(
        row,
        (
            "generated_at",
            "goal_id",
            "classification",
            "agent_id",
            "progress_scope",
            "delivery_batch_scale",
            "delivery_outcome",
            "delivery_turn_kind",
            "recommended_action",
            "health_check",
            "json_exists",
            "markdown_exists",
            "lifecycle_phase",
            "lifecycle_flags",
        ),
    )
    return sanitize_string_fields(run, ("recommended_action", "health_check"))


def sanitize_goal(row: dict[str, Any]) -> dict[str, Any]:
    goal = keep_fields(
        row,
        (
            "id",
            "domain",
            "status",
            "lifecycle_phase",
            "lifecycle_flags",
            "registry_member",
            "legacy_runtime_goal",
            "adapter_kind",
            "adapter_status",
            "index_exists",
            "raw_index_records",
            "unique_runs",
            "quota",
        ),
    )
    goal["latest_runs"] = [
        sanitize_run(run)
        for run in row.get("latest_runs", [])[:5]
        if isinstance(run, dict)
    ]
    return goal


def sanitize_queue_item(row: dict[str, Any], todos: list[dict[str, Any]]) -> dict[str, Any]:
    item = keep_fields(
        row,
        (
            "goal_id",
            "status",
            "lifecycle_phase",
            "lifecycle_flags",
            "waiting_on",
            "severity",
            "recommended_action",
            "source",
            "quota",
        ),
    )
    item = sanitize_string_fields(item, ("recommended_action",))
    item["agent_todos"] = {
        "source_section": "Agent Todo",
        "total_count": len(todos),
        "open_count": sum(1 for todo in todos if not todo.get("done")),
        "done_count": sum(1 for todo in todos if todo.get("done")),
        "items": todos,
    }
    return item


def load_status(args: argparse.Namespace) -> dict[str, Any]:
    if args.status_json:
        return json.loads(Path(args.status_json).read_text(encoding="utf-8"))
    cli = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        args.registry,
        "--runtime-root",
        args.runtime_root,
        "--format",
        "json",
        "status",
        "--goal-id",
        args.goal_id,
        "--limit",
        "20",
    ]
    result = subprocess.run(cli, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def update_example(example: dict[str, Any], live: dict[str, Any], *, agent_ids: set[str], goal_id: str) -> dict[str, Any]:
    projection = live.get("agent_management_projection") if isinstance(live.get("agent_management_projection"), dict) else {}
    live_agents = [
        row
        for row in projection.get("agents", [])
        if isinstance(row, dict) and row.get("agent_id") in agent_ids
    ]
    agents = [sanitize_agent(row) for row in live_agents]
    current_todo_ids = {
        todo.get("todo_id")
        for agent in agents
        for todo in [agent.get("current_todo")]
        if isinstance(todo, dict) and todo.get("todo_id")
    }
    live_todos = [
        sanitize_todo(row)
        for row in (live.get("todo_index", {}).get("items") or [])
        if isinstance(row, dict)
        and row.get("role") == "agent"
        and (
            row.get("todo_id") in current_todo_ids
            or row.get("claimed_by") in agent_ids
            or row.get("agent_id") in agent_ids
        )
        and row.get("status") != "done"
    ]
    seen_todo_ids: set[str] = set()
    todos: list[dict[str, Any]] = []
    for todo in live_todos:
        todo_id = str(todo.get("todo_id") or "")
        if todo_id and todo_id in seen_todo_ids:
            continue
        if todo_id:
            seen_todo_ids.add(todo_id)
        todos.append(todo)
        if len(todos) >= 12:
            break

    queue_rows = [
        row
        for row in live.get("attention_queue", {}).get("items", [])
        if isinstance(row, dict) and row.get("goal_id") == goal_id
    ]
    run_goals = [
        sanitize_goal(row)
        for row in live.get("run_history", {}).get("goals", [])
        if isinstance(row, dict) and row.get("id") == goal_id
    ]
    next_example = dict(example)
    next_example["ok"] = live.get("ok", True)
    next_example["registry"] = PUBLIC_REGISTRY
    next_example["runtime_root"] = PUBLIC_RUNTIME_ROOT
    next_example["goal_count"] = live.get("goal_count", len(run_goals))
    next_example["run_count"] = live.get("run_count", 0)
    for key in (
        "usage_summary",
        "event_ledger_summary",
        "promotion_readiness_summary",
        "promotion_gate",
        "decision_freshness_summary",
        "contract",
        "global_registry",
    ):
        if key in live:
            next_example[key] = sanitize_value(live[key])
    next_example["run_history"] = {
        "available": True,
        "goal_count": len(run_goals),
        "run_count": len(run_goals[0].get("latest_runs", [])) if run_goals else 0,
        "goals": run_goals,
    }
    next_example["attention_queue"] = {
        "available": True,
        "item_count": len(queue_rows),
        "needs_user_or_controller": sum(1 for row in queue_rows if row.get("waiting_on") in {"user", "user_or_controller", "controller"}),
        "needs_controller": sum(1 for row in queue_rows if row.get("waiting_on") == "controller"),
        "needs_codex": sum(1 for row in queue_rows if row.get("waiting_on") == "codex"),
        "watching_external_evidence": sum(1 for row in queue_rows if row.get("waiting_on") == "external_evidence"),
        "items": [sanitize_queue_item(row, todos) for row in queue_rows[:1]],
    }
    next_example["todo_index"] = {
        "schema_version": "todo_index_v0",
        "source": "live_loopx_status_public_slice",
        "total_count": len(todos),
        "current_projected_count": len(todos),
        "rollout_event_count": live.get("todo_index", {}).get("rollout_event_count", 0),
        "item_limit": len(todos),
        "items": todos,
    }
    next_example["agent_management_projection"] = {
        "schema_version": projection.get("schema_version") or "agent_management_projection_v0",
        "mode": projection.get("mode") or "read_only",
        "goal_id": projection.get("goal_id") or goal_id,
        "generated_at": projection.get("generated_at"),
        "style_hint": projection.get("style_hint"),
        "truth_contract": projection.get("truth_contract"),
        "source_summary": {
            **(projection.get("source_summary") if isinstance(projection.get("source_summary"), dict) else {}),
            "todo_source": "live_loopx_status_public_slice",
            "public_safe_export": True,
        },
        "agents": agents,
    }
    return next_example


def main() -> int:
    args = parse_args()
    agent_ids = set(args.agent_id or DEFAULT_AGENT_IDS)
    example_path = Path(args.example)
    example = json.loads(example_path.read_text(encoding="utf-8"))
    live = load_status(args)
    next_example = update_example(example, live, agent_ids=agent_ids, goal_id=args.goal_id)
    rendered = json.dumps(next_example, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        example_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
