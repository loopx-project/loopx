"""Model-owned task planning for an existing Goal, before its caller starts work."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from ...agent_registry import require_registered_agent_id
from ...execution_profile import execution_profile_is_fine_grained
from ...history import load_registry
from ...registry import find_registry_goal
from ...control_plane.todos.list_readback import list_goal_todos
from ..todos.todo_semantics import todo_item_is_actionable_open
from ..todos.contract import (
    TODO_STATUS_BLOCKED,
    TODO_TERMINAL_STATUS_VALUES,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_BLOCKER,
    TODO_TASK_CLASS_USER_GATE,
)
from .start_contract import goal_planner_contract
from .start_goal_todo_delta import todo_authoring_steps


TASK_PLAN_SCHEMA = "loopx_task_planning_v0"
TASK_PLAN_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["input_digest", "status", "todo_ids"],
    "properties": {
        "input_digest": {"type": "string"},
        "status": {"type": "string", "enum": ["ready", "blocked"]},
        "todo_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
}


def build_task_planning_packet(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    text: str,
    project: Path | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    """Read canonical planning inputs; create no Goal, Todo, Turn or host loop."""
    if not text.strip():
        raise ValueError("todo plan requires non-empty --text")
    agent_id = require_registered_agent_id(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        field="agent_id",
    )
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if goal is None:
        raise ValueError("todo plan requires an existing Goal")
    # Read both roles explicitly: the compact lane display is not a complete frontier.
    todos = []
    for role in ("agent", "user"):
        listed = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            role=role,
            project=project,
            runtime_root_arg=runtime_root_arg,
        )
        todos.extend(listed["todos"])
    runnable = [
        t
        for t in todos
        if t.get("role") == "agent"
        and t.get("task_class") == TODO_TASK_CLASS_ADVANCEMENT
        and todo_item_is_actionable_open(t)
    ]
    fine = execution_profile_is_fine_grained(goal.get("execution_profile"))
    prefix = ["loopx", "--format", "json", "--registry", str(registry_path)]
    if runtime_root_arg:
        prefix += ["--runtime-root", runtime_root_arg]
    cli = shlex.join(prefix)
    steps = todo_authoring_steps(
        existing_runnable_frontier=runnable,
        plan_prompt=None,
        fine_grained=fine,
        cli_bin="loopx",
        runtime_root=runtime_root_arg,
        goal_id=goal_id,
        agent_id=agent_id,
        registry_path=registry_path,
    )
    identity = {"goal_id": goal_id, "agent_id": agent_id, "text": text}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "ok": True,
        "read_only": True,
        "dry_run": True,
        "command": "plan",
        "schema_version": TASK_PLAN_SCHEMA,
        **identity,
        "input_digest": digest,
        "planner": goal_planner_contract(fine_grained=fine),
        "ordered_steps": steps,
        "existing_todos": todos,
        "runnable_todo_ids": [
            t["todo_id"] for t in runnable if t.get("claimed_by") == agent_id
        ],
        "blocking_todo_ids": [
            t["todo_id"]
            for t in todos
            if t.get("status") not in TODO_TERMINAL_STATUS_VALUES
            and (
                t.get("status") == TODO_STATUS_BLOCKED
                or t.get("task_class")
                in {TODO_TASK_CLASS_BLOCKER, TODO_TASK_CLASS_USER_GATE}
            )
        ],
        "goal_waiting_on": goal.get("waiting_on"),
        "result_schema": TASK_PLAN_RESULT_SCHEMA,
        "execution_handoff": {
            "owner": "caller",
            "requires_quota_guard": True,
            "starts_host_loop": False,
            "spends_quota": False,
            "planning_is_advancement": False,
        },
        "task_body": (
            "Execute the LoopX task-planning checkpoint for this already registered Goal/Agent. "
            "Use the attached planner and ordered_steps, shared with /loopx. Read the exact text "
            "and inspect the workspace as needed; make the approach and acceptance explicit "
            "before writing task Todos through the routed public CLI. Compare all existing "
            "work and waits; reuse/update covered work and add only uncovered work. "
            "Do not create a planning/setup Todo, restart the Goal, complete task Todos, "
            "change task files, clear waits, activate a loop, execute task work or spend quota. "
            "The caller owns the execution_handoff and must enter its quota guard after readback. "
            "This is a planning-stage boundary, not task completion. Planning does not grant "
            "additional permissions. For ready, return the actual open advancement Todo ids "
            "claimed by this agent that cover this input. For blocked, persist/reference the "
            "relevant blocker or User gate and return its Todo ids. Return input_digest exactly. "
            "Do not claim success from prose or fabricate Todo ids. Command prefix: "
            + cli
        ),
    }


def render_task_planning_packet(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
