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
from .acceptance import inspect_goal_acceptance
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


def compute_task_planning_input_digest(*, goal_id: str, agent_id: str, text: str) -> str:
    identity = {"goal_id": goal_id, "agent_id": agent_id, "text": text}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def inspect_goal_planning_frontier(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    project: Path | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    """Read canonical Goal Todos, blockers, and acceptance basis without mutating state."""
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
    acceptance = inspect_goal_acceptance(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        runtime_root=runtime_root_arg,
        include_work_details=False,
    )
    return {
        "agent_id": agent_id,
        "goal": goal,
        "existing_todos": todos,
        "runnable": runnable,
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
        "goal_acceptance": {
            key: acceptance[key]
            for key in (
                "provider_revision",
                "source_authority",
                "goal_acceptance_contract",
                "planning_requirements",
            )
        },
    }


def validate_task_plan_readback(
    result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Verify a task-planning checkpoint result against canonical post-plan state."""
    if (
        result.get("input_digest") != before["input_digest"]
        or after["input_digest"] != before["input_digest"]
    ):
        raise ValueError("planning input changed before readback")
    status = result.get("status")
    ids = result.get("todo_ids")
    if (
        status not in {"ready", "blocked"}
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(item, str) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(
            "planning result requires unique actual Todo ids and a typed status"
        )
    todos = {item["todo_id"]: item for item in after["existing_todos"]}
    if any(todo_id not in todos for todo_id in ids):
        raise ValueError("planning referenced a missing or unrelated Todo")
    if status == "ready" and not set(ids).issubset(after["runnable_todo_ids"]):
        raise ValueError("planning referenced non-runnable or unclaimed work")
    if status == "blocked" and not set(ids).issubset(after["blocking_todo_ids"]):
        raise ValueError("blocked planning result requires unresolved blocking Todos")
    requirements = after.get("goal_acceptance", {}).get("planning_requirements", {})
    if status == "ready" and (
        requirements.get("agent_id") != after["agent_id"]
        or requirements.get("declaration_current") is not True
        or not set(ids).issubset(requirements.get("linked_todo_ids", []))
    ):
        raise ValueError("ready planning requires canonical overall requirements linked to its Todos")
    return {
        "input_digest": before["input_digest"],
        "status": status,
        "todo_ids": ids,
        "goal_id": after["goal_id"],
        "agent_id": after["agent_id"],
        "state_readback_verified": True,
        **({"goal_requirements_readback": requirements} if status == "ready" else {}),
        "execution_owner": "caller",
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
    frontier = inspect_goal_planning_frontier(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        project=project,
        runtime_root_arg=runtime_root_arg,
    )
    agent_id = str(frontier["agent_id"])
    goal = frontier["goal"]
    todos = frontier["existing_todos"]
    runnable = frontier["runnable"]
    goal_acceptance = frontier["goal_acceptance"]
    outcomes = (
        (goal_acceptance.get("goal_acceptance_contract") or {})
        .get("outcome_requirements", {})
        .get("outcomes", [])
    )
    batch_applicable = (
        not todos
        and not outcomes
        and not frontier["goal_waiting_on"]
        and not frontier["blocking_todo_ids"]
    )
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
        planning_text=text,
        batch_applicable=batch_applicable,
    )
    identity = {"goal_id": goal_id, "agent_id": agent_id, "text": text}
    digest = compute_task_planning_input_digest(
        goal_id=goal_id, agent_id=agent_id, text=text
    )
    project_args = ["--project", str(project)] if project is not None else []
    batch_route = shlex.join([
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        f"--text={text}",
        *project_args,
    ])
    return {
        "ok": True,
        "read_only": True,
        "dry_run": True,
        "command": "plan",
        "schema_version": TASK_PLAN_SCHEMA,
        **identity,
        "input_digest": digest,
        "planner": goal_planner_contract(fine_grained=fine),
        # Planning needs declarations and revision-bound work links, not private
        # check commands or a second copy of every task's validation details.
        "goal_acceptance": goal_acceptance,
        "planning_batch": {
            "schema_version": "loopx_planning_batch_proposal_v1",
            "applicable": batch_applicable,
            "schema_command": f"{batch_route} --schema",
            "preview_command_template": f"{batch_route} --document '<proposal.json>' --dry-run",
            "apply_command_template": f"{batch_route} --document '<proposal.json>' --execute",
        },
        "ordered_steps": steps,
        "existing_todos": todos,
        "runnable_todo_ids": frontier["runnable_todo_ids"],
        "blocking_todo_ids": frontier["blocking_todo_ids"],
        "goal_waiting_on": frontier["goal_waiting_on"],
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
            "and inspect the workspace as needed; follow goal_requirements_guidance to "
            "reuse existing requirements or declare missing sourced requirements through the existing outcome owner, "
            "link actual work and read it back before ready (when planning_batch.applicable is true, "
            "you may submit one initial outcome + Todo batch via planning_batch.apply_command_template). "
            "Make the approach explicit "
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
