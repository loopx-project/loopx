"""Write-ownership guards for a LoopX-controlled native KunlunCode Goal."""

from __future__ import annotations

import json
import os
from typing import Any


NATIVE_CONTROLLER_ENV = "LOOPX_KUNLUNCODE_OUTER_CONTROLLER"
NATIVE_CONTROLLER_GOAL_ENV = "LOOPX_KUNLUNCODE_OUTER_GOAL_ID"
NATIVE_CONTROLLER_AGENT_ENV = "LOOPX_KUNLUNCODE_OUTER_AGENT_ID"


def _native_controller_rejection(operation: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": (
                f"{operation} is owned by the LoopX outer controller during a native "
                "KunlunCode Goal run; wait for verifier-backed terminal writeback"
            ),
        }
    )


def guard_native_controller_writeback(control_plane: Any) -> None:
    """Prevent model-visible MCP tools from committing before native verification."""

    if os.environ.get(NATIVE_CONTROLLER_ENV) != "1":
        return

    def blocked_claim(_todo_id: str, _agent_id: str) -> str:
        return _native_controller_rejection("claim_task")

    def blocked_complete(
        _todo_id: str,
        _agent_id: str,
        _evidence: str,
        next_agent_todo: str = "",
        task_lease_idempotency_key: str = "",
        task_lease_expected_version: int | None = None,
        no_follow_up: bool = False,
        successor_todo_ids: list[str] | None = None,
        agent_vision: dict | None = None,
        vision_unchanged_reason: str = "",
        first_delivery: bool = False,
        delivery_read_context_id: str = "",
        read_context_id: str = "",
    ) -> str:
        del (
            next_agent_todo,
            task_lease_idempotency_key,
            task_lease_expected_version,
            no_follow_up,
            successor_todo_ids,
            agent_vision,
            vision_unchanged_reason,
            first_delivery,
            delivery_read_context_id,
            read_context_id,
        )
        return _native_controller_rejection("complete_task")

    control_plane.claim_task = blocked_claim
    control_plane.complete_task = blocked_complete

    def blocked_vision(*_args: Any, **_kwargs: Any) -> str:
        return _native_controller_rejection("review_task_vision")

    control_plane.review_task_vision = blocked_vision


def native_controller_cli_write_block(arguments: Any) -> dict[str, Any] | None:
    """Block direct model CLI writes to the Goal owned by the outer controller."""

    if os.environ.get(NATIVE_CONTROLLER_ENV) != "1":
        return None
    goal_id = str(os.environ.get(NATIVE_CONTROLLER_GOAL_ENV) or "").strip()
    if not goal_id:
        return None
    raw_goal_ids: list[Any] = []
    for attribute in ("goal_id", "goal_ids"):
        value = getattr(arguments, attribute, None)
        if isinstance(value, (list, tuple, set)):
            raw_goal_ids.extend(value)
        else:
            raw_goal_ids.append(value)
    requested_goal_ids = {
        str(value).strip() for value in raw_goal_ids if str(value or "").strip()
    }
    if goal_id not in requested_goal_ids:
        return None

    command = str(getattr(arguments, "command", None) or "")
    operation: str | None = None
    if command == "todo":
        todo_command = str(getattr(arguments, "todo_command", None) or "add")
        if todo_command not in {"list", "suggest"}:
            operation = f"todo {todo_command}"
    elif command == "quota":
        quota_command = str(getattr(arguments, "quota_command", None) or "status")
        if quota_command not in {"status", "plan", "should-run"}:
            operation = f"quota {quota_command}"
    elif command == "task-lease":
        lease_command = str(getattr(arguments, "task_lease_command", None) or "")
        if lease_command != "inspect":
            operation = f"task-lease {lease_command}".strip()
    elif command == "turn" and getattr(arguments, "turn_command", None) == "run-once":
        operation = "turn run-once"
    elif command in {
        "archive-runtime",
        "configure-goal",
        "operator-gate",
        "refresh-state",
        "register-agent",
        "retire-global-goal",
        "reward",
    }:
        operation = command
    if operation is None:
        return None
    return {
        "ok": False,
        "error_code": "kunluncode_outer_controller_write_owned",
        "error": (
            f"{operation} for goal {goal_id!r} is owned by the LoopX outer controller "
            "during this native KunlunCode Goal run"
        ),
        "goal_id": goal_id,
        "agent_id": str(os.environ.get(NATIVE_CONTROLLER_AGENT_ENV) or "") or None,
        "operation": operation,
        "next_action": "wait for verifier-backed terminal writeback",
    }
